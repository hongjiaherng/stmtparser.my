"""Identify which parser handles a given statement PDF.

This module owns PDF I/O for detection (one ``pdfplumber.open`` per file) and
delegates the actual marker-matching to each parser's ``detect()`` function.
"""

from pathlib import Path

import pdfplumber

from .parsers import REGISTRY

UNKNOWN = "unknown"


def detect_format(pdf_path: Path) -> str:
    """Return the registry key of the matching parser, or ``"unknown"``."""
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        first_page_text = pdf.pages[0].extract_text() or ""

    for name, parser in REGISTRY.items():
        if parser.detect(first_page_text):
            return name
    return UNKNOWN
