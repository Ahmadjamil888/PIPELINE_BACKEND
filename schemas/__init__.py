from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum
from pydantic import BaseModel, Field, HttpUrl
from uuid import UUID


# ==================== ENUMS ====================

class GitProvider(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"


class RepoStatus(str, Enum):
    PENDING = "pending"
    CONNECTED = "connected"
    ANALYZING = "analyzing"
    ERROR = "error"


class Framework(str, Enum):
    NEXTJS = "nextjs"
    REACT = "react"
    VUE = "vue"
    ANGULAR = "angular"
    SVELTE = "svelte"
    NUXTJS = "nuxtjs"
    REMIX = "remix"
    FASTAPI = "fastapi"
    FLASK = "flask"
    DJANGO = "django"
    EXPRESS = "express"
    NESTJS = "nestjs"
    GO = "go"
    RUST = "rust"
    UNKNOWN = "unknown"


class Language(str, Enum):
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    RUBY = "ruby"
    PHP = "php"
    UNKNOWN = "unknown"


class Platform(str, Enum):
    VERCEL = "vercel"
    RENDER = "render"
    DOCKER = "docker"


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class ServiceDeploymentStatus(str, Enum):
    PENDING = "pending"
    BUILDING = "building"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SandboxStatus(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    DESTROYED = "destroyed"


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class RenderServiceType(str, Enum):
    WEB_SERVICE = "web_service"
    STATIC_SITE = "static_site"
    BACKGROUND_WORKER = "background_worker"
    CRON_JOB = "cron_job"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


# ==================== REPOSITORY SCHEMAS ====================

class RepoConnectionRequest(BaseModel):
    repo_url: HttpUrl = Field(description="Full URL to the Git repository")
    provider: GitProvider = Field(description="Git provider")
    branch: str = Field(default="main", description="Default branch to use")
    name: Optional[str] = Field(default=None, description="Optional custom name for the repository")


class RepoConnection(BaseModel):
    id: UUID = Field(description="Unique identifier for the connected repository")
    repo_url: HttpUrl
    provider: GitProvider
    branch: str = "main"
    name: Optional[str] = None
    status: RepoStatus
    created_at: datetime
    updated_at: Optional[datetime] = None


class DetectedService(BaseModel):
    name: str = Field(description="Service name (e.g., frontend, api, backend)")
    framework: Framework = Field(description="Detected framework")
    path: str = Field(description="Relative path within the repository")
    language: Language = Field(description="Primary programming language")
    recommended_platform: Platform = Field(description="Recommended deployment platform")
    detected_files: List[str] = Field(default_factory=list, description="Key files that helped identify the service")
    build_command: Optional[str] = Field(default=None, description="Detected or suggested build command")
    start_command: Optional[str] = Field(default=None, description="Detected or suggested start command")
    env_variables: List[str] = Field(default_factory=list, description="Detected environment variable requirements")


class RepoAnalysis(BaseModel):
    repo_id: UUID
    services: List[DetectedService]
    is_monorepo: bool = Field(description="Whether the repository is a monorepo")
    detected_workspaces: List[str] = Field(default_factory=list, description="Workspace tools detected (e.g., turborepo, nx, pnpm)")
    root_config: Optional[Dict[str, Any]] = Field(default=None, description="Root-level configuration files")
    analyzed_at: datetime
    sandbox_id: UUID = Field(description="ID of the sandbox used for analysis")


# ==================== DEPLOYMENT SCHEMAS ====================

class VercelDeploymentConfig(BaseModel):
    project_name: Optional[str] = Field(default=None, description="Vercel project name")
    team_id: Optional[str] = None
    framework: Optional[str] = None


class RenderDeploymentConfig(BaseModel):
    service_name: Optional[str] = None
    service_type: RenderServiceType = RenderServiceType.WEB_SERVICE
    plan: str = "starter"


class ServiceDeploymentConfig(BaseModel):
    name: str
    path: str
    platform: Platform
    build_command: Optional[str] = None
    start_command: Optional[str] = None
    output_directory: Optional[str] = None
    env_variables: Dict[str, str] = Field(default_factory=dict)
    vercel_config: Optional[VercelDeploymentConfig] = None
    render_config: Optional[RenderDeploymentConfig] = None


class DeploymentCreateRequest(BaseModel):
    repo_id: UUID
    services: List[ServiceDeploymentConfig]
    environment: Environment = Environment.PRODUCTION
    branch: Optional[str] = Field(default=None, description="Branch to deploy from")
    env_variables: Dict[str, str] = Field(default_factory=dict, description="Environment variables for all services")


class PlannedService(BaseModel):
    name: str
    path: str
    platform: Platform
    status: ServiceDeploymentStatus = ServiceDeploymentStatus.PENDING
    estimated_duration_seconds: Optional[int] = Field(default=None, description="Estimated deployment duration")


class DeploymentPlan(BaseModel):
    id: UUID
    repo_id: UUID
    status: str = Field(enum=["pending", "planned", "approved"])
    services: List[PlannedService]
    environment: Environment
    branch: Optional[str] = None
    created_at: datetime


class ServiceDeployment(BaseModel):
    name: str
    path: str
    platform: Platform
    status: ServiceDeploymentStatus
    build_logs_url: Optional[HttpUrl] = None
    deployment_url: Optional[HttpUrl] = None
    platform_deployment_id: Optional[str] = Field(default=None, description="Deployment ID from the platform (Vercel/Render)")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class DeploymentStatus(BaseModel):
    id: UUID
    repo_id: UUID
    sandbox_id: Optional[UUID] = None
    status: DeploymentStatus
    services: List[ServiceDeployment]
    environment: Environment
    branch: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    retry_count: int = 0


class DeploymentExecutionResponse(BaseModel):
    deployment_id: UUID
    status: str
    message: str
    estimated_duration_seconds: Optional[int] = None


class LogEntry(BaseModel):
    timestamp: datetime
    level: LogLevel
    message: str
    source: Optional[str] = Field(default=None, description="Component that generated the log")


class DeploymentLogs(BaseModel):
    deployment_id: UUID
    logs: List[LogEntry]
    timestamp: datetime


class DeploymentList(BaseModel):
    deployments: List[DeploymentStatus]
    total: int
    limit: int
    offset: int


# ==================== SANDBOX SCHEMAS ====================

class SandboxResources(BaseModel):
    cpu_cores: int = 2
    memory_mb: int = 4096
    disk_gb: int = 20


class SandboxCreateRequest(BaseModel):
    repo_id: Optional[UUID] = Field(default=None, description="Optional repo to pre-clone")
    repo_url: Optional[HttpUrl] = None
    branch: str = "main"
    environment_variables: Dict[str, str] = Field(default_factory=dict)
    resources: SandboxResources = Field(default_factory=SandboxResources)


class Sandbox(BaseModel):
    id: UUID
    status: SandboxStatus
    repo_id: Optional[UUID] = None
    repo_url: Optional[HttpUrl] = None
    branch: str = "main"
    workspace_url: Optional[str] = Field(default=None, description="URL to access the Daytona workspace")
    resources: SandboxResources
    environment_variables: Dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    destroyed_at: Optional[datetime] = None


class SandboxList(BaseModel):
    sandboxes: List[Sandbox]
    total: int


class CommandExecuteRequest(BaseModel):
    command: str = Field(example="npm install")
    working_directory: str = "/workspace"
    timeout_seconds: int = 300
    env_variables: Dict[str, str] = Field(default_factory=dict)


class CommandExecuteResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    executed_at: datetime


class TerminalSessionRequest(BaseModel):
    shell: str = "/bin/bash"
    working_directory: str = "/workspace"
    environment_variables: Dict[str, str] = Field(default_factory=dict)


class TerminalSession(BaseModel):
    session_id: UUID
    sandbox_id: UUID
    websocket_url: HttpUrl = Field(description="WebSocket URL for terminal connection")
    shell: str
    working_directory: str
    created_at: datetime
    expires_at: Optional[datetime] = None


class SandboxLogs(BaseModel):
    sandbox_id: UUID
    logs: List[LogEntry]


# ==================== ERROR SCHEMAS ====================

class ErrorResponse(BaseModel):
    error: str = Field(description="Error code")
    message: str = Field(description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error details")
    request_id: Optional[str] = Field(default=None, description="Request ID for debugging")


# ==================== HEALTH SCHEMAS ====================

class HealthResponse(BaseModel):
    status: str = Field(enum=["healthy", "unhealthy", "degraded"])
    timestamp: datetime
    version: str
    services: Optional[Dict[str, str]] = None
