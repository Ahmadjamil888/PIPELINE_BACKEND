"""
Render deployment service.
Handles creating services and triggering deployments via Render API.
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

RENDER_TOKEN = os.getenv("RENDER_API_KEY")
RENDER_HEADERS = {
    "Authorization": f"Bearer {RENDER_TOKEN}",
    "Content-Type": "application/json"
}


async def deploy_to_render(
    repo_full_name: str,
    analysis: dict,
    installation_id: int,
    deployment_id: str
) -> str:
    """
    Deploy a backend to Render.
    
    Args:
        repo_full_name: e.g., "owner/repo"
        analysis: AI analysis of backend config
        installation_id: GitHub App installation ID
        deployment_id: Our internal deployment ID
        
    Returns:
        Deployed URL
    """
    if not RENDER_TOKEN:
        raise Exception("RENDER_API_KEY not configured")
    
    owner_id = os.getenv("RENDER_OWNER_ID")
    if not owner_id:
        raise Exception("RENDER_OWNER_ID not configured")
    
    async with httpx.AsyncClient() as client:
        service_name = f"pipeline-{deployment_id[:8]}-backend"
        
        # Detect runtime
        language = analysis.get("language", "python")
        runtime = "python" if language == "python" else "node"
        
        root_dir = analysis.get("directory", ".").lstrip("./") or None
        
        # Build command
        build_command = analysis.get("build_command", "")
        if runtime == "python" and not build_command:
            build_command = "pip install -r requirements.txt"
        elif runtime == "node" and not build_command:
            build_command = "npm install"
        
        # Start command
        start_command = analysis.get("start_command", "")
        if runtime == "python" and not start_command:
            start_command = "uvicorn main:app --host 0.0.0.0 --port 8000"
        elif runtime == "node" and not start_command:
            start_command = "npm start"
        
        logger.info(f"Creating Render service: {service_name}")
        
        # Create service
        create_payload = {
            "type": "web_service",
            "name": service_name,
            "ownerId": owner_id,
            "repo": f"https://github.com/{repo_full_name}",
            "branch": "main",
            "serviceDetails": {
                "runtime": runtime,
                "buildCommand": build_command,
                "startCommand": start_command,
                "envVars": [
                    {"key": "PORT", "value": "8000"},
                    {"key": "PYTHON_VERSION", "value": "3.11.0"},
                    {"key": "NODE_VERSION", "value": "18.17.0"}
                ]
            }
        }
        
        if root_dir:
            create_payload["rootDir"] = root_dir
        
        create_resp = await client.post(
            "https://api.render.com/v1/services",
            headers=RENDER_HEADERS,
            json=create_payload
        )
        
        if create_resp.status_code not in [200, 201]:
            raise Exception(f"Failed to create Render service: {create_resp.text}")
        
        service = create_resp.json()
        service_id = service.get("service", {}).get("id")
        
        logger.info(f"Render service created: {service_id}")
        
        # Poll for deploy completion
        url = await poll_render_service(client, service_id)
        
        return url


async def poll_render_service(client: httpx.AsyncClient, service_id: str) -> str:
    """Poll Render service status until deployed."""
    for i in range(60):  # Max 5 minutes
        await asyncio.sleep(5)
        
        status_resp = await client.get(
            f"https://api.render.com/v1/services/{service_id}",
            headers=RENDER_HEADERS
        )
        
        if status_resp.status_code != 200:
            continue
        
        svc = status_resp.json().get("service", {})
        
        # Check if service is deployed
        if svc.get("suspended") == "not_suspended":
            service_details = svc.get("serviceDetails", {})
            url = service_details.get("url")
            if url:
                full_url = f"https://{url}"
                logger.info(f"Render service ready: {full_url}")
                return full_url
        
        # Check for deploy status
        deploy_status = svc.get("deploy", {}).get("status")
        logger.debug(f"Render deploy status: {deploy_status}")
    
    raise Exception("Render deployment timed out")


async def update_render_env_vars(service_id: str, env_vars: dict):
    """Update environment variables for a Render service."""
    async with httpx.AsyncClient() as client:
        # Update env vars
        await client.put(
            f"https://api.render.com/v1/services/{service_id}/env-vars",
            headers=RENDER_HEADERS,
            json=[{"key": k, "value": v} for k, v in env_vars.items()]
        )
        
        # Trigger redeploy
        await client.post(
            f"https://api.render.com/v1/services/{service_id}/deploys",
            headers=RENDER_HEADERS
        )
        
        logger.info(f"Updated Render env vars for {service_id}")
