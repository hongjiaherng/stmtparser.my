"""Smoke tests against PDFs in this package's ``fixtures/`` directory.

For now ``fixtures/`` holds the maintainer's real personal statements (not
committed). When synthetic samples land they will replace what is there and
become git-tracked. Tests skip themselves if no PDFs are present.
"""

from pathlib import Path

import pytest

from stmtparser_my.detect import detect_format
from stmtparser_my.parsers import REGISTRY

maybank_savings = REGISTRY["maybank_savings"]
maybank_cc = REGISTRY["maybank_cc"]
tng = REGISTRY["tng"]

FIXTURES = Path(__file__).parent / "fixtures"

# Bin every fixture PDF by what detect_format() returns rather than hard-coding
# filename patterns. Avoids baking the maintainer's account numbers into the
# test code and works with any naming convention.
ALL_PDFS = sorted(FIXTURES.glob("*.pdf"))
_BY_TYPE: dict[str, list[Path]] = {"maybank_savings": [], "maybank_cc": [], "tng": []}
for _p in ALL_PDFS:
    _fmt = detect_format(_p)
    if _fmt in _BY_TYPE:
        _BY_TYPE[_fmt].append(_p)
SAVINGS_PDFS = _BY_TYPE["maybank_savings"]
CC_PDFS = _BY_TYPE["maybank_cc"]
TNG_PDFS = _BY_TYPE["tng"]


@pytest.mark.parametrize("pdf", SAVINGS_PDFS, ids=lambda p: p.name)
def test_maybank_savings_reconciles(pdf: Path) -> None:
    r = maybank_savings.parse(pdf)
    assert r.opening_balance is not None, "opening balance missing"
    assert r.closing_balance is not None, "closing balance missing"
    assert r.transactions, "no transactions parsed"
    net = sum(t.amount for t in r.transactions)
    assert abs((r.opening_balance + net) - r.closing_balance) < 0.01, (
        f"opening {r.opening_balance:.2f} + net {net:.2f} != closing {r.closing_balance:.2f}"
    )
    assert r.warnings == [], f"unexpected warnings: {r.warnings}"
    for tx in r.transactions:
        assert tx.notes


@pytest.mark.parametrize("pdf", CC_PDFS, ids=lambda p: p.name)
def test_maybank_cc_basic(pdf: Path) -> None:
    r = maybank_cc.parse(pdf)
    assert r.transactions, "no transactions parsed"
    for tx in r.transactions:
        assert tx.notes


@pytest.mark.parametrize("pdf", TNG_PDFS, ids=lambda p: p.name)
def test_tng_basic(pdf: Path) -> None:
    r = tng.parse(pdf)
    assert r.transactions, "no transactions parsed"
    assert r.warnings == [], f"unexpected warnings: {r.warnings}"
    for tx in r.transactions:
        assert tx.notes
        # GO+ sweep rows are filtered out; the only legitimate "GO+" mention
        # is the aggregated Daily Earnings synthetic row.
        if "GO+" in tx.notes:
            assert tx.notes.startswith("GO+ Daily Earnings"), (
                f"unexpected GO+ row leaked through: {tx.notes!r}"
            )


@pytest.mark.parametrize("pdf", TNG_PDFS, ids=lambda p: p.name)
def test_tng_aggregates_go_plus_daily_earnings(pdf: Path) -> None:
    """GO+ Daily Earnings rows are summed into one synthetic transaction.

    The aggregate is dated at the statement period end, has positive amount,
    notes start with ``GO+ Daily Earnings``, and no individual Daily Earnings
    rows leak through.
    """
    r = tng.parse(pdf)
    de_rows = [t for t in r.transactions if t.notes.startswith("GO+ Daily Earnings")]
    assert len(de_rows) <= 1, "expected at most one aggregated Daily Earnings row"
    if de_rows:
        agg = de_rows[0]
        assert agg.amount > 0, "Daily Earnings aggregate must be positive"
        assert "entries" in agg.notes
    # No surviving GO+ Cash In / Cash Out rows from the GO+ section.
    assert not any("GO+ Cash" in t.notes for t in r.transactions)


@pytest.mark.parametrize("pdf", TNG_PDFS, ids=lambda p: p.name)
def test_tng_signage_for_duitnow_types(pdf: Path) -> None:
    """DUITNOW_RECEIVEFROM is an inflow (+); DuitNow QR is an outflow (-)."""
    r = tng.parse(pdf)
    receive_rows = [t for t in r.transactions if "DUITNOW_RECEI" in t.notes]
    qr_rows = [t for t in r.transactions if "DuitNow QR" in t.notes]
    for tx in receive_rows:
        assert tx.amount > 0, f"DUITNOW_RECEIVEFROM row should be inflow: {tx}"
    for tx in qr_rows:
        assert tx.amount < 0, f"DuitNow QR row should be outflow: {tx}"


@pytest.mark.parametrize(
    ("pdf", "expected"),
    [
        *((p, "maybank_savings") for p in SAVINGS_PDFS),
        *((p, "maybank_cc") for p in CC_PDFS),
        *((p, "tng") for p in TNG_PDFS),
    ],
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_detect_format(pdf: Path, expected: str) -> None:
    assert detect_format(pdf) == expected
