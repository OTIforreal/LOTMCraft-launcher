import json
import os
import shutil
import uuid
from pathlib import Path

APP_NAME = "LOTMCraft"
DEFAULT_JVM_ARGS = "-XX:+UseG1GC -XX:+ParallelRefProcEnabled"
DEFAULT_PACK = {
    "name": "LOTMCraft",
    "minecraft": "1.21.1",
    "neoforge": "21.1.262",
    "server": "194.93.2.243:2403",
}
BUILTIN_CONFIG = {
    "drive_folder_id": "https://github.com/OTIforreal/LOTMCraft-launcher/releases/tag/modpack",
    "azure_client_id": "00000000402b5328",
    "redirect_uri": "https://login.live.com/oauth20_desktop.srf",
}
DEFAULT_SETTINGS = {
    "ram": 4,
    "jvm_args": DEFAULT_JVM_ARGS,
    "game_dir": "",
    "width": 1280,
    "height": 720,
    "hide_on_launch": False,
    "active_account": "",
    "optional_mods": [],
    "bg_blur": 0,
    "bg_dim": 35,
    "music_enabled": False,
    "music_volume": 40,
}


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_instance_dir() -> Path:
    return data_dir() / "instance"


def instance_dir() -> Path:
    custom = str(load_settings().get("game_dir") or "").strip()
    path = Path(os.path.expandvars(custom)) if custom else default_instance_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / "mods").mkdir(exist_ok=True)
    return path


def load_config() -> dict:
    config = dict(BUILTIN_CONFIG)
    override = data_dir() / "config.json"
    if override.is_file():
        try:
            stored = json.loads(override.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            config.update({key: value for key, value in stored.items() if isinstance(value, str) and value.strip()})
    return config


def load_settings() -> dict:
    path = data_dir() / "settings.json"
    settings = dict(DEFAULT_SETTINGS)
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            settings.update(stored)
    return settings


def save_settings(settings: dict) -> None:
    path = data_dir() / "settings.json"
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _accounts_path() -> Path:
    return data_dir() / "accounts.json"


def load_accounts() -> list[dict]:
    path = _accounts_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("id")] if isinstance(data, list) else []


def save_accounts(accounts: list[dict]) -> None:
    _accounts_path().write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


def skins_dir() -> Path:
    path = data_dir() / "skins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_account_id() -> str:
    return uuid.uuid4().hex


def migrate_legacy() -> None:
    """Carry over the single Microsoft login, nickname and skin of the old launcher."""
    if _accounts_path().is_file():
        return
    accounts: list[dict] = []
    settings = load_settings()
    active = ""
    legacy_auth = data_dir() / "auth.json"
    if legacy_auth.is_file():
        try:
            stored = json.loads(legacy_auth.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict) and stored.get("refresh_token"):
            account = {
                "id": new_account_id(),
                "type": "microsoft",
                "name": stored.get("name", ""),
                "uuid": stored.get("id", ""),
                "access_token": stored.get("access_token", ""),
                "refresh_token": stored.get("refresh_token", ""),
            }
            accounts.append(account)
            if settings.get("use_microsoft"):
                active = account["id"]
    nickname = str(settings.get("nickname") or "").strip()
    if nickname:
        account = {
            "id": new_account_id(),
            "type": "offline",
            "name": nickname,
            "skin_model": "slim" if settings.get("skin_model") == "slim" else "classic",
        }
        legacy_skin = data_dir() / "skin.png"
        if legacy_skin.is_file():
            shutil.copyfile(legacy_skin, skins_dir() / f"{account['id']}.png")
        accounts.append(account)
        if not active:
            active = account["id"]
    if accounts and not active:
        active = accounts[0]["id"]
    settings["active_account"] = active
    for key in ("nickname", "use_microsoft", "skin_model"):
        settings.pop(key, None)
    save_settings(settings)
    save_accounts(accounts)
