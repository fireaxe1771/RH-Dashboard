"""Unit tests for backend/auth.py TokenVerifier and get_current_user.

These tests exercise the *real* TokenVerifier logic (issuer/audience
validation, malformed-token handling) in isolation, bypassing the
conftest ``mock_entra_verification`` autouse fixture that stubs out
``verify_token`` for the integration tests.

A test RSA key pair is generated with ``cryptography`` and used to sign
JWTs directly, so no network/JWKS fetch is required.
"""
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from jose import jwt

# Capture the REAL unbound methods at import time, before any autouse
# fixture (conftest.mock_entra_verification) has a chance to monkeypatch
# them. These references are used to restore the real implementation
# inside the tests below.
from auth import TokenVerifier

_real_verify_token = TokenVerifier.verify_token
_real_get_public_key = TokenVerifier.get_public_key
_real_fetch_jwks = TokenVerifier._fetch_jwks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rsa_key_pair():
    """Generates a 2048-bit RSA key pair for signing/verifying test JWTs.

    Returns ``(private_pem, public_key)`` where ``private_pem`` is PEM-encoded
    (python-jose's encode requires PEM/key material, not a raw key object) and
    ``public_key`` is a cryptography RSAPublicKey object (accepted by decode).
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    return private_pem, public_key


@pytest.fixture
def verifier(monkeypatch):
    """A TokenVerifier with real verify_token logic and test tenant/client ids.

    Re-applies the real ``verify_token`` (overriding the conftest mock) and
    stubs ``get_public_key`` to return the test RSA public key so no JWKS
    fetch occurs.
    """
    monkeypatch.setattr(TokenVerifier, "verify_token", _real_verify_token)
    monkeypatch.setattr(TokenVerifier, "get_public_key", _real_get_public_key)
    monkeypatch.setattr(TokenVerifier, "_fetch_jwks", _real_fetch_jwks)

    v = TokenVerifier()
    # Use deterministic ids so issuer/audience checks are predictable.
    v.tenant_id = "test-tenant-id"
    v.client_id = "test-client-id"
    return v


def _make_token(
    private_key,
    *,
    kid="test-kid",
    issuer="https://login.microsoftonline.com/test-tenant-id/v2.0",
    audience="test-client-id",
    expired=False,
    extra_claims=None,
):
    """Builds and signs a test RS256 JWT with the given claims."""
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now - 10 if expired else now + 3600,
        "preferred_username": "john.doe@streamlineas.com",
        "name": "John Doe",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# _build_dev_user
# ---------------------------------------------------------------------------

def test_build_dev_user_returns_expected_mock_identity(monkeypatch):
    # Patch the settings object that auth.py actually references. Importing
    # from config here would get a different object if test_config_validation's
    # fresh_settings fixture has reloaded config.
    import auth
    monkeypatch.setattr(auth.settings, "AZURE_CLIENT_ID", "dev-client-id")
    user = TokenVerifier._build_dev_user()
    assert user["preferred_username"] == "dev.local@streamlineas.com"
    assert user["upn"] == "dev.local@streamlineas.com"
    assert user["name"] == "Local Dev User"
    assert user["iss"] == "local-dev-bypass"
    assert user["aud"] == "dev-client-id"


# ---------------------------------------------------------------------------
# verify_token: malformed / missing kid
# ---------------------------------------------------------------------------

def test_verify_token_rejects_malformed_token(verifier):
    with pytest.raises(HTTPException) as exc:
        verifier.verify_token("not-a-jwt")
    assert exc.value.status_code == 401
    assert "Invalid token format" in exc.value.detail


def test_verify_token_rejects_token_with_no_kid(verifier, rsa_key_pair):
    private_key, _ = rsa_key_pair
    # Sign without a kid header.
    token = jwt.encode(
        {"iss": "x", "aud": "x", "iat": int(time.time()), "exp": int(time.time()) + 3600},
        private_key,
        algorithm="RS256",
    )
    with pytest.raises(HTTPException) as exc:
        verifier.verify_token(token)
    assert exc.value.status_code == 401
    assert "kid" in exc.value.detail


# ---------------------------------------------------------------------------
# verify_token: signature / issuer / audience validation
# ---------------------------------------------------------------------------

def test_verify_token_accepts_valid_token(verifier, rsa_key_pair, monkeypatch):
    private_key, public_key = rsa_key_pair
    monkeypatch.setattr(verifier, "get_public_key", lambda kid: public_key)
    token = _make_token(private_key)
    payload = verifier.verify_token(token)
    assert payload["preferred_username"] == "john.doe@streamlineas.com"
    assert payload["aud"] == "test-client-id"


def test_verify_token_rejects_wrong_issuer(verifier, rsa_key_pair, monkeypatch):
    private_key, public_key = rsa_key_pair
    monkeypatch.setattr(verifier, "get_public_key", lambda kid: public_key)
    token = _make_token(
        private_key,
        issuer="https://login.microsoftonline.com/wrong-tenant/v2.0",
    )
    with pytest.raises(HTTPException) as exc:
        verifier.verify_token(token)
    assert exc.value.status_code == 401


def test_verify_token_rejects_wrong_audience(verifier, rsa_key_pair, monkeypatch):
    private_key, public_key = rsa_key_pair
    monkeypatch.setattr(verifier, "get_public_key", lambda kid: public_key)
    token = _make_token(private_key, audience="some-other-client-id")
    with pytest.raises(HTTPException) as exc:
        verifier.verify_token(token)
    assert exc.value.status_code == 401


def test_verify_token_accepts_legacy_sts_issuer(verifier, rsa_key_pair, monkeypatch):
    private_key, public_key = rsa_key_pair
    monkeypatch.setattr(verifier, "get_public_key", lambda kid: public_key)
    token = _make_token(
        private_key,
        issuer="https://sts.windows.net/test-tenant-id/",
    )
    payload = verifier.verify_token(token)
    assert payload["iss"] == "https://sts.windows.net/test-tenant-id/"


def test_verify_token_rejects_expired_token(verifier, rsa_key_pair, monkeypatch):
    private_key, public_key = rsa_key_pair
    monkeypatch.setattr(verifier, "get_public_key", lambda kid: public_key)
    token = _make_token(private_key, expired=True)
    with pytest.raises(HTTPException) as exc:
        verifier.verify_token(token)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_user_returns_dev_user_when_bypass_enabled(monkeypatch):
    # Patch the settings object that auth.py actually references (see
    # test_build_dev_user_returns_expected_mock_identity for rationale).
    import auth
    from auth import get_current_user
    monkeypatch.setattr(auth.settings, "DEV_AUTH_BYPASS", True)
    user = await get_current_user(credentials=None)
    assert user["preferred_username"] == "dev.local@streamlineas.com"


@pytest.mark.asyncio
async def test_get_current_user_raises_401_when_no_credentials(monkeypatch):
    import auth
    from auth import get_current_user
    monkeypatch.setattr(auth.settings, "DEV_AUTH_BYPASS", False)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(credentials=None)
    assert exc.value.status_code == 401
    assert "missing Bearer token" in exc.value.detail
