"""
Organisations routes with plan enforcement.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime

from deps.plan_limits import enforce_org_limit

router = APIRouter(prefix="/organisations", tags=["Organisations"])


class CreateOrgRequest(BaseModel):
    name: str
    slug: Optional[str] = None


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    created_at: str
    updated_at: str


async def get_db(request: Request):
    """Get database connection from app state."""
    return request.app.state.db


async def get_current_user(request: Request):
    """Get current user from auth token."""
    # This should be replaced with actual auth logic
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # TODO: Validate JWT and return user info
        return {"id": "test-user", "email": "test@example.com"}
    raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("", response_model=OrgResponse)
async def create_organisation(
    data: CreateOrgRequest,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Create a new organisation.
    Enforces: free tier max 1 org per user.
    """
    profile_id = user["id"]
    
    # Enforce plan limits
    await enforce_org_limit(db, profile_id)
    
    # Generate slug if not provided
    slug = data.slug or data.name.lower().replace(" ", "-").replace("_", "-")
    
    # Check slug uniqueness
    existing = await db.fetch_one(
        "SELECT id FROM organisations WHERE slug = :slug",
        {"slug": slug}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Organisation slug already exists")
    
    # Create organisation
    org_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    await db.execute(
        """
        INSERT INTO organisations (id, name, slug, owner_id, created_at, updated_at)
        VALUES (:id, :name, :slug, :owner_id, :created_at, :updated_at)
        """,
        {
            "id": org_id,
            "name": data.name,
            "slug": slug,
            "owner_id": profile_id,
            "created_at": now,
            "updated_at": now,
        }
    )
    
    return OrgResponse(
        id=org_id,
        name=data.name,
        slug=slug,
        owner_id=profile_id,
        created_at=now,
        updated_at=now,
    )


@router.get("", response_model=List[OrgResponse])
async def list_organisations(
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """List all organisations for the current user."""
    profile_id = user["id"]
    
    rows = await db.fetch_all(
        """
        SELECT id, name, slug, owner_id, created_at, updated_at
        FROM organisations
        WHERE owner_id = :owner_id
        ORDER BY created_at DESC
        """,
        {"owner_id": profile_id}
    )
    
    return [
        OrgResponse(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            owner_id=row["owner_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


@router.delete("/{org_id}")
async def delete_organisation(
    org_id: str,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Delete an organisation and all its projects (cascade).
    """
    profile_id = user["id"]
    
    # Verify ownership
    org = await db.fetch_one(
        "SELECT id, owner_id FROM organisations WHERE id = :id",
        {"id": org_id}
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    
    if org["owner_id"] != profile_id:
        raise HTTPException(status_code=403, detail="Not authorised to delete this organisation")
    
    # Delete all projects first (cascade in DB should handle this, but being explicit)
    await db.execute(
        "DELETE FROM projects WHERE org_id = :org_id",
        {"org_id": org_id}
    )
    
    # Delete organisation
    await db.execute(
        "DELETE FROM organisations WHERE id = :id",
        {"id": org_id}
    )
    
    return {"message": "Organisation deleted successfully"}
