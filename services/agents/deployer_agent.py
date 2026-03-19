"""
Agent 4: Deployer
Deploys services to Vercel and Render platforms.
"""
import httpx
import os
import logging
import asyncio

logger = logging.getLogger(__name__)

VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "")
RENDER_TOKEN = os.getenv("RENDER_TOKEN", "")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "")


async def get_render_owner_id() -> str:
    """Get your Render owner/team ID — required for creating services."""
    if RENDER_OWNER_ID:
        return RENDER_OWNER_ID
    
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://api.render.com/v1/owners?limit=1",
            headers={
                "Authorization": f"Bearer {RENDER_TOKEN}",
                "Accept": "application/json",
            },
        )
        
        if res.status_code != 200:
            raise Exception(f"Failed to get Render owners: {res.status_code} - {res.text}")
        
        owners = res.json()
        if owners and len(owners) > 0:
            owner_id = owners[0].get("owner", {}).get("id")
            if owner_id:
                logger.info(f"Got Render owner ID: {owner_id}")
                return owner_id
        
        raise Exception("Could not get Render owner ID from API response")


async def deploy_to_vercel(
    service: dict,
    repo_full_name: str,
    installation_token: str,
    env_vars: dict,
) -> dict:
    """
    Deploy a service to Vercel via API.
    
    Args:
        service: Service dict with name, framework, build_command, etc.
        repo_full_name: e.g., "owner/repo"
        installation_token: GitHub installation token (not used directly)
        env_vars: Environment variables to set
        
    Returns:
        dict with deployment_id, url, status
    """
    if not VERCEL_TOKEN:
        raise ValueError("VERCEL_TOKEN not configured")
    
    project_name = f"pipeline-{service['name']}-{repo_full_name.replace('/', '-')}"
    project_name = project_name.lower()[:50]  # Vercel limit
    
    framework = service.get("framework", "nextjs")
    if framework == "nextjs":
        vercel_framework = "nextjs"
    elif framework in ["react", "vue", "svelte"]:
        vercel_framework = framework
    else:
        vercel_framework = None
    
    logger.info(f"Deploying {service['name']} to Vercel as {project_name}")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Step 1: Create Vercel project
        create_payload = {
            "name": project_name,
            "framework": vercel_framework,
            "gitRepository": {
                "type": "github",
                "repo": repo_full_name,
            },
            "rootDirectory": service.get("path", ""),
        }
        
        # Add optional fields if present
        if service.get("build_command"):
            create_payload["buildCommand"] = service["build_command"]
        if service.get("output_directory"):
            create_payload["outputDirectory"] = service["output_directory"]
        if service.get("install_command"):
            create_payload["installCommand"] = service["install_command"]
        
        create_res = await client.post(
            "https://api.vercel.com/v10/projects",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            json=create_payload,
        )
        
        if create_res.status_code not in [200, 201]:
            error_text = create_res.text
            logger.error(f"Failed to create Vercel project: {create_res.status_code} - {error_text}")
            raise Exception(f"Vercel project creation failed: {error_text}")
        
        project = create_res.json()
        project_id = project.get("id")
        
        logger.info(f"Vercel project created: {project_id}")
        
        # Step 2: Add environment variables
        if env_vars and project_id:
            env_payload = [
                {
                    "key": k,
                    "value": v,
                    "type": "encrypted",
                    "target": ["production", "preview", "development"],
                }
                for k, v in env_vars.items()
            ]
            
            env_res = await client.post(
                f"https://api.vercel.com/v10/projects/{project_id}/env",
                headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
                json=env_payload,
            )
            
            if env_res.status_code not in [200, 201]:
                logger.warning(f"Failed to set env vars: {env_res.status_code}")
        
        # Step 3: Trigger deployment
        deploy_res = await client.post(
            "https://api.vercel.com/v13/deployments",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            json={
                "name": project_name,
                "gitSource": {
                    "type": "github",
                    "repo": repo_full_name,
                    "ref": "main",
                },
                "projectId": project_id,
                "target": "production",
            },
        )
        
        if deploy_res.status_code not in [200, 201]:
            error_text = deploy_res.text
            logger.error(f"Failed to trigger Vercel deployment: {deploy_res.status_code} - {error_text}")
            raise Exception(f"Vercel deployment failed: {error_text}")
        
        deployment = deploy_res.json()
        deployment_id = deployment.get("id")
        url = deployment.get("url")
        
        logger.info(f"Vercel deployment triggered: {deployment_id}")
        
        # Step 4: Poll for deployment status
        final_status = await poll_vercel_deployment(client, deployment_id)
        
        return {
            "platform": "vercel",
            "deployment_id": deployment_id,
            "url": f"https://{url}" if url else None,
            "status": final_status,
            "project_id": project_id,
        }


async def poll_vercel_deployment(client: httpx.AsyncClient, deployment_id: str, max_attempts: int = 30) -> str:
    """Poll Vercel deployment status until ready or failed."""
    for attempt in range(max_attempts):
        await asyncio.sleep(2)
        
        res = await client.get(
            f"https://api.vercel.com/v13/deployments/{deployment_id}",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
        )
        
        if res.status_code == 200:
            data = res.json()
            status = data.get("readyState", "BUILDING")
            
            if status == "READY":
                logger.info(f"Vercel deployment {deployment_id} is ready")
                return "success"
            elif status in ["ERROR", "CANCELED"]:
                logger.error(f"Vercel deployment {deployment_id} failed: {status}")
                return "failed"
        
        logger.debug(f"Vercel deployment {deployment_id} status: {status}, attempt {attempt + 1}")
    
    return "timeout"


async def get_render_owner_id() -> str:
    """Get your Render owner/team ID — required for creating services."""
    if RENDER_OWNER_ID:
        return RENDER_OWNER_ID
    
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://api.render.com/v1/owners?limit=1",
            headers={
                "Authorization": f"Bearer {RENDER_TOKEN}",
                "Accept": "application/json",
            },
        )
        
        if res.status_code != 200:
            raise Exception(f"Failed to get Render owners: {res.status_code} - {res.text}")
        
        owners = res.json()
        if owners and len(owners) > 0:
            owner_id = owners[0].get("owner", {}).get("id")
            if owner_id:
                logger.info(f"Got Render owner ID: {owner_id}")
                return owner_id
        
        raise Exception("Could not get Render owner ID from API response")


async def deploy_to_render(
    service: dict,
    repo_full_name: str,
    env_vars: dict,
) -> dict:
    """
    Deploy a service to Render via API v1.
    
    Args:
        service: Service dict with name, build_command, start_command, etc.
        repo_full_name: e.g., "owner/repo"
        env_vars: Environment variables to set
        
    Returns:
        dict with service_id, url, status
    """
    if not RENDER_TOKEN:
        raise ValueError("RENDER_TOKEN not configured")
    
    service_name = f"pipeline-{service['name']}-{repo_full_name.split('/')[-1]}"
    service_name = service_name.lower()[:50]
    
    logger.info(f"Deploying {service['name']} to Render as {service_name}")
    
    # Get owner ID (required)
    owner_id = await get_render_owner_id()
    
    # Determine environment based on framework
    framework = service.get("framework", "")
    language = service.get("language", "")
    
    if framework in ["fastapi", "flask", "django"] or language == "python":
        env = "python"
    elif framework in ["express", "nestjs"] or language in ["javascript", "typescript"]:
        env = "node"
    else:
        env = "docker"
    
    build_command = service.get("build_command", "")
    start_command = service.get("start_command", "")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            "https://api.render.com/v1/services",
            headers={
                "Authorization": f"Bearer {RENDER_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "type": "web_service",
                "name": service_name,
                "ownerId": owner_id,
                "repo": f"https://github.com/{repo_full_name}",
                "branch": "main",
                "rootDir": service.get("path", "") or "",
                "buildCommand": build_command,
                "startCommand": start_command,
                "serviceDetails": {
                    "env": env,
                    "plan": "starter",
                    "region": "oregon",
                    "pullRequestPreviewsEnabled": False,
                    "buildCommand": build_command,
                    "startCommand": start_command,
                },
                "envVars": [
                    {"key": k, "value": v}
                    for k, v in env_vars.items()
                ],
            },
        )
        
        logger.info(f"Render response: {res.status_code}")
        
        if res.status_code not in (200, 201):
            error_text = res.text
            logger.error(f"Render deploy failed: {res.status_code} - {error_text}")
            raise Exception(f"Render deploy failed: {error_text}")
        
        data = res.json()
        service_data = data.get("service", data)
        service_id = service_data.get("id")
        
        logger.info(f"Render service created: {service_id}")
        
        return {
            "platform": "render",
            "service_id": service_id,
            "url": f"https://{service_name}.onrender.com",
            "status": "deploying",
        }


async def poll_render_service(client: httpx.AsyncClient, service_id: str, max_attempts: int = 60) -> tuple:
    """Poll Render service status until deployed or failed."""
    for attempt in range(max_attempts):
        await asyncio.sleep(5)
        
        res = await client.get(
            f"https://api.render.com/v1/services/{service_id}",
            headers={"Authorization": f"Bearer {RENDER_TOKEN}"},
        )
        
        if res.status_code == 200:
            data = res.json()
            service_data = data.get("service", {})
            
            # Check if service is live
            if service_data.get("suspended") == "not_suspended":
                service_details = service_data.get("serviceDetails", {})
                url = service_details.get("url")
                if url:
                    logger.info(f"Render service {service_id} is live at {url}")
                    return ("success", url)
            
            # Check deploy status
            deploy = service_data.get("deploy", {})
            deploy_status = deploy.get("status")
            
            if deploy_status == "failed":
                logger.error(f"Render deploy failed for {service_id}")
                return ("failed", None)
        
        logger.debug(f"Render service {service_id} polling, attempt {attempt + 1}")
    
    return ("timeout", None)
