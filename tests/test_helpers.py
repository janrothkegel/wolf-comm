"""Tests for wolf_comm.helpers."""
from wolf_comm.helpers import bearer_header


def test_bearer_header():
    assert bearer_header("abc") == {"Authorization": "Bearer abc"}
