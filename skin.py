import json
import shutil
from pathlib import Path

import requests
from PIL import Image

from common import data_dir

LOADER_NAME = "customskinloader.jar"
LOADER_URL = "https://cdn.modrinth.com/data/idMHQ4n2/versions/yyYvYfZI/CustomSkinLoader_Universal-15.1.jar"
CLASSIC = "classic"
SLIM = "slim"


def skin_path() -> Path:
    return data_dir() / "skin.png"


def has_skin() -> bool:
    return skin_path().is_file()


def save_skin(source: Path) -> None:
    try:
        image = Image.open(source)
        image.load()
    except Exception as error:
        raise RuntimeError("That file is not a PNG skin.") from error
    width, height = image.size
    scale = width // 64 if width else 0
    if image.format != "PNG" or scale < 1 or width != scale * 64 or height not in (scale * 32, scale * 64) or width > 1024:
        raise RuntimeError("Skin must be a 64×64 or 64×32 PNG, or a larger version of that size.")
    image.convert("RGBA").save(skin_path(), "PNG")


def clear_skin() -> None:
    skin_path().unlink(missing_ok=True)


def preview_image() -> Image.Image | None:
    path = skin_path()
    if not path.is_file():
        return None
    image = Image.open(path).convert("RGBA")
    scale = max(1, image.width // 64)
    face = image.crop((8 * scale, 8 * scale, 16 * scale, 16 * scale)).resize((84, 84), Image.NEAREST)
    hat = image.crop((40 * scale, 8 * scale, 48 * scale, 16 * scale)).resize((84, 84), Image.NEAREST)
    face.alpha_composite(hat)
    return face


def prepare_offline_skin(game_dir: Path, username: str, model: str) -> None:
    mods = game_dir / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    if not has_skin():
        clear_local_skin(game_dir, username)
        (mods / LOADER_NAME).unlink(missing_ok=True)
        return
    _ensure_loader(mods)
    choice = "slim" if model == SLIM else "default"
    _write_config(game_dir, choice)
    folder = game_dir / "CustomSkinLoader" / "LocalSkin" / "skins"
    clear_local_skin(game_dir, username)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skin_path(), folder / f"{username}.png")


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
