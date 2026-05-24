"""Prompt templates and JSON schemas for AI interactions."""

# ── Structured output schemas ───────────────────────────────────────

DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["docker-compose", "kubernetes", "helm", "kustomize", "unknown"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
}

RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string"},
                    "category": {"type": "string"},
                    "remediation": {"type": "string"},
                },
            },
        },
    },
}

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string"},
                    "auto_apply": {"type": "boolean"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}

# ── Embedding prefixes for domain separation ────────────────────────

CHUNK_PREFIX_DOC = "documentation: "
CHUNK_PREFIX_EXAMPLE = "example: "
CHUNK_PREFIX_MANIFEST = "manifest: "
