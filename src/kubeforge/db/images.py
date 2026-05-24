"""Image CRUD operations."""

from __future__ import annotations

from datetime import datetime

from kubeforge.db.engine import get_db
from kubeforge.models import Image


async def create_image(reference: str, registry: str = "", repository: str = "", tag: str = "", digest: str = "") -> Image:
    img = Image(reference=reference, registry=registry, repository=repository, tag=tag, digest=digest)
    db = await get_db()
    # Use both old and new column names for compatibility
    await db.execute(
        """INSERT OR IGNORE INTO images (id, reference, name, registry, repository, tag, digest, size_bytes, cached, is_cached, cached_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (img.id, img.reference, img.repository or img.reference, img.registry, img.repository, img.tag, img.digest,
         img.size_bytes, int(img.cached), int(img.cached), img.cached_path, img.created_at.isoformat()),
    )
    await db.commit()
    return img


async def get_image_by_ref(reference: str) -> Image | None:
    db = await get_db()
    # Try reference column first, fall back to name:tag match
    try:
        cursor = await db.execute("SELECT * FROM images WHERE reference = ?", (reference,))
        row = await cursor.fetchone()
    except Exception:
        # Fallback: older schema without reference column
        parts = reference.rsplit(":", 1)
        name = parts[0]
        tag = parts[1] if len(parts) > 1 else "latest"
        cursor = await db.execute("SELECT * FROM images WHERE name = ? AND tag = ?", (name, tag))
        row = await cursor.fetchone()
    return _row_to_image(row) if row else None


async def link_artifact_image(artifact_id: str, image_id: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO artifact_images (artifact_id, image_id) VALUES (?, ?)",
        (artifact_id, image_id),
    )
    await db.commit()


async def list_project_images(project_id: str) -> list[Image]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT DISTINCT i.* FROM images i
           JOIN artifact_images ai ON i.id = ai.image_id
           JOIN deployment_artifacts da ON ai.artifact_id = da.id
           WHERE da.project_id = ?""",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_image(r) for r in rows]


async def update_image_cache(image_id: str, cached_path: str, size_bytes: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE images SET cached = 1, cached_path = ?, size_bytes = ? WHERE id = ?",
        (cached_path, size_bytes, image_id),
    )
    await db.commit()


def _row_to_image(row) -> Image:
    # Handle both old schema (name, is_cached) and new schema (reference, repository, cached)
    keys = row.keys() if hasattr(row, "keys") else []

    reference = row["reference"] if "reference" in keys else ""
    repository = row["repository"] if "repository" in keys else ""
    name = row["name"] if "name" in keys else ""

    # Fallback: build reference from name:tag if reference column is empty/missing
    if not reference:
        tag = row["tag"] or ""
        reference = f"{name}:{tag}" if tag else name

    if not repository:
        repository = name

    cached = False
    if "cached" in keys:
        cached = bool(row["cached"])
    elif "is_cached" in keys:
        cached = bool(row["is_cached"])

    created_at_str = row["created_at"] if "created_at" in keys else None
    if created_at_str:
        created_at = datetime.fromisoformat(created_at_str)
    else:
        resolved = row["resolved_at"] if "resolved_at" in keys else None
        created_at = datetime.fromisoformat(resolved) if resolved else datetime.utcnow()

    return Image(
        id=row["id"],
        reference=reference,
        registry=row["registry"] or "",
        repository=repository,
        tag=row["tag"] or "",
        digest=row["digest"] or "",
        size_bytes=row["size_bytes"],
        cached=cached,
        cached_path=row["cached_path"] or "",
        created_at=created_at,
    )
