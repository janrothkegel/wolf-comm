from datetime import datetime
import logging

from httpx import AsyncClient, Headers, HTTPStatusError, RequestError

from wolf_comm import constants
from wolf_comm.constants import SESSION_ID, TIMESTAMP
from wolf_comm.helpers import bearer_header

_LOGGER = logging.getLogger(__name__)

async def create_session(client: AsyncClient, token: str) -> str:
    data = {
        TIMESTAMP: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        resp = await client.post(
            constants.BASE_URL_PORTAL + "/api/portal/CreateSession2",
            headers=Headers({
                **bearer_header(token),
                "Content-Type": "application/json"
            }),
            json=data
        )
        resp.raise_for_status()
        session_id = resp.json().get('BrowserSessionId')
        if not session_id:
            raise ValueError("BrowserSessionId not found in the response")
        _LOGGER.debug('Created session with ID: %s', session_id)
        return session_id
    except HTTPStatusError as e:
        _LOGGER.error('HTTP error occurred while creating session: %s', e)
        raise
    except RequestError as e:
        _LOGGER.error('Request error occurred while creating session: %s', e)
        raise
    except Exception as e:
        _LOGGER.error('An unexpected error occurred while creating session: %s', e)
        raise

async def update_session(client: AsyncClient, token: str, session_id: str):
    data = {
        SESSION_ID: session_id
    }
    try:
        resp = await client.post(
            constants.BASE_URL_PORTAL + "/api/portal/UpdateSession",
            headers=Headers({
                **bearer_header(token),
                "Content-Type": "application/json"
            }),
            json=data
        )
        resp.raise_for_status()
        _LOGGER.debug('Updated session with ID: %s', session_id)
    except HTTPStatusError as e:
        _LOGGER.error('HTTP error occurred while updating session: %s', e)
        raise
    except RequestError as e:
        _LOGGER.error('Request error occurred while updating session: %s', e)
        raise
    except Exception as e:
        _LOGGER.error('An unexpected error occurred while updating session: %s', e)
        raise
