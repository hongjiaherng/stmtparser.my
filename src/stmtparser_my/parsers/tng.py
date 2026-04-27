"""Parser for Touch 'n Go eWallet transaction history PDFs.

We only support text-extractable PDFs (newer TnG exports). When you download
your statement from the TnG app, choose the option that produces a normal
(non-image) PDF; that is what this parser reads via pdfplumber.

Approach
--------
TnG's table cells frequently wrap across multiple lines (a long Reference
number gets split into 4 short lines, the Description spills onto the next
line, etc.). A naive ``extract_text()`` parse can't tell which continuation
fragment belongs to which column. We use ``page.extract_words()`` instead,
which gives every word's (x, y) bounding box, then bin words into columns by
their x position. Continuation rows (rows whose Date column is empty) get
merged into the active transaction column-by-column.

Sections
--------
The PDF contains two stacked tables, each with its own column header:

1. ``TNG WALLET TRANSACTION``: everyday wallet activity. Column 8 is
   ``Wallet Balance``.
2. ``GO+ TRANSACTION``: the GO+ MMF sub-account. Column 8 is ``GO+ Balance``.

Both produce their own ``RawSection`` (lossless mirror of the PDF).

Pipeline
--------
- ``extract_raw`` produces both raw sections and stores the statement period
  end on ``result.statement_date`` (needed by ``normalize`` for Daily Earnings
  aggregation).
- ``normalize`` reads the raw rows and emits the ``Transaction`` list:

  - **Wallet rows**: drop those that mention ``GO+`` (sweeps to/from the MMF;
    they double-count against the GO+ section). Keep everything else.
  - **GO+ rows**: drop everything except ``GO+ Daily Earnings`` and aggregate
    those into one synthetic transaction dated at ``statement_date`` (positive
    amount, dropped if total < RM0.01).
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import cast

import pdfplumber

from ..transactions import ParseResult, RawSection, Transaction
from .base import StatementParser

# pdfplumber returns word dicts with object-typed values; we normalize each
# word in _extract_pages so downstream code can use ``Word`` directly.
Word = dict[str, object]

ACCOUNT_LABEL_PREFIX = "TNG eWallet"
MIN_AGGREGATED_DAILY_EARNINGS = 0.01

DATE_PREFIX_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
GO_PLUS_RE = re.compile(r"GO\s*\+", re.IGNORECASE)
DAILY_EARNING_RE = re.compile(r"daily\s+earning", re.IGNORECASE)
GO_PLUS_SECTION_RE = re.compile(r"GO\+\s*TRANSACTION", re.IGNORECASE)
WALLET_ID_RE = re.compile(r"Wallet\s*ID\s+(\d+)")
PERIOD_RE = re.compile(
    r"Transaction\s+Period\s+"
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
    r"\s*-\s*"
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
    re.IGNORECASE,
)
MONEY_RE = re.compile(r"^RM\s*([\d,]+\.\d+)$")

# y-coordinate tolerance when grouping words into a row.
ROW_Y_TOLERANCE = 3.0

# Column layout observed on the user's TnG export (x0 of each column header).
COLUMNS: tuple[tuple[str, float, float], ...] = (
    ("Date", 0.0, 80.0),
    ("Status", 80.0, 140.0),
    ("Transaction Type", 140.0, 230.0),
    ("Reference", 230.0, 295.0),
    ("Description", 295.0, 460.0),
    ("Details", 460.0, 657.0),
    ("Amount (RM)", 657.0, 750.0),
    ("Balance", 750.0, 10_000.0),
)
COLUMN_NAMES = tuple(name for name, _, _ in COLUMNS)

WALLET_RAW_COLUMNS: tuple[str, ...] = (*COLUMN_NAMES[:-1], "Wallet Balance")
GO_PLUS_RAW_COLUMNS: tuple[str, ...] = (*COLUMN_NAMES[:-1], "GO+ Balance")

INFLOW_KEYWORDS: tuple[str, ...] = (
    "reload",
    "receive",
    "refund",
    "cashback",
    "incentive",
    "promo",
    "duitnow_recei",
    "go+ daily earning",
    "go+ cash in",
)
OUTFLOW_KEYWORDS: tuple[str, ...] = (
    "transfer to",
    "payment",
    "direct debit",
    "purchase",
    "spending",
    "send to",
    "withdraw",
    "ewallet cash out",
    "duitnow_trans",
    "duitnow qr",
    "go+ cash out",
)

# How each column should join its wrapped pieces.
#   "concat": single token spanning lines (long ID number wrapped on the
#              page); rejoin with no separator.
#   "space":  natural-language text; rejoin with single space.
#   "first":  value should never wrap; if it does, take the first piece.
_JOIN_RULE: dict[str, str] = {
    "Date": "first",
    "Status": "space",
    "Transaction Type": "space",
    "Reference": "concat",
    "Description": "space",
    "Details": "concat",
    "Amount (RM)": "first",
    "Balance": "first",
}


def _column_for_x(x0: float) -> str | None:
    for name, lo, hi in COLUMNS:
        if lo <= x0 < hi:
            return name
    return None


def _w_top(w: Word) -> float:
    return cast(float, w["top"])


def _w_x0(w: Word) -> float:
    return cast(float, w["x0"])


def _w_text(w: Word) -> str:
    return cast(str, w["text"])


def _parse_period_end(full_text: str) -> date | None:
    if m := PERIOD_RE.search(full_text):
        try:
            return datetime.strptime(
                f"{m.group(4)} {m.group(5)} {m.group(6)}", "%d %B %Y"
            ).date()
        except ValueError:
            return None
    return None


def _classify(tx_type: str) -> int:
    """+1 inflow, -1 outflow, 0 unknown."""
    t = tx_type.lower()
    if any(kw in t for kw in INFLOW_KEYWORDS):
        return 1
    if any(kw in t for kw in OUTFLOW_KEYWORDS):
        return -1
    return 0


def _parse_money(s: str) -> float | None:
    if m := MONEY_RE.match(s.strip()):
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_iso_date(s: str) -> date | None:
    """Accept either the PDF's ``D/M/YYYY`` form or the normalized ``YYYY-MM-DD``."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _join_pieces(pieces: list[str], rule: str) -> str:
    if not pieces:
        return ""
    if rule == "first":
        return pieces[0]
    if rule == "concat":
        return "".join(pieces)
    return " ".join(pieces)


def _extract_pages(pdf_path: Path) -> list[list[Word]]:
    pages: list[list[Word]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            pages.append(
                [
                    {
                        "text": str(w["text"]),
                        "x0": float(w["x0"]),  # type: ignore[arg-type]
                        "top": float(w["top"]),  # type: ignore[arg-type]
                    }
                    for w in words
                ]
            )
    return pages


def _full_text(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _group_words_into_rows(words: list[Word]) -> list[list[Word]]:
    """Cluster words into rows by their ``top`` coordinate."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (_w_top(w), _w_x0(w)))
    rows: list[list[Word]] = []
    current_row: list[Word] = []
    current_top = _w_top(sorted_words[0])
    for w in sorted_words:
        top = _w_top(w)
        if abs(top - current_top) > ROW_Y_TOLERANCE:
            rows.append(sorted(current_row, key=_w_x0))
            current_row = [w]
            current_top = top
        else:
            current_row.append(w)
    if current_row:
        rows.append(sorted(current_row, key=_w_x0))
    return rows


def _bin_row_into_columns(row_words: list[Word]) -> dict[str, str]:
    bins: dict[str, list[str]] = {name: [] for name in COLUMN_NAMES}
    for w in row_words:
        col = _column_for_x(_w_x0(w))
        if col is not None:
            bins[col].append(_w_text(w))
    return {col: " ".join(parts).strip() for col, parts in bins.items()}


def _is_header_row(cells: dict[str, str]) -> bool:
    return cells.get("Date", "").lower() == "date" and "status" in cells.get("Status", "").lower()


def _row_text(cells: dict[str, str]) -> str:
    return " ".join(v for v in cells.values() if v).strip()


def _is_metadata_or_footer(cells: dict[str, str]) -> bool:
    text = _row_text(cells).lower()
    if not text:
        return True
    markers = (
        "tng wallet transaction",
        "registered name",
        "wallet id",
        "account status",
        "generated date",
        "transaction period",
        "this is a system generated",
        "customer service through",
        "operating hours",
    )
    return any(m in text for m in markers)


def _finalize_row(cells: dict[str, list[str]], section: str) -> dict[str, object]:
    """Convert accumulated per-column word lists into the final raw row shape."""
    joined = {col: _join_pieces(cells.get(col, []), _JOIN_RULE[col]) for col in COLUMN_NAMES}

    parsed_date = _parse_iso_date(joined["Date"])
    out: dict[str, object] = {
        "Date": parsed_date.strftime("%Y-%m-%d") if parsed_date else joined["Date"],
        "Status": joined["Status"],
        "Transaction Type": joined["Transaction Type"],
        "Reference": joined["Reference"],
        "Description": joined["Description"],
        "Details": joined["Details"],
    }

    amount_value = _parse_money(joined["Amount (RM)"])
    tx_type = joined["Transaction Type"]
    direction = _classify(tx_type) or -1  # 0 -> default to outflow
    out["Amount (RM)"] = (
        direction * amount_value if amount_value is not None else joined["Amount (RM)"]
    )

    balance_value = _parse_money(joined["Balance"])
    balance_col = "Wallet Balance" if section == "wallet" else "GO+ Balance"
    out[balance_col] = balance_value if balance_value is not None else joined["Balance"]

    return out


class TngParser(StatementParser):
    name = "tng"
    source = "TNG eWallet"

    def detect(self, first_page_text: str) -> bool:
        haystack = first_page_text.upper()
        return (
            "TNG WALLET" in haystack
            or "TOUCH 'N GO EWALLET" in haystack
            or ("TOUCH" in haystack and "GO" in haystack and "WALLET" in haystack)
        )

    def extract_raw(self, pdf_path: Path) -> ParseResult:
        pdf_path = Path(pdf_path)
        full_text = _full_text(pdf_path)

        label = ACCOUNT_LABEL_PREFIX
        if m := WALLET_ID_RE.search(full_text):
            label = f"{label} {m.group(1)}"

        period_end = _parse_period_end(full_text)
        wallet_raw = RawSection(columns=WALLET_RAW_COLUMNS)
        go_plus_raw = RawSection(columns=GO_PLUS_RAW_COLUMNS)
        result = ParseResult(
            source_file=str(pdf_path),
            account_label=label,
            statement_date=period_end,  # used by normalize() for Daily Earnings date
            raw_sections={"wallet": wallet_raw, "go-plus": go_plus_raw},
        )
        if period_end is None:
            result.warnings.append(
                "Could not find 'Transaction Period' line; "
                "GO+ Daily Earnings will be dropped."
            )

        section = "wallet"
        open_row: dict[str, list[str]] | None = None

        def emit(row: dict[str, list[str]]) -> None:
            target = wallet_raw if section == "wallet" else go_plus_raw
            target.rows.append(_finalize_row(row, section))

        for page_words in _extract_pages(pdf_path):
            for row_words in _group_words_into_rows(page_words):
                cells = _bin_row_into_columns(row_words)
                if _is_metadata_or_footer(cells) or _is_header_row(cells):
                    continue
                if GO_PLUS_SECTION_RE.search(_row_text(cells)):
                    if open_row is not None:
                        emit(open_row)
                        open_row = None
                    section = "go_plus"
                    continue

                date_cell = cells.get("Date", "")
                if DATE_PREFIX_RE.match(date_cell):
                    if open_row is not None:
                        emit(open_row)
                    open_row = {col: [val] if val else [] for col, val in cells.items()}
                elif open_row is not None:
                    for col, val in cells.items():
                        if val:
                            open_row[col].append(val)

        if open_row is not None:
            emit(open_row)

        return result

    def normalize(self, result: ParseResult) -> None:
        wallet_raw = result.raw_sections.get("wallet")
        go_plus_raw = result.raw_sections.get("go-plus")
        if wallet_raw is None or go_plus_raw is None:
            return

        # Wallet rows -> transactions (drop GO+ sweeps).
        for raw_row in wallet_raw.rows:
            full = " ".join(str(v) for v in raw_row.values())
            if GO_PLUS_RE.search(full):
                continue
            tx = self._wallet_transaction_from_raw(raw_row, result.warnings)
            if tx is not None:
                result.transactions.append(tx)

        # GO+ rows -> aggregate Daily Earnings into a single synthetic Transaction.
        period_end = result.statement_date
        daily_earnings: list[tuple[date, float]] = []
        for raw_row in go_plus_raw.rows:
            full = " ".join(str(v) for v in raw_row.values())
            if not DAILY_EARNING_RE.search(full):
                continue
            date_str = str(raw_row.get("Date") or "")
            amount = raw_row.get("Amount (RM)")
            if isinstance(amount, float):
                de_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                daily_earnings.append((de_date, abs(amount)))

        if period_end is not None and daily_earnings:
            total = sum(amount for _, amount in daily_earnings)
            if total >= MIN_AGGREGATED_DAILY_EARNINGS:
                first = min(d for d, _ in daily_earnings)
                last = max(d for d, _ in daily_earnings)
                result.transactions.append(
                    Transaction(
                        date=period_end,
                        notes=(
                            f"GO+ Daily Earnings ({len(daily_earnings)} entries, "
                            f"{first} to {last})"
                        ),
                        amount=round(total, 2),
                        source=self.source,
                    )
                )

    def _wallet_transaction_from_raw(
        self, raw: dict[str, object], warnings: list[str]
    ) -> Transaction | None:
        date_str = raw.get("Date")
        amount = raw.get("Amount (RM)")
        if not isinstance(date_str, str) or not isinstance(amount, float):
            return None
        tx_date = _parse_iso_date(date_str)
        if tx_date is None:
            return None

        tx_type = str(raw.get("Transaction Type") or "")
        description = str(raw.get("Description") or "")
        description_one_line = " ".join(description.split())
        if not tx_type or _classify(tx_type) == 0:
            warnings.append(
                f"Unknown transaction type {tx_type!r} for {tx_date}; treating as outflow."
            )
        notes = " | ".join(p for p in (tx_type, description_one_line) if p) or "(no description)"
        return Transaction(date=tx_date, notes=notes, amount=amount, source=self.source)
