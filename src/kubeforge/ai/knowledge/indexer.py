"""Knowledge base indexer — bootstrap Qdrant with K3s/K8s docs."""

from __future__ import annotations

import hashlib
import logging

from kubeforge.ai.knowledge.prompts import CHUNK_PREFIX_DOC, CHUNK_PREFIX_EXAMPLE
from kubeforge.ai.ollama import generate_embedding
from kubeforge.config import settings

logger = logging.getLogger("kubeforge.ai.knowledge.indexer")

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


async def ensure_collections() -> None:
    """Create Qdrant collections if they don't exist."""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        client = QdrantClient(url=settings.qdrant.url)
        for collection in (settings.qdrant.docs_collection, settings.qdrant.manifests_collection):
            existing = [c.name for c in client.get_collections().collections]
            if collection not in existing:
                client.create_collection(
                    collection_name=collection,
                    vectors_config=VectorParams(size=settings.ai.embedding_dim, distance=Distance.COSINE),
                )
                logger.info(f"Created collection: {collection}")
    except Exception as e:
        logger.warning(f"Qdrant not available: {e}")


async def index_document(content: str, metadata: dict, collection: str | None = None) -> int:
    """Chunk, embed, and upsert a document into Qdrant. Returns chunk count."""
    collection = collection or settings.qdrant.docs_collection
    chunks = _split_chunks(content, CHUNK_SIZE, CHUNK_OVERLAP)
    if not chunks:
        return 0

    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct

        client = QdrantClient(url=settings.qdrant.url)
        points = []
        for i, chunk in enumerate(chunks):
            prefix = CHUNK_PREFIX_EXAMPLE if metadata.get("type") else CHUNK_PREFIX_DOC
            embedding = await generate_embedding(f"{prefix}{chunk}")
            if not embedding:
                continue
            points.append(PointStruct(
                id=_chunk_id(content, i),
                vector=embedding,
                payload={"text": chunk, "chunk_index": i, **metadata},
            ))

        if points:
            client.upsert(collection_name=collection, points=points)
        return len(points)
    except Exception as e:
        logger.warning(f"Indexing failed: {e}")
        return 0


async def index_builtin_knowledge() -> int:
    """Bootstrap the vector store with K3s/K8s knowledge."""
    total = 0
    for item in _builtin_knowledge():
        total += await index_document(item["content"], item["metadata"])
    logger.info(f"Indexed {total} chunks from built-in knowledge")
    return total


def _split_chunks(text: str, size: int, overlap: int) -> list[str]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    chunks, current, length = [], [], 0
    for line in lines:
        if length + len(line) > size and current:
            chunks.append("\n".join(current))
            keep, klen = [], 0
            for s in reversed(current):
                if klen + len(s) > overlap:
                    break
                keep.insert(0, s)
                klen += len(s)
            current, length = keep, klen
        current.append(line)
        length += len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _chunk_id(content: str, idx: int) -> str:
    return hashlib.sha256(f"{content[:200]}:{idx}".encode()).hexdigest()[:16]


def _builtin_knowledge() -> list[dict]:
    return [
        {"content": "K3s Resource Limits: Always set CPU/memory requests and limits. For single-node K3s keep total requests under 80% of capacity. Use LimitRange and ResourceQuota for namespace enforcement.", "metadata": {"source": "builtin", "topic": "resource-limits"}},
        {"content": "Traefik on K3s: Ships with Traefik v2 as default ingress controller. Use IngressRoute CRD for advanced routing. For air-gap TLS use cert-manager with self-signed CA.", "metadata": {"source": "builtin", "topic": "traefik-ingress"}},
        {"content": "Longhorn Storage: Distributed block storage for K3s. Default 3 replicas; single-node set to 1. Supports snapshots, encryption (LUKS), and S3 backups.", "metadata": {"source": "builtin", "topic": "longhorn-storage"}},
        {"content": "K3s Air-Gap: Place images in /var/lib/rancher/k3s/agent/images/. Configure registries.yaml at /etc/rancher/k3s/. Helm charts go in /var/lib/rancher/k3s/server/static/charts/.", "metadata": {"source": "builtin", "topic": "airgap"}},
        {"content": "K3s Security: Enable PSA (baseline/restricted). Use --secrets-encryption. Set automountServiceAccountToken: false. Apply default-deny NetworkPolicy. Enable audit logging.", "metadata": {"source": "builtin", "topic": "security"}},
    ]
