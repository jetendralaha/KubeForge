"""Deployment, packaging job, generated manifest, and audit log CRUD."""

from __future__ import annotations

from datetime import UTC, datetime

from kubeforge.db.engine import get_db
from kubeforge.models import (
    AuditLog,
    Deployment,
    DeploymentStatus,
    GeneratedManifest,
    PackageStatus,
    PackagingJob,
)

# ── Generated manifests ────────────────────────────────────────────


async def create_generated_manifest(
    project_id: str, artifact_id: str, filename: str, content: str, kind: str = ""
) -> GeneratedManifest:
    m = GeneratedManifest(project_id=project_id, artifact_id=artifact_id, filename=filename, content=content, kind=kind)
    db = await get_db()
    await db.execute(
        """INSERT INTO generated_manifests (id, project_id, artifact_id, filename, content, kind, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (m.id, m.project_id, m.artifact_id, m.filename, m.content, m.kind, m.created_at.isoformat()),
    )
    await db.commit()
    return m


async def list_generated_manifests(project_id: str) -> list[GeneratedManifest]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM generated_manifests WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )
    return [_row_to_manifest(r) for r in await cursor.fetchall()]


async def delete_project_manifests(project_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM generated_manifests WHERE project_id = ?", (project_id,))
    await db.commit()


# ── Packaging jobs ─────────────────────────────────────────────────


async def create_packaging_job(project_id: str) -> PackagingJob:
    job = PackagingJob(project_id=project_id)
    db = await get_db()
    await db.execute(
        """INSERT INTO packaging_jobs (id, project_id, status, output_path, size_bytes, error_message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (job.id, job.project_id, job.status.value, job.output_path, job.size_bytes,
         job.error_message, job.created_at.isoformat()),
    )
    await db.commit()
    return job


async def get_packaging_job(job_id: str) -> PackagingJob | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM packaging_jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    return _row_to_job(row) if row else None


async def update_packaging_job(
    job_id: str, status: PackageStatus,
    output_path: str = "", size_bytes: int = 0, error_message: str = "",
) -> None:
    db = await get_db()
    completed = datetime.now(UTC).isoformat() if status in (PackageStatus.COMPLETED, PackageStatus.FAILED) else None
    await db.execute(
        """UPDATE packaging_jobs SET status = ?, output_path = ?, size_bytes = ?,
           error_message = ?, completed_at = ? WHERE id = ?""",
        (status.value, output_path, size_bytes, error_message, completed, job_id),
    )
    await db.commit()


# ── Deployments ────────────────────────────────────────────────────


async def create_deployment(project_id: str, package_id: str = "", target: str = "") -> Deployment:
    dep = Deployment(project_id=project_id, package_id=package_id, target=target)
    db = await get_db()
    await db.execute(
        """INSERT INTO deployments (id, project_id, package_id, target, status, log, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (dep.id, dep.project_id, dep.package_id, dep.target, dep.status.value,
         dep.log, dep.created_at.isoformat(), dep.updated_at.isoformat()),
    )
    await db.commit()
    return dep


async def get_deployment(deployment_id: str) -> Deployment | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,))
    row = await cursor.fetchone()
    return _row_to_deployment(row) if row else None


async def update_deployment_status(deployment_id: str, status: DeploymentStatus, log: str = "") -> None:
    db = await get_db()
    await db.execute(
        "UPDATE deployments SET status = ?, log = ?, updated_at = ? WHERE id = ?",
        (status.value, log, datetime.now(UTC).isoformat(), deployment_id),
    )
    await db.commit()


# Alias used by the orchestrator
async def update_deployment(deployment_id: str, status: DeploymentStatus, log: str = "") -> None:
    """Update deployment status and log — alias for update_deployment_status."""
    await update_deployment_status(deployment_id, status, log)


# ── Audit log ──────────────────────────────────────────────────────


async def create_audit_log(action: str, project_id: str = "", entity_type: str = "",
                           entity_id: str = "", details: str = "") -> AuditLog:
    entry = AuditLog(action=action, project_id=project_id, entity_type=entity_type,
                     entity_id=entity_id, details=details)
    db = await get_db()
    await db.execute(
        """INSERT INTO audit_logs (id, project_id, action, entity_type, entity_id, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entry.id, entry.project_id, entry.action, entry.entity_type,
         entry.entity_id, entry.details, entry.created_at.isoformat()),
    )
    await db.commit()
    return entry


# ── Row converters ─────────────────────────────────────────────────


def _row_to_manifest(row) -> GeneratedManifest:
    return GeneratedManifest(
        id=row["id"], project_id=row["project_id"], artifact_id=row["artifact_id"] or "",
        filename=row["filename"], content=row["content"], kind=row["kind"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_job(row) -> PackagingJob:
    return PackagingJob(
        id=row["id"], project_id=row["project_id"],
        status=PackageStatus(row["status"]), output_path=row["output_path"] or "",
        size_bytes=row["size_bytes"], error_message=row["error_message"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
    )


def _row_to_deployment(row) -> Deployment:
    return Deployment(
        id=row["id"], project_id=row["project_id"], package_id=row["package_id"] or "",
        target=row["target"] or "", status=DeploymentStatus(row["status"]),
        log=row["log"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
