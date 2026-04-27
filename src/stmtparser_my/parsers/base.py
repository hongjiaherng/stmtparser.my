"""Interface every statement parser must implement.

Each parser is a two-stage pipeline:

1. ``extract_raw(pdf_path) -> ParseResult`` reads the PDF and produces the
   lossless raw rows (``result.raw_sections``) plus statement metadata
   (account label, statement date, opening/closing balances). The
   ``transactions`` list is left empty.

2. ``normalize(result) -> None`` reads the raw rows and produces the
   downstream-ready ``transactions`` list, mutating ``result`` in place.

The default ``parse(pdf_path)`` simply chains them, so day-to-day callers
need not care about the split. The split is for testability (each stage is
unit-testable without the other) and for clarity (PDF parsing concerns
stay in stage 1, opinionated transformations stay in stage 2).

Adding a new parser:

1. Create a module under ``stmt_parser.parsers`` with a subclass that sets
   ``name`` as a class attribute and implements ``detect()``,
   ``extract_raw()``, and ``normalize()``.
2. Instantiate it in ``parsers/__init__.py`` and add to ``REGISTRY``.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from ..transactions import ParseResult


class StatementParser(ABC):
    #: Registry key, also used in CLI's ``--type`` flag. Lowercase, snake_case.
    name: str

    @abstractmethod
    def detect(self, first_page_text: str) -> bool:
        """Return True if this parser handles the given PDF."""

    @abstractmethod
    def extract_raw(self, pdf_path: Path) -> ParseResult:
        """Stage 1: PDF -> ``raw_sections`` + statement metadata.

        ``transactions`` is empty in the returned ``ParseResult``.
        """

    @abstractmethod
    def normalize(self, result: ParseResult) -> None:
        """Stage 2: populate ``result.transactions`` from ``result.raw_sections``."""

    def parse(self, pdf_path: Path) -> ParseResult:
        """Run both stages."""
        result = self.extract_raw(pdf_path)
        self.normalize(result)
        return result
