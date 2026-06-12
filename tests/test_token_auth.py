"""Tests for wolf_comm.token_auth."""
import pytest

from wolf_comm.token_auth import PasswordToLong, TokenAuth, Tokens


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
