"""
AI-powered deployment pipeline.
Analyzes repositories using Daytona sandboxes and OpenRouter AI,
then deploys to Vercel and Render.
"""
import asyncio
import json
import logging
import os
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


async def run_ai_deployment_pipeline(
    deployment_id: str,
    repo_full_name: str,
    installation_id: int,
    user_id: str
):
    """
    Run the complete AI deployment pipeline.
    
    1. Clone repo in Daytona sandbox
    2. Analyze with OpenRouter AI
    3. Deploy frontend to Vercel
    4. Deploy backend to Render
    5. Cross-configure env vars
    """
    from api.routes.deployments import update_deployment_status
    from api.routes.github import get_installation_token
    from services.vercel_service import deploy_to_vercel
    from services.render_service import deploy_to_render
    
    await update_deployment_status(deployment_id, "analyzing")
    
    try:
        # ── STEP 1: Get installation token ─────────────────────
        token = await get_installation_token(installation_id)
        
        # ── STEP 2: Clone repo in Daytona Sandbox ───────────────
        await update_deployment_status(deployment_id, "cloning")
        
        # For now, skip Daytona and do local analysis
        # TODO: Integrate Daytona SDK when available
        
        # ── STEP 3: Fetch repo structure from GitHub ─────────────
        async with httpx.AsyncClient() as client:
            # Get repo contents
            contents_resp = await client.get(
                f"https://api.github.com/repos/{repo_full_name}/contents",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json"
                }
            )
            
            if contents_resp.status_code != 200:
                raise Exception(f"Failed to fetch repo contents: {contents_resp.text}")
            
            contents = contents_resp.json()
            root_files = [item["name"] for item in contents if item["type"] == "file"]
            
            # Try to get key config files
            package_json = "NOT_FOUND"
            requirements = "NOT_FOUND"
            docker_compose = "NOT_FOUND"
            
            # Check for package.json
            if "package.json" in root_files:
                pkg_resp = await client.get(
                    f"https://api.github.com/repos/{repo_full_name}/contents/package.json",
                    headers={"Authorization": f"Bearer {token}"}
                )
                if pkg_resp.status_code == 200:
                    import base64
                    content = pkg_resp.json().get("content", "")
                    package_json = base64.b64decode(content).decode("utf-8")[:2000]
            
            # Check for requirements.txt
            if "requirements.txt" in root_files:
                req_resp = await client.get(
                    f"https://api.github.com/repos/{repo_full_name}/contents/requirements.txt",
                    headers={"Authorization": f"Bearer {token}"}
                )
                if req_resp.status_code == 200:
                    import base64
                    content = req_resp.json().get("content", "")
                    requirements = base64.b64decode(content).decode("utf-8")[:1000]
            
            # Check for docker-compose.yml
            if "docker-compose.yml" in root_files or "docker-compose.yaml" in root_files:
                dc_file = "docker-compose.yml" if "docker-compose.yml" in root_files else "docker-compose.yaml"
                dc_resp = await client.get(
                    f"https://api.github.com/repos/{repo_full_name}/contents/{dc_file}",
                    headers={"Authorization": f"Bearer {token}"}
                )
                if dc_resp.status_code == 200:
                    import base64
                    content = dc_resp.json().get("content", "")
                    docker_compose = base64.b64decode(content).decode("utf-8")[:2000]
        
        # ── STEP 4: AI analyzes the stack ──────────────────────
        await update_deployment_status(deployment_id, "ai_analyzing")
        
        analysis = await analyze_with_openrouter(
            root_files=root_files,
            package_json=package_json,
            requirements=requirements,
            docker_compose=docker_compose
        )
        
        await update_deployment_status(deployment_id, "analyzed", analysis=analysis)
        
        # ── STEP 5: Deploy based on AI analysis ────────────────
        frontend_url = None
        backend_url = None
        
        # Deploy backend first (if exists)
        if analysis.get("backend", {}).get("exists", False):
            await update_deployment_status(deployment_id, "deploying_backend")
            backend_url = await deploy_to_render(
                repo_full_name=repo_full_name,
                analysis=analysis["backend"],
                installation_id=installation_id,
                deployment_id=deployment_id
            )
        
        # Deploy frontend (if exists)
        if analysis.get("frontend", {}).get("exists", False):
            await update_deployment_status(deployment_id, "deploying_frontend")
            
            # Pass backend URL as env var to frontend
            env_vars = {}
            if backend_url:
                env_vars["NEXT_PUBLIC_API_URL"] = backend_url
                env_vars["VITE_API_URL"] = backend_url
            
            frontend_url = await deploy_to_vercel(
                repo_full_name=repo_full_name,
                analysis=analysis["frontend"],
                env_vars=env_vars,
                deployment_id=deployment_id
            )
        
        # Update backend CORS with frontend URL
        if backend_url and frontend_url:
            # TODO: Update Render env vars to allow frontend origin
            pass
        
        # ── STEP 6: Done ───────────────────────────────────────
        await update_deployment_status(
            deployment_id, "success",
            frontend_url=frontend_url,
            backend_url=backend_url
        )
        
        logger.info(f"Deployment {deployment_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Deployment pipeline failed: {e}")
        await update_deployment_status(deployment_id, "failed", error=str(e))
        raise


async def analyze_with_openrouter(
    root_files: list,
    package_json: str,
    requirements: str,
    docker_compose: str
) -> dict:
    """
    Use OpenRouter AI to analyze the repository stack.
    """
    import httpx
    
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise Exception("OPENROUTER_API_KEY not configured")
    
    # Build the prompt
    prompt = f"""Analyze this repository and return JSON only.

Root files: {', '.join(root_files)}

package.json: {package_json[:2000] if package_json != 'NOT_FOUND' else 'NOT_FOUND'}

requirements.txt: {requirements[:1000] if requirements != 'NOT_FOUND' else 'NOT_FOUND'}

docker-compose.yml: {docker_compose[:2000] if docker_compose != 'NOT_FOUND' else 'NOT_FOUND'}

Return this exact JSON structure:
{{
  "stack_type": "monorepo|fullstack|frontend_only|backend_only",
  "frontend": {{
    "exists": true/false,
    "framework": "nextjs|react|vue|svelte|static|none",
    "directory": "./frontend or ./apps/web or . etc",
    "build_command": "npm run build",
    "install_command": "npm install",
    "output_directory": ".next or dist or build",
    "env_vars_needed": ["NEXT_PUBLIC_API_URL"]
  }},
  "backend": {{
    "exists": true/false,
    "framework": "fastapi|express|django|flask|nestjs|none",
    "language": "python|nodejs|other",
    "directory": "./backend or ./apps/api or . etc",
    "build_command": "pip install -r requirements.txt",
    "start_command": "uvicorn main:app --host 0.0.0.0 --port 8000",
    "env_vars_needed": ["DATABASE_URL"]
  }},
  "services": ["postgres", "redis"],
  "deployment_strategy": "frontend_to_vercel_backend_to_render"
}}"""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://pipeline-labs.vercel.app"
            },
            json={
                "model": "anthropic/claude-sonnet-4-20250514",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a DevOps expert. Analyze repositories and return deployment configurations. Respond with valid JSON only."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }
        )
        
        if response.status_code != 200:
            raise Exception(f"OpenRouter API error: {response.text}")
        
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        
        # Extract JSON from response
        try:
            # Try to parse directly first
            analysis = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if json_match:
                analysis = json.loads(json_match.group(1))
            else:
                raise Exception(f"Could not parse AI response as JSON: {content[:500]}")
        
        return analysis
