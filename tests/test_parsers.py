"""End-to-end tests against the redacted demo fixtures in ``fixtures/``.

The three PDFs under ``tests/fixtures/`` are real Maybank/TnG statements
with PII destructively redacted (cardholder name, address, full account
number, counterparty names). They preserve byte layout and pdfplumber
extraction behavior, so they are a faithful e2e signal.

Caveat — the redaction tool also removed several transaction rows
wholesale. As a result:

- Maybank Savings no longer satisfies opening + net == closing. We assert
  the parser correctly surfaces the reconciliation warning instead of
  passing silently. On a non-redacted statement the test should be
  flipped to ``warnings == []`` (the original strongest correctness
  signal we have for this parser).
- The Maybank CC fixture lost its FX-continuation row (TRANSACTED AMOUNT
  USD …), so the FX-continuation code path is not e2e-covered here.
"""

from datetime import date
from pathlib import Path

import pytest

from stmtparser_my.detect import detect_format
from stmtparser_my.parsers import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"
SAVINGS_PDF = FIXTURES / "maybank_savings_demo.pdf"
CC_PDF = FIXTURES / "maybank_cc_demo.pdf"
TNG_PDF = FIXTURES / "tng_demo.pdf"


# --- detect ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("pdf", "expected"),
    [
        (SAVINGS_PDF, "maybank_savings"),
        (CC_PDF, "maybank_cc"),
        (TNG_PDF, "tng"),
    ],
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_detect_format(pdf: Path, expected: str) -> None:
    assert detect_format(pdf) == expected


# --- Maybank Personal Saver ------------------------------------------------


def test_maybank_savings_metadata() -> None:
    r = REGISTRY["maybank_savings"].parse(SAVINGS_PDF)
    assert r.statement_date == date(2026, 2, 28)
    assert r.account_label.startswith("Maybank Personal Saver")
    assert r.opening_balance == pytest.approx(1019.56)
    assert r.closing_balance == pytest.approx(18.74)


def test_maybank_savings_transactions_parsed() -> None:
    r = REGISTRY["maybank_savings"].parse(SAVINGS_PDF)
    assert len(r.transactions) == 13
    assert len(r.raw_sections["transactions"].rows) == 13
    for tx in r.transactions:
        assert tx.notes
        assert tx.amount != 0


def test_maybank_savings_reconciliation_warns_on_redaction_gap() -> None:
    # Redaction dropped several rows from this fixture, so the parser must
    # surface a balance-mismatch warning rather than producing silently
    # incorrect output. On a complete statement, swap this for
    # `assert r.warnings == []`.
    r = REGISTRY["maybank_savings"].parse(SAVINGS_PDF)
    assert any("Balance mismatch" in w for w in r.warnings)


def test_maybank_savings_filters_page_marker_noise() -> None:
    # The page footer "000005 MUKA/ 頁/PAGE : 5" used to leak into
    # transaction descriptions because the noise pattern was anchored to
    # start-of-line. Make sure no description line still carries it.
    r = REGISTRY["maybank_savings"].parse(SAVINGS_PDF)
    for row in r.raw_sections["transactions"].rows:
        desc = row["TRANSACTION DESCRIPTION"]
        assert isinstance(desc, list)
        for piece in desc:
            assert isinstance(piece, str)
            assert "MUKA/" not in piece
            assert "PAGE" not in piece


# --- Maybank Credit Card ---------------------------------------------------


def test_maybank_cc_metadata() -> None:
    r = REGISTRY["maybank_cc"].parse(CC_PDF)
    assert r.statement_date == date(2026, 1, 12)
    assert r.account_label.startswith("Maybank Credit Card")


def test_maybank_cc_transactions_parsed() -> None:
    r = REGISTRY["maybank_cc"].parse(CC_PDF)
    assert len(r.transactions) == 15
    for tx in r.transactions:
        assert tx.notes


def test_maybank_cc_signage() -> None:
    # Card is treated as a liability: purchases are outflows (negative),
    # payments/refunds/rebates are inflows (positive).
    r = REGISTRY["maybank_cc"].parse(CC_PDF)
    payments = [t for t in r.transactions if "PYMT@MAYBANK2U" in t.notes]
    assert payments, "expected at least one payment-received row"
    for t in payments:
        assert t.amount > 0
    rebates = [t for t in r.transactions if "CASH REBATE" in t.notes]
    assert rebates, "expected cash-rebate rows"
    for t in rebates:
        assert t.amount > 0
    purchases = [
        t
        for t in r.transactions
        if "Shopee" in t.notes or "GRAB" in t.notes or "Google" in t.notes
    ]
    assert purchases, "expected purchase rows"
    for t in purchases:
        assert t.amount < 0


def test_maybank_cc_description_split_into_merchant_and_location() -> None:
    # The CC parser splits each description on 4+ whitespace runs so that
    # merchant name and location land in separate elements of the row's
    # description list. Confirm at least one row has the multi-element shape.
    r = REGISTRY["maybank_cc"].parse(CC_PDF)
    multi = [
        row
        for row in r.raw_sections["transactions"].rows
        if isinstance(desc := row.get("Transaction Description"), list)
        and len(desc) >= 2
    ]
    assert multi, "expected at least one row split into merchant + location"


# --- Touch 'n Go eWallet ---------------------------------------------------


def test_tng_metadata() -> None:
    r = REGISTRY["tng"].parse(TNG_PDF)
    assert r.statement_date == date(2026, 4, 27)
    assert r.account_label.startswith("TNG eWallet")


def test_tng_raw_sections_extracted() -> None:
    r = REGISTRY["tng"].parse(TNG_PDF)
    assert len(r.raw_sections["wallet"].rows) == 2
    assert len(r.raw_sections["go-plus"].rows) == 3


def test_tng_filters_wallet_to_go_plus_sweep() -> None:
    # The wallet-side "Reload via GO+ Balance" row is a sweep mirror of a
    # GO+ Cash Out and would double-count if kept. The "Payment" row that
    # doesn't mention GO+ survives.
    r = REGISTRY["tng"].parse(TNG_PDF)
    wallet_normalized = [
        t for t in r.transactions if not t.notes.startswith("GO+ Daily Earnings")
    ]
    assert len(wallet_normalized) == 1
    assert wallet_normalized[0].amount == pytest.approx(-50.0)


def test_tng_aggregates_go_plus_daily_earnings() -> None:
    r = REGISTRY["tng"].parse(TNG_PDF)
    daily = [t for t in r.transactions if t.notes.startswith("GO+ Daily Earnings")]
    assert len(daily) == 1
    agg = daily[0]
    assert agg.amount > 0
    assert agg.date == date(2026, 4, 27)
    assert "3 entries" in agg.notes


def test_tng_no_individual_go_plus_rows_leak() -> None:
    r = REGISTRY["tng"].parse(TNG_PDF)
    for tx in r.transactions:
        if "GO+" in tx.notes:
            assert tx.notes.startswith("GO+ Daily Earnings"), (
                f"unexpected GO+ row leaked through: {tx.notes!r}"
            )
