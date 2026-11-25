"""Record docs/figures/demo.gif: a scripted tour of the public parts of the live app.

uv run --with playwright python scripts/demo_gif.py [--shots]   (needs ffmpeg; --shots saves one PNG per step instead)
"""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

SITE = "https://s2change.dzaky.ai.id"
OUT = Path("docs/figures/demo.gif")
SIZE = {"width": 1440, "height": 820}


def drag_swipe(page: Page, frm: float, to: float, steps: int = 40) -> None:
    box = page.locator(".swipe-handle").bounding_box()
    if not box:
        return
    y = box["y"] + box["height"] / 2
    w = SIZE["width"]
    page.mouse.move(w * frm, y)
    page.mouse.down()
    page.mouse.move(w * to, y, steps=steps)
    page.mouse.up()


def tour(page: Page, shot) -> None:
    page.goto(f"{SITE}/#/report")
    page.wait_for_selector(".prose img")
    page.wait_for_timeout(1500)
    shot("report")
    page.mouse.move(700, 400)
    for _ in range(6):
        page.mouse.wheel(0, 260)
        page.wait_for_timeout(250)
    page.wait_for_timeout(800)
    shot("report-scrolled")

    page.goto(f"{SITE}/#/map?oblique=0&agent=0")
    page.wait_for_selector("#t-swipe")
    page.wait_for_timeout(5000)
    shot("map-imagery")
    page.locator(".ch-list button").first.click()
    page.wait_for_timeout(3500)
    shot("map-selected")

    page.click("#t-swipe")
    page.wait_for_timeout(3000)
    drag_swipe(page, 0.5, 0.3)
    page.wait_for_timeout(400)
    drag_swipe(page, 0.3, 0.72)
    page.wait_for_timeout(400)
    drag_swipe(page, 0.72, 0.5)
    page.wait_for_timeout(800)
    shot("map-swipe")
    page.click("#t-swipe")
    page.wait_for_timeout(2500)
    shot("map-back")

    page.goto(f"{SITE}/#/map?layer=facilities&oblique=0&agent=0")
    page.reload()
    page.wait_for_timeout(5000)
    shot("facilities")

    page.goto(f"{SITE}/#/map?before=provided-before&after=2026-08-14&oblique=0&agent=0")
    page.wait_for_timeout(9000)
    shot("compare-2026")

    page.goto(f"{SITE}/#/database")
    page.wait_for_selector(".ex-tab")
    page.wait_for_timeout(3000)
    shot("database")
    page.mouse.move(700, 400)
    for _ in range(5):
        page.mouse.wheel(0, 240)
        page.wait_for_timeout(250)
    page.wait_for_timeout(1200)
    shot("database-tables")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", action="store_true")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=SIZE, record_video_dir=None if args.shots else str(tmp), record_video_size=SIZE)
        page = ctx.new_page()
        shot = (lambda name: page.screenshot(path=str(tmp / f"{name}.png"))) if args.shots else (lambda name: None)
        tour(page, shot)
        video = page.video
        ctx.close()
        webm = Path(video.path()) if video else None
        browser.close()
    if args.shots:
        print(tmp)
        return
    vf = (
        "setpts=PTS/1.6,fps=5,scale=780:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=80:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle"
    )
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(webm), "-vf", vf, str(OUT)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
