# stmtparser.my

Banks in Malaysia give you a PDF. You need data.

Every statement downloaded from Maybank or Touch 'n Go ends up as a locked-down
PDF, the format that was never meant to be analysed, tracked, or imported into
anything. If you want to do budgeting, spending analysis, or just know where your
money went last month, you are stuck reading line by line or typing it in yourself.
This tool does that work for you.

`stmtparser.my` pulls the transaction rows out of Malaysian bank and e-wallet PDF
statements and writes them as clean, structured files you can actually use.

## Supported statements

- Maybank Personal Saver (savings account)
- Maybank Credit Card
- Touch 'n Go eWallet (wallet + GO+ sections)

## Quick start

```bash
uv sync

# Parse one or more PDFs. Output folder is created in the current directory.
uv run stmtparser path/to/statement.pdf

# Force a specific parser if auto-detect fails:
uv run stmtparser path/to/file.pdf --type maybank_savings

# Write output to a specific directory:
uv run stmtparser path/to/file.pdf -o path/to/output

# Quality gates
uv run ruff check
uv run ty check
uv run pytest
```

## Output

For each input PDF, `stmtparser` writes a folder containing two files:

- `normalized.csv`: columns `Date, Notes, Amount` with a signed `Amount`
  (positive = money in, negative = money out). Ready to import into any
  budgeting tool or spreadsheet.
- `raw.json`: a lossless mirror of the PDF table, organised by section.
  Schema:

  ```json
  {
    "<section-name>": {
      "columns": ["Date", "Status", "..."],
      "rows": [{ "Date": "...", "Status": "...", "...": "..." }]
    }
  }
  ```

  Maybank Savings and CC use a single section named `transactions`. Touch 'n
  Go has two stacked tables: `wallet` and `go-plus`.

The TnG `go-plus` section aggregates all `GO+ Daily Earnings` rows into one
synthetic transaction dated at the statement period end.

## Design notes

- **Two-stage parsing.** Each parser implements `extract_raw` (PDF to raw rows
  and statement metadata) and `normalize` (raw rows to the universal `Transaction`
  list). The default `parse` chains them. The split makes raw extraction
  independently testable and keeps opinionated transformations out of the PDF
  parsing layer.
- **No OCR.** All supported statement types ship as text-based PDFs, so
  `pdfplumber` is the only runtime dependency. If a TnG export yields no
  extractable text, re-export it from the TnG app as a normal (non-image) PDF.
- **No PII in code.** Address shapes and bilingual page headers are matched via
  generic regex with no hardcoded names, account numbers, or street fragments in
  the source.

## Adding a new parser

1. Create a new module in `src/stmtparser_my/parsers/` with a `StatementParser`
   subclass (see `parsers/base.py`). Set the `name` and `source` class attributes;
   implement `detect`, `extract_raw`, and `normalize`.
2. Register an instance in `REGISTRY` in `parsers/__init__.py`.

Both the CLI and the format dispatcher pick the new parser up automatically.

## TODOs

- [ ] Replace the personal PDFs in `tests/fixtures/` with shareable synthetic fixtures (one per parser) and commit them. Then rewrite the test suite around them with explicit per-fixture assertions (known opening/closing pair for the Savings reconciliation check, a CC continuation line, a TnG GO+ sweep + Daily Earnings + DuitNow row), instead of the current glob-and-skip-if-empty pattern that runs almost nothing on a clean checkout.
