"""Windows-user DPAPI storage. Private keys never pass through dashboard routes."""

import ctypes
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


class CredentialError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Credentials:
    key_id: str = field(repr=False)
    private_pem: bytes = field(repr=False)


def validate(key_id, private_pem):
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key_id):
        raise CredentialError("Enter the Kalshi API key ID, not an email or password")
    if not isinstance(private_pem, bytes) or not 1 <= len(private_pem) <= 65536:
        raise CredentialError("Select the downloaded private key file")
    try:
        key = serialization.load_pem_private_key(private_pem, password=None)
    except (ValueError, TypeError):
        raise CredentialError("The file must be a supported unencrypted private PEM key") from None
    if not isinstance(key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
        raise CredentialError("Kalshi requires an RSA or Ed25519 private key")
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < 2048:
        raise CredentialError("RSA keys must be at least 2048 bits")
    return Credentials(key_id, private_pem)


def read_credentials(key_id, path):
    with Path(path).open("rb") as stream:
        return validate(key_id, stream.read(65537))


def default_store_path():
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        raise CredentialError("Windows local storage is available only on your Windows PC")
    return Path(os.environ["LOCALAPPDATA"]) / "KalshiTerminalV2" / "credentials.dpapi"


class WindowsDPAPI:
    """Current Windows user scope; no machine-wide or plaintext fallback."""

    def __init__(self):
        if os.name != "nt":
            raise CredentialError("Windows DPAPI is unavailable on this platform")

    def transform(self, value, decrypt=False):
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        source, output = Blob(len(value), buffer), Blob()
        crypto = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            function = crypto.CryptUnprotectData
            function.argtypes = [
                ctypes.POINTER(Blob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(Blob),
            ]
            args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
        else:
            function = crypto.CryptProtectData
            function.argtypes = [
                ctypes.POINTER(Blob),
                wintypes.LPCWSTR,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(Blob),
            ]
            args = (
                ctypes.byref(source),
                "Kalshi Terminal V2",
                None,
                None,
                None,
                1,
                ctypes.byref(output),
            )
        function.restype = wintypes.BOOL
        if not function(*args):
            raise CredentialError("Windows could not unlock/protect credentials for this user")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            # DPAPI allocates this memory with LocalAlloc. Wipe before freeing it.
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))

    def protect(self, value):
        return self.transform(value)

    def unprotect(self, value):
        return self.transform(value, decrypt=True)


class LocalCredentialStore:
    def __init__(self, path=None, protection=None):
        self.path = Path(path) if path is not None else default_store_path()
        self.protection = protection

    def save(self, credentials, replace=False):
        credentials = validate(credentials.key_id, credentials.private_pem)
        if self.path.exists() and not replace:
            raise CredentialError("Stored credentials already exist; explicitly choose replacement")
        provider = self.protection or WindowsDPAPI()
        payload = json.dumps(
            {
                "version": 1,
                "key_id": credentials.key_id,
                "private_pem": credentials.private_pem.decode("ascii"),
            }
        ).encode()
        encrypted = provider.protect(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, self.path)
            else:
                # Exclusive creation without a race that can replace another import.
                os.link(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def load(self):
        provider = self.protection or WindowsDPAPI()
        try:
            with self.path.open("rb") as stream:
                encrypted = stream.read(131073)
            if len(encrypted) > 131072:
                raise CredentialError("Stored credential file is invalid")
            data = json.loads(provider.unprotect(encrypted))
            if data.get("version") != 1:
                raise CredentialError("Unsupported credential store version")
            return validate(data["key_id"], data["private_pem"].encode("ascii"))
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise CredentialError(
                "Cannot unlock local credentials; re-import with the setup helper"
            ) from None

    def remove(self):
        self.path.unlink(missing_ok=True)


def load_for_settings(settings):
    if settings.key_id or settings.private_key_path:
        if not (settings.key_id and settings.private_key_path):
            raise CredentialError(
                "Set both credential environment variables or remove both to use the local store"
            )
        return read_credentials(settings.key_id, settings.private_key_path)
    if os.name == "nt":
        store = LocalCredentialStore()
        if store.path.exists():
            return store.load()
    return None


def credential_status(settings):
    if settings.key_id or settings.private_key_path:
        return {
            "source": "environment",
            "configured": bool(settings.key_id and settings.private_key_path),
            "scope": "This running machine only",
        }
    if os.name == "nt":
        return {
            "source": "windows_user_store",
            "configured": LocalCredentialStore().path.exists(),
            "scope": "Current Windows account on this PC",
        }
    return {"source": "none", "configured": False, "scope": "This running machine only"}
