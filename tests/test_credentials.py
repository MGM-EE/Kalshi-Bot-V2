import os

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from backend.app.connectors.kalshi import Signer
from backend.app.credentials import (
    CredentialError,
    LocalCredentialStore,
    load_for_settings,
    validate,
)
from backend.app.main import create_app
from backend.app.settings import Settings


class TestProtection:
    __test__ = False

    def __init__(self):
        self.key = Fernet(Fernet.generate_key())

    def protect(self, value):
        return self.key.encrypt(value)

    def unprotect(self, value):
        return self.key.decrypt(value)


def credentials():
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return validate("local-test-key", pem)


def test_encrypted_round_trip_replacement_and_removal(tmp_path):
    value = credentials()
    store = LocalCredentialStore(tmp_path / "credentials.dpapi", TestProtection())
    store.save(value)
    data = store.path.read_bytes()
    assert value.private_pem not in data
    assert value.key_id.encode() not in data
    assert store.load() == value
    assert value.private_pem.decode() not in repr(value)
    with pytest.raises(CredentialError, match="already exist"):
        store.save(credentials())
    assert store.path.read_bytes() == data
    replacement = credentials()
    store.save(replacement, replace=True)
    assert store.load() == replacement
    assert list(tmp_path.iterdir()) == [store.path]
    store.remove()
    assert not store.path.exists()


def test_bad_inputs_never_overwrite_store(tmp_path):
    store = LocalCredentialStore(tmp_path / "credentials.dpapi", TestProtection())
    with pytest.raises(CredentialError):
        validate("bad id with spaces", b"not a key")
    with pytest.raises(CredentialError):
        validate("test-id", b"not a key")
    assert not store.path.exists()


def test_in_memory_signing_and_environment_precedence(tmp_path):
    value = credentials()
    path = tmp_path / "private.pem"
    path.write_bytes(value.private_pem)
    restored = load_for_settings(Settings(key_id=value.key_id, private_key_path=str(path)))
    assert restored == value
    signer = Signer.from_credentials(restored)
    assert signer.headers("GET", "/trade-api/ws/v2")["KALSHI-ACCESS-KEY"] == value.key_id
    with pytest.raises(CredentialError, match="both"):
        load_for_settings(Settings(key_id="partial-key"))


def test_status_api_exposes_only_presence_and_source(tmp_path):
    value = credentials()
    app = create_app(
        Settings(
            key_id=value.key_id,
            private_key_path=str(tmp_path / "private.pem"),
            paper_database_path=":memory:",
        ),
        connect_live=False,
    )
    with TestClient(app) as client:
        response = client.get("/api/credentials/status")
        assert response.json()["source"] == "environment"
        assert response.json()["configured"] is True
        assert value.key_id not in response.text
        assert "private.pem" not in response.text
        assert set(response.json()) == {"source", "configured", "scope"}


@pytest.mark.skipif(os.name != "nt", reason="Requires the current Windows user's real DPAPI")
def test_actual_windows_dpapi(tmp_path):
    store = LocalCredentialStore(tmp_path / "windows.dpapi")
    value = credentials()
    store.save(value)
    assert store.load() == value
