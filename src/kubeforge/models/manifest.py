"""Normalized manifest intermediate representation.

Every parser (Compose, K8s, Helm) produces this common format.
The generator then converts it to production-ready K3s manifests.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Flexible(BaseModel):
    """Base model with coercion enabled - accepts int where str is expected and vice versa."""

    model_config = ConfigDict(coerce_numbers_to_str=True, arbitrary_types_allowed=True)


class ImageRef(_Flexible):
    registry: str = ""
    repository: str = ""
    tag: str = "latest"
    digest: str = ""

    @property
    def full(self) -> str:
        parts: list[str] = []
        if self.registry:
            parts.append(f"{self.registry}/")
        parts.append(self.repository)
        if self.digest:
            parts.append(f"@{self.digest}")
        elif self.tag:
            parts.append(f":{self.tag}")
        return "".join(parts)


class Port(_Flexible):
    container_port: int
    host_port: int | None = None
    protocol: str = "TCP"


class EnvVar(_Flexible):
    name: str
    value: str = ""
    value_from: str = ""  # secret or configmap ref


class VolumeMount(_Flexible):
    name: str
    mount_path: str
    read_only: bool = False
    sub_path: str = ""


class Volume(_Flexible):
    name: str
    type: str = "emptyDir"          # emptyDir, pvc, configMap, secret, hostPath
    source: str = ""                # PVC name / configMap name / host path
    size: str = ""                  # e.g. "10Gi"
    storage_class: str = ""


class ServicePort(_Flexible):
    name: str = ""
    port: int
    target_port: int | str  # K8s allows named ports (e.g. "metrics")
    protocol: str = "TCP"
    node_port: int | None = None


class Service(_Flexible):
    name: str
    type: str = "ClusterIP"         # ClusterIP, NodePort, LoadBalancer
    selector: dict[str, str] = Field(default_factory=dict)
    ports: list[ServicePort] = Field(default_factory=list)


class ResourceRequirements(_Flexible):
    cpu_request: str = ""
    cpu_limit: str = ""
    memory_request: str = ""
    memory_limit: str = ""


class HealthCheck(_Flexible):
    type: str = "http"              # http, tcp, exec
    path: str = "/"
    port: int | str = 0            # K8s allows named ports (e.g. "http", "controller")
    command: list[str] = Field(default_factory=list)
    interval_seconds: int = 30
    timeout_seconds: int = 10
    initial_delay_seconds: int = 5
    failure_threshold: int = 3


class IngressRule(_Flexible):
    host: str
    path: str = "/"
    path_type: str = "Prefix"
    service_name: str = ""
    service_port: int = 0


class Ingress(_Flexible):
    name: str
    annotations: dict[str, str] = Field(default_factory=dict)
    tls: bool = False
    tls_secret: str = ""
    rules: list[IngressRule] = Field(default_factory=list)


class ConfigData(_Flexible):
    name: str
    data: dict[str, str] = Field(default_factory=dict)


class SecretData(_Flexible):
    name: str
    type: str = "Opaque"
    data: dict[str, str] = Field(default_factory=dict)


class Dependency(_Flexible):
    name: str
    condition: str = "started"      # started, healthy, completed


class Workload(_Flexible):
    """A single deployable unit (container + config)."""

    name: str
    image: ImageRef
    replicas: int = 1
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    ports: list[Port] = Field(default_factory=list)
    env: list[EnvVar] = Field(default_factory=list)
    volumes: list[VolumeMount] = Field(default_factory=list)
    resources: ResourceRequirements = Field(default_factory=ResourceRequirements)
    liveness_probe: HealthCheck | None = None
    readiness_probe: HealthCheck | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    dependencies: list[Dependency] = Field(default_factory=list)
    is_stateful: bool = False
    privileged: bool = False
    run_as_non_root: bool = True
    service_account: str = ""
    restart_policy: str = "Always"


class Network(_Flexible):
    name: str
    driver: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class RawResource(_Flexible):
    """Passthrough for K8s resources the parser doesn't deeply understand."""

    api_version: str
    kind: str
    metadata: dict = Field(default_factory=dict)
    spec: dict = Field(default_factory=dict)


class NormalizedManifest(_Flexible):
    """The universal IR that every parser produces."""

    source_type: str = ""           # docker-compose, kubernetes, helm
    source_file: str = ""
    namespace: str = "default"

    workloads: list[Workload] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    volumes: list[Volume] = Field(default_factory=list)
    config_maps: list[ConfigData] = Field(default_factory=list)
    secrets: list[SecretData] = Field(default_factory=list)
    ingresses: list[Ingress] = Field(default_factory=list)
    networks: list[Network] = Field(default_factory=list)
    raw_resources: list[RawResource] = Field(default_factory=list)

    def all_images(self) -> list[ImageRef]:
        return [w.image for w in self.workloads]
