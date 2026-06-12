import datetime
import logging

from httpx import AsyncClient

from wolf_comm import constants

from lxml import html
from lxml.etree import ParserError
import pkce
import shortuuid


_LOGGER = logging.getLogger(__name__)


def _extract_verification_token(page_text: str):
    """Pull the anti-forgery token out of the login page HTML.

    Targets the input by name instead of position: on the live page the
    username/password inputs are wrapped in divs while the hidden token
    inputs are direct form children, so a positional '//form/input' lookup
    only works by accident of the current markup. The page contains two
    forms, each carrying the same per-response token, so taking the first
    match is correct. Returns None when the page is empty/unparseable or
    carries no usable token (missing field or empty value) — all signs of a
    rate-limited or degraded portal rather than a real login page.
    """
    if not page_text or not page_text.strip():
        return None
    try:
        tree = html.document_fromstring(page_text)
    except ParserError:
        return None
    elements = tree.xpath(f'//input[@name="{constants.REQUEST_VERIFICATION_TOKEN}"]/@value')
    if not elements:
        return None
    return elements[0] or None


class Tokens:
    """Has only one token: access"""

    def __init__(self, access_token: str, expires_in: int):
        self.access_token = access_token
        self.expire_date = datetime.datetime.now() + datetime.timedelta(seconds=expires_in)

    def is_expired(self) -> bool:
        return self.expire_date < datetime.datetime.now()


class TokenAuth:
    """Adds poosibility to login with passed credentials"""

    def __init__(self, username: str, password: str):
        if len(password) > 30:
            raise PasswordToLong(f'Your password is {len(password)} long, but maximum is 30')
        self.username = username
        self.password = password

    async def token(self, client: AsyncClient) -> Tokens:
        try:
            # Generate client-sided variables for OpenID
            code_verifier, code_challenge = pkce.generate_pkce_pair()
            state = shortuuid.uuid()

            # Retrieve verification token from WOLF website
            r = await client.get(
                url=f'{constants.AUTHENTICATION_BASE_URL}/Account/Login',
                params={
                    'ReturnUrl': '/idsrv/connect/authorize/callback',
                    'client_id': constants.AUTHENTICATION_CLIENT,
                    'redirect_uri': f'{constants.BASE_URL}/signin-callback.html',
                    'response_type': 'code',
                    'scope': 'openid profile api role',
                    'state': state,
                    'code_challenge': code_challenge,
                    'code_challenge_method': 'S256',
                    'response_mode': 'query',
                    'lang': 'en-GB',
                }
            )

            _LOGGER.debug('Verification code response: %s', r.content)

            verification_token = _extract_verification_token(r.text)

            if verification_token is not None:

                _LOGGER.debug('Verification token: %s', verification_token)

                # Get code
                login_data = {
                    "Input.Username": self.username,
                    "Input.Password": self.password,
                    constants.REQUEST_VERIFICATION_TOKEN: verification_token
                }

                r = await client.post(
                    url=f'{constants.AUTHENTICATION_BASE_URL}/Account/Login',
                    params={
                        'ReturnUrl': f'{constants.AUTHENTICATION_URL}/connect/authorize/callback?'
                        f'client_id={constants.AUTHENTICATION_CLIENT}'
                        f'&redirect_uri={constants.BASE_URL}/signin-callback.html'
                        '&response_type=code'
                        '&scope=openid profile api role'
                        f'&state={state}'
                        f'&code_challenge={code_challenge}'
                        '&code_challenge_method=S256'
                        '&response_mode=query'
                        '&lang=en-GB'
                    },
                    headers={
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                    },
                    data=login_data,
                    cookies=r.cookies,
                    follow_redirects=True
                )

                _LOGGER.debug('Code response: %s', r.content)
                code = r.url.params['code']

                headers = {
                    "Cache-control": "no-cache",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:108.0) Gecko/20100101 Firefox/108.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-GB,en;q=0.8,en-US;q=0.5,en;q=0.3",
                    "Referer": constants.BASE_URL + "/",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "TE": "trailers"
                }

                # Get token
                r = await client.post(
                    constants.AUTHENTICATION_BASE_URL + "/connect/token",
                    headers=headers,
                    data={
                        "client_id": "smartset.web",
                        "code": code,
                        "redirect_uri": constants.BASE_URL + "/signin-callback.html",
                        "code_verifier": code_verifier,
                        "grant_type": "authorization_code",
                    },
                )

                json = r.json()
                _LOGGER.debug('Token response: %s', json)
                if "error" in json:
                    raise InvalidAuth
                _LOGGER.info('Successfully authenticated')
                return Tokens(json.get("access_token"), json.get("expires_in"))
            else:
                _LOGGER.error('No verification token on the login page; '
                              'portal is rate-limiting or unavailable')
                raise PortalUnavailable
        except InvalidAuth:
            raise
        except Exception as e:
            _LOGGER.error('An error occurred: %s', e)
            raise InvalidAuth from e


class InvalidAuth(Exception):
    """Please check whether you entered an invalid username or password. If everything looks fine then probably there is an issue with Wolf SmartSet servers."""
    pass


class PortalUnavailable(InvalidAuth):
    """The login page did not contain the verification token — the portal is
    rate-limiting, in maintenance, or serving an error page. Credentials were
    never submitted; retry later instead of re-authenticating. Subclasses
    InvalidAuth so existing handlers keep working; catch this first to
    distinguish portal trouble from genuinely wrong credentials."""
    pass


class PasswordToLong(Exception):
    """Please check the lenght of your provided password. Wolf SmartSet server only accept password lenght less or equal 30 characters."""
    pass
