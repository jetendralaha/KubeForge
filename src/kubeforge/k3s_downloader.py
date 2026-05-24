"""K3s binary downloader — fetches K3s binaries and install script for offline deployment.

Downloads:
- K3s server binary (linux/amd64 or linux/arm64)
- K3s airgap images tarball
- k3s-install.sh bootstrap script

These are placed in the bundle so the target machine can install K3s
without internet access.
"""

from __future__ import annotations

import hashlib
import logging
import platform
from collections.abc import Callable
from pathlib import Path

import httpx

from kubeforge.config import settings

logger = logging.getLogger("kubeforge.k3s_downloader")

ProgressCallback = Callable[[str, int, int], None]

# GitHub release base for K3s
K3S_RELEASE_BASE = "https://github.com/k3s-io/k3s/releases/download"
K3S_INSTALL_SCRIPT = "https://get.k3s.io"

# Architecture mapping
ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


class K3sDownloadError(Exception):
    """Raised when K3s download fails."""


def _get_arch(target_arch: str = "auto") -> str:
    """Resolve target architecture."""
    if target_arch != "auto":
        return target_arch
    machine = platform.machine().lower()
    return ARCH_MAP.get(machine, "amd64")


def _k3s_binary_url(version: str, arch: str) -> str:
    """Get K3s binary download URL."""
    suffix = "" if arch == "amd64" else f"-{arch}"
    return f"{K3S_RELEASE_BASE}/{version}/k3s{suffix}"


def _k3s_airgap_url(version: str, arch: str) -> str:
    """Get K3s airgap images tarball URL."""
    return f"{K3S_RELEASE_BASE}/{version}/k3s-airgap-images-{arch}.tar.zst"


def _k3s_checksums_url(version: str) -> str:
    """Get K3s SHA256 checksums URL."""
    return f"{K3S_RELEASE_BASE}/{version}/sha256sum-{_get_arch()}.txt"


async def _download_file(
    url: str,
    dest: Path,
    client: httpx.AsyncClient,
    description: str = "",
) -> Path:
    """Download a file with streaming."""
    logger.info(f"Downloading {description or url} → {dest.name}")
    try:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise K3sDownloadError(f"Failed to download {url}: {e}")

    size = dest.stat().st_size
    logger.info(f"Downloaded {dest.name} ({size:,} bytes)")
    return dest


async def download_k3s_binary(
    output_dir: Path,
    version: str = "",
    arch: str = "auto",
) -> Path:
    """Download the K3s server binary.

    Args:
        output_dir: Directory to save the binary.
        version: K3s version (defaults to config).
        arch: Target architecture ("amd64", "arm64", or "auto").

    Returns:
        Path to the downloaded binary.
    """
    version = version or settings.packager.k3s_version
    arch = _get_arch(arch)
    url = _k3s_binary_url(version, arch)
    dest = output_dir / "k3s"

    async with httpx.AsyncClient(timeout=300.0) as client:
        await _download_file(url, dest, client, f"K3s {version} binary ({arch})")

    # Make executable
    dest.chmod(0o755)
    return dest


async def download_k3s_airgap_images(
    output_dir: Path,
    version: str = "",
    arch: str = "auto",
) -> Path | None:
    """Download the K3s airgap images tarball.

    Args:
        output_dir: Directory to save the tarball.
        version: K3s version (defaults to config).
        arch: Target architecture.

    Returns:
        Path to the downloaded tarball, or None if unavailable.
    """
    version = version or settings.packager.k3s_version
    arch = _get_arch(arch)
    url = _k3s_airgap_url(version, arch)
    dest = output_dir / f"k3s-airgap-images-{arch}.tar.zst"

    async with httpx.AsyncClient(timeout=600.0) as client:
        try:
            await _download_file(url, dest, client, f"K3s airgap images ({arch})")
            return dest
        except K3sDownloadError:
            # Try .tar.gz fallback (older K3s versions)
            url_gz = url.replace(".tar.zst", ".tar.gz")
            dest_gz = output_dir / f"k3s-airgap-images-{arch}.tar.gz"
            try:
                await _download_file(url_gz, dest_gz, client, f"K3s airgap images .tar.gz ({arch})")
                return dest_gz
            except K3sDownloadError:
                logger.warning("K3s airgap images not available for download")
                return None


async def download_k3s_install_script(output_dir: Path) -> Path:
    """Download the official K3s install script.

    Returns:
        Path to the install script.
    """
    dest = output_dir / "k3s-install.sh"
    async with httpx.AsyncClient(timeout=60.0) as client:
        await _download_file(K3S_INSTALL_SCRIPT, dest, client, "K3s install script")

    dest.chmod(0o755)
    return dest


async def download_all(
    output_dir: Path,
    version: str = "",
    arch: str = "auto",
    progress: ProgressCallback | None = None,
) -> dict[str, Path | None]:
    """Download all K3s components for offline installation.

    Args:
        output_dir: Target directory for downloads.
        version: K3s version string.
        arch: Target architecture.
        progress: Optional progress callback.

    Returns:
        Dict with paths: {"binary": Path, "airgap_images": Path|None, "install_script": Path}
    """
    version = version or settings.packager.k3s_version
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 3
    step = 0

    def _progress(msg: str) -> None:
        nonlocal step
        step += 1
        if progress:
            progress(msg, step, total)

    _progress(f"Downloading K3s binary {version}")
    binary = await download_k3s_binary(output_dir, version, arch)

    _progress("Downloading K3s airgap images")
    airgap = await download_k3s_airgap_images(output_dir, version, arch)

    _progress("Downloading K3s install script")
    install = await download_k3s_install_script(output_dir)

    return {
        "binary": binary,
        "airgap_images": airgap,
        "install_script": install,
    }


def compute_checksums(k3s_dir: Path) -> dict[str, str]:
    """Compute SHA256 checksums for all downloaded K3s files."""
    checksums: dict[str, str] = {}
    for f in sorted(k3s_dir.iterdir()):
        if f.is_file():
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                while chunk := fh.read(8192):
                    h.update(chunk)
            checksums[f.name] = h.hexdigest()
    return checksums
