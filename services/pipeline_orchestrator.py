"""
Main Pipeline Orchestrator
Ties all 5 agents together and runs them in sequence.
"""
import asyncio
import logging
from services.github_service import get_installation_token
from services.agents.scanner_agent import scan_repository
from services.agents.analyzer_agent import analyze_service
from services.agents.planner_agent import create_deployment_plan
from services.agents.deployer_agent import deploy_to_vercel, deploy_to_render
from services.agents.error_fixer_agent import fix_deployment_error
from services.db_service import (
    get_user_by_github_username,
    create_project,
    update_project_status,
    save_analysis_result,
    save_deployment,
    update_deployment_status,
    get_user_api_keys,
    emit_progress,
)

logger = logging.getLogger(__name__)


async def start_pipeline(
    repo_full_name: str,
    repo_id: int,
    installation_id: int,
    github_username: str,
):
    """
    Main orchestrator — runs all 5 agents in sequence.
    
    Flow:
    1. Setup: Get GitHub token, find user, create project
    2. Agent 1: Scan repository structure
    3. Agent 2: Analyze each service deeply
    4. Agent 3: Create deployment plan
    5. Agent 4: Deploy each service (with error handling)
    6. Agent 5: Fix errors and retry if needed
    """
    logger.info(f"Starting pipeline for {repo_full_name}")
    project_id = None
    
    try:
        # ── Setup ─────────────────────────────────────────────────────────────
        token = await get_installation_token(installation_id)
        user = await get_user_by_github_username(github_username)
        
        if not user:
            logger.error(f"User {github_username} not found in DB")
            return
        
        user_id = user["id"]
        api_keys = await get_user_api_keys(user_id)
        
        # Create project in DB
        project = await create_project(
            owner_id=user_id,
            repo_url=f"https://github.com/{repo_full_name}",
            repo_id=repo_id,
            name=repo_full_name.split("/")[-1],
        )
        project_id = project["id"]
        
        logger.info(f"Project created: {project_id}")
        
        # ── Agent 1: Scan ─────────────────────────────────────────────────────
        await emit_progress(project_id, "scanning", "Scanning repository structure...")
        await update_project_status(project_id, "analyzing")
        
        scan_result = await scan_repository(token, repo_full_name)
        services = scan_result["services"]
        
        await emit_progress(
            project_id, "scanned",
            f"Found {len(services)} service(s): {', '.join(s['name'] for s in services)}"
        )
        
        # ── Agent 2: Analyze each service ─────────────────────────────────────
        await emit_progress(project_id, "analyzing", "Analyzing each service...")
        analysis_results = []
        
        for service in services:
            await emit_progress(
                project_id, "analyzing",
                f"Analyzing {service['name']} ({service['framework']})..."
            )
            analysis = await analyze_service(token, repo_full_name, service)
            # Merge analysis back into service
            service.update(analysis)
            analysis_results.append(analysis)
        
        await save_analysis_result(project_id, scan_result, analysis_results)
        await update_project_status(project_id, "analyzed")
        
        await emit_progress(
            project_id, "analyzed",
            f"Analysis complete for {len(services)} services"
        )
        
        # ── Agent 3: Plan ─────────────────────────────────────────────────────
        await emit_progress(project_id, "planning", "Creating deployment plan...")
        plan = await create_deployment_plan(
            repo_full_name, scan_result, analysis_results, api_keys
        )
        
        await emit_progress(
            project_id, "planned",
            f"Deployment plan: {len(plan['services'])} services, ~{plan.get('total_estimated_minutes', '?')} min"
        )
        
        # ── Agent 4: Deploy each service ──────────────────────────────────────
        deployment_urls = {}
        
        for service_plan in plan["services"]:
            service_name = service_plan["name"]
            service = next(s for s in services if s["name"] == service_name)
            platform = service_plan["platform"]
            
            await emit_progress(
                project_id, "deploying",
                f"Deploying {service_name} to {platform}..."
            )
            
            # Resolve env vars (replace service references with actual URLs)
            env_vars = {}
            env_vars.update(service_plan.get("env_vars_static", {}))
            
            for k, v in service_plan.get("env_vars_from_services", {}).items():
                if isinstance(v, str) and "." in v:
                    ref_service = v.split(".")[0]
                    env_vars[k] = deployment_urls.get(ref_service, "")
                else:
                    env_vars[k] = v
            
            # Also add env vars from error fixer if any
            if service.get("fixed_env_vars"):
                env_vars.update(service["fixed_env_vars"])
            
            # Deploy
            deployment_result = None
            try:
                if platform == "vercel":
                    deployment_result = await deploy_to_vercel(
                        service, repo_full_name, token, env_vars
                    )
                elif platform == "render":
                    deployment_result = await deploy_to_render(
                        service, repo_full_name, env_vars
                    )
                else:
                    logger.warning(f"Unknown platform: {platform}")
                    continue
                
                # Check deployment status
                if deployment_result["status"] == "success":
                    deployment_urls[service_name] = deployment_result.get("url", "")
                    await save_deployment(project_id, service_name, platform, deployment_result)
                    await emit_progress(
                        project_id, "deployed",
                        f"✅ {service_name} deployed at {deployment_result.get('url')}"
                    )
                else:
                    # Deployment failed - try to get error logs
                    error_msg = f"Deployment failed with status: {deployment_result['status']}"
                    raise Exception(error_msg)
                    
            except Exception as e:
                error_logs = str(e)
                logger.error(f"Error deploying {service_name}: {error_logs}")
                
                await emit_progress(
                    project_id, "fixing",
                    f"🔧 Error deploying {service_name}, running AI fix..."
                )
                
                # ── Agent 5: Fix error and retry ──────────────────────────────
                fix_result = await fix_deployment_error(
                    token, repo_full_name, service, error_logs
                )
                
                if fix_result.get("fixed"):
                    await emit_progress(
                        project_id, "retrying",
                        f"🔄 Fix applied to {', '.join(fix_result['fixes_applied'])}, redeploying {service_name}..."
                    )
                    
                    # Add any new env vars from the fix
                    if fix_result.get("env_vars_to_add"):
                        service["fixed_env_vars"] = fix_result["env_vars_to_add"]
                    
                    # Wait for GitHub to process the push
                    await asyncio.sleep(10)
                    
                    # Retry deployment
                    try:
                        if platform == "vercel":
                            # Update env vars for retry
                            retry_env_vars = env_vars.copy()
                            if fix_result.get("env_vars_to_add"):
                                retry_env_vars.update(fix_result["env_vars_to_add"])
                            
                            deployment_result = await deploy_to_vercel(
                                service, repo_full_name, token, retry_env_vars
                            )
                        elif platform == "render":
                            retry_env_vars = env_vars.copy()
                            if fix_result.get("env_vars_to_add"):
                                retry_env_vars.update(fix_result["env_vars_to_add"])
                            
                            deployment_result = await deploy_to_render(
                                service, repo_full_name, retry_env_vars
                            )
                        
                        if deployment_result and deployment_result.get("status") == "success":
                            deployment_urls[service_name] = deployment_result.get("url", "")
                            await save_deployment(project_id, service_name, platform, deployment_result)
                            await emit_progress(
                                project_id, "deployed",
                                f"✅ {service_name} deployed after fix at {deployment_result.get('url')}"
                            )
                        else:
                            await emit_progress(
                                project_id, "failed",
                                f"❌ {service_name} still failing after fix"
                            )
                    except Exception as retry_error:
                        await emit_progress(
                            project_id, "failed",
                            f"❌ {service_name} retry failed: {str(retry_error)[:100]}"
                        )
                else:
                    await emit_progress(
                        project_id, "failed",
                        f"❌ Could not auto-fix {service_name}: {fix_result.get('reason', 'Unknown error')[:100]}"
                    )
        
        # ── Complete ──────────────────────────────────────────────────────────
        await update_project_status(project_id, "complete")
        await emit_progress(
            project_id, "complete",
            f"🎉 All services deployed! URLs: {', '.join([f'{k}: {v}' for k, v in deployment_urls.items()])}"
        )
        
        logger.info(f"Pipeline complete for {repo_full_name}")
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        if project_id:
            await update_project_status(project_id, "error")
            await emit_progress(project_id, "error", f"Pipeline error: {str(e)[:200]}")
