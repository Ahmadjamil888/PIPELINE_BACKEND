"""
Projects routes with plan enforcement.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime

from deps.plan_limits import enforce_project_limit

router = APIRouter(prefix="/projects", tags=["Projects"])


class CreateProjectRequest(BaseModel):
    repo_full_name: str
    name: str
    default_branch: str = "main"


class ProjectResponse(BaseModel):
    id: str
    org_id: str
    name: str
    repo_full_name: str
    default_branch: str
    status: str  # analyzing, analyzed, deploying, deployed, error
    created_at: str
    updated_at: str


async def get_db(request: Request):
    return request.app.state.db


async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return {"id": "test-user", "email": "test@example.com"}
    raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/organisations/{org_id}/projects", response_model=ProjectResponse)
async def create_project(
    org_id: str,
    data: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Create a new project for an organisation.
    Enforces: free tier max 3 projects per org.
    Triggers AI analysis job on success.
    """
    profile_id = user["id"]
    
    # Verify org ownership
    org = await db.fetch_one(
        "SELECT id, owner_id FROM organisations WHERE id = :id",
        {"id": org_id}
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    
    if org["owner_id"] != profile_id:
        raise HTTPException(status_code=403, detail="Not authorised to create projects in this organisation")
    
    # Enforce plan limits
    await enforce_project_limit(db, org_id)
    
    # Create project
    project_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    await db.execute(
        """
        INSERT INTO projects (id, org_id, name, repo_full_name, default_branch, status, created_at, updated_at)
        VALUES (:id, :org_id, :name, :repo_full_name, :default_branch, :status, :created_at, :updated_at)
        """,
        {
            "id": project_id,
            "org_id": org_id,
            "name": data.name,
            "repo_full_name": data.repo_full_name,
            "default_branch": data.default_branch,
            "status": "analyzing",
            "created_at": now,
            "updated_at": now,
        }
    )
    
    # Trigger AI analysis in background
    background_tasks.add_task(run_ai_analysis, project_id, data.repo_full_name, db)
    
    return ProjectResponse(
        id=project_id,
        org_id=org_id,
        name=data.name,
        repo_full_name=data.repo_full_name,
        default_branch=data.default_branch,
        status="analyzing",
        created_at=now,
        updated_at=now,
    )


@router.get("/organisations/{org_id}/projects", response_model=List[ProjectResponse])
async def list_projects(
    org_id: str,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """List all projects for an organisation."""
    profile_id = user["id"]
    
    # Verify org ownership
    org = await db.fetch_one(
        "SELECT owner_id FROM organisations WHERE id = :id",
        {"id": org_id}
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    
    if org["owner_id"] != profile_id:
        raise HTTPException(status_code=403, detail="Not authorised to view these projects")
    
    rows = await db.fetch_all(
        """
        SELECT id, org_id, name, repo_full_name, default_branch, status, created_at, updated_at
        FROM projects
        WHERE org_id = :org_id
        ORDER BY created_at DESC
        """,
        {"org_id": org_id}
    )
    
    return [
        ProjectResponse(
            id=row["id"],
            org_id=row["org_id"],
            name=row["name"],
            repo_full_name=row["repo_full_name"],
            default_branch=row["default_branch"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get a single project by ID."""
    profile_id = user["id"]
    
    row = await db.fetch_one(
        """
        SELECT p.id, p.org_id, p.name, p.repo_full_name, p.default_branch, p.status, p.created_at, p.updated_at
        FROM projects p
        JOIN organisations o ON p.org_id = o.id
        WHERE p.id = :project_id AND o.owner_id = :owner_id
        """,
        {"project_id": project_id, "owner_id": profile_id}
    )
    
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ProjectResponse(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        repo_full_name=row["repo_full_name"],
        default_branch=row["default_branch"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Delete a project and update org project count."""
    profile_id = user["id"]
    
    # Verify ownership and get project
    project = await db.fetch_one(
        """
        SELECT p.id, p.org_id
        FROM projects p
        JOIN organisations o ON p.org_id = o.id
        WHERE p.id = :project_id AND o.owner_id = :owner_id
        """,
        {"project_id": project_id, "owner_id": profile_id}
    )
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Delete project (services will cascade)
    await db.execute(
        "DELETE FROM projects WHERE id = :id",
        {"id": project_id}
    )
    
    return {"message": "Project deleted successfully"}


async def run_ai_analysis(project_id: str, repo_full_name: str, db):
    """Background task to run AI analysis on a project."""
    # TODO: Implement AI analysis
    # 1. Create Daytona sandbox
    # 2. Clone repo
    # 3. Run AI detection
    # 4. Create services
    # 5. Update project status
    pass
