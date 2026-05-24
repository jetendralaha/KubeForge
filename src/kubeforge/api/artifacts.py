"""Artifact upload, listing, and retrieval routes."""

from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from kubeforge.db import artifacts as db
from kubeforge.db import projects as proj_db
from kubeforge.events import Event, bus, ARTIFACT_UPLOADED, ARTIFACT_PARSED
from kubeforge.models import ArtifactStatus, ChartRole
from kubeforge.parsers import get_registry
from kubeforge.parsers.base import ParseOptions

logger = logging.getLogger("kubeforge.api.artifacts")

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("/{project_id}/upload", status_code=201)
async def upload_artifact(
    project_id: str,
    file: UploadFile = File(...),
    values_file: UploadFile | None = File(None, description="Helm values.yml override file"),
    namespace: str = Form("default", description="Target namespace for deployment"),
    chart_role: str = Form("app", description="Chart role: 'app' or 'paas' (for multi-chart ordering)"),
):
    """Upload a deployment artifact (Compose, K8s YAML, Helm chart .tgz).

    For Helm charts:
    - Upload the .tgz file as the main ``file``
    - Optionally attach a ``values_file`` with custom values.yml overrides
    - Set ``namespace`` to deploy into a specific namespace (default: "default")
    - Set ``chart_role`` to "paas" or "app" for multi-chart deploy ordering
    """
    project = await proj_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    raw_bytes = await file.read()
    filename = file.filename or "unknown.yaml"
    fn_lower = filename.lower()

    # Determine if binary (Helm .tgz) or text (YAML)
    is_binary = fn_lower.endswith((".tgz", ".tar.gz"))
    if is_binary:
        # Store binary content as base64
        content = base64.b64encode(raw_bytes).decode("ascii")
    else:
        content = raw_bytes.decode("utf-8")

    # Read values file if provided
    values_content = ""
    if values_file and values_file.filename:
        values_content = (await values_file.read()).decode("utf-8")

    # Validate chart_role
    try:
        role = ChartRole(chart_role.lower())
    except ValueError:
        raise HTTPException(400, f"Invalid chart_role: '{chart_role}'. Must be 'app' or 'paas'.")

    # Deploy order: paas=0 (first), app=1 (second)
    deploy_order = 0 if role == ChartRole.PAAS else 1

    # Auto-detect type
    registry = get_registry()
    parser, confidence = registry.detect(content, filename)
    artifact_type = parser.name if parser else ""

    # Create artifact with helm-specific fields
    artifact = await db.create_artifact(
        project_id, filename, content, artifact_type,
        values_content=values_content,
        namespace=namespace,
        chart_role=role.value,
        deploy_order=deploy_order,
    )

    # Auto-parse if detected
    if parser and confidence >= 0.5:
        try:
            opts = ParseOptions(filename=filename, namespace=namespace, values_content=values_content)

            manifest = parser.parse(content, opts)
            parsed_json = manifest.model_dump_json()
            await db.update_artifact_parsed(artifact.id, artifact_type, parsed_json)
            artifact.status = ArtifactStatus.PARSED
            artifact.artifact_type = artifact_type
            artifact.parsed_json = parsed_json

            await bus.publish(Event(ARTIFACT_PARSED, {
                "artifact_id": artifact.id,
                "project_id": project_id,
                "artifact_type": artifact_type,
            }))
        except Exception as e:
            logger.warning(f"Auto-parse failed for {filename}: {e}")
            await db.update_artifact_parsed(artifact.id, artifact_type, "", ArtifactStatus.FAILED)
            artifact.status = ArtifactStatus.FAILED

    await bus.publish(Event(ARTIFACT_UPLOADED, {
        "artifact_id": artifact.id,
        "project_id": project_id,
    }))

    return {
        "id": artifact.id,
        "filename": filename,
        "artifact_type": artifact_type,
        "status": artifact.status.value,
        "confidence": confidence if parser else 0.0,
        "namespace": namespace,
        "chart_role": role.value,
        "deploy_order": deploy_order,
        "has_values": bool(values_content),
    }


@router.get("/{project_id}")
async def list_artifacts(project_id: str):
    artifacts = await db.list_artifacts(project_id)
    return [
        {
            "id": a.id,
            "filename": a.filename,
            "artifact_type": a.artifact_type,
            "status": a.status.value,
            "created_at": a.created_at.isoformat(),
        }
        for a in artifacts
    ]


@router.get("/detail/{artifact_id}")
async def get_artifact(artifact_id: str):
    artifact = await db.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    return {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "filename": artifact.filename,
        "artifact_type": artifact.artifact_type,
        "status": artifact.status.value,
        "content": artifact.content,
        "parsed": json.loads(artifact.parsed_json) if artifact.parsed_json else None,
        "namespace": artifact.namespace,
        "chart_role": artifact.chart_role,
        "deploy_order": artifact.deploy_order,
        "has_values": bool(artifact.values_content),
        "created_at": artifact.created_at.isoformat(),
    }
