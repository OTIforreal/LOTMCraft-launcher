import ctypes
import gzip
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import urllib.parse
import uuid
from pathlib import Path

import requests

import minecraft_launcher_lib.command
import minecraft_launcher_lib.install
import minecraft_launcher_lib.microsoft_account
import minecraft_launcher_lib.mod_loader
import minecraft_launcher_lib.runtime
from minecraft_launcher_lib._helper import inherit_json
from minecraft_launcher_lib.exceptions import AccountNotOwnMinecraft, AzureAppNotPermitted, InvalidChecksum, InvalidRefreshToken
from minecraft_launcher_lib.install import install_libraries

_SLOW_HOSTS: set[str] = set()
_MIRRORS = {
    "piston-data.mojang.com": "https://bmclapi2.bangbang93.com",
    "piston-meta.mojang.com": "https://bmclapi2.bangbang93.com",
    "launchermeta.mojang.com": "https://bmclapi2.bangbang93.com",
    "launcher.mojang.com": "https://bmclapi2.bangbang93.com",
    "libraries.minecraft.net": "https://bmclapi2.bangbang93.com/maven",
    "resources.download.minecraft.net": "https://bmclapi2.bangbang93.com/assets",
}


def _download_urls(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    root = _MIRRORS.get(host)
    if root is None:
        return [url]
    mirror = root + parsed.path
    if parsed.query:
        mirror += "?" + parsed.query
    if host in _SLOW_HOSTS:
        return [mirror]
    return [url, mirror]


def _sha1_file(path: str) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_matches(path: str, sha1: str | None) -> bool:
    if not os.path.isfile(path):
        return False
    return sha1 is None or _sha1_file(path) == sha1


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _prefer_ipv4() -> None:
    try:
        connection = sys.modules.get("urllib3.util.connection")
        if connection is None:
            import urllib3.util.connection as connection
        connection.allowed_gai_family = lambda: socket.AF_INET
    except Exception:
        return


def _harden_downloads() -> None:
    helper = sys.modules["minecraft_launcher_lib._helper"]
    original_download = helper.download_file
    original_request = requests.Session.request
    if getattr(original_request, "_lotm", False):
        return
    _prefer_ipv4()

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (20, 120))
        last = None
        for candidate in _download_urls(str(url)):
            for _ in range(3):
                try:
                    response = original_request(self, method, candidate, **kwargs)
                    if response.status_code < 500:
                        return response
                    last = requests.HTTPError(f"{response.status_code} for {candidate}")
                    response.close()
                    break
                except requests.RequestException as error:
                    last = error
            host = urllib.parse.urlparse(candidate).hostname
            if host and isinstance(last, (requests.Timeout, requests.ConnectionError)):
                _SLOW_HOSTS.add(host.lower())
        if last is not None:
            raise last
        raise requests.ConnectionError(str(url))

    request._lotm = True
    requests.Session.request = request

    def download_file(url, path, callback=None, sha1=None, lzma_compressed=False, session=None, minecraft_directory=None, overwrite=False):
        if callback is None:
            callback = {}
        last = None
        for candidate in _download_urls(url):
            try:
                result = original_download(
                    candidate, path, callback, sha1, lzma_compressed, session, minecraft_directory, overwrite
                )
            except (requests.RequestException, InvalidChecksum) as error:
                last = error
                _remove_file(path)
                continue
            if result or (not overwrite and _file_matches(path, sha1)):
                return result
            last = requests.HTTPError(f"Download failed: {candidate}")
            _remove_file(path)
        if last is not None:
            raise RuntimeError(f"Could not download {os.path.basename(path)}. Check the internet connection.") from last
        return False

    helper.download_file = download_file
    for module in list(sys.modules.values()):
        if getattr(module, "download_file", None) is original_download:
            module.download_file = download_file


_harden_downloads()

from common import instance_dir
from skin import clear_local_skin, prepare_offline_skin


def _auth_code(value: str, state: str | None = None) -> str:
    value = value.strip()
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query or parsed.fragment)
    if "error" in query:
        raise RuntimeError("Microsoft denied the sign-in.")
    if "code" in query:
        if state and query.get("state") and query["state"][0] != state:
            raise RuntimeError("The sign-in response did not match this request. Try again.")
        return query["code"][0]
    if value and " " not in value and "http" not in value:
        return value
    raise RuntimeError("That address does not contain a sign-in code.")


LIVE_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
LIVE_TOKEN_URL = "https://login.live.com/oauth20_token.srf"


def _minecraft_profile(token_request: dict, legacy: bool = False) -> dict:
    microsoft = minecraft_launcher_lib.microsoft_account
    try:
        if legacy:
            response = requests.post(
                "https://user.auth.xboxlive.com/user/authenticate",
                json={
                    "Properties": {
                        "AuthMethod": "RPS",
                        "SiteName": "user.auth.xboxlive.com",
                        "RpsTicket": "t=" + token_request["access_token"],
                    },
                    "RelyingParty": "http://auth.xboxlive.com",
                    "TokenType": "JWT",
                },
                headers={"Accept": "application/json"},
                timeout=30,
            )
            xbl_request = response.json()
        else:
            xbl_request = microsoft.authenticate_with_xbl(token_request["access_token"])
        xbl_token = xbl_request["Token"]
        userhash = xbl_request["DisplayClaims"]["xui"][0]["uhs"]
        xsts_request = microsoft.authenticate_with_xsts(xbl_token)
        if "Token" not in xsts_request:
            raise RuntimeError("This Microsoft account cannot use Xbox services (no Xbox profile, or a child account).")
        account_request = microsoft.authenticate_with_minecraft(userhash, xsts_request["Token"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Xbox sign-in failed.") from error
    if "access_token" not in account_request:
        raise RuntimeError("This Microsoft account is not allowed to use Minecraft.")
    profile = microsoft.get_profile(account_request["access_token"])
    if isinstance(profile, dict) and profile.get("error") == "NOT_FOUND":
        raise AccountNotOwnMinecraft()
    profile["access_token"] = account_request["access_token"]
    profile["refresh_token"] = token_request["refresh_token"]
    return profile


def offline_uuid(name: str) -> str:
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def valid_nickname(name: str) -> bool:
    return 3 <= len(name) <= 16 and name.replace("_", "").isalnum()


class Game:
    def __init__(self, client_id: str, redirect_uri: str):
        self.client_id = client_id.strip()
        self.redirect_uri = redirect_uri.strip() or "https://login.live.com/oauth20_desktop.srf"

    @property
    def directory(self) -> Path:
        return instance_dir()

    def device_start(self) -> dict:
        """Ask Microsoft for a short code the user types at microsoft.com/link."""
        if not self.client_id:
            raise RuntimeError("Microsoft sign-in is not set up.")
        response = requests.post(
            "https://login.live.com/oauth20_connect.srf",
            data={"client_id": self.client_id, "scope": LIVE_SCOPE, "response_type": "device_code"},
            timeout=30,
        )
        data = response.json()
        if "user_code" not in data:
            raise RuntimeError(str(data.get("error_description") or data.get("error") or "Microsoft did not return a code."))
        return data

    def device_poll(self, device_code: str) -> dict | None:
        """Return the Minecraft profile once the code is accepted, None while still waiting."""
        response = requests.post(
            LIVE_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            timeout=30,
        )
        data = response.json()
        if "access_token" in data:
            try:
                profile = _minecraft_profile(data, legacy=True)
            except AccountNotOwnMinecraft:
                raise RuntimeError("This Microsoft account does not own Minecraft.")
            profile["auth"] = "device"
            return profile
        error = data.get("error")
        if error in ("authorization_pending", "slow_down"):
            return None
        if error in ("expired_token", "bad_verification_code"):
            raise RuntimeError("The code expired. Start the sign-in again.")
        if error == "authorization_declined":
            raise RuntimeError("The sign-in was declined.")
        raise RuntimeError(str(data.get("error_description") or error or "Sign-in failed."))

    def refresh(self, refresh_token: str, device: bool = False) -> dict:
        if not self.client_id:
            raise RuntimeError("Microsoft sign-in is not set up.")
        if device:
            response = requests.post(
                LIVE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": LIVE_SCOPE,
                },
                timeout=30,
            )
            data = response.json()
            if "access_token" not in data:
                raise RuntimeError("The Microsoft session expired. Sign in again.")
            data.setdefault("refresh_token", refresh_token)
            try:
                profile = _minecraft_profile(data, legacy=True)
            except AccountNotOwnMinecraft:
                raise RuntimeError("This Microsoft account does not own Minecraft.")
            profile["auth"] = "device"
            return profile
        try:
            return minecraft_launcher_lib.microsoft_account.complete_refresh(
                self.client_id, None, None, refresh_token
            )
        except AzureAppNotPermitted:
            raise RuntimeError("This Azure app is not allowed to use the Minecraft API.")
        except AccountNotOwnMinecraft:
            raise RuntimeError("This Microsoft account does not own Minecraft.")
        except InvalidRefreshToken:
            raise RuntimeError("The Microsoft session expired. Sign in again.")

    def ensure(self, pack: dict, callback: dict) -> str:
        minecraft = pack["minecraft"]
        neoforge = pack["neoforge"]
        loader = minecraft_launcher_lib.mod_loader.get_mod_loader("neoforge")
        version_id = loader.get_installed_version(minecraft, neoforge)
        java = self._java(minecraft, version_id)
        if self._installed(minecraft, version_id) and java:
            self._repair_libraries(version_id, callback)
            return version_id

        callback.get("setStatus", lambda text: None)(f"Installing Minecraft {minecraft}")
        minecraft_launcher_lib.install.install_minecraft_version(minecraft, self.directory, callback=callback)
        java = self._java(minecraft, version_id)
        if not java:
            raise RuntimeError("Could not install Java 21.")

        callback.get("setStatus", lambda text: None)(f"Installing NeoForge {neoforge}")
        loader._base.get_minecraft_versions = lambda stable_only=False: [minecraft]
        loader._base.get_loader_versions = lambda minecraft_version, stable_only=False: [neoforge]
        try:
            loader.install(minecraft, self.directory, loader_version=neoforge, callback=callback, java=java)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode("utf-8", "replace").strip()
            tail = detail[-500:] if detail else "the installer failed"
            raise RuntimeError(f"NeoForge did not install. {tail}") from error
        self._repair_libraries(version_id, callback)
        return version_id

    def launch(self, version_id: str, pack: dict, account: dict, *, ram: int, jvm_args: str, width: int, height: int,
               join: bool, on_exit=None) -> str | None:
        directory = self.directory
        if account.get("type") == "microsoft":
            username = account["name"]
            user_id = account["uuid"]
            token = account["access_token"]
            clear_local_skin(directory, username)
        else:
            username = account["name"]
            if not valid_nickname(username):
                raise RuntimeError("Username: 3-16 characters, letters, numbers, and _.")
            user_id = offline_uuid(username)
            token = "0"
            prepare_offline_skin(directory, account["id"], username, account.get("skin_model") or "classic")

        java = self._java(pack["minecraft"], version_id)
        if not java:
            raise RuntimeError("Could not find Java 21.")
        ram, ram_note = _fit_ram(ram)

        extra = _split_args(jvm_args)
        jvm = list(extra)
        if not any(item.startswith("-Xmx") for item in extra):
            jvm.insert(0, f"-Xmx{ram}G")
        options = {
            "username": username,
            "uuid": user_id,
            "token": token,
            "jvmArguments": jvm,
            "launcherName": "LOTMCraft",
            "launcherVersion": "1.0",
            "gameDirectory": str(directory),
        }
        if width > 0 and height > 0:
            options["customResolution"] = True
            options["resolutionWidth"] = str(width)
            options["resolutionHeight"] = str(height)
        address = pack.get("server") or ""
        if address:
            _remember_server(directory, address, pack.get("name") or "LOTMCraft")
            if join:
                quick_play = directory / "quickPlay"
                if quick_play.is_dir():
                    quick_play = directory / "quickPlay.json"
                options["quickPlayPath"] = str(quick_play)
                options["quickPlayMultiplayer"] = address

        options["executablePath"] = _windowless_java(java)
        command = minecraft_launcher_lib.command.get_minecraft_command(version_id, directory, options)
        _start_process(command, directory, on_exit)
        return ram_note

    def _repair_libraries(self, version_id: str, callback: dict) -> None:
        if not _missing_module_jars(self.directory, version_id):
            return
        with _version_json(self.directory, version_id).open(encoding="utf-8") as handle:
            data = json.load(handle)
        if "inheritsFrom" in data:
            data = inherit_json(data, self.directory)
        callback.get("setStatus", lambda text: None)("Downloading game libraries")
        install_libraries(version_id, data["libraries"], str(self.directory), callback)

    def _installed(self, minecraft: str, version_id: str) -> bool:
        return _version_json(self.directory, minecraft).is_file() and _version_json(self.directory, version_id).is_file()

    def _java(self, minecraft: str, version_id: str) -> str | None:
        for version in (version_id, minecraft):
            if not _version_json(self.directory, version).is_file():
                continue
            info = minecraft_launcher_lib.runtime.get_version_runtime_information(version, self.directory)
            if not info:
                continue
            path = minecraft_launcher_lib.runtime.get_executable_path(info["name"], self.directory)
            if path:
                return path
        return None


class _Nbt:
    def __init__(self, kind: int, value):
        self.kind = kind
        self.value = value


def _remember_server(directory: Path, address: str, name: str) -> None:
    address = address.strip()
    if not address:
        return
    path = directory / "servers.dat"
    title = (name or "LOTMCraft").strip() or "LOTMCraft"
    try:
        root = _read_server_root(path)
    except Exception:
        root = {}
    servers = root.get("servers")
    entries = []
    if servers is not None and servers.kind == 9 and servers.value[0] == 10:
        entries = list(servers.value[1])
    found = False
    for entry in entries:
        current = entry.get("ip")
        if current is None or current.kind != 8 or str(current.value).strip().lower() != address.lower():
            continue
        entry.pop("hidden", None)
        label = entry.get("name")
        if label is None or label.kind != 8 or label.value == "Minecraft Server":
            entry["name"] = _Nbt(8, title)
        found = True
    if not found:
        entries.insert(0, {"name": _Nbt(8, title), "ip": _Nbt(8, address)})
    root["servers"] = _Nbt(9, (10, entries))
    path.write_bytes(_write_root(root))


def _read_server_root(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if not raw or raw[0] != 10:
        raise ValueError("servers.dat")
    _, offset = _nbt_string(raw, 1)
    compound, _ = _nbt_compound(raw, offset)
    return compound


def _nbt_string(data: bytes, offset: int) -> tuple[str, int]:
    length = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    return data[offset:offset + length].decode("utf-8", "surrogateescape"), offset + length


def _nbt_compound(data: bytes, offset: int) -> tuple[dict, int]:
    result = {}
    while offset < len(data):
        kind = data[offset]
        offset += 1
        if kind == 0:
            return result, offset
        name, offset = _nbt_string(data, offset)
        value, offset = _nbt_payload(kind, data, offset)
        result[name] = _Nbt(kind, value)
    raise ValueError("servers.dat")


def _nbt_payload(kind: int, data: bytes, offset: int):
    if kind == 1:
        return struct.unpack_from(">b", data, offset)[0], offset + 1
    if kind == 2:
        return struct.unpack_from(">h", data, offset)[0], offset + 2
    if kind == 3:
        return struct.unpack_from(">i", data, offset)[0], offset + 4
    if kind == 4:
        return struct.unpack_from(">q", data, offset)[0], offset + 8
    if kind == 5:
        return struct.unpack_from(">f", data, offset)[0], offset + 4
    if kind == 6:
        return struct.unpack_from(">d", data, offset)[0], offset + 8
    if kind == 7:
        length = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return data[offset:offset + length], offset + length
    if kind == 8:
        return _nbt_string(data, offset)
    if kind == 9:
        element = data[offset]
        count = struct.unpack_from(">i", data, offset + 1)[0]
        offset += 5
        items = []
        for _ in range(count):
            value, offset = _nbt_payload(element, data, offset)
            items.append(value)
        return (element, items), offset
    if kind == 10:
        return _nbt_compound(data, offset)
    if kind == 11:
        count = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return list(struct.unpack_from(f">{count}i", data, offset)), offset + 4 * count
    if kind == 12:
        count = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        return list(struct.unpack_from(f">{count}q", data, offset)), offset + 8 * count
    raise ValueError("servers.dat")


def _write_root(compound: dict) -> bytes:
    return bytes([10, 0, 0]) + _write_compound(compound)


def _write_compound(compound: dict) -> bytes:
    out = bytearray()
    for name, tag in compound.items():
        out.append(tag.kind)
        out += _write_utf(name)
        out += _write_payload(tag.kind, tag.value)
    out.append(0)
    return bytes(out)


def _write_utf(text: str) -> bytes:
    encoded = text.encode("utf-8", "surrogateescape")
    if len(encoded) > 65535:
        raise ValueError("servers.dat")
    return struct.pack(">H", len(encoded)) + encoded


def _write_payload(kind: int, value) -> bytes:
    if kind == 1:
        return struct.pack(">b", value)
    if kind == 2:
        return struct.pack(">h", value)
    if kind == 3:
        return struct.pack(">i", value)
    if kind == 4:
        return struct.pack(">q", value)
    if kind == 5:
        return struct.pack(">f", value)
    if kind == 6:
        return struct.pack(">d", value)
    if kind == 7:
        return struct.pack(">i", len(value)) + value
    if kind == 8:
        return _write_utf(value)
    if kind == 9:
        element, items = value
        body = b"".join(_write_payload(element, item) for item in items)
        return bytes([element]) + struct.pack(">i", len(items)) + body
    if kind == 10:
        return _write_compound(value)
    if kind == 11:
        return struct.pack(">i", len(value)) + struct.pack(f">{len(value)}i", *value)
    if kind == 12:
        return struct.pack(">i", len(value)) + struct.pack(f">{len(value)}q", *value)
    raise ValueError("servers.dat")


def _windowless_java(java: str) -> str:
    if sys.platform != "win32":
        return java
    windowless = Path(java).with_name("javaw.exe")
    if windowless.is_file():
        return str(windowless)
    return java


def _version_json(directory: Path, version: str) -> Path:
    return directory / "versions" / version / f"{version}.json"


def _missing_module_jars(directory: Path, version_id: str) -> bool:
    path = _version_json(directory, version_id)
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    libraries = str(directory / "libraries")
    for item in data.get("arguments", {}).get("jvm", []):
        if not isinstance(item, str) or "${library_directory}" not in item:
            continue
        text = item.replace("${library_directory}", libraries).replace("${classpath_separator}", os.pathsep)
        for part in text.split(os.pathsep):
            if part.endswith(".jar") and not Path(part).is_file():
                return True
    return False


def _split_args(text: str) -> list[str]:
    tokens = re.findall(r'(?:[^\s"]|"[^"]*")+', text or "")
    return [token.replace('"', "") for token in tokens if token.replace('"', "")]


def total_memory_gb() -> int:
    if sys.platform != "win32":
        return 16

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 16
    return max(2, int(status.ullTotalPhys / (1024 ** 3)))


def _fit_ram(requested: int) -> tuple[int, str | None]:
    requested = max(1, min(64, int(requested)))
    if sys.platform != "win32":
        return requested, None
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return requested, None
    total = int(status.ullTotalPhys / (1024 ** 3))
    cap = max(2, total - 2) if total > 3 else 2
    fitted = min(requested, cap)
    if fitted < requested:
        return fitted, f"Memory lowered to {fitted} GB."
    return requested, None


def _start_process(command: list[str], directory: Path, on_exit) -> None:
    """Start the game. on_exit(code, failure_text_or_None) runs once the game closes."""
    log_path = directory / "launcher-game.log"
    log_handle = log_path.open("wb")
    startupinfo = None
    flags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 1
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(directory),
            creationflags=flags,
            startupinfo=startupinfo,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_handle.close()
    try:
        code = process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        threading.Thread(target=_watch_exit, args=(process, log_path, on_exit), daemon=True).start()
        return
    raise RuntimeError(_failure_text(log_path, code))


def _watch_exit(process: subprocess.Popen, log_path: Path, on_exit) -> None:
    code = process.wait()
    if on_exit is None:
        return
    try:
        on_exit(code, None if code in (0, None) else _failure_text(log_path, code))
    except Exception:
        return


def _failure_text(path: Path, code: int) -> str:
    try:
        lines = path.read_bytes()[-6000:].decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    useful = [line.strip() for line in lines if line.strip()]
    if useful:
        return "\n".join(useful[-8:])[:700]
    return f"The game closed before a window appeared (code {code})."
