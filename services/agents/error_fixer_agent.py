"""
Agent 5: Error Fixer
Reads deployment error logs, diagnoses issues, and applies fixes to the repo.
"""
import logging
from services.ai_client import call_ai_json, call_ai
from services.github_service import get_file_content, push_file_to_github

logger = logging.getLogger(__name__)


async def fix_deployment_error(
    token: str,
    repo_full_name: str,
    service: dict,
    error_logs: str,
) -> dict:
    """
    Agent 5: Read error logs, find the fix, apply it to the repo.
    
    Args:
        token: GitHub access token
        repo_full_name: e.g., "owner/repo"
        service: Service dict with name, path, framework
        error_logs: Deployment error logs from Vercel/Render
        
    Returns:
        dict with fixed (bool), fixes_applied, env_vars_to_add, reason
    """
    logger.info(f"Analyzing deployment error for {service['name']}")
    
    # Step 1: Ask AI what is wrong and how to fix it
    fix_plan = await call_ai_json([
        {
            "role": "system",
            "content": """You are an expert at fixing deployment errors.
Analyze the error logs and return JSON:
{
  "error_type": "build_error|runtime_error|config_error|missing_env_var|dependency_error",
  "root_cause": "What caused the error in 1-2 sentences",
  "fix_description": "What needs to be fixed",
  "files_to_modify": [
    {
      "path": "relative/path/to/file",
      "change_description": "What to change in this file"
    }
  ],
  "env_vars_to_add": {"KEY": "value"},
  "can_auto_fix": true|false
}"""
        },
        {
            "role": "user",
            "content": f"Service: {service['name']} ({service.get('framework', 'unknown')})\nError logs:\n{error_logs[:4000]}"
        }
    ])
    
    if not fix_plan.get("can_auto_fix"):
        logger.warning(f"Cannot auto-fix error for {service['name']}: {fix_plan.get('root_cause')}")
        return {
            "fixed": False,
            "reason": fix_plan.get("root_cause"),
            "error_type": fix_plan.get("error_type"),
        }
    
    # Step 2: For each file that needs to change, read it and fix it
    fixes_applied = []
    service_path = service.get("path", "")
    
    for file_fix in fix_plan.get("files_to_modify", []):
        file_path = file_fix["path"]
        if service_path:
            full_path = f"{service_path}/{file_path}".lstrip("/")
        else:
            full_path = file_path
        
        logger.info(f"Fixing file: {full_path}")
        
        # Read current file content
        current_content = await get_file_content(token, repo_full_name, full_path)
        
        if not current_content:
            logger.warning(f"Could not read file {full_path}, skipping")
            continue
        
        # Ask AI to rewrite the file with the fix applied
        fixed_content = await call_ai([
            {
                "role": "system",
                "content": "You are a code editor. Apply the fix to the file. Return ONLY the fixed file content, nothing else. No markdown, no explanation."
            },
            {
                "role": "user",
                "content": f"File: {full_path}\n\nFix to apply: {file_fix['change_description']}\n\nCurrent file content:\n{current_content}"
            }
        ])
        
        # Clean up the response
        fixed_content = fixed_content.strip()
        if fixed_content.startswith("```"):
            # Remove markdown code blocks
            lines = fixed_content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            fixed_content = "\n".join(lines).strip()
        
        # Step 3: Push the fix to GitHub
        try:
            await push_file_to_github(
                token=token,
                repo_full_name=repo_full_name,
                file_path=full_path,
                content=fixed_content,
                commit_message=f"fix: Auto-fix deployment error in {file_path} [Pipeline AI]",
            )
            fixes_applied.append(full_path)
            logger.info(f"Successfully pushed fix for {full_path}")
        except Exception as e:
            logger.error(f"Failed to push fix for {full_path}: {e}")
    
    return {
        "fixed": len(fixes_applied) > 0,
        "fixes_applied": fixes_applied,
        "env_vars_to_add": fix_plan.get("env_vars_to_add", {}),
        "root_cause": fix_plan.get("root_cause"),
        "error_type": fix_plan.get("error_type"),
    }
