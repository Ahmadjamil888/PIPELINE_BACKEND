"""
Projects API - Progress Polling
Frontend polls this endpoint to show live deployment progress.
"""
from fastapi import APIRouter, HTTPException
from services.db_service import get_project_progress, get_project

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("/{project_id}/progress")
async def get_progress(project_id: str):
    """
    Frontend polls this to show live progress.
    Returns project info and all progress steps.
    """
    # Get project details
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get progress steps
    progress_steps = await get_project_progress(project_id)
    
    return {
        "project": project,
        "progress_steps": progress_steps,
    }


@router.get("/{project_id}")
async def get_project_details(project_id: str):
    """Get full project details including analysis and deployments."""
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return {"project": project}
