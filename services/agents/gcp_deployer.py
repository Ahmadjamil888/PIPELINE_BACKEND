import httpx
import os
import json
import base64
import logging
from typing import Dict, Any
from api.utils.status_messages import public_status, sanitize_error_message

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GCP_SA_KEY = os.getenv("GCP_SERVICE_ACCOUNT_KEY", "")  # base64 encoded JSON key

async def get_gcp_token() -> str:
    """Get GCP access token from service account key"""
    try:
        import google.oauth2.service_account
        import google.auth.transport.requests

        if not GCP_SA_KEY:
            raise Exception("GCP_SERVICE_ACCOUNT_KEY not configured")
        
        key_data = json.loads(base64.b64decode(GCP_SA_KEY))
        credentials = google.oauth2.service_account.Credentials.from_service_account_info(
            key_data,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        return credentials.token
    except ImportError:
        logger.error("Google Cloud libraries not installed. Run: pip install google-auth google-cloud-run google-cloud-aiplatform")
        raise Exception("Google Cloud libraries not installed")
    except Exception as e:
        logger.error(f"Failed to get GCP token: {e}")
        raise


async def trigger_cloud_build(repo_url: str, image_tag: str, service_name: str) -> str:
    """Trigger Cloud Build to build Docker image from GitHub repo"""
    try:
        from google.cloud.devtools import cloudbuild_v1
        from google.cloud.devtools.cloudbuild_v1.types import Build, BuildStep, Source, RepoSource
        
        client = cloudbuild_v1.CloudBuildClient()
        
        # Parse GitHub URL → owner/repo
        parts = repo_url.rstrip("/").split("/")
        repo_name = parts[-1]
        owner = parts[-2]
        
        # Build steps
        build_steps = [
            BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["build", "-t", image_tag, "."]
            ),
            BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["push", image_tag]
            )
        ]
        
        build = Build(
            steps=build_steps,
            images=[image_tag],
            source=Source(
                repo_source=RepoSource(
                    project_id=GCP_PROJECT_ID,
                    repo_name=f"github_{owner}_{repo_name}",
                    branch_name="main"
                )
            ),
            timeout={"seconds": 600}
        )
        
        operation = client.create_build(project_id=GCP_PROJECT_ID, build=build)
        
        # Poll until complete
        result = operation.result(timeout=600)
        if result.status != Build.Status.SUCCESS:
            raise Exception(f"Build failed: {result.status_detail}")
        
        logger.info(f"Pipeline Labs Builder completed successfully: {result.id}")
        return result.id
        
    except ImportError:
        logger.warning("Cloud Build libraries not installed, skipping build step")
        return "skipped"
    except Exception as e:
        logger.error(f"Pipeline Labs Builder failed: {e}")
        # Continue with deployment even if build fails
        return "failed"


async def deploy_to_cloud_run(
    service: Dict[str, Any],
    image_url: str,
    env_vars: Dict[str, str],
) -> Dict[str, Any]:
    """Deploy a containerized AI model to Google Cloud Run"""
    try:
        token = await get_gcp_token()
        service_name = f"pipeline-{service['name']}".lower()[:63]

        # Build env var list
        env_list = [
            {"name": k, "value": v}
            for k, v in env_vars.items()
        ]

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Check if service exists
            service_path = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/services/{service_name}"
            
            try:
                # Try to get existing service
                res = await client.get(
                    f"https://run.googleapis.com/v2/{service_path}",
                    headers={"Authorization": f"Bearer {token}"}
                )
                
                # Update existing service
                res = await client.patch(
                    f"https://run.googleapis.com/v2/{service_path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "template": {
                            "containers": [{
                                "image": image_url,
                                "env": env_list,
                                "resources": {
                                    "limits": {
                                        "cpu": "2",
                                        "memory": "4Gi",
                                    }
                                },
                                "ports": [{"containerPort": service.get("port", 8080)}],
                            }],
                            "scaling": {
                                "minInstanceCount": 0,
                                "maxInstanceCount": 10,
                            },
                        }
                    }
                )
            except:
                # Create new service
                res = await client.post(
                    f"https://run.googleapis.com/v2/projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/services",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "name": f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/services/{service_name}",
                        "template": {
                            "containers": [{
                                "image": image_url,
                                "env": env_list,
                                "resources": {
                                    "limits": {
                                        "cpu": "2",
                                        "memory": "4Gi",
                                    }
                                },
                                "ports": [{"containerPort": service.get("port", 8080)}],
                            }],
                            "scaling": {
                                "minInstanceCount": 0,
                                "maxInstanceCount": 10,
                            },
                        },
                        "ingress": "INGRESS_TRAFFIC_ALL",
                    },
                )

            if res.status_code not in (200, 201, 202):
                logger.error(f"Pipeline Labs Runtime deployment failed: {res.text}")
                raise Exception(f"Deployment failed: {res.text}")

            # Make service public
            await client.post(
                f"https://run.googleapis.com/v2/{service_path}:setIamPolicy",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "policy": {
                        "bindings": [{
                            "role": "roles/run.invoker",
                            "members": ["allUsers"]
                        }]
                    }
                }
            )

            # Get service URL
            service_res = await client.get(
                f"https://run.googleapis.com/v2/{service_path}",
                headers={"Authorization": f"Bearer {token}"}
            )
            
            service_data = service_res.json()
            uri = service_data.get("uri", "")

            # Return branded response - NEVER expose the GCP URL
            return {
                "platform": "pipeline-labs",
                "service_name": service_name,
                "internal_url": uri,  # Hidden from users, stored in DB
                "status": "deployed",
            }
            
    except Exception as e:
        logger.error(f"Failed to deploy to Pipeline Labs Runtime: {e}")
        # Log real error internally
        logger.error(f"GCP error: {str(e)}")
        # Return branded error
        raise Exception(sanitize_error_message(str(e)))


async def deploy_to_vertex_ai(
    model_name: str,
    model_artifact_uri: str,
    serving_container_image: str,
) -> Dict[str, Any]:
    """Deploy an AI model to Vertex AI endpoint"""
    try:
        token = await get_gcp_token()

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Upload model
            model_res = await client.post(
                f"https://us-central1-aiplatform.googleapis.com/v1/projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/models:upload",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": {
                        "displayName": model_name,
                        "artifactUri": model_artifact_uri,
                        "containerSpec": {
                            "imageUri": serving_container_image,
                        },
                    }
                },
            )

            if model_res.status_code not in (200, 201):
                logger.error(f"Vertex AI model upload failed: {model_res.text}")
                raise Exception(f"Model upload failed: {model_res.text}")

            model_data = model_res.json()
            model_id = model_data.get("name", "").split("/")[-1]

            # Create endpoint
            endpoint_res = await client.post(
                f"https://us-central1-aiplatform.googleapis.com/v1/projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/endpoints",
                headers={"Authorization": f"Bearer {token}"},
                json={"displayName": f"{model_name}-endpoint"},
            )

            if endpoint_res.status_code not in (200, 201):
                logger.error(f"Vertex AI endpoint creation failed: {endpoint_res.text}")
                raise Exception(f"Endpoint creation failed: {endpoint_res.text}")

            endpoint_data = endpoint_res.json()
            endpoint_id = endpoint_data.get("name", "").split("/")[-1]

        return {
            "platform": "pipeline-labs-vertex",
            "model_id": model_id,
            "endpoint_id": endpoint_id,
            "status": "deploying",
        }
    except Exception as e:
        logger.error(f"Failed to deploy to Vertex AI: {e}")
        raise Exception(sanitize_error_message(str(e)))
