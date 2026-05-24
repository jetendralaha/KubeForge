"""LLM client — OpenAI-compatible API for inference and embeddings.

Works with any provider that exposes the ``/v1/chat/completions`` endpoint:
- Ollama (local, no API key needed)
- OpenAI / Azure OpenAI
- Groq, Together, Mistral, vLLM, LM Studio, etc.

Configuration via environment variables:
  KUBEFORGE_AI_BASE_URL   — e.g. https://api.openai.com/v1
  KUBEFORGE_AI_API_KEY    — e.g. sk-...
  KUBEFORGE_AI_MODEL      — e.g. gpt-4o-mini
"""

from __future__ import annotations

import json
import logging

import httpx

from kubeforge.config import settings

logger = logging.getLogger("kubeforge.ai.llm")


def _auth_headers() -> dict[str, str]:
    """Build Authorization header if an API key is configured."""
    key = settings.ai.api_key
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


async def chat_completion(
    prompt: str,
    system_prompt: str = "",
    json_schema: dict | None = None,
    model: str = "",
    temperature: float = 0.3,
) -> dict:
    """Send a chat completion request to the configured LLM provider.

    Uses the OpenAI-compatible ``/v1/chat/completions`` endpoint.
    If json_schema is provided, instructs the model to return structured JSON.
    Returns the parsed JSON dict, or {"error": "..."} on failure.
    """
    model = model or settings.ai.model
    base_url = settings.ai.effective_base_url

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }

    if json_schema:
        # OpenAI: response_format; Ollama also supports this
        body["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=settings.ai.timeout, headers=_auth_headers()) as client:
            resp = await client.post(f"{base_url}/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()

        # OpenAI-compatible response: data["choices"][0]["message"]["content"]
        choices = data.get("choices", [])
        if not choices:
            return {"error": "Empty response from LLM", "raw": data}

        content = choices[0].get("message", {}).get("content", "")

        # Parse JSON response
        if json_schema or body.get("response_format"):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                    return json.loads(content)
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                    return json.loads(content)
                return {"error": "Failed to parse JSON response", "raw": content}

        return {"content": content}

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        detail = e.response.text[:200] if e.response.text else ""
        if status == 401:
            logger.error("LLM auth failed — check KUBEFORGE_AI_API_KEY")
            return {"error": "Authentication failed. Check your API key (KUBEFORGE_AI_API_KEY)."}
        logger.error(f"LLM HTTP error: {status} {detail}")
        return {"error": f"LLM HTTP error: {status}"}
    except httpx.ConnectError:
        logger.warning(f"Cannot connect to LLM at {base_url} — AI features disabled")
        return {"error": f"Cannot connect to LLM at {base_url}"}
    except Exception as e:
        logger.exception("LLM request failed")
        return {"error": str(e)}


async def generate_embedding(text: str, model: str = "") -> list[float]:
    """Generate an embedding vector.

    Uses ``/v1/embeddings`` (OpenAI-compatible).
    Returns an empty list on failure.
    """
    model = model or settings.ai.embedding_model
    base_url = settings.ai.effective_base_url

    try:
        async with httpx.AsyncClient(timeout=settings.ai.timeout, headers=_auth_headers()) as client:
            resp = await client.post(
                f"{base_url}/embeddings",
                json={"model": model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()

        # OpenAI format: data["data"][0]["embedding"]
        emb_data = data.get("data", [])
        if emb_data:
            return emb_data[0].get("embedding", [])

        # Fallback: Ollama native format
        embeddings = data.get("embeddings", [])
        if embeddings:
            return embeddings[0]
        return data.get("embedding", [])

    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}")
        return []


async def health_check() -> bool:
    """Check if the LLM endpoint is reachable."""
    base_url = settings.ai.effective_base_url
    try:
        async with httpx.AsyncClient(timeout=5.0, headers=_auth_headers()) as client:
            resp = await client.get(f"{base_url}/models")
            return resp.status_code == 200
    except Exception:
        return False
