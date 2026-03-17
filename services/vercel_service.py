"""
Vercel deployment service.
Handles creating projects and triggering deployments via Vercel API.
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

VERCEL_TOKEN = os.getenv("VERCEL_TOKEN")
VERCEL_HEADERS = {
    "Authorization": f"Bearer {VERCEL_TOKEN}",
    "Content-Type": "application/json"
}


async def deploy_to_vercel(
    repo_full_name: str,
    analysis: dict,
    env_vars: dict,
    deployment_id: str
) -> str:
    """
    Deploy a frontend to Vercel.
    
    Args:
        repo_full_name: e.g., "owner/repo"
        analysis: AI analysis of frontend config
        env_vars: Environment variables to set
        deployment_id: Our internal deployment ID
        
    Returns:
        Deployed URL
    """
    if not VERCEL_TOKEN:
        raise Exception("VERCEL_TOKEN not configured")
    
    async with httpx.AsyncClient() as client:
        # Step 1: Create Vercel project
        project_name = f"pipeline-{deployment_id[:8]}-frontend"
        
        framework = map_framework_to_vercel(analysis.get("framework", "nextjs"))
        root_dir = analysis.get("directory", ".").lstrip("./") or None
        
        create_payload = {
            "name": project_name,
            "framework": framework,
            "gitRepository": {
                "type": "github",
                "repo": repo_full_name
            },
            "buildCommand": analysis.get("build_command"),
            "outputDirectory": analysis.get("output_directory"),
            "installCommand": analysis.get("install_command"),
            "environmentVariables": [
                {
                    "key": k,
                    "value": v,
                    "target": ["production", "preview"]
                }
                for k, v in env_vars.items()
            ] if env_vars else []
        }
        
        if root_dir:
            create_payload["rootDirectory"] = root_dir
        
        logger.info(f"Creating Vercel project: {project_name}")
        
        create_resp = await client.post(
            "https://api.vercel.com/v10/projects",
            headers=VERCEL_HEADERS,
            json=create_payload
        )
        
        if create_resp.status_code not in [200, 201]:
            raise Exception(f"Failed to create Vercel project: {create_resp.text}")
        
        project = create_resp.json()
        project_id = project.get("id")
        
        logger.info(f"Vercel project created: {project_id}")
        
        # Step 2: Trigger deployment
        deploy_payload = {
            "name": project_name,
            "project": project_id,
            "gitSource": {
                "type": "github",
                "repoId": repo_full_name,
                "ref": "main"
            },
            "target": "production"
        }
        
        deploy_resp = await client.post(
            "https://api.vercel.com/v13/deployments",
            headers=VERCEL_HEADERS,
            json=deploy_payload
        )
        
        if deploy_resp.status_code not in [200, 201]:
            raise Exception(f"Failed to trigger Vercel deployment: {deploy_resp.text}")
        
        deployment = deploy_resp.json()
        deploy_id = deployment.get("id")
        
        logger.info(f"Vercel deployment triggered: {deploy_id}")
        
        # Step 3: Poll until ready
        url = await poll_deployment_status(client, deploy_id)
        
        return url


async def poll_deployment_status(client: httpx.AsyncClient, deploy_id: str) -> str:
    """Poll Vercel deployment status until ready."""
    for i in range(60):  # Max 5 minutes
        await asyncio.sleep(5)
        
        status_resp = await client.get(
            f"https://api.vercel.com/v13/deployments/{deploy_id}",
            headers=VERCEL_HEADERS
        )
        
        if status_resp.status_code != 200:
            continue
        
        status_data = status_resp.json()
        ready_state = status_data.get("readyState")
        
        if ready_state == "READY":
            url = status_data.get("url")
            logger.info(f"Vercel deployment ready: {url}")
            return f"https://{url}"
        
        elif ready_state in ["ERROR", "CANCELED"]:
            raise Exception(f"Vercel deployment failed with state: {ready_state}")
        
        logger.debug(f"Vercel deployment status: {ready_state}")
    
    raise Exception("Vercel deployment timed out")


def map_framework_to_vercel(framework: str) -> str:
    """Map our framework names to Vercel framework slugs."""
    mapping = {
        "nextjs": "nextjs",
        "react": "create-react-app",
        "vue": "vue",
        "svelte": "svelte",
        "angular": "angular",
        "static": "static",
        "none": None
    }
    return mapping.get(framework.lower(), None)
