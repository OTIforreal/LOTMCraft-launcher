import ctypes
import os
import sys
import traceback
from pathlib import Path

from common import data_dir

from api import Api


def app_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)


def web_root() -> Path:
    candidates = [app_root() / "web", Path(__file__).resolve().parent / "web", Path(sys.executable).resolve().parent / "web"]
    for path in candidates:
        if (path / "index.html").is_file():
            return path
    return candidates[0]


def run_embedded(api: Api) -> None:
    import webview

    window = webview.create_window(
        "LOTMCraft",
        str(web_root() / "index.html"),
        js_api=api,
        width=1280,
        height=800,
        min_size=(1000, 680),
        background_color="#090a10",
    )
    api.attach(window)
    webview.start(debug=False, gui="edgechromium")


def run_browser_fallback(api: Api) -> None:
    import webserver

    api.attach(None)
    webserver.run(api, web_root(), data_dir() / "browser-profile")


def main() -> None:
    api = Api()
    try:
        if os.environ.get("LOTMCRAFT_FALLBACK"):
            raise RuntimeError("fallback requested")
        run_embedded(api)
    except Exception:
        try:
            (data_dir() / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        try:
            run_browser_fallback(api)
        except Exception as error:
            if sys.platform == "win32":
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"LOTMCraft could not open its window.\n\n{error}\n\nDetails: {data_dir() / 'launcher-error.log'}",
                    "LOTMCraft",
                    0x10,
                )
            raise


if __name__ == "__main__":
    main()
