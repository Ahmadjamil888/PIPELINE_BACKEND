# Pipeline Labs Status Messages
# Maps internal GCP statuses to user-friendly Pipeline Labs messages

INTERNAL_TO_PUBLIC = {
    # Cloud Build stages
    "QUEUED": "Preparing your environment...",
    "WORKING": "Building your model...",
    "FETCHING_SOURCE": "Cloning your repository...",
    "BUILDING": "Packaging your model...",
    "PUSHING": "Finalizing build...",
    "BUILD_SUCCESS": "Build complete",
    "BUILD_FAILURE": "Build failed",
    
    # Cloud Run stages  
    "DEPLOYING": "Deploying your model...",
    "ROUTING_TRAFFIC": "Spinning up your endpoint...",
    "READY": "Your model is live",
    "FAILED": "Deployment failed",
    "CONTAINER_FAILED": "Your model crashed on startup — check your logs",
    "SERVING": "Your model is running",
    
    # Pipeline Labs custom stages
    "analyzing": "Analyzing your repository...",
    "detecting_framework": "Detecting model framework...",
    "configuring": "Configuring runtime...",
    "pending": "Initializing deployment...",
    "running": "Deployment in progress...",
    "completed": "Deployment complete",
    "error": "Deployment encountered an issue",
}

def public_status(internal: str) -> str:
    """Convert internal GCP status to user-friendly message"""
    return INTERNAL_TO_PUBLIC.get(internal.upper(), "Processing...")

def sanitize_error_message(error: str) -> str:
    """Remove GCP-specific details from error messages"""
    import re
    
    # Patterns to hide
    gcp_patterns = [
        r'\.run\.app',
        r'\.googleapis\.com',
        r'us-central1',
        r'gcr\.io',
        r'cloudbuild',
        r'artifactregistry',
        r'gke-',
        r'cloud-build-',
        r'pkg\.dev',
        r'projects/[^/]+',
        r'locations/[^/]+',
        r'services/[^/]+',
    ]
    
    sanitized = error
    for pattern in gcp_patterns:
        sanitized = re.sub(pattern, '[pipeline-labs-internal]', sanitized, flags=re.IGNORECASE)
    
    # Replace GCP service names
    replacements = {
        "Cloud Run": "Pipeline Labs Runtime",
        "Cloud Build": "Pipeline Labs Builder",
        "Artifact Registry": "Pipeline Labs Registry",
        "Google Cloud": "Pipeline Labs Cloud",
        "GKE": "Pipeline Labs Container Engine",
    }
    
    for gcp_term, pipeline_term in replacements.items():
        sanitized = sanitized.replace(gcp_term, pipeline_term)
    
    return sanitized
