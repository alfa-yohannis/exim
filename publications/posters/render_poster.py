"""Render poster_id.html, poster_en.html, dan poster_vi.html menjadi PDF A1 potret, siap cetak.

    ..\\..\\.venv\\Scripts\\python.exe render_poster.py          # keduanya
    ..\\..\\.venv\\Scripts\\python.exe render_poster.py en       # satu bahasa saja

Memakai Chromium yang sudah terpasang bersama Playwright.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
LANGUAGES = ("id", "en", "vi")

PAGE_WIDTH = "594mm"   # A1 potret
PAGE_HEIGHT = "841mm"


def render(languages: tuple[str, ...]) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1122, "height": 1587})
        for language in languages:
            source = HERE / f"poster_{language}.html"
            output = HERE / f"poster_{language}.pdf"
            page.goto(source.as_uri(), wait_until="networkidle")
            page.pdf(
                path=str(output),
                width=PAGE_WIDTH,
                height=PAGE_HEIGHT,
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                prefer_css_page_size=True,
            )
            print(f"PDF -> {output}  ({output.stat().st_size / 1024:.0f} KB)")
        browser.close()


if __name__ == "__main__":
    requested = tuple(arg for arg in sys.argv[1:] if arg in LANGUAGES)
    render(requested or LANGUAGES)
