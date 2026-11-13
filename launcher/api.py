import base64
import json
import os
import re
import shutil
import socket
import struct
import threading
import time
import webbrowser
from pathlib import Path

import requests

try:
    import webview
except Exception:  # the interface also works without pywebview (browser fallback)
    webview = None

from common import (
    DEFAULT_JVM_ARGS,
    DEFAULT_PACK,
    DEFAULT_SETTINGS,
    data_dir,
    default_instance_dir,
    instance_dir,
    load_accounts,
    load_config,
    load_settings,
    migrate_legacy,
    new_account_id,
    save_accounts,
    save_settings,
    skins_dir,
)
from drive_sync import DriveSync, Unreachable, _read_pack
from game import Game, total_memory_gb, valid_nickname
from skin import clear_skin, download_skin, save_skin, skin_data_url, skin_path

BOOSTY_URL = "https://boosty.to/talesofheresy"
PATREON_URL = "https://www.patreon.com/cw/Tales_of_Heresy"
SUPPORT_LINKS = {"boosty": BOOSTY_URL, "patreon": PATREON_URL}
APP_VERSION = "1.1.0"
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_TYPES = {".mp3", ".ogg", ".wav", ".m4a", ".flac"}
MAX_MEDIA_BYTES = 40 * 1024 * 1024


def _fail(error: Exception) -> str:
    if isinstance(error, requests.RequestException):
        return "Network error. Check the internet connection or VPN."
    return str(error) or error.__class__.__name__


def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_varint(sock: socket.socket) -> int:
    result = 0
    for shift in range(0, 35, 7):
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("closed")
        result |= (chunk[0] & 0x7F) << shift
        if not chunk[0] & 0x80:
            return result
    raise ValueError("varint")


def ping_server(address: str, timeout: float = 3.0) -> dict:
    host, _, port_text = address.strip().partition(":")
    port = int(port_text) if port_text.isdigit() else 25565
    started = time.time()
    with socket.create_connection((host, port), timeout) as sock:
        sock.settimeout(timeout)
        raw_host = host.encode("utf-8")
        body = b"\x00" + _varint(767) + _varint(len(raw_host)) + raw_host + struct.pack(">H", port) + _varint(1)
        sock.sendall(_varint(len(body)) + body + b"\x01\x00")
        _read_varint(sock)
        _read_varint(sock)
        length = _read_varint(sock)
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                break
            payload += chunk
    info = json.loads(payload.decode("utf-8", "replace"))
    players = info.get("players") or {}
    return {
        "online": True,
        "players": int(players.get("online") or 0),
        "max": int(players.get("max") or 0),
        "latency": int((time.time() - started) * 1000),
    }


class Api:
    """Everything the web interface can call. Public methods return plain JSON data."""

    def __init__(self) -> None:
        migrate_legacy()
        self._window = None
        self._sink = None
        self._config = load_config()
        self._game = Game(self._config.get("azure_client_id") or "", self._config.get("redirect_uri") or "")
        self._busy = False
        self._progress_max = 1
        self._logs: list[dict] = []
        self._login: dict | None = None
        self._login_window = None
        self._game_running = threading.Event()
        self._hidden = False
        self._lock = threading.Lock()
        self.log("Launcher started")

    # ---- plumbing -------------------------------------------------------------------------

    def attach(self, window) -> None:
        self._window = window

    def set_sink(self, sink) -> None:
        self._sink = sink

    def emit(self, kind: str, **payload) -> None:
        message = {"type": kind, **payload}
        window = self._window
        if window is not None:
            try:
                window.evaluate_js(f"window.__emit({json.dumps(message)})")
            except Exception:
                pass
        elif self._sink is not None:
            self._sink(message)

    def _dialog(self, kind: str, types=None, directory: str = "") -> str | None:
        """Native file/folder picker: the web window's dialog, or tkinter in browser fallback mode."""
        if self._window is not None and webview is not None:
            try:
                if kind == "folder":
                    result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=directory)
                else:
                    result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=tuple(types or ()))
            except Exception:
                return None
            return result[0] if result else None
        try:
            from tkinter import Tk, filedialog
        except Exception:
            return None
        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if kind == "folder":
                path = filedialog.askdirectory(initialdir=directory or None, parent=root)
            else:
                patterns = []
                for entry in types or ():
                    match = re.match(r"^(.*?)\s*\((.*)\)$", entry)
                    if match:
                        patterns.append((match.group(1), match.group(2).replace(";", " ")))
                path = filedialog.askopenfilename(filetypes=patterns or [("All files", "*.*")], parent=root)
        finally:
            root.destroy()
        return path or None

    def log(self, text: str, level: str = "info", source: str = "Launcher") -> None:
        text = str(text).strip()
        if not text:
            return
        for line in text.splitlines():
            entry = {"source": source, "level": level, "text": line.strip(), "time": time.strftime("%H:%M:%S")}
            self._logs.append(entry)
            if len(self._logs) > 600:
                del self._logs[:100]
            self.emit("log", entry=entry)

    def status(self, text: str, level: str = "info") -> None:
        self.log(text, level)
        self.emit("status", text=str(text).splitlines()[0] if text else "", level=level)

    def progress(self, done, total) -> None:
        value = None if not total else max(0.0, min(1.0, done / total))
        self.emit("progress", value=value)

    def set_busy(self, value: bool) -> None:
        self._busy = value
        self.emit("busy", value=value)

    # ---- state ----------------------------------------------------------------------------

    def _public_account(self, account: dict) -> dict:
        return {
            "id": account["id"],
            "type": account.get("type", "offline"),
            "name": account.get("name", ""),
            "skin_model": account.get("skin_model", "classic"),
            "has_skin": skin_path(account["id"]).is_file(),
        }

    def _pack(self) -> dict:
        local = instance_dir() / "pack.json"
        if local.is_file():
            try:
                return _read_pack(local)
            except (OSError, ValueError):
                pass
        return dict(DEFAULT_PACK)

    def refresh_pack(self) -> dict:
        """Fetch pack.json (server address, versions) without waiting for the first launch."""
        if self._busy:
            return {"ok": False}
        try:
            pack = DriveSync(self._config.get("drive_folder_id", "")).read_pack(lambda text: None)
        except Exception:
            return {"ok": False}
        return {"ok": True, "pack": pack}

    def _mods(self) -> dict:
        try:
            names = DriveSync(self._config.get("drive_folder_id", "")).local_optional()
        except Exception:
            names = []
        settings = load_settings()
        enabled = {str(name).casefold() for name in settings.get("optional_mods") or []}
        present = {path.name.casefold() for path in (instance_dir() / "mods").glob("*.jar")}
        if not enabled:
            enabled = {name.casefold() for name in names if name.casefold() in present}
        return {
            "optional": [{"name": name, "enabled": name.casefold() in enabled} for name in names],
            "installed": len(present),
        }

    def get_state(self) -> dict:
        settings = load_settings()
        accounts = [self._public_account(item) for item in load_accounts()]
        active = settings.get("active_account")
        if not any(item["id"] == active for item in accounts):
            active = accounts[0]["id"] if accounts else ""
        return {
            "settings": {key: settings.get(key, DEFAULT_SETTINGS[key]) for key in DEFAULT_SETTINGS},
            "accounts": accounts,
            "active": active,
            "pack": self._pack(),
            "mods": self._mods(),
            "busy": self._busy,
            "logs": self._logs[-300:],
            "system": {
                "total_ram": total_memory_gb(),
                "default_jvm_args": DEFAULT_JVM_ARGS,
                "default_game_dir": str(default_instance_dir()),
                "game_dir": str(instance_dir()),
                "version": APP_VERSION,
            },
        }

    # ---- settings -------------------------------------------------------------------------

    def save_settings(self, patch: dict) -> dict:
        settings = load_settings()
        try:
            if "ram" in patch:
                settings["ram"] = max(1, min(64, int(patch["ram"])))
            if "jvm_args" in patch:
                settings["jvm_args"] = " ".join(str(patch["jvm_args"]).split())
            if "game_dir" in patch:
                settings["game_dir"] = str(patch["game_dir"] or "").strip()
            for key in ("width", "height"):
                if key in patch:
                    settings[key] = max(320, min(7680, int(patch[key])))
            for key in ("hide_on_launch", "music_enabled"):
                if key in patch:
                    settings[key] = bool(patch[key])
            for key, low, high in (("bg_blur", 0, 24), ("bg_dim", 0, 85), ("music_volume", 0, 100)):
                if key in patch:
                    settings[key] = max(low, min(high, int(patch[key])))
            if "active_account" in patch:
                settings["active_account"] = str(patch["active_account"] or "")
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": f"Invalid value: {_fail(error)}"}
        if self._busy and "game_dir" in patch and settings["game_dir"] != load_settings().get("game_dir"):
            return {"ok": False, "error": "Wait until the current task finishes."}
        save_settings(settings)
        if "game_dir" in patch:
            try:
                instance_dir()
            except OSError as error:
                settings["game_dir"] = ""
                save_settings(settings)
                return {"ok": False, "error": f"Cannot use that folder: {_fail(error)}"}
        return {"ok": True, "state": self.get_state()}

    def choose_folder(self) -> str | None:
        return self._dialog("folder", directory=str(instance_dir()))

    def open_folder(self) -> None:
        try:
            os.startfile(str(instance_dir()))
        except OSError as error:
            self.log(f"Could not open the folder: {_fail(error)}", "warn")

    def open_support(self, name: str) -> None:
        url = SUPPORT_LINKS.get(name)
        if url:
            webbrowser.open(url)

    # ---- appearance / music ---------------------------------------------------------------

    def _media(self, kind: str, allowed: set[str]) -> dict:
        chosen = self._dialog("file", (f"{kind} files ({';'.join('*' + ext for ext in sorted(allowed))})",))
        if not chosen:
            return {"ok": False, "error": ""}
        source = Path(chosen)
        if source.suffix.lower() not in allowed:
            return {"ok": False, "error": "Unsupported file type."}
        if source.stat().st_size > MAX_MEDIA_BYTES:
            return {"ok": False, "error": "That file is too large (40 MB max)."}
        for old in data_dir().glob(f"{kind.lower()}.*"):
            old.unlink(missing_ok=True)
        target = data_dir() / f"{kind.lower()}{source.suffix.lower()}"
        shutil.copyfile(source, target)
        return {"ok": True}

    def _stored_media(self, kind: str) -> Path | None:
        return next(iter(sorted(data_dir().glob(f"{kind}.*"))), None)

    def choose_background(self) -> dict:
        return self._media("Background", IMAGE_TYPES)

    def get_background(self) -> str | None:
        path = self._stored_media("background")
        if path is None:
            return None
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
        return _data_url(path, mime.get(path.suffix.lower(), "image/png"))

    def clear_background(self) -> None:
        for path in data_dir().glob("background.*"):
            path.unlink(missing_ok=True)

    def choose_music(self) -> dict:
        return self._media("Music", AUDIO_TYPES)

    def get_music(self) -> dict | None:
        path = self._stored_media("music")
        if path is None:
            return None
        mime = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac"}
        return {"name": path.name, "url": _data_url(path, mime.get(path.suffix.lower(), "audio/mpeg"))}

    def clear_music(self) -> None:
        for path in data_dir().glob("music.*"):
            path.unlink(missing_ok=True)

    # ---- accounts -------------------------------------------------------------------------

    def get_skin(self, account_id: str) -> str | None:
        return skin_data_url(account_id)

    def pick_skin_file(self) -> str | None:
        return self._dialog("file", ("PNG skin (*.png)",))

    def _apply_skin(self, account_id: str, mode: str, value: str) -> None:
        if mode == "file":
            save_skin(account_id, Path(value))
        elif mode == "url":
            download_skin(account_id, value)
        elif mode == "clear":
            clear_skin(account_id)

    def add_offline(self, name: str, skin_mode: str, skin_value: str, model: str) -> dict:
        name = (name or "").strip()
        if not valid_nickname(name):
            return {"ok": False, "error": "Username: 3–16 characters, letters, numbers, and _."}
        accounts = load_accounts()
        if any(item.get("type") == "offline" and item.get("name", "").casefold() == name.casefold() for item in accounts):
            return {"ok": False, "error": "An offline account with that name already exists."}
        account = {"id": new_account_id(), "type": "offline", "name": name, "skin_model": "slim" if model == "slim" else "classic"}
        try:
            self._apply_skin(account["id"], skin_mode, skin_value)
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}
        accounts.append(account)
        save_accounts(accounts)
        self._activate(account["id"])
        self.log(f"Added offline account {name}")
        return {"ok": True, "state": self.get_state()}

    def update_offline(self, account_id: str, skin_mode: str, skin_value: str, model: str) -> dict:
        accounts = load_accounts()
        account = next((item for item in accounts if item["id"] == account_id and item.get("type") == "offline"), None)
        if account is None:
            return {"ok": False, "error": "Account not found."}
        try:
            self._apply_skin(account_id, skin_mode, skin_value)
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}
        account["skin_model"] = "slim" if model == "slim" else "classic"
        save_accounts(accounts)
        return {"ok": True, "state": self.get_state()}

    def remove_account(self, account_id: str) -> dict:
        accounts = [item for item in load_accounts() if item["id"] != account_id]
        save_accounts(accounts)
        skin_path(account_id).unlink(missing_ok=True)
        settings = load_settings()
        if settings.get("active_account") == account_id:
            settings["active_account"] = accounts[0]["id"] if accounts else ""
            save_settings(settings)
        return {"ok": True, "state": self.get_state()}

    def set_active(self, account_id: str) -> dict:
        self._activate(account_id)
        return {"ok": True, "state": self.get_state()}

    def _activate(self, account_id: str) -> None:
        settings = load_settings()
        settings["active_account"] = account_id
        save_settings(settings)

    # ---- Microsoft sign-in (device code) --------------------------------------------------

    def login_microsoft(self) -> dict:
        if self._busy:
            return {"ok": False, "error": "Wait until the current task finishes."}
        if self._login is not None:
            return {"ok": True}
        try:
            data = self._game.device_start()
        except Exception as error:
            return {"ok": False, "error": _fail(error)}
        session = {"data": data, "cancelled": False}
        self._login = session
        self.emit(
            "login",
            phase="code",
            code=data["user_code"],
            url=data.get("verification_uri") or "https://www.microsoft.com/link",
        )
        threading.Thread(target=self._login_poll, args=(session,), daemon=True).start()
        return {"ok": True}

    def login_open_browser(self) -> None:
        session = self._login
        url = (session["data"].get("verification_uri") if session else None) or "https://www.microsoft.com/link"
        webbrowser.open(url)

    def login_cancel(self) -> None:
        session = self._login
        if session is not None:
            session["cancelled"] = True
            self._login = None
        self.emit("login", phase="closed")

    def _login_poll(self, session: dict) -> None:
        data = session["data"]
        interval = max(3, int(data.get("interval") or 5))
        deadline = time.time() + int(data.get("expires_in") or 900)
        profile = None
        try:
            while time.time() < deadline:
                time.sleep(interval)
                if session["cancelled"]:
                    return
                try:
                    profile = self._game.device_poll(data["device_code"])
                except requests.RequestException:
                    continue
                if profile is not None:
                    break
            if profile is None:
                raise RuntimeError("The code expired. Start the sign-in again.")
        except Exception as error:
            if self._login is session:
                self._login = None
                self.emit("login", phase="error", error=_fail(error))
            self.log(f"Sign-in failed: {_fail(error)}", "warn")
            return
        if self._login is not session:
            return
        self._login = None
        accounts = load_accounts()
        existing = next((item for item in accounts if item.get("type") == "microsoft" and item.get("uuid") == profile["id"]), None)
        account = existing or {"id": new_account_id(), "type": "microsoft"}
        self._store_profile(account, profile)
        if existing is None:
            accounts.append(account)
        save_accounts(accounts)
        self._activate(account["id"])
        self.log(f"Signed in as {account['name']}")
        self.emit("login", phase="done", state=self.get_state())

    def _store_profile(self, account: dict, profile: dict) -> None:
        account["name"] = profile.get("name", account.get("name", ""))
        account["uuid"] = profile.get("id", account.get("uuid", ""))
        account["access_token"] = profile.get("access_token", "")
        account["refresh_token"] = profile.get("refresh_token", account.get("refresh_token", ""))
        if profile.get("auth"):
            account["auth"] = profile["auth"]
        skins = [item for item in profile.get("skins") or [] if isinstance(item, dict) and item.get("url")]
        chosen = next((item for item in skins if item.get("state") == "ACTIVE"), skins[0] if skins else None)
        if chosen:
            account["skin_model"] = "slim" if str(chosen.get("variant", "")).upper() == "SLIM" else "classic"
            try:
                response = requests.get(chosen["url"], timeout=15)
                response.raise_for_status()
                skin_path(account["id"]).write_bytes(response.content)
            except requests.RequestException:
                pass

    # ---- server status --------------------------------------------------------------------

    def server_status(self) -> dict:
        address = self._pack().get("server") or ""
        if not address:
            return {"address": "", "online": None}
        try:
            result = ping_server(address)
        except Exception:
            result = {"online": False}
        return {"address": address, **result}

    # ---- mods -----------------------------------------------------------------------------

    def set_optional(self, name: str, enabled: bool) -> dict:
        if self._busy:
            return {"ok": False, "error": "Wait until the current task finishes."}
        try:
            DriveSync(self._config.get("drive_folder_id", "")).set_optional(name, bool(enabled))
        except Exception as error:
            return {"ok": False, "error": _fail(error)}
        settings = load_settings()
        chosen = {str(item).casefold(): item for item in settings.get("optional_mods") or []}
        if enabled:
            chosen[name.casefold()] = name
        else:
            chosen.pop(name.casefold(), None)
        settings["optional_mods"] = list(chosen.values())
        save_settings(settings)
        self.log(f"{'Enabled' if enabled else 'Disabled'} {name}")
        return {"ok": True, "state": self.get_state()}

    def check_mods(self) -> dict:
        if self._busy:
            return {"ok": False, "error": "Wait until the current task finishes."}
        if not self._config.get("drive_folder_id"):
            return {"ok": False, "error": "The mod folder link is missing."}
        self.set_busy(True)
        threading.Thread(target=self._check_mods_work, daemon=True).start()
        return {"ok": True}

    def _check_mods_work(self) -> None:
        try:
            self.status("Checking mod updates")
            settings = load_settings()
            DriveSync(self._config.get("drive_folder_id", "")).check_mods(
                self.status, self.progress, list(settings.get("optional_mods") or [])
            )
        except Exception as error:
            self.status(_fail(error), "error")
        finally:
            self.set_busy(False)
            self.emit("state", state=self.get_state())

    # ---- play -----------------------------------------------------------------------------

    def play(self, join: bool = True) -> dict:
        if self._busy:
            return {"ok": False, "error": "Wait until the current task finishes."}
        accounts = load_accounts()
        settings = load_settings()
        account = next((item for item in accounts if item["id"] == settings.get("active_account")), None)
        if account is None:
            return {"ok": False, "error": "Add an account first.", "need_account": True}
        self.set_busy(True)
        threading.Thread(target=self._play_work, args=(account, bool(join)), daemon=True).start()
        return {"ok": True}

    def _install_callback(self) -> dict:
        def set_max(value: int) -> None:
            self._progress_max = value or 1

        return {
            "setStatus": lambda text: self.status(text),
            "setProgress": lambda value: self.progress(value, self._progress_max),
            "setMax": set_max,
        }

    def _play_work(self, account: dict, join: bool) -> None:
        launched = False
        try:
            settings = load_settings()
            self.log(f'Preparing instance "default"')
            sync = DriveSync(self._config.get("drive_folder_id", ""))
            try:
                sync.check_mods(self.status, self.progress, list(settings.get("optional_mods") or []))
                pack = sync.read_pack(self.status)
            except (Unreachable, requests.RequestException) as error:
                have_local = (instance_dir() / "pack.json").is_file() and any((instance_dir() / "mods").glob("*.jar"))
                if not have_local:
                    raise RuntimeError(
                        "Cannot reach GitHub, so the mods cannot be downloaded. Check the internet connection."
                    ) from error
                self.status("GitHub is unreachable. Launching with the mods already installed.", "warn")
                pack = self._pack()
            if account.get("type") == "microsoft":
                self.status("Signing in to Microsoft")
                profile = self._game.refresh(account["refresh_token"], account.get("auth") == "device")
                accounts = load_accounts()
                stored = next((item for item in accounts if item["id"] == account["id"]), None)
                if stored is not None:
                    self._store_profile(stored, profile)
                    save_accounts(accounts)
                    account = stored
                self.emit("state", state=self.get_state())
            version_id = self._game.ensure(pack, self._install_callback())
            self.status("Launching game ...")
            self._game_running.set()
            note = self._game.launch(
                version_id,
                pack,
                account,
                ram=int(settings["ram"]),
                jvm_args=str(settings.get("jvm_args") or ""),
                width=int(settings["width"]),
                height=int(settings["height"]),
                join=join,
                on_exit=self._game_exited,
            )
            launched = True
            if note:
                self.log(note, "warn")
            self.status("Game started. The window can take a few minutes.")
            self.progress(1, 1)
            threading.Thread(target=self._tail_game_log, daemon=True).start()
            if settings.get("hide_on_launch") and self._window is not None:
                self._hidden = True
                self._window.hide()
        except Exception as error:
            self.status(_fail(error), "error")
        finally:
            if not launched:
                self._game_running.clear()
            self.set_busy(False)
            self.emit("state", state=self.get_state())

    def _game_exited(self, code, failure) -> None:
        self._game_running.clear()
        if self._hidden and self._window is not None:
            self._hidden = False
            try:
                self._window.show()
            except Exception:
                pass
        if failure:
            self.status(failure, "error")
        else:
            self.status("Game closed")
        self.emit("game", state="closed")

    def _tail_game_log(self) -> None:
        path = instance_dir() / "launcher-game.log"
        position = 0
        while True:
            try:
                size = path.stat().st_size
                if size < position:
                    position = 0
                if size > position:
                    with path.open("rb") as handle:
                        handle.seek(position)
                        chunk = handle.read(size - position)
                        position = size
                    for line in chunk.decode("utf-8", "replace").splitlines()[-80:]:
                        lowered = line.lower()
                        level = "error" if "error" in lowered or "exception" in lowered else "warn" if "warn" in lowered else "info"
                        self.log(line, level, "Game")
            except OSError:
                pass
            if not self._game_running.is_set():
                return
            time.sleep(1.0)

    # ---- window ---------------------------------------------------------------------------

    def clear_logs(self) -> None:
        self._logs.clear()

    def open_log_file(self) -> None:
        path = instance_dir() / "launcher-game.log"
        if path.is_file():
            os.startfile(str(path))
