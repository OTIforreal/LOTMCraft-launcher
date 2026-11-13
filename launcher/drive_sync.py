import html
import json
import re
import shutil
import zipfile
from pathlib import Path

import requests

from common import DEFAULT_PACK, instance_dir

RELEASE_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/releases/(?:tag|download)/([^/?#]+)", re.IGNORECASE)
RELEASE_KEY = re.compile(r"^([\w.-]+)/([\w.-]+)/([^/?#\s]+)$")
RELEASE_API = "https://api.github.com/repos/{}/{}/releases/tags/{}"
MOD_PART = re.compile(r"^mods(?:-(\d+))?\.zip$", re.IGNORECASE)
MOD_VOLUME = re.compile(r"^mods\.z(\d+)$", re.IGNORECASE)
SPLIT_MARKER = b"PK\x07\x08"


def server_address(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith("адрес"):
        return ""
    return value


def folder_id_from(value: str) -> str:
    """Turn a GitHub release link into the stable key owner/repo/tag."""
    value = (value or "").strip()
    match = RELEASE_URL.search(value)
    if match:
        return "/".join(match.groups())
    if RELEASE_KEY.fullmatch(value):
        return value
    return ""


class Unreachable(RuntimeError):
    pass


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
        mod_archives, mods_changed = self._fetch_mod_parts(files, status, progress)
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
            mod_archives,
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

    def _fetch_mod_parts(self, files: list[dict], status, progress) -> tuple[list[Path], bool]:
        volumes = []
        separate = []
        for item in files:
            volume = MOD_VOLUME.fullmatch(item["name"])
            if volume:
                volumes.append((int(volume.group(1)), item))
                continue
            part = MOD_PART.fullmatch(item["name"])
            if part and not (volumes or any(MOD_VOLUME.fullmatch(other["name"]) for other in files)):
                separate.append((int(part.group(1) or 1), item))
        has_zip = any(item["name"].lower() == "mods.zip" for item in files)
        if volumes:
            if not has_zip:
                raise RuntimeError("mods.zip was not found in the modpack release.")
            separate = [(order, item) for order, item in separate if item["name"].lower() != "mods.zip"]
        elif not has_zip and not separate:
            raise RuntimeError("mods.zip was not found in the modpack release.")
        changed = False
        paths = []
        if volumes:
            pieces = [item for _, item in sorted(volumes)]
            pieces.append(next(item for item in files if item["name"].lower() == "mods.zip"))
            piece_paths = []
            for item in pieces:
                changed = self._fetch_zip(files, item["name"], status, progress, required=True) or changed
                piece_paths.append(instance_dir() / item["name"])
            joined = instance_dir() / "mods.joined.zip"
            if changed or not joined.is_file():
                status("Joining mod archive")
                _join_split_zip(piece_paths, joined)
                changed = True
            paths.append(joined)
        listed = {path.name.casefold() for path in paths}
        for _, item in sorted(separate):
            if volumes and item["name"].lower() == "mods.zip":
                continue
            changed = self._fetch_zip(files, item["name"], status, progress, required=True) or changed
            path = instance_dir() / item["name"]
            paths.append(path)
            listed.add(path.name.casefold())
        if not volumes:
            joined = instance_dir() / "mods.joined.zip"
            if joined.is_file():
                joined.unlink()
                changed = True
        for local in instance_dir().iterdir():
            name = local.name
            if not local.is_file():
                continue
            if MOD_VOLUME.fullmatch(name) and name.casefold() not in {item["name"].casefold() for _, item in volumes}:
                local.unlink()
                changed = True
            elif not volumes and MOD_PART.fullmatch(name) and name.casefold() not in listed:
                local.unlink()
                changed = True
        if not paths:
            raise RuntimeError("mods.zip was not found in the modpack release.")
        return paths, changed

    def _fetch_zip(self, files: list[dict], filename: str, status, progress, required: bool) -> bool:
        match = next((item for item in files if item["name"].lower() == filename.lower()), None)
        if match is None:
            if required:
                raise RuntimeError(f"{filename} was not found in the modpack release.")
            return False
        status(f"Checking {filename}")
        progress(0, 1)
        return self._download(
            match["id"],
            instance_dir() / filename,
            lambda done, size: progress(done, size or 1),
            int(match.get("size") or 0),
            match.get("hash") or "",
        )

    def _require_folder(self) -> None:
        if not self.folder_id:
            raise RuntimeError("The modpack link is missing.")

    def _list_files(self) -> list[dict]:
        owner, repo, tag = self.folder_id.split("/", 2)
        try:
            response = self.session.get(
                RELEASE_API.format(owner, repo, tag),
                headers={"Accept": "application/vnd.github+json"},
                timeout=30,
            )
        except requests.RequestException as error:
            raise Unreachable("Cannot reach GitHub. Check the internet connection.") from error
        if response.status_code == 404:
            raise RuntimeError("The modpack release was not found on GitHub.")
        if response.status_code in (403, 429):
            raise RuntimeError("GitHub is limiting requests from this network. Try again in a few minutes.")
        response.raise_for_status()
        files = []
        for asset in response.json().get("assets") or []:
            name = str(asset.get("name") or "").strip()
            url = str(asset.get("browser_download_url") or "")
            if not name or not url:
                continue
            files.append({
                "id": url,
                "name": name,
                "size": int(asset.get("size") or 0),
                "hash": str(asset.get("digest") or asset.get("updated_at") or ""),
            })
        if not files:
            raise RuntimeError("The modpack release has no files.")
        return files

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
        self._download(
            match["id"],
            destination,
            lambda done, total: None,
            int(match.get("size") or 0),
            match.get("hash") or "",
        )
        try:
            return _read_pack(destination)
        except ValueError as error:
            raise RuntimeError("pack.json on the modpack release is invalid.") from error

    def _download(self, file_id: str, destination: Path, progress, size: int = 0, digest: str = "") -> bool:
        if _same_file(destination, size, digest):
            return False
        temporary = destination.with_suffix(destination.suffix + ".part")
        response = self._open(file_id)
        try:
            remote_size = int(response.headers.get("Content-Length") or size or 0)
            done = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    progress(done, remote_size)
            if remote_size and temporary.stat().st_size != remote_size:
                raise RuntimeError(f"{destination.name} download stopped early.")
            temporary.replace(destination)
            _write_stamp(destination, digest)
            return True
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            response.close()

    def _open(self, url: str) -> requests.Response:
        try:
            response = self.session.get(url, stream=True, timeout=(15, 180))
        except requests.RequestException as error:
            raise Unreachable("Cannot reach GitHub. Check the internet connection.") from error
        response.raise_for_status()
        if "text/html" in response.headers.get("Content-Type", ""):
            response.close()
            raise RuntimeError("GitHub returned a page instead of the file.")
        return response


def _stamp_path(path: Path) -> Path:
    return path.with_name(path.name + ".hash")


def _same_file(path: Path, size: int, digest: str) -> bool:
    if not path.is_file() or not size or path.stat().st_size != size:
        return False
    stamp = _stamp_path(path)
    return bool(digest) and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest


def _write_stamp(path: Path, digest: str) -> None:
    if digest:
        _stamp_path(path).write_text(digest, encoding="utf-8")


def _join_split_zip(parts: list[Path], destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with temporary.open("wb") as output:
            for part in parts:
                with part.open("rb") as source:
                    marker = source.read(len(SPLIT_MARKER))
                    if marker != SPLIT_MARKER:
                        output.write(marker)
                    shutil.copyfileobj(source, output)
        with zipfile.ZipFile(temporary) as archive:
            archive.namelist()
        temporary.replace(destination)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Could not join the mod archive. The parts are incomplete or damaged.") from error


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


def _install_mods(required: list[Path], optional: Path | None, chosen: list[str]) -> tuple[int, int]:
    mods = instance_dir() / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    skip = {name.casefold() for name in _jar_names(optional)} if optional is not None and optional.is_file() else set()
    required_names: set[str] = set()
    for index, archive in enumerate(required):
        required_names |= _write_jars(archive, mods, None, index == 0, skip)
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
