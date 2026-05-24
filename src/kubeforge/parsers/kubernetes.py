"""Raw Kubernetes YAML manifest parser.

Handles multi-document YAML files containing Deployments, StatefulSets,
Services, ConfigMaps, Secrets, PVCs, Ingresses, etc.
"""

from __future__ import annotations

import logging

import yaml  # type: ignore[import]

from kubeforge.models.manifest import (
    ConfigData,
    EnvVar,
    HealthCheck,
    ImageRef,
    Ingress,
    IngressRule,
    NormalizedManifest,
    Port,
    RawResource,
    ResourceRequirements,
    SecretData,
    Service,
    ServicePort,
    Volume,
    VolumeMount,
    Workload,
)
from kubeforge.parsers.base import ManifestParser, ParseOptions, ValidationError

logger = logging.getLogger("kubeforge.parsers.kubernetes")


class KubernetesParser(ManifestParser):
    @property
    def name(self) -> str:
        return "kubernetes"

    @property
    def description(self) -> str:
        return "Raw Kubernetes YAML manifest parser"

    # ── Detection ───────────────────────────────────────────────────

    def detect(self, content: str, filename: str = "") -> float:
        score = 0.0

        try:
            docs = list(yaml.safe_load_all(content))
        except yaml.YAMLError:
            return 0.0

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if "apiVersion" in doc and "kind" in doc:
                score += 0.5
            if "services" in doc and "apiVersion" not in doc:
                score -= 0.5  # Looks like Compose

        return max(0.0, min(1.0, score))

    # ── Validation ──────────────────────────────────────────────────

    def validate(self, content: str) -> list[ValidationError]:
        errors: list[ValidationError] = []
        try:
            docs = list(yaml.safe_load_all(content))
        except yaml.YAMLError as e:
            return [ValidationError(message=f"Invalid YAML: {e}")]

        for i, doc in enumerate(docs):
            if doc is None:
                continue
            if not isinstance(doc, dict):
                errors.append(ValidationError(message=f"Document {i}: must be a mapping"))
                continue
            if "apiVersion" not in doc:
                errors.append(ValidationError(message=f"Document {i}: missing 'apiVersion'"))
            if "kind" not in doc:
                errors.append(ValidationError(message=f"Document {i}: missing 'kind'"))

        return errors

    # ── Parsing ─────────────────────────────────────────────────────

    def parse(self, content: str, options: ParseOptions | None = None) -> NormalizedManifest:
        opts = options or ParseOptions()
        docs = list(yaml.safe_load_all(content))

        manifest = NormalizedManifest(
            source_type="kubernetes",
            source_file=opts.filename,
            namespace=opts.namespace,
        )

        for doc in docs:
            if not isinstance(doc, dict):
                continue

            kind = doc.get("kind", "")
            metadata = doc.get("metadata", {}) or {}
            name = metadata.get("name", "unnamed")

            # Detect namespace
            if ns := metadata.get("namespace"):
                manifest.namespace = ns

            if kind in ("Deployment", "StatefulSet", "DaemonSet"):
                workload = self._parse_workload(doc, kind)
                manifest.workloads.append(workload)

            elif kind == "Service":
                svc = self._parse_service(doc)
                manifest.services.append(svc)

            elif kind == "PersistentVolumeClaim":
                vol = self._parse_pvc(doc)
                manifest.volumes.append(vol)

            elif kind == "ConfigMap":
                cm = ConfigData(
                    name=name,
                    data=doc.get("data", {}),
                )
                manifest.config_maps.append(cm)

            elif kind == "Secret":
                # Prefer stringData (plain text) over data (base64-encoded)
                secret_data = doc.get("stringData", {})
                if not secret_data:
                    # data field is base64-encoded -- decode it for our IR
                    import base64
                    raw_data = doc.get("data", {})
                    for k, v in raw_data.items():
                        try:
                            secret_data[k] = base64.b64decode(v).decode("utf-8")
                        except Exception:
                            # If decode fails, keep as-is (already plain or binary)
                            secret_data[k] = v
                sec = SecretData(
                    name=name,
                    type=doc.get("type", "Opaque"),
                    data=secret_data,
                )
                manifest.secrets.append(sec)

            elif kind == "Ingress":
                ing = self._parse_ingress(doc)
                manifest.ingresses.append(ing)

            else:
                # Store unrecognized resources as-is
                manifest.raw_resources.append(RawResource(
                    api_version=doc.get("apiVersion", ""),
                    kind=kind,
                    metadata=metadata,
                    spec=doc.get("spec", {}),
                ))

        return manifest

    # ── Workload parsing ────────────────────────────────────────────

    def _parse_workload(self, doc: dict, kind: str) -> Workload:
        metadata = doc.get("metadata", {}) or {}
        name = metadata.get("name", "unnamed")
        spec = doc.get("spec", {}) or {}
        replicas = spec.get("replicas", 1)
        template = spec.get("template", {}) or {}
        pod_spec = template.get("spec", {}) or {}
        containers = pod_spec.get("containers", [])

        if not containers:
            return Workload(name=name, image=ImageRef(repository=name))

        # Use first container as primary
        c = containers[0]
        image_ref = _parse_image_ref(c.get("image", f"{name}:latest"))

        workload = Workload(
            name=name,
            image=image_ref,
            replicas=replicas,
            is_stateful=kind == "StatefulSet",
            labels=metadata.get("labels", {}),
            annotations=metadata.get("annotations", {}),
        )

        # Command / args
        if cmd := c.get("command"):
            workload.command = cmd if isinstance(cmd, list) else [cmd]
        if args := c.get("args"):
            workload.args = args if isinstance(args, list) else [args]

        # Ports
        for p in c.get("ports", []):
            workload.ports.append(Port(
                container_port=p.get("containerPort", 0),
                host_port=p.get("hostPort"),
                protocol=p.get("protocol", "TCP"),
            ))

        # Env
        for e in c.get("env", []):
            ev = EnvVar(name=e.get("name", ""))
            if "value" in e:
                ev.value = str(e["value"])
            elif "valueFrom" in e:
                vf = e["valueFrom"]
                if "secretKeyRef" in vf:
                    ev.value_from = (
                        f"secret:{vf['secretKeyRef'].get('name', '')}:"
                        f"{vf['secretKeyRef'].get('key', '')}"
                    )
                elif "configMapKeyRef" in vf:
                    ev.value_from = (
                        f"configmap:{vf['configMapKeyRef'].get('name', '')}:"
                        f"{vf['configMapKeyRef'].get('key', '')}"
                    )
            workload.env.append(ev)

        # Volume mounts
        for vm in c.get("volumeMounts", []):
            workload.volumes.append(VolumeMount(
                name=vm.get("name", ""),
                mount_path=vm.get("mountPath", ""),
                read_only=vm.get("readOnly", False),
                sub_path=vm.get("subPath", ""),
            ))

        # Resources
        res = c.get("resources", {})
        if res:
            workload.resources = ResourceRequirements(
                cpu_request=res.get("requests", {}).get("cpu", ""),
                memory_request=res.get("requests", {}).get("memory", ""),
                cpu_limit=res.get("limits", {}).get("cpu", ""),
                memory_limit=res.get("limits", {}).get("memory", ""),
            )

        # Probes
        if lp := c.get("livenessProbe"):
            workload.liveness_probe = _parse_probe(lp)
        if rp := c.get("readinessProbe"):
            workload.readiness_probe = _parse_probe(rp)

        # Security context
        sc = c.get("securityContext", {})
        if sc.get("privileged"):
            workload.privileged = True
        if sc.get("runAsNonRoot") is False or sc.get("runAsUser") == 0:
            workload.run_as_non_root = False

        return workload

    # ── Service parsing ─────────────────────────────────────────────

    def _parse_service(self, doc: dict) -> Service:
        metadata = doc.get("metadata", {}) or {}
        spec = doc.get("spec", {}) or {}

        svc = Service(
            name=metadata.get("name", "unnamed"),
            type=spec.get("type", "ClusterIP"),
            selector=spec.get("selector", {}),
        )

        for p in spec.get("ports", []):
            svc.ports.append(ServicePort(
                name=p.get("name", ""),
                port=p.get("port", 0),
                target_port=p.get("targetPort", p.get("port", 0)),
                protocol=p.get("protocol", "TCP"),
                node_port=p.get("nodePort"),
            ))

        return svc

    # ── PVC parsing ─────────────────────────────────────────────────

    def _parse_pvc(self, doc: dict) -> Volume:
        metadata = doc.get("metadata", {}) or {}
        spec = doc.get("spec", {}) or {}
        resources = spec.get("resources", {}).get("requests", {})

        return Volume(
            name=metadata.get("name", "unnamed"),
            type="pvc",
            size=resources.get("storage", "1Gi"),
            storage_class=spec.get("storageClassName", ""),
        )

    # ── Ingress parsing ─────────────────────────────────────────────

    def _parse_ingress(self, doc: dict) -> Ingress:
        metadata = doc.get("metadata", {}) or {}
        spec = doc.get("spec", {}) or {}

        ing = Ingress(
            name=metadata.get("name", "unnamed"),
            annotations=metadata.get("annotations", {}),
        )

        # TLS
        tls_list = spec.get("tls", [])
        if tls_list:
            ing.tls = True
            if tls_list[0].get("secretName"):
                ing.tls_secret = tls_list[0]["secretName"]

        # Rules
        for rule in spec.get("rules", []):
            host = rule.get("host", "")
            for path_entry in rule.get("http", {}).get("paths", []):
                backend = path_entry.get("backend", {})
                svc_name = ""
                svc_port = 0

                # networking.k8s.io/v1 format
                if "service" in backend:
                    svc_name = backend["service"].get("name", "")
                    svc_port = backend["service"].get("port", {}).get("number", 0)
                else:
                    svc_name = backend.get("serviceName", "")
                    svc_port = backend.get("servicePort", 0)

                ing.rules.append(IngressRule(
                    host=host,
                    path=path_entry.get("path", "/"),
                    path_type=path_entry.get("pathType", "Prefix"),
                    service_name=svc_name,
                    service_port=svc_port,
                ))

        return ing


# ── Helpers ─────────────────────────────────────────────────────────


def _parse_image_ref(image: str) -> ImageRef:
    """Parse image string into ImageRef."""
    ref = ImageRef()

    if "@" in image:
        image, ref.digest = image.rsplit("@", 1)

    if ":" in image and not image.endswith(":"):
        parts = image.rsplit(":", 1)
        # Guard against registry port being mistaken for tag
        if not parts[1].isdigit() or "/" in parts[0].split(":")[-1]:
            image = parts[0]
            ref.tag = parts[1]
        else:
            ref.tag = "latest"
    else:
        ref.tag = "latest"

    segments = image.split("/")
    if len(segments) >= 2 and ("." in segments[0] or ":" in segments[0] or segments[0] == "localhost"):
        ref.registry = segments[0]
        ref.repository = "/".join(segments[1:])
    else:
        ref.repository = image

    return ref


def _parse_probe(probe: dict) -> HealthCheck:
    """Parse a K8s probe spec."""
    hc = HealthCheck()

    if "httpGet" in probe:
        hc.type = "http"
        hc.path = probe["httpGet"].get("path", "/")
        hc.port = probe["httpGet"].get("port", 0)
    elif "tcpSocket" in probe:
        hc.type = "tcp"
        hc.port = probe["tcpSocket"].get("port", 0)
    elif "exec" in probe:
        hc.type = "exec"
        hc.command = probe["exec"].get("command", [])

    hc.initial_delay_seconds = probe.get("initialDelaySeconds", 0)
    hc.interval_seconds = probe.get("periodSeconds", 10)
    hc.timeout_seconds = probe.get("timeoutSeconds", 5)
    hc.failure_threshold = probe.get("failureThreshold", 3)

    return hc
