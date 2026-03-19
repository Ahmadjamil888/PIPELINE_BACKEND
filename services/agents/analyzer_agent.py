"""
Agent 2: Deep Analyzer
Reads code in detail, finds env vars, configs, and potential issues.
"""
import logging
from services.ai_client import call_ai_json
from services.github_service import get_file_content

logger = logging.getLogger(__name__)


async def analyze_service(token: str, repo_full_name: str, service: dict) -> dict:
    """
    Agent 2: Deep analyze a single service — read code, find issues.
    
    Args:
        token: GitHub access token
        repo_full_name: e.g., "owner/repo"
        service: Service dict from scanner (name, path, framework, etc.)
        
    Returns:
        dict with detailed analysis including build commands, env vars, issues
    """
    service_path = service["path"]
    framework = service["framework"]
    
    logger.info(f"Analyzing service: {service['name']} ({framework}) at {service_path}")
    
    # Determine which files to read based on framework
    files_to_read = []
    
    if framework in ["nextjs", "react", "vue", "svelte", "angular"]:
        files_to_read = [
            f"{service_path}/package.json",
            f"{service_path}/next.config.js",
            f"{service_path}/next.config.ts",
            f"{service_path}/vite.config.js",
            f"{service_path}/vite.config.ts",
            f"{service_path}/.env.example",
            f"{service_path}/.env.local.example",
            f"{service_path}/src/app/layout.tsx",
            f"{service_path}/src/index.ts",
            f"{service_path}/src/main.tsx",
            f"{service_path}/public",
        ]
    elif framework in ["fastapi", "flask", "django"]:
        files_to_read = [
            f"{service_path}/requirements.txt",
            f"{service_path}/Pipfile",
            f"{service_path}/pyproject.toml",
            f"{service_path}/main.py",
            f"{service_path}/app.py",
            f"{service_path}/manage.py",
            f"{service_path}/.env.example",
            f"{service_path}/Dockerfile",
            f"{service_path}/docker-compose.yml",
            f"{service_path}/{service['name']}/settings.py",
        ]
    elif framework in ["express", "nestjs"]:
        files_to_read = [
            f"{service_path}/package.json",
            f"{service_path}/.env.example",
            f"{service_path}/src/index.js",
            f"{service_path}/src/main.ts",
            f"{service_path}/src/server.js",
            f"{service_path}/Dockerfile",
        ]
    elif framework in ["go"]:
        files_to_read = [
            f"{service_path}/go.mod",
            f"{service_path}/main.go",
            f"{service_path}/Dockerfile",
            f"{service_path}/.env.example",
        ]
    elif framework in ["rust"]:
        files_to_read = [
            f"{service_path}/Cargo.toml",
            f"{service_path}/src/main.rs",
            f"{service_path}/Dockerfile",
        ]
    
    # Read all relevant files
    file_contents = {}
    for path in files_to_read:
        try:
            content = await get_file_content(token, repo_full_name, path)
            if content:
                file_contents[path] = content[:2500]  # limit size
        except Exception as e:
            logger.debug(f"Could not read {path}: {e}")
    
    logger.info(f"Read {len(file_contents)} files for analysis")
    
    # Build prompt for AI
    contents_str = "\n\n".join([f"=== {k} ===\n{v}" for k, v in file_contents.items()])
    
    result = await call_ai_json([
        {
            "role": "system",
            "content": """You are a senior DevOps engineer. Analyze this service deeply.
Return JSON with this exact structure:
{
  "build_command": "exact build command or null",
  "start_command": "exact start command or null",
  "output_directory": "build output dir or null",
  "port": 3000,
  "node_version": "18" or null,
  "python_version": "3.11" or null,
  "env_vars": [
    {"key": "DATABASE_URL", "description": "PostgreSQL connection string", "required": true, "example": "postgresql://..."}
  ],
  "potential_issues": [
    {"issue": "Missing env var", "fix": "Add DATABASE_URL to deployment config"}
  ],
  "deployment_config": {
    "vercel": {"framework": "nextjs", "buildCommand": "npm run build", "outputDirectory": ".next"},
    "render": {"type": "web_service", "buildCommand": "pip install -r requirements.txt", "startCommand": "uvicorn main:app"}
  },
  "summary": "Brief summary of the service architecture"
}"""
        },
        {
            "role": "user",
            "content": f"Service: {service['name']} ({framework})\nPath: {service_path}\n\nFiles:\n{contents_str}"
        }
    ])
    
    logger.info(f"Analysis complete for {service['name']}")
    return result
