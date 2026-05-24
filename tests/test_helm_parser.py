"""Tests for the Helm chart parser."""

from __future__ import annotations

import base64
import io
import tarfile
import textwrap
from unittest.mock import patch

import pytest

from kubeforge.parsers.base import ParseOptions
from kubeforge.parsers.helm import HelmParseError, HelmParser

# ── Helpers ─────────────────────────────────────────────────────────


def _make_chart_tgz(chart_name: str = "myapp", values: dict | None = None) -> bytes:
    """Create an in-memory .tgz containing a minimal Helm chart."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Chart.yaml
        chart_yaml = textwrap.dedent(f"""\
            apiVersion: v2
            name: {chart_name}
            version: 1.0.0
            type: application
            description: A test Helm chart
        """)
        _add_to_tar(tar, f"{chart_name}/Chart.yaml", chart_yaml)

        # values.yaml
        import yaml
        vals = values or {"replicaCount": 1, "image": {"repository": "nginx", "tag": "1.25"}}
        _add_to_tar(tar, f"{chart_name}/values.yaml", yaml.dump(vals))

        # templates/deployment.yaml
        deployment_tpl = textwrap.dedent("""\
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {{ .Release.Name }}-{{ .Chart.Name }}
              namespace: {{ .Release.Namespace }}
            spec:
              replicas: {{ .Values.replicaCount }}
              selector:
                matchLabels:
                  app: {{ .Chart.Name }}
              template:
                metadata:
                  labels:
                    app: {{ .Chart.Name }}
                spec:
                  containers:
                    - name: {{ .Chart.Name }}
                      image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
                      ports:
                        - containerPort: 80
        """)
        _add_to_tar(tar, f"{chart_name}/templates/deployment.yaml", deployment_tpl)

        # templates/service.yaml
        service_tpl = textwrap.dedent("""\
            apiVersion: v1
            kind: Service
            metadata:
              name: {{ .Release.Name }}-{{ .Chart.Name }}
              namespace: {{ .Release.Namespace }}
            spec:
              type: ClusterIP
              selector:
                app: {{ .Chart.Name }}
              ports:
                - port: 80
                  targetPort: 80
        """)
        _add_to_tar(tar, f"{chart_name}/templates/service.yaml", service_tpl)

    return buf.getvalue()


def _add_to_tar(tar: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ── Tests ───────────────────────────────────────────────────────────


class TestHelmDetection:
    """Test the detect() method."""

    def setup_method(self):
        self.parser = HelmParser()

    def test_detect_by_filename_tgz(self):
        score = self.parser.detect("irrelevant", "myapp-1.0.0.tgz")
        assert score >= 0.80

    def test_detect_by_filename_tar_gz(self):
        score = self.parser.detect("irrelevant", "myapp-1.0.0.tar.gz")
        assert score >= 0.80

    def test_detect_base64_tgz_with_chart_yaml(self):
        raw = _make_chart_tgz()
        content = _encode(raw)
        score = self.parser.detect(content, "myapp.tgz")
        assert score >= 0.95

    def test_detect_template_markers(self):
        content = '{{ .Values.image.repository }}:{{ .Values.image.tag }}'
        score = self.parser.detect(content, "deployment.yaml")
        assert score >= 0.70

    def test_detect_no_match(self):
        score = self.parser.detect("just some random text", "notes.txt")
        assert score < 0.5

    def test_detect_release_marker(self):
        content = "namespace: {{ .Release.Namespace }}"
        score = self.parser.detect(content, "file.yaml")
        assert score >= 0.70


class TestHelmValidation:
    """Test the validate() method."""

    def setup_method(self):
        self.parser = HelmParser()

    def test_validate_invalid_base64(self):
        errors = self.parser.validate("not-base64!@#$")
        assert any("base64" in e.message.lower() for e in errors)

    def test_validate_valid_chart(self):
        raw = _make_chart_tgz()
        content = _encode(raw)
        errors = self.parser.validate(content)
        # May have a warning about helm not being installed, but no errors about content
        content_errors = [e for e in errors if e.severity == "error"]
        assert len(content_errors) == 0

    def test_validate_tgz_without_chart_yaml(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            _add_to_tar(tar, "readme.txt", "hello")
        content = _encode(buf.getvalue())
        errors = self.parser.validate(content)
        assert any("Chart.yaml" in e.message for e in errors)


class TestHelmParse:
    """Test the parse() method with mocked helm template."""

    def setup_method(self):
        self.parser = HelmParser()

    @patch("kubeforge.parsers.helm.HelmParser._helm_template")
    def test_parse_delegates_to_k8s_parser(self, mock_template):
        """Verify that parse() calls helm template and delegates to KubernetesParser."""
        rendered_yaml = textwrap.dedent("""\
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: myapp
              namespace: production
            spec:
              replicas: 2
              selector:
                matchLabels:
                  app: myapp
              template:
                metadata:
                  labels:
                    app: myapp
                spec:
                  containers:
                    - name: myapp
                      image: nginx:1.25
                      ports:
                        - containerPort: 80
            ---
            apiVersion: v1
            kind: Service
            metadata:
              name: myapp
              namespace: production
            spec:
              type: ClusterIP
              selector:
                app: myapp
              ports:
                - port: 80
                  targetPort: 80
        """)
        mock_template.return_value = rendered_yaml

        raw = _make_chart_tgz()
        content = _encode(raw)
        opts = ParseOptions(filename="myapp.tgz", namespace="production")

        manifest = self.parser.parse(content, opts)

        assert manifest.source_type == "helm"
        assert manifest.namespace == "production"
        assert len(manifest.workloads) >= 1
        assert manifest.workloads[0].name == "myapp"
        assert manifest.workloads[0].image.repository == "nginx"

    @patch("kubeforge.parsers.helm.HelmParser._helm_template")
    def test_parse_with_values(self, mock_template):
        """Verify values content is passed through to helm template."""
        mock_template.return_value = textwrap.dedent("""\
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: myapp
              namespace: staging
            spec:
              replicas: 3
              selector:
                matchLabels:
                  app: myapp
              template:
                metadata:
                  labels:
                    app: myapp
                spec:
                  containers:
                    - name: myapp
                      image: nginx:1.25
        """)

        raw = _make_chart_tgz()
        content = _encode(raw)
        opts = ParseOptions(filename="myapp.tgz", namespace="staging")
        opts.values_content = "replicaCount: 3"  # type: ignore[attr-defined]

        manifest = self.parser.parse(content, opts)

        assert manifest.namespace == "staging"
        # Verify values_content was passed to _helm_template
        call_kwargs = mock_template.call_args
        assert call_kwargs is not None

    def test_parse_invalid_base64(self):
        with pytest.raises(HelmParseError, match="base64"):
            self.parser.parse("not-valid-base64!!")

    def test_parse_invalid_tgz(self):
        content = base64.b64encode(b"not a tarball").decode()
        with pytest.raises(HelmParseError, match="tgz"):
            self.parser.parse(content)


class TestHelmHelpers:
    """Test static helper methods."""

    def test_is_valid_tgz(self):
        raw = _make_chart_tgz()
        assert HelmParser._is_valid_tgz(raw) is True

    def test_is_valid_tgz_invalid(self):
        assert HelmParser._is_valid_tgz(b"not a tgz") is False

    def test_tgz_has_chart_yaml(self):
        raw = _make_chart_tgz()
        assert HelmParser._tgz_has_chart_yaml(raw) is True

    def test_extract_chart_name(self):
        raw = _make_chart_tgz(chart_name="my-awesome-app")
        name = HelmParser._extract_chart_name(raw)
        assert name == "my-awesome-app"

    def test_extract_chart_name_fallback(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            _add_to_tar(tar, "readme.txt", "hello")
        name = HelmParser._extract_chart_name(buf.getvalue())
        assert name == "release"
