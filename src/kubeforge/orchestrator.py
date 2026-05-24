"""Deployment orchestrator — applies generated manifests to a K3s/K8s cluster.

Handles multi-chart ordering:
1. Groups manifests by chart role (PaaS first, then App).
2. Applies PaaS manifests → waits for pod readiness.
3. Applies App manifests → waits for pod readiness.
4. Reports status via the event bus.

Uses ``kubectl`` under the hood.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from kubeforge.config import settings
from kubeforge.db import deployments as deploy_db
from kubeforge.events import (
    CHART_DEPLOYED,
    DEPLOY_COMPLETED,
    DEPLOY_FAILED,
    DEPLOY_STARTED,
    Event,
    bus,
)
from kubeforge.models import Deployment, DeploymentStatus

logger = logging.getLogger("kubeforge.orchestrator")

ProgressCallback = Callable[[str], None]


class DeployError(Exception):
    """Raised when a deployment step fails."""


async def _run_cmd(cmd: list[str], timeout: float = 600.0) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise DeployError(f"Command timed out after {timeout}s: {' '.join(cmd)}")
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def kubectl_apply(
    manifest_content: str,
    namespace: str = "",
    kubeconfig: str = "",
) -> str:
    """Apply a YAML manifest via ``kubectl apply -f -``.

    Returns: kubectl stdout output.
    """
    kubectl = settings.deploy.kubectl_bin
    cmd = [kubectl, "apply", "-f", "-"]

    if namespace:
        cmd.extend(["--namespace", namespace])
    if kubeconfig or settings.deploy.kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig or settings.deploy.kubeconfig])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=manifest_content.encode()),
        timeout=60.0,
    )

    if proc.returncode != 0:
        raise DeployError(f"kubectl apply failed: {stderr.decode()}")

    return stdout.decode()


async def wait_for_readiness(
    namespace: str,
    timeout: int = 0,
    kubeconfig: str = "",
) -> bool:
    """Wait for all pods in *namespace* to become Ready.

    Uses ``kubectl wait`` with the configured readiness timeout.
    Returns True if all pods are ready, raises DeployError on timeout.
    """
    kubectl = settings.deploy.kubectl_bin
    wait_timeout = timeout or settings.deploy.readiness_timeout

    cmd = [
        kubectl, "wait",
        "--for=condition=Ready",
        "pods", "--all",
        "--namespace", namespace,
        f"--timeout={wait_timeout}s",
    ]
    if kubeconfig or settings.deploy.kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig or settings.deploy.kubeconfig])

    logger.info(f"Waiting for pods in namespace '{namespace}' (timeout={wait_timeout}s)...")

    rc, stdout, stderr = await _run_cmd(cmd, timeout=float(wait_timeout + 30))

    if rc != 0:
        # Check if there are simply no pods yet
        if "no matching resources found" in stderr.lower():
            logger.info(f"No pods found in namespace '{namespace}' — nothing to wait for")
            return True
        raise DeployError(
            f"Pods in namespace '{namespace}' did not become ready within {wait_timeout}s: {stderr}"
        )

    logger.info(f"All pods ready in namespace '{namespace}'")
    return True


async def deploy_project(
    project_id: str,
    kubeconfig: str = "",
    readiness_timeout: int = 0,
    progress: ProgressCallback | None = None,
) -> Deployment:
    """Orchestrate a full project deployment with multi-chart ordering.

    1. Load generated manifests grouped by chart role / deploy order.
    2. Apply PaaS manifests first → wait for readiness.
    3. Apply App manifests → wait for readiness.
    4. Record deployment status.

    Args:
        project_id: The project to deploy.
        kubeconfig: Path to kubeconfig file (optional, uses default if empty).
        readiness_timeout: Seconds to wait for pod readiness (0 = use settings default).
        progress: Optional callback for status messages.

    Returns:
        The Deployment record with final status.
    """

    def _log(msg: str) -> None:
        logger.info(msg)
        if progress:
            progress(msg)

    # Create deployment record
    deployment = await deploy_db.create_deployment(project_id)
    await deploy_db.update_deployment(deployment.id, DeploymentStatus.RUNNING)

    await bus.publish(Event(DEPLOY_STARTED, {
        "deployment_id": deployment.id,
        "project_id": project_id,
    }))

    log_lines: list[str] = []

    try:
        # Load all generated manifests
        manifests = await deploy_db.list_generated_manifests(project_id)
        if not manifests:
            raise DeployError("No generated manifests found. Run generate first.")

        # Group manifests by their prefix (which encodes deploy order):
        #   "00-paas/00-namespace.yaml" → group "00-paas"
        #   "01-app/05-workloads.yaml"  → group "01-app"
        #   For single-artifact projects: "wordpress/05-workloads.yaml" → group "wordpress"
        groups: dict[str, list] = {}
        for m in manifests:
            parts = m.filename.split("/", 1)
            group_key = parts[0] if len(parts) > 1 else "_default"
            groups.setdefault(group_key, []).append(m)

        # Sort groups by key (which starts with "00-", "01-", etc.)
        sorted_groups = sorted(groups.items(), key=lambda kv: kv[0])

        total_groups = len(sorted_groups)
        for idx, (group_name, group_manifests) in enumerate(sorted_groups):
            _log(f"Deploying chart group [{idx + 1}/{total_groups}]: {group_name}")
            log_lines.append(f"--- Deploying: {group_name} ---")

            # Sort manifests within group by filename (00-namespace first, etc.)
            group_manifests.sort(key=lambda m: m.filename)

            # Extract namespace from the namespace manifest if present
            namespace = ""
            for m in group_manifests:
                if "namespace" in m.filename.lower():
                    # Parse namespace from the YAML content
                    import yaml
                    try:
                        doc = yaml.safe_load(m.content)
                        if doc and doc.get("kind") == "Namespace":
                            namespace = doc.get("metadata", {}).get("name", "")
                    except Exception:
                        pass
                    break

            # Apply each manifest in the group
            for m in group_manifests:
                _log(f"  Applying: {m.filename}")
                try:
                    output = await kubectl_apply(m.content, kubeconfig=kubeconfig)
                    log_lines.append(f"  ✓ {m.filename}: {output.strip()}")
                except DeployError as e:
                    log_lines.append(f"  ✗ {m.filename}: {e}")
                    raise

            # Wait for pods to be ready before proceeding to next group
            if namespace and idx < total_groups - 1:
                _log(f"  Waiting for pods in '{namespace}' to become ready...")
                log_lines.append(f"  Waiting for readiness in namespace '{namespace}'...")
                await wait_for_readiness(
                    namespace=namespace,
                    timeout=readiness_timeout,
                    kubeconfig=kubeconfig,
                )
                log_lines.append(f"  ✓ All pods ready in '{namespace}'")

            await bus.publish(Event(CHART_DEPLOYED, {
                "deployment_id": deployment.id,
                "project_id": project_id,
                "group": group_name,
                "group_index": idx,
            }))

        # Wait for readiness in the very last group too
        if namespace:
            _log(f"  Waiting for final pods in '{namespace}' to become ready...")
            await wait_for_readiness(
                namespace=namespace,
                timeout=readiness_timeout,
                kubeconfig=kubeconfig,
            )
            log_lines.append(f"  ✓ All pods ready in '{namespace}'")

        # Success
        _log("Deployment completed successfully")
        log_lines.append("\n=== Deployment succeeded ===")
        await deploy_db.update_deployment(
            deployment.id, DeploymentStatus.SUCCEEDED, log="\n".join(log_lines),
        )

        await bus.publish(Event(DEPLOY_COMPLETED, {
            "deployment_id": deployment.id,
            "project_id": project_id,
        }))

    except Exception as e:
        error_msg = str(e)
        _log(f"Deployment failed: {error_msg}")
        log_lines.append(f"\n=== Deployment FAILED: {error_msg} ===")
        await deploy_db.update_deployment(
            deployment.id, DeploymentStatus.FAILED, log="\n".join(log_lines),
        )

        await bus.publish(Event(DEPLOY_FAILED, {
            "deployment_id": deployment.id,
            "project_id": project_id,
            "error": error_msg,
        }))

    # Refresh from DB
    updated = await deploy_db.get_deployment(deployment.id)
    return updated or deployment
