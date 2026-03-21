import httpx
import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from services.db_service import supabase
from typing import Optional, Dict, Any

router = APIRouter(prefix="/domains", tags=["Domains"])

VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "")


class AddDomainRequest(BaseModel):
    user_id: str
    project_id: str
    domain: str
    service_name: str


class VerifyDomainResponse(BaseModel):
    domain: str
    verified: bool
    dns_status: Optional[Dict[str, Any]] = None


@router.post("/add")
async def add_custom_domain(req: AddDomainRequest):
    """
    Add a custom domain to a project (Pro/Team plan only)
    """
    try:
        # Check user is on pro plan
        sub_res = supabase.table("subscriptions")\
            .select("plan")\
            .eq("user_id", req.user_id)\
            .execute()

        if not sub_res.data:
            raise HTTPException(status_code=403, detail="No subscription found")

        plan = sub_res.data[0]["plan"]
        if plan not in ("pro", "team"):
            raise HTTPException(
                status_code=403,
                detail="Custom domains require a Pro or Team plan"
            )

        # Verify project ownership
        project_res = supabase.table("projects")\
            .select("owner_id")\
            .eq("id", req.project_id)\
            .execute()
        
        if not project_res.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = project_res.data[0]
        
        # Verify user owns the project
        profile_res = supabase.table("profiles")\
            .select("id")\
            .eq("user_id", req.user_id)\
            .single()
        
        if not profile_res.data or project["owner_id"] != profile_res.data["id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        # Add domain to Vercel project
        if not VERCEL_TOKEN:
            # For development, skip Vercel integration
            verification_data = {
                "verified": False,
                "verification": [{
                    "type": "CNAME",
                    "domain": req.domain,
                    "value": "cname.vercel-dns.com"
                }]
            }
        else:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"https://api.vercel.com/v10/projects/{req.service_name}/domains",
                    headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
                    json={"name": req.domain},
                )

            if res.status_code not in (200, 201):
                data = res.json()
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to add domain: {data.get('error', {}).get('message', 'Unknown error')}"
                )

            verification_data = res.json()

        # Save to DB
        supabase.table("project_domains").upsert({
            "project_id": req.project_id,
            "domain": req.domain,
            "verified": verification_data.get("verified", False),
            "verification_records": verification_data.get("verification", []),
        }).execute()

        return {
            "domain": req.domain,
            "verified": verification_data.get("verified", False),
            "verification": verification_data.get("verification", []),
            "instructions": f"Add a CNAME record pointing {req.domain} to cname.vercel-dns.com"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add domain: {str(e)}")


@router.get("/verify/{domain}", response_model=VerifyDomainResponse)
async def verify_domain(domain: str):
    """
    Check if domain DNS has propagated
    """
    try:
        if not VERCEL_TOKEN:
            # For development, return mock response
            return {
                "domain": domain,
                "verified": False,
                "dns_status": {"status": "pending"}
            }

        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"https://api.vercel.com/v10/domains/{domain}/config",
                headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            )
        
        if res.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to verify domain: {res.text}"
            )

        data = res.json()
        return {
            "domain": domain,
            "verified": data.get("configuredBy") is not None,
            "dns_status": data,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to verify domain: {str(e)}")


@router.get("/project/{project_id}")
async def get_project_domains(project_id: str, user_id: str):
    """
    Get all custom domains for a project
    """
    try:
        # Verify project ownership
        project_res = supabase.table("projects")\
            .select("owner_id")\
            .eq("id", project_id)\
            .execute()
        
        if not project_res.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = project_res.data[0]
        
        # Verify user owns the project
        profile_res = supabase.table("profiles")\
            .select("id")\
            .eq("user_id", user_id)\
            .single()
        
        if not profile_res.data or project["owner_id"] != profile_res.data["id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        # Get domains
        domains_res = supabase.table("project_domains")\
            .select("*")\
            .eq("project_id", project_id)\
            .execute()

        return {"domains": domains_res.data or []}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get domains: {str(e)}")


@router.delete("/project/{project_id}/domain/{domain}")
async def remove_custom_domain(project_id: str, domain: str, user_id: str):
    """
    Remove a custom domain from a project
    """
    try:
        # Verify project ownership
        project_res = supabase.table("projects")\
            .select("owner_id")\
            .eq("id", project_id)\
            .execute()
        
        if not project_res.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = project_res.data[0]
        
        # Verify user owns the project
        profile_res = supabase.table("profiles")\
            .select("id")\
            .eq("user_id", user_id)\
            .single()
        
        if not profile_res.data or project["owner_id"] != profile_res.data["id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        # Remove from Vercel if configured
        if VERCEL_TOKEN:
            # Get service name from project
            service_name = f"pipeline-{project_id[:8]}"
            async with httpx.AsyncClient() as client:
                res = await client.delete(
                    f"https://api.vercel.com/v10/projects/{service_name}/domains/{domain}",
                    headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
                )
                # Don't fail if Vercel deletion fails, just log it
                if res.status_code not in (200, 204):
                    print(f"Failed to remove domain from Vercel: {res.text}")

        # Remove from database
        supabase.table("project_domains")\
            .delete()\
            .eq("project_id", project_id)\
            .eq("domain", domain)\
            .execute()

        return {"message": "Domain removed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove domain: {str(e)}")
