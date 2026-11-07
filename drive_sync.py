import html
import json
import re
import shutil
import zipfile
from pathlib import Path

import requests

from common import DEFAULT_PACK, instance_dir

FOLDER_ID = re.compile(r"(?:/folders/|^)([a-zA-Z0-9_-]{10,})")
FOLDER_PAGE = "https://drive.google.com/embeddedfolderview"
DOWNLOAD_PAGE = "https://drive.google.com/uc"
ENTRY = re.compile(r'id="entry-([^"]+)"[\s\S]*?<div class="flip-entry-title">([^<]*)</div>')
FORM_ACTION = re.compile(r'<form[^>]+id="download-form"[^>]+action="([^"]+)"', re.IGNORECASE)
FORM_FIELD = re.compile(r'name="([^"]+)"\s+value="([^"]*)"')


def server_address(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith("адрес"):
        return ""
    return value


def folder_id_from(value: str) -> str:
    value = (value or "").strip()
    match = FOLDER_ID.search(value)
    return match.group(1) if match else ""


class DriveSync:
    def __init__(self, folder_id: str):
        self.folder_id = folder_id_from(folder_id)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0"
        self.session.headers["Accept-Encoding"] = "identity"

    def read_pack(self, status) -> dict:
        self._require_folder()
        return self._pack(self._list_files(), status)

    def check_mods(self, status, progress, enabled: list[str]) -> list[str]:
        self._require_folder()
        files = self._list_files()
        mods_changed = self._fetch_zip(files, "mods.zip", status, progress, required=True)
        optional_listed = any(item["name"].lower() == "optional.zip" for item in files)
        optional_path = instance_dir() / "optional.zip"
        if optional_listed:
            optional_changed = self._fetch_zip(files, "optional.zip", status, progress, required=False)
        else:
            optional_changed = optional_path.is_file()
            optional_path.unlink(missing_ok=True)
        names = self.local_optional()
        wanted = {name.casefold() for name in enabled}
        chosen = [name for name in names if name.casefold() in wanted]
        if not mods_changed and not optional_changed and any((instance_dir() / "mods").glob("*.jar")):
            _fix_fabric_versions()
            text = "Mods are up to date"
            if not optional_listed:
                text += ". optional.zip was not found"
            status(text)
            progress(1, 1)
            return names
        status("Extracting mods")
        required_count, _ = _install_mods(
            instance_dir() / "mods.zip",
            instance_dir() / "optional.zip" if optional_listed else None,
            chosen,
        )
        _fix_fabric_versions()
        text = f"Mods updated ({required_count})" if mods_changed or optional_changed else "Mods are up to date"
        if not optional_listed:
            text += ". optional.zip was not found"
        status(text)
        progress(1, 1)
        return names

    def local_optional(self) -> list[str]:
        return _jar_names(instance_dir() / "optional.zip")

    def set_optional(self, name: str, enabled: bool) -> None:
        safe = Path(name).name
        target = instance_dir() / "mods" / safe
        if not enabled:
            if target.is_file():
                target.unlink()
            return
        archive = instance_dir() / "optional.zip"
        if not archive.is_file():
            raise RuntimeError("optional.zip is not downloaded yet. Check mod updates.")
        _extract_named(archive, safe, target)

    def _fetch_zip(self, files: list[dict], filename: str, status, progress, required: bool) -> bool:
        match = next((item for item in files if item["name"].lower() == filename), None)
        if match is None:
            if required:
                raise RuntimeError(f"{filename} was not found in the Google Drive folder.")
            return False
        status(f"Checking {filename}")
        progress(0, 1)
        return self._download(match["id"], instance_dir() / filename, lambda done, size: progress(done, size or 1))

    def _require_folder(self) -> None:
        if not self.folder_id:
            raise RuntimeError("The mod folder link is missing.")

    def _list_files(self) -> list[dict]:
        response = self.session.get(FOLDER_PAGE, params={"id": self.folder_id}, timeout=60)
        response.raise_for_status()
        entries = ENTRY.findall(response.text)
        if not entries:
            raise RuntimeError("Google Drive folder is not shared by link, or it is empty.")
        return [{"id": file_id, "name": html.unescape(name).strip()} for file_id, name in entries]

    def _pack(self, files: list[dict], status) -> dict:
        pack = dict(DEFAULT_PACK)
        match = next((item for item in files if item["name"] == "pack.json"), None)
        local = instance_dir() / "pack.json"
        if match is None:
            if local.is_file():
                return _read_pack(local)
            status("pack.json was not found, using the default versions")
            return pack
        destination = local
        self._download(match["id"], destination, lambda done, size: None)
        try:
            return _read_pack(destination)
        except ValueError as error:
            raise RuntimeError("pack.json on Google Drive is invalid.") from error

    def _download(self, file_id: str, destination: Path, progress) -> bool:
        temporary = destination.with_suffix(destination.suffix + ".part")
        response = self._open(file_id)
        try:
            size = int(response.headers.get("Content-Length") or 0)
            if destination.is_file() and size and destination.stat().st_size == size:
                return False
            done = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    progress(done, size)
            temporary.replace(destination)
            return True
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            response.close()

    def _open(self, file_id: str) -> requests.Response:
        response = self.session.get(
            DOWNLOAD_PAGE,
            params={"export": "download", "id": file_id, "confirm": "t"},
            stream=True,
            timeout=180,
        )
        response.raise_for_status()
        if "text/html" not in response.headers.get("Content-Type", ""):
            return response
        page = response.text
        response.close()
        action = FORM_ACTION.search(page)
        if action is None:
            raise RuntimeError("Google Drive returned a page instead of the file. Check that the folder is shared by link.")
        follow = self.session.get(
            html.unescape(action.group(1)),
            params=dict(FORM_FIELD.findall(page)),
            stream=True,
            timeout=180,
        )
        follow.raise_for_status()
        if "text/html" in follow.headers.get("Content-Type", ""):
            follow.close()
            raise RuntimeError("Google Drive returned a page instead of the file. Check that the folder is shared by link.")
        return follow


def _open_zip(archive: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(archive)
    except zipfile.BadZipFile as error:
        raise RuntimeError(f"{archive.name} is damaged. Try the update again.") from error


def _jar_names(archive: Path) -> list[str]:
    if not archive.is_file():
        return []
    names = []
    seen = set()
    with _open_zip(archive) as bundle:
        for info in bundle.infolist():
            name = _jar_name(info)
            if name is None:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    names.sort(key=str.casefold)
    return names


def _jar_name(info: zipfile.ZipInfo) -> str | None:
    if info.is_dir():
        return None
    name = Path(info.filename).name
    if not name.lower().endswith(".jar") or name.startswith("."):
        return None
    return name


def _extract_named(archive: Path, name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open_zip(archive) as bundle:
        found = next((info for info in reversed(bundle.infolist()) if _jar_name(info) == name), None)
        if found is None:
            raise RuntimeError(f"{name} was not found in {archive.name}.")
        with bundle.open(found) as source, target.open("wb") as handle:
            shutil.copyfileobj(source, handle)
        _fix_fabric_versions()


def _fabric_version(filename: str) -> str:
    match = re.search(r"\d+\.\d+(?:\.\d+)?", filename)
    return match.group(0) if match else "1.0.0"


def _fix_fabric_versions() -> None:
    mods = instance_dir() / "mods"
    if not mods.is_dir():
        return
    cache = instance_dir() / ".cache" / "connector"
    for jar in mods.glob("*.jar"):
        if not _rewrite_placeholder_version(jar):
            continue
        if not cache.is_dir():
            continue
        for cached in cache.glob(f"{jar.stem}*"):
            cached.unlink(missing_ok=True)


def _rewrite_placeholder_version(jar: Path) -> bool:
    temporary = jar.with_suffix(".jar.tmp")
    try:
        with zipfile.ZipFile(jar) as archive:
            try:
                raw = archive.read("fabric.mod.json")
            except KeyError:
                return False
            meta = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(meta, dict) or meta.get("version") != "${version}":
                return False
            meta["version"] = _fabric_version(jar.name)
            replacement = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
            with zipfile.ZipFile(temporary, "w") as output:
                for info in archive.infolist():
                    data = replacement if info.filename == "fabric.mod.json" else archive.read(info)
                    entry = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = info.external_attr
                    output.writestr(entry, data)
        temporary.replace(jar)
        return True
    except Exception:
        temporary.unlink(missing_ok=True)
        return False


def _install_mods(required: Path, optional: Path | None, chosen: list[str]) -> tuple[int, int]:
    mods = instance_dir() / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    skip = {name.casefold() for name in _jar_names(optional)} if optional is not None and optional.is_file() else set()
    required_names = _write_jars(required, mods, None, True, skip)
    if not required_names:
        raise RuntimeError("mods.zip does not contain any jar files.")
    chosen_names = {name.casefold() for name in chosen}
    optional_names = set()
    if optional is not None and optional.is_file():
        for name in _jar_names(optional):
            if name.casefold() not in chosen_names:
                continue
            _extract_named(optional, name, mods / name)
            optional_names.add(name.casefold())
    for local in mods.glob("*.jar"):
        folded = local.name.casefold()
        if folded not in required_names and folded not in optional_names:
            local.unlink()
    return len(required_names), len(optional_names)


def _write_jars(archive: Path, mods: Path, only: set[str] | None, pack: bool, skip: set[str] | None = None) -> set[str]:
    names = set()
    with _open_zip(archive) as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            filename = Path(info.filename).name
            if pack and filename == "pack.json":
                _read_pack_bytes(bundle.read(info), instance_dir() / "pack.json")
                continue
            name = _jar_name(info)
            if name is None or (only is not None and name.casefold() not in only):
                continue
            if skip and name.casefold() in skip:
                continue
            with bundle.open(info) as source, (mods / name).open("wb") as handle:
                shutil.copyfileobj(source, handle)
            names.add(name.casefold())
    return names


def _read_pack(path: Path) -> dict:
    return _apply_pack(json.loads(path.read_text(encoding="utf-8")))


def _read_pack_bytes(payload: bytes, path: Path) -> None:
    path.write_bytes(payload)


def _apply_pack(stored: dict) -> dict:
    pack = dict(DEFAULT_PACK)
    if isinstance(stored, dict):
        for key in ("name", "minecraft", "neoforge", "server"):
            if stored.get(key):
                pack[key] = str(stored[key])
    pack["server"] = server_address(pack["server"])
    return pack
