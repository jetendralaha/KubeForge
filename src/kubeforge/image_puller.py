"""Container image puller — exports images as tarballs for air-gap deployment.

Supports two backends:
- Docker CLI (`docker save`)
- Skopeo (`skopeo copy`) for environments without Docker daemon

The puller exports each image to a .tar file that can be imported via
`k3s ctr images import` or `docker load` on the target machine.

Registry authentication is supported via:
- Docker config.json / containers auth.json (--authfile)
- Inline credentials per registry
- Insecure registry (skip TLS) configuration
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from kubeforge.config import settings

logger = logging.getLogger("kubeforge.image_puller")

ProgressCallback = Callable[[str, int, int], None]


class ImagePullError(Exception):
    """Raised when image pull/export fails."""


def _sanitize_filename(reference: str) -> str:
    """Convert an image reference to a safe filename."""
    return reference.replace("/", "_").replace(":", "_").replace("@", "_") + ".tar"


def _detect_backend() -> str:
    """Detect available container tool: 'docker', 'skopeo', or 'none'."""
    if shutil.which("docker"):
        return "docker"
    if shutil.which("skopeo"):
        return "skopeo"
    return "none"


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
        raise ImagePullError(f"Command timed out after {timeout}s: {' '.join(cmd)}")
    return proc.returncode or 0, stdout.decode(), stderr.decode()


# ── Registry authentication ─────────────────────────────────────────


async def authenticate_registries(
    credentials: list[dict] | None = None,
    auth_file: str = "",
    backend: str = "docker",
) -> str:
    """Authenticate with private registries before pulling.

    For Docker backend: runs ``docker login`` per credential.
    For Skopeo backend: builds a temporary auth.json file.

    Args:
        credentials: List of ``{registry, username, password}`` dicts.
        auth_file: Path to an existing Docker-compatible config.json.
        backend: "docker" or "skopeo".

    Returns:
        Path to the auth file for Skopeo, or "" for Docker (which stores
        credentials in its own config).
    """
    creds = credentials or []
    existing_auth = auth_file or settings.registry.auth_file

    if backend == "docker":
        # Docker: run docker login for each credential
        for cred in creds:
            registry = cred.get("registry", "")
            username = cred.get("username", "")
            password = cred.get("password", "")
            if not all([registry, username, password]):
                continue
            logger.info(f"Authenticating with Docker registry: {registry}")
            rc, _, stderr = await _run_cmd([
                "docker", "login",
                "--username", username,
                "--password-stdin",
                registry,
            ])
            # docker login --password-stdin reads from stdin, but _run_cmd doesn't
            # support stdin.  Use --password instead (less secure but functional).
            rc, _, stderr = await _run_cmd([
                "docker", "login",
                "-u", username,
                "-p", password,
                registry,
            ])
            if rc != 0:
                logger.warning(f"docker login failed for {registry}: {stderr}")
        return ""

    elif backend == "skopeo":
        # Skopeo: build auth.json (Docker config format)
        auth_data: dict = {"auths": {}}

        # Load existing auth file if provided
        if existing_auth and Path(existing_auth).is_file():
            try:
                auth_data = json.loads(Path(existing_auth).read_text())
            except Exception as e:
                logger.warning(f"Failed to load auth file {existing_auth}: {e}")

        # Merge additional credentials
        import base64
        for cred in creds:
            registry = cred.get("registry", "")
            username = cred.get("username", "")
            password = cred.get("password", "")
            if not all([registry, username, password]):
                continue
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth_data.setdefault("auths", {})[registry] = {"auth": token}

        if auth_data.get("auths"):
            # Write to a temporary auth file
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="kubeforge-auth-", delete=False,
            )
            json.dump(auth_data, tmp)
            tmp.close()
            logger.info(f"Wrote Skopeo auth file: {tmp.name}")
            return tmp.name

        # If an existing auth file was provided without extra creds, use it directly
        if existing_auth:
            return existing_auth

    return ""


def _registry_for_reference(reference: str) -> str:
    """Extract the registry host from an image reference."""
    # docker.io/library/nginx:latest → docker.io
    # ghcr.io/org/repo:tag → ghcr.io
    parts = reference.split("/")
    if len(parts) >= 2 and ("." in parts[0] or ":" in parts[0]):
        return parts[0]
    return "docker.io"


def _is_insecure_registry(reference: str, extra: list[str] | None = None) -> bool:
    """Check if the image's registry is in the insecure list."""
    registry = _registry_for_reference(reference)
    insecure = set(settings.registry.insecure_registries)
    if extra:
        insecure.update(extra)
    return registry in insecure


async def pull_image_docker(reference: str, output_path: Path) -> Path:
    """Pull and save an image using Docker CLI."""
    # Pull the image first
    rc, _, stderr = await _run_cmd(["docker", "pull", reference])
    if rc != 0:
        raise ImagePullError(f"docker pull failed for {reference}: {stderr}")

    # Save to tar
    rc, _, stderr = await _run_cmd(["docker", "save", "-o", str(output_path), reference])
    if rc != 0:
        raise ImagePullError(f"docker save failed for {reference}: {stderr}")

    return output_path


async def pull_image_skopeo(
    reference: str,
    output_path: Path,
    auth_file: str = "",
    tls_verify: bool = True,
) -> Path:
    """Pull and save an image using skopeo."""
    src = f"docker://{reference}"
    dest = f"docker-archive:{output_path}"

    cmd = ["skopeo", "copy"]

    # Auth file
    if auth_file:
        cmd.extend(["--authfile", auth_file])

    # TLS verification
    if not tls_verify or _is_insecure_registry(reference):
        cmd.append("--src-tls-verify=false")

    cmd.extend([src, dest])

    rc, _, stderr = await _run_cmd(cmd)
    if rc != 0:
        raise ImagePullError(f"skopeo copy failed for {reference}: {stderr}")

    return output_path


async def pull_images(
    image_refs: list[str],
    output_dir: Path,
    backend: str = "auto",
    progress: ProgressCallback | None = None,
    max_concurrent: int = 0,
    credentials: list[dict] | None = None,
    auth_file: str = "",
    insecure_registries: list[str] | None = None,
) -> list[Path]:
    """Pull all images and save as tarballs.

    Args:
        image_refs: List of image references (e.g., "nginx:1.25", "postgres:16-alpine")
        output_dir: Directory to write .tar files
        backend: "docker", "skopeo", or "auto" (detect)
        progress: Optional progress callback
        max_concurrent: Max parallel pulls (0 = use settings default)
        credentials: Optional list of {registry, username, password} for auth
        auth_file: Optional path to Docker config.json / auth.json

    Returns:
        List of paths to created .tar files
    """
    if backend == "auto":
        backend = _detect_backend()

    if backend == "none":
        logger.warning("No container tool found (docker/skopeo). Skipping image pull.")
        return []

    # Use settings defaults if not explicitly provided
    if max_concurrent <= 0:
        max_concurrent = settings.packager.max_concurrent_pulls

    output_dir.mkdir(parents=True, exist_ok=True)

    # Authenticate before pulling
    skopeo_auth_file = ""
    if credentials or auth_file or settings.registry.auth_file:
        skopeo_auth_file = await authenticate_registries(
            credentials=credentials,
            auth_file=auth_file,
            backend=backend,
        )

    tls_verify = settings.registry.tls_verify

    sem = asyncio.Semaphore(max_concurrent)
    total = len(image_refs)
    results: list[Path] = []
    errors: list[str] = []

    async def _pull_one(idx: int, ref: str) -> Path | None:
        async with sem:
            filename = _sanitize_filename(ref)
            dest = output_dir / filename
            if progress:
                progress(f"Pulling image: {ref}", idx + 1, total)
            logger.info(f"[{idx + 1}/{total}] Pulling {ref} → {filename}")
            try:
                ref_tls = tls_verify and not _is_insecure_registry(ref, insecure_registries)
                if backend == "docker":
                    path = await pull_image_docker(ref, dest)
                else:
                    path = await pull_image_skopeo(
                        ref, dest,
                        auth_file=skopeo_auth_file,
                        tls_verify=ref_tls,
                    )
                return path
            except ImagePullError as e:
                logger.error(str(e))
                errors.append(str(e))
                return None

    tasks = [_pull_one(i, ref) for i, ref in enumerate(image_refs)]
    paths = await asyncio.gather(*tasks)

    results = [p for p in paths if p is not None]

    # Clean up temporary auth file
    if skopeo_auth_file and skopeo_auth_file != (auth_file or settings.registry.auth_file):
        try:
            Path(skopeo_auth_file).unlink(missing_ok=True)
        except Exception:
            pass

    if errors:
        logger.warning(f"{len(errors)} image(s) failed to pull. Bundle will be incomplete.")

    logger.info(f"Pulled {len(results)}/{total} images to {output_dir}")
    return results


def compute_image_checksums(image_dir: Path) -> dict[str, str]:
    """Compute SHA256 checksums for all image tarballs."""
    checksums: dict[str, str] = {}
    for tar_file in sorted(image_dir.glob("*.tar")):
        h = hashlib.sha256()
        with open(tar_file, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        checksums[tar_file.name] = h.hexdigest()
    return checksums
