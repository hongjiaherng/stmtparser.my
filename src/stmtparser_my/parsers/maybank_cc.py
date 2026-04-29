"""Parser for Maybank Credit Card statements.

Format characteristics:

- Header has Statement Date in format ``DD MMM YY`` (e.g. ``"12 JAN 26"``).
- Transaction lines: ``DD/MM  DD/MM  DESCRIPTION...  AMOUNT[CR]``.

  - First date = posting date, second = transaction date (no year, inferred).
  - ``CR`` suffix = credit (refund / payment received).
  - No suffix = debit (purchase).
- ``TRANSACTED AMOUNT  USD  10.79`` continuation lines are foreign-currency
  notes for the previous transaction.

Pipeline:

- ``extract_raw`` reads the PDF, populates statement metadata + raw rows.
  Each raw row's ``Transaction Description`` is a list[str], one element
  for the merchant line, plus an extra element for the FX continuation if
  present (verbatim PDF wording, e.g. ``"TRANSACTED AMOUNT USD 10.79"``).
- ``normalize`` joins each row's description list into a single notes line
  (separated by `` | ``), keeping every line verbatim. No special-casing
  of any particular label; if the PDF starts emitting a new continuation
  type we haven't seen before, it just passes through.
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import cast

import pdfplumber

from ..transactions import ParseResult, RawSection, Transaction
from ._utils import parse_amount
from .base import StatementParser

RAW_COLUMNS: tuple[str, ...] = (
    "Posting Date",
    "Transaction Date",
    "Transaction Description",
    "Amount(RM)",
)

MONTH_NAME_TO_NUM: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

STATEMENT_DATE_RE = re.compile(
    r"(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{2,4})",
    re.IGNORECASE,
)

ACCOUNT_RE = re.compile(r"\b(\d{4}\s\d{4}\s\d{4}\s\d{4})\b")

TX_LINE_RE = re.compile(
    r"""
    ^\s*
    (?P<post>\d{2}/\d{2})
    \s+
    (?P<txn>\d{2}/\d{2})
    \s+
    (?P<desc>.+?)
    \s+
    (?P<amount>[\d,]+\.\d{2})
    (?P<cr>CR)?
    \s*$
    """,
    re.VERBOSE,
)

FX_LINE_RE = re.compile(
    r"^\s*TRANSACTED\s+AMOUNT\s+([A-Z]{3})\s+([\d,]+\.\d{2})\s*$",
    re.IGNORECASE,
)

IGNORE_LINE_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"RETAIL\s+INTEREST\s+RATE",
        r"YOUR\s+(PREVIOUS\s+STATEMENT\s+BALANCE|COMBINED\s+CREDIT\s+LIMIT)",
        r"TOTAL\s+(CREDIT|DEBIT)\s+THIS\s+MONTH",
        r"SUB\s*TOTAL",
        r"JUMLAH\s+(KREDIT|DEBIT|PENYATA)",
        r"^\s*$",
    )
)


def _resolve_year(month_day: str, statement_date: date) -> int:
    """If the txn month is later than the statement month, it is from last year."""
    _, month_str = month_day.split("/")
    month = int(month_str)
    if month > statement_date.month:
        return statement_date.year - 1
    return statement_date.year


def _maybe_statement_date(
    day: str | int, month_abbr: str, year: str | int
) -> date | None:
    try:
        d = int(day)
        mo = MONTH_NAME_TO_NUM[month_abbr.upper()]
        y = int(year)
    except (KeyError, ValueError):
        return None
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


class MaybankCCParser(StatementParser):
    name = "maybank_cc"
    label_prefix = "Maybank Credit Card"

    def detect(self, first_page_text: str) -> bool:
        haystack = first_page_text
        return (
            "STATEMENT OF CREDIT CARD ACCOUNT" in haystack
            and "Malayan Banking Berhad (3813-K)" in haystack
        )

    def extract_raw(self, pdf_path: Path) -> ParseResult:
        pdf_path = Path(pdf_path)
        full_lines: list[str] = []
        raw_full_parts: list[str] = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # layout=True preserves horizontal whitespace based on each
                # word's x-position, so the wide gap between merchant name
                # and location (e.g. "SHOPEE MALAYSIA       KUALA LUMPUR MY")
                # survives as multiple spaces — needed for the 2+-whitespace
                # split in TX_LINE_RE handling below.
                text = page.extract_text(layout=True) or ""
                raw_full_parts.append(text)
                full_lines.extend(text.splitlines())
        raw_full = "\n".join(raw_full_parts)

        statement_date: date | None = None
        m = re.search(
            r"Statement\s+Date.*?Payment\s+Due\s+Date.*?(\d{1,2})\s+([A-Z]{3})\s+(\d{2,4})",
            raw_full,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            statement_date = _maybe_statement_date(m.group(1), m.group(2), m.group(3))

        if statement_date is None:
            for sm in STATEMENT_DATE_RE.finditer(raw_full[:2000]):
                statement_date = _maybe_statement_date(
                    sm.group(1), sm.group(2), sm.group(3)
                )
                if statement_date is not None:
                    break

        if statement_date is None:
            # Unlike Savings/TnG, CC transaction lines have no year — we need
            # the statement date to resolve year via `_resolve_year`. Failing
            # fast is preferable to producing rows dated in the wrong year.
            raise ValueError(f"Could not find statement date in {pdf_path}")

        account = ""
        if m := ACCOUNT_RE.search(raw_full):
            account = m.group(1)
        card_number = account.replace(" ", "-") if account else ""
        label = f"{self.label_prefix} {card_number}".strip()

        raw_section = RawSection(columns=RAW_COLUMNS)
        result = ParseResult(
            source_file=str(pdf_path),
            account_label=label,
            statement_date=statement_date,
            raw_sections={"transactions": raw_section},
        )

        last_raw: dict[str, object] | None = None
        for line in full_lines:
            s = line.rstrip()
            if any(p.search(s) for p in IGNORE_LINE_RES):
                continue

            if m := TX_LINE_RE.match(s):
                txn_date_str = m.group("txn")
                try:
                    year = _resolve_year(txn_date_str, statement_date)
                    d_part, mo_part = txn_date_str.split("/")
                    txn_date = date(year, int(mo_part), int(d_part))
                except ValueError:
                    continue
                post_date_str = m.group("post")
                try:
                    post_year = _resolve_year(post_date_str, statement_date)
                    pd_part, pm_part = post_date_str.split("/")
                    post_date = date(post_year, int(pm_part), int(pd_part))
                except ValueError:
                    post_date = txn_date
                # The PDF separates the merchant name from the location with a
                # run of spaces (e.g. "SHOPEE MALAYSIA            KUALA LUMPUR MY").
                # Split on 2+ whitespace so each visually-distinct segment is its
                # own element in the description list, then collapse single-space
                # runs within each segment.
                desc_parts = [
                    " ".join(p.split())
                    for p in re.split(r"\s{4,}", m.group("desc"))
                    if p.strip()
                ]
                amount = parse_amount(m.group("amount"))
                is_credit = m.group("cr") is not None
                signed = amount if is_credit else -amount

                last_raw = {
                    "Posting Date": post_date.strftime("%Y-%m-%d"),
                    "Transaction Date": txn_date.strftime("%Y-%m-%d"),
                    "Transaction Description": desc_parts,
                    "Amount(RM)": signed,
                }
                raw_section.rows.append(last_raw)
                continue

            if (m := FX_LINE_RE.match(s)) and last_raw is not None:
                currency, fx_amount = m.group(1), m.group(2)
                desc_list = cast(list[str], last_raw["Transaction Description"])
                desc_list.append(f"TRANSACTED AMOUNT {currency} {fx_amount}")

        return result

    def normalize(self, result: ParseResult) -> None:
        raw_section = result.raw_sections.get("transactions")
        if raw_section is None:
            return
        for row in raw_section.rows:
            txn_date_str = row.get("Transaction Date")
            amount = row.get("Amount(RM)")
            desc_lines = row.get("Transaction Description")
            if not isinstance(txn_date_str, str) or not isinstance(
                amount, (int, float)
            ):
                continue
            if isinstance(desc_lines, list):
                notes_parts = [str(line) for line in desc_lines]
            elif desc_lines:
                notes_parts = [str(desc_lines)]
            else:
                notes_parts = []
            notes = " ".join(" | ".join(notes_parts).split())
            result.transactions.append(
                Transaction(
                    date=datetime.strptime(txn_date_str, "%Y-%m-%d").date(),
                    notes=notes,
                    amount=float(amount),
                )
            )
