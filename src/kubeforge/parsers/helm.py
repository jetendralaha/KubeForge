"""Helm chart (.tgz) parser — renders Helm templates via ``helm template``
and converts the rendered YAML output into a NormalizedManifest.

The parser expects a **packaged** chart archive (``helm package`` output, i.e. a
``.tgz`` file).  An optional *values* file can be supplied through
``ParseOptions`` so that user-provided overrides are applied during rendering.

Requirements:
- ``helm`` CLI must be available on ``$PATH`` (or configured via
  ``KUBEFORGE_HELM_BIN``).
"""

from __future__ import annotations

import base64
import io
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

import yaml  # type: ignore[import]

from kubeforge.config import settings
from kubeforge.models.manifest import NormalizedManifest
from kubeforge.parsers.base import ManifestParser, ParseOptions, ValidationError

logger = logging.getLogger("kubeforge.parsers.helm")


class HelmParseError(Exception):
    """Raised when Helm template rendering fails."""


class HelmParser(ManifestParser):
    """Parse a packaged Helm chart (.tgz) via ``helm template``."""

    @property
    def name(self) -> str:
        return "helm"

    @property
    def description(self) -> str:
        return "Helm chart (.tgz) parser — renders via helm template"

    # ── Detection ───────────────────────────────────────────────────

    def detect(self, content: str, filename: str = "") -> float:
        """Detect whether *content* represents a Helm chart archive.

        Because ``.tgz`` files are binary, the ``content`` may have been
        base64-encoded prior to storage (the upload API stores binary
        artifacts this way).  We check:

        1. Filename ends with ``.tgz`` or ``.tar.gz`` → 0.85
        2. Content decodes from base64 to a valid gzip/tar → 0.95
        3. Tar contains ``Chart.yaml`` → 0.98
        4. Content contains Helm-template markers (``{{ .Values``) → 0.70
        """
        score = 0.0

        # Filename hint
        fn_lower = filename.lower()
        if fn_lower.endswith((".tgz", ".tar.gz")):
            score = max(score, 0.85)

        # Try base64 decode → tarball inspection
        try:
            raw = base64.b64decode(content)
            if self._is_valid_tgz(raw):
                score = max(score, 0.90)
                if self._tgz_has_chart_yaml(raw):
                    score = max(score, 0.98)
        except Exception:
            pass

        # Fallback: textual Helm template markers
        if "{{ .Values" in content or "{{ .Release" in content or "{{- include" in content:
            score = max(score, 0.70)

        # Check for Chart.yaml in textual content (unlikely for .tgz but possible)
        if "apiVersion:" in content and "name:" in content and "version:" in content:
            if "type: application" in content or "type: library" in content:
                score = max(score, 0.60)

        return score

    # ── Parse ───────────────────────────────────────────────────────

    def parse(self, content: str, options: ParseOptions | None = None) -> NormalizedManifest:
        """Render the Helm chart via ``helm template`` and parse the output.

        ``content`` should be the base64-encoded ``.tgz`` archive.
        ``options.values_content`` (if present on a subclass/extension) carries
        the raw YAML of a custom values file.

        The rendered multi-document YAML is then delegated to the
        KubernetesParser for full structural parsing.
        """
        opts = options or ParseOptions()
        namespace = opts.namespace or "default"

        # Decode the archive
        try:
            raw = base64.b64decode(content)
        except Exception:
            raise HelmParseError("Content is not valid base64-encoded data")

        if not self._is_valid_tgz(raw):
            raise HelmParseError("Decoded content is not a valid .tgz archive")

        # Extract chart name from Chart.yaml for release name
        chart_name = self._extract_chart_name(raw) or "release"

        # Render via helm template
        rendered_yaml = self._helm_template(
            raw_tgz=raw,
            release_name=chart_name,
            namespace=namespace,
            values_content=getattr(opts, "values_content", ""),
        )

        # Delegate to KubernetesParser
        from kubeforge.parsers.kubernetes import KubernetesParser

        k8s_parser = KubernetesParser()
        k8s_opts = ParseOptions(filename=opts.filename, namespace=namespace)
        manifest = k8s_parser.parse(rendered_yaml, k8s_opts)

        # Override source metadata
        manifest.source_type = "helm"
        manifest.source_file = opts.filename
        manifest.namespace = namespace

        return manifest

    # ── Validate ────────────────────────────────────────────────────

    def validate(self, content: str) -> list[ValidationError]:
        """Validate the Helm chart archive without fully rendering."""
        errors: list[ValidationError] = []

        # Try base64 decode
        try:
            raw = base64.b64decode(content)
        except Exception:
            errors.append(ValidationError(message="Content is not valid base64"))
            return errors

        if not self._is_valid_tgz(raw):
            errors.append(ValidationError(message="Content is not a valid .tgz archive"))
            return errors

        if not self._tgz_has_chart_yaml(raw):
            errors.append(ValidationError(message="Chart.yaml not found in archive", severity="error"))

        # Check that helm CLI is available
        helm_bin = settings.helm.bin
        if not shutil.which(helm_bin):
            errors.append(ValidationError(
                message=f"Helm CLI not found at '{helm_bin}'. Install Helm or set KUBEFORGE_HELM_BIN.",
                severity="warning",
            ))

        return errors

    # ── Private helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_valid_tgz(data: bytes) -> bool:
        """Check if bytes represent a valid gzip tar archive."""
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as _:
                return True
        except (tarfile.TarError, EOFError, OSError):
            return False

    @staticmethod
    def _tgz_has_chart_yaml(data: bytes) -> bool:
        """Check if the .tgz contains a Chart.yaml at any depth."""
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("Chart.yaml") or member.name.endswith("Chart.yml"):
                        return True
        except (tarfile.TarError, EOFError, OSError):
            pass
        return False

    @staticmethod
    def _extract_chart_name(data: bytes) -> str:
        """Extract the chart name from Chart.yaml inside the archive."""
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("Chart.yaml") or member.name.endswith("Chart.yml"):
                        f = tar.extractfile(member)
                        if f:
                            chart_meta = yaml.safe_load(f.read())
                            return chart_meta.get("name", "release")
        except Exception:
            pass
        return "release"

    @staticmethod
    def _helm_template(
        raw_tgz: bytes,
        release_name: str,
        namespace: str,
        values_content: str = "",
    ) -> str:
        """Run ``helm template`` on a .tgz archive and return rendered YAML.

        Uses synchronous subprocess because Helm template is fast and this
        is called from the synchronous ``parse()`` method.
        """
        import subprocess

        helm_bin = settings.helm.bin
        if not shutil.which(helm_bin):
            raise HelmParseError(
                f"Helm CLI not found at '{helm_bin}'. "
                "Install Helm (https://helm.sh/docs/intro/install/) "
                "or set KUBEFORGE_HELM_BIN to the Helm binary path."
            )

        with tempfile.TemporaryDirectory(prefix="kubeforge-helm-") as tmpdir:
            chart_path = Path(tmpdir) / "chart.tgz"
            chart_path.write_bytes(raw_tgz)

            cmd: list[str] = [
                helm_bin, "template",
                release_name,
                str(chart_path),
                "--namespace", namespace,
                "--include-crds",
            ]

            # Write values file if provided
            if values_content and values_content.strip():
                values_path = Path(tmpdir) / "values-override.yaml"
                values_path.write_text(values_content, encoding="utf-8")
                cmd.extend(["--values", str(values_path)])

            logger.info(f"Running: {' '.join(cmd)}")

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=settings.helm.timeout,
                    cwd=tmpdir,
                )
            except FileNotFoundError:
                raise HelmParseError(f"Helm binary not found: {helm_bin}")
            except subprocess.TimeoutExpired:
                raise HelmParseError(
                    f"Helm template timed out after {settings.helm.timeout}s"
                )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise HelmParseError(f"helm template failed (rc={result.returncode}): {stderr}")

            rendered = result.stdout
            if not rendered.strip():
                raise HelmParseError("helm template produced empty output")

            logger.info(f"Helm template rendered {len(rendered)} bytes for '{release_name}'")
            return rendered
