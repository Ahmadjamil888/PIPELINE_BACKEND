"""
AI Analysis routes with SSE streaming.
"""
import asyncio
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import AsyncGenerator

router = APIRouter(prefix="/projects", tags=["AI Analysis"])

# Store analysis progress (in production, use Redis)
analysis_progress: dict[str, list[dict]] = {}


class AnalysisProgress:
    """Track analysis progress for a project."""
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.steps: list[dict] = []
        self.complete = False
        self.error: str | None = None
    
    def add_step(self, step: str, status: str, message: str = "", details: dict = None):
        self.steps.append({
            "id": str(uuid.uuid4())[:8],
            "step": step,
            "status": status,  # running, complete, error
            "message": message,
            "details": details or {},
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    def to_sse(self) -> str:
        data = {
            "project_id": self.project_id,
            "steps": self.steps,
            "complete": self.complete,
            "error": self.error,
        }
        return f"data: {json.dumps(data)}\n\n"


async def get_db(request: Request):
    return request.app.state.db


async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return {"id": "test-user", "email": "test@example.com"}
    raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/{project_id}/analysis/stream")
async def analysis_stream(
    project_id: str,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    SSE endpoint for live AI analysis updates.
    Frontend subscribes to get real-time progress.
    """
    profile_id = user["id"]
    
    # Verify project ownership
    project = await db.fetch_one(
        """
        SELECT p.id, p.status
        FROM projects p
        JOIN organisations o ON p.org_id = o.id
        WHERE p.id = :project_id AND o.owner_id = :owner_id
        """,
        {"project_id": project_id, "owner_id": profile_id}
    )
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        progress = analysis_progress.get(project_id, AnalysisProgress(project_id))
        
        # Send initial state
        yield progress.to_sse()
        
        # Stream updates until complete or error
        last_step_count = len(progress.steps)
        while not progress.complete and not progress.error:
            await asyncio.sleep(1)
            
            # Check for new steps
            if len(progress.steps) > last_step_count:
                last_step_count = len(progress.steps)
                yield progress.to_sse()
        
        # Final update
        yield progress.to_sse()
        yield "event: close\ndata: {}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post("/{project_id}/analyse")
async def trigger_analysis(
    project_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Trigger AI analysis manually.
    Normally triggered automatically on project create.
    """
    profile_id = user["id"]
    
    # Verify project ownership
    project = await db.fetch_one(
        """
        SELECT p.id, p.repo_full_name
        FROM projects p
        JOIN organisations o ON p.org_id = o.id
        WHERE p.id = :project_id AND o.owner_id = :owner_id
        """,
        {"project_id": project_id, "owner_id": profile_id}
    )
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Reset and start analysis
    analysis_progress[project_id] = AnalysisProgress(project_id)
    
    background_tasks.add_task(
        run_ai_analysis,
        project_id,
        project["repo_full_name"],
        db
    )
    
    return {"message": "Analysis started", "project_id": project_id}


async def run_ai_analysis(project_id: str, repo_full_name: str, db):
    """
    Background task to run AI analysis on a project.
    Updates progress via the analysis_progress dict.
    """
    progress = analysis_progress.get(project_id)
    if not progress:
        progress = AnalysisProgress(project_id)
        analysis_progress[project_id] = progress
    
    try:
        # Step 1: Create Daytona sandbox
        progress.add_step(
            step="create_sandbox",
            status="running",
            message="Creating Daytona sandbox..."
        )
        # TODO: Implement actual Daytona sandbox creation
        await asyncio.sleep(2)  # Simulate
        progress.add_step(
            step="create_sandbox",
            status="complete",
            message="Daytona sandbox created"
        )
        
        # Step 2: Clone repo
        progress.add_step(
            step="clone_repo",
            status="running",
            message=f"Cloning {repo_full_name}..."
        )
        # TODO: Implement actual git clone
        await asyncio.sleep(3)  # Simulate
        progress.add_step(
            step="clone_repo",
            status="complete",
            message="Repository cloned"
        )
        
        # Step 3: AI Detection
        progress.add_step(
            step="detect_services",
            status="running",
            message="Running AI detection on codebase..."
        )
        # TODO: Implement actual AI detection with Claude API
        await asyncio.sleep(5)  # Simulate
        
        # Mock detected services
        detected_services = [
            {"name": "backend", "language": "python", "framework": "fastapi", "path": "/backend"},
            {"name": "frontend", "language": "typescript", "framework": "nextjs", "path": "/frontend"},
        ]
        progress.add_step(
            step="detect_services",
            status="complete",
            message=f"Detected {len(detected_services)} services",
            details={"services": detected_services}
        )
        
        # Step 4: Create service sandboxes
        for service in detected_services:
            progress.add_step(
                step=f"create_sandbox_{service['name']}",
                status="running",
                message=f"Creating sandbox for {service['name']}..."
            )
            # TODO: Create per-service sandbox
            await asyncio.sleep(2)  # Simulate
            
            # Create service record
            service_id = str(uuid.uuid4())
            await db.execute(
                """
                INSERT INTO services (id, project_id, name, language, framework, path, status, created_at)
                VALUES (:id, :project_id, :name, :language, :framework, :path, :status, :created_at)
                """,
                {
                    "id": service_id,
                    "project_id": project_id,
                    "name": service["name"],
                    "language": service["language"],
                    "framework": service["framework"],
                    "path": service["path"],
                    "status": "created",
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            
            progress.add_step(
                step=f"create_sandbox_{service['name']}",
                status="complete",
                message=f"Sandbox created for {service['name']}"
            )
        
        # Step 5: Attempt builds
        progress.add_step(
            step="build_services",
            status="running",
            message="Building services and auto-fixing errors..."
        )
        # TODO: Implement actual build attempts with auto-fix
        await asyncio.sleep(4)  # Simulate
        progress.add_step(
            step="build_services",
            status="complete",
            message="All services built successfully"
        )
        
        # Update project status
        await db.execute(
            "UPDATE projects SET status = 'analyzed', updated_at = :now WHERE id = :id",
            {"id": project_id, "now": datetime.utcnow().isoformat()}
        )
        
        progress.complete = True
        progress.add_step(
            step="complete",
            status="complete",
            message="Analysis complete! Ready for deployment."
        )
        
    except Exception as e:
        progress.error = str(e)
        progress.add_step(
            step="error",
            status="error",
            message=f"Analysis failed: {str(e)}"
        )
        await db.execute(
            "UPDATE projects SET status = 'error', updated_at = :now WHERE id = :id",
            {"id": project_id, "now": datetime.utcnow().isoformat()}
        )
