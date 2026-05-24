"""K8s manifest generator — converts NormalizedManifest to production-ready YAML.

Generates security-hardened manifests for K3s with:
- Pod Security Admission labels
- Network Policies (default-deny + explicit allows)
- Resource limits
- Longhorn StorageClass for PVCs
- Traefik Ingress annotations
- ServiceAccount with automountServiceAccountToken: false
"""

from __future__ import annotations

import logging

import yaml

from kubeforge.models.manifest import NormalizedManifest, Workload
from kubeforge.resolver import build_dependency_graph, detect_databases, detect_exposed_services

logger = logging.getLogger("kubeforge.generator")


def generate(manifest: NormalizedManifest, namespace: str = "", skip_hardening: bool = False) -> dict[str, str]:
    """Generate production-ready K8s YAML files from a NormalizedManifest.

    Args:
        manifest: The normalized intermediate representation.
        namespace: Override namespace (falls back to manifest.namespace or "default").
        skip_hardening: If True, skip resource-limit injection and security-context
                        overrides.  Used for Helm-rendered manifests that already
                        contain their own settings.

    Returns: {filename: yaml_content}
    """
    ns = namespace or manifest.namespace or "default"
    files: dict[str, str] = {}

    # 1. Namespace with PSA labels
    files["00-namespace.yaml"] = _render_namespace(ns)

    # 2. ServiceAccounts
    sa_names: set[str] = set()
    for w in manifest.workloads:
        sa_name = f"{w.name}-sa"
        sa_names.add(sa_name)

    if sa_names:
        files["01-serviceaccounts.yaml"] = _render_service_accounts(ns, sa_names)

    # 3. ConfigMaps
    if manifest.config_maps:
        docs = []
        for cm in manifest.config_maps:
            docs.append(_build_configmap(ns, cm.name, cm.data))
        files["02-configmaps.yaml"] = _multi_doc(docs)

    # 4. Secrets
    if manifest.secrets:
        docs = []
        for s in manifest.secrets:
            docs.append(_build_secret(ns, s.name, s.data, s.type))
        files["03-secrets.yaml"] = _multi_doc(docs)

    # 5. PVCs (Longhorn)
    pvc_docs = []
    for v in manifest.volumes:
        if v.type == "pvc":
            pvc_docs.append(_build_pvc(ns, v.name, v.size or "1Gi", v.storage_class or "longhorn"))
    if pvc_docs:
        files["04-pvcs.yaml"] = _multi_doc(pvc_docs)

    # 6. Workloads (Deployment / StatefulSet)
    db_names = set(detect_databases(manifest))
    workload_docs = []
    for w in manifest.workloads:
        is_stateful = w.is_stateful or w.name in db_names
        sa_name = f"{w.name}-sa"
        workload_docs.append(_build_workload(ns, w, is_stateful, sa_name, skip_hardening=skip_hardening))
    if workload_docs:
        files["05-workloads.yaml"] = _multi_doc(workload_docs)

    # 7. Services
    svc_docs = []
    for svc in manifest.services:
        svc_docs.append(_build_service(ns, svc.name, svc.selector, svc.ports, svc.type))
    if svc_docs:
        files["06-services.yaml"] = _multi_doc(svc_docs)

    # 8. Ingresses (Traefik)
    if manifest.ingresses:
        ing_docs = []
        for ing in manifest.ingresses:
            ing_docs.append(_build_ingress(ns, ing))
        files["07-ingresses.yaml"] = _multi_doc(ing_docs)

    # 9. Network Policies
    dep_graph = build_dependency_graph(manifest)
    exposed = detect_exposed_services(manifest)
    np_docs = _build_network_policies(ns, manifest, dep_graph, exposed)
    if np_docs:
        files["08-networkpolicies.yaml"] = _multi_doc(np_docs)

    # 10. Raw resources (passthrough -- CRDs, RBAC, Jobs, etc.)
    if manifest.raw_resources:
        raw_docs = []
        # Resource kinds that are namespace-scoped (should get namespace injected)
        # Cluster-scoped kinds that should NOT get a namespace
        cluster_kinds = {
            "ClusterRole", "ClusterRoleBinding", "Namespace",
            "PersistentVolume", "StorageClass", "IngressClass",
            "CustomResourceDefinition", "MutatingWebhookConfiguration",
            "ValidatingWebhookConfiguration", "PriorityClass",
        }
        for raw in manifest.raw_resources:
            doc = {
                "apiVersion": raw.api_version,
                "kind": raw.kind,
                "metadata": dict(raw.metadata),  # Copy to avoid mutating original
            }
            # Inject namespace for namespace-scoped resources that don't have one
            if raw.kind not in cluster_kinds:
                if "namespace" not in doc["metadata"] or not doc["metadata"]["namespace"]:
                    doc["metadata"]["namespace"] = ns
            if raw.spec:
                doc["spec"] = raw.spec
            raw_docs.append(doc)
        files["09-raw-resources.yaml"] = _multi_doc(raw_docs)

    logger.info(f"Generated {len(files)} manifest files for namespace '{ns}'")
    return files


def generate_combined(manifest: NormalizedManifest, namespace: str = "", skip_hardening: bool = False) -> str:
    """Generate a single combined YAML with all resources in proper dependency order.

    This is the recommended output for air-gap/bootable deployments where
    kubectl apply -f deploy.yaml handles all resources atomically.
    Resource order follows K8s best practices:
      Namespace → SA → ConfigMap → Secret → PVC → Workload → Service → Ingress → NetworkPolicy → Raw
    """
    files = generate(manifest, namespace, skip_hardening)
    # Files are already numbered in dependency order (00-namespace, 01-sa, etc.)
    combined_parts = []
    for filename in sorted(files.keys()):
        combined_parts.append(f"# --- {filename} ---")
        combined_parts.append(files[filename])
    return "\n---\n".join(combined_parts)


# ── Builders ────────────────────────────────────────────────────────


def _render_namespace(ns: str) -> str:
    doc = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": ns,
            "labels": {
                "pod-security.kubernetes.io/enforce": "baseline",
                "pod-security.kubernetes.io/warn": "restricted",
            },
        },
    }
    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _render_service_accounts(ns: str, names: set[str]) -> str:
    docs = []
    for name in sorted(names):
        docs.append({
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": name, "namespace": ns},
            "automountServiceAccountToken": False,
        })
    return _multi_doc(docs)


def _build_configmap(ns: str, name: str, data: dict) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": ns},
        "data": data,
    }


def _build_secret(ns: str, name: str, data: dict, secret_type: str = "Opaque") -> dict:
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": ns},
        "type": secret_type,
        "stringData": data,
    }
    # Validate dockerconfigjson secrets have valid JSON
    if secret_type == "kubernetes.io/dockerconfigjson":
        import json
        docker_key = ".dockerconfigjson"
        if docker_key in data:
            try:
                # Validate it's proper JSON
                json.loads(data[docker_key])
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"Secret '{name}' has invalid JSON in {docker_key}. "
                    "This may cause apply errors."
                )
    return secret


def _build_pvc(ns: str, name: str, size: str, storage_class: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": storage_class,
            "resources": {"requests": {"storage": size}},
        },
    }


def _build_workload(ns: str, w: Workload, is_stateful: bool, sa_name: str, skip_hardening: bool = False) -> dict:
    kind = "StatefulSet" if is_stateful else "Deployment"
    labels = {"app": w.name, **w.labels}

    container: dict = {
        "name": w.name,
        "image": w.image.full,
        "imagePullPolicy": "IfNotPresent",
    }

    if w.command:
        container["command"] = w.command
    if w.args:
        container["args"] = w.args

    if w.ports:
        container["ports"] = [
            {"containerPort": p.container_port, "protocol": p.protocol}
            for p in w.ports
        ]

    if w.env:
        env_list = []
        for e in w.env:
            if e.value_from and e.value_from.startswith("secret:"):
                parts = e.value_from.split(":")
                env_list.append({
                    "name": e.name,
                    "valueFrom": {"secretKeyRef": {"name": parts[1], "key": parts[2] if len(parts) > 2 else ""}},
                })
            elif e.value_from and e.value_from.startswith("configmap:"):
                parts = e.value_from.split(":")
                env_list.append({
                    "name": e.name,
                    "valueFrom": {"configMapKeyRef": {"name": parts[1], "key": parts[2] if len(parts) > 2 else ""}},
                })
            else:
                env_list.append({"name": e.name, "value": e.value})
        container["env"] = env_list

    if w.volumes:
        container["volumeMounts"] = [
            {"name": vm.name, "mountPath": vm.mount_path, **({"readOnly": True} if vm.read_only else {})}
            for vm in w.volumes
        ]

    # Resources (ensure defaults unless skip_hardening)
    res = w.resources
    if skip_hardening and (res.cpu_request or res.memory_request or res.cpu_limit or res.memory_limit):
        # Preserve the chart's own resource settings
        resources: dict = {"requests": {}, "limits": {}}
        if res.cpu_request:
            resources["requests"]["cpu"] = res.cpu_request
        if res.memory_request:
            resources["requests"]["memory"] = res.memory_request
        if res.cpu_limit:
            resources["limits"]["cpu"] = res.cpu_limit
        if res.memory_limit:
            resources["limits"]["memory"] = res.memory_limit
        container["resources"] = resources
    elif not skip_hardening:
        container["resources"] = {
            "requests": {
                "cpu": res.cpu_request or "100m",
                "memory": res.memory_request or "128Mi",
            },
            "limits": {
                "cpu": res.cpu_limit or "500m",
                "memory": res.memory_limit or "256Mi",
            },
        }

    # Probes
    if w.liveness_probe:
        container["livenessProbe"] = _build_probe(w.liveness_probe)
    if w.readiness_probe:
        container["readinessProbe"] = _build_probe(w.readiness_probe)

    # Security context (skip if helm chart already provides its own)
    if not skip_hardening:
        container["securityContext"] = {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": not w.privileged,
            "runAsNonRoot": w.run_as_non_root,
        }

    pod_spec: dict = {
        "serviceAccountName": sa_name,
        "automountServiceAccountToken": False,
        "containers": [container],
    }

    # Volumes
    if w.volumes:
        vol_list = []
        for vm in w.volumes:
            vol: dict = {"name": vm.name}
            # Assume PVC if mount exists
            vol["persistentVolumeClaim"] = {"claimName": vm.name}
            vol_list.append(vol)
        pod_spec["volumes"] = vol_list

    doc: dict = {
        "apiVersion": "apps/v1",
        "kind": kind,
        "metadata": {
            "name": w.name,
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "replicas": w.replicas,
            "selector": {"matchLabels": {"app": w.name}},
            "template": {
                "metadata": {"labels": labels},
                "spec": pod_spec,
            },
        },
    }

    if is_stateful:
        doc["spec"]["serviceName"] = w.name

    return doc


def _build_probe(hc) -> dict:
    probe: dict = {}
    if hc.type == "http":
        probe["httpGet"] = {"path": hc.path, "port": hc.port}
    elif hc.type == "tcp":
        probe["tcpSocket"] = {"port": hc.port}
    elif hc.type == "exec":
        probe["exec"] = {"command": hc.command}

    probe["initialDelaySeconds"] = hc.initial_delay_seconds
    probe["periodSeconds"] = hc.interval_seconds
    probe["timeoutSeconds"] = hc.timeout_seconds
    probe["failureThreshold"] = hc.failure_threshold
    return probe


def _build_service(ns: str, name: str, selector: dict, ports, svc_type: str = "ClusterIP") -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "type": svc_type,
            "selector": selector,
            "ports": [
                {
                    "name": p.name or f"port-{p.port}",
                    "port": p.port,
                    "targetPort": p.target_port,
                    "protocol": p.protocol,
                }
                for p in ports
            ],
        },
    }


def _build_ingress(ns: str, ing) -> dict:
    annotations = {
        "kubernetes.io/ingress.class": "traefik",
        "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
        **ing.annotations,
    }

    spec: dict = {"rules": []}
    for rule in ing.rules:
        spec["rules"].append({
            "host": rule.host,
            "http": {
                "paths": [{
                    "path": rule.path,
                    "pathType": rule.path_type,
                    "backend": {
                        "service": {
                            "name": rule.service_name,
                            "port": {"number": rule.service_port},
                        },
                    },
                }],
            },
        })

    if ing.tls:
        hosts = list({r.host for r in ing.rules if r.host})
        spec["tls"] = [{"hosts": hosts, "secretName": ing.tls_secret or f"{ing.name}-tls"}]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": ing.name, "namespace": ns, "annotations": annotations},
        "spec": spec,
    }


def _build_network_policies(ns: str, manifest: NormalizedManifest, dep_graph: dict, exposed: list) -> list[dict]:
    policies: list[dict] = []

    # Default-deny
    policies.append({
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "default-deny", "namespace": ns},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
            "egress": [
                # Allow DNS
                {"ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]},
            ],
        },
    })

    # Per-workload allow rules
    for w in manifest.workloads:
        ingress_rules: list[dict] = []

        # Allow from workloads that depend on this one
        for src, deps in dep_graph.items():
            if w.name in deps:
                ingress_rules.append({
                    "from": [{"podSelector": {"matchLabels": {"app": src}}}],
                    "ports": [{"port": p.container_port, "protocol": p.protocol} for p in w.ports],
                })

        # Allow from Traefik for exposed services
        if w.name in exposed:
            ingress_rules.append({
                "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
                "ports": [{"port": p.container_port, "protocol": p.protocol} for p in w.ports],
            })

        if ingress_rules:
            egress_rules: list[dict] = [
                {"ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]},
            ]
            # Allow egress to dependencies
            for dep_name in dep_graph.get(w.name, []):
                dep_workload = next((ww for ww in manifest.workloads if ww.name == dep_name), None)
                if dep_workload and dep_workload.ports:
                    egress_rules.append({
                        "to": [{"podSelector": {"matchLabels": {"app": dep_name}}}],
                        "ports": [{"port": p.container_port, "protocol": p.protocol} for p in dep_workload.ports],
                    })

            policies.append({
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": f"allow-{w.name}", "namespace": ns},
                "spec": {
                    "podSelector": {"matchLabels": {"app": w.name}},
                    "policyTypes": ["Ingress", "Egress"],
                    "ingress": ingress_rules,
                    "egress": egress_rules,
                },
            })

    return policies


def _multi_doc(docs: list[dict]) -> str:
    """Render multiple K8s docs as a multi-document YAML string."""
    parts = []
    for doc in docs:
        parts.append(yaml.dump(doc, default_flow_style=False, sort_keys=False))
    return "---\n".join(parts)
