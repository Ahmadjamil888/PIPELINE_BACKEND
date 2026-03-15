import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import uuid4

from agents.ai_client import OpenRouterClient
from schemas import (
    RepoAnalysis,
    DeploymentPlan,
    DeploymentCreateRequest,
    PlannedService,
    ServiceDeploymentConfig,
    VercelDeploymentConfig,
    RenderDeploymentConfig,
    Platform,
    ServiceDeploymentStatus,
    Environment
)

logger = logging.getLogger(__name__)


class AIDeploymentPlanner:
    """AI-enhanced deployment planner using OpenRouter/DeepSeek."""

    def __init__(self, ai_client: Optional[OpenRouterClient] = None):
        self.ai = ai_client or OpenRouterClient()

    async def create_deployment_plan(
        self,
        request: DeploymentCreateRequest,
        repo_analysis: Optional[RepoAnalysis] = None
    ) -> DeploymentPlan:
        """
        Create an AI-optimized deployment plan.
        """
        logger.info(f"Creating AI deployment plan for repo {request.repo_id}")
        
        # Get AI deployment strategy
        services_data = [
            {
                "name": s.name,
                "path": s.path,
                "platform": s.platform.value,
                "framework": repo_analysis.services[i].framework.value if repo_analysis and i < len(repo_analysis.services) else "unknown"
            }
            for i, s in enumerate(request.services)
        ]
        
        ai_plan = await self.ai.generate_deployment_plan(
            services=services_data,
            environment=request.environment.value,
            constraints={"budget": "optimized", "parallel": True}
        )
        
        # Create planned services with AI estimates
        planned_services = []
        for svc in request.services:
            ai_svc = next((s for s in ai_plan.get("stages", []) if svc.name in s.get("services", [])), {})
            
            planned_services.append(PlannedService(
                name=svc.name,
                path=svc.path,
                platform=svc.platform,
                status=ServiceDeploymentStatus.PENDING,
                estimated_duration_seconds=ai_svc.get("estimated_duration_minutes", 3) * 60
            ))
        
        plan = DeploymentPlan(
            id=uuid4(),
            repo_id=request.repo_id,
            status="planned",
            services=planned_services,
            environment=request.environment,
            branch=request.branch,
            created_at=datetime.now()
        )
        
        logger.info(f"AI deployment plan created: {plan.id}")
        return plan

    def validate_plan(self, plan: DeploymentPlan) -> List[str]:
        """Validate a deployment plan."""
        issues = []
        
        # Check for service name conflicts
        names = [s.name for s in plan.services]
        if len(names) != len(set(names)):
            issues.append("Duplicate service names detected")
        
        # Check platform compatibility
        for service in plan.services:
            if service.platform not in [Platform.VERCEL, Platform.RENDER, Platform.DOCKER]:
                issues.append(f"Unsupported platform: {service.platform}")
        
        return issues

    async def generate_deployment_scripts(
        self,
        plan: DeploymentPlan,
        repo_analysis: RepoAnalysis
    ) -> Dict[str, str]:
        """Generate build and deploy scripts using AI."""
        
        # Build script
        build_steps = []
        for service in plan.services:
            svc_info = next((s for s in repo_analysis.services if s.name == service.name), None)
            if svc_info:
                build_steps.append({
                    "name": service.name,
                    "path": service.path,
                    "build_command": svc_info.build_command or "npm run build",
                    "install_command": "npm install" if svc_info.language in ["javascript", "typescript"] else "pip install -r requirements.txt"
                })
        
        build_script = self._generate_build_script(build_steps)
        deploy_script = self._generate_deploy_script(plan.services)
        
        return {
            "build": build_script,
            "deploy": deploy_script
        }

    def _generate_build_script(self, steps: List[Dict]) -> str:
        """Generate build shell script."""
        lines = [
            "#!/bin/bash",
            "set -e",
            "echo 'Starting build process...'",
            ""
        ]
        
        for step in steps:
            lines.extend([
                f"echo 'Building {step['name']}...'",
                f"cd /workspace/repo/{step['path']}",
                f"{step['install_command']}",
                f"{step['build_command']}" if step['build_command'] else "echo 'No build command'",
                ""
            ])
        
        lines.append("echo 'Build complete!'")
        return "\n".join(lines)

    def _generate_deploy_script(self, services: List[PlannedService]) -> str:
        """Generate deployment shell script."""
        lines = [
            "#!/bin/bash",
            "set -e",
            "echo 'Starting deployment...'",
            ""
        ]
        
        for svc in services:
            lines.extend([
                f"echo 'Deploying {svc.name} to {svc.platform}...'",
                f"cd /workspace/repo/{svc.path}",
                f"# Deploy to {svc.platform}",
                ""
            ])
        
        lines.append("echo 'Deployment complete!'")
        return "\n".join(lines)
