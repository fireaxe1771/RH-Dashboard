import logging
import urllib.request
import json
from jose import jwt
from jose.exceptions import JWTError
from fastapi import Request, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import settings

logger = logging.getLogger(__name__)

# Security scheme helper
security_scheme = HTTPBearer(auto_error=False)

class TokenVerifier:
    """Retrieves Microsoft public JWKS certificates and validates incoming API request tokens."""

    def __init__(self):
        self.jwks: dict = {}
        self.tenant_id = settings.AZURE_TENANT_ID
        self.client_id = settings.AZURE_CLIENT_ID

    @staticmethod
    def _build_dev_user() -> dict:
        """Returns a stable mock identity for local-development bypass mode."""
        return {
            "preferred_username": "dev.local@streamlineas.com",
            "upn": "dev.local@streamlineas.com",
            "name": "Local Dev User",
            "iss": "local-dev-bypass",
            "aud": settings.AZURE_CLIENT_ID or "local-dev",
        }

    def _fetch_jwks(self) -> dict:
        """Retrieves active Microsoft public key sets from discovery endpoint."""
        url = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
        try:
            logger.info("Downloading active Microsoft JWKS public key set...")
            with urllib.request.urlopen(url, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"Failed to fetch Microsoft signing keys from AAD: {e}")
            # Fail loudly on configuration/network failure (don't return empty mock keys)
            raise RuntimeError(f"Entra ID Security Catalog Unreachable: {e}")

    def get_public_key(self, kid: str) -> dict:
        """Retrieves public key corresponding to key ID (kid) from cached JWKS."""
        if not self.jwks:
            self.jwks = self._fetch_jwks()
            
        for key in self.jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
                
        # If kid not found, reload JWKS once (keys might have rotated)
        logger.info("Key ID not found in cache. Re-fetching Microsoft certificates...")
        self.jwks = self._fetch_jwks()
        for key in self.jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
                
        raise JWTError("Unknown certificate key ID: Token signature verification key missing.")

    def verify_token(self, token: str) -> dict:
        """Verifies JWT token signature and audience claims, returning payload claims.
        
        Throws JWTError or raises HTTPException if validation fails.
        """
        try:
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token format: {e}"
            )

        kid = unverified_header.get("kid")
        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token header missing signature key ID (kid)."
            )

        public_key = self.get_public_key(kid)

        # Allow Azure AD issuers:
        # Standard format is https://login.microsoftonline.com/{tenant_id}/v2.0
        # or older sts format https://sts.windows.net/{tenant_id}/
        issuers = [
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/"
        ]

        try:
            # Decode and verify token
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self.client_id,
                options={"verify_aud": True, "verify_iss": False, "verify_exp": True}
            )
            
            # Manually verify issuer list
            iss = payload.get("iss")
            if iss not in issuers:
                raise JWTError(f"Issuer '{iss}' does not match expected tenant options.")
                
            return payload
        except JWTError as e:
            logger.error(f"JWT Verification failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token signature verification failed: {e}"
            )

# Create token verifier instance
verifier = TokenVerifier()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security_scheme)) -> dict:
    """Dependency verifying security bearer credentials, returning verified user claim dictionary."""
    if settings.DEV_AUTH_BYPASS:
        return verifier._build_dev_user()

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing Bearer token credentials."
        )
        
    token = credentials.credentials
    return verifier.verify_token(token)
