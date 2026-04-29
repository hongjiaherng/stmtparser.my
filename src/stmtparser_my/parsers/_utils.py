"""Helpers shared across parser modules."""


def parse_amount(raw: str) -> float:
    """Parse a comma-separated decimal string (e.g. ``"1,234.56"``)."""
    return float(raw.replace(",", ""))
