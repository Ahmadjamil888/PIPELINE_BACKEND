import os
import logging
from typing import Dict, Any, Optional

import httpx

logger = logging.getLogger(__name__)


class VercelDeployer:
    """Deployer for Vercel platform."""

    API_BASE = "https://api.vercel.com/v13"

    def __init__(self):
        self.token = os.getenv("VERCEL_TOKEN")
        if not self.token:
            logger.warning("VERCEL_TOKEN not set - Vercel deployments will fail")
        
        self.client = httpx.AsyncClient(
            base_url=self.API_BASE,
            headers={"Authorization": f"Bearer {self.token}"}
        )

    async def deploy_project(
        self,
        repo_url: str,
        path: str,
        project_name: Optional[str] = None,
        framework: Optional[str] = None,
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Deploy a project to Vercel.
        
        Args:
            repo_url: Repository URL
            path: Path within repository to deploy
            project_name: Optional Vercel project name (auto-generated if not provided)
            framework: Framework preset (e.g., 'nextjs', 'react')
            team_id: Optional team ID
            
        Returns:
            Deployment result with URL and deployment ID
        """
        if not self.token:
            raise RuntimeError("VERCEL_TOKEN not configured")
        
        logger.info(f"Deploying to Vercel: {repo_url} (path: {path})")
        
        # Generate project name if not provided
        if not project_name:
            from urllib.parse import urlparse
            parsed = urlparse(repo_url)
            repo_name = parsed.path.strip("/").split("/")[-1].replace(".git", "")
            path_slug = path.replace("/", "-").replace(".", "-").strip("-")
            project_name = f"{repo_name}-{path_slug}" if path_slug else repo_name
        
        try:
            # Create or get project
            project = await self._get_or_create_project(project_name, team_id, framework)
            project_id = project.get("id")
            
            # Deploy from Git
            deployment = await self._create_deployment(
                project_id=project_id,
                repo_url=repo_url,
                path=path,
                team_id=team_id
            )
            
            deployment_url = deployment.get("url")
            deployment_id = deployment.get("id")
            
            logger.info(f"Vercel deployment created: {deployment_url}")
            
            return {
                "url": f"https://{deployment_url}" if deployment_url else None,
                "deployment_id": deployment_id,
                "project_id": project_id,
                "status": deployment.get("status", "pending")
            }
            
        except httpx.HTTPError as e:
            logger.error(f"Vercel deployment failed: {e}")
            raise RuntimeError(f"Vercel deployment failed: {e}")

    async def _get_or_create_project(
        self,
        project_name: str,
        team_id: Optional[str] = None,
        framework: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get existing project or create new one."""
        
        # Check if project exists
        try:
            response = await self.client.get(
                "/projects/info",
                params={
                    "projectId": project_name,
                    **({"teamId": team_id} if team_id else {})
                }
            )
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        
        # Create new project
        json_body = {
            "name": project_name,
            "framework": framework,
        }
        
        if team_id:
            json_body["teamId"] = team_id
        
        response = await self.client.post(
            "/projects",
            json=json_body
        )
        response.raise_for_status()
        
        return response.json()

    async def _create_deployment(
        self,
        project_id: str,
        repo_url: str,
        path: str,
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new deployment."""
        
        json_body = {
            "name": project_id,
            "project": project_id,
            "gitSource": {
                "type": "github",  # or gitlab
                "repo": self._extract_repo_path(repo_url),
                "ref": "main"
            },
            "target": "production"
        }
        
        if path and path != "." and path != "./":
            json_body["source"] = path
        
        params = {}
        if team_id:
            params["teamId"] = team_id
        
        response = await self.client.post(
            "/deployments",
            json=json_body,
            params=params
        )
        response.raise_for_status()
        
        return response.json()

    def _extract_repo_path(self, repo_url: str) -> str:
        """Extract owner/repo from GitHub URL."""
        from urllib.parse import urlparse
        parsed = urlparse(repo_url)
        path = parsed.path.strip("/").replace(".git", "")
        return path

    async def get_deployment_status(
        self,
        deployment_id: str,
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get status of a deployment."""
        
        params = {}
        if team_id:
            params["teamId"] = team_id
        
        response = await self.client.get(
            f"/deployments/{deployment_id}",
            params=params
        )
        response.raise_for_status()
        
        return response.json()

    async def list_deployments(
        self,
        project_id: str,
        team_id: Optional[str] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """List deployments for a project."""
        
        params = {
            "projectId": project_id,
            "limit": limit
        }
        if team_id:
            params["teamId"] = team_id
        
        response = await self.client.get(
            "/deployments",
            params=params
        )
        response.raise_for_status()
        
        return response.json()
