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
        f"&scope=repo,read:user"
        f"&state={user_id}"
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
    
    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="GitHub OAuth credentials not configured")
    
    try:
        # Exchange code for access token
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code
                }
            )
            
            if token_response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to exchange code for token")
            
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            
            if not access_token:
                raise HTTPException(status_code=400, detail="No access token received")
            
            # Get user info from GitHub
            user_response = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if user_response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to get user info")
            
            user_data = user_response.json()
            github_user_id = str(user_data.get("id"))
            
            # Store the token
            _github_tokens[github_user_id] = {
                "access_token": access_token,
                "refresh_token": None,
                "created_at": datetime.now()
            }
            
            logger.info(f"GitHub OAuth completed for user: {github_user_id}")
            
            # Redirect to frontend with success
            from fastapi.responses import RedirectResponse
            return RedirectResponse(
                url="https://pipeline-labs.vercel.app/dashboard/repos/connect?github_connected=true"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GitHub callback failed: {e}")
        raise HTTPException(status_code=500, detail="GitHub OAuth callback failed")


@router.get(
    "/repos/{user_id}"
)
async def proxy_github_repos(user_id: str):
    """
    Proxy GitHub API request to fetch user's repositories.
    Uses stored token to authenticate with GitHub.
    """
    import httpx
    
    token_data = _github_tokens.get(user_id)
    if not token_data:
        raise HTTPException(status_code=401, detail="GitHub not connected")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user/repos?sort=updated&per_page=100",
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
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
