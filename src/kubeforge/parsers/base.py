"""Abstract base class for manifest parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from kubeforge.models.manifest import NormalizedManifest


@dataclass
class ParseOptions:
    """Options passed to parsers."""
    filename: str = ""
    namespace: str = "default"
    strict: bool = False
    values_content: str = ""  # Helm values.yml override content


@dataclass
class ValidationError:
    message: str
    line: int = 0
    field: str = ""
    severity: str = "error"          # error, warning


class ManifestParser(ABC):
    """Interface every parser must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def detect(self, content: str, filename: str = "") -> float:
        """Return a confidence score 0.0–1.0 that this parser handles the content."""
        ...

    @abstractmethod
    def parse(self, content: str, options: ParseOptions | None = None) -> NormalizedManifest:
        """Parse content into a NormalizedManifest."""
        ...

    @abstractmethod
    def validate(self, content: str) -> list[ValidationError]:
        """Check for structural errors without fully parsing."""
        ...
