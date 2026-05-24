"""Artifact CRUD operations."""

from __future__ import annotations

from datetime import datetime

from kubeforge.db.engine import get_db
from kubeforge.models import Artifact, ArtifactStatus, ChartRole


async def create_artifact(
    project_id: str,
    filename: str,
    content: str,
    artifact_type: str = "",
    values_content: str = "",
    namespace: str = "default",
    chart_role: str = "app",
    deploy_order: int = 0,
) -> Artifact:
    artifact = Artifact(
        project_id=project_id,
        filename=filename,
        content=content,
        artifact_type=artifact_type,
        values_content=values_content,
        namespace=namespace,
        chart_role=ChartRole(chart_role),
        deploy_order=deploy_order,
    )
    db = await get_db()
    await db.execute(
        """INSERT INTO deployment_artifacts
           (id, project_id, filename, content, artifact_type, status, parsed_json,
            values_content, namespace, chart_role, deploy_order, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (artifact.id, artifact.project_id, artifact.filename, artifact.content,
         artifact.artifact_type, artifact.status.value, artifact.parsed_json,
         artifact.values_content, artifact.namespace, artifact.chart_role.value,
         artifact.deploy_order, artifact.created_at.isoformat()),
    )
    await db.commit()
    return artifact


async def get_artifact(artifact_id: str) -> Artifact | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM deployment_artifacts WHERE id = ?", (artifact_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_artifact(row)


async def list_artifacts(project_id: str) -> list[Artifact]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM deployment_artifacts WHERE project_id = ? ORDER BY deploy_order ASC, created_at DESC",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_artifact(r) for r in rows]


async def update_artifact_parsed(
    artifact_id: str, artifact_type: str, parsed_json: str, status: ArtifactStatus = ArtifactStatus.PARSED
) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE deployment_artifacts SET artifact_type = ?, parsed_json = ?, status = ? WHERE id = ?",
        (artifact_type, parsed_json, status.value, artifact_id),
    )
    await db.commit()


def _row_to_artifact(row) -> Artifact:
    # Handle both old DB schema (without new columns) and new schema
    values_content = ""
    namespace = "default"
    chart_role = ChartRole.APP
    deploy_order = 0

    try:
        values_content = row["values_content"] or ""
    except (IndexError, KeyError):
        pass
    try:
        namespace = row["namespace"] or "default"
    except (IndexError, KeyError):
        pass
    try:
        chart_role = ChartRole(row["chart_role"]) if row["chart_role"] else ChartRole.APP
    except (IndexError, KeyError, ValueError):
        pass
    try:
        deploy_order = row["deploy_order"] or 0
    except (IndexError, KeyError):
        pass

    return Artifact(
        id=row["id"],
        project_id=row["project_id"],
        filename=row["filename"],
        content=row["content"],
        artifact_type=row["artifact_type"] or "",
        status=ArtifactStatus(row["status"]),
        parsed_json=row["parsed_json"] or "",
        values_content=values_content,
        namespace=namespace,
        chart_role=chart_role,
        deploy_order=deploy_order,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
