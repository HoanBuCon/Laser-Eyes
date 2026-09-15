"""Exam Sites API Endpoints."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_site_repo
from api.schemas import SiteCreate, SiteResponse
from storage.repositories import SiteRepository

router = APIRouter(prefix="/sites", tags=["Exam Sites"])


@router.post("/", response_model=SiteResponse, status_code=status.HTTP_201_CREATED)
def create_site(payload: SiteCreate, repo: SiteRepository = Depends(get_site_repo)):
    """Register a new exam site / campus."""
    return repo.create(
        name=payload.name,
        address=payload.address,
        contact_info=payload.contact_info,
    )


@router.get("/", response_model=List[SiteResponse])
def list_sites(repo: SiteRepository = Depends(get_site_repo)):
    """List all registered exam sites."""
    return repo.list_all()


@router.get("/{site_id}", response_model=SiteResponse)
def get_site(site_id: str, repo: SiteRepository = Depends(get_site_repo)):
    """Retrieve details of an exam site."""
    site = repo.get_by_id(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Exam site not found")
    return site
