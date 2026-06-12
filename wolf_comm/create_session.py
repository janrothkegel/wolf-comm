from datetime import datetime

from httpx import AsyncClient, Headers

from wolf_comm import constants
from wolf_comm.constants import SESSION_ID, TIMESTAMP
from wolf_comm.helpers import bearer_header


# BrowserSessionId is a JSON number on the wire (verified against the
# openHAB wolfsmartset binding's CreateSession2DTO, which declares Integer),
# matching WolfClient.session_id: Optional[int].
async def create_session(client: AsyncClient, token: str) -> int:
    data = {
        TIMESTAMP: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    resp = await client.post(
        constants.BASE_URL_PORTAL + "/api/portal/CreateSession2",
        headers=Headers({
            **bearer_header(token),
            **{"Content-Type": "application/json"}
            }
        ),
        json=data
    )

    return resp.json()['BrowserSessionId']


async def update_session(client: AsyncClient, token: str, session_id: int):
    data = {
        SESSION_ID: session_id
    }
    await client.post(
        constants.BASE_URL_PORTAL + "/api/portal/UpdateSession",
        headers=Headers({
            **bearer_header(token),
            **{"Content-Type": "application/json"}
            }
        ),
        json=data)
