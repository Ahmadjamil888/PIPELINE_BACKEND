import os
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import uuid4, UUID
import httpx

from schemas import (
    Sandbox,
    SandboxStatus,
    SandboxResources,
    CommandExecuteRequest,
    CommandExecuteResponse,
    TerminalSession,
    TerminalSessionRequest,
    SandboxLogs,
    LogEntry,
    LogLevel
)

logger = logging.getLogger(__name__)


class DaytonaManager:
    """Manager for Daytona workspace sandboxes."""

    def __init__(self):
        self.api_key = os.getenv("DAYTONA_API_KEY")
        self.api_url = os.getenv("DAYTONA_API_URL", "https://api.daytona.io/v1")
        self.client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        
        # In-memory store for sandboxes (replace with database in production)
        self._sandboxes: Dict[UUID, Sandbox] = {}
        self._logs: Dict[UUID, List[Dict]] = {}

    async def create_workspace(
        self,
        repo_url: Optional[str] = None,
        branch: str = "main",
        repo_id: Optional[UUID] = None,
        resources: Optional[SandboxResources] = None,
        environment_variables: Optional[Dict[str, str]] = None
    ) -> Sandbox:
        """
        Create a new Daytona workspace.
        
        Args:
            repo_url: URL of the repository to clone
            branch: Branch to checkout
            repo_id: Associated repository ID
            resources: Resource configuration
            environment_variables: Environment variables for the sandbox
            
        Returns:
            Created Sandbox instance
        """
        logger.info(f"Creating Daytona workspace for {repo_url}")
        
        sandbox_id = uuid4()
        env_vars = environment_variables or {}
        
        # Add API tokens to environment
        if os.getenv("VERCEL_TOKEN"):
            env_vars["VERCEL_TOKEN"] = os.getenv("VERCEL_TOKEN")
        if os.getenv("RENDER_API_KEY"):
            env_vars["RENDER_API_KEY"] = os.getenv("RENDER_API_KEY")
        
        res = resources or SandboxResources()
        
        # Create sandbox via Daytona API
        try:
            response = await self.client.post(
                "/workspaces",
                json={
                    "id": str(sandbox_id),
                    "image": "daytonaio/workspace:latest",
                    "resources": {
                        "cpu": res.cpu_cores,
                        "memory": res.memory_mb,
                        "disk": res.disk_gb
                    },
                    "env": env_vars,
                    "auto_stop_interval": 30  # Auto-stop after 30 minutes of inactivity
                }
            )
            response.raise_for_status()
            workspace_data = response.json()
            
            sandbox = Sandbox(
                id=sandbox_id,
                status=SandboxStatus.CREATING,
                repo_id=repo_id,
                repo_url=repo_url,
                branch=branch,
                workspace_url=workspace_data.get("url"),
                resources=res,
                environment_variables=env_vars,
                created_at=datetime.now()
            )
            
            self._sandboxes[sandbox_id] = sandbox
            self._logs[sandbox_id] = []
            
            # Start the sandbox
            await self.start_sandbox(str(sandbox_id))
            
            logger.info(f"Sandbox {sandbox_id} created successfully")
            return sandbox
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to create Daytona workspace: {e}")
            raise RuntimeError(f"Failed to create workspace: {e}")

    async def get_sandbox(self, sandbox_id: str) -> Sandbox:
        """Get sandbox by ID."""
        sandbox_uuid = UUID(sandbox_id)
        if sandbox_uuid in self._sandboxes:
            return self._sandboxes[sandbox_uuid]
        
        # Try to fetch from API
        try:
            response = await self.client.get(f"/workspaces/{sandbox_id}")
            response.raise_for_status()
            data = response.json()
            
            return Sandbox(
                id=UUID(sandbox_id),
                status=SandboxStatus(data.get("status", "stopped")),
                workspace_url=data.get("url"),
                resources=SandboxResources(),
                environment_variables={},
                created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat()))
            )
        except httpx.HTTPError:
            raise ValueError(f"Sandbox {sandbox_id} not found")

    async def start_sandbox(self, sandbox_id: str) -> Sandbox:
        """Start a sandbox."""
        logger.info(f"Starting sandbox {sandbox_id}")
        
        try:
            response = await self.client.post(f"/workspaces/{sandbox_id}/start")
            response.raise_for_status()
            
            # Update local status
            sandbox_uuid = UUID(sandbox_id)
            if sandbox_uuid in self._sandboxes:
                self._sandboxes[sandbox_uuid].status = SandboxStatus.RUNNING
                self._sandboxes[sandbox_uuid].started_at = datetime.now()
            
            logger.info(f"Sandbox {sandbox_id} started")
            return await self.get_sandbox(sandbox_id)
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to start sandbox: {e}")
            raise RuntimeError(f"Failed to start sandbox: {e}")

    async def stop_sandbox(self, sandbox_id: str) -> Sandbox:
        """Stop a sandbox."""
        logger.info(f"Stopping sandbox {sandbox_id}")
        
        try:
            response = await self.client.post(f"/workspaces/{sandbox_id}/stop")
            response.raise_for_status()
            
            # Update local status
            sandbox_uuid = UUID(sandbox_id)
            if sandbox_uuid in self._sandboxes:
                self._sandboxes[sandbox_uuid].status = SandboxStatus.STOPPED
                self._sandboxes[sandbox_uuid].stopped_at = datetime.now()
            
            logger.info(f"Sandbox {sandbox_id} stopped")
            return await self.get_sandbox(sandbox_id)
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to stop sandbox: {e}")
            raise RuntimeError(f"Failed to stop sandbox: {e}")

    async def destroy_workspace(self, sandbox_id: str) -> None:
        """Destroy a sandbox and free resources."""
        logger.info(f"Destroying sandbox {sandbox_id}")
        
        try:
            response = await self.client.delete(f"/workspaces/{sandbox_id}")
            response.raise_for_status()
            
            # Update local status
            sandbox_uuid = UUID(sandbox_id)
            if sandbox_uuid in self._sandboxes:
                self._sandboxes[sandbox_uuid].status = SandboxStatus.DESTROYED
                self._sandboxes[sandbox_uuid].destroyed_at = datetime.now()
            
            logger.info(f"Sandbox {sandbox_id} destroyed")
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to destroy sandbox: {e}")
            raise RuntimeError(f"Failed to destroy sandbox: {e}")

    async def run_command(
        self,
        sandbox_id: str,
        command: str,
        working_directory: str = "/workspace",
        timeout_seconds: int = 300,
        env_variables: Optional[Dict[str, str]] = None
    ) -> CommandExecuteResponse:
        """
        Run a command in the sandbox.
        
        Args:
            sandbox_id: Sandbox ID
            command: Command to execute
            working_directory: Working directory for the command
            timeout_seconds: Command timeout
            env_variables: Additional environment variables
            
        Returns:
            Command execution result
        """
        logger.debug(f"Running command in sandbox {sandbox_id}: {command}")
        
        try:
            response = await self.client.post(
                f"/workspaces/{sandbox_id}/exec",
                json={
                    "command": command,
                    "cwd": working_directory,
                    "timeout": timeout_seconds,
                    "env": env_variables or {}
                },
                timeout=timeout_seconds + 10
            )
            response.raise_for_status()
            result = response.json()
            
            # Log the command execution
            self._log(sandbox_id, LogLevel.INFO, f"Command: {command}", "command")
            if result.get("stderr"):
                self._log(sandbox_id, LogLevel.WARNING, result["stderr"], "command")
            
            return CommandExecuteResponse(
                exit_code=result.get("exit_code", 1),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                duration_ms=result.get("duration_ms", 0),
                executed_at=datetime.now()
            )
            
        except httpx.TimeoutException:
            self._log(sandbox_id, LogLevel.ERROR, f"Command timed out: {command}", "command")
            return CommandExecuteResponse(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout_seconds} seconds",
                duration_ms=timeout_seconds * 1000,
                executed_at=datetime.now()
            )
        except httpx.HTTPError as e:
            self._log(sandbox_id, LogLevel.ERROR, f"Command failed: {str(e)}", "command")
            raise RuntimeError(f"Command execution failed: {e}")

    async def open_terminal(
        self,
        sandbox_id: str,
        request: Optional[TerminalSessionRequest] = None
    ) -> TerminalSession:
        """
        Open an interactive terminal session.
        
        Args:
            sandbox_id: Sandbox ID
            request: Terminal session configuration
            
        Returns:
            Terminal session details with WebSocket URL
        """
        logger.info(f"Opening terminal session in sandbox {sandbox_id}")
        
        req = request or TerminalSessionRequest()
        
        try:
            response = await self.client.post(
                f"/workspaces/{sandbox_id}/terminal",
                json={
                    "shell": req.shell,
                    "cwd": req.working_directory,
                    "env": req.environment_variables
                }
            )
            response.raise_for_status()
            data = response.json()
            
            session_id = uuid4()
            
            return TerminalSession(
                session_id=session_id,
                sandbox_id=UUID(sandbox_id),
                websocket_url=data.get("websocket_url"),
                shell=req.shell,
                working_directory=req.working_directory,
                created_at=datetime.now(),
                expires_at=datetime.now()  # Session expires after some time
            )
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to open terminal: {e}")
            raise RuntimeError(f"Failed to open terminal: {e}")

    async def get_logs(self, sandbox_id: str, tail: int = 100) -> List[Dict]:
        """Get logs from a sandbox."""
        sandbox_uuid = UUID(sandbox_id)
        
        if sandbox_uuid in self._logs:
            return self._logs[sandbox_uuid][-tail:]
        
        # Try to fetch from API
        try:
            response = await self.client.get(
                f"/workspaces/{sandbox_id}/logs",
                params={"tail": tail}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return []

    def _log(self, sandbox_id: str, level: LogLevel, message: str, source: Optional[str] = None):
        """Add a log entry for a sandbox."""
        sandbox_uuid = UUID(sandbox_id)
        if sandbox_uuid not in self._logs:
            self._logs[sandbox_uuid] = []
        
        self._logs[sandbox_uuid].append({
            "timestamp": datetime.now().isoformat(),
            "level": level.value,
            "message": message,
            "source": source or "daytona_manager"
        })

    async def list_sandboxes(
        self,
        status: Optional[SandboxStatus] = None,
        limit: int = 20
    ) -> List[Sandbox]:
        """List all sandboxes."""
        sandboxes = list(self._sandboxes.values())
        
        if status:
            sandboxes = [s for s in sandboxes if s.status == status]
        
        return sandboxes[:limit]

    async def clone_repository(
        self,
        sandbox_id: str,
        repo_url: str,
        branch: str = "main",
        target_path: str = "/workspace/repo"
    ) -> bool:
        """Clone a repository into the sandbox."""
        logger.info(f"Cloning {repo_url} into sandbox {sandbox_id}")
        
        # Check if git is available
        git_check = await self.run_command(sandbox_id, "which git")
        if git_check.exit_code != 0:
            # Install git
            install_result = await self.run_command(
                sandbox_id,
                "apt-get update && apt-get install -y git",
                timeout_seconds=120
            )
            if install_result.exit_code != 0:
                raise RuntimeError("Failed to install git")
        
        # Clone the repository
        result = await self.run_command(
            sandbox_id,
            f"git clone -b {branch} --depth 1 {repo_url} {target_path}",
            timeout_seconds=120
        )
        
        if result.exit_code == 0:
            self._log(sandbox_id, LogLevel.INFO, f"Repository cloned: {repo_url}", "clone")
            return True
        else:
            self._log(sandbox_id, LogLevel.ERROR, f"Clone failed: {result.stderr}", "clone")
            return False
