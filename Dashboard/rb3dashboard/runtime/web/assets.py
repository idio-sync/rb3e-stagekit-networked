"""Serve the dashboard page from an explicit allowlist of files.

Routes map to filenames by an exact lookup rather than by joining a request
path onto a directory. Traversal is therefore impossible by construction, not
by filtering: a request can only ever name one of the files listed here.

Assets are read from disk on each request. The page is a few kilobytes, this
server handles a household rather than a crowd, and reading fresh means an
edited file appears on reload without a restart.
"""

import os


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Request path -> (filename on disk, content type)
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def lookup(route):
    """Return (bytes, content type) for a known route, or None."""
    entry = ASSETS.get(route)
    if entry is None:
        return None
    filename, content_type = entry
    path = os.path.join(STATIC_DIR, filename)
    try:
        with open(path, "rb") as handle:
            return handle.read(), content_type
    except OSError:
        return None
