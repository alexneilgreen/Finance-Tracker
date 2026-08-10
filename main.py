"""
main.py
-------
Entry point for the Personal Finance & Investment Tracker.

1. Starts the Flask API/server on 127.0.0.1 only (no network exposure).
2. Opens a chromeless native window (PyWebView) pointed at that server.
3. On window close, copies the SQLite file to data/backups/ with a timestamp.

Run with:  python main.py
Package with:  pyinstaller --noconfirm --onefile --add-data "frontend:frontend" main.py
"""

import base64
import shutil
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import webview

from backend import db_manager as db
from backend.api import app as flask_app

HOST = "127.0.0.1"
BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "data" / "backups"


def find_free_port():
    """Grabs an available localhost port so we never collide with anything else."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def start_server(port):
    # threaded=True lets the SPA fire several fetch() calls concurrently.
    # use_reloader must stay False - it's not compatible with running in a thread.
    flask_app.run(host=HOST, port=port, threaded=True, use_reloader=False)


def backup_database():
    """Called when the window closes. Keeps the last 20 backups."""
    if not db.DB_PATH.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"finance_{stamp}.db"
    shutil.copy2(db.DB_PATH, dest)

    # Prune old backups beyond the most recent 20
    backups = sorted(BACKUP_DIR.glob("finance_*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-20]:
        old.unlink(missing_ok=True)


class JSApi:
    """
    Exposed to the frontend as window.pywebview.api.* — needed because a
    plain <a download> / blob-URL click has no browser download manager to
    catch it inside a chromeless native window, so files silently go
    nowhere. This instead opens a real native "Save As" dialog and writes
    the bytes directly, which is the reliable way to save a generated file
    (like the Annual Report PDF) from a PyWebView app.
    """

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def save_file(self, base64_data, filename):
        try:
            dialog_type = getattr(webview, "FileDialog", None)
            dialog_type = dialog_type.SAVE if dialog_type else webview.SAVE_DIALOG
            result = self._window.create_file_dialog(
                dialog_type, save_filename=filename
            )
            if not result:
                return {"saved": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            with open(path, "wb") as f:
                f.write(base64.b64decode(base64_data))
            return {"saved": True, "path": str(path)}
        except Exception as e:
            return {"saved": False, "error": str(e)}


def main():
    db.init_db()

    # Belt-and-suspenders: PyWebView blocks native browser-style downloads by
    # default (this is almost certainly why the old blob-URL download went
    # nowhere). The primary save path is the native dialog below, but this
    # keeps the plain-browser fallback in report.js viable too.
    webview.settings["ALLOW_DOWNLOADS"] = True

    port = find_free_port()
    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()
    time.sleep(0.6)  # give Flask a moment to bind before the window loads it

    js_api = JSApi()
    window = webview.create_window(
        "Personal Finance & Investment Tracker",
        url=f"http://{HOST}:{port}/",
        width=1440,
        height=900,
        min_size=(1100, 700),
        js_api=js_api,
    )
    js_api.set_window(window)
    window.events.closed += backup_database

    webview.start()


if __name__ == "__main__":
    main()