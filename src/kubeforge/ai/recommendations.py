"""Best practices recommendation engine — heuristic + LLM."""

from __future__ import annotations

import logging

import yaml

from kubeforge.ai.ollama import chat_completion
from kubeforge.models import RecommendationItem, RecommendationResult

logger = logging.getLogger("kubeforge.ai.recommendations")

RECOMMEND_SYSTEM = (
    "You are a K3s deployment expert. Recommend best practices for production "
    "deployment on K3s with Traefik ingress and Longhorn storage.\n\n"
    "Focus on: resource optimization, HA, security hardening, ingress/TLS, "
    "storage, health probes, image management.\n\n"
    "Return JSON: {\"recommendations\": [{\"category\": \"...\", \"title\": "
    "\"...\", \"description\": \"...\", \"priority\": \"high|medium|low\", "
    "\"auto_apply\": true|false}], \"summary\": \"...\"}"
)


async def get_recommendations(content: str, artifact_type: str = "") -> RecommendationResult:
    """Generate K3s deployment recommendations."""
    recs: list[RecommendationItem] = []

    recs.extend(_heuristic_recommendations(content, artifact_type))
    recs.extend(await _llm_recommendations(content, artifact_type))

    # Deduplicate
    seen: set[str] = set()
    unique: list[RecommendationItem] = []
    for r in recs:
        if r.title not in seen:
            seen.add(r.title)
            unique.append(r)

    priority_order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda r: priority_order.get(r.priority, 3))

    return RecommendationResult(
        recommendations=unique,
        summary=f"Generated {len(unique)} recommendation(s) for K3s deployment.",
    )


def _heuristic_recommendations(content: str, artifact_type: str) -> list[RecommendationItem]:
    recs: list[RecommendationItem] = []

    try:
        docs = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return recs

    has_ingress = False
    has_netpol = False
    has_hpa = False
    service_count = 0
    db_detected = False
    db_images = ["postgres", "mysql", "mariadb", "mongo", "redis", "elasticsearch"]

    for doc in docs:
        if not isinstance(doc, dict):
            continue

        kind = doc.get("kind", "")
        if kind in ("Ingress", "IngressRoute"):
            has_ingress = True
        if kind == "NetworkPolicy":
            has_netpol = True
        if kind == "HorizontalPodAutoscaler":
            has_hpa = True

        # Compose services
        services = doc.get("services", {})
        if isinstance(services, dict):
            service_count = len(services)
            for svc in services.values():
                if isinstance(svc, dict):
                    img = svc.get("image", "").lower()
                    if any(db in img for db in db_images):
                        db_detected = True

        # K8s workloads
        if kind in ("Deployment", "StatefulSet"):
            containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            for c in containers:
                if isinstance(c, dict) and any(db in c.get("image", "").lower() for db in db_images):
                    db_detected = True

    if not has_ingress and service_count > 0:
        recs.append(RecommendationItem(
            category="networking", title="Add Traefik Ingress",
            description="Configure Traefik IngressRoute for external access. K3s includes Traefik by default.",
            priority="high", auto_apply=True,
        ))

    recs.append(RecommendationItem(
        category="security", title="Enable TLS with self-signed CA",
        description="For air-gap deployments, generate a self-signed CA and configure cert-manager.",
        priority="high", auto_apply=True,
    ))

    if db_detected:
        recs.append(RecommendationItem(
            category="storage", title="Use Longhorn StorageClass for databases",
            description="Configure Longhorn with 3 replicas for data resilience. Use ReadWriteOnce.",
            priority="high", auto_apply=True,
        ))
        recs.append(RecommendationItem(
            category="reliability", title="Use StatefulSet for databases",
            description="Databases need StatefulSet for stable network identities and ordered management.",
            priority="high", auto_apply=True,
        ))

    if not has_netpol:
        recs.append(RecommendationItem(
            category="security", title="Add default-deny NetworkPolicy",
            description="Apply default-deny for ingress and egress, then add explicit allow rules.",
            priority="medium", auto_apply=True,
        ))

    recs.append(RecommendationItem(
        category="security", title="Enable Pod Security Admission",
        description="Set pod-security.kubernetes.io/enforce: baseline on the namespace.",
        priority="medium", auto_apply=True,
    ))

    if not has_hpa and service_count > 1:
        recs.append(RecommendationItem(
            category="performance", title="Consider HorizontalPodAutoscaler",
            description="Add HPA for web-facing services to auto-scale on CPU/memory.",
            priority="low", auto_apply=False,
        ))

    recs.append(RecommendationItem(
        category="reliability", title="Add PodDisruptionBudget",
        description="For replicated services, add PDB to ensure availability during updates.",
        priority="medium", auto_apply=True,
    ))

    return recs


async def _llm_recommendations(content: str, artifact_type: str) -> list[RecommendationItem]:
    truncated = content[:4000]
    prompt = (
        f"Analyse this {artifact_type or 'deployment'} for K3s:\n\n"
        "```yaml\n"
        f"{truncated}\n"
        "```\n\n"
        "Recommend optimisations."
    )

    result = await chat_completion(
        prompt=prompt,
        system_prompt=RECOMMEND_SYSTEM,
        json_schema={"type": "object"},
    )

    if "error" in result:
        return []

    items: list[RecommendationItem] = []
    for r in result.get("recommendations", []):
        try:
            items.append(RecommendationItem(**r))
        except Exception:
            continue
    return items
