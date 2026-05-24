"""Offline packaging, ISO creation, and bundle download routes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from kubeforge.db import deployments as deploy_db
from kubeforge.db import images as image_db
from kubeforge.db import projects as proj_db
from kubeforge.events import Event, bus, PACKAGE_STARTED, PACKAGE_COMPLETED
from kubeforge.models import PackageStatus
from kubeforge.packager import create_bundle, create_iso
from kubeforge.bootable_iso import create_bootable_iso

logger = logging.getLogger("kubeforge.api.packages")

router = APIRouter(prefix="/packages", tags=["packages"])


@router.post("/{project_id}", status_code=202)
async def create_package(project_id: str):
    """Create an offline deployment bundle (async)."""
    project = await proj_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    manifests_list = await deploy_db.list_generated_manifests(project_id)
    if not manifests_list:
        raise HTTPException(400, "No manifests generated. Run /manifests/{id}/generate first.")

    # Collect manifests and images
    manifest_files = {m.filename: m.content for m in manifests_list}
    images = await image_db.list_project_images(project_id)
    image_refs = [i.reference for i in images]

    # Create job
    job = await deploy_db.create_packaging_job(project_id)
    await deploy_db.update_packaging_job(job.id, PackageStatus.BUILDING)

    await bus.publish(Event(PACKAGE_STARTED, {
        "job_id": job.id, "project_id": project_id,
    }))

    # Run packaging in background
    asyncio.create_task(_run_packaging(job.id, project.name, manifest_files, image_refs))

    return {"job_id": job.id, "status": "building"}


# ── ISO creation ────────────────────────────────────────────────────


class ISORequest(BaseModel):
    pull_images: bool = True
    download_k3s: bool = True
    target_arch: str = "auto"
    bootable: bool = False  # If True, create bootable ISO with Alpine Linux OS
    registry_credentials: list[dict] | None = None  # [{registry, username, password}]
    auth_file: str = ""  # Path to Docker config.json
    insecure_registries: list[str] | None = None  # Registries to skip TLS for


@router.post("/{project_id}/iso", status_code=202)
async def create_iso_package(project_id: str, body: ISORequest | None = None):
    """Create a full air-gap ISO image with pulled images and K3s binaries.

    The ISO file includes everything needed for on-premises deployment
    without internet access.
    """
    body = body or ISORequest()

    project = await proj_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    manifests_list = await deploy_db.list_generated_manifests(project_id)
    if not manifests_list:
        raise HTTPException(400, "No manifests generated. Run /manifests/{id}/generate first.")

    manifest_files = {m.filename: m.content for m in manifests_list}
    images = await image_db.list_project_images(project_id)
    image_refs = [i.reference for i in images]

    # Auto-resolve images if none are linked yet
    if not image_refs and body.pull_images:
        logger.info(f"No images linked for project {project_id}, auto-resolving...")
        from kubeforge.db import artifacts as artifact_db
        from kubeforge.models.manifest import NormalizedManifest
        from kubeforge.resolver import extract_images, resolve_image

        artifacts = await artifact_db.list_artifacts(project_id)
        for artifact in artifacts:
            if not artifact.parsed_json:
                continue
            manifest = NormalizedManifest.model_validate_json(artifact.parsed_json)
            refs = extract_images(manifest)
            for ref in refs:
                img = resolve_image(ref)
                existing = await image_db.get_image_by_ref(img.reference)
                if not existing:
                    existing = await image_db.create_image(
                        img.reference, img.registry, img.repository, img.tag, img.digest,
                    )
                await image_db.link_artifact_image(artifact.id, existing.id)
        # Re-fetch
        images = await image_db.list_project_images(project_id)
        image_refs = [i.reference for i in images]
        logger.info(f"Auto-resolved {len(image_refs)} images for project {project_id}")

    # Create job
    job = await deploy_db.create_packaging_job(project_id)
    await deploy_db.update_packaging_job(job.id, PackageStatus.BUILDING)

    await bus.publish(Event(PACKAGE_STARTED, {
        "job_id": job.id, "project_id": project_id, "format": "iso",
        "bootable": body.bootable,
    }))

    asyncio.create_task(_run_iso_packaging(
        job.id, project.name, manifest_files, image_refs,
        pull_images=body.pull_images,
        download_k3s=body.download_k3s,
        target_arch=body.target_arch,
        bootable=body.bootable,
        registry_credentials=body.registry_credentials,
        auth_file=body.auth_file,
        insecure_registries=body.insecure_registries,
    ))

    return {"job_id": job.id, "status": "building", "format": "bootable-iso" if body.bootable else "iso"}


async def _run_iso_packaging(
    job_id: str,
    project_name: str,
    manifests: dict[str, str],
    images: list[str],
    pull_images: bool = True,
    download_k3s: bool = True,
    target_arch: str = "auto",
    bootable: bool = False,
    registry_credentials: list[dict] | None = None,
    auth_file: str = "",
    insecure_registries: list[str] | None = None,
) -> None:
    """Background task for ISO creation."""
    try:
        if bootable:
            output_path = await create_bootable_iso(
                project_name,
                manifests,
                images,
                pull_container_images=pull_images,
                download_k3s=download_k3s,
                target_arch=target_arch,
                registry_credentials=registry_credentials,
                auth_file=auth_file,
                insecure_registries=insecure_registries,
            )
        else:
            output_path = await create_iso(
                project_name,
                manifests,
                images,
                pull_container_images=pull_images,
                download_k3s=download_k3s,
                target_arch=target_arch,
                registry_credentials=registry_credentials,
                auth_file=auth_file,
                insecure_registries=insecure_registries,
            )
        size = Path(output_path).stat().st_size
        await deploy_db.update_packaging_job(
            job_id, PackageStatus.COMPLETED, output_path=output_path, size_bytes=size,
        )
        await bus.publish(Event(PACKAGE_COMPLETED, {
            "job_id": job_id, "output_path": output_path, "size_bytes": size, "format": "iso",
        }))
    except Exception as e:
        logger.exception("ISO packaging failed")
        await deploy_db.update_packaging_job(job_id, PackageStatus.FAILED, error_message=str(e))


# ── Existing tar.gz background task ────────────────────────────────


async def _run_packaging(job_id: str, project_name: str, manifests: dict[str, str], images: list[str]) -> None:
    """Background task for bundle creation."""
    try:
        output_path = await create_bundle(project_name, manifests, images)
        size = Path(output_path).stat().st_size
        await deploy_db.update_packaging_job(
            job_id, PackageStatus.COMPLETED, output_path=output_path, size_bytes=size,
        )
        await bus.publish(Event(PACKAGE_COMPLETED, {
            "job_id": job_id, "output_path": output_path, "size_bytes": size,
        }))
    except Exception as e:
        logger.exception("Packaging failed")
        await deploy_db.update_packaging_job(job_id, PackageStatus.FAILED, error_message=str(e))


@router.get("/{job_id}/status")
async def get_package_status(job_id: str):
    """Check packaging job status."""
    job = await deploy_db.get_packaging_job(job_id)
    if not job:
        raise HTTPException(404, "Packaging job not found")
    return {
        "id": job.id,
        "status": job.status.value,
        "output_path": job.output_path,
        "size_bytes": job.size_bytes,
        "error": job.error_message,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("/{job_id}/download")
async def download_package(job_id: str):
    """Download the completed bundle archive or ISO."""
    job = await deploy_db.get_packaging_job(job_id)
    if not job:
        raise HTTPException(404, "Packaging job not found")
    if job.status != PackageStatus.COMPLETED:
        raise HTTPException(400, f"Package not ready (status: {job.status.value})")
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(404, "Package file not found")

    # Detect media type from extension
    suffix = Path(job.output_path).suffix.lower()
    media_types = {
        ".iso": "application/x-iso9660-image",
        ".gz": "application/gzip",
        ".tgz": "application/gzip",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=job.output_path,
        media_type=media_type,
        filename=Path(job.output_path).name,
    )
