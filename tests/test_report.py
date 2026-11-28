from __future__ import annotations

import hashlib
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "webapp/data/report"


def test_report_pdf_was_printed_from_the_current_report():
    """The PDF download must match the report next to it. Refresh it with `make report-pdf`."""
    printed_from = (REPORT / "s2change-report.pdf.source").read_text().split()[0]
    assert printed_from == hashlib.sha256((REPORT / "report.md").read_bytes()).hexdigest(), "stale PDF: run `make report-pdf`"
