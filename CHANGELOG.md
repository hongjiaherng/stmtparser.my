# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-04-29

### Added

- Maybank Personal Saver parser: extracts every transaction line plus bilingual page-header metadata (account number, statement date, opening / closing balance), and reconciles `opening + net == closing`.
- Maybank Credit Card parser: extracts posting/transaction date pairs, signs amounts (purchases negative, payments/credits positive), splits merchant + location into separate description elements, and preserves foreign-currency continuation lines (`TRANSACTED AMOUNT USD …`).
- Touch 'n Go eWallet parser: position-aware extraction of both stacked tables (`TNG WALLET TRANSACTION` and `GO+ TRANSACTION`), wallet-side GO+ sweep filtering, and aggregation of `GO+ Daily Earnings` into one synthetic transaction dated at the statement period end.
- `stmtparser` CLI: accepts one or many PDFs, auto-detects statement type, writes a per-statement folder `<YYYYMMDD>__<account-label>/` containing `normalized.csv` (`Date, Notes, Amount`), `raw.json` (lossless mirror of the PDF table), and a copy of the source PDF. `--type` forces a specific parser, `--version` prints the installed version.
- End-to-end tests against three redacted demo PDFs committed to `tests/fixtures/`.
