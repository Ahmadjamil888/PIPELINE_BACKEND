import logging
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends

from schemas import (
    DeploymentPlan,
    DeploymentCreateRequest,
    DeploymentStatus,
    DeploymentExecutionResponse,
    DeploymentLogs,
    DeploymentList,
    ErrorResponse,
    ServiceDeployment
)
from agents.deployment_planner import DeploymentPlanner
from agents.deployment_runner import DeploymentRunner
from sandbox.daytona_manager import DaytonaManager
from deployers.vercel import VercelDeployer
from deployers.render import RenderDeployer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/deployments", tags=["Deployments"])

# In-memory store (replace with database in production)
_deployments: dict = {}
_deployment_logs: dict = {}


def get_deployment_runner():
    daytona = DaytonaManager()
    vercel = VercelDeployer()
    render = RenderDeployer()
    return DeploymentRunner(daytona, vercel, render)


def get_deployment_planner():
    return DeploymentPlanner()


@router.get(
    "",
    response_model=DeploymentList
)
async def list_deployments(
    status: Optional[str] = Query(None),
    repo_id: Optional[UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
) -> DeploymentList:
    """List all deployments with optional filtering."""
    deployments = list(_deployments.values())
    
    if status:
        deployments = [d for d in deployments if d.status == status]
    if repo_id:
        deployments = [d for d in deployments if d.repo_id == repo_id]
    
    total = len(deployments)
    deployments = deployments[offset:offset + limit]
    
    return DeploymentList(
        deployments=deployments,
        total=total,
        limit=limit,
        offset=offset
    )


@router.post(
    "/",
    response_model=DeploymentPlan,
    status_code=201,
    responses={400: {"model": ErrorResponse}}
)
async def create_deployment(
    request: DeploymentCreateRequest,
    planner: DeploymentPlanner = Depends(get_deployment_planner)
) -> DeploymentPlan:
    """
    Create a new deployment plan based on repository analysis.
    
    This endpoint generates a deployment plan with service configurations,
    build commands, and deployment platform assignments.
    """
    logger.info(f"Creating deployment plan for repo: {request.repo_id}")
    
    # Generate plan
    plan = await planner.create_deployment_plan(
        request=request,
        repo_analysis=None  # Could be fetched from storage
    )
    
    # Validate plan
    issues = planner.validate_plan(plan)
    if issues:
        raise HTTPException(
            status_code=400,
            detail=f"Deployment plan validation failed: {', '.join(issues)}"
        )
    
    # Generate proper UUID
    plan.id = uuid4()
    
    # Store plan
    _deployments[str(plan.id)] = plan
    
    logger.info(f"Deployment plan created: {plan.id}")
    return plan


@router.get(
    "/{deployment_id}",
    response_model=DeploymentStatus,
    responses={404: {"model": ErrorResponse}}
)
async def get_deployment(deployment_id: UUID) -> DeploymentStatus:
    """Get deployment status and details."""
    deployment = _deployments.get(str(deployment_id))
    
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    return deployment


@router.post(
    "/{deployment_id}/run",
    response_model=DeploymentExecutionResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    }
)
async def run_deployment(
    deployment_id: UUID,
    background_tasks: BackgroundTasks,
    runner: DeploymentRunner = Depends(get_deployment_runner)
) -> DeploymentExecutionResponse:
    """
    Execute a deployment.
    
    This endpoint:
    1. Creates/starts a Daytona sandbox
    2. Runs build commands
    3. Deploys services to Vercel/Render
    4. Tracks deployment status
    
    The deployment runs asynchronously in the background.
    """
    logger.info(f"Executing deployment: {deployment_id}")
    
    deployment = _deployments.get(str(deployment_id))
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    # Check if deployment can be run
    if deployment.status not in ["pending", "planned", "approved", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Deployment cannot be run in status: {deployment.status}"
        )
    
    # Get repo info (would come from stored connection in production)
    from api.routes.repos import _repos
    repo = _repos.get(str(deployment.repo_id))
    if not repo:
        raise HTTPException(
            status_code=400,
            detail="Associated repository not found"
        )
    
    # Start deployment in background
    deployment.status = "running"
    deployment.started_at = datetime.now()
    
    # Add background task
    background_tasks.add_task(
        _execute_deployment,
        deployment_id,
        deployment,
        str(repo.repo_url),
        repo.branch or "main",
        runner
    )
    
    # Estimate duration
    estimated_seconds = sum(
        s.estimated_duration_seconds or 120
        for s in deployment.services
    ) if hasattr(deployment, 'services') and deployment.services else 300
    
    return DeploymentExecutionResponse(
        deployment_id=deployment_id,
        status="running",
        message="Deployment execution started",
        estimated_duration_seconds=estimated_seconds
    )


async def _execute_deployment(
    deployment_id: UUID,
    deployment,
    repo_url: str,
    branch: str,
    runner: DeploymentRunner
):
    """Background task to execute deployment."""
    try:
        result = await runner.run_deployment(
            deployment_id=deployment_id,
            plan=deployment,
            repo_url=repo_url,
            branch=branch
        )
        
        # Update stored deployment
        _deployments[str(deployment_id)] = result
        
        logger.info(f"Deployment {deployment_id} completed with status: {result.status}")
        
    except Exception as e:
        logger.error(f"Deployment execution failed: {e}")
        deployment.status = "failed"
        deployment.error_message = str(e)
        deployment.completed_at = datetime.now()


@router.post(
    "/{deployment_id}/cancel",
    response_model=DeploymentStatus
)
async def cancel_deployment(deployment_id: UUID) -> DeploymentStatus:
    """Cancel a running deployment."""
    deployment = _deployments.get(str(deployment_id))
    
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    if deployment.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel deployment in status: {deployment.status}"
        )
    
    deployment.status = "cancelled"
    deployment.completed_at = datetime.now()
    
    # Cleanup sandbox
    if deployment.sandbox_id:
        daytona = DaytonaManager()
        try:
            await daytona.destroy_workspace(str(deployment.sandbox_id))
        except Exception as e:
            logger.warning(f"Failed to cleanup sandbox on cancel: {e}")
    
    logger.info(f"Deployment cancelled: {deployment_id}")
    return deployment


@router.post(
    "/{deployment_id}/retry",
    response_model=DeploymentExecutionResponse
)
async def retry_deployment(
    deployment_id: UUID,
    background_tasks: BackgroundTasks,
    runner: DeploymentRunner = Depends(get_deployment_runner)
) -> DeploymentExecutionResponse:
    """Retry a failed deployment."""
    logger.info(f"Retrying deployment: {deployment_id}")
    
    deployment = _deployments.get(str(deployment_id))
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    if deployment.status not in ["failed", "cancelled"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry deployment in status: {deployment.status}"
        )
    
    # Get repo info
    from api.routes.repos import _repos
    repo = _repos.get(str(deployment.repo_id))
    if not repo:
        raise HTTPException(status_code=400, detail="Associated repository not found")
    
    retry_count = (deployment.retry_count or 0) + 1
    
    # Start retry in background
    deployment.status = "retrying"
    deployment.started_at = datetime.now()
    
    background_tasks.add_task(
        _execute_retry,
        deployment_id,
        deployment,
        str(repo.repo_url),
        repo.branch or "main",
        retry_count,
        runner
    )
    
    return DeploymentExecutionResponse(
        deployment_id=deployment_id,
        status="retrying",
        message=f"Deployment retry started (attempt {retry_count})",
        estimated_duration_seconds=300
    )


async def _execute_retry(
    deployment_id: UUID,
    deployment,
    repo_url: str,
    branch: str,
    retry_count: int,
    runner: DeploymentRunner
):
    """Background task to retry deployment."""
    try:
        result = await runner.retry_deployment(
            deployment_id=deployment_id,
            plan=deployment,
            repo_url=repo_url,
            branch=branch,
            retry_count=retry_count
        )
        
        _deployments[str(deployment_id)] = result
        
        logger.info(f"Deployment retry {deployment_id} completed: {result.status}")
        
    except Exception as e:
        logger.error(f"Deployment retry failed: {e}")
        deployment.status = "failed"
        deployment.error_message = str(e)
        deployment.completed_at = datetime.now()


@router.get(
    "/{deployment_id}/logs",
    response_model=DeploymentLogs,
    responses={404: {"model": ErrorResponse}}
)
async def get_deployment_logs(
    deployment_id: UUID,
    tail: int = Query(100, ge=1, le=1000),
    since: Optional[datetime] = Query(None)
) -> DeploymentLogs:
    """Get deployment logs from the sandbox."""
    deployment = _deployments.get(str(deployment_id))
    
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    # Get logs from runner
    runner = get_deployment_runner()
    
    logs = await runner.get_deployment_logs(
        deployment_id=deployment_id,
        sandbox_id=deployment.sandbox_id,
        tail=tail
    )
    
    # Filter by timestamp if specified
    if since:
        logs = [l for l in logs if l.timestamp >= since]
    
    return DeploymentLogs(
        deployment_id=deployment_id,
        logs=logs,
        timestamp=datetime.now()
    )
