import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
import logging
from services.db_service import supabase
from api.utils.status_messages import sanitize_error_message

logger = logging.getLogger(__name__)
router = APIRouter()

async def get_internal_url(model_id: str) -> str:
    """Get internal GCP URL from database using public model ID"""
    try:
        result = supabase.table("deployments") \
            .select("internal_url") \
            .eq("public_id", model_id) \
            .single() \
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Model not found")
        
        internal_url = result.data.get("internal_url")
        if not internal_url:
            raise HTTPException(status_code=502, detail="Model deployment not ready")
            
        return internal_url
    except Exception as e:
        logger.error(f"Failed to get internal URL for model {model_id}: {e}")
        raise HTTPException(status_code=500, detail="Unable to access model")

@router.api_route("/models/{model_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_to_model(model_id: str, path: str, request: Request):
    """Proxy requests to the actual model deployment"""
    try:
        internal_url = await get_internal_url(model_id)
        
        # Prepare headers (remove host header)
        headers = {k: v for k, v in request.headers.items() 
                  if k.lower() not in ["host", "content-length"]}
        
        # Get request body
        body = await request.body()
        
        # Make request to internal service
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(
                method=request.method,
                url=f"{internal_url}/{path}",
                headers=headers,
                content=body
            )
        
        # Prepare response headers (remove connection-specific headers)
        response_headers = {k: v for k, v in response.headers.items()
                          if k.lower() not in ["connection", "transfer-encoding", "content-length"]}
        
        return StreamingResponse(
            response.aiter_bytes(),
            status_code=response.status_code,
            headers=response_headers
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Proxy error for model {model_id}: {e}")
        raise HTTPException(
            status_code=502,
            detail="Model temporarily unavailable"
        )

@router.get("/models/{model_id}")
async def get_model_info(model_id: str):
    """Get public information about a model deployment"""
    try:
        result = supabase.table("deployments") \
            .select("model_name, public_url, status, created_at") \
            .eq("public_id", model_id) \
            .single() \
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Model not found")
        
        return {
            "model_id": model_id,
            "name": result.data.get("model_name"),
            "url": result.data.get("public_url"),
            "status": result.data.get("status"),
            "created_at": result.data.get("created_at")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get model info {model_id}: {e}")
        raise HTTPException(status_code=500, detail="Unable to fetch model information")
