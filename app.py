"""
app.py — Ghost desktop app.

System tray icon + native window wrapping the dashboard.
Run this instead of opening the browser manually.

Usage:
    venv/bin/python3 app.py
"""

import threading
import webbrowser
from PIL import Image
import pystray

DASHBOARD_URL = "http://localhost:8000/dashboard"
APP_TITLE     = "Ghost"


def _make_icon() -> Image.Image:
    """
    Simple icon for the system tray.
    """
    return Image.open("dashboard/static/ghost.png").resize((64, 64)).convert("RGB")


def _open_window():
    """Open the dashboard in a native pywebview window."""
    import webview
    webview.create_window(
        APP_TITLE,
        DASHBOARD_URL,
        width=1200,
        height=800,
        resizable=True,
    )
    webview.start()

def _build_tray(icon: pystray.Icon): # type: ignore
    """Called when the tray icon is ready — nothing to do on startup."""
    pass

_open_event = threading.Event()

def _on_open(icon, item):
    import subprocess
    subprocess.Popen(["pake-ghost"])


def _on_browser(icon, item):
    """Tray menu: Open in Browser — fallback for when webview has issues."""
    webbrowser.open(DASHBOARD_URL)


def _on_quit(icon, item):
    """Tray menu: Quit — stop the tray icon loop."""
    icon.stop()


def main():
    image = _make_icon()

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", _on_open, default=True),
        pystray.MenuItem("Open in Browser", _on_browser),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _on_quit),
    )

    icon = pystray.Icon(APP_TITLE, image, APP_TITLE, menu)

    # pystray runs in background thread, pywebview stays on main thread
    t = threading.Thread(target=icon.run, daemon=True)
    t.start()

    _open_event.wait()
    _open_window()
    # Keep main thread alive
    t.join()


if __name__ == "__main__":
    main()