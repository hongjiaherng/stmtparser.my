"""Statement parsers.

Each parser implements ``StatementParser`` (see ``base.py``) and is registered
in ``REGISTRY`` keyed by its short name. Detection order matters: the first
parser whose ``detect()`` returns True wins, so list more-specific markers
first.
"""

from .base import StatementParser
from .maybank_cc import MaybankCCParser
from .maybank_savings import MaybankSavingsParser
from .tng import TngParser

# Iteration order matters for detection: the first parser whose ``detect()``
# returns True wins, so list more-specific markers first.
REGISTRY: dict[str, StatementParser] = {
    p.name: p
    for p in (
        MaybankSavingsParser(),
        MaybankCCParser(),
        TngParser(),
    )
}

__all__ = ["REGISTRY", "StatementParser"]
