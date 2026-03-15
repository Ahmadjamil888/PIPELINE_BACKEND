import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import uuid4, UUID

from sandbox.daytona_manager import DaytonaManager
from deployers.vercel import VercelDeployer
from deployers.render import RenderDeployer
from schemas import (
    DeploymentStatus,
    DeploymentPlan,
    ServiceDeployment,
    ServiceDeploymentStatus,
    DeploymentStatus as DeploymentStatusEnum,
    LogEntry,
    LogLevel
)

logger = logging.getLogger(__name__)


class DeploymentRunner:
    """AI agent for executing deployments in Daytona sandboxes."""

    def __init__(
        self,
        daytona_manager: DaytonaManager,
        vercel_deployer: VercelDeployer,
        render_deployer: RenderDeployer
    ):
        self.daytona = daytona_manager
        self.vercel = vercel_deployer
        self.render = render_deployer

    async def run_deployment(
        self,
        deployment_id: UUID,
        plan: DeploymentPlan,
        repo_url: str,
        branch: str
    ) -> DeploymentStatus:
        """
        Execute a deployment plan in a Daytona sandbox.
        
        Args:
            deployment_id: Unique deployment identifier
            plan: Deployment plan to execute
            repo_url: URL of the repository
            branch: Branch to deploy
            
        Returns:
            DeploymentStatus with results
        """
        logger.info(f"Starting deployment execution: {deployment_id}")
        
        # Initialize deployment status
        status = DeploymentStatus(
            id=deployment_id,
            repo_id=plan.repo_id,
            status=DeploymentStatusEnum.PENDING,
            services=[
                ServiceDeployment(
                    name=s.name,
                    path=s.path,
                    platform=s.platform,
                    status=ServiceDeploymentStatus.PENDING
                )
                for s in plan.services
            ],
            environment=plan.environment,
            branch=branch,
            started_at=datetime.now()
        )
        
        # Create sandbox
        try:
            sandbox = await self.daytona.create_workspace(
                repo_url=repo_url,
                branch=branch
            )
            status.sandbox_id = sandbox.id
            status.status = DeploymentStatusEnum.RUNNING
            
            # Clone repository
            await self._clone_repository(sandbox.id, repo_url, branch)
            
            # Build and deploy each service
            for i, service_plan in enumerate(plan.services):
                service_status = status.services[i]
                
                try:
                    service_status.status = ServiceDeploymentStatus.BUILDING
                    service_status.started_at = datetime.now()
                    
                    # Build the service
                    await self._build_service(sandbox.id, service_plan)
                    
                    service_status.status = ServiceDeploymentStatus.DEPLOYING
                    
                    # Deploy to platform
                    deployment_result = await self._deploy_service(
                        sandbox.id,
                        service_plan,
                        repo_url,
                        plan.environment
                    )
                    
                    service_status.status = ServiceDeploymentStatus.DEPLOYED
                    service_status.deployment_url = deployment_result.get("url")
                    service_status.platform_deployment_id = deployment_result.get("platform_id")
                    service_status.completed_at = datetime.now()
                    
                    logger.info(f"Service {service_plan.name} deployed successfully")
                    
                except Exception as e:
                    logger.error(f"Failed to deploy service {service_plan.name}: {e}")
                    service_status.status = ServiceDeploymentStatus.FAILED
                    service_status.error_message = str(e)
                    service_status.completed_at = datetime.now()
            
            # Update overall status
            failed_services = [s for s in status.services if s.status == ServiceDeploymentStatus.FAILED]
            if failed_services:
                status.status = DeploymentStatusEnum.FAILED
                status.error_message = f"{len(failed_services)} service(s) failed to deploy"
            else:
                status.status = DeploymentStatusEnum.SUCCEEDED
            
            status.completed_at = datetime.now()
            if status.started_at:
                status.duration_seconds = int((status.completed_at - status.started_at).total_seconds())
            
        except Exception as e:
            logger.error(f"Deployment execution failed: {e}")
            status.status = DeploymentStatusEnum.FAILED
            status.error_message = str(e)
            status.completed_at = datetime.now()
        
        finally:
            # Cleanup sandbox on success, keep on failure for debugging
            if status.status == DeploymentStatusEnum.SUCCEEDED and status.sandbox_id:
                try:
                    await self.daytona.destroy_workspace(str(status.sandbox_id))
                except Exception as e:
                    logger.warning(f"Failed to cleanup sandbox: {e}")
        
        return status

    async def retry_deployment(
        self,
        deployment_id: UUID,
        plan: DeploymentPlan,
        repo_url: str,
        branch: str,
        retry_count: int = 1
    ) -> DeploymentStatus:
        """Retry a failed deployment."""
        logger.info(f"Retrying deployment {deployment_id} (attempt {retry_count})")
        
        result = await self.run_deployment(deployment_id, plan, repo_url, branch)
        result.retry_count = retry_count
        result.status = DeploymentStatusEnum.RETRYING if result.status != DeploymentStatusEnum.SUCCEEDED else result.status
        
        return result

    async def _clone_repository(
        self,
        sandbox_id: str,
        repo_url: str,
        branch: str
    ) -> None:
        """Clone the repository into the sandbox."""
        logger.info(f"Cloning repository {repo_url} (branch: {branch})")
        
        result = await self.daytona.run_command(
            sandbox_id,
            f"git clone -b {branch} --depth 1 {repo_url} /workspace/repo"
        )
        
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to clone repository: {result.stderr}")
        
        logger.info("Repository cloned successfully")

    async def _build_service(
        self,
        sandbox_id: str,
        service_plan
    ) -> None:
        """Build a service in the sandbox."""
        logger.info(f"Building service: {service_plan.name}")
        
        service_path = f"/workspace/repo/{service_plan.path}"
        
        # Install dependencies based on detected files
        pkg_result = await self.daytona.run_command(
            sandbox_id,
            f"ls {service_path}/package.json 2>/dev/null || echo 'NOT_FOUND'"
        )
        
        if "NOT_FOUND" not in pkg_result.stdout:
            # Node.js project
            logger.info("Installing Node.js dependencies")
            install_result = await self.daytona.run_command(
                sandbox_id,
                f"cd {service_path} && npm install",
                timeout_seconds=300
            )
            if install_result.exit_code != 0:
                raise RuntimeError(f"npm install failed: {install_result.stderr}")
        
        # Run build command if specified
        if service_plan.build_command:
            logger.info(f"Running build command: {service_plan.build_command}")
            build_result = await self.daytona.run_command(
                sandbox_id,
                f"cd {service_path} && {service_plan.build_command}",
                timeout_seconds=600
            )
            if build_result.exit_code != 0:
                raise RuntimeError(f"Build failed: {build_result.stderr}")

    async def _deploy_service(
        self,
        sandbox_id: str,
        service_plan,
        repo_url: str,
        environment: str
    ) -> Dict[str, Any]:
        """Deploy a service to its target platform."""
        logger.info(f"Deploying service {service_plan.name} to {service_plan.platform}")
        
        if service_plan.platform.value == "vercel":
            return await self._deploy_to_vercel(
                sandbox_id,
                service_plan,
                repo_url
            )
        elif service_plan.platform.value == "render":
            return await self._deploy_to_render(
                sandbox_id,
                service_plan,
                repo_url,
                environment
            )
        else:
            raise ValueError(f"Unsupported platform: {service_plan.platform}")

    async def _deploy_to_vercel(
        self,
        sandbox_id: str,
        service_plan,
        repo_url: str
    ) -> Dict[str, Any]:
        """Deploy service to Vercel."""
        service_path = f"/workspace/repo/{service_plan.path}"
        
        # Use vercel deployer
        result = await self.vercel.deploy_project(
            repo_url=repo_url,
            path=service_path,
            project_name=service_plan.vercel_config.project_name if service_plan.vercel_config else None
        )
        
        return {
            "url": result.get("url"),
            "platform_id": result.get("deployment_id")
        }

    async def _deploy_to_render(
        self,
        sandbox_id: str,
        service_plan,
        repo_url: str,
        environment: str
    ) -> Dict[str, Any]:
        """Deploy service to Render."""
        service_path = f"/workspace/repo/{service_plan.path}"
        
        # Use render deployer
        config = service_plan.render_config
        result = await self.render.deploy_service(
            repo_url=repo_url,
            path=service_path,
            service_name=config.service_name if config else service_plan.name,
            service_type=config.service_type if config else "web_service",
            start_command=service_plan.start_command
        )
        
        return {
            "url": result.get("url"),
            "platform_id": result.get("service_id")
        }

    async def get_deployment_logs(
        self,
        deployment_id: UUID,
        sandbox_id: Optional[UUID] = None,
        tail: int = 100
    ) -> List[LogEntry]:
        """Retrieve logs for a deployment."""
        if not sandbox_id:
            return []
        
        try:
            logs_data = await self.daytona.get_logs(str(sandbox_id))
            
            # Convert to LogEntry format
            entries = []
            for log in logs_data[-tail:]:
                entries.append(LogEntry(
                    timestamp=datetime.fromisoformat(log.get("timestamp", datetime.now().isoformat())),
                    level=LogLevel(log.get("level", "info")),
                    message=log.get("message", ""),
                    source=log.get("source")
                ))
            
            return entries
        except Exception as e:
            logger.error(f"Failed to get deployment logs: {e}")
            return []
