"""Docker Compose file parser.

Converts docker-compose.yml into NormalizedManifest IR.
Supports v2/v3 Compose format with services, volumes, networks.
"""

from __future__ import annotations

import logging
import re

import yaml  # type: ignore[import]

from kubeforge.models.manifest import (
    Dependency,
    EnvVar,
    HealthCheck,
    ImageRef,
    Network,
    NormalizedManifest,
    Port,
    ResourceRequirements,
    Service,
    ServicePort,
    Volume,
    VolumeMount,
    Workload,
)
from kubeforge.parsers.base import ManifestParser, ParseOptions, ValidationError

logger = logging.getLogger("kubeforge.parsers.compose")


class ComposeParser(ManifestParser):
    @property
    def name(self) -> str:
        return "docker-compose"

    @property
    def description(self) -> str:
        return "Docker Compose v2/v3 file parser"

    # ── Detection ───────────────────────────────────────────────────

    def detect(self, content: str, filename: str = "") -> float:
        score = 0.0
        lower_name = filename.lower()

        # Filename hints
        if "compose" in lower_name or "docker-compose" in lower_name:
            score += 0.4

        # Content hints
        try:
            doc = yaml.safe_load(content)
            if isinstance(doc, dict):
                if "services" in doc:
                    score += 0.5
                if "version" in doc:
                    score += 0.1
                # Negative: K8s fields
                if "apiVersion" in doc or "kind" in doc:
                    score -= 0.4
        except yaml.YAMLError:
            pass

        return max(0.0, min(1.0, score))

    # ── Validation ──────────────────────────────────────────────────

    def validate(self, content: str) -> list[ValidationError]:
        errors: list[ValidationError] = []
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as e:
            return [ValidationError(message=f"Invalid YAML: {e}")]

        if not isinstance(doc, dict):
            return [ValidationError(message="Compose file must be a YAML mapping")]

        if "services" not in doc:
            errors.append(ValidationError(message="Missing required 'services' key"))

        services = doc.get("services", {})
        if isinstance(services, dict):
            for name, svc in services.items():
                if not isinstance(svc, dict):
                    errors.append(ValidationError(message=f"Service '{name}' must be a mapping"))
                    continue
                if "image" not in svc and "build" not in svc:
                    errors.append(ValidationError(
                        message=f"Service '{name}' must have 'image' or 'build'",
                        severity="warning",
                    ))

        return errors

    # ── Parsing ─────────────────────────────────────────────────────

    def parse(self, content: str, options: ParseOptions | None = None) -> NormalizedManifest:
        opts = options or ParseOptions()
        doc = yaml.safe_load(content)

        if not isinstance(doc, dict) or "services" not in doc:
            raise ValueError("Invalid Docker Compose file: missing 'services'")

        manifest = NormalizedManifest(
            source_type="docker-compose",
            source_file=opts.filename,
            namespace=opts.namespace,
        )

        services_map: dict = doc.get("services", {})

        for svc_name, svc_def in services_map.items():
            if not isinstance(svc_def, dict):
                continue

            workload = self._parse_service(svc_name, svc_def)
            manifest.workloads.append(workload)

            # Create a ClusterIP Service for each workload with ports
            if workload.ports:
                k8s_svc = Service(
                    name=_sanitize(svc_name),
                    selector={"app": _sanitize(svc_name)},
                    ports=[
                        ServicePort(
                            name=f"port-{p.container_port}",
                            port=p.container_port,
                            target_port=p.container_port,
                            protocol=p.protocol,
                        )
                        for p in workload.ports
                    ],
                )
                manifest.services.append(k8s_svc)

        # Top-level volumes → PVCs
        for vol_name in doc.get("volumes", {}):
            manifest.volumes.append(Volume(name=_sanitize(vol_name), type="pvc", size="1Gi"))

        # Top-level networks
        for net_name, net_def in (doc.get("networks", {}) or {}).items():
            driver = ""
            if isinstance(net_def, dict):
                driver = net_def.get("driver", "")
            manifest.networks.append(Network(name=_sanitize(net_name), driver=driver))

        return manifest

    # ── Service → Workload ──────────────────────────────────────────

    def _parse_service(self, name: str, svc: dict) -> Workload:
        image_ref = _parse_image_ref(svc.get("image", f"{name}:latest"))

        workload = Workload(
            name=_sanitize(name),
            image=image_ref,
            labels={"app": _sanitize(name)},
        )

        # Ports
        for p in svc.get("ports", []):
            workload.ports.append(_parse_port(p))

        # Environment
        env = svc.get("environment", {})
        workload.env = _parse_environment(env)

        # Volumes
        for v in svc.get("volumes", []):
            workload.volumes.append(_parse_volume_mount(v))

        # Command / entrypoint
        if "command" in svc:
            cmd = svc["command"]
            workload.command = cmd if isinstance(cmd, list) else cmd.split()

        if "entrypoint" in svc:
            ep = svc["entrypoint"]
            workload.args = ep if isinstance(ep, list) else ep.split()

        # Resources
        deploy = svc.get("deploy", {})
        if deploy:
            workload.resources = _parse_resources(deploy)
            workload.replicas = deploy.get("replicas", 1)

        # Health check
        hc = svc.get("healthcheck", {})
        if hc:
            workload.liveness_probe = _parse_healthcheck(hc)

        # Dependencies
        depends = svc.get("depends_on", [])
        workload.dependencies = _parse_depends_on(depends)

        # Privileged
        if svc.get("privileged", False):
            workload.privileged = True
            workload.run_as_non_root = False

        # Network mode
        if svc.get("network_mode") == "host":
            workload.privileged = True

        # Detect stateful services (databases)
        db_patterns = ["postgres", "mysql", "mariadb", "mongo", "redis", "elasticsearch", "minio"]
        if any(p in image_ref.repository.lower() for p in db_patterns):
            workload.is_stateful = True

        return workload


# ── Helpers ─────────────────────────────────────────────────────────


def _parse_image_ref(image: str) -> ImageRef:
    """Parse 'registry/repo:tag@digest' into ImageRef."""
    ref = ImageRef()
    digest = ""

    if "@" in image:
        image, digest = image.rsplit("@", 1)
        ref.digest = digest

    if ":" in image and not image.endswith(":"):
        parts = image.rsplit(":", 1)
        image = parts[0]
        ref.tag = parts[1]
    else:
        ref.tag = "latest"

    # Determine registry vs repository
    segments = image.split("/")
    if len(segments) >= 2 and ("." in segments[0] or ":" in segments[0] or segments[0] == "localhost"):
        ref.registry = segments[0]
        ref.repository = "/".join(segments[1:])
    else:
        ref.repository = image

    return ref


def _parse_port(port_def) -> Port:
    """Parse '8080:80' or {'target': 80, 'published': 8080}."""
    if isinstance(port_def, dict):
        return Port(
            container_port=int(port_def.get("target", 0)),
            host_port=int(port_def.get("published", 0)) or None,
            protocol=port_def.get("protocol", "TCP").upper(),
        )

    s = str(port_def)
    # Remove IP binding prefix (e.g., "127.0.0.1:")
    if s.count(":") > 1:
        s = s.split(":", 1)[1]

    proto = "TCP"
    if "/" in s:
        s, proto = s.rsplit("/", 1)
        proto = proto.upper()

    if ":" in s:
        host, container = s.split(":")
        return Port(container_port=int(container), host_port=int(host), protocol=proto)

    return Port(container_port=int(s), protocol=proto)


def _parse_environment(env) -> list[EnvVar]:
    """Parse environment as list or dict."""
    result: list[EnvVar] = []
    if isinstance(env, dict):
        for k, v in env.items():
            result.append(EnvVar(name=k, value=str(v) if v is not None else ""))
    elif isinstance(env, list):
        for item in env:
            if "=" in str(item):
                k, v = str(item).split("=", 1)
                result.append(EnvVar(name=k, value=v))
            else:
                result.append(EnvVar(name=str(item)))
    return result


def _parse_volume_mount(vol) -> VolumeMount:
    """Parse 'name:/path:ro' volume mount string."""
    if isinstance(vol, dict):
        return VolumeMount(
            name=_sanitize(vol.get("source", "vol")),
            mount_path=vol.get("target", "/data"),
            read_only=vol.get("read_only", False),
        )

    parts = str(vol).split(":")
    name = _sanitize(parts[0].split("/")[-1] or "vol")
    mount_path = parts[1] if len(parts) > 1 else parts[0]
    read_only = len(parts) > 2 and parts[2] == "ro"

    return VolumeMount(name=name, mount_path=mount_path, read_only=read_only)


def _parse_resources(deploy: dict) -> ResourceRequirements:
    """Parse deploy.resources limits/reservations."""
    res = ResourceRequirements()
    resources = deploy.get("resources", {})

    limits = resources.get("limits", {})
    if limits:
        res.cpu_limit = str(limits.get("cpus", ""))
        mem = limits.get("memory", "")
        res.memory_limit = str(mem)

    reservations = resources.get("reservations", {})
    if reservations:
        res.cpu_request = str(reservations.get("cpus", ""))
        mem = reservations.get("memory", "")
        res.memory_request = str(mem)

    return res


def _parse_healthcheck(hc: dict) -> HealthCheck:
    """Parse Compose healthcheck into HealthCheck."""
    check = HealthCheck(type="exec")
    test = hc.get("test", [])

    if isinstance(test, list):
        # ["CMD", "curl", "-f", "http://localhost"]
        if test and test[0] in ("CMD", "CMD-SHELL"):
            check.command = test[1:]
        else:
            check.command = test
    elif isinstance(test, str):
        check.command = ["sh", "-c", test]

    # Parse durations (e.g., "30s" → 30)
    check.interval_seconds = _parse_duration(hc.get("interval", "30s"))
    check.timeout_seconds = _parse_duration(hc.get("timeout", "10s"))
    check.initial_delay_seconds = _parse_duration(hc.get("start_period", "0s"))
    check.failure_threshold = hc.get("retries", 3)

    return check


def _parse_depends_on(depends) -> list[Dependency]:
    """Parse depends_on as list or dict."""
    deps: list[Dependency] = []
    if isinstance(depends, list):
        for d in depends:
            deps.append(Dependency(name=_sanitize(str(d))))
    elif isinstance(depends, dict):
        for name, cond in depends.items():
            condition = "started"
            if isinstance(cond, dict):
                condition = cond.get("condition", "started").replace("service_", "")
            deps.append(Dependency(name=_sanitize(name), condition=condition))
    return deps


def _parse_duration(val: str | int) -> int:
    """Parse '30s', '1m', '2m30s' into seconds."""
    if isinstance(val, int):
        return val
    val = str(val).strip()
    if not val or val == "0s":
        return 0

    match = re.match(r"(?:(\d+)m)?(?:(\d+)s)?", val)
    if match:
        minutes = int(match.group(1) or 0)
        seconds = int(match.group(2) or 0)
        return minutes * 60 + seconds

    try:
        return int(val.rstrip("s"))
    except ValueError:
        return 30


def _sanitize(name: str) -> str:
    """Sanitize a name for use as a K8s resource name."""
    name = re.sub(r"[^a-zA-Z0-9-]", "-", name.lower())
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:63] or "unnamed"
