"""KubeForge configuration via environment variables and defaults."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _default_data_dir() -> Path:
    return Path(os.environ.get("KUBEFORGE_DATA_DIR", Path.home() / ".kubeforge"))


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080
    reload: bool = False

    model_config = {"env_prefix": "KUBEFORGE_SERVER_"}


class DatabaseSettings(BaseSettings):
    url: str = Field(default_factory=lambda: f"sqlite+aiosqlite:///{_default_data_dir() / 'kubeforge.db'}")

    model_config = {"env_prefix": "KUBEFORGE_DB_"}


class AISettings(BaseSettings):
    """LLM configuration.

    Supports any OpenAI-compatible API (OpenAI, Azure, Groq, Ollama, vLLM,
    LM Studio, etc.).  Set ``KUBEFORGE_AI_BASE_URL`` and ``KUBEFORGE_AI_API_KEY``.

    For Ollama (default, no key needed):
      KUBEFORGE_AI_BASE_URL=http://localhost:11434/v1

    For OpenAI:
      KUBEFORGE_AI_BASE_URL=https://api.openai.com/v1
      KUBEFORGE_AI_API_KEY=sk-...
      KUBEFORGE_AI_MODEL=gpt-4o-mini

    For Azure OpenAI:
      KUBEFORGE_AI_BASE_URL=https://<resource>.openai.azure.com/openai/deployments/<deployment>
      KUBEFORGE_AI_API_KEY=<azure-key>
      KUBEFORGE_AI_MODEL=gpt-4o-mini

    For any OpenAI-compatible provider (Groq, Together, Mistral, etc.):
      KUBEFORGE_AI_BASE_URL=https://api.groq.com/openai/v1
      KUBEFORGE_AI_API_KEY=gsk_...
      KUBEFORGE_AI_MODEL=llama-3.1-70b-versatile
    """
    base_url: str = "http://localhost:11434/v1"  # OpenAI-compatible base URL
    api_key: str = ""                            # API key (empty = no auth, e.g. local Ollama)
    model: str = "mistral:7b"                    # Chat model name
    embedding_model: str = "nomic-embed-text"    # Embedding model name
    embedding_dim: int = 768
    timeout: float = 120.0

    # Legacy alias kept for backwards compat with KUBEFORGE_AI_OLLAMA_URL
    ollama_url: str = ""

    model_config = {"env_prefix": "KUBEFORGE_AI_"}

    @property
    def effective_base_url(self) -> str:
        """Resolve the actual base URL to use."""
        # If user set the legacy OLLAMA_URL, derive base_url from it
        if self.ollama_url:
            return self.ollama_url.rstrip("/") + "/v1"
        return self.base_url.rstrip("/")


class QdrantSettings(BaseSettings):
    url: str = "http://localhost:6333"
    docs_collection: str = "kubeforge_docs"
    manifests_collection: str = "kubeforge_manifests"

    model_config = {"env_prefix": "KUBEFORGE_QDRANT_"}


class PackagerSettings(BaseSettings):
    output_dir: str = Field(default_factory=lambda: str(_default_data_dir() / "packages"))
    k3s_version: str = "v1.30.2+k3s1"
    target_arch: str = "auto"                # "amd64", "arm64", or "auto"
    pull_images: bool = True                  # Pull container images for ISO
    download_k3s: bool = True                 # Download K3s binary for ISO
    iso_backend: str = "auto"                 # "xorriso", "genisoimage", "mkisofs", "pycdlib", "auto"
    max_concurrent_pulls: int = 3             # Parallel image pulls
    image_pull_backend: str = "auto"          # "docker", "skopeo", "auto"

    model_config = {"env_prefix": "KUBEFORGE_PACKAGER_"}


class HelmSettings(BaseSettings):
    """Settings for Helm chart processing."""
    bin: str = "helm"                         # Path to Helm CLI binary
    timeout: int = 120                        # Helm template render timeout (seconds)

    model_config = {"env_prefix": "KUBEFORGE_HELM_"}


class RegistrySettings(BaseSettings):
    """Settings for private container registry authentication."""
    auth_file: str = ""                       # Path to Docker-compatible config.json or containers auth.json
    tls_verify: bool = True                   # Verify TLS certificates for registries
    insecure_registries: list[str] = Field(default_factory=list)  # Registries to treat as HTTP (no TLS)

    model_config = {"env_prefix": "KUBEFORGE_REGISTRY_"}


class DeploySettings(BaseSettings):
    """Settings for deployment orchestration."""
    kubectl_bin: str = "kubectl"              # Path to kubectl binary
    kubeconfig: str = ""                      # Path to kubeconfig (default: ~/.kube/config)
    readiness_timeout: int = 300              # Seconds to wait for pod readiness between chart deploys
    poll_interval: int = 5                    # Seconds between readiness checks

    model_config = {"env_prefix": "KUBEFORGE_DEPLOY_"}


class Settings(BaseSettings):
    """Root settings container."""

    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    ai: AISettings = Field(default_factory=AISettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    packager: PackagerSettings = Field(default_factory=PackagerSettings)
    helm: HelmSettings = Field(default_factory=HelmSettings)
    registry: RegistrySettings = Field(default_factory=RegistrySettings)
    deploy: DeploySettings = Field(default_factory=DeploySettings)
    log_level: str = "INFO"
    migrations_dir: str = Field(
        default_factory=lambda: str(Path(__file__).resolve().parent.parent.parent / "migrations")
    )

    model_config = {"env_prefix": "KUBEFORGE_"}


settings = Settings()
