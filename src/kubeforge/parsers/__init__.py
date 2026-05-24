"""Parser registry — auto-detect and dispatch to the right parser."""

from __future__ import annotations

import logging

from kubeforge.models.manifest import NormalizedManifest
from kubeforge.parsers.base import ManifestParser, ParseOptions, ValidationError

logger = logging.getLogger("kubeforge.parsers")


class ParserRegistry:
    """Registry of all available manifest parsers."""

    def __init__(self) -> None:
        self._parsers: dict[str, ManifestParser] = {}

    def register(self, parser: ManifestParser) -> None:
        self._parsers[parser.name] = parser
        logger.info(f"Registered parser: {parser.name}")

    def get(self, name: str) -> ManifestParser | None:
        return self._parsers.get(name)

    def list_parsers(self) -> list[str]:
        return list(self._parsers.keys())

    def detect(self, content: str, filename: str = "") -> tuple[ManifestParser | None, float]:
        """Auto-detect the best parser for the given content.

        Returns (parser, confidence) or (None, 0.0).
        """
        best_parser: ManifestParser | None = None
        best_score = 0.0

        for parser in self._parsers.values():
            try:
                score = parser.detect(content, filename)
                if score > best_score:
                    best_score = score
                    best_parser = parser
            except Exception:
                logger.exception(f"Error in {parser.name}.detect()")

        if best_parser:
            logger.info(f"Detected parser: {best_parser.name} (confidence={best_score:.2f})")

        return best_parser, best_score

    def parse_auto(self, content: str, filename: str = "", namespace: str = "default") -> NormalizedManifest:
        """Detect and parse content automatically."""
        parser, score = self.detect(content, filename)
        if parser is None or score < 0.3:
            raise ValueError(f"Cannot detect manifest type (best confidence: {score:.2f})")

        opts = ParseOptions(filename=filename, namespace=namespace)
        return parser.parse(content, opts)

    def validate_auto(self, content: str, filename: str = "") -> list[ValidationError]:
        """Detect and validate content."""
        parser, _ = self.detect(content, filename)
        if parser is None:
            return [ValidationError(message="Cannot detect manifest type")]
        return parser.validate(content)


# ── Singleton with built-in parsers ─────────────────────────────────

_registry: ParserRegistry | None = None


def get_registry() -> ParserRegistry:
    """Get or create the singleton parser registry with all built-in parsers."""
    global _registry
    if _registry is None:
        _registry = ParserRegistry()

        from kubeforge.parsers.compose import ComposeParser
        from kubeforge.parsers.helm import HelmParser
        from kubeforge.parsers.kubernetes import KubernetesParser

        _registry.register(ComposeParser())
        _registry.register(KubernetesParser())
        _registry.register(HelmParser())

    return _registry
