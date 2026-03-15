"""
GitHub App Integration Routes
Handles GitHub App installation, webhooks, and repository access
"""
import os
import time
import jwt
import requests
from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["github"])

# GitHub App Configuration from environment
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_SECRET", "")  # This should be the full private key PEM
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_APP_NAME = "pipeline"  # Your GitHub App name

# In-memory storage for installations (use database in production)
# Maps installation_id -> {user_id, repositories: []}
installations: Dict[int, Dict[str, Any]] = {}


def generate_jwt() -> str:
    """Generate a JWT for GitHub App authentication"""
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + (10 * 60),  # 10 minutes
        "iss": GITHUB_APP_ID
    }
    
    # The private key should be in PEM format
    # For GitHub Apps, you download a .pem file from the app settings
    jwt_token = jwt.encode(payload, GITHUB_PRIVATE_KEY, algorithm="RS256")
    return jwt_token


def get_installation_token(installation_id: int) -> str:
    """Exchange JWT for an installation access token"""
    jwt_token = generate_jwt()
    
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    
    response = requests.post(url, headers=headers)
    
    if response.status_code != 201:
        logger.error(f"Failed to get installation token: {response.text}")
        raise HTTPException(status_code=400, detail="Failed to get installation token")
    
    return response.json()["token"]


@router.get("/connect")
async def github_connect():
    """
    Returns the URL to start GitHub App installation
    Frontend redirects user to this URL
    """
    # GitHub App installation URL format
    install_url = f"https://github.com/apps/{GITHUB_APP_NAME}/installations/new"
    
    return {
        "url": install_url,
        "app_name": GITHUB_APP_NAME
    }


@router.get("/callback")
async def github_callback(
    installation_id: Optional[int] = None,
    setup_action: Optional[str] = None,
    state: Optional[str] = None
):
    """
    GitHub App installation callback
    GitHub redirects here after user installs the app
    """
    logger.info(f"GitHub callback received: installation_id={installation_id}, setup_action={setup_action}")
    
    if not installation_id:
        raise HTTPException(status_code=400, detail="No installation_id provided")
    
    # Store installation (in production, save to database)
    installations[installation_id] = {
        "id": installation_id,
        "setup_action": setup_action,
        "created_at": time.time()
    }
    
    # Redirect back to frontend dashboard
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    redirect_url = f"{frontend_url}/dashboard/repos/connect?installation_id={installation_id}&connected=true"
    
    return RedirectResponse(url=redirect_url)


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None)
):
    """
    GitHub webhook endpoint
    Receives events from GitHub (installations, pushes, etc.)
    """
    payload = await request.body()
    
    # Verify webhook signature (in production)
    # if GITHUB_WEBHOOK_SECRET and x_hub_signature_256:
    #     import hmac
    #     import hashlib
    #     expected = hmac.new(
    #         GITHUB_WEBHOOK_SECRET.encode(),
    #         payload,
    #         hashlib.sha256
    #     ).hexdigest()
    #     if not hmac.compare_digest(f"sha256={expected}", x_hub_signature_256):
    #         raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        data = await request.json()
    except:
        data = {}
    
    event = x_github_event or "unknown"
    logger.info(f"GitHub webhook received: {event}")
    
    # Handle different webhook events
    if event == "installation":
        # App was installed or updated
        action = data.get("action")
        installation = data.get("installation", {})
        installation_id = installation.get("id")
        
        if action in ["created", "added"] and installation_id:
            installations[installation_id] = {
                "id": installation_id,
                "account": installation.get("account", {}),
                "repositories": data.get("repositories_added", []),
                "created_at": time.time()
            }
            logger.info(f"Installation {installation_id} created")
    
    elif event == "installation_repositories":
        # Repositories were added/removed from installation
        action = data.get("action")
        installation = data.get("installation", {})
        installation_id = installation.get("id")
        
        if installation_id and installation_id in installations:
            if action == "added":
                repos_added = data.get("repositories_added", [])
                installations[installation_id]["repositories"].extend(repos_added)
                logger.info(f"Added {len(repos_added)} repos to installation {installation_id}")
    
    elif event == "push":
        # Code was pushed - could trigger deployments
        repository = data.get("repository", {})
        ref = data.get("ref")
        logger.info(f"Push to {repository.get('full_name')} on {ref}")
        # TODO: Trigger deployment pipeline here
    
    return {"status": "ok", "event": event}


@router.get("/repos")
async def get_repos(installation_id: Optional[int] = None):
    """
    Get repositories accessible by the GitHub App installation
    Requires installation_id from the callback
    """
    if not installation_id:
        # Return stored installations if no ID provided
        return {
            "installations": list(installations.keys()),
            "repos": []
        }
    
    if installation_id not in installations:
        raise HTTPException(status_code=404, detail="Installation not found")
    
    try:
        # Get installation token
        token = get_installation_token(installation_id)
        
        # Fetch repositories using installation token
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json"
        }
        
        response = requests.get(
            "https://api.github.com/installation/repositories",
            headers=headers
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch repos: {response.text}")
            raise HTTPException(status_code=400, detail="Failed to fetch repositories")
        
        repos_data = response.json()
        repositories = repos_data.get("repositories", [])
        
        # Simplify repo data for frontend
        simplified_repos = [
            {
                "id": repo["id"],
                "name": repo["name"],
                "full_name": repo["full_name"],
                "description": repo.get("description"),
                "html_url": repo["html_url"],
                "private": repo["private"],
                "language": repo.get("language"),
                "stargazers_count": repo["stargazers_count"],
                "updated_at": repo["updated_at"],
                "default_branch": repo.get("default_branch", "main")
            }
            for repo in repositories
        ]
        
        return {
            "total_count": repos_data.get("total_count", len(repositories)),
            "repositories": simplified_repos
        }
    
    except Exception as e:
        logger.error(f"Error fetching repos: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repos/{owner}/{repo}/connect")
async def connect_repository(
    owner: str,
    repo: str,
    installation_id: int,
    request: Request
):
    """
    Connect a specific repository to the user's account
    Stores the repo info in database
    """
    try:
        body = await request.json()
        user_id = body.get("user_id")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id required")
        
        # Get installation token
        token = get_installation_token(installation_id)
        
        # Verify repo exists and is accessible
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json"
        }
        
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=headers
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Repository not found or not accessible")
        
        repo_data = response.json()
        
        # In production, save to database
        # For now, store in memory
        if installation_id not in installations:
            installations[installation_id] = {}
        
        if "connected_repos" not in installations[installation_id]:
            installations[installation_id]["connected_repos"] = []
        
        installations[installation_id]["connected_repos"].append({
            "owner": owner,
            "repo": repo,
            "user_id": user_id,
            "repo_url": repo_data["html_url"],
            "default_branch": repo_data.get("default_branch", "main"),
            "connected_at": time.time()
        })
        
        return {
            "success": True,
            "message": f"Repository {owner}/{repo} connected successfully",
            "repository": {
                "id": repo_data["id"],
                "name": repo_data["name"],
                "full_name": repo_data["full_name"],
                "html_url": repo_data["html_url"],
                "default_branch": repo_data.get("default_branch", "main")
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting repo: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/installations")
async def get_installations():
    """
    Get all stored GitHub App installations
    """
    return {
        "installations": [
            {
                "id": inst_id,
                "account": data.get("account", {}),
                "repository_count": len(data.get("repositories", []))
            }
            for inst_id, data in installations.items()
        ]
    }
