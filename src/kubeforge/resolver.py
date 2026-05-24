"""Image resolver — extract, parse, and catalog images from manifests."""

from __future__ import annotations

import logging
import re

from kubeforge.models import Image
from kubeforge.models.manifest import ImageRef, NormalizedManifest

logger = logging.getLogger("kubeforge.resolver")

# Database patterns for automatic StatefulSet promotion
DB_PATTERNS = re.compile(
    r"(postgres|mysql|mariadb|mongo|redis|memcached|elasticsearch|minio|cockroach|cassandra|etcd|consul|vault)",
    re.IGNORECASE,
)

# Web-facing port patterns
WEB_PORTS = {80, 443, 8080, 8443, 3000, 5000, 8000, 9090}


def extract_images(manifest: NormalizedManifest) -> list[ImageRef]:
    """Get all unique image references from a manifest."""
    seen: set[str] = set()
    images: list[ImageRef] = []
    for w in manifest.workloads:
        full = w.image.full
        if full not in seen:
            seen.add(full)
            images.append(w.image)
    return images


def resolve_image(ref: ImageRef) -> Image:
    """Convert an ImageRef to a storable Image model."""
    return Image(
        reference=ref.full,
        registry=ref.registry,
        repository=ref.repository,
        tag=ref.tag,
        digest=ref.digest,
    )


def resolve_all(manifest: NormalizedManifest) -> list[Image]:
    """Resolve all images from a manifest into Image models."""
    refs = extract_images(manifest)
    return [resolve_image(r) for r in refs]


def detect_databases(manifest: NormalizedManifest) -> list[str]:
    """Detect database workloads by image name pattern matching."""
    dbs: list[str] = []
    for w in manifest.workloads:
        if DB_PATTERNS.search(w.image.repository):
            dbs.append(w.name)
    return dbs


def detect_exposed_services(manifest: NormalizedManifest) -> list[str]:
    """Detect services that expose web-facing ports."""
    exposed: list[str] = []
    for w in manifest.workloads:
        for p in w.ports:
            if p.container_port in WEB_PORTS:
                exposed.append(w.name)
                break
    return exposed


def build_dependency_graph(manifest: NormalizedManifest) -> dict[str, list[str]]:
    """Build a dependency graph from workload dependencies.

    Returns: {workload_name: [dependency_names]}
    """
    graph: dict[str, list[str]] = {}
    for w in manifest.workloads:
        graph[w.name] = [d.name for d in w.dependencies]
    return graph
