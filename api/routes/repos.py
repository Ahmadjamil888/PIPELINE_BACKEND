import logging
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends

from schemas import (
    RepoConnection,
    RepoConnectionRequest,
    RepoAnalysis,
    ErrorResponse,
    RepoStatus
)
from agents.repo_analyzer import RepoAnalyzer
from sandbox.daytona_manager import DaytonaManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/repos", tags=["Repositories"])

# In-memory store (replace with database in production)
_repos: dict = {}
_analyses: dict = {}


def get_daytona_manager():
    return DaytonaManager()


def get_repo_analyzer(daytona: DaytonaManager = Depends(get_daytona_manager)):
    return RepoAnalyzer(daytona)


@router.post(
    "/connect",
    response_model=RepoConnection,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse}
    }
)
async def connect_repo(
    request: RepoConnectionRequest
) -> RepoConnection:
    """
    Connect a Git repository from GitHub or GitLab.
    
    This endpoint validates the repository URL and creates a connection record.
    The repository is not cloned until analysis is performed.
    """
    logger.info(f"Connecting repository: {request.repo_url}")
    
    # Validate repository URL
    try:
        from urllib.parse import urlparse
        parsed = urlparse(str(request.repo_url))
        if parsed.scheme not in ("https", "http"):
            raise HTTPException(
                status_code=400,
                detail="Invalid repository URL scheme. Use https or http."
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid repository URL: {e}")
    
    # Create connection
    try:
        repo_id = UUID(int=int(datetime.now().timestamp() * 1000))
        
        connection = RepoConnection(
            id=repo_id,
            repo_url=request.repo_url,
            provider=request.provider,
            branch=request.branch,
            name=request.name or str(request.repo_url).split("/")[-1].replace(".git", ""),
            status=RepoStatus.CONNECTED,
            created_at=datetime.now()
        )
        
        _repos[str(repo_id)] = connection
        
        logger.info(f"Repository connected: {repo_id}")
        return connection
    except Exception as e:
        logger.error(f"Failed to create repository connection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create connection: {str(e)}")


@router.get(
    "/{repo_id}",
    response_model=RepoConnection,
    responses={404: {"model": ErrorResponse}}
)
async def get_repo(repo_id: UUID) -> RepoConnection:
    """Get repository details by ID."""
    repo = _repos.get(str(repo_id))
    
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    return repo


@router.get(
    "",
    response_model=List[RepoConnection]
)
async def list_repos(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100)
) -> List[RepoConnection]:
    """List all connected repositories."""
    repos = list(_repos.values())
    
    if status:
        repos = [r for r in repos if r.status == status]
    
    return repos[:limit]


@router.post(
    "/{repo_id}/analyze",
    response_model=RepoAnalysis,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def analyze_repo(
    repo_id: UUID,
    background_tasks: BackgroundTasks,
    analyzer: RepoAnalyzer = Depends(get_repo_analyzer)
) -> RepoAnalysis:
    """
    Analyze a repository to detect services and frameworks.
    
    This endpoint:
    1. Creates a Daytona workspace sandbox
    2. Clones the repository
    3. Runs AI analysis to detect services, frameworks, and deployment configuration
    
    Returns a detailed analysis with detected services and recommendations.
    """
    logger.info(f"Starting analysis for repo: {repo_id}")
    
    repo = _repos.get(str(repo_id))
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    # Update status
    repo.status = RepoStatus.ANALYZING
    
    try:
        # Run analysis
        analysis = await analyzer.analyze_repository(
            repo_url=str(repo.repo_url),
            branch=repo.branch
        )
        
        # Update repo with analysis reference
        repo.status = RepoStatus.CONNECTED
        repo.updated_at = datetime.now()
        
        # Store analysis
        _analyses[str(repo_id)] = analysis
        
        logger.info(f"Analysis complete for repo: {repo_id}")
        return analysis
        
    except Exception as e:
        repo.status = RepoStatus.ERROR
        logger.error(f"Analysis failed for repo {repo_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(e)}"
        )


@router.get(
    "/{repo_id}/analysis",
    response_model=RepoAnalysis,
    responses={404: {"model": ErrorResponse}}
)
async def get_repo_analysis(repo_id: UUID) -> RepoAnalysis:
    """Get the analysis results for a repository."""
    analysis = _analyses.get(str(repo_id))
    
    if not analysis:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found. Run POST /repos/{repo_id}/analyze first."
        )
    
    return analysis


@router.delete(
    "/{repo_id}",
    status_code=204
)
async def delete_repo(repo_id: UUID):
    """Delete a repository connection."""
    if str(repo_id) not in _repos:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    del _repos[str(repo_id)]
    if str(repo_id) in _analyses:
        del _analyses[str(repo_id)]
    
    logger.info(f"Repository deleted: {repo_id}")
