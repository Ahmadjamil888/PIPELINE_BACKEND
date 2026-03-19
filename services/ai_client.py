"""
OpenRouter AI Client
Single interface for all AI calls via OpenRouter.
"""
import httpx
import os
import json
import logging

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"


async def call_ai(
    messages: list[dict],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4000,
) -> str:
    """
    Single function for all AI calls via OpenRouter.
    
    Args:
        messages: List of message dicts with role and content
        model: Model identifier (default: Claude Sonnet 4)
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate
        
    Returns:
        AI response content as string
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not configured")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        res = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://pipeline-labs.vercel.app",
                "X-Title": "Pipeline AI DevOps",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        
        if res.status_code != 200:
            logger.error(f"OpenRouter API error: {res.status_code} - {res.text}")
            raise Exception(f"OpenRouter API error: {res.status_code}")
        
        data = res.json()
        return data["choices"][0]["message"]["content"]


async def call_ai_json(
    messages: list[dict],
    model: str = DEFAULT_MODEL
) -> dict:
    """
    Call AI and parse JSON response.
    Automatically strips markdown code blocks if present.
    
    Args:
        messages: List of message dicts
        model: Model identifier
        
    Returns:
        Parsed JSON as dict
    """
    # Append instruction to return only JSON
    messages = messages.copy()
    messages[-1]["content"] += "\n\nRespond with ONLY valid JSON, no markdown, no explanation."
    
    content = await call_ai(messages, model=model)
    
    # Strip markdown code blocks if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {content[:500]}")
        raise Exception(f"AI returned invalid JSON: {str(e)}")
