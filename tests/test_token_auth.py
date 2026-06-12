"""Tests for wolf_comm.token_auth."""
from unittest.mock import AsyncMock

import pytest

from wolf_comm.token_auth import (
    InvalidAuth,
    PasswordToLong,
    PortalUnavailable,
    TokenAuth,
    Tokens,
    _extract_verification_token,
)

TOKEN = "CfDJ8K9bQxsJfNlKtSTnencuRtest-token-value"

# Mirrors the structure of the live /idsrv/Account/Login page (verified
# 2026-06-12): username/password inputs nested in divs, and TWO forms each
# carrying an identical hidden __RequestVerificationToken as a direct child.
REALISTIC_LOGIN_HTML = f"""
<!DOCTYPE html>
<html><body>
<form method="post" action="/idsrv/Account/Login">
  <div><input name="Input.Username" type="text" value=""></div>
  <div><input name="Input.Password" type="password" value=""></div>
  <button type="submit">Anmelden</button>
  <input name="__RequestVerificationToken" type="hidden" value="{TOKEN}">
</form>
<form method="post" action="/idsrv/Account/ForgotPassword">
  <input name="__RequestVerificationToken" type="hidden" value="{TOKEN}">
</form>
</body></html>
"""

# Hostile variant: username/password as *direct* form children ahead of the
# token. The old positional '//form/input/@value' lookup would grab the empty
# username value here; the name-targeted XPath must not.
FLAT_MARKUP_LOGIN_HTML = f"""
<html><body>
<form method="post">
  <input name="Input.Username" type="text" value="">
  <input name="Input.Password" type="password" value="">
  <input name="__RequestVerificationToken" type="hidden" value="{TOKEN}">
</form>
</body></html>
"""


def test_tokens_not_expired():
    tokens = Tokens("token", 3600)
    assert tokens.access_token == "token"
    assert tokens.is_expired() is False


def test_tokens_expired():
    tokens = Tokens("token", -1)
    assert tokens.is_expired() is True


def test_token_auth_accepts_30_char_password():
    auth = TokenAuth("user", "x" * 30)
    assert auth.username == "user"
    assert auth.password == "x" * 30


def test_token_auth_rejects_31_char_password():
    with pytest.raises(PasswordToLong):
        TokenAuth("user", "x" * 31)


def test_extract_verification_token_from_realistic_login_page():
    assert _extract_verification_token(REALISTIC_LOGIN_HTML) == TOKEN


def test_extract_verification_token_survives_flat_markup():
    assert _extract_verification_token(FLAT_MARKUP_LOGIN_HTML) == TOKEN


def test_extract_verification_token_missing_returns_none():
    assert _extract_verification_token("<html><body><form></form></body></html>") is None


def test_extract_verification_token_empty_or_unparseable_returns_none():
    # An empty/whitespace body (rate-limit or maintenance page) must return
    # None instead of letting lxml's ParserError escape — token() then raises
    # PortalUnavailable rather than a misleading InvalidAuth.
    assert _extract_verification_token("") is None
    assert _extract_verification_token("   \n\t") is None


def test_extract_verification_token_empty_value_returns_none():
    # A token input with an empty value is unusable; posting it would fail
    # later with a misleading InvalidAuth, so treat it as no-token.
    html = '<form><input name="__RequestVerificationToken" value=""></form>'
    assert _extract_verification_token(html) is None


async def test_empty_login_page_raises_portal_unavailable():
    auth = TokenAuth("user", "pass")
    response = AsyncMock()
    response.text = ""
    client = AsyncMock()
    client.get.return_value = response

    with pytest.raises(PortalUnavailable):
        await auth.token(client)


def test_auth_exceptions_importable_from_package_root():
    # Consumers are told to catch these; they must not need to know the
    # token_auth submodule path.
    from wolf_comm import (  # noqa: F401
        InvalidAuth as _ia,
        PasswordToLong as _ptl,
        PortalUnavailable as _pu,
        Tokens as _t,
        TokenAuth as _ta,
    )


async def test_token_wraps_unexpected_errors_with_cause():
    # Failures inside the OAuth flow surface as InvalidAuth, but the original
    # exception must be preserved via the __cause__ chain for diagnosability.
    auth = TokenAuth("user", "pass")
    client = AsyncMock()
    client.get.side_effect = ConnectionError("dns failure")

    with pytest.raises(InvalidAuth) as exc_info:
        await auth.token(client)

    assert isinstance(exc_info.value.__cause__, ConnectionError)


async def test_missing_token_raises_portal_unavailable():
    # A login page without the verification token (rate-limit/maintenance/
    # error page) raises PortalUnavailable — credentials were never submitted,
    # so consumers must not treat this as wrong username/password. It
    # propagates as-is instead of being re-wrapped.
    auth = TokenAuth("user", "pass")
    response = AsyncMock()
    response.text = "<html><body>no form here</body></html>"
    client = AsyncMock()
    client.get.return_value = response

    with pytest.raises(PortalUnavailable) as exc_info:
        await auth.token(client)

    assert exc_info.value.__cause__ is None


async def test_portal_unavailable_is_catchable_as_invalid_auth():
    # Backward compatibility: existing handlers that catch InvalidAuth must
    # keep working unchanged.
    assert issubclass(PortalUnavailable, InvalidAuth)

    auth = TokenAuth("user", "pass")
    response = AsyncMock()
    response.text = "<html><body>rate limited</body></html>"
    client = AsyncMock()
    client.get.return_value = response

    with pytest.raises(InvalidAuth):
        await auth.token(client)
