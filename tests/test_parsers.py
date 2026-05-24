"""Tests for Docker Compose and Kubernetes parsers."""

from pathlib import Path

from kubeforge.parsers import get_registry
from kubeforge.parsers.compose import ComposeParser
from kubeforge.parsers.kubernetes import KubernetesParser

TESTDATA = Path(__file__).parent / "testdata"


def test_compose_detect():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    parser = ComposeParser()
    score = parser.detect(content, "docker-compose.yml")
    assert score >= 0.8


def test_compose_parse():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    parser = ComposeParser()
    manifest = parser.parse(content)
    assert manifest.source_type == "docker-compose"
    assert len(manifest.workloads) == 3
    names = {w.name for w in manifest.workloads}
    assert "wordpress" in names
    assert "mysql" in names
    assert "redis" in names


def test_compose_parse_images():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    parser = ComposeParser()
    manifest = parser.parse(content)
    wp = next(w for w in manifest.workloads if w.name == "wordpress")
    assert wp.image.repository == "wordpress"
    assert wp.image.tag == "6.4-php8.2-apache"


def test_compose_volumes():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    parser = ComposeParser()
    manifest = parser.parse(content)
    assert len(manifest.volumes) == 3  # wp-content, mysql-data, redis-data


def test_compose_stateful_detection():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    parser = ComposeParser()
    manifest = parser.parse(content)
    mysql = next(w for w in manifest.workloads if w.name == "mysql")
    assert mysql.is_stateful is True


def test_kube_detect():
    content = (TESTDATA / "kube" / "insecure.yaml").read_text()
    parser = KubernetesParser()
    score = parser.detect(content)
    assert score >= 0.4


def test_kube_parse():
    content = (TESTDATA / "kube" / "insecure.yaml").read_text()
    parser = KubernetesParser()
    manifest = parser.parse(content)
    assert manifest.source_type == "kubernetes"
    assert len(manifest.workloads) == 1
    assert manifest.workloads[0].name == "insecure-app"


def test_kube_parse_privileged():
    content = (TESTDATA / "kube" / "insecure.yaml").read_text()
    parser = KubernetesParser()
    manifest = parser.parse(content)
    assert manifest.workloads[0].privileged is True


def test_registry_auto_detect():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    registry = get_registry()
    parser, score = registry.detect(content, "docker-compose.yml")
    assert parser is not None
    assert parser.name == "docker-compose"
    assert score >= 0.8


def test_registry_parse_auto():
    content = (TESTDATA / "compose" / "wordpress.yaml").read_text()
    registry = get_registry()
    manifest = registry.parse_auto(content, "docker-compose.yml")
    assert manifest.source_type == "docker-compose"
    assert len(manifest.workloads) == 3
