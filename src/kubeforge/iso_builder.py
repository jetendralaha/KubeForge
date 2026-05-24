"""ISO builder — creates bootable/mountable ISO images for air-gap on-premises deployment.

Produces an ISO 9660 image containing:
- K3s binary + airgap images + install script
- Container image tarballs
- Generated Kubernetes manifests
- Automated installer script
- Registry configuration
- Checksums & documentation

Supports multiple backends:
- xorriso (preferred, most widely available)
- genisoimage (Debian/Ubuntu)
- mkisofs (legacy)
- Pure Python fallback using pycdlib (no external tools required)
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("kubeforge.iso_builder")

ProgressCallback = Callable[[str, int, int], None]


class ISOBuildError(Exception):
    """Raised when ISO creation fails."""


def _detect_iso_tool() -> str | None:
    """Detect available ISO creation tool."""
    for tool in ("xorriso", "genisoimage", "mkisofs"):
        if shutil.which(tool):
            return tool
    return None


async def _build_with_xorriso(source_dir: Path, output_path: Path, volume_label: str) -> Path:
    """Build ISO using xorriso."""
    import asyncio

    cmd = [
        "xorriso",
        "-as", "mkisofs",
        "-R",                          # Rock Ridge extensions (preserves permissions)
        "-J",                          # Joliet extensions (Windows compatibility)
        "-V", volume_label[:32],       # Volume label (max 32 chars)
        "-o", str(output_path),
        str(source_dir),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise ISOBuildError(f"xorriso failed: {stderr.decode()}")

    return output_path


async def _build_with_genisoimage(source_dir: Path, output_path: Path, volume_label: str) -> Path:
    """Build ISO using genisoimage."""
    import asyncio

    cmd = [
        "genisoimage",
        "-R",
        "-J",
        "-V", volume_label[:32],
        "-o", str(output_path),
        str(source_dir),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise ISOBuildError(f"genisoimage failed: {stderr.decode()}")

    return output_path


async def _build_with_mkisofs(source_dir: Path, output_path: Path, volume_label: str) -> Path:
    """Build ISO using mkisofs."""
    import asyncio

    cmd = [
        "mkisofs",
        "-R",
        "-J",
        "-V", volume_label[:32],
        "-o", str(output_path),
        str(source_dir),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise ISOBuildError(f"mkisofs failed: {stderr.decode()}")

    return output_path


async def _build_with_pycdlib(source_dir: Path, output_path: Path, volume_label: str) -> Path:
    """Build ISO using pure Python pycdlib library (no external tools needed)."""
    try:
        import pycdlib
    except ImportError:
        raise ISOBuildError(
            "No ISO tool found. Install one of: xorriso, genisoimage, mkisofs, "
            "or install pycdlib: pip install pycdlib"
        )

    iso = pycdlib.PyCdlib()
    iso.new(
        interchange_level=3,
        joliet=3,
        rock_ridge="1.09",
        vol_ident=volume_label[:32],
    )

    def _add_directory_recursive(local_path: Path, iso_path: str, joliet_path: str, rr_name: str) -> None:
        """Recursively add directories and files to the ISO."""
        iso.add_directory(
            iso_path=iso_path,
            joliet_path=joliet_path,
            rr_name=rr_name,
        )

        for item in sorted(local_path.iterdir()):
            # compute ISO/Joliet paths for each item (used below when adding files/dirs)
            item_joliet = f"{joliet_path}/{item.name}"
            if item.is_file():
                iso.add_file(
                    str(item),
                    iso_path=f"{iso_path}/{item.name.upper().replace('.', '_')[:12]}.;1",
                    joliet_path=item_joliet,
                    rr_name=item.name,
                )
            elif item.is_dir():
                _add_directory_recursive(
                    item,
                    f"{iso_path}/{item.name.upper()[:8]}",
                    item_joliet,
                    item.name,
                )

    # Add top-level contents
    for item in sorted(source_dir.iterdir()):
        if item.is_file():
            iso.add_file(
                str(item),
                iso_path=f"/{item.name.upper().replace('.', '_')[:12]}.;1",
                joliet_path=f"/{item.name}",
                rr_name=item.name,
            )
        elif item.is_dir():
            _add_directory_recursive(
                item,
                f"/{item.name.upper()[:8]}",
                f"/{item.name}",
                item.name,
            )

    iso.write(str(output_path))
    iso.close()

    return output_path


# ── Public API ──────────────────────────────────────────────────────


async def build_iso(
    source_dir: Path,
    output_path: Path,
    volume_label: str = "KUBEFORGE",
    backend: str = "auto",
) -> Path:
    """Build an ISO image from a directory.

    Args:
        source_dir: Directory containing all files to include in the ISO.
        output_path: Where to write the .iso file.
        volume_label: ISO volume label (max 32 chars).
        backend: "xorriso", "genisoimage", "mkisofs", "pycdlib", or "auto".

    Returns:
        Path to the created .iso file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if backend == "auto":
        tool = _detect_iso_tool()
        if tool:
            backend = tool
        else:
            backend = "pycdlib"

    builders = {
        "xorriso": _build_with_xorriso,
        "genisoimage": _build_with_genisoimage,
        "mkisofs": _build_with_mkisofs,
        "pycdlib": _build_with_pycdlib,
    }

    builder = builders.get(backend)
    if not builder:
        raise ISOBuildError(f"Unknown ISO backend: {backend}")

    logger.info(f"Building ISO with {backend}: {output_path}")
    result = await builder(source_dir, output_path, volume_label)

    size = result.stat().st_size
    logger.info(f"ISO created: {result} ({size:,} bytes / {size / 1024 / 1024:.1f} MB)")
    return result


def compute_iso_checksum(iso_path: Path) -> str:
    """Compute SHA256 checksum for the ISO file."""
    h = hashlib.sha256()
    with open(iso_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()
