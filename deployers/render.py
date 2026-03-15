import os
import logging
from typing import Dict, Any, Optional
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class RenderServiceType(str, Enum):
    WEB_SERVICE = "web_service"
    STATIC_SITE = "static_site"
    BACKGROUND_WORKER = "background_worker"
    CRON_JOB = "cron_job"


class RenderDeployer:
    """Deployer for Render platform."""

    API_BASE = "https://api.render.com/v1"

    def __init__(self):
        self.api_key = os.getenv("RENDER_API_KEY")
        if not self.api_key:
            logger.warning("RENDER_API_KEY not set - Render deployments will fail")
        
        self.client = httpx.AsyncClient(
            base_url=self.API_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json"
            }
        )

    async def deploy_service(
        self,
        repo_url: str,
        path: str,
        service_name: str,
        service_type: str = "web_service",
        start_command: Optional[str] = None,
        build_command: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Deploy a service to Render.
        
        Args:
            repo_url: Repository URL
            path: Path within repository (root if empty)
            service_name: Service name on Render
            service_type: Type of service (web_service, static_site, etc.)
            start_command: Command to start the service
            build_command: Command to build the service
            env_vars: Environment variables
            
        Returns:
            Deployment result with URL and service ID
        """
        if not self.api_key:
            raise RuntimeError("RENDER_API_KEY not configured")
        
        logger.info(f"Deploying to Render: {service_name} ({service_type})")
        
        try:
            # Get or create service
            service = await self._get_or_create_service(
                service_name=service_name,
                repo_url=repo_url,
                service_type=service_type,
                path=path,
                start_command=start_command,
                build_command=build_command,
                env_vars=env_vars
            )
            
            service_id = service.get("id")
            
            # Trigger a deploy
            deploy = await self._trigger_deploy(service_id)
            
            logger.info(f"Render service created: {service_id}")
            
            return {
                "url": service.get("service"),
                "service_id": service_id,
                "deploy_id": deploy.get("id") if deploy else None,
                "status": service.get("status", "created")
            }
            
        except httpx.HTTPError as e:
            logger.error(f"Render deployment failed: {e}")
            raise RuntimeError(f"Render deployment failed: {e}")

    async def _get_or_create_service(
        self,
        service_name: str,
        repo_url: str,
        service_type: str,
        path: str,
        start_command: Optional[str] = None,
        build_command: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Get existing service or create new one."""
        
        # Try to find existing service
        try:
            response = await self.client.get("/services", params={"name": service_name})
            response.raise_for_status()
            services = response.json()
            
            if services and len(services) > 0:
                logger.info(f"Found existing Render service: {service_name}")
                return services[0]
        except httpx.HTTPError as e:
            logger.debug(f"Could not list services: {e}")
        
        # Create new service
        service_config = self._build_service_config(
            service_name=service_name,
            repo_url=repo_url,
            service_type=service_type,
            path=path,
            start_command=start_command,
            build_command=build_command,
            env_vars=env_vars
        )
        
        response = await self.client.post(
            "/services",
            json=service_config
        )
        response.raise_for_status()
        
        return response.json()

    def _build_service_config(
        self,
        service_name: str,
        repo_url: str,
        service_type: str,
        path: str,
        start_command: Optional[str] = None,
        build_command: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Build service configuration for Render API."""
        
        # Parse repo URL
        owner, repo = self._parse_repo_url(repo_url)
        
        config = {
            "type": service_type,
            "name": service_name,
            "ownerId": owner,
            "repo": repo,
            "branch": "main",
            "autoDeploy": "yes",
            "envVars": [
                {"key": k, "value": v}
                for k, v in (env_vars or {}).items()
            ]
        }
        
        # Add path if not root
        if path and path != "." and path != "./":
            config["rootDir"] = path
        
        # Service-type specific settings
        if service_type == "web_service":
            config["serviceDetails"] = {
                "env": "docker",  # or 'node', 'python', 'go', etc.
                "plan": "starter",
            }
            if start_command:
                config["serviceDetails"]["startCommand"] = start_command
            if build_command:
                config["serviceDetails"]["buildCommand"] = build_command
                
        elif service_type == "static_site":
            config["serviceDetails"] = {
                "publishPath": "dist"  # or build output directory
            }
            if build_command:
                config["serviceDetails"]["buildCommand"] = build_command
        
        return config

    async def _trigger_deploy(self, service_id: str) -> Optional[Dict[str, Any]]:
        """Trigger a manual deploy for a service."""
        
        try:
            response = await self.client.post(
                f"/services/{service_id}/deploys",
                json={"clearCache": False}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to trigger deploy: {e}")
            return None

    def _parse_repo_url(self, repo_url: str) -> tuple:
        """Parse owner and repo from GitHub/GitLab URL."""
        from urllib.parse import urlparse
        parsed = urlparse(repo_url)
        parts = parsed.path.strip("/").replace(".git", "").split("/")
        
        if len(parts) >= 2:
            return parts[0], parts[1]
        return "", parts[0] if parts else ""

    async def get_service_status(self, service_id: str) -> Dict[str, Any]:
        """Get status of a service."""
        
        response = await self.client.get(f"/services/{service_id}")
        response.raise_for_status()
        
        return response.json()

    async def get_deploy_status(self, service_id: str, deploy_id: str) -> Dict[str, Any]:
        """Get status of a specific deploy."""
        
        response = await self.client.get(
            f"/services/{service_id}/deploys/{deploy_id}"
        )
        response.raise_for_status()
        
        return response.json()

    async def list_services(self, limit: int = 20) -> Dict[str, Any]:
        """List all services."""
        
        response = await self.client.get("/services", params={"limit": limit})
        response.raise_for_status()
        
        return response.json()

    async def update_environment_variables(
        self,
        service_id: str,
        env_vars: Dict[str, str]
    ) -> Dict[str, Any]:
        """Update environment variables for a service."""
        
        response = await self.client.put(
            f"/services/{service_id}/env-vars",
            json=[
                {"key": k, "value": v}
                for k, v in env_vars.items()
            ]
        )
        response.raise_for_status()
        
        return response.json()
