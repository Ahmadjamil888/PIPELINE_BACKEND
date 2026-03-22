import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, Response

# Import routers
from api.routes import repos, deployments, sandboxes, dashboard, billing, organisations, analysis
from api.routes.github import router as github_router
from api import webhooks, projects
from api.domains import router as domains_router
from api.routes.models import router as models_router
from schemas import HealthResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Pipeline AI DevOps Platform")
    
    # Verify required environment variables
    required_vars = []
    optional_vars = [
        "DAYTONA_API_KEY",
        "VERCEL_TOKEN",
        "RENDER_API_KEY",
    ]
    
    for var in optional_vars:
        if not os.getenv(var):
            logger.warning(f"Optional environment variable not set: {var}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Pipeline AI DevOps Platform")


# Create FastAPI application
app = FastAPI(
    title="Pipeline AI DevOps Platform",
    description="""
    AI DevOps automation platform for deploying applications to Vercel and Render.
    
    ## Features
    
    - **Repository Analysis**: AI-powered detection of services and frameworks
    - **Deployment Planning**: Automated generation of deployment configurations
    - **Sandboxed Execution**: Daytona workspaces for isolated build and deploy
    - **Multi-Platform Support**: Deploy to Vercel and Render
    - **Interactive Terminals**: Live terminal access to sandboxes
    
    ## Authentication
    
    All endpoints require Bearer token authentication via the `Authorization` header.
    """,
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

ALLOWED_ORIGINS = [
    "https://pipeline-labs.vercel.app",
    "http://localhost:3000",
]

# Method 1 — standard middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Method 2 — manual header injection as backup
@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    origin = request.headers.get("origin", "")

    # Handle preflight
    if request.method == "OPTIONS":
        response = JSONResponse(content={}, status_code=200)
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
            response.headers["Access-Control-Allow-Headers"] = "*"
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    response = await call_next(request)

    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"

    # Response sanitization to hide GCP references
    if "application/json" in response.headers.get("content-type", ""):
        import re
        GCP_PATTERNS = [
            r"\.run\.app", r"\.googleapis\.com", r"us-central1",
            r"gcr\.io", r"cloudbuild", r"artifactregistry",
            r"gke-", r"cloud-build-", r"pkg\.dev",
            r"projects/[^/]+", r"locations/[^/]+", r"services/[^/]+"
        ]
        
        # Read response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        
        try:
            text = body.decode()
            # Check for GCP patterns
            for pattern in GCP_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    # Log the leak for fixing
                    logger.warning(f"GCP reference detected in response: {pattern}")
                    # Replace with placeholder
                    text = re.sub(pattern, "[pipeline-labs-internal]", text, flags=re.IGNORECASE)
            
            # Return sanitized response
            return Response(
                content=text,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json"
            )
        except:
            # If sanitization fails, return original response
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers)
            )

    return response

# Routers come AFTER all middleware
app.include_router(repos.router, prefix="/api/v1")
app.include_router(deployments.router, prefix="/api/v1")
app.include_router(sandboxes.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(github_router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(organisations.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(domains_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Please try again later."
        }
    )

@app.exception_handler(status.HTTP_404_NOT_FOUND)
async def not_found_handler(request, exc):
    """Handle 404 errors."""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "error": "not_found",
            "message": "The requested resource was not found."
        }
    )

@app.exception_handler(status.HTTP_422_UNPROCESSABLE_ENTITY)
async def validation_exception_handler(request, exc):
    """Handle validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "validation_error",
            "message": "Invalid request data."
        }
    )


# Root endpoint
@app.get("/")
async def root():
    """API root."""
    return {
        "name": "Pipeline AI DevOps Platform",
        "version": "1.0.0",
        "documentation": "https://pipeline.stldocs.app",
        "status": "operational"
    }

@app.get("/api/v1/health")
async def health():
    return {"status": "healthy", "version": "1.0.0"}

@app.get("/ping")
async def ping():
    """Simple ping endpoint for health checks."""
    return {"pong": True, "timestamp": datetime.now().isoformat()}
