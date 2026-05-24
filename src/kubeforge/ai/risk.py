"""Security and reliability risk analysis — heuristic + LLM."""

from __future__ import annotations

import logging

import yaml

from kubeforge.ai.ollama import chat_completion
from kubeforge.models import RiskItem, RiskResult

logger = logging.getLogger("kubeforge.ai.risk")

RISK_SYSTEM = (
    "You are a Kubernetes security expert specialising in K3s.\n"
    "Analyse the manifest for security risks, misconfigurations, and reliability "
    "concerns.\n\n"
    "Categories: security, reliability, performance, networking, storage, configuration\n"
    "Severity: critical, high, medium, low, info\n\n"
    "Return JSON: {\"risks\": [{\"title\": \"...\", \"description\": \"...\", "
    "\"severity\": \"...\", \"category\": \"...\", \"remediation\": "
    "\"...\"}]}"
)


async def detect_risks(
    content: str,
    artifact_type: str = "",
    project_id: str = "",
    analysis_id: str = "",
) -> RiskResult:
    """Analyse content for deployment risks."""
    risks: list[RiskItem] = []

    # 1. Heuristic checks (instant, no LLM needed)
    heuristic = _heuristic_risks(content, artifact_type)
    risks.extend(heuristic)

    # 2. LLM deep analysis
    llm = await _llm_risks(content, artifact_type)
    risks.extend(llm)

    # Deduplicate by title
    seen: set[str] = set()
    unique: list[RiskItem] = []
    for r in risks:
        if r.title not in seen:
            seen.add(r.title)
            # Inject project/analysis IDs
            r.project_id = project_id
            r.analysis_id = analysis_id
            unique.append(r)

    # Sort by severity
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    unique.sort(key=lambda r: sev_order.get(r.severity, 5))

    summary = f"Found {len(unique)} risk(s): " + ", ".join(
        f"{sum(1 for r in unique if r.severity == s)} {s}" for s in ("critical", "high", "medium", "low")
        if any(r.severity == s for r in unique)
    )

    return RiskResult(risks=unique, summary=summary)


def _heuristic_risks(content: str, artifact_type: str) -> list[RiskItem]:
    """Pattern-based risk detection."""
    risks: list[RiskItem] = []

    try:
        docs = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return risks

    for doc in docs:
        if not isinstance(doc, dict):
            continue

        # ── Docker Compose checks ───────────────────────
        services = doc.get("services", {})
        if isinstance(services, dict):
            for name, svc in services.items():
                if not isinstance(svc, dict):
                    continue

                image = svc.get("image", "")

                if ":latest" in image or ":" not in str(image):
                    risks.append(RiskItem(
                        title=f"'{name}' uses :latest tag",
                        description="Latest tags are mutable and break reproducibility in air-gap deployments.",
                        severity="high", category="reliability",
                        remediation=f"Pin to a specific version, e.g. {image.split(':')[0]}:1.0.0",
                    ))

                if svc.get("privileged"):
                    risks.append(RiskItem(
                        title=f"'{name}' runs privileged",
                        description="Privileged containers have full host access. This breaks Pod Security Admission.",
                        severity="critical", category="security",
                        remediation="Remove 'privileged: true' and use specific capabilities instead.",
                    ))

                if not svc.get("healthcheck"):
                    risks.append(RiskItem(
                        title=f"'{name}' has no healthcheck",
                        description="Without health checks, K8s cannot detect and restart unhealthy containers.",
                        severity="medium", category="reliability",
                        remediation="Add a healthcheck with a CMD or HTTP endpoint.",
                    ))

                if svc.get("network_mode") == "host":
                    risks.append(RiskItem(
                        title=f"'{name}' uses host networking",
                        description="Host networking bypasses network isolation and network policies.",
                        severity="high", category="security",
                        remediation="Remove 'network_mode: host' and use port mappings instead.",
                    ))

                # Secrets in plain env vars
                env = svc.get("environment", {})
                if isinstance(env, dict):
                    env_items = env.items()
                else:
                    env_items = []
                    seq = env if isinstance(env, list) else []
                    for e in seq:
                        if "=" in str(e):
                            k, v = e.split("=", 1)
                            env_items.append((k, v))
                        else:
                            env_items.append((e, ""))

                for k, v in env_items:
                    k_lower = str(k).lower()
                    if any(s in k_lower for s in ("password", "secret", "key", "token")) and v:
                        risks.append(RiskItem(
                            title=f"'{name}' has hardcoded secret: {k}",
                            description="Secrets in environment variables are visible in container inspect and logs.",
                            severity="high", category="security",
                            remediation="Use Kubernetes Secrets and mount via secretKeyRef.",
                        ))

                # No resource limits
                deploy = svc.get("deploy", {})
                if not deploy or not deploy.get("resources", {}).get("limits"):
                    risks.append(RiskItem(
                        title=f"'{name}' has no resource limits",
                        description="Without limits, a container can consume all node resources.",
                        severity="medium", category="performance",
                        remediation="Add deploy.resources.limits with cpu and memory.",
                    ))

        # ── Kubernetes manifest checks ─────────────────
        kind = doc.get("kind", "")
        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            metadata = doc.get("metadata", {}) or {}
            name = metadata.get("name", "unnamed")
            spec = doc.get("spec", {}) or {}
            template = spec.get("template", {}) or {}
            containers = template.get("spec", {}).get("containers", [])

            for c in containers:
                if not isinstance(c, dict):
                    continue
                img = c.get("image", "")

                if ":latest" in img or (img and ":" not in img):
                    risks.append(RiskItem(
                        title=f"'{name}' uses :latest tag",
                        description="Mutable tags break reproducibility.",
                        severity="high", category="reliability",
                        remediation="Pin a specific image version.",
                    ))

                if not c.get("resources", {}).get("limits"):
                    risks.append(RiskItem(
                        title=f"'{name}' has no resource limits",
                        severity="medium", category="performance",
                        remediation="Set resources.limits.cpu and resources.limits.memory.",
                    ))

                sc = c.get("securityContext", {})
                if sc.get("privileged"):
                    risks.append(RiskItem(
                        title=f"'{name}' runs privileged",
                        severity="critical", category="security",
                        remediation="Remove securityContext.privileged or set to false.",
                    ))

                if not sc.get("runAsNonRoot"):
                    risks.append(RiskItem(
                        title=f"'{name}' may run as root",
                        severity="medium", category="security",
                        remediation="Set securityContext.runAsNonRoot: true.",
                    ))

                if not c.get("livenessProbe") and not c.get("readinessProbe"):
                    risks.append(RiskItem(
                        title=f"'{name}' has no health probes",
                        severity="medium", category="reliability",
                        remediation="Add livenessProbe and readinessProbe.",
                    ))

    return risks


async def _llm_risks(content: str, artifact_type: str) -> list[RiskItem]:
    """Use LLM for deeper risk analysis."""
    truncated = content[:4000]
    prompt = (
        f"Analyse this {artifact_type or 'deployment'} for risks:\n\n"
        "```yaml\n"
        f"{truncated}\n"
        "```"
    )

    result = await chat_completion(
        prompt=prompt,
        system_prompt=RISK_SYSTEM,
        json_schema={"type": "object"},
    )

    if "error" in result:
        return []

    items: list[RiskItem] = []
    for r in result.get("risks", []):
        try:
            items.append(RiskItem(
                title=r.get("title", ""),
                description=r.get("description", ""),
                severity=r.get("severity", "medium"),
                category=r.get("category", ""),
                remediation=r.get("remediation", ""),
            ))
        except Exception:
            continue

    return items
