import logging
from fastapi import APIRouter, Depends

from auth import get_current_user
from billing.auth import get_billing_token

logger = logging.getLogger(__name__)

# Router mounted under the "/api/billing" prefix in main.py
billing_router = APIRouter(tags=["billing"])


@billing_router.get("/auth/test", dependencies=[Depends(get_current_user)])
async def test_billing_auth():
    """Temporary endpoint validating billing service principal token acquisition.

    Removed in Phase 9 after end-to-end verification.
    """
    token = get_billing_token()
    return {"status": "ok", "token_prefix": token[:10]}
