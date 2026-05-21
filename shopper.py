"""
Generates shoppable search URLs and Google Lens visual search.
"""

import io
import urllib.parse
import webbrowser

STORES = [
    ("Google Shopping", "https://www.google.com/search?q={q}&tbm=shop"),
    ("Amazon",          "https://www.amazon.com/s?k={q}"),
    ("ASOS",            "https://www.asos.com/search/?q={q}"),
    ("Shopstyle",       "https://www.shopstyle.com/browse?fts={q}"),
]


def get_links(search_query: str) -> list[tuple[str, str]]:
    """Return list of (store_name, url) for the given search query."""
    q = urllib.parse.quote_plus(search_query)
    return [(name, url.format(q=q)) for name, url in STORES]


def open_google_lens(pil_img):
    """Copy image to clipboard and open Google Lens — user pastes with ⌘V.
    Google rejects cross-session uploads, so clipboard is the reliable path.
    Runs synchronously — call from a background thread."""
    import subprocess
    import tempfile
    import os

    # Save to a temp PNG, then load into clipboard via osascript
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        pil_img.save(tmp, format="PNG")
        subprocess.run(
            ["osascript", "-e",
             f'set the clipboard to (read (POSIX file "{tmp}") as «class PNGf»)'],
            check=True,
        )
    finally:
        os.unlink(tmp)

    webbrowser.open("https://lens.google.com/")
