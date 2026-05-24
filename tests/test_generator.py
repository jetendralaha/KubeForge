"""Tests for the K8s manifest generator."""

from kubeforge.generator import generate
from kubeforge.models.manifest import (
    ImageRef,
    NormalizedManifest,
    Port,
    Service,
    ServicePort,
    Workload,
    Volume,
)


def _simple_manifest() -> NormalizedManifest:
    return NormalizedManifest(
        source_type="docker-compose",
        namespace="test-ns",
        workloads=[
            Workload(
                name="web",
                image=ImageRef(repository="nginx", tag="1.25"),
                replicas=2,
                ports=[Port(container_port=80)],
                labels={"app": "web"},
            ),
        ],
        services=[
            Service(
                name="web",
                selector={"app": "web"},
                ports=[ServicePort(name="http", port=80, target_port=80)],
            ),
        ],
        volumes=[
            Volume(name="data", type="pvc", size="5Gi"),
        ],
    )


def test_generate_produces_files():
    manifest = _simple_manifest()
    files = generate(manifest)
    assert "00-namespace.yaml" in files
    assert "05-workloads.yaml" in files
    assert "06-services.yaml" in files


def test_namespace_has_psa_labels():
    manifest = _simple_manifest()
    files = generate(manifest)
    ns_content = files["00-namespace.yaml"]
    assert "pod-security.kubernetes.io/enforce" in ns_content
    assert "baseline" in ns_content


def test_workload_has_security_context():
    manifest = _simple_manifest()
    files = generate(manifest)
    workload = files["05-workloads.yaml"]
    assert "allowPrivilegeEscalation: false" in workload
    assert "runAsNonRoot: true" in workload


def test_pvc_uses_longhorn():
    manifest = _simple_manifest()
    files = generate(manifest)
    pvcs = files.get("04-pvcs.yaml", "")
    assert "longhorn" in pvcs


def test_network_policies_generated():
    manifest = _simple_manifest()
    files = generate(manifest)
    assert "08-networkpolicies.yaml" in files
    np = files["08-networkpolicies.yaml"]
    assert "default-deny" in np
