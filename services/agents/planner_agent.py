"""
Agent 3: Deployment Planner
Creates deployment plan for all services based on analysis.
"""
import logging
from services.ai_client import call_ai_json

logger = logging.getLogger(__name__)


async def create_deployment_plan(
    repo_full_name: str,
    scan_result: dict,
    analysis_results: list[dict],
    user_api_keys: dict,
) -> dict:
    """
    Agent 3: Create a full deployment plan for all services.
    
    Args:
        repo_full_name: e.g., "owner/repo"
        scan_result: Result from scanner agent
        analysis_results: List of analysis results per service
        user_api_keys: Dict with vercel_token, render_token, etc.
        
    Returns:
        dict with deployment_order, services plan, warnings, etc.
    """
    logger.info(f"Creating deployment plan for {repo_full_name}")
    
    # Determine available platforms
    available_platforms = []
    if user_api_keys.get("vercel_token"):
        available_platforms.append("vercel")
    if user_api_keys.get("render_token"):
        available_platforms.append("render")
    
    # Build services info string
    services_info = "\n".join([
        f"- {s['name']} ({s['framework']}) at {s['path']} → recommended: {s['recommended_platform']}"
        for s in scan_result["services"]
    ])
    
    # Build analysis summary
    analysis_summary = []
    for i, analysis in enumerate(analysis_results):
        service_name = scan_result["services"][i]["name"]
        analysis_summary.append({
            "service": service_name,
            "build_command": analysis.get("build_command"),
            "start_command": analysis.get("start_command"),
            "port": analysis.get("port"),
            "env_vars": analysis.get("env_vars", []),
        })
    
    plan = await call_ai_json([
        {
            "role": "system",
            "content": f"""You are a DevOps architect. Create a deployment plan.
Available platforms: {available_platforms}

Rules:
1. Frontend services (Next.js, React, Vue, Angular, Svelte) → Vercel
2. Backend services (FastAPI, Flask, Django, Express, NestJS) → Render
3. Services with no specific platform → use recommended_platform
4. Consider dependencies: backend must deploy before frontend if frontend needs API_URL
5. Databases should deploy first

Return JSON:
{{
  "deployment_order": ["service1", "service2"],
  "services": [
    {{
      "name": "frontend",
      "platform": "vercel",
      "deploy_after": ["backend"],
      "env_vars_from_services": {{"NEXT_PUBLIC_API_URL": "backend.url"}},
      "env_vars_static": {{"NEXT_PUBLIC_APP_NAME": "My App"}},
      "estimated_minutes": 3,
      "use_analysis_config": true
    }}
  ],
  "total_estimated_minutes": 8,
  "warnings": ["Frontend depends on backend URL - deploy backend first"]
}}"""
        },
        {
            "role": "user",
            "content": f"Repo: {repo_full_name}\n\nServices:\n{services_info}\n\nAnalysis: {str(analysis_summary)[:4000]}"
        }
    ])
    
    logger.info(f"Deployment plan created: {len(plan.get('services', []))} services, {plan.get('total_estimated_minutes')} min estimated")
    return plan
