import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

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


class DeploymentPlanner:
    """AI agent for generating deployment plans based on repository analysis."""

    def __init__(self):
        self.platform_configs = {
            Platform.VERCEL: {
                "build_env": "VERCEL",
                "supports_ssr": True,
                "supports_static": True,
            },
            Platform.RENDER: {
                "supports_web_services": True,
                "supports_static_sites": True,
                "supports_background_workers": True,
            },
        }

    async def create_deployment_plan(
        self,
        request: DeploymentCreateRequest,
        repo_analysis: Optional[RepoAnalysis] = None
    ) -> DeploymentPlan:
        """
        Create a deployment plan based on repository analysis and user configuration.
        
        Args:
            request: Deployment creation request with service configurations
            repo_analysis: Optional repository analysis to auto-configure services
            
        Returns:
            DeploymentPlan with planned services and estimated durations
        """
        logger.info(f"Creating deployment plan for repo {request.repo_id}")
        
        planned_services = []
        
        for service_config in request.services:
            planned = self._plan_service(service_config, repo_analysis)
            planned_services.append(planned)
        
        plan = DeploymentPlan(
            id=datetime.now().timestamp(),  # Will be replaced with proper UUID
            repo_id=request.repo_id,
            status="planned",
            services=planned_services,
            environment=request.environment,
            branch=request.branch,
            created_at=datetime.now()
        )
        
        logger.info(f"Deployment plan created with {len(planned_services)} services")
        return plan

    def _plan_service(
        self,
        config: ServiceDeploymentConfig,
        repo_analysis: Optional[RepoAnalysis]
    ) -> PlannedService:
        """Plan a single service deployment."""
        
        # Estimate duration based on platform and service type
        estimated_duration = self._estimate_duration(config)
        
        # Enhance config with platform-specific settings if not provided
        if config.platform == Platform.VERCEL and not config.vercel_config:
            config.vercel_config = self._generate_vercel_config(config)
        
        if config.platform == Platform.RENDER and not config.render_config:
            config.render_config = self._generate_render_config(config)
        
        return PlannedService(
            name=config.name,
            path=config.path,
            platform=config.platform,
            status=ServiceDeploymentStatus.PENDING,
            estimated_duration_seconds=estimated_duration
        )

    def _estimate_duration(self, config: ServiceDeploymentConfig) -> int:
        """Estimate deployment duration in seconds based on service configuration."""
        base_duration = 60  # Base: 1 minute
        
        # Platform-specific adjustments
        if config.platform == Platform.VERCEL:
            base_duration = 120  # Vercel typically faster
        elif config.platform == Platform.RENDER:
            base_duration = 180  # Render builds take longer
        
        # Add time for complex build commands
        if config.build_command:
            if "npm" in config.build_command or "yarn" in config.build_command:
                base_duration += 60
            if "cargo" in config.build_command:
                base_duration += 120  # Rust builds are slower
            if "go build" in config.build_command:
                base_duration += 30
        
        return base_duration

    def _generate_vercel_config(
        self,
        config: ServiceDeploymentConfig
    ) -> VercelDeploymentConfig:
        """Generate Vercel-specific deployment configuration."""
        project_name = config.name.lower().replace(" ", "-").replace("_", "-")
        
        return VercelDeploymentConfig(
            project_name=project_name,
            framework=self._detect_framework_for_vercel(config)
        )

    def _generate_render_config(
        self,
        config: ServiceDeploymentConfig
    ) -> RenderDeploymentConfig:
        """Generate Render-specific deployment configuration."""
        service_name = config.name.lower().replace(" ", "-").replace("_", "-")
        
        # Determine service type based on configuration
        service_type = "web_service"
        if not config.start_command:
            service_type = "static_site"
        
        return RenderDeploymentConfig(
            service_name=service_name,
            service_type=service_type,
            plan="starter"
        )

    def _detect_framework_for_vercel(
        self,
        config: ServiceDeploymentConfig
    ) -> Optional[str]:
        """Detect framework for Vercel configuration."""
        framework_map = {
            "nextjs": "nextjs",
            "react": "create-react-app",
            "vue": "vue",
            "angular": "angular",
            "svelte": "svelte",
            "nuxtjs": "nuxtjs",
            "remix": "remix",
        }
        
        # Infer from build command or path
        build_cmd = config.build_command or ""
        if "next" in build_cmd:
            return "nextjs"
        if "gatsby" in build_cmd:
            return "gatsby"
        if "nuxt" in build_cmd:
            return "nuxtjs"
        
        return None

    async def generate_build_script(
        self,
        services: List[ServiceDeploymentConfig],
        repo_analysis: Optional[RepoAnalysis]
    ) -> str:
        """
        Generate a build script for the entire deployment.
        
        Args:
            services: List of service configurations
            repo_analysis: Repository analysis for context
            
        Returns:
            Shell script as a string
        """
        script_lines = [
            "#!/bin/bash",
            "set -e",
            "",
            "echo 'Starting deployment build process...'",
            "",
        ]
        
        for service in services:
            script_lines.extend([
                f"echo 'Building service: {service.name}'",
                f"cd /workspace/repo/{service.path}",
                "",
            ])
            
            if service.build_command:
                script_lines.extend([
                    f"echo 'Running build command: {service.build_command}'",
                    service.build_command,
                    "",
                ])
            
            # Add platform-specific steps
            if service.platform == Platform.VERCEL:
                script_lines.extend([
                    "echo 'Preparing for Vercel deployment'",
                    "if [ -f vercel.json ]; then echo 'Using existing vercel.json'; fi",
                    "",
                ])
            
            elif service.platform == Platform.RENDER:
                script_lines.extend([
                    "echo 'Preparing for Render deployment'",
                    "if [ -f render.yaml ]; then echo 'Using existing render.yaml'; fi",
                    "",
                ])
        
        script_lines.extend([
            "echo 'Build process completed successfully'",
            "exit 0",
        ])
        
        return "\n".join(script_lines)

    async def generate_deploy_script(
        self,
        services: List[ServiceDeploymentConfig],
        environment: Environment
    ) -> str:
        """
        Generate a deployment script for executing deployments.
        
        Args:
            services: List of service configurations
            environment: Deployment environment
            
        Returns:
            Shell script as a string
        """
        script_lines = [
            "#!/bin/bash",
            "set -e",
            "",
            f"echo 'Starting deployment to {environment} environment'",
            "",
        ]
        
        for service in services:
            script_lines.extend([
                f"echo 'Deploying service: {service.name}'",
                f"cd /workspace/repo/{service.path}",
                "",
            ])
            
            if service.platform == Platform.VERCEL:
                script_lines.extend([
                    "# Vercel deployment",
                    "if command -v vercel &> /dev/null; then",
                    f"  vercel --prod --yes --token $VERCEL_TOKEN",
                    "else",
                    "  echo 'Vercel CLI not available'",
                    "  exit 1",
                    "fi",
                    "",
                ])
            
            elif service.platform == Platform.RENDER:
                script_lines.extend([
                    "# Render deployment via API",
                    "echo 'Deploying to Render via API...'",
                    "# Render deployments are handled via API calls",
                    "",
                ])
        
        script_lines.extend([
            "echo 'All services deployed successfully'",
            "exit 0",
        ])
        
        return "\n".join(script_lines)

    def validate_plan(self, plan: DeploymentPlan) -> List[str]:
        """
        Validate a deployment plan and return any issues.
        
        Args:
            plan: Deployment plan to validate
            
        Returns:
            List of validation issues (empty if valid)
        """
        issues = []
        
        for service in plan.services:
            # Check for conflicting names
            if not service.name or len(service.name) < 2:
                issues.append(f"Service name '{service.name}' is too short")
            
            # Check platform compatibility
            if service.platform not in [Platform.VERCEL, Platform.RENDER, Platform.DOCKER]:
                issues.append(f"Unsupported platform: {service.platform}")
        
        return issues
