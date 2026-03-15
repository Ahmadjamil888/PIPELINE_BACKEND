import os
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

from sandbox.daytona_manager import DaytonaManager
from agents.ai_client import OpenRouterClient
from schemas import DetectedService, Framework, Language, Platform, RepoAnalysis

logger = logging.getLogger(__name__)


class RepoAnalyzer:
    """Advanced AI agent for analyzing repository structure with OpenRouter/DeepSeek integration."""

    FRAMEWORK_PATTERNS = {
        # Frontend frameworks
        "nextjs": {
            "files": ["next.config.js", "next.config.mjs", "next.config.ts"],
            "dependencies": ["next"],
            "language": Language.TYPESCRIPT,
            "platform": Platform.VERCEL,
        },
        "react": {
            "files": ["vite.config.ts", "vite.config.js", "craco.config.js"],
            "dependencies": ["react", "react-dom"],
            "language": Language.JAVASCRIPT,
            "platform": Platform.VERCEL,
        },
        "vue": {
            "files": ["vue.config.js", "vite.config.ts"],
            "dependencies": ["vue"],
            "language": Language.JAVASCRIPT,
            "platform": Platform.VERCEL,
        },
        "angular": {
            "files": ["angular.json"],
            "dependencies": ["@angular/core"],
            "language": Language.TYPESCRIPT,
            "platform": Platform.VERCEL,
        },
        "svelte": {
            "files": ["svelte.config.js"],
            "dependencies": ["svelte"],
            "language": Language.JAVASCRIPT,
            "platform": Platform.VERCEL,
        },
        "nuxtjs": {
            "files": ["nuxt.config.ts", "nuxt.config.js"],
            "dependencies": ["nuxt"],
            "language": Language.TYPESCRIPT,
            "platform": Platform.VERCEL,
        },
        "remix": {
            "files": ["remix.config.js"],
            "dependencies": ["@remix-run/core"],
            "language": Language.TYPESCRIPT,
            "platform": Platform.VERCEL,
        },
        # Backend frameworks
        "fastapi": {
            "files": ["main.py", "app.py", "requirements.txt", "pyproject.toml"],
            "dependencies": ["fastapi", "starlette"],
            "language": Language.PYTHON,
            "platform": Platform.RENDER,
        },
        "flask": {
            "files": ["app.py", "wsgi.py", "requirements.txt"],
            "dependencies": ["flask", "werkzeug"],
            "language": Language.PYTHON,
            "platform": Platform.RENDER,
        },
        "django": {
            "files": ["manage.py", "settings.py", "requirements.txt"],
            "dependencies": ["django"],
            "language": Language.PYTHON,
            "platform": Platform.RENDER,
        },
        "express": {
            "files": ["server.js", "app.js", "package.json"],
            "dependencies": ["express"],
            "language": Language.JAVASCRIPT,
            "platform": Platform.RENDER,
        },
        "nestjs": {
            "files": ["nest-cli.json"],
            "dependencies": ["@nestjs/core"],
            "language": Language.TYPESCRIPT,
            "platform": Platform.RENDER,
        },
        # Compiled languages
        "go": {
            "files": ["go.mod", "main.go"],
            "dependencies": [],
            "language": Language.GO,
            "platform": Platform.RENDER,
        },
        "rust": {
            "files": ["Cargo.toml"],
            "dependencies": [],
            "language": Language.RUST,
            "platform": Platform.RENDER,
        },
    }

    MONOREPO_PATTERNS = {
        "turborepo": ["turbo.json"],
        "nx": ["nx.json", "project.json"],
        "pnpm_workspace": ["pnpm-workspace.yaml"],
        "lerna": ["lerna.json"],
    }

    def __init__(self, daytona_manager: DaytonaManager, ai_client: Optional[OpenRouterClient] = None):
        self.daytona = daytona_manager
        self.ai = ai_client or OpenRouterClient()

    async def analyze_repository(
        self,
        repo_url: str,
        branch: str = "main",
        sandbox_id: Optional[str] = None,
        use_ai: bool = True
    ) -> RepoAnalysis:
        """
        Analyze a repository to detect services, frameworks, and deployment configuration.
        
        Args:
            repo_url: URL of the Git repository
            branch: Branch to analyze
            sandbox_id: Optional existing sandbox ID to use
            use_ai: Whether to use AI for enhanced monorepo analysis
            
        Returns:
            RepoAnalysis with detected services and configuration
        """
        logger.info(f"Starting AI-enhanced analysis for {repo_url} (branch: {branch})")
        
        # Create or use existing sandbox
        if sandbox_id:
            sandbox = await self.daytona.get_sandbox(sandbox_id)
        else:
            sandbox = await self.daytona.create_workspace(repo_url=repo_url, branch=branch)
        
        try:
            # Clone repo if not already cloned
            clone_result = await self.daytona.run_command(
                sandbox.id,
                f"git clone -b {branch} {repo_url} /workspace/repo || echo 'Repo already exists'"
            )
            logger.debug(f"Clone result: {clone_result}")
            
            # Get repository structure
            structure = await self._get_repo_structure(sandbox.id)
            
            # Detect monorepo tools
            detected_workspaces = self._detect_workspace_tools(structure)
            is_monorepo = len(detected_workspaces) > 0 or len([f for f in structure if f.endswith('package.json')]) > 1
            
            # Detect services - use AI for monorepos
            services = []
            if use_ai and is_monorepo:
                logger.info("Using AI for monorepo breakdown")
                package_files = await self._get_package_files(sandbox.id, structure)
                ai_analysis = await self.ai.analyze_monorepo(structure, package_files)
                services = self._parse_ai_services(ai_analysis)
                
                # Enhance with AI-suggested env vars
                services = await self._enhance_with_ai_env_vars(services, sandbox.id)
            else:
                services = await self._detect_services(sandbox.id, structure)
            
            # Get root config
            root_config = await self._get_root_config(sandbox.id)
            
            analysis = RepoAnalysis(
                repo_id=sandbox.repo_id if sandbox.repo_id else sandbox.id,
                services=services,
                is_monorepo=is_monorepo,
                detected_workspaces=detected_workspaces,
                root_config=root_config,
                analyzed_at=datetime.now(),
                sandbox_id=sandbox.id
            )
            
            logger.info(f"Analysis complete: {len(services)} services detected")
            return analysis
            
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            raise

    async def _get_repo_structure(self, sandbox_id: str) -> List[str]:
        """Get the file structure of the repository."""
        result = await self.daytona.run_command(
            sandbox_id,
            "find /workspace/repo -type f 2>/dev/null | sed 's|/workspace/repo/||' | head -200"
        )
        return [f for f in result.stdout.split("\n") if f and not f.startswith('.git')]

    async def _get_package_files(self, sandbox_id: str, structure: List[str]) -> Dict[str, str]:
        """Get package.json and other config files for AI analysis."""
        package_files = {}
        for file in structure:
            if file.endswith("package.json") or file.endswith("pyproject.toml") or file.endswith("requirements.txt") or file.endswith("go.mod") or file.endswith("Cargo.toml"):
                result = await self.daytona.run_command(
                    sandbox_id,
                    f"cat /workspace/repo/{file} 2>/dev/null || echo ''"
                )
                if result.stdout:
                    package_files[file] = result.stdout[:2000]
        return package_files

    def _parse_ai_services(self, ai_analysis: Dict) -> List[DetectedService]:
        """Parse AI monorepo analysis into DetectedService objects."""
        services = []
        for svc in ai_analysis.get("services", []):
            try:
                framework = Framework(svc.get("framework", "unknown"))
            except ValueError:
                framework = Framework.UNKNOWN
            
            lang_map = {
                "nextjs": Language.TYPESCRIPT, "react": Language.JAVASCRIPT,
                "fastapi": Language.PYTHON, "django": Language.PYTHON, "flask": Language.PYTHON,
                "express": Language.JAVASCRIPT, "go": Language.GO, "rust": Language.RUST
            }
            
            services.append(DetectedService(
                name=svc.get("name", "unnamed"),
                framework=framework,
                path=svc.get("path", "."),
                language=lang_map.get(svc.get("framework"), Language.UNKNOWN),
                recommended_platform=Platform(svc.get("deploy_platform", "docker")),
                detected_files=[],
                build_command=svc.get("build_command"),
                start_command=svc.get("start_command"),
                env_variables=svc.get("env_vars", [])
            ))
        return services

    async def _enhance_with_ai_env_vars(self, services: List[DetectedService], sandbox_id: str) -> List[DetectedService]:
        """Use AI to detect environment variables for each service."""
        for service in services:
            if service.env_variables:
                continue
            code_samples = await self._get_code_samples(sandbox_id, service.path)
            try:
                env_vars = await self.ai.suggest_environment_variables(
                    service.name, code_samples, service.framework.value
                )
                service.env_variables = [v["name"] for v in env_vars if v.get("required")]
            except Exception as e:
                logger.warning(f"AI env var detection failed for {service.name}: {e}")
        return services

    async def _get_code_samples(self, sandbox_id: str, path: str) -> List[str]:
        """Get code samples for AI analysis."""
        full_path = f"/workspace/repo/{path}"
        result = await self.daytona.run_command(
            sandbox_id,
            f"find {full_path} -type f \\( -name '*.js' -o -name '*.ts' -o -name '*.py' -o -name '*.go' \\) 2>/dev/null | head -3"
        )
        samples = []
        for file in result.stdout.split("\n")[:2]:
            if file:
                content = await self.daytona.run_command(sandbox_id, f"cat {file} 2>/dev/null || echo ''")
                if content.stdout:
                    samples.append(content.stdout[:1500])
        return samples

    def _detect_workspace_tools(self, structure: List[str]) -> List[str]:
        """Detect monorepo workspace tools."""
        detected = []
        for tool, patterns in self.MONOREPO_PATTERNS.items():
            for pattern in patterns:
                if any(pattern in file for file in structure):
                    detected.append(tool)
                    break
        return detected

    async def _detect_services(self, sandbox_id: str, structure: List[str]) -> List[DetectedService]:
        """Detect services in the repository."""
        services = []
        
        # Check for services in subdirectories
        result = await self.daytona.run_command(
            sandbox_id,
            "find /workspace/repo -mindepth 1 -maxdepth 2 -type d | grep -v '.git' | head -20"
        )
        
        directories = [d.strip() for d in result.stdout.split("\n") if d.strip()]
        
        # Always check root
        directories.insert(0, "/workspace/repo")
        
        for directory in directories:
            service_name = Path(directory).name if directory != "/workspace/repo" else "root"
            service_path = str(Path(directory).relative_to("/workspace/repo")) if directory != "/workspace/repo" else ""
            
            detected = await self._detect_framework_in_directory(sandbox_id, directory)
            if detected:
                services.append(detected)
        
        return services

    async def _detect_framework_in_directory(
        self, 
        sandbox_id: str, 
        directory: str
    ) -> Optional[DetectedService]:
        """Detect framework in a specific directory."""
        service_name = Path(directory).name if directory != "/workspace/repo" else "app"
        service_path = str(Path(directory).relative_to("/workspace/repo")) if directory != "/workspace/repo" else "."
        
        # Check for package.json (Node.js)
        pkg_result = await self.daytona.run_command(
            sandbox_id,
            f"cat {directory}/package.json 2>/dev/null || echo 'NOT_FOUND'"
        )
        
        if pkg_result.stdout != "NOT_FOUND":
            try:
                pkg_data = json.loads(pkg_result.stdout)
                deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                
                for framework, config in self.FRAMEWORK_PATTERNS.items():
                    if any(dep in deps for dep in config["dependencies"]):
                        detected_files = config["files"] + ["package.json"]
                        return DetectedService(
                            name=service_name,
                            framework=Framework(framework),
                            path=service_path,
                            language=config["language"],
                            recommended_platform=config["platform"],
                            detected_files=[f for f in detected_files if f in str(deps) or f == "package.json"],
                            build_command=pkg_data.get("scripts", {}).get("build"),
                            start_command=pkg_data.get("scripts", {}).get("start") or pkg_data.get("scripts", {}).get("dev"),
                            env_variables=self._extract_env_vars(pkg_result.stdout)
                        )
            except json.JSONDecodeError:
                pass
        
        # Check for Python projects
        pyproject_result = await self.daytona.run_command(
            sandbox_id,
            f"cat {directory}/pyproject.toml 2>/dev/null || cat {directory}/requirements.txt 2>/dev/null || echo 'NOT_FOUND'"
        )
        
        if pyproject_result.stdout != "NOT_FOUND":
            content = pyproject_result.stdout.lower()
            for framework, config in self.FRAMEWORK_PATTERNS.items():
                if config["language"] == Language.PYTHON:
                    if any(dep in content for dep in config["dependencies"]):
                        return DetectedService(
                            name=service_name,
                            framework=Framework(framework),
                            path=service_path,
                            language=Language.PYTHON,
                            recommended_platform=config["platform"],
                            detected_files=[f for f in config["files"] if f in ["pyproject.toml", "requirements.txt", "main.py"]],
                            build_command="pip install -r requirements.txt" if framework in ["fastapi", "flask"] else None,
                            start_command=f"python main.py" if Path(f"{directory}/main.py").exists() else None,
                            env_variables=[]
                        )
        
        # Check for Go projects
        gomod_result = await self.daytona.run_command(
            sandbox_id,
            f"cat {directory}/go.mod 2>/dev/null || echo 'NOT_FOUND'"
        )
        
        if gomod_result.stdout != "NOT_FOUND":
            return DetectedService(
                name=service_name,
                framework=Framework.GO,
                path=service_path,
                language=Language.GO,
                recommended_platform=Platform.RENDER,
                detected_files=["go.mod", "main.go"],
                build_command="go build -o app",
                start_command="./app",
                env_variables=[]
            )
        
        # Check for Rust projects
        cargo_result = await self.daytona.run_command(
            sandbox_id,
            f"cat {directory}/Cargo.toml 2>/dev/null || echo 'NOT_FOUND'"
        )
        
        if cargo_result.stdout != "NOT_FOUND":
            return DetectedService(
                name=service_name,
                framework=Framework.RUST,
                path=service_path,
                language=Language.RUST,
                recommended_platform=Platform.RENDER,
                detected_files=["Cargo.toml"],
                build_command="cargo build --release",
                start_command="./target/release/app",
                env_variables=[]
            )
        
        return None

    def _extract_env_vars(self, content: str) -> List[str]:
        """Extract environment variable patterns from code."""
        env_vars = []
        # Simple pattern matching for common env var patterns
        if "process.env" in content or "import.meta.env" in content:
            env_vars.append("NODE_ENV")
        if "DATABASE" in content.upper():
            env_vars.append("DATABASE_URL")
        if "API" in content.upper():
            env_vars.append("API_URL")
        return env_vars

    async def _get_root_config(self, sandbox_id: str) -> Dict[str, Any]:
        """Get root-level configuration files."""
        config = {}
        
        files_to_check = [
            "/workspace/repo/package.json",
            "/workspace/repo/pyproject.toml",
            "/workspace/repo/requirements.txt",
            "/workspace/repo/Dockerfile",
            "/workspace/repo/docker-compose.yml",
            "/workspace/repo/.env.example",
        ]
        
        for file_path in files_to_check:
            result = await self.daytona.run_command(
                sandbox_id,
                f"cat {file_path} 2>/dev/null || echo 'NOT_FOUND'"
            )
            if result.stdout != "NOT_FOUND":
                config[Path(file_path).name] = result.stdout[:1000]  # Limit size
        
        return config


# Import datetime here to avoid circular import
from datetime import datetime
