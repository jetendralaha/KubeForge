"""Manifest generation and image resolution routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from kubeforge.db import artifacts as artifact_db
from kubeforge.db import deployments as deploy_db
from kubeforge.db import images as image_db
from kubeforge.db import projects as proj_db
from kubeforge.events import IMAGES_RESOLVED, MANIFESTS_GENERATED, Event, bus
from kubeforge.generator import generate
from kubeforge.models import ArtifactStatus
from kubeforge.models.manifest import NormalizedManifest
from kubeforge.parsers import get_registry
from kubeforge.parsers.base import ParseOptions
from kubeforge.resolver import extract_images, resolve_image

logger = logging.getLogger("kubeforge.api.manifests")

router = APIRouter(prefix="/manifests", tags=["manifests"])


@router.post("/{project_id}/generate")
async def generate_manifests(project_id: str, namespace: str = ""):
    """Generate production-ready K3s manifests from all project artifacts.

    Artifacts are processed in deploy_order (PaaS first, App second).
    Each artifact's namespace is respected — if no namespace is provided at
    the API level, the artifact-level namespace is used.
    """
    project = await proj_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    artifacts = await artifact_db.list_artifacts(project_id)
    parsed = [a for a in artifacts if a.parsed_json]

    # Attempt on-demand parsing for unparsed artifacts
    if not parsed:
        unparsed = [a for a in artifacts if not a.parsed_json and a.artifact_type]
        if not unparsed:
            raise HTTPException(400, "No artifacts found. Upload artifacts first.")

        registry = get_registry()
        parse_errors: list[str] = []

        for a in unparsed:
            parser, _ = registry.detect(a.content, a.filename)
            if not parser:
                continue
            try:
                opts = ParseOptions(
                    filename=a.filename,
                    namespace=a.namespace,
                    values_content=a.values_content,
                )
                manifest = parser.parse(a.content, opts)
                parsed_json = manifest.model_dump_json()
                await artifact_db.update_artifact_parsed(a.id, a.artifact_type, parsed_json)
                a.parsed_json = parsed_json
                a.status = ArtifactStatus.PARSED
                parsed.append(a)
            except Exception as e:
                parse_errors.append(f"{a.filename}: {e}")
                logger.warning(f"On-demand parse failed for {a.filename}: {e}")

        if not parsed:
            detail = "Failed to parse artifacts."
            if parse_errors:
                detail += " Errors:\n" + "\n".join(parse_errors)
            raise HTTPException(400, detail)

    # Sort by deploy_order (PaaS = 0 before App = 1)
    parsed.sort(key=lambda a: (a.deploy_order, a.created_at))

    # Delete previous manifests
    await deploy_db.delete_project_manifests(project_id)

    all_files: dict[str, str] = {}

    for idx, artifact in enumerate(parsed):
        manifest = NormalizedManifest.model_validate_json(artifact.parsed_json)

        # Namespace priority: API-level override > artifact-level > manifest-level > project name
        ns = namespace or artifact.namespace or manifest.namespace or project.name

        # Determine if this is a helm-rendered manifest (skip hardening)
        skip_hardening = artifact.artifact_type == "helm"

        files = generate(manifest, ns, skip_hardening=skip_hardening)

        # Build a clean role label: "paas" / "app" (from enum value) + namespace
        role_value = artifact.chart_role
        if hasattr(role_value, "value"):
            role_value = role_value.value  # Extract enum value
        role_label = f"{idx:02d}-{role_value}-{ns}" if len(parsed) > 1 else ns

        # Accumulate for combined manifest
        for filename, content in sorted(files.items()):
            key = f"{role_label}/{filename}"
            all_files[key] = content

    # Generate single combined deploy.yaml with proper K8s dependency order
    combined_parts = []
    for key in sorted(all_files.keys()):
        combined_parts.append(f"# --- {key} ---\n{all_files[key]}")
    combined_content = "\n---\n".join(combined_parts)

    # Store only the combined deploy.yaml in DB
    await deploy_db.create_generated_manifest(
        project_id, parsed[0].id, "deploy.yaml", combined_content,
        kind="combined",
    )

    await bus.publish(Event(MANIFESTS_GENERATED, {
        "project_id": project_id,
        "manifest_count": len(all_files),
    }))

    return {
        "project_id": project_id,
        "manifest_count": len(all_files),
        "files": ["deploy.yaml"],
        "sections": list(all_files.keys()),
    }


@router.get("/{project_id}")
async def list_manifests(project_id: str):
    """List generated manifests."""
    manifests = await deploy_db.list_generated_manifests(project_id)
    return [
        {"id": m.id, "filename": m.filename, "kind": m.kind, "created_at": m.created_at.isoformat()}
        for m in manifests
    ]


@router.get("/{project_id}/download")
async def download_manifests(project_id: str):
    """Get all generated manifest content."""
    manifests = await deploy_db.list_generated_manifests(project_id)
    if not manifests:
        raise HTTPException(404, "No manifests generated yet")
    return {m.filename: m.content for m in manifests}


@router.post("/{project_id}/resolve-images")
async def resolve_images(project_id: str):
    """Resolve and catalogue all images from parsed artifacts."""
    artifacts = await artifact_db.list_artifacts(project_id)
    parsed = [a for a in artifacts if a.parsed_json]

    all_images: list[dict] = []

    for artifact in parsed:
        manifest = NormalizedManifest.model_validate_json(artifact.parsed_json)
        refs = extract_images(manifest)

        for ref in refs:
            img = resolve_image(ref)
            # Store or get existing
            existing = await image_db.get_image_by_ref(img.reference)
            if not existing:
                existing = await image_db.create_image(
                    img.reference, img.registry, img.repository, img.tag, img.digest,
                )
            await image_db.link_artifact_image(artifact.id, existing.id)
            all_images.append({
                "id": existing.id,
                "reference": existing.reference,
                "registry": existing.registry,
                "repository": existing.repository,
                "tag": existing.tag,
                "cached": existing.cached,
            })

    await bus.publish(Event(IMAGES_RESOLVED, {
        "project_id": project_id,
        "image_count": len(all_images),
    }))

    return {"project_id": project_id, "images": all_images}


@router.get("/{project_id}/images")
async def list_images(project_id: str):
    """List all images for a project."""
    images = await image_db.list_project_images(project_id)
    return [
        {
            "id": i.id, "reference": i.reference, "registry": i.registry,
            "repository": i.repository, "tag": i.tag, "cached": i.cached,
        }
        for i in images
    ]
