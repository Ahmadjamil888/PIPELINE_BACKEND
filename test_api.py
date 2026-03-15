#!/usr/bin/env python3
"""
Pipeline AI DevOps Platform - API Test Script
Tests all endpoints to verify functionality
"""

import asyncio
import httpx
import json
import sys
from datetime import datetime
from typing import Dict, Any, Optional

# Configuration
BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api/v1"
TIMEOUT = 30.0

# Test results storage
test_results: list = []


def log_test(name: str, passed: bool, details: str = "", error: Optional[str] = None):
    """Log test result"""
    status = "✅ PASS" if passed else "❌ FAIL"
    result = {"name": name, "passed": passed, "details": details, "error": error}
    test_results.append(result)
    
    print(f"\n{status} - {name}")
    if details:
        print(f"   Details: {details}")
    if error:
        print(f"   Error: {error}")


async def test_health_endpoint():
    """Test the health check endpoint"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.get(f"{BASE_URL}/health")
            
            if response.status_code == 200:
                data = response.json()
                status = data.get("status", "unknown")
                services = data.get("services", {})
                
                details = f"Status: {status}"
                if services:
                    details += f" | Services: {', '.join([f'{k}={v}' for k, v in services.items()])}"
                
                log_test("Health Check", True, details)
                return True
            else:
                log_test("Health Check", False, f"Status code: {response.status_code}")
                return False
        except Exception as e:
            log_test("Health Check", False, error=str(e))
            return False


async def test_openapi_docs():
    """Test OpenAPI documentation endpoints"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            # Test Swagger UI
            response_swagger = await client.get(f"{BASE_URL}/docs")
            swagger_ok = response_swagger.status_code == 200
            
            # Test ReDoc
            response_redoc = await client.get(f"{BASE_URL}/redoc")
            redoc_ok = response_redoc.status_code == 200
            
            # Test OpenAPI JSON
            response_openapi = await client.get(f"{BASE_URL}/openapi.json")
            openapi_ok = response_openapi.status_code == 200
            
            if swagger_ok and redoc_ok and openapi_ok:
                log_test("OpenAPI Docs", True, "Swagger UI, ReDoc, and OpenAPI JSON all accessible")
                return True
            else:
                log_test("OpenAPI Docs", False, f"Swagger: {swagger_ok}, ReDoc: {redoc_ok}, OpenAPI: {openapi_ok}")
                return False
        except Exception as e:
            log_test("OpenAPI Docs", False, error=str(e))
            return False


async def test_repository_endpoints():
    """Test repository management endpoints"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            # Test: Connect a repo
            repo_data = {
                "repo_url": "https://github.com/example/test-repo",
                "provider": "github",
                "branch": "main",
                "name": "test-repo"
            }
            
            response = await client.post(
                f"{API_URL}/repos/connect",
                json=repo_data
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                repo_id = data.get("id")
                log_test("Repo Connect", True, f"Connected repo with ID: {repo_id}")
                
                # Test: List repos
                list_response = await client.get(f"{API_URL}/repos")
                if list_response.status_code == 200:
                    log_test("Repo List", True)
                else:
                    log_test("Repo List", False, f"Status: {list_response.status_code}")
                
                # Test: Get repo details
                if repo_id:
                    get_response = await client.get(f"{API_URL}/repos/{repo_id}")
                    if get_response.status_code == 200:
                        log_test("Repo Get", True)
                    else:
                        log_test("Repo Get", False, f"Status: {get_response.status_code}")
                
                return True
            else:
                log_test("Repo Connect", False, f"Status: {response.status_code}, Body: {response.text}")
                return False
                
        except Exception as e:
            log_test("Repository Endpoints", False, error=str(e))
            return False


async def test_deployment_endpoints():
    """Test deployment management endpoints"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            # Test: List deployments
            response = await client.get(f"{API_URL}/deployments")
            
            if response.status_code == 200:
                data = response.json()
                total = data.get("total", 0)
                log_test("Deployment List", True, f"Found {total} deployments")
                return True
            else:
                log_test("Deployment List", False, f"Status: {response.status_code}")
                return False
                
        except Exception as e:
            log_test("Deployment Endpoints", False, error=str(e))
            return False


async def test_sandbox_endpoints():
    """Test sandbox management endpoints"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            # Test: List sandboxes
            response = await client.get(f"{API_URL}/sandboxes")
            
            if response.status_code == 200:
                data = response.json()
                total = data.get("total", 0)
                log_test("Sandbox List", True, f"Found {total} sandboxes")
                return True
            else:
                log_test("Sandbox List", False, f"Status: {response.status_code}")
                return False
                
        except Exception as e:
            log_test("Sandbox Endpoints", False, error=str(e))
            return False


async def test_dashboard_endpoints():
    """Test dashboard endpoints"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            # Test: Dashboard stats
            stats_response = await client.get(f"{API_URL}/dashboard/stats")
            
            if stats_response.status_code == 200:
                data = stats_response.json()
                repos = data.get("repositories", {})
                deps = data.get("deployments", {})
                log_test(
                    "Dashboard Stats", 
                    True, 
                    f"Repos: {repos.get('total', 0)}, Deployments: {deps.get('total', 0)}"
                )
            else:
                log_test("Dashboard Stats", False, f"Status: {stats_response.status_code}")
            
            # Test: Dashboard deployments
            deps_response = await client.get(f"{API_URL}/dashboard/deployments")
            if deps_response.status_code == 200:
                log_test("Dashboard Deployments", True)
            else:
                log_test("Dashboard Deployments", False, f"Status: {deps_response.status_code}")
            
            # Test: Dashboard projects
            projects_response = await client.get(f"{API_URL}/dashboard/projects")
            if projects_response.status_code == 200:
                log_test("Dashboard Projects", True)
            else:
                log_test("Dashboard Projects", False, f"Status: {projects_response.status_code}")
            
            return True
                
        except Exception as e:
            log_test("Dashboard Endpoints", False, error=str(e))
            return False


async def test_root_endpoint():
    """Test root API endpoint"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.get(BASE_URL)
            
            if response.status_code == 200:
                data = response.json()
                name = data.get("name", "Unknown")
                version = data.get("version", "Unknown")
                log_test("Root Endpoint", True, f"{name} v{version}")
                return True
            else:
                log_test("Root Endpoint", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            log_test("Root Endpoint", False, error=str(e))
            return False


async def run_all_tests():
    """Run all API tests"""
    print("=" * 60)
    print("Pipeline AI DevOps Platform - API Test Suite")
    print("=" * 60)
    print(f"Testing against: {BASE_URL}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)
    
    tests = [
        ("Root Endpoint", test_root_endpoint),
        ("Health Check", test_health_endpoint),
        ("OpenAPI Docs", test_openapi_docs),
        ("Repository Endpoints", test_repository_endpoints),
        ("Deployment Endpoints", test_deployment_endpoints),
        ("Sandbox Endpoints", test_sandbox_endpoints),
        ("Dashboard Endpoints", test_dashboard_endpoints),
    ]
    
    for name, test_func in tests:
        try:
            await test_func()
        except Exception as e:
            log_test(name, False, error=f"Test crashed: {str(e)}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for r in test_results if r["passed"])
    failed = sum(1 for r in test_results if not r["passed"])
    total = len(test_results)
    
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")
    print(f"Success Rate: {(passed/total*100):.1f}%" if total > 0 else "N/A")
    
    if failed > 0:
        print("\nFailed Tests:")
        for r in test_results:
            if not r["passed"]:
                print(f"  - {r['name']}: {r.get('error', 'No details')}")
    
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    # Check if server is running
    try:
        import httpx
        response = httpx.get(BASE_URL, timeout=5.0)
        print(f"Server detected at {BASE_URL}")
    except Exception as e:
        print(f"\n⚠️  WARNING: Cannot connect to {BASE_URL}")
        print(f"   Error: {e}")
        print(f"\n   Please start the backend server first:")
        print(f"   cd backend && uvicorn main:app --reload")
        print()
        sys.exit(1)
    
    # Run tests
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
