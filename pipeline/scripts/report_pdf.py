"""Print the Report tab to webapp/data/report/s2change-report.pdf, so the PDF has the same diagrams and figures as the app.

    make report-pdf        # = python -m s2cd.site, then this script (it starts and stops its own Vite dev server)

The page is printed with its print stylesheet (webapp/viewer/src/styles/report.css): no app chrome, A4, page breaks kept out of
figures and tables. Lazy images are forced to load and collapsed sections opened before printing. Chrome embeds the
full-resolution PNGs (about 10 MB), so the images are then re-encoded at 150 dpi (about 2.5 MB, still sharp in print).
The SHA-256 of the report.md it was printed from goes to s2change-report.pdf.source next to it; tests/test_report.py fails
when the PDF is older than the report, so a stale PDF cannot ship unnoticed.
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pymupdf
from playwright.sync_api import sync_playwright

OUT = Path("webapp/data/report/s2change-report.pdf")
STAMP = OUT.with_suffix(".pdf.source")
SOURCE = Path("webapp/data/report/report.md")
PORT = 5199


def source_hash(path: Path = SOURCE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def dev_server(port: int = PORT):
    """The viewer's Vite dev server (it serves webapp/data at /web/), for the duration of the print."""
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    proc = subprocess.Popen(cmd, cwd="webapp/viewer", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                socket.create_connection(("localhost", port), timeout=1).close()  # Vite may bind ::1 only
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError(f"the viewer dev server did not start on port {port}")
        yield f"http://localhost:{port}/#/report"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


READY = """() => {
  const doc = document.querySelector('.rp-article .prose');
  if (!doc || document.querySelector('.prose pre code.language-mermaid')) return false;
  return [...doc.querySelectorAll('img')].every((i) => i.complete && i.naturalWidth > 0);
}"""


def downsample(doc: pymupdf.Document, above: int = 170, target: int = 150, quality: int = 82) -> None:
    """Re-encode page images printed above `above` dpi at `target` dpi.

    Not doc.rewrite_images: it also rewrites the bitmap glyphs of colour-emoji Type 3 fonts and leaves them blank.
    """
    done: set[int] = set()
    for page in doc:
        for xref, smask, width, *_ in page.get_images(full=True):
            rects = page.get_image_rects(xref)
            if xref in done or not rects:
                continue
            inches = max(r.width for r in rects) / 72
            if width / inches <= above:
                continue
            pix = pymupdf.Pixmap(doc, xref)
            if smask:
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
            scale = target * inches / width
            pix = pymupdf.Pixmap(pix, max(1, round(pix.width * scale)), max(1, round(pix.height * scale)), None)
            if pix.alpha:
                page.replace_image(xref, pixmap=pix)
            else:
                page.replace_image(xref, stream=pix.tobytes("jpeg", jpg_quality=quality))
            done.add(xref)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    with dev_server() as url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1600})
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector(".rp-article .prose", timeout=60_000)
        page.evaluate("""() => {
          for (const i of document.querySelectorAll('.prose img')) i.loading = 'eager';
          for (const d of document.querySelectorAll('.prose details')) d.open = true;
        }""")
        page.wait_for_function(READY, timeout=60_000)
        page.emulate_media(media="print")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(
            path=str(args.out),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font:8px system-ui,-apple-system,Helvetica,Arial,sans-serif;color:#64748b;'
                'padding:0 14mm;display:flex;justify-content:space-between">'
                "<span>s2change · change detection at an open-pit copper mine, NW Zambia</span>"
                '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
            ),
        )
        browser.close()
    doc = pymupdf.open(args.out)
    downsample(doc)
    doc.set_metadata({**doc.metadata, "title": "s2change report"})
    tmp = args.out.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    tmp.replace(args.out)
    STAMP.write_text(f"{source_hash()}  report.md\n")
    print(f"{args.out}: {args.out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
