"""Parser for Maybank Personal Saver / Savings Account statements.

Format characteristics:

- Each transaction starts with a line:
  ``DD/MM/YY  DESCRIPTION_HEAD   AMOUNT[+|-]   BALANCE``
- Followed by 1..N continuation lines (description detail, account refs, notes).
- Continuation lines do NOT start with ``DD/MM/YY``.
- Data section is bounded by ``BEGINNING BALANCE`` and ``ENDING BALANCE``.
- Trailing ``-`` = debit (outflow), ``+`` = credit (inflow).
- Numbers use commas as thousands separators.

Pipeline:

- ``extract_raw`` reads the PDF, populates statement metadata + the raw rows
  (verbatim columns: ENTRY DATE / TRANSACTION DESCRIPTION (list[str]) /
  TRANSACTION AMOUNT (signed) / STATEMENT BALANCE).
- ``normalize`` reads those raw rows and emits the ``Transaction`` list with
  one collapsed-line ``notes`` per row. The raw description is left untouched.
"""

import contextlib
import re
from datetime import date, datetime
from pathlib import Path

import pdfplumber

from stmtparser_my.parsers._utils import parse_amount
from stmtparser_my.parsers.base import StatementParser
from stmtparser_my.transactions import ParseResult, RawSection, Transaction

RAW_COLUMNS: tuple[str, ...] = (
    "ENTRY DATE",
    "TRANSACTION DESCRIPTION",
    "TRANSACTION AMOUNT",
    "STATEMENT BALANCE",
)

TX_HEADER_RE = re.compile(
    r"""
    ^\s*
    (?P<date>\d{2}/\d{2}/\d{2})
    \s+
    (?P<head_desc>.+?)
    \s+
    (?P<amount>[\d,]+\.\d{2})
    (?P<sign>[+\-])
    \s+
    (?P<balance>[\d,]+\.\d{2})
    \s*$
    """,
    re.VERBOSE,
)

# Account number: '戶號 : NNNNNN-NNNNNN' on one line, followed (a few lines
# later) by the bilingual label 'ACCOUNT NUMBER' (printed on two lines in the
# header). The label is required as a non-capturing anchor.
ACCOUNT_NUMBER_RE = re.compile(
    r"戶號\s*:\s*(\d{6}-\d{6}).*?ACCOUNT\s+NUMBER",
    re.DOTALL,
)

# Statement date: '結單日期 : DD/MM/YY' on one line, followed (a few lines later)
# by the bilingual label 'STATEMENT DATE'. The label is required as a
# non-capturing anchor to disambiguate from any other DD/MM/YY that might
# appear near '結單日期'.
STATEMENT_DATE_RE = re.compile(
    r"結單日期\s*:\s*(\d{2}/\d{2}/\d{2})(?!\d).*?STATEMENT\s+DATE",
    re.DOTALL,
)

BEGINNING_BALANCE_RE = re.compile(
    r"BEGINNING\s+BALANCE\s+([\d,]+\.\d{2})", re.IGNORECASE
)
ENDING_BALANCE_RE = re.compile(r"ENDING\s+BALANCE\s*:\s*([\d,]+\.\d{2})", re.IGNORECASE)

NOISE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        # Bank header / branch / regulator banners
        r"^Malayan Banking Berhad",
        r"^14th Floor",
        r"^\d{6}\s+(SIMPANG|CAWANGAN|BRANCH)",
        r"^PROTECTED BY PIDM",
        # Malaysian address structure (account holder address re-printed in page header).
        r"^\d{1,4}\s+(LORONG|JALAN|TAMAN|JLN|TMN|TINGKAT|KAMPUNG|KG|LRG|LOT|NO)\b",
        r"^(LORONG|JALAN|TAMAN|JLN|TMN|TINGKAT|KAMPUNG|KG|LRG)\s+[A-Z]",
        r"^\d{5}\s+[A-Z]",
        # Address fragments printed with comma separators surrounding a 5-digit
        # Malaysian postcode. Transaction descriptions never contain a comma
        # directly followed by a 5-digit postcode, so this is safe.
        r",\s*\d{5}\b",
        # Bilingual "Statement Date" label appears next to the holder name on
        # each page header; never appears in transaction descriptions.
        r"結單日期",
        # Bilingual column headers and labels printed on each page
        r"^URUSNIAGA AKAUN",
        r"^TARIKH MASUK",
        r"^進支日期",
        r"^ENTRY DATE",
        # Page marker prints as e.g. "000005 MUKA/ 頁/PAGE : 5" — the leading
        # row number means we can't anchor to start of line.
        r"MUKA/",
        r"頁/PAGE",
        r"^TARIKH PENYATA",
        r"^STATEMENT DATE",
        r"^NOMBOR AKAUN",
        r"^戶號",
        r"^ACCOUNT\s*$",
        r"^NUMBER\s*$",
        r"^\s*$",
        # Bilingual customer-service / disclaimer footer
        r"^Perhation",
        r"^\(\d+\)",
        r"^Semua maklumat",
        r"^若银行",
        r"^All items",
        r"^Sila beritahu",
        r"^請通知",
        r"^Please notify",
        r"^tempoh 21",
    )
)


def _is_noise(line: str) -> bool:
    return any(p.search(line) for p in NOISE_PATTERNS)


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%d/%m/%y").date()


class MaybankSavingsParser(StatementParser):
    name = "maybank_savings"
    label_prefix = "Maybank Personal Saver"

    def detect(self, first_page_text: str) -> bool:
        haystack = first_page_text
        return bool(
            "PERSONAL SAVER" in haystack
            and "Malayan Banking Berhad (3813-K)" in haystack
            and "URUSNIAGA AKAUN" in haystack
        )

    def extract_raw(self, pdf_path: Path) -> ParseResult:
        pdf_path = Path(pdf_path)
        full_lines: list[str] = []
        raw_full_parts: list[str] = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                raw_full_parts.append(text)
                full_lines.extend(text.splitlines())
        raw_full = "\n".join(raw_full_parts)

        account_no = ""
        if m := ACCOUNT_NUMBER_RE.search(raw_full):
            account_no = m.group(1)

        statement_date: date | None = None
        if m := STATEMENT_DATE_RE.search(raw_full):
            with contextlib.suppress(ValueError):
                statement_date = _parse_date(m.group(1))

        opening_balance: float | None = None
        if m := BEGINNING_BALANCE_RE.search(raw_full):
            opening_balance = parse_amount(m.group(1))

        closing_balance: float | None = None
        if m := ENDING_BALANCE_RE.search(raw_full):
            closing_balance = parse_amount(m.group(1))

        label = f"{self.label_prefix} {account_no}".strip()
        raw_section = RawSection(columns=RAW_COLUMNS)
        result = ParseResult(
            source_file=str(pdf_path),
            account_label=label,
            statement_date=statement_date,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            raw_sections={"transactions": raw_section},
        )

        in_data = False
        current_tx: dict[str, str] | None = None
        current_detail_lines: list[str] = []

        def flush() -> None:
            nonlocal current_tx, current_detail_lines
            if current_tx is None:
                return
            head = current_tx["head_desc"].strip()
            raw_desc_lines = [
                head,
                *(s.strip() for s in current_detail_lines if s.strip()),
            ]
            amount = parse_amount(current_tx["amount"])
            if current_tx["sign"] == "-":
                amount = -amount
            balance = parse_amount(current_tx["balance"])
            tx_date = _parse_date(current_tx["date"])

            raw_section.rows.append(
                {
                    "ENTRY DATE": tx_date.strftime("%Y-%m-%d"),
                    "TRANSACTION DESCRIPTION": raw_desc_lines,
                    "TRANSACTION AMOUNT": amount,
                    "STATEMENT BALANCE": balance,
                }
            )
            current_tx = None
            current_detail_lines = []

        for line in full_lines:
            stripped = line.rstrip()

            if not in_data:
                if BEGINNING_BALANCE_RE.search(stripped):
                    in_data = True
                continue

            if ENDING_BALANCE_RE.search(stripped):
                flush()
                in_data = False
                continue

            if _is_noise(stripped):
                continue

            if m := TX_HEADER_RE.match(stripped):
                flush()
                current_tx = m.groupdict()
                current_detail_lines = []
            elif current_tx is not None and stripped.strip():
                current_detail_lines.append(stripped)

        flush()

        # Reconciliation against raw amounts (independent of normalize()).
        if opening_balance is not None and closing_balance is not None:
            net = sum(
                float(r["TRANSACTION AMOUNT"])
                for r in raw_section.rows
                if isinstance(r["TRANSACTION AMOUNT"], (int, float))
            )
            diff = (opening_balance + net) - closing_balance
            if abs(diff) > 0.01:
                result.warnings.append(
                    f"Balance mismatch: opening {opening_balance:.2f} + net {net:.2f} "
                    f"!= closing {closing_balance:.2f} (off by {diff:.2f}). "
                    "Some transactions may have been missed or duplicated."
                )

        return result

    def normalize(self, result: ParseResult) -> None:
        raw_section = result.raw_sections.get("transactions")
        if raw_section is None:
            return
        for row in raw_section.rows:
            date_str = row.get("ENTRY DATE")
            amount = row.get("TRANSACTION AMOUNT")
            desc_lines = row.get("TRANSACTION DESCRIPTION")
            if not isinstance(date_str, str) or not isinstance(amount, (int, float)):
                continue
            if isinstance(desc_lines, list):
                # The first line is the generic transaction-type head
                # (e.g. "IBK FUND TFR FR A/C", "SALE DEBIT") and adds no
                # information when continuation lines exist. Drop it unless
                # it's the only line (e.g. "CASH DEPOSIT" with no detail).
                parts = desc_lines[1:] if len(desc_lines) > 1 else desc_lines
                notes = " ".join(" ".join(str(p) for p in parts).split())
            else:
                notes = (
                    " ".join(str(desc_lines).split()) if desc_lines is not None else ""
                )
            result.transactions.append(
                Transaction(
                    date=datetime.strptime(date_str, "%Y-%m-%d").date(),
                    notes=notes,
                    amount=float(amount),
                )
            )
