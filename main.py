import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse

# Import routers
from api.routes import repos, deployments, sandboxes, dashboard, github
from api.billing import router as billing_router
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

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pipeline-labs.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred"
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.status_code,
            "message": exc.detail
        }
    )


# Health check endpoint
@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
    description="Check API health status and service availability"
)
async def health_check() -> HealthResponse:
    """Check system health."""
    services = {
        "api": "healthy"
    }
    
    # Check external services
    if os.getenv("DAYTONA_API_KEY"):
        services["daytona"] = "healthy"
    else:
        services["daytona"] = "unhealthy"
    
    if os.getenv("OPENROUTER_API_KEY"):
        services["ai"] = "healthy"
    else:
        services["ai"] = "unhealthy"
    
    return HealthResponse(
        status="healthy" if all(s == "healthy" for s in services.values()) else "degraded",
        timestamp=datetime.now(),
        version="1.0.0",
        services=services
    )


# Custom Swagger UI with constrained CSS
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    """Custom Swagger UI with CSS to prevent overflow."""
    return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
    <title>Pipeline AI DevOps Platform - API Documentation</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
    <style>
        html { box-sizing: border-box; overflow-x: hidden; }
        *, *:before, *:after { box-sizing: inherit; }
        body { 
            margin: 0; 
            padding: 0;
            overflow-x: hidden;
        }
        #swagger-ui {
            max-width: 100vw;
            overflow-x: hidden;
        }
        .swagger-container {
            max-width: 100%;
            overflow-x: auto;
        }
        .swagger-ui {
            max-width: 100%;
        }
        .swagger-ui .wrapper {
            max-width: 1460px;
            width: 100%;
            padding: 0 20px;
        }
        .swagger-ui .opblock {
            max-width: 100%;
        }
        .swagger-ui .opblock .opblock-summary {
            flex-wrap: wrap;
        }
        .swagger-ui .opblock .opblock-summary-method {
            min-width: 80px;
        }
        .swagger-ui table {
            max-width: 100%;
            display: block;
            overflow-x: auto;
        }
        .swagger-ui .response-col_description {
            max-width: 100%;
            word-wrap: break-word;
        }
        .swagger-ui .model-box {
            max-width: 100%;
            overflow-x: auto;
        }
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        window.onload = function() {
            window.ui = SwaggerUIBundle({
                url: '/openapi.json',
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.presets.standalone
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "BaseLayout",
                defaultModelsExpandDepth: -1,
                docExpansion: "list",
                operationsSorter: "alpha",
                tagsSorter: "alpha",
                tryItOutEnabled: true
            });
        };
    </script>
</body>
</html>
    """)


# Include routers
app.include_router(repos.router, prefix="/api/v1")
app.include_router(deployments.router, prefix="/api/v1")
app.include_router(sandboxes.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(github.router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")


# Root endpoint
@app.get("/")
async def root():
    """API root."""
    return {
        "name": "Pipeline AI DevOps Platform",
        "version": "1.0.0",
        "documentation": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    
    # Run development server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
