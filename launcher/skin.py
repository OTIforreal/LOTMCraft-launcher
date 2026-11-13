import base64
import io
import json
import shutil
import urllib.parse
from pathlib import Path

import requests
from PIL import Image

from common import data_dir, skins_dir

LOADER_NAME = "customskinloader.jar"
LOADER_URL = "https://cdn.modrinth.com/data/idMHQ4n2/versions/yyYvYfZI/CustomSkinLoader_Universal-15.1.jar"
CLASSIC = "classic"
SLIM = "slim"


MAX_SKIN_BYTES = 2 * 1024 * 1024


def skin_path(account_id: str) -> Path:
    return skins_dir() / f"{account_id}.png"


def has_skin(account_id: str) -> bool:
    return skin_path(account_id).is_file()


def _validated_png(raw: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as error:
        raise RuntimeError("That is not a PNG skin.") from error
    width, height = image.size
    scale = width // 64 if width else 0
    if image.format != "PNG" or scale < 1 or width != scale * 64 or height not in (scale * 32, scale * 64) or width > 1024:
        raise RuntimeError("Skin must be a 64×64 or 64×32 PNG, or a larger version of that size.")
    return image.convert("RGBA")


def save_skin_bytes(account_id: str, raw: bytes) -> None:
    _validated_png(raw).save(skin_path(account_id), "PNG")


def save_skin(account_id: str, source: Path) -> None:
    try:
        if source.stat().st_size > MAX_SKIN_BYTES:
            raise RuntimeError("That file is too large for a skin.")
        raw = source.read_bytes()
    except OSError as error:
        raise RuntimeError("Could not read that file.") from error
    save_skin_bytes(account_id, raw)


def download_skin(account_id: str, url: str) -> None:
    url = (url or "").strip()
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise RuntimeError("Enter a link that starts with http:// or https://")
    try:
        with requests.get(url, timeout=20, stream=True, headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw += chunk
                if len(raw) > MAX_SKIN_BYTES:
                    raise RuntimeError("That link is too large to be a skin.")
    except requests.RequestException as error:
        raise RuntimeError("Could not download the skin from that link.") from error
    save_skin_bytes(account_id, bytes(raw))


def clear_skin(account_id: str) -> None:
    skin_path(account_id).unlink(missing_ok=True)


def skin_data_url(account_id: str) -> str | None:
    path = skin_path(account_id)
    if not path.is_file():
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def prepare_offline_skin(game_dir: Path, account_id: str, username: str, model: str) -> None:
    mods = game_dir / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    if not has_skin(account_id):
        clear_local_skin(game_dir, username)
        (mods / LOADER_NAME).unlink(missing_ok=True)
        return
    _ensure_loader(mods)
    choice = "slim" if model == SLIM else "default"
    _write_config(game_dir, choice)
    folder = game_dir / "CustomSkinLoader" / "LocalSkin" / "skins"
    clear_local_skin(game_dir, username)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skin_path(account_id), folder / f"{username}.png")


def clear_local_skin(game_dir: Path, username: str) -> None:
    folder = game_dir / "CustomSkinLoader" / "LocalSkin" / "skins"
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        if path.suffix.lower() in {".png", ".json"}:
            path.unlink(missing_ok=True)


def _jar_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 10000 and path.read_bytes()[:2] == b"PK"
    except OSError:
        return False


def _ensure_loader(mods: Path) -> None:
    if any(path.name.lower().startswith("customskinloader") and path.name != LOADER_NAME for path in mods.glob("*.jar")):
        return
    cached = data_dir() / "skinloader" / LOADER_NAME
    cached.parent.mkdir(parents=True, exist_ok=True)
    if not _jar_ok(cached):
        cached.unlink(missing_ok=True)
        temporary = cached.with_suffix(".part")
        try:
            response = requests.get(LOADER_URL, timeout=60)
            response.raise_for_status()
            temporary.write_bytes(response.content)
            if not _jar_ok(temporary):
                raise RuntimeError("Skin loader download was not a jar.")
            temporary.replace(cached)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Could not download the skin loader.") from error
    shutil.copyfile(cached, mods / LOADER_NAME)


def _write_config(game_dir: Path, model: str) -> None:
    path = game_dir / "CustomSkinLoader" / "CustomSkinLoader.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": "15.1",
        "loadlist": [
            {
                "name": "LocalSkin",
                "type": "Legacy",
                "checkPNG": False,
                "skin": "LocalSkin/skins/{USERNAME}.png",
                "model": model,
                "cape": "LocalSkin/capes/{USERNAME}.png",
                "elytra": "LocalSkin/elytras/{USERNAME}.png",
            },
            {"name": "Mojang", "type": "MojangAPI"},
        ],
        "enableTransparentSkin": True,
        "forceLoadAllTextures": True,
        "enableCape": True,
    }, indent=2), encoding="utf-8")
