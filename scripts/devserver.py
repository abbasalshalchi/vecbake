"""Static server for the player, plus a snapshot endpoint.

`POST /save/<name>` writes the request body to `snapshots/<name>`, which lets
the page dump `canvas.toBlob()` straight to disk. That turns "does the bake
look right?" into a file you can diff against a Blender render, instead of
something you have to eyeball in a browser.

    python scripts/devserver.py [port]
"""

import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS = os.path.join(ROOT, "snapshots")


class Handler(SimpleHTTPRequestHandler):
    # An .ivg *is* an SVG, so serve it as one. Without this a static host falls
    # back to application/octet-stream, and the file downloads instead of
    # rendering when you open it or point an <img> at it.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".ivg": "image/svg+xml",
        ".svg": "image/svg+xml",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_POST(self):
        if not self.path.startswith("/save/"):
            self.send_error(404)
            return
        name = os.path.basename(self.path[len("/save/"):]) or "snapshot.png"
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        os.makedirs(SNAPSHOTS, exist_ok=True)
        path = os.path.join(SNAPSHOTS, name)
        with open(path, "wb") as fh:
            fh.write(data)
        body = path.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"serving {ROOT} on http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
