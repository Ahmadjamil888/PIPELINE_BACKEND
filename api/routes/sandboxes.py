import logging
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Depends

from schemas import (
    Sandbox,
    SandboxCreateRequest,
    SandboxList,
    CommandExecuteRequest,
    CommandExecuteResponse,
    TerminalSession,
    TerminalSessionRequest,
    SandboxLogs,
    ErrorResponse
)
from sandbox.daytona_manager import DaytonaManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sandboxes", tags=["Sandboxes"])

# In-memory store (replace with database in production)
_sandboxes: dict = {}


def get_daytona_manager():
    return DaytonaManager()


@router.get(
    "",
    response_model=SandboxList
)
async def list_sandboxes(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> SandboxList:
    """List all Daytona sandboxes."""
    from sandbox.daytona_manager import SandboxStatus
    
    status_filter = SandboxStatus(status) if status else None
    sandboxes = await daytona.list_sandboxes(status=status_filter, limit=limit)
    
    return SandboxList(
        sandboxes=sandboxes,
        total=len(sandboxes)
    )


@router.post(
    "/",
    response_model=Sandbox,
    status_code=201,
    responses={500: {"model": ErrorResponse}}
)
async def create_sandbox(
    request: SandboxCreateRequest,
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> Sandbox:
    """
    Create a new Daytona workspace sandbox.
    
    Optionally pre-clone a repository if repo_id or repo_url is provided.
    """
    logger.info("Creating sandbox")
    
    try:
        sandbox = await daytona.create_workspace(
            repo_url=request.repo_url,
            branch=request.branch,
            repo_id=request.repo_id,
            resources=request.resources,
            environment_variables=request.environment_variables
        )
        
        logger.info(f"Sandbox created: {sandbox.id}")
        return sandbox
        
    except Exception as e:
        logger.error(f"Failed to create sandbox: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create sandbox: {str(e)}"
        )


@router.get(
    "/{sandbox_id}",
    response_model=Sandbox,
    responses={404: {"model": ErrorResponse}}
)
async def get_sandbox(
    sandbox_id: UUID,
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> Sandbox:
    """Get sandbox details by ID."""
    try:
        sandbox = await daytona.get_sandbox(str(sandbox_id))
        return sandbox
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")


@router.delete(
    "/{sandbox_id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}}
)
async def destroy_sandbox(
    sandbox_id: UUID,
    daytona: DaytonaManager = Depends(get_daytona_manager)
):
    """Destroy a sandbox and free all resources."""
    logger.info(f"Destroying sandbox: {sandbox_id}")
    
    try:
        await daytona.destroy_workspace(str(sandbox_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to destroy sandbox: {str(e)}"
        )


@router.post(
    "/{sandbox_id}/start",
    response_model=Sandbox,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}}
)
async def start_sandbox(
    sandbox_id: UUID,
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> Sandbox:
    """Start a stopped sandbox."""
    logger.info(f"Starting sandbox: {sandbox_id}")
    
    try:
        sandbox = await daytona.start_sandbox(str(sandbox_id))
        return sandbox
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start sandbox: {str(e)}"
        )


@router.post(
    "/{sandbox_id}/stop",
    response_model=Sandbox,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}}
)
async def stop_sandbox(
    sandbox_id: UUID,
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> Sandbox:
    """Stop a running sandbox."""
    logger.info(f"Stopping sandbox: {sandbox_id}")
    
    try:
        sandbox = await daytona.stop_sandbox(str(sandbox_id))
        return sandbox
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop sandbox: {str(e)}"
        )


@router.post(
    "/{sandbox_id}/execute",
    response_model=CommandExecuteResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def execute_command(
    sandbox_id: UUID,
    request: CommandExecuteRequest,
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> CommandExecuteResponse:
    """
    Execute a command inside a sandbox.
    
    Returns the command output, exit code, and execution time.
    """
    logger.info(f"Executing command in sandbox {sandbox_id}: {request.command}")
    
    try:
        result = await daytona.run_command(
            sandbox_id=str(sandbox_id),
            command=request.command,
            working_directory=request.working_directory,
            timeout_seconds=request.timeout_seconds,
            env_variables=request.env_variables
        )
        
        return result
        
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Command execution failed: {str(e)}"
        )


@router.post(
    "/{sandbox_id}/terminal",
    response_model=TerminalSession,
    status_code=201,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def open_terminal(
    sandbox_id: UUID,
    request: Optional[TerminalSessionRequest] = None,
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> TerminalSession:
    """
    Open an interactive terminal session in a sandbox.
    
    Returns a WebSocket URL for connecting to the terminal.
    """
    logger.info(f"Opening terminal in sandbox: {sandbox_id}")
    
    try:
        session = await daytona.open_terminal(
            sandbox_id=str(sandbox_id),
            request=request
        )
        
        return session
        
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to open terminal: {str(e)}"
        )


@router.get(
    "/{sandbox_id}/logs",
    response_model=SandboxLogs,
    responses={404: {"model": ErrorResponse}}
)
async def get_sandbox_logs(
    sandbox_id: UUID,
    tail: int = Query(100, ge=1, le=1000),
    daytona: DaytonaManager = Depends(get_daytona_manager)
) -> SandboxLogs:
    """Get execution logs from a sandbox."""
    try:
        logs_data = await daytona.get_logs(str(sandbox_id), tail=tail)
        
        # Convert to schema
        from schemas import LogEntry, LogLevel
        
        entries = []
        for log in logs_data:
            try:
                level = LogLevel(log.get("level", "info"))
            except ValueError:
                level = LogLevel.INFO
            
            entries.append(LogEntry(
                timestamp=datetime.fromisoformat(log.get("timestamp", datetime.now().isoformat())),
                level=level,
                message=log.get("message", ""),
                source=log.get("source")
            ))
        
        return SandboxLogs(
            sandbox_id=sandbox_id,
            logs=entries
        )
        
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")


@router.post(
    "/{sandbox_id}/clone",
    response_model=dict,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def clone_repo(
    sandbox_id: UUID,
    repo_url: str,
    branch: str = "main",
    daytona: DaytonaManager = Depends(get_daytona_manager)
):
    """Clone a repository into the sandbox."""
    logger.info(f"Cloning {repo_url} into sandbox {sandbox_id}")
    
    try:
        success = await daytona.clone_repository(
            sandbox_id=str(sandbox_id),
            repo_url=repo_url,
            branch=branch
        )
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to clone repository"
            )
        
        return {
            "sandbox_id": str(sandbox_id),
            "repo_url": repo_url,
            "branch": branch,
            "status": "cloned"
        }
        
    except ValueError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
