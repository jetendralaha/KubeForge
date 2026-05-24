"""Knowledge base sub-package for RAG."""

from kubeforge.ai.knowledge.indexer import ensure_collections, index_builtin_knowledge, index_document
from kubeforge.ai.knowledge.retriever import retrieve_context, retrieve_formatted

__all__ = [
    "ensure_collections",
    "index_builtin_knowledge",
    "index_document",
    "retrieve_context",
    "retrieve_formatted",
]
