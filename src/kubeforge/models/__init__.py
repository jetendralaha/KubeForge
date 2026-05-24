"""Domain models for KubeForge persistence and API schemas."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex[:20]


# ── Status enums ────────────────────────────────────────────────────


class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ArtifactStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"


class AnalysisStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DeploymentStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class PackageStatus(str, enum.Enum):
    PENDING = "pending"
    BUILDING = "building"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Domain models ───────────────────────────────────────────────────


class Project(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ChartRole(str, enum.Enum):
    """Role of a Helm chart in a multi-chart project."""
    APP = "app"
    PAAS = "paas"


class Artifact(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    filename: str
    content: str
    artifact_type: str = ""          # docker-compose, kubernetes, helm
    status: ArtifactStatus = ArtifactStatus.UPLOADED
    parsed_json: str = ""            # serialised NormalizedManifest
    values_content: str = ""         # Helm values.yml content
    namespace: str = "default"       # Target namespace for deployment
    chart_role: ChartRole = ChartRole.APP  # paas or app (for multi-chart ordering)
    deploy_order: int = 0            # Explicit deploy order (0 = first)
    created_at: datetime = Field(default_factory=_now)


class Image(BaseModel):
    id: str = Field(default_factory=_new_id)
    reference: str                   # full image ref
    registry: str = ""
    repository: str = ""
    tag: str = ""
    digest: str = ""
    size_bytes: int = 0
    cached: bool = False
    cached_path: str = ""
    created_at: datetime = Field(default_factory=_now)


class AIAnalysis(BaseModel):
    id: str = Field(default_factory=_new_id)
    artifact_id: str
    project_id: str
    analysis_type: str = ""          # risk, recommendation, detection
    status: AnalysisStatus = AnalysisStatus.PENDING
    result_json: str = ""
    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class RiskItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    analysis_id: str
    project_id: str
    title: str
    description: str = ""
    severity: str = "medium"         # critical, high, medium, low, info
    category: str = ""               # security, reliability, performance
    remediation: str = ""


class GeneratedManifest(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    artifact_id: str = ""
    filename: str
    content: str
    kind: str = ""
    created_at: datetime = Field(default_factory=_now)


class PackagingJob(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    status: PackageStatus = PackageStatus.PENDING
    output_path: str = ""
    size_bytes: int = 0
    error_message: str = ""
    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class Deployment(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    package_id: str = ""
    target: str = ""
    status: DeploymentStatus = DeploymentStatus.PENDING
    log: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AuditLog(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str = ""
    action: str
    entity_type: str = ""
    entity_id: str = ""
    details: str = ""
    created_at: datetime = Field(default_factory=_now)


class RegistryCredential(BaseModel):
    """Credential for authenticating with a private container registry."""
    registry: str                    # e.g., ghcr.io, 123456.dkr.ecr.us-east-1.amazonaws.com
    username: str
    password: str                    # password or token
    auth_type: str = "basic"         # basic, token, aws-ecr


# ── API request / response schemas ─────────────────────────────────


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime


class AnalysisResult(BaseModel):
    artifact_type: str = ""
    confidence: float = 0.0
    risks: list[RiskItem] = Field(default_factory=list)
    recommendations: list[RecommendationItem] = Field(default_factory=list)


class RecommendationItem(BaseModel):
    category: str = ""
    title: str = ""
    description: str = ""
    priority: str = "medium"
    auto_apply: bool = False


class DetectionResult(BaseModel):
    type: str
    confidence: float
    reasoning: str = ""


class RiskResult(BaseModel):
    risks: list[RiskItem]
    summary: str = ""


class RecommendationResult(BaseModel):
    recommendations: list[RecommendationItem]
    summary: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
