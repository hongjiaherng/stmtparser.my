# stmtparser.my

Banks in Malaysia give you a PDF. You need data.

Every statement downloaded from Maybank or Touch 'n Go ends up as a locked-down PDF, the format that was never meant to be analysed, tracked, or imported into anything. If you want to do budgeting, spending analysis, or just know where your money went last month, you are stuck reading line by line or typing it in yourself. This tool does that work for you.

`stmtparser.my` pulls the transaction rows out of Malaysian bank and e-wallet PDF statements and writes them as clean, structured files you can actually use.

## Why this exists

I budget with [Actual](https://actualbudget.org/), which only syncs transactions directly from European, US, and Brazilian banks. For Malaysian accounts the only option is keying every line in by hand. This tool turns the PDF statement into a CSV that imports straight into Actual (or any other budgeting tool), so the manual step goes away.

## Supported statements

- Maybank Personal Saver (savings account)
- Maybank Credit Card
- Touch 'n Go eWallet (wallet + GO+ sections)

## Install

Requires [uv](https://docs.astral.sh/uv/) (`winget install --id=astral-sh.uv` on Windows, `brew install uv` on macOS, or [the curl script](https://docs.astral.sh/uv/getting-started/installation/) elsewhere).

Install `stmtparser` as a global tool, straight from the GitHub repo:

```bash
uv tool install --from git+https://github.com/hongjiaherng/stmtparser.my stmtparser
```

This drops a `stmtparser` executable on your `PATH`. No need to clone the repo or manage a venv. Run from anywhere:

```bash
stmtparser path/to/statement.pdf
```

To upgrade later:

```bash
uv tool upgrade stmtparser
```

To uninstall:

```bash
uv tool uninstall stmtparser
```

## Usage

```bash
# Parse one or more PDFs. Output folder is created in the current directory.
stmtparser path/to/statement.pdf

# Force a specific parser if auto-detect fails:
stmtparser path/to/file.pdf --type maybank_savings

# Write output to a specific directory:
stmtparser path/to/file.pdf -o path/to/output

# Process multiple PDFs in one go:
stmtparser path/to/jan.pdf path/to/feb.pdf path/to/mar.pdf -o out/
```

## Develop locally

```bash
git clone https://github.com/hongjiaherng/stmtparser.my
cd stmtparser.my
uv sync

# Run from the source tree without installing:
uv run stmtparser tests/fixtures/maybank_savings_demo.pdf -o out/

# Quality checks
uv run ruff check
uv run ty check
uv run pytest
```

## Output

For each input PDF, `stmtparser` writes a folder named `<YYYYMMDD>__<account-label>/` (e.g. `20260228__Maybank_Personal_Saver_999999-888888/`) containing three files:

- `normalized.csv`: columns `Date, Notes, Amount` with a signed `Amount` (positive = money in, negative = money out). Ready to import into any budgeting tool or spreadsheet. For my case, Actual Budget.
- `raw.json`: a hopefully "lossless" mirror of the PDF table, organised by section.
  Schema:

  ```json
  {
    "<section-name>": {
      "columns": ["Date", "Status", "..."],
      "rows": [{ "Date": "...", "Status": "...", "...": "..." }]
    }
  }
  ```

  Maybank Savings and CC use a single section named `transactions`. Touch 'n Go has two stacked tables: `wallet` and `go-plus`.
- A copy of the source PDF, so the folder is self-contained and re-parseable without hunting for the original.

The `YYYYMMDD` prefix is the statement date, so output folders sort chronologically. If the parser can't extract a statement date, it falls back to the input PDF's stem.

In `normalized.csv`, the TnG `go-plus` section aggregates all `GO+ Daily Earnings` rows into one synthetic transaction dated at the statement period end.

## Design notes

- **Two-stage parsing.** Each parser implements `extract_raw` (PDF to raw rows and statement metadata) and `normalize` (raw rows to the universal `Transaction` list). The default `parse` chains them. The split makes raw extraction independently testable and keeps opinionated transformations out of the PDF parsing layer.
- **No OCR.** All supported statement types ship as text-based PDFs, so `pdfplumber` is the only runtime dependency. If a TnG export yields no extractable text, re-export it from the TnG app as a normal (non-image) PDF, same as you would for Maybank.
- **No PII in code.** Address shapes and bilingual page headers are matched via generic regex with no hardcoded names, account numbers, or street fragments in the source.

## Adding a new parser

1. Create a new module in `src/stmtparser_my/parsers/` with a `StatementParser` subclass (see `parsers/base.py`). Set the `name` and `label_prefix` class attributes; implement `detect`, `extract_raw`, and `normalize`.
2. Register an instance in `REGISTRY` in `parsers/__init__.py`. Iteration order matters for auto-detection: more-specific markers first.

Both the CLI and the format dispatcher pick the new parser up automatically.

## Tests

End-to-end tests run against three redacted demo PDFs in `tests/fixtures/`:

- `maybank_savings_demo.pdf`
- `maybank_cc_demo.pdf`
- `tng_demo.pdf`

These are real statements with personal information destructively redacted (cardholder name, mailing address, full account/wallet IDs, counterparty names). Layout, byte positions, and `pdfplumber` extraction behavior are preserved, so the tests exercise the same code paths as a clean statement.

Each test asserts concrete known values: statement dates, opening/closing balances, transaction counts, signage (purchases negative, payments positive), description-list shapes, GO+ sweep filtering, and Daily Earnings aggregation.

Caveats from redaction:

- The Maybank Savings demo had several transaction rows removed by the redaction pass, so opening + net != closing. The reconciliation test asserts the parser surfaces the expected balance-mismatch warning rather than passing silently. On a complete statement, the assertion would flip to `warnings == []`.
- The Maybank CC demo lost its FX-continuation row (`TRANSACTED AMOUNT USD …`), so the FX-line code path is not e2e-covered. The logic is still in place; it just isn't exercised by the published fixture.

To run:

```bash
uv run pytest
```