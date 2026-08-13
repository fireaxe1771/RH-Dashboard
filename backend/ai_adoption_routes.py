"""FastAPI routes for the AI Adoption report (/api/ai-adoption/*).

Provides department-level AI adoption metrics: which departments are using AI,
which aren't, and what percentage of total draft volume flows through AI-enabled
departments. Used by the frontend AiAdoptionDashboard component.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_ai_db
from ai_adoption_service import get_ai_adoption_report, AiAdoptionResponse

ai_adoption_router = APIRouter(tags=["AI Adoption"])


@ai_adoption_router.get(
    "/departments",
    response_model=AiAdoptionResponse,
    dependencies=[Depends(get_current_user)],
)
async def list_ai_adoption_departments(
    start_date: str = Query(..., description="ISO start date (YYYY-MM-DD)."),
    end_date: str = Query(..., description="ISO end date (YYYY-MM-DD)."),
    limit: int = Query(50, ge=1, le=500, description="Maximum departments to return."),
    ai_status: str = Query("all", description="Filter by AI status: all, using_ai, not_using_ai, unknown."),
    ai_db=Depends(get_ai_db),
):
    """Returns the top departments by draft activity with their current AI adoption status."""
    if ai_status not in {"all", "using_ai", "not_using_ai", "unknown"}:
        raise HTTPException(status_code=400, detail="ai_status must be one of: all, using_ai, not_using_ai, unknown")
    return await get_ai_adoption_report(ai_db, start_date, end_date, limit, ai_status)
