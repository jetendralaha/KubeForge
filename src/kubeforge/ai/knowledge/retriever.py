"""RAG retriever — query Qdrant for relevant context."""

from __future__ import annotations

import logging

from kubeforge.ai.knowledge.prompts import CHUNK_PREFIX_MANIFEST
from kubeforge.ai.ollama import generate_embedding
from kubeforge.config import settings

logger = logging.getLogger("kubeforge.ai.knowledge.retriever")


async def retrieve_context(
    query: str,
    collection: str | None = None,
    top_k: int = 5,
    score_threshold: float = 0.6,
) -> list[dict]:
    """Retrieve relevant chunks from Qdrant."""
    collection = collection or settings.qdrant.docs_collection
    embedding = await generate_embedding(f"{CHUNK_PREFIX_MANIFEST}{query}")
    if not embedding:
        return []

    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=settings.qdrant.url)
        results = client.search(
            collection_name=collection,
            query_vector=embedding,
            limit=top_k,
            score_threshold=score_threshold,
        )
        return [
            {"text": h.payload.get("text", ""), "score": h.score,
             "source": h.payload.get("source", ""), "topic": h.payload.get("topic", "")}
            for h in results
        ]
    except Exception as e:
        logger.warning(f"Retrieval failed: {e}")
        return []


async def retrieve_formatted(query: str, top_k: int = 5) -> str:
    """Retrieve and format context as a single string for prompt injection."""
    contexts = await retrieve_context(query, top_k=top_k)
    if not contexts:
        return "No relevant context found."

    parts = []
    for i, ctx in enumerate(contexts, 1):
        parts.append(
            f"--- Context {i} (source: {ctx['source']}, topic: {ctx['topic']}, "
            f"score: {ctx['score']:.2f}) ---\n"
            f"{ctx['text']}\n"
        )
    return "\n".join(parts)
