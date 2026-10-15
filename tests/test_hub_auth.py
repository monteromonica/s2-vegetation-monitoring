import base64
import json

import pytest

from s2veg import hub


def _fake_jwt(payload: dict) -> str:
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


def test_describe_token_reads_expiry_without_exposing_the_token():
    token = _fake_jwt({"iss": "https://issuer.example", "iat": 1_700_000_000, "exp": 1_700_003_600})
    info = hub.describe_token(token)
    assert info["lifetime_s"] == 3600
    assert info["expires_utc"].startswith("2023-11-14")
    assert token not in json.dumps(info)


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("SH_PROVIDER", raising=False)
    assert "services.sentinel-hub.com" in hub.provider_urls()["token_url"]
    monkeypatch.setenv("SH_PROVIDER", "cdse")
    assert "identity.dataspace.copernicus.eu" in hub.provider_urls()["token_url"]
    monkeypatch.setenv("SH_PROVIDER", "nonsense")
    with pytest.raises(RuntimeError):
        hub.provider_urls()


def test_missing_credentials_fail_with_a_clear_message(monkeypatch):
    monkeypatch.delenv("SH_CLIENT_ID", raising=False)
    monkeypatch.delenv("SH_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SH_CLIENT_ID"):
        hub.credentials()


def test_token_error_messages_are_actionable(monkeypatch):
    class Resp:
        status_code, text = 401, '{"error":"invalid_client"}'
    monkeypatch.setattr(hub.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError, match="wrong client id/secret"):
        hub.get_token("id", "secret")
