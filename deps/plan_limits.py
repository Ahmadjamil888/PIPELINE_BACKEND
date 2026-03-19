"""
Plan enforcement logic for organisations and projects.
"""
from fastapi import HTTPException

PLAN_LIMITS = {
    "free":    {"orgs": 1, "projects": 3},
    "starter": {"orgs": 3, "projects": 10},
    "pro":     {"orgs": 10, "projects": 50},
    "team":    {"orgs": -1, "projects": -1},  # unlimited
}


async def get_user_plan(db, profile_id: str) -> str:
    """Get the user's current plan from subscriptions table."""
    result = await db.fetch_one(
        "SELECT plan FROM subscriptions WHERE profile_id = :profile_id AND status = 'active'",
        {"profile_id": profile_id}
    )
    return result["plan"] if result else "free"


async def enforce_org_limit(db, profile_id: str, plan: str = None):
    """Enforce organization creation limit based on plan."""
    if plan is None:
        plan = await get_user_plan(db, profile_id)
    
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    
    if limit["orgs"] == -1:
        return  # unlimited
    
    # Count existing orgs
    count = await db.fetch_one(
        "SELECT COUNT(*) as count FROM organisations WHERE owner_id = :owner_id",
        {"owner_id": profile_id}
    )
    org_count = count["count"] if count else 0
    
    if org_count >= limit["orgs"]:
        raise HTTPException(
            status_code=403, 
            detail=f"Your {plan} plan allows only {limit['orgs']} organisation(s). Please upgrade to create more."
        )


async def enforce_project_limit(db, org_id: str, plan: str = None):
    """Enforce project creation limit based on plan."""
    if plan is None:
        # Get plan from org's owner
        result = await db.fetch_one(
            """
            SELECT s.plan 
            FROM organisations o
            JOIN subscriptions s ON s.profile_id = o.owner_id
            WHERE o.id = :org_id AND s.status = 'active'
            """,
            {"org_id": org_id}
        )
        plan = result["plan"] if result else "free"
    
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    
    if limit["projects"] == -1:
        return  # unlimited
    
    # Count existing projects
    count = await db.fetch_one(
        "SELECT COUNT(*) as count FROM projects WHERE org_id = :org_id",
        {"org_id": org_id}
    )
    project_count = count["count"] if count else 0
    
    if project_count >= limit["projects"]:
        raise HTTPException(
            status_code=403,
            detail=f"Your {plan} plan allows only {limit['projects']} projects per organisation. Please upgrade to create more."
        )


async def can_create_org(db, profile_id: str) -> tuple[bool, str]:
    """Check if user can create an org, returns (can_create, reason)."""
    try:
        await enforce_org_limit(db, profile_id)
        return True, ""
    except HTTPException as e:
        return False, e.detail


async def can_create_project(db, org_id: str) -> tuple[bool, str]:
    """Check if org can create a project, returns (can_create, reason)."""
    try:
        await enforce_project_limit(db, org_id)
        return True, ""
    except HTTPException as e:
        return False, e.detail
