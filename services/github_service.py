"""
GitHub App Service
Handles JWT generation and GitHub API interactions.
"""
import time
import os
import httpx
import jwt
import logging
import base64

logger = logging.getLogger(__name__)

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY", "")


def generate_jwt() -> str:
    """Generate a JWT to authenticate as the GitHub App."""
    if not GITHUB_APP_ID or not GITHUB_PRIVATE_KEY:
        raise ValueError("GITHUB_APP_ID or GITHUB_PRIVATE_KEY not configured")
    
    # Handle escaped newlines in env var
    private_key = GITHUB_PRIVATE_KEY.replace("\\n", "\n")
    
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,  # 10 minutes
        "iss": GITHUB_APP_ID,
    }
    
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    """Get an installation access token for cloning repos."""
    app_jwt = generate_jwt()
    
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        
        if res.status_code != 201:
            logger.error(f"Failed to get installation token: {res.status_code} - {res.text}")
            raise Exception(f"Failed to get installation token: {res.status_code}")
        
        return res.json()["token"]


async def get_repo_file_tree(token: str, repo_full_name: str, branch: str = "main") -> list:
    """Get the full file tree of a repository."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/git/trees/{branch}?recursive=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        
        if res.status_code != 200:
            logger.error(f"Failed to get file tree: {res.status_code}")
            raise Exception(f"Failed to get file tree: {res.status_code}")
        
        return res.json().get("tree", [])


async def get_file_content(token: str, repo_full_name: str, file_path: str) -> str:
    """Get content of a specific file from GitHub."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/contents/{file_path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        
        if res.status_code != 200:
            logger.warning(f"Failed to get file {file_path}: {res.status_code}")
            return ""
        
        data = res.json()
        if "content" in data:
            return base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
        return ""


async def push_file_to_github(
    token: str,
    repo_full_name: str,
    file_path: str,
    content: str,
    commit_message: str,
    branch: str = "main"
) -> None:
    """Push a file change to GitHub."""
    async with httpx.AsyncClient() as client:
        # Get current file SHA (needed for updates)
        get_res = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/contents/{file_path}",
            headers={"Authorization": f"Bearer {token}"},
            params={"ref": branch}
        )
        
        sha = None
        if get_res.status_code == 200:
            sha = get_res.json().get("sha")
        
        payload = {
            "message": commit_message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        
        put_res = await client.put(
            f"https://api.github.com/repos/{repo_full_name}/contents/{file_path}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        
        if put_res.status_code not in [200, 201]:
            logger.error(f"Failed to push file: {put_res.status_code} - {put_res.text}")
            raise Exception(f"Failed to push file: {put_res.status_code}")
        
        logger.info(f"Successfully pushed {file_path} to {repo_full_name}")
