import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, parse_qs, urlparse

from httpx import AsyncClient
from lxml import html
import pkce
import shortuuid

from wolf_comm import constants

_LOGGER = logging.getLogger(__name__)

# OpenID Connect Constants
OPENID_SCOPE = "openid profile api role"
OAUTH_GRANT_TYPE = "authorization_code"
PKCE_METHOD = "S256"
RESPONSE_TYPE = "code"
RESPONSE_MODE = "query"
DEFAULT_LANGUAGE = "de-DE"
TOKEN_ENDPOINT_PATH = "/connect/token"
LOGIN_ENDPOINT_PATH = "/Account/Login"
AUTHORIZE_CALLBACK_PATH = "/idsrv/connect/authorize/callback"
CLIENT_ID = "smartset.web"
VERIFICATION_TOKEN_XPATH = '//form/input/@value'


@dataclass
class Tokens:
    """Represents OAuth2 tokens with expiration tracking."""

    access_token: str
    expires_in: int
    token_type: str = "Bearer"

    @property
    def expire_date(self) -> datetime:
        """Calculate token expiration time."""
        return datetime.now() + timedelta(seconds=self.expires_in)

    def is_expired(self) -> bool:
        """Check if token has expired."""
        return self.expire_date < datetime.now()


class TokenAuth:
    """Handles OAuth2 PKCE authentication with Wolf SmartSet identity server."""

    _MAX_PASSWORD_LENGTH = 30

    def __init__(self, username: str, password: str) -> None:
        """Initialize authentication handler.

        Args:
            username: Wolf SmartSet account username
            password: Wolf SmartSet account password

        Raises:
            PasswordTooLong: If password exceeds maximum length
        """
        if len(password) > self._MAX_PASSWORD_LENGTH:
            raise PasswordTooLong(
                f"Password length {len(password)} exceeds maximum of {self._MAX_PASSWORD_LENGTH} characters"
            )
        self.username = username
        self.password = password

    async def token(self, client: AsyncClient) -> Tokens:
        """Authenticate and retrieve access token using OAuth2 PKCE flow.

        Args:
            client: AsyncClient for HTTP requests

        Returns:
            Tokens object with access_token and expiration info

        Raises:
            InvalidAuth: If authentication fails at any step
        """
        try:
            # Step 1: Generate PKCE parameters
            code_verifier, code_challenge = pkce.generate_pkce_pair()
            state = shortuuid.uuid()

            # Step 2: Get verification token from login form
            verification_token = await self._get_verification_token(client, code_challenge, state)

            # Step 3: Submit login credentials and get authorization code
            code = await self._get_authorization_code(
                client, verification_token, code_challenge, state
            )

            # Step 4: Exchange code for access token
            tokens = await self._get_access_token(client, code, code_verifier)

            _LOGGER.info("Successfully authenticated with Wolf SmartSet")
            return tokens

        except (KeyError, IndexError, AttributeError, ValueError) as e:
            _LOGGER.error("Authentication error: %s", e, exc_info=True)
            raise InvalidAuth(f"Authentication failed: {e}") from e

    async def _get_verification_token(
        self, client: AsyncClient, code_challenge: str, state: str
    ) -> str:
        """Retrieve CSRF verification token from login form.

        Args:
            client: AsyncClient for HTTP requests
            code_challenge: PKCE code challenge
            state: OAuth state parameter

        Returns:
            Verification token from form

        Raises:
            InvalidAuth: If verification token cannot be extracted
        """
        params = self._build_authorize_params(code_challenge, state)

        response = await client.get(
            url=f"{constants.AUTHENTICATION_BASE_URL}{LOGIN_ENDPOINT_PATH}",
            params=params,
        )

        _LOGGER.debug("Login form response status: %s", response.status_code)

        try:
            tree = html.document_fromstring(response.text)
            elements = tree.xpath(VERIFICATION_TOKEN_XPATH)

            if not elements:
                raise ValueError("Verification token not found in login form")

            token = elements[0]
            _LOGGER.debug("Verification token extracted successfully")
            return token

        except Exception as e:
            _LOGGER.error("Failed to extract verification token: %s", e)
            raise InvalidAuth("Could not extract verification token from login form") from e

    async def _get_authorization_code(
        self,
        client: AsyncClient,
        verification_token: str,
        code_challenge: str,
        state: str,
    ) -> str:
        """Submit credentials and retrieve authorization code.

        Args:
            client: AsyncClient for HTTP requests
            verification_token: CSRF token from login form
            code_challenge: PKCE code challenge
            state: OAuth state parameter

        Returns:
            Authorization code

        Raises:
            InvalidAuth: If authorization code cannot be obtained
        """
        authorize_params = self._build_authorize_params(code_challenge, state)
        return_url = f"{constants.AUTHENTICATION_URL}{AUTHORIZE_CALLBACK_PATH}?{urlencode(authorize_params)}"

        login_data = {
            "Input.Username": self.username,
            "Input.Password": self.password,
            "__RequestVerificationToken": verification_token,
        }

        response = await client.post(
            url=f"{constants.AUTHENTICATION_BASE_URL}{LOGIN_ENDPOINT_PATH}",
            params={"ReturnUrl": return_url},
            data=login_data,
            follow_redirects=True,
        )

        _LOGGER.debug("Login response status: %s", response.status_code)

        try:
            parsed_url = urlparse(str(response.url))
            query_params = parse_qs(parsed_url.query)

            if "code" not in query_params:
                raise ValueError("Authorization code not found in redirect URL")

            code = query_params["code"][0]
            _LOGGER.debug("Authorization code obtained successfully")
            return code

        except (KeyError, IndexError) as e:
            _LOGGER.error("Failed to extract authorization code: %s", e)
            raise InvalidAuth("Could not obtain authorization code") from e

    async def _get_access_token(
        self,
        client: AsyncClient,
        code: str,
        code_verifier: str,
    ) -> Tokens:
        """Exchange authorization code for access token.

        Args:
            client: AsyncClient for HTTP requests
            code: Authorization code
            code_verifier: PKCE code verifier

        Returns:
            Tokens object with access_token and expiration info

        Raises:
            InvalidAuth: If token exchange fails
        """
        token_data = {
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": f"{constants.BASE_URL}/signin-callback.html",
            "code_verifier": code_verifier,
            "grant_type": OAUTH_GRANT_TYPE,
        }

        response = await client.post(
            url=f"{constants.AUTHENTICATION_BASE_URL}{TOKEN_ENDPOINT_PATH}",
            data=token_data,
        )

        _LOGGER.debug("Token endpoint response status: %s", response.status_code)

        try:
            token_response = response.json()

            if "error" in token_response:
                error_desc = token_response.get("error_description", token_response.get("error"))
                raise InvalidAuth(f"Token endpoint error: {error_desc}")

            access_token = token_response.get("access_token")
            expires_in = token_response.get("expires_in", 3600)

            if not access_token:
                raise ValueError("No access token in response")

            return Tokens(access_token=access_token, expires_in=expires_in)

        except Exception as e:
            _LOGGER.error("Failed to process token response: %s", e)
            raise InvalidAuth("Could not obtain access token") from e

    @staticmethod
    def _build_authorize_params(code_challenge: str, state: str) -> dict:
        """Build OAuth authorization parameters.

        Args:
            code_challenge: PKCE code challenge
            state: OAuth state parameter

        Returns:
            Dictionary of authorization parameters
        """
        return {
            "ReturnUrl": AUTHORIZE_CALLBACK_PATH,
            "client_id": constants.AUTHENTICATION_CLIENT,
            "redirect_uri": f"{constants.BASE_URL}/signin-callback.html",
            "response_type": RESPONSE_TYPE,
            "scope": OPENID_SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": PKCE_METHOD,
            "response_mode": RESPONSE_MODE,
            "lang": DEFAULT_LANGUAGE,
        }


class InvalidAuth(Exception):
    """Raised when authentication fails.

    This can occur due to:
    - Invalid username or password
    - CSRF token extraction failure
    - Authorization code retrieval issue
    - Token exchange failure
    - Wolf SmartSet server issues
    """

    pass


class PasswordTooLong(Exception):
    """Raised when password exceeds maximum allowed length.

    Wolf SmartSet limits passwords to 30 characters maximum.
    """

    pass
