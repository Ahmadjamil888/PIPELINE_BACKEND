"""
Agent 1: Repo Scanner
Detects repository structure, services, frameworks, and configuration.
"""
import logging
from services.ai_client import call_ai_json
from services.github_service import get_repo_file_tree, get_file_content

logger = logging.getLogger(__name__)

IMPORTANT_FILES = [
    "package.json", "requirements.txt", "Pipfile", "pyproject.toml",
    "next.config.js", "next.config.ts", "vite.config.js", "vite.config.ts",
    "docker-compose.yml", "Dockerfile", "turbo.json", "nx.json",
    ".env.example", ".env.sample", "render.yaml", "vercel.json",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle",
    "app.py", "main.py", "index.js", "server.js",
]


async def scan_repository(token: str, repo_full_name: str, branch: str = "main") -> dict:
    """
    Agent 1: Scan repo structure and detect all services.
    
    Returns:
        dict with is_monorepo, monorepo_tool, services, env_vars, summary
    """
    logger.info(f"Scanning repository: {repo_full_name}")
    
    # Step 1: Get full file tree
    tree = await get_repo_file_tree(token, repo_full_name, branch)
    all_paths = [f["path"] for f in tree if f["type"] == "blob"]
    
    logger.info(f"Found {len(all_paths)} files in repository")
    
    # Step 2: Read important config files
    file_contents = {}
    for path in all_paths:
        filename = path.split("/")[-1].lower()
        if filename in [f.lower() for f in IMPORTANT_FILES]:
            try:
                content = await get_file_content(token, repo_full_name, path)
                if content:
                    file_contents[path] = content[:3000]  # limit size
            except Exception as e:
                logger.warning(f"Could not read {path}: {e}")
    
    logger.info(f"Read {len(file_contents)} config files")
    
    # Step 3: Ask AI to analyze the structure
    file_tree_str = "\n".join(all_paths[:500])  # limit to 500 files
    configs_str = "\n\n".join([f"=== {k} ===\n{v}" for k, v in file_contents.items()])
    
    result = await call_ai_json([
        {
            "role": "system",
            "content": """You are an expert DevOps engineer. Analyze repository structure and detect all services.
Return JSON with this exact structure:
{
  "is_monorepo": boolean,
  "monorepo_tool": "turborepo|nx|pnpm-workspaces|lerna|none",
  "services": [
    {
      "name": "frontend",
      "path": "apps/frontend",
      "framework": "nextjs|react|vue|angular|svelte|fastapi|flask|django|express|nestjs|go|rust|java|unknown",
      "language": "typescript|javascript|python|go|rust|java|unknown",
      "recommended_platform": "vercel|render|docker",
      "build_command": "npm run build",
      "start_command": "npm start",
      "output_directory": ".next",
      "port": 3000,
      "detected_files": ["package.json", "next.config.js"],
      "env_vars_needed": ["DATABASE_URL", "NEXT_PUBLIC_API_URL"]
    }
  ],
  "env_vars": ["DATABASE_URL", "SECRET_KEY"],
  "has_docker": boolean,
  "has_ci": boolean,
  "summary": "Brief description of the repository"
}"""
        },
        {
            "role": "user",
            "content": f"Repository: {repo_full_name}\n\nFile tree:\n{file_tree_str}\n\nConfig files:\n{configs_str}"
        }
    ])
    
    logger.info(f"Scan complete. Detected {len(result.get('services', []))} services")
    return result
