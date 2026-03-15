import os
import json
import logging
from typing import List, Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class OpenRouterClient:
    """Client for OpenRouter AI API with DeepSeek and other models."""
    
    API_BASE = "https://openrouter.ai/api/v1"
    
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.model = os.getenv("AI_MODEL", "deepseek/deepseek-chat")
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "4000"))
        self.temperature = float(os.getenv("AI_TEMPERATURE", "0.3"))
        
        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY not set - AI features will be disabled")
        
        self.client = httpx.AsyncClient(
            base_url=self.API_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://pipeline.dev",
                "X-Title": "Pipeline AI DevOps"
            },
            timeout=120.0
        )
    
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict] = None
    ) -> str:
        """Send chat completion request to OpenRouter."""
        if not self.api_key:
            raise RuntimeError("OpenRouter API key not configured")
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        
        if response_format:
            payload["response_format"] = response_format
        
        try:
            response = await self.client.post(
                "/chat/completions",
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            logger.error(f"OpenRouter API error: {e}")
            raise RuntimeError(f"AI request failed: {e}")
        except (KeyError, IndexError) as e:
            logger.error(f"Invalid response format: {e}")
            raise RuntimeError("Invalid AI response format")
    
    async def analyze_code(
        self,
        code: str,
        context: str = "",
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analyze code using AI."""
        
        system_prompt = """You are an expert code analyzer. Analyze the provided code and return a JSON response with:
- detected_framework: The framework used (e.g., nextjs, fastapi, django, react)
- language: The programming language
- dependencies: List of key dependencies detected
- entry_points: Main entry points (files that start the app)
- build_commands: Suggested build commands
- env_variables: Required environment variables
- port: Default port if detectable
- service_type: web_service, static_site, or worker

Return ONLY valid JSON, no markdown formatting."""
        
        user_prompt = f"Context: {context}\n\nCode to analyze:\n```{language or ''}\n{code}\n```"
        
        response = await self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse AI response as JSON: {response}")
            return {}
    
    async def analyze_monorepo(
        self,
        file_structure: List[str],
        package_files: Dict[str, str]
    ) -> Dict[str, Any]:
        """Analyze a monorepo structure and break down into services."""
        
        system_prompt = """You are an expert monorepo analyzer. Analyze the file structure and package configurations to:
1. Identify all individual services/applications
2. Determine dependencies between services
3. Suggest deployment order
4. Identify shared packages/libraries

Return a JSON response with:
{
  "services": [
    {
      "name": "service name",
      "path": "relative path in repo",
      "type": "frontend|backend|shared|worker",
      "framework": "detected framework",
      "depends_on": ["other service names"],
      "deploy_platform": "vercel|render|docker",
      "build_command": "suggested build command",
      "start_command": "suggested start command",
      "env_vars": ["REQUIRED_VAR_1", "REQUIRED_VAR_2"]
    }
  ],
  "shared_packages": ["paths to shared code"],
  "deployment_order": ["service names in deploy order"],
  "workspace_tool": "turborepo|nx|pnpm|lerna|none"
}"""
        
        # Build context from package files
        context = "File structure:\n" + "\n".join(file_structure[:100])  # Limit for token count
        context += "\n\nPackage configurations:\n"
        for path, content in package_files.items():
            context += f"\n--- {path} ---\n{content[:1000]}\n"  # Limit each file
        
        response = await self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            max_tokens=8000,
            response_format={"type": "json_object"}
        )
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse monorepo analysis: {response}")
            return {"services": [], "deployment_order": []}
    
    async def generate_deployment_plan(
        self,
        services: List[Dict[str, Any]],
        environment: str,
        constraints: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate an optimized deployment plan."""
        
        system_prompt = """You are a DevOps expert. Generate an optimized deployment plan considering:
- Parallel deployments where possible
- Service dependencies
- Environment-specific configurations
- Cost optimization

Return JSON with:
{
  "stages": [
    {
      "stage_number": 1,
      "services": ["service names to deploy in this stage"],
      "parallel": true|false,
      "estimated_duration_minutes": number
    }
  ],
  "total_estimated_duration": number,
  "environment_variables": {"service_name": {"VAR_NAME": "description"}},
  "health_checks": ["suggested health check endpoints"],
  "rollback_strategy": "description"
}"""
        
        user_prompt = f"Environment: {environment}\n\nServices:\n{json.dumps(services, indent=2)}"
        if constraints:
            user_prompt += f"\n\nConstraints:\n{json.dumps(constraints, indent=2)}"
        
        response = await self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=4000,
            response_format={"type": "json_object"}
        )
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse deployment plan: {response}")
            return {"stages": [], "total_estimated_duration": 0}
    
    async def suggest_environment_variables(
        self,
        service_name: str,
        code_samples: List[str],
        framework: str
    ) -> List[Dict[str, str]]:
        """Suggest required environment variables for a service."""
        
        system_prompt = """Analyze the code and suggest all environment variables needed.
Return a JSON array:
[
  {
    "name": "VAR_NAME",
    "description": "What this variable does",
    "required": true|false,
    "default_value": "suggested default or null",
    "source": "vercel|render|database|external"
  }
]"""
        
        context = f"Service: {service_name}\nFramework: {framework}\n\n"
        for i, code in enumerate(code_samples[:5]):
            context += f"Code sample {i+1}:\n{code[:500]}\n\n"
        
        response = await self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            response_format={"type": "json_object"}
        )
        
        try:
            data = json.loads(response)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            logger.error(f"Failed to parse env vars: {response}")
            return []
    
    async def analyze_build_logs(
        self,
        logs: str,
        service_name: str
    ) -> Dict[str, Any]:
        """Analyze build logs and suggest fixes for errors."""
        
        system_prompt = """You are a build troubleshooting expert. Analyze the logs and provide:
- error_summary: Brief description of the main error
- suggested_fix: Step-by-step fix
- is_retryable: Whether retrying might help
- severity: low|medium|high|critical"""
        
        response = await self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Service: {service_name}\n\nBuild logs:\n{logs[:3000]}"}
            ],
            response_format={"type": "json_object"}
        )
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {
                "error_summary": "Could not parse build error",
                "suggested_fix": "Please review logs manually",
                "is_retryable": False,
                "severity": "unknown"
            }
