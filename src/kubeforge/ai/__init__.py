"""AI package — Ollama-based analysis, detection, risk, and recommendations.

All AI features are **optional**. The core conversion pipeline
(upload → parse → generate → package/ISO) works without Ollama or Qdrant.
These features only activate when Ollama is reachable and (for RAG)
when qdrant-client is installed.
"""

try:
    from kubeforge.ai.ollama import chat_completion, generate_embedding, health_check
    from kubeforge.ai.detector import detect_type
    from kubeforge.ai.risk import detect_risks
    from kubeforge.ai.recommendations import get_recommendations
except ImportError:  # pragma: no cover
    # AI deps not installed — provide stubs so the rest of the app loads
    async def chat_completion(*a, **kw):  # type: ignore[misc]
        return {"error": "AI features not installed. Install with: pip install kubeforge[ai]"}

    async def generate_embedding(*a, **kw):  # type: ignore[misc]
        return []

    async def health_check():  # type: ignore[misc]
        return False

    async def detect_type(*a, **kw):  # type: ignore[misc]
        return {"type": "unknown", "confidence": 0.0, "reasoning": "AI not available"}

    async def detect_risks(*a, **kw):  # type: ignore[misc]
        return {"risks": [], "summary": "AI not available"}

    async def get_recommendations(*a, **kw):  # type: ignore[misc]
        return {"recommendations": [], "summary": "AI not available"}

__all__ = [
    "chat_completion",
    "generate_embedding",
    "health_check",
    "detect_type",
    "detect_risks",
    "get_recommendations",
]
