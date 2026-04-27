"""Common parser output types and CSV writers.

Each parsed PDF produces a ``ParseResult`` that carries:

- ``transactions``: the normalized rows, schema ``Date, Notes, Amount``
  (signed amount).
- ``raw_sections``: the lossless rows extracted directly from the PDF
  table. Each section keeps its own native column set (e.g. TnG's wallet
  section has 8 columns including Status, Reference, Details). Used for
  archival / spot-checking / future re-processing.
"""

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

NORMALIZED_FIELDS: tuple[str, ...] = ("Date", "Notes", "Amount")


@dataclass
class Transaction:
    date: date
    notes: str
    amount: float  # signed: + inflow, - outflow
    source: str

    def as_row(self) -> dict[str, str]:
        return {
            "Date": self.date.strftime("%Y-%m-%d"),
            "Notes": self.notes,
            "Amount": f"{self.amount:.2f}",
        }


@dataclass
class RawSection:
    """A lossless mirror of one tabular section in the PDF.

    ``rows`` are dicts keyed by column name. Cell value types vary by column:

    - Date columns: ``str`` in ``YYYY-MM-DD``.
    - Amount/balance columns: signed ``float``.
    - Description columns that may wrap across multiple PDF lines: ``list[str]``,
      one element per PDF line.
    - Everything else: ``str``.
    """

    columns: tuple[str, ...]
    rows: list[dict[str, object]] = field(default_factory=list)


@dataclass
class ParseResult:
    source_file: str
    account_label: str
    transactions: list[Transaction] = field(default_factory=list)
    statement_date: date | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    warnings: list[str] = field(default_factory=list)
    # All sections of one statement land in a single ``raw.json``; keys are the
    # section names (``"transactions"`` for single-section parsers, multiple
    # like ``"wallet"`` / ``"go-plus"`` for parsers with stacked tables).
    raw_sections: dict[str, RawSection] = field(default_factory=dict)


def write_normalized_csv(transactions: Iterable[Transaction], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(NORMALIZED_FIELDS))
        writer.writeheader()
        for tx in transactions:
            writer.writerow(tx.as_row())


def write_raw_json(sections: dict[str, RawSection], output_path: Path) -> None:
    """Write all raw sections of one statement to a single JSON file.

    Schema::

        {
          "<section-key>": {
            "columns": ["Date", "Status", ...],
            "rows": [{"Date": "...", "Status": "...", ...}, ...]
          },
          ...
        }

    Single-section parsers (e.g. Maybank) use one section key like
    ``"transactions"``. Multi-section parsers (e.g. TnG) emit one key per
    section (``"wallet"``, ``"go-plus"``).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: {
            "columns": list(section.columns),
            "rows": [{c: row.get(c, "") for c in section.columns} for row in section.rows],
        }
        for key, section in sections.items()
    }
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def collapse_whitespace(s: str) -> str:
    return " ".join(s.split())
