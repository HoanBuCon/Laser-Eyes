"""Statistics and Analytics API Endpoints."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, Query

from api.dependencies import get_stats_repo
from api.schemas import RoomRankingItem, StatisticsSummaryResponse
from storage.repositories import StatisticsRepository

router = APIRouter(prefix="/statistics", tags=["Statistics & Analytics"])


@router.get("/summary", response_model=StatisticsSummaryResponse)
def get_summary_statistics(
    site_id: Optional[str] = Query(None, description="Optional filter by site"),
    room_id: Optional[str] = Query(None, description="Optional filter by room"),
    repo: StatisticsRepository = Depends(get_stats_repo),
):
    """Retrieve macro violation counts, class distribution, and room activity metrics."""
    return repo.get_overall_summary(site_id=site_id, room_id=room_id)


@router.get("/rankings", response_model=List[RoomRankingItem])
def get_classroom_risk_rankings(
    site_id: Optional[str] = Query(None, description="Optional filter by site"),
    repo: StatisticsRepository = Depends(get_stats_repo),
):
    """Get classrooms ranked from highest to lowest risk score for auditor prioritization."""
    return repo.get_room_rankings(site_id=site_id)
