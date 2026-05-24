"""Artifact type detection — heuristic + LLM."""

from __future__ import annotations

import logging

import yaml

from kubeforge.ai.ollama import chat_completion
from kubeforge.models import DetectionResult

logger = logging.getLogger("kubeforge.ai.detector")

DETECT_SYSTEM = """You are a DevOps expert. Identify the type of deployment artifact.
Return JSON: {"type": "docker-compose|kubernetes|helm|kustomize|unknown", "confidence": 0.0-1.0, "reasoning": "..."}"""


async def detect_type(content: str, filename: str = "") -> DetectionResult:
    """Detect the artifact type using heuristic analysis + LLM fallback."""
    # 1. Fast heuristic detection
    result = _heuristic_detect(content, filename)
    if result.confidence >= 0.8:
        return result

    # 2. LLM detection for ambiguous cases
    llm_result = await _llm_detect(content)
    if llm_result and llm_result.confidence > result.confidence:
        return llm_result

    return result


def _heuristic_detect(content: str, filename: str = "") -> DetectionResult:
    """Pattern-matching detection without LLM."""
    lower_name = filename.lower()

    try:
        docs = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return DetectionResult(type="unknown", confidence=0.1, reasoning="Invalid YAML")

    # Docker Compose
    for doc in docs:
        if isinstance(doc, dict) and "services" in doc:
            conf = 0.8
            if "compose" in lower_name or "docker" in lower_name:
                conf = 0.95
            return DetectionResult(type="docker-compose", confidence=conf, reasoning="Has 'services' key")

    # Kubernetes manifests
    has_api_version = False
    for doc in docs:
        if isinstance(doc, dict) and "apiVersion" in doc and "kind" in doc:
            has_api_version = True
            break

    if has_api_version:
        return DetectionResult(type="kubernetes", confidence=0.85, reasoning="Has apiVersion + kind")

    # Helm chart
    for doc in docs:
        if isinstance(doc, dict):
            if "apiVersion" in doc and doc.get("kind") == "":
                pass
            if "Chart.yaml" in filename or "templates" in filename:
                return DetectionResult(type="helm", confidence=0.7, reasoning="Helm chart structure")

    # Kustomize
    if "kustomization" in lower_name:
        return DetectionResult(type="kustomize", confidence=0.85, reasoning="Kustomization file")

    return DetectionResult(type="unknown", confidence=0.2, reasoning="No clear pattern match")


async def _llm_detect(content: str) -> DetectionResult | None:
    """Use LLM for deeper detection."""
    truncated = content[:3000]
    result = await chat_completion(
        prompt=f"Identify this deployment artifact type:\n\n```\n{truncated}\n```",
        system_prompt=DETECT_SYSTEM,
        json_schema={"type": "object"},
    )

    if "error" in result:
        return None

    try:
        return DetectionResult(
            type=result.get("type", "unknown"),
            confidence=float(result.get("confidence", 0.5)),
            reasoning=result.get("reasoning", "LLM detection"),
        )
    except (ValueError, TypeError):
        return None
