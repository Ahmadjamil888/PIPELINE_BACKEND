import logging
from typing import Optional
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from schemas import ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["GitHub"])

# In-memory store for GitHub tokens (replace with database in production)
_github_tokens: dict = {}


class GitHubTokenRequest(BaseModel):
    user_id: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None


class GitHubTokenResponse(BaseModel):
    user_id: str
    connected: bool
    created_at: datetime


@router.post(
    "/token",
    response_model=GitHubTokenResponse,
    responses={401: {"model": ErrorResponse}}
)
async def store_github_token(request: GitHubTokenRequest):
    """
    Store GitHub OAuth token for a user.
    Called by frontend after GitHub OAuth flow completes.
    """
    logger.info(f"Storing GitHub token for user: {request.user_id}")
    
    try:
        _github_tokens[request.user_id] = {
            "access_token": request.access_token,
            "refresh_token": request.refresh_token,
            "expires_at": request.expires_at,
            "created_at": datetime.now()
        }
        
        return GitHubTokenResponse(
            user_id=request.user_id,
            connected=True,
            created_at=datetime.now()
        )
    except Exception as e:
        logger.error(f"Failed to store GitHub token: {e}")
        raise HTTPException(status_code=500, detail="Failed to store GitHub token")


@router.get(
    "/token/{user_id}",
    response_model=dict,
    responses={404: {"model": ErrorResponse}}
)
async def get_github_token(user_id: str):
    """
    Get stored GitHub token for a user.
    Internal use only - returns masked token.
    """
    token_data = _github_tokens.get(user_id)
    
    if not token_data:
        raise HTTPException(status_code=404, detail="GitHub token not found")
    
    # Return masked version for security
    token = token_data["access_token"]
    masked = f"{token[:8]}...{token[-4:]}" if len(token) > 12 else "****"
    
    return {
        "user_id": user_id,
        "connected": True,
        "token_masked": masked,
        "created_at": token_data["created_at"],
        "expires_at": token_data.get("expires_at")
    }


@router.delete(
    "/token/{user_id}",
    status_code=204
)
async def revoke_github_token(user_id: str):
    """
    Revoke/delete GitHub token for a user.
    """
    if user_id in _github_tokens:
        del _github_tokens[user_id]
        logger.info(f"GitHub token revoked for user: {user_id}")
    
    return None


@router.get(
    "/connect"
)
async def github_connect(user_id: str = ""):
    """
    Returns the GitHub OAuth URL for the user to initiate the connection.
    """
    import os
    
    client_id = os.getenv("GITHUB_CLIENT_ID")
    callback_url = os.getenv(
        "GITHUB_CALLBACK_URL",
        "https://pipeline-ai-labs-by-ahmad.up.railway.app/api/v1/github/callback"
    )
    
    if not client_id:
        raise HTTPException(status_code=500, detail="GitHub OAuth client ID not configured")
    
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={callback_url}"
        f"&scope=repo,read:user,user:email"
        f"&state={user_id}"  # Pass user_id as state
    )
    return {"auth_url": auth_url}


@router.get(
    "/callback"
)
async def github_callback(code: str, state: str = None):
    """
    GitHub OAuth callback endpoint.
    Exchanges the code for an access token and stores it.
    """
    import httpx
    import os
    from fastapi.responses import RedirectResponse
    
    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "https://pipeline-labs.vercel.app")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="GitHub OAuth credentials not configured")
    
    try:
        # Exchange code for access token
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code
                }
            )
            
            if token_response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to exchange code for token")
            
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            
            print(f"GitHub callback — state={state}, token_received={bool(access_token)}")
            
            if not access_token:
                print(f"GitHub OAuth error: {token_data}")
                return RedirectResponse(
                    url=f"{FRONTEND_URL}/dashboard?github_error=true"
                )
            
            # Get GitHub user info
            async with httpx.AsyncClient() as client:
                user_res = await client.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
            github_user = user_res.json()
            github_username = github_user.get("login", "")
            
            print(f"GitHub user: {github_username}")
            
            # Save token to Supabase — state is the user_id
            if state:
                from services.db_service import supabase
                
                profile_res = supabase.table("profiles")\
                    .select("id")\
                    .eq("user_id", state)\
                    .execute()
                
                print(f"Profile lookup: {profile_res.data}")
                
                if profile_res.data:
                    profile_id = profile_res.data[0]["id"]
                    
                    # Upsert token to database
                    token_res = supabase.table("github_tokens").upsert({
                        "profile_id": profile_id,
                        "access_token_encrypted": access_token,
                    }, on_conflict="profile_id").execute()
                    
                    print(f"Token saved: {token_res.data}")
                    
                    # Update github_username on profile
                    supabase.table("profiles").update({
                        "github_username": github_username,
                    }).eq("id", profile_id).execute()
            
            logger.info(f"GitHub OAuth completed for user: {github_username}")
            
            return RedirectResponse(
                url=f"{FRONTEND_URL}/dashboard?github_connected=true"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GitHub callback failed: {e}")
        raise HTTPException(status_code=500, detail="GitHub OAuth callback failed")


@router.get("/repos/{user_id}")
async def proxy_github_repos(user_id: str):
    """
    Proxy GitHub API request to fetch user's repositories.
    Uses stored token to authenticate with GitHub.
    """
    import httpx
    from services.db_service import supabase
    
    # Get profile from user_id
    profile_res = supabase.table("profiles")\
        .select("id")\
        .eq("user_id", user_id)\
        .execute()
    
    if not profile_res.data:
        raise HTTPException(status_code=401, detail="User not found")
    
    profile_id = profile_res.data[0]["id"]
    
    # Get token from database
    token_res = supabase.table("github_tokens")\
        .select("access_token_encrypted")\
        .eq("profile_id", profile_id)\
        .execute()
    
    if not token_res.data:
        raise HTTPException(status_code=401, detail="GitHub not connected")
    
    access_token = token_res.data[0]["access_token_encrypted"]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user/repos?sort=updated&per_page=100",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"GitHub API error: {response.text}"
                )
            
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"GitHub API request failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch GitHub repositories")


async def get_installation_token(installation_id: int) -> str:
    """
    Get an installation access token using GitHub App JWT auth.
    """
    import jwt
    import time
    import httpx
    import os
    
    app_id = os.getenv("GITHUB_APP_ID")
    private_key = os.getenv("GITHUB_PRIVATE_KEY", "").replace("\\n", "\n")
    
    if not app_id or not private_key:
        raise HTTPException(status_code=500, detail="GitHub App credentials not configured")
    
    # Create JWT signed with private key
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,  # 10 minutes
        "iss": app_id
    }
    jwt_token = jwt.encode(payload, private_key, algorithm="RS256")
    
    # Exchange JWT for installation token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json"
            }
        )
        
        if response.status_code != 201:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to get installation token: {response.text}"
            )
        
        return response.json()["token"]


@router.get("/setup")
async def github_setup(
    installation_id: int,
    setup_action: str = "install",
    state: str = None
):
    """
    GitHub App setup endpoint - called after user installs the app.
    Saves installation info and redirects back to frontend.
    """
    import httpx
    from fastapi.responses import RedirectResponse
    
    try:
        user_id = state
        
        # Get installation token
        token = await get_installation_token(installation_id)
        
        # Fetch repos this installation has access to
        async with httpx.AsyncClient() as client:
            repos_response = await client.get(
                "https://api.github.com/installation/repositories",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json"
                }
            )
            
            if repos_response.status_code != 200:
                logger.error(f"Failed to fetch repos: {repos_response.text}")
                raise HTTPException(status_code=500, detail="Failed to fetch repositories")
            
            repos = repos_response.json().get("repositories", [])
        
        # Save installation info
        _github_installations[user_id] = {
            "installation_id": installation_id,
            "repos": [{"id": r["id"], "full_name": r["full_name"], "name": r["name"], 
                      "default_branch": r["default_branch"], "html_url": r["html_url"]} 
                     for r in repos],
            "created_at": datetime.now()
        }
        
        logger.info(f"GitHub App installed for user {user_id}, installation_id: {installation_id}, repos: {len(repos)}")
        
        # Redirect to frontend
        return RedirectResponse(
            url=f"https://pipeline-labs.vercel.app/dashboard/repos?connected=true&installation_id={installation_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GitHub setup failed: {e}")
        raise HTTPException(status_code=500, detail=f"GitHub setup failed: {str(e)}")


@router.get("/status")
async def github_status(user_id: str):
    """Check if user has a GitHub token stored"""
    from services.db_service import supabase

    profile_res = supabase.table("profiles")\
        .select("id")\
        .eq("user_id", user_id)\
        .execute()

    if not profile_res.data:
        return {"connected": False}

    profile_id = profile_res.data[0]["id"]

    token_res = supabase.table("github_tokens")\
        .select("id")\
        .eq("profile_id", profile_id)\
        .execute()

    return {"connected": bool(token_res.data)}


@router.get("/repos")
async def list_repos(user_id: str):
    """
    List repositories accessible to the user via GitHub App installation.
    """
    installation = _github_installations.get(user_id)
    
    if not installation:
        raise HTTPException(status_code=404, detail="GitHub App not installed")
    
    try:
        # Get fresh token and fetch current repos
        token = await get_installation_token(installation["installation_id"])
        
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/installation/repositories",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json"
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch repositories")
            
            repos = response.json().get("repositories", [])
            
            # Format for frontend
            formatted_repos = [{
                "id": r["id"],
                "name": r["name"],
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "private": r["private"],
                "html_url": r["html_url"],
                "clone_url": r.get("clone_url", ""),
                "default_branch": r["default_branch"],
                "language": r.get("language", ""),
                "updated_at": r["updated_at"]
            } for r in repos]
            
            return {"repositories": formatted_repos}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list repos: {e}")
        raise HTTPException(status_code=500, detail="Failed to list repositories")


@router.post("/repos/connect")
async def connect_repo(body: dict):
    """Create a project from a selected repo and start AI pipeline"""
    from services.db_service import supabase
    from services.pipeline_orchestrator import start_pipeline
    import asyncio

    user_id = body.get("user_id")
    repo = body.get("repo")

    if not user_id or not repo:
        raise HTTPException(status_code=400, detail="Missing user_id or repo")

    # Get profile
    profile_res = supabase.table("profiles")\
        .select("id, github_username")\
        .eq("user_id", user_id)\
        .execute()

    if not profile_res.data:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile = profile_res.data[0]

    # Create project in DB immediately
    project_res = supabase.table("projects").insert({
        "owner_id": profile["id"],
        "name": repo["name"],
        "repo_url": repo.get("html_url", repo.get("url", "")),
        "github_repo_id": repo["id"],
        "provider": "github",
        "default_branch": repo.get("default_branch", "main"),
        "is_private": repo.get("private", False),
        "status": "pending",
    }).execute()

    project = project_res.data[0]
    project_id = project["id"]

    # Start AI pipeline in background
    asyncio.create_task(start_pipeline(
        repo_full_name=repo["full_name"],
        repo_id=repo["id"],
        installation_id=body.get("installation_id", 0),
        github_username=profile["github_username"] or "",
        project_id=project_id,
    ))

    return {"project_id": project_id}
