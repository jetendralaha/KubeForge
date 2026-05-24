"""Deployment orchestration routes — apply generated manifests to a K3s/K8s cluster."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kubeforge.db import deployments as deploy_db
from kubeforge.db import projects as proj_db
from kubeforge.models import DeploymentStatus
from kubeforge.orchestrator import deploy_project

logger = logging.getLogger("kubeforge.api.deploy")

router = APIRouter(prefix="/deploy", tags=["deploy"])


class DeployRequest(BaseModel):
    kubeconfig: str = ""
    readiness_timeout: int = 0  # 0 = use settings default


@router.post("/{project_id}", status_code=202)
async def start_deployment(project_id: str, body: DeployRequest | None = None):
    """Deploy generated manifests to a K3s/K8s cluster.

    For multi-chart projects (PaaS + App), the orchestrator deploys PaaS
    first, waits for pod readiness, then deploys the App chart.
    """
    body = body or DeployRequest()

    project = await proj_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    manifests = await deploy_db.list_generated_manifests(project_id)
    if not manifests:
        raise HTTPException(400, "No manifests generated. Run /manifests/{id}/generate first.")

    # Run deployment in background
    deployment = await deploy_db.create_deployment(project_id)

    asyncio.create_task(_run_deploy(
        project_id=project_id,
        deployment_id=deployment.id,
        kubeconfig=body.kubeconfig,
        readiness_timeout=body.readiness_timeout,
    ))

    return {
        "deployment_id": deployment.id,
        "project_id": project_id,
        "status": "running",
    }


async def _run_deploy(
    project_id: str,
    deployment_id: str,
    kubeconfig: str = "",
    readiness_timeout: int = 0,
) -> None:
    """Background task for orchestrated deployment."""
    try:
        await deploy_project(
            project_id=project_id,
            kubeconfig=kubeconfig,
            readiness_timeout=readiness_timeout,
        )
    except Exception as e:
        logger.exception("Deployment failed")
        await deploy_db.update_deployment(
            deployment_id, DeploymentStatus.FAILED, log=str(e),
        )


@router.get("/{deployment_id}/status")
async def get_deployment_status(deployment_id: str):
    """Check deployment status and logs."""
    deployment = await deploy_db.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(404, "Deployment not found")
    return {
        "id": deployment.id,
        "project_id": deployment.project_id,
        "status": deployment.status.value,
        "log": deployment.log,
        "created_at": deployment.created_at.isoformat(),
        "updated_at": deployment.updated_at.isoformat(),
    }


@router.get("/project/{project_id}")
async def list_deployments(project_id: str):
    """List all deployments for a project."""
    db = await deploy_db.get_db()
    cursor = await db.execute(
        "SELECT * FROM deployments WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row["id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
