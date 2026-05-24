"""Project CRUD operations."""

from __future__ import annotations

from datetime import UTC, datetime

from kubeforge.db.engine import get_db
from kubeforge.models import Project, ProjectStatus


async def create_project(name: str, description: str = "") -> Project:
    project = Project(name=name, description=description)
    db = await get_db()
    await db.execute(
        """INSERT INTO projects (id, name, description, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (project.id, project.name, project.description, project.status.value,
         project.created_at.isoformat(), project.updated_at.isoformat()),
    )
    await db.commit()
    return project


async def get_project(project_id: str) -> Project | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_project(row)


async def list_projects(status: str | None = None) -> list[Project]:
    db = await get_db()
    if status:
        cursor = await db.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC", (status,)
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM projects WHERE status != 'deleted' ORDER BY created_at DESC"
        )
    rows = await cursor.fetchall()
    return [_row_to_project(r) for r in rows]


async def update_project_status(project_id: str, status: ProjectStatus) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (status.value, datetime.now(UTC).isoformat(), project_id),
    )
    await db.commit()


async def delete_project(project_id: str) -> None:
    await update_project_status(project_id, ProjectStatus.DELETED)


def _row_to_project(row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        status=ProjectStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
