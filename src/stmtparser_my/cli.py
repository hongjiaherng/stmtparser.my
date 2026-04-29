"""CLI entry point: PDF statements -> per-statement output folder."""

import argparse
import re
import shutil
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from stmtparser_my.detect import UNKNOWN, detect_format
from stmtparser_my.parsers import REGISTRY
from stmtparser_my.transactions import ParseResult, write_normalized_csv, write_raw_json

DEFAULT_OUTPUT_DIR = Path(".")


def _safe_filename(s: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\- ]+", "", s).strip()
    return re.sub(r"\s+", "_", cleaned) or "statement"


def process_one(pdf: Path, output_root: Path, forced_type: str | None) -> ParseResult:
    fmt = forced_type or detect_format(pdf)
    if fmt == UNKNOWN:
        raise ValueError(
            f"Could not detect statement type for {pdf}. "
            f"Pass --type explicitly (one of: {', '.join(REGISTRY)})."
        )

    result = REGISTRY[fmt].parse(pdf)

    label_part = (
        _safe_filename(result.account_label) if result.account_label else "statement"
    )
    # Prefix with statement date (YYYYMMDD) so output folders sort
    # chronologically. Fall back to the PDF stem when the parser couldn't
    # extract a date.
    date_part = (
        result.statement_date.strftime("%Y%m%d")
        if result.statement_date
        else pdf.stem
    )
    out_dir = output_root / f"{date_part}__{label_part}"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_normalized_csv(result.transactions, out_dir / "normalized.csv")
    if result.raw_sections:
        write_raw_json(result.raw_sections, out_dir / "raw.json")
    # Keep the source PDF alongside its derived outputs so each folder is
    # self-contained and re-parseable without hunting for the original.
    shutil.copy2(pdf, out_dir / pdf.name)

    raw_summary = ", ".join(
        f"{k}={len(s.rows)}" for k, s in result.raw_sections.items()
    )
    print(
        f"[{fmt}] {pdf.name}: {len(result.transactions)} normalized"
        + (f" | raw[{raw_summary}]" if raw_summary else "")
        + f" -> {out_dir}"
    )
    if result.opening_balance is not None and result.closing_balance is not None:
        print(
            f"   Balances: opening {result.opening_balance:.2f}, closing {result.closing_balance:.2f}"
        )
    for w in result.warnings:
        print(f"   ! {w}", file=sys.stderr)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stmtparser",
        description=(
            "Parse bank/wallet PDF statements. Each PDF produces a folder "
            "containing `normalized.csv` (Date, Notes, Amount) and "
            "`raw.json` (mirror of the PDF table, sections keyed by name).\n"
            f"Currently supports: {', '.join(REGISTRY)}."
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"stmtparser {version('stmtparser.my')}",
    )
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to convert")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Root directory for output folders (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--type",
        choices=sorted(REGISTRY),
        default=None,
        help="Force a specific statement type. Default: auto-detect.",
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    failed = 0
    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"error: {pdf} does not exist", file=sys.stderr)
            failed += 1
            continue
        try:
            process_one(pdf, args.output_dir, args.type)
        except Exception as e:
            print(f"error processing {pdf}: {e}", file=sys.stderr)
            failed += 1

    return 1 if failed else 0
