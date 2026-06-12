"""Shared fixtures for the wolf_comm test suite."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Make the repo root importable regardless of how pytest is invoked.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wolf_comm.token_auth import Tokens  # noqa: E402
from wolf_comm.wolf_client import WolfClient  # noqa: E402

EXAMPLES_DIR = REPO_ROOT / "parameters-examples"


@pytest.fixture(scope="session")
def gas_desc():
    """Real GetGuiDescriptionForGateway response for a gas boiler (valid JSON)."""
    with open(EXAMPLES_DIR / "gasparameters.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def gashybrid_desc():
    """Real GetGuiDescriptionForGateway response for a gas hybrid system (valid JSON)."""
    with open(EXAMPLES_DIR / "gashybridparameters.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def heatpump_desc():
    """Real GetGuiDescriptionForGateway response for a heat pump system."""
    with open(EXAMPLES_DIR / "heatpumpparameter.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def luftung_desc():
    """Real GetGuiDescriptionForGateway response for a ventilation system."""
    with open(EXAMPLES_DIR / "luftung.json", encoding="utf-8") as f:
        return json.load(f)


def make_authorized_client(*responses):
    """Build a WolfClient wired to a mocked httpx client.

    Tokens/session are pre-seeded so no network auth happens and the
    session-refresh timer is in the future so update_session is skipped.
    Each entry in *responses* is returned (in order) from client.request().
    """
    http = AsyncMock()
    http.request = AsyncMock(side_effect=list(responses))
    wc = WolfClient("user", "password", client=http)
    wc.tokens = Tokens("test-token", 3600)
    wc.session_id = 1
    wc.last_session_refesh = datetime.now() + timedelta(seconds=60)
    return wc, http
