"""
GitHub App Webhook Handler
Receives webhooks when users install the GitHub App or push code.
"""
import hmac
import hashlib
import os
import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

def verify_github_signature(body: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature using HMAC SHA-256."""
    if not GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set, skipping verification")
        return True
    
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle GitHub App webhooks.
    Events: installation, installation_repositories, push
    """
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    event = request.headers.get("x-github-event", "")
    
    logger.info(f"Received GitHub webhook: event={event}")
    
    # Verify signature
    if not verify_github_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    import json
    payload = json.loads(body)
    
    # Handle installation created
    if event == "installation":
        if payload.get("action") == "created":
            installation_id = payload["installation"]["id"]
            repos = payload.get("repositories", [])
            sender = payload["sender"]["login"]
            
            logger.info(f"GitHub App installed by {sender}, installation_id={installation_id}, repos={len(repos)}")
            
            # Import here to avoid circular imports
            from services.pipeline_orchestrator import start_pipeline
            
            for repo in repos:
                background_tasks.add_task(
                    start_pipeline,
                    repo_full_name=repo["full_name"],
                    repo_id=repo["id"],
                    installation_id=installation_id,
                    github_username=sender,
                )
    
    # Handle repositories added to existing installation
    elif event == "installation_repositories":
        if payload.get("action") == "added":
            installation_id = payload["installation"]["id"]
            repos_added = payload.get("repositories_added", [])
            sender = payload["sender"]["login"]
            
            logger.info(f"Repositories added to installation {installation_id}: {len(repos_added)}")
            
            from services.pipeline_orchestrator import start_pipeline
            
            for repo in repos_added:
                background_tasks.add_task(
                    start_pipeline,
                    repo_full_name=repo["full_name"],
                    repo_id=repo["id"],
                    installation_id=installation_id,
                    github_username=sender,
                )
    
    # Handle push events
    elif event == "push":
        background_tasks.add_task(handle_push, payload)
    
    return {"received": True}


async def handle_push(payload: dict):
    """Handle git push events - trigger re-analysis and re-deployment."""
    repo_full_name = payload["repository"]["full_name"]
    branch = payload["ref"].replace("refs/heads/", "")
    sender = payload["sender"]["login"]
    
    logger.info(f"Push to {repo_full_name} on {branch} by {sender}")
    
    # TODO: Find project in DB and trigger re-deployment if on main branch
    # For now, just log it
    if branch == "main":
        logger.info(f"Main branch push detected for {repo_full_name}")
        # Could trigger re-deployment here
