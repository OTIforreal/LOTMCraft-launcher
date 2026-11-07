import json
import os
from pathlib import Path

APP_NAME = "LOTMCraft"
DEFAULT_PACK = {
    "name": "LOTMCraft",
    "minecraft": "1.21.1",
    "neoforge": "21.1.262",
    "server": "",
}
BUILTIN_CONFIG = {
    "drive_folder_id": "https://drive.google.com/drive/folders/16JrZ9VwwKde4ul1gFskxG_jm_djhID6Q",
    "azure_client_id": "00000000402b5328",
    "redirect_uri": "https://login.live.com/oauth20_desktop.srf",
}


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def instance_dir() -> Path:
    path = data_dir() / "instance"
    path.mkdir(parents=True, exist_ok=True)
    (path / "mods").mkdir(exist_ok=True)
    return path


def load_config() -> dict:
    return dict(BUILTIN_CONFIG)


def load_settings() -> dict:
    path = data_dir() / "settings.json"
    settings = {"nickname": "", "ram": 4, "use_microsoft": False}
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            settings.update(stored)
    return settings


def save_settings(settings: dict) -> None:
    path = data_dir() / "settings.json"
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_auth() -> dict | None:
    path = data_dir() / "auth.json"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not data.get("refresh_token"):
        return None
    return data


def save_auth(account: dict) -> None:
    path = data_dir() / "auth.json"
    stored = {
        "id": account.get("id", ""),
        "name": account.get("name", ""),
        "access_token": account.get("access_token", ""),
        "refresh_token": account.get("refresh_token", ""),
    }
    path.write_text(json.dumps(stored), encoding="utf-8")


def clear_auth() -> None:
    path = data_dir() / "auth.json"
    if path.is_file():
        path.unlink()
