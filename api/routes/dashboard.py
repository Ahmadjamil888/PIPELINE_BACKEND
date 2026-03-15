import logging
from typing import List, Dict, Any
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Depends

from schemas import (
    DeploymentStatus,
    ErrorResponse,
    Sandbox
)
from agents.ai_client import OpenRouterClient
from api.routes.deployments import _deployments
from api.routes.repos import _repos
from api.routes.sandboxes import _sandboxes
from sandbox.daytona_manager import DaytonaManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get(
    "/stats",
    response_model=Dict[str, Any]
)
async def get_dashboard_stats() -> Dict[str, Any]:
    """
    Get dashboard statistics including:
    - Total repositories connected
    - Total deployments (by status)
    - Active sandboxes
    - AI-powered insights
    """
    # Count deployments by status
    deployments_by_status = {}
    for dep in _deployments.values():
        status = dep.status.value if hasattr(dep.status, 'value') else dep.status
        deployments_by_status[status] = deployments_by_status.get(status, 0) + 1
    
    # Get active sandboxes
    active_sandboxes = len([s for s in _sandboxes.values() if s.status.value == "running"])
    
    # Calculate AI insights
    total_services_deployed = sum(
        len([s for s in dep.services if s.status.value == "deployed"])
        for dep in _deployments.values()
    )
    
    return {
        "repositories": {
            "total": len(_repos),
            "connected": len([r for r in _repos.values() if r.status == "connected"]),
            "analyzing": len([r for r in _repos.values() if r.status == "analyzing"])
        },
        "deployments": {
            "total": len(_deployments),
            "by_status": deployments_by_status,
            "running": deployments_by_status.get("running", 0),
            "succeeded": deployments_by_status.get("succeeded", 0),
            "failed": deployments_by_status.get("failed", 0)
        },
        "sandboxes": {
            "total": len(_sandboxes),
            "active": active_sandboxes
        },
        "ai_insights": {
            "total_services_deployed": total_services_deployed,
            "services_by_platform": await _get_services_by_platform(),
            "monorepos_analyzed": len([r for r in _repos.values() if getattr(r, 'is_monorepo', False)])
        },
        "timestamp": datetime.now().isoformat()
    }


async def _get_services_by_platform() -> Dict[str, int]:
    """Count services by deployment platform."""
    platform_counts = {}
    
    for dep in _deployments.values():
        for svc in dep.services:
            platform = svc.platform.value if hasattr(svc.platform, 'value') else str(svc.platform)
            platform_counts[platform] = platform_counts.get(platform, 0) + 1
    
    return platform_counts


@router.get(
    "/deployments",
    response_model=List[Dict[str, Any]]
)
async def get_all_deployments(
    status: str = Query(None),
    repo_id: str = Query(None),
    limit: int = Query(50)
) -> List[Dict[str, Any]]:
    """
    Get all deployments with detailed information for dashboard.
    Includes environment variables and service URLs.
    """
    result = []
    
    for dep in list(_deployments.values())[-limit:]:
        if status and dep.status != status:
            continue
        if repo_id and str(dep.repo_id) != repo_id:
            continue
        
        # Get repo info
        repo = _repos.get(str(dep.repo_id))
        
        # Build service info
        services_info = []
        for svc in dep.services:
            services_info.append({
                "name": svc.name,
                "path": svc.path,
                "platform": svc.platform.value if hasattr(svc.platform, 'value') else str(svc.platform),
                "status": svc.status.value if hasattr(svc.status, 'value') else str(svc.status),
                "deployment_url": svc.deployment_url,
                "build_logs_url": svc.build_logs_url,
                "env_variables": svc.env_variables if hasattr(svc, 'env_variables') else []
            })
        
        result.append({
            "id": str(dep.id),
            "repo_id": str(dep.repo_id),
            "repo_name": repo.name if repo else "Unknown",
            "repo_url": str(repo.repo_url) if repo else None,
            "status": dep.status.value if hasattr(dep.status, 'value') else dep.status,
            "environment": dep.environment.value if hasattr(dep.environment, 'value') else dep.environment,
            "branch": dep.branch,
            "services": services_info,
            "sandbox_id": str(dep.sandbox_id) if dep.sandbox_id else None,
            "started_at": dep.started_at.isoformat() if dep.started_at else None,
            "completed_at": dep.completed_at.isoformat() if dep.completed_at else None,
            "duration_seconds": dep.duration_seconds,
            "retry_count": dep.retry_count,
            "error_message": dep.error_message
        })
    
    # Sort by started_at descending
    result.sort(key=lambda x: x["started_at"] or "", reverse=True)
    
    return result


@router.get(
    "/deployments/{deployment_id}/env",
    response_model=Dict[str, Any]
)
async def get_deployment_env_vars(deployment_id: str) -> Dict[str, Any]:
    """
    Get all environment variables for a deployment.
    Includes suggested variables from AI analysis and deployed values.
    """
    dep = _deployments.get(deployment_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    # Get repo analysis for env var suggestions
    repo = _repos.get(str(dep.repo_id))
    
    env_vars_by_service = {}
    
    for svc in dep.services:
        service_env = {
            "detected": svc.env_variables if hasattr(svc, 'env_variables') else [],
            "deployed": {},  # Would be fetched from platform APIs
            "suggested": []
        }
        
        env_vars_by_service[svc.name] = service_env
    
    return {
        "deployment_id": deployment_id,
        "environment": dep.environment.value if hasattr(dep.environment, 'value') else dep.environment,
        "services": env_vars_by_service,
        "global_env_vars": {}  # Shared across services
    }


@router.get(
    "/projects",
    response_model=List[Dict[str, Any]]
)
async def get_all_projects() -> List[Dict[str, Any]]:
    """
    Get all projects with their deployments and access information.
    """
    projects = []
    
    for repo in _repos.values():
        # Find deployments for this repo
        repo_deployments = [
            d for d in _deployments.values() 
            if str(d.repo_id) == str(repo.id)
        ]
        
        # Get latest deployment
        latest_deployment = None
        if repo_deployments:
            latest = max(repo_deployments, key=lambda d: d.created_at or datetime.min)
            latest_deployment = {
                "id": str(latest.id),
                "status": latest.status.value if hasattr(latest.status, 'value') else latest.status,
                "environment": latest.environment.value if hasattr(latest.environment, 'value') else latest.environment,
                "services_count": len(latest.services),
                "deployed_urls": [
                    s.deployment_url for s in latest.services 
                    if s.deployment_url
                ]
            }
        
        projects.append({
            "id": str(repo.id),
            "name": repo.name,
            "repo_url": str(repo.repo_url),
            "provider": repo.provider.value if hasattr(repo.provider, 'value') else repo.provider,
            "branch": repo.branch,
            "status": repo.status,
            "created_at": repo.created_at.isoformat() if repo.created_at else None,
            "deployments_count": len(repo_deployments),
            "latest_deployment": latest_deployment,
            "has_analysis": str(repo.id) in _repos  # Simplified check
        })
    
    return projects


@router.get(
    "/projects/{repo_id}/details",
    response_model=Dict[str, Any]
)
async def get_project_details(repo_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific project including:
    - All deployments
    - Environment variables
    - Sandbox access
    - Service URLs
    """
    repo = _repos.get(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get all deployments for this repo
    deployments = [
        {
            "id": str(d.id),
            "status": d.status.value if hasattr(d.status, 'value') else d.status,
            "environment": d.environment.value if hasattr(d.environment, 'value') else d.environment,
            "branch": d.branch,
            "services": [
                {
                    "name": s.name,
                    "platform": s.platform.value if hasattr(s.platform, 'value') else str(s.platform),
                    "status": s.status.value if hasattr(s.status, 'value') else str(s.status),
                    "url": s.deployment_url,
                    "env_vars": s.env_variables if hasattr(s, 'env_variables') else []
                }
                for s in d.services
            ],
            "started_at": d.started_at.isoformat() if d.started_at else None,
            "completed_at": d.completed_at.isoformat() if d.completed_at else None,
            "sandbox_id": str(d.sandbox_id) if d.sandbox_id else None
        }
        for d in _deployments.values()
        if str(d.repo_id) == repo_id
    ]
    
    # Sort by date
    deployments.sort(key=lambda x: x["started_at"] or "", reverse=True)
    
    # Get active sandboxes
    active_sandboxes = [
        {
            "id": str(s.id),
            "status": s.status.value if hasattr(s.status, 'value') else str(s.status),
            "workspace_url": s.workspace_url
        }
        for s in _sandboxes.values()
        if s.repo_id == repo_id
    ]
    
    return {
        "project": {
            "id": str(repo.id),
            "name": repo.name,
            "repo_url": str(repo.repo_url),
            "provider": repo.provider.value if hasattr(repo.provider, 'value') else repo.provider,
            "branch": repo.branch,
            "status": repo.status,
            "created_at": repo.created_at.isoformat() if repo.created_at else None
        },
        "deployments": deployments,
        "active_sandboxes": active_sandboxes,
        "total_deployments": len(deployments),
        "successful_deployments": len([d for d in deployments if d["status"] == "succeeded"])
    }


@router.get(
    "/ai/suggest-env",
    response_model=Dict[str, Any]
)
async def ai_suggest_env_vars(
    repo_id: str = Query(...),
    service_name: str = Query(...)
) -> Dict[str, Any]:
    """
    Use AI to suggest environment variables for a service.
    """
    ai = OpenRouterClient()
    
    # This would analyze the code and suggest env vars
    # For now, return a placeholder response
    
    return {
        "repo_id": repo_id,
        "service_name": service_name,
        "suggested_vars": [
            {"name": "DATABASE_URL", "description": "Database connection string", "required": True},
            {"name": "API_KEY", "description": "API authentication key", "required": True},
            {"name": "NODE_ENV", "description": "Environment mode", "required": False, "default": "production"}
        ],
        "generated_by": "ai",
        "timestamp": datetime.now().isoformat()
    }
