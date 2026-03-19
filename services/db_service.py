"""
Database Service (Supabase)
Handles all database operations for the Pipeline AI system.
"""
import os
import logging
from datetime import datetime
from supabase import create_client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_URL and SUPABASE_SERVICE_KEY else None


async def get_user_by_github_username(github_username: str) -> dict | None:
    """Find user by GitHub username."""
    if not supabase:
        logger.error("Supabase not configured")
        return None
    
    try:
        res = supabase.table("profiles")\
            .select("*")\
            .eq("github_username", github_username)\
            .execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error fetching user: {e}")
        return None


async def create_project(owner_id: str, repo_url: str, repo_id: int, name: str) -> dict:
    """Create a new project in the database."""
    if not supabase:
        raise ValueError("Supabase not configured")
    
    try:
        res = supabase.table("projects").insert({
            "owner_id": owner_id,
            "repo_url": repo_url,
            "github_repo_id": repo_id,
            "name": name,
            "status": "pending",
            "provider": "github",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
        return res.data[0]
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        raise


async def update_project_status(project_id: str, status: str):
    """Update project status."""
    if not supabase:
        logger.error("Supabase not configured")
        return
    
    try:
        supabase.table("projects").update({
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"Error updating project status: {e}")


async def save_analysis_result(project_id: str, scan_result: dict, analysis_results: list):
    """Save analysis results to the project."""
    if not supabase:
        logger.error("Supabase not configured")
        return
    
    try:
        supabase.table("projects").update({
            "analysis_result": {
                "scan": scan_result,
                "analysis": analysis_results,
                "analyzed_at": datetime.utcnow().isoformat(),
            },
            "is_monorepo": scan_result.get("is_monorepo", False),
            "detected_services_count": len(scan_result.get("services", [])),
            "status": "analyzed",
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", project_id).execute()
        
        # Also save to ai_analyses table for history
        supabase.table("ai_analyses").insert({
            "project_id": project_id,
            "analysis_type": "repo_scan",
            "input_data": {"repo_full_name": scan_result.get("repo_full_name")},
            "result_data": scan_result,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
        
    except Exception as e:
        logger.error(f"Error saving analysis: {e}")


async def save_deployment(project_id: str, service_name: str, platform: str, result: dict):
    """Save deployment record."""
    if not supabase:
        logger.error("Supabase not configured")
        return
    
    try:
        supabase.table("deployments").insert({
            "project_id": project_id,
            "name": service_name,
            "platform": platform,
            "status": "success" if result.get("status") == "success" else "failed",
            "platform_deployment_id": result.get("deployment_id") or result.get("service_id"),
            "platform_deployment_url": result.get("url"),
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"Error saving deployment: {e}")


async def update_deployment_status(deployment_id: str, status: str, url: str = None):
    """Update deployment status."""
    if not supabase:
        logger.error("Supabase not configured")
        return
    
    try:
        update = {"status": status}
        if url:
            update["platform_deployment_url"] = url
        
        supabase.table("deployments").update(update).eq("id", deployment_id).execute()
    except Exception as e:
        logger.error(f"Error updating deployment: {e}")


async def get_user_api_keys(user_id: str) -> dict:
    """Get user's API keys for deployment platforms."""
    # For now, use environment variables
    # In production, fetch encrypted keys from DB
    return {
        "vercel_token": os.getenv("VERCEL_TOKEN"),
        "render_token": os.getenv("RENDER_TOKEN"),
    }


async def emit_progress(project_id: str, stage: str, message: str):
    """Save progress to DB — frontend polls this."""
    logger.info(f"[{stage}] {message}")
    
    if not supabase:
        return
    
    try:
        supabase.table("ai_analyses").insert({
            "project_id": project_id,
            "analysis_type": "deployment_progress",
            "input_data": {"stage": stage},
            "result_data": {"message": message, "stage": stage},
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"Error emitting progress: {e}")


async def get_project_progress(project_id: str) -> list:
    """Get all progress steps for a project."""
    if not supabase:
        return []
    
    try:
        res = supabase.table("ai_analyses")\
            .select("*")\
            .eq("project_id", project_id)\
            .eq("analysis_type", "deployment_progress")\
            .order("created_at", desc=False)\
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        return []


async def get_project(project_id: str) -> dict | None:
    """Get project by ID."""
    if not supabase:
        return None
    
    try:
        res = supabase.table("projects")\
            .select("*")\
            .eq("id", project_id)\
            .execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error getting project: {e}")
        return None
