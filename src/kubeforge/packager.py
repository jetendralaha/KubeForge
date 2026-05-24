"""Offline bundle packager — creates air-gap deployment archives and ISO images.

Produces either:
A) tar.gz bundle (lightweight, no images pulled):
   - Generated K3s manifests
   - Image manifest (list of required images)
   - K3s configuration (registries.yaml)
   - install.sh bootstrap script
   - README with deployment instructions
   - SHA256 checksums

B) Full ISO image (complete on-prem air-gap deployment):
   - Everything in A, plus:
   - Actual container image tarballs (docker save)
   - K3s binary + airgap images tarball
   - K3s install script
   - Fully self-contained — no internet needed on target
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import textwrap
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable

from kubeforge.config import settings
from kubeforge.image_puller import pull_images, compute_image_checksums
from kubeforge.iso_builder import build_iso, compute_iso_checksum
from kubeforge.k3s_downloader import download_all as download_k3s_all, compute_checksums as k3s_checksums

logger = logging.getLogger("kubeforge.packager")

ProgressCallback = Callable[[str, int, int], None]  # (message, current, total)


async def create_bundle(
    project_name: str,
    manifests: dict[str, str],
    images: list[str],
    output_dir: str = "",
    progress: ProgressCallback | None = None,
) -> str:
    """Create an offline deployment bundle.

    Args:
        project_name: Name used for the archive file.
        manifests: {filename: yaml_content} from the generator.
        images: List of image references to include in the manifest.
        output_dir: Override output directory.
        progress: Optional callback for progress updates.

    Returns:
        Path to the created .tar.gz archive.
    """
    out = Path(output_dir or settings.packager.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive_name = f"{project_name}-{timestamp}.tar.gz"
    archive_path = out / archive_name
    prefix = f"{project_name}/"

    total_steps = len(manifests) + 5  # manifests + image list + config + install + readme + checksums
    step = 0

    def _progress(msg: str) -> None:
        nonlocal step
        step += 1
        if progress:
            progress(msg, step, total_steps)
        logger.info(f"[{step}/{total_steps}] {msg}")

    checksums: dict[str, str] = {}

    with tarfile.open(archive_path, "w:gz") as tar:
        # Manifests
        for filename, content in sorted(manifests.items()):
            _progress(f"Adding manifest: {filename}")
            _add_string(tar, f"{prefix}manifests/{filename}", content)
            checksums[f"manifests/{filename}"] = _sha256(content)

        # Image manifest
        _progress("Adding image manifest")
        image_list = "\n".join(sorted(images)) + "\n"
        _add_string(tar, f"{prefix}images.txt", image_list)
        checksums["images.txt"] = _sha256(image_list)

        # K3s registries.yaml
        _progress("Adding registries.yaml")
        registries = _build_registries_yaml()
        _add_string(tar, f"{prefix}registries.yaml", registries)

        # Install script
        _progress("Adding install.sh")
        install_sh = _build_install_script(project_name)
        _add_string(tar, f"{prefix}install.sh", install_sh, executable=True)

        # README
        _progress("Adding README")
        readme = _build_readme(project_name, images)
        _add_string(tar, f"{prefix}README.md", readme)

        # Checksums
        _progress("Adding checksums")
        checksum_content = "\n".join(f"{sha}  {path}" for path, sha in sorted(checksums.items())) + "\n"
        _add_string(tar, f"{prefix}SHA256SUMS", checksum_content)

    size = archive_path.stat().st_size
    logger.info(f"Bundle created: {archive_path} ({size:,} bytes)")
    return str(archive_path)


async def create_iso(
    project_name: str,
    manifests: dict[str, str],
    images: list[str],
    output_dir: str = "",
    pull_container_images: bool = True,
    download_k3s: bool = True,
    target_arch: str = "auto",
    iso_backend: str = "auto",
    progress: ProgressCallback | None = None,
    registry_credentials: list[dict] | None = None,
    auth_file: str = "",
    insecure_registries: list[str] | None = None,
) -> str:
    """Create a complete air-gap ISO image for on-premises deployment.

    This pulls actual container images, downloads K3s binaries, and packages
    everything into a self-contained .iso file that can be mounted/burned
    on the target machine.

    Args:
        project_name: Name used for the ISO file.
        manifests: {filename: yaml_content} from the generator.
        images: List of image references to pull and bundle.
        output_dir: Override output directory.
        pull_container_images: Whether to pull and export container images.
        download_k3s: Whether to download K3s binary and airgap images.
        target_arch: Target architecture ("amd64", "arm64", or "auto").
        iso_backend: ISO tool to use ("xorriso", "genisoimage", "mkisofs", "pycdlib", "auto").
        progress: Optional callback for progress updates.
        registry_credentials: Optional list of {registry, username, password} for private registries.
        auth_file: Optional path to Docker config.json / auth.json for registry auth.

    Returns:
        Path to the created .iso file.
    """
    out = Path(output_dir or settings.packager.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    iso_name = f"{project_name}-{timestamp}.iso"
    iso_path = out / iso_name

    # We build the ISO contents in a temp directory
    with tempfile.TemporaryDirectory(prefix="kubeforge-iso-") as tmp:
        staging = Path(tmp) / project_name
        staging.mkdir()

        total_steps = 2 + len(manifests) + (1 if pull_container_images else 0) + (1 if download_k3s else 0) + 3
        step = 0

        def _progress(msg: str) -> None:
            nonlocal step
            step += 1
            if progress:
                progress(msg, step, total_steps)
            logger.info(f"[{step}/{total_steps}] {msg}")

        # 1. Write manifests
        manifests_dir = staging / "manifests"
        manifests_dir.mkdir()
        for filename, content in sorted(manifests.items()):
            _progress(f"Writing manifest: {filename}")
            manifest_path = manifests_dir / filename
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(content, encoding="utf-8")

        # 2. Write image list
        _progress("Writing image manifest")
        (staging / "images.txt").write_text("\n".join(sorted(images)) + "\n", encoding="utf-8")

        # 3. Pull container images
        if pull_container_images and images:
            _progress(f"Pulling {len(images)} container images (this may take a while)...")
            images_dir = staging / "images"
            await pull_images(
                images, images_dir,
                backend=settings.packager.image_pull_backend,
                progress=progress,
                max_concurrent=settings.packager.max_concurrent_pulls,
                credentials=registry_credentials,
                auth_file=auth_file,
                insecure_registries=insecure_registries,
            )

        # 4. Download K3s
        if download_k3s:
            _progress("Downloading K3s binaries and airgap images...")
            k3s_dir = staging / "k3s"
            await download_k3s_all(k3s_dir, arch=target_arch, progress=progress)

        # 5. Write registries.yaml
        _progress("Writing registries.yaml")
        (staging / "registries.yaml").write_text(_build_registries_yaml(), encoding="utf-8")

        # 6. Write install script (enhanced for ISO)
        _progress("Writing install.sh")
        install_content = _build_iso_install_script(project_name)
        install_path = staging / "install.sh"
        install_path.write_text(install_content, encoding="utf-8")
        install_path.chmod(0o755)

        # 7. Write README
        _progress("Writing README")
        readme_content = _build_iso_readme(project_name, images)
        (staging / "README.md").write_text(readme_content, encoding="utf-8")

        # 8. Compute & write checksums
        _progress("Computing checksums")
        checksums: dict[str, str] = {}
        for f in staging.rglob("*"):
            if f.is_file() and f.name != "SHA256SUMS":
                rel = f.relative_to(staging)
                h = hashlib.sha256(f.read_bytes()).hexdigest()
                checksums[str(rel)] = h
        checksum_content = "\n".join(f"{sha}  {path}" for path, sha in sorted(checksums.items())) + "\n"
        (staging / "SHA256SUMS").write_text(checksum_content, encoding="utf-8")

        # 9. Build ISO
        _progress("Building ISO image...")
        await build_iso(staging, iso_path, volume_label=f"KUBEFORGE_{project_name.upper()[:20]}", backend=iso_backend)

    size = iso_path.stat().st_size
    checksum = compute_iso_checksum(iso_path)

    # Write companion checksum file
    (out / f"{iso_name}.sha256").write_text(f"{checksum}  {iso_name}\n", encoding="utf-8")

    logger.info(f"ISO created: {iso_path} ({size:,} bytes / {size / 1024 / 1024:.1f} MB)")
    logger.info(f"SHA256: {checksum}")
    return str(iso_path)


def _add_string(tar: tarfile.TarFile, name: str, content: str, executable: bool = False) -> None:
    """Add a string as a file in the tar archive."""
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    info.mode = 0o755 if executable else 0o644
    tar.addfile(info, BytesIO(data))

def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_registries_yaml() -> str:
    return textwrap.dedent("""\
        # K3s private registry configuration
        # Place at: /etc/rancher/k3s/registries.yaml
        mirrors:
          "docker.io":
            endpoint:
              - "https://registry.local:5000"
          "registry.local:5000":
            endpoint:
              - "https://registry.local:5000"
        configs:
          "registry.local:5000":
            tls:
              insecure_skip_verify: true
    """)


def _build_install_script(project_name: str) -> str:
    return textwrap.dedent(f"""\
        #!/bin/bash
        set -euo pipefail

        echo "=========================================="
        echo " KubeForge Air-Gap Installer"
        echo " Project: {project_name}"
        echo "=========================================="

        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        K3S_VERSION="{settings.packager.k3s_version}"
        NAMESPACE="{project_name}"

        # ── Pre-flight checks ─────────────────────────
        check_root() {{
            if [ "$(id -u)" -ne 0 ]; then
                echo "ERROR: This script must be run as root"
                exit 1
            fi
        }}

        check_k3s() {{
            if ! command -v k3s &>/dev/null; then
                echo "K3s not found. Install K3s first:"
                echo "  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=$K3S_VERSION sh -"
                exit 1
            fi
            echo "K3s version: $(k3s --version | head -1)"
        }}

        # ── Load images ───────────────────────────────
        load_images() {{
            echo ""
            echo "Loading container images..."
            if [ -d "$SCRIPT_DIR/images/" ]; then
                for img in "$SCRIPT_DIR"/images/*.tar; do
                    echo "  Loading: $(basename "$img")"
                    k3s ctr images import "$img"
                done
            else
                echo "  No image tarballs found in images/"
                echo "  Pull images manually from images.txt"
            fi
        }}

        # ── Configure registry ────────────────────────
        setup_registry() {{
            echo ""
            echo "Configuring private registry..."
            mkdir -p /etc/rancher/k3s/
            if [ -f "$SCRIPT_DIR/registries.yaml" ]; then
                cp "$SCRIPT_DIR/registries.yaml" /etc/rancher/k3s/registries.yaml
                echo "  Copied registries.yaml"
            fi
        }}

        # ── Apply manifests ───────────────────────────
        apply_manifests() {{
            echo ""
            echo "Applying Kubernetes manifests..."
            for manifest in "$SCRIPT_DIR"/manifests/*.yaml; do
                echo "  Applying: $(basename "$manifest")"
                k3s kubectl apply -f "$manifest"
            done
        }}

        # ── Verify deployment ─────────────────────────
        verify() {{
            echo ""
            echo "Waiting for pods to be ready..."
            k3s kubectl -n "$NAMESPACE" wait --for=condition=ready pod --all --timeout=300s || true
            echo ""
            echo "Deployment status:"
            k3s kubectl -n "$NAMESPACE" get pods
            echo ""
            k3s kubectl -n "$NAMESPACE" get svc
        }}

        # ── Main ──────────────────────────────────────
        main() {{
            check_root
            check_k3s
            setup_registry
            load_images
            apply_manifests
            verify

            echo ""
            echo "=========================================="
            echo " Deployment complete!"
            echo "=========================================="
        }}

        main "$@"
    """)


def _build_readme(project_name: str, images: list[str]) -> str:
    image_list = "\n".join(f"  - {img}" for img in sorted(images))
    return textwrap.dedent(f"""\
        # {project_name} — Air-Gap Deployment Bundle

        Generated by KubeForge.

        ## Quick Start

        1. Copy this bundle to your target machine
        2. Extract: `tar xzf {project_name}-*.tar.gz`
        3. Run: `sudo ./install.sh`

        ## Prerequisites

        - K3s {settings.packager.k3s_version} installed
        - Longhorn storage driver (for persistent volumes)

        ## Container Images

        The following images are required:

        {image_list}

        Pre-pull these images or place tarballs in the `images/` directory.

        ## Files

        | File | Description |
        |------|-------------|
        | `manifests/` | Kubernetes YAML manifests |
        | `images.txt` | Required container image list |
        | `registries.yaml` | K3s private registry config |
        | `install.sh` | Automated install script |
        | `SHA256SUMS` | File integrity checksums |
    """)


def _build_iso_install_script(project_name: str) -> str:
    """Build the enhanced install script for fully self-contained ISO deployment."""
    return textwrap.dedent(f"""\
        #!/bin/bash
        set -euo pipefail

        echo "=========================================="
        echo " KubeForge ISO Air-Gap Installer"
        echo " Project: {project_name}"
        echo " K3s: {settings.packager.k3s_version}"
        echo "=========================================="

        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        K3S_VERSION="{settings.packager.k3s_version}"
        NAMESPACE="{project_name}"

        # ── Pre-flight checks ─────────────────────────
        check_root() {{
            if [ "$(id -u)" -ne 0 ]; then
                echo "ERROR: This script must be run as root"
                exit 1
            fi
        }}

        # ── Verify checksums ──────────────────────────
        verify_checksums() {{
            echo ""
            echo "Verifying file integrity..."
            if [ -f "$SCRIPT_DIR/SHA256SUMS" ]; then
                cd "$SCRIPT_DIR"
                if sha256sum --check SHA256SUMS --quiet 2>/dev/null; then
                    echo "  ✓ All checksums verified"
                else
                    echo "  ⚠ Checksum verification failed (some files may be corrupted)"
                    read -p "  Continue anyway? [y/N] " -n 1 -r
                    echo
                    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
                fi
                cd - >/dev/null
            fi
        }}

        # ── Install K3s ──────────────────────────────
        install_k3s() {{
            echo ""
            if command -v k3s &>/dev/null; then
                echo "K3s already installed: $(k3s --version | head -1)"
                return 0
            fi

            echo "Installing K3s {settings.packager.k3s_version}..."

            # Use bundled binary if available
            if [ -f "$SCRIPT_DIR/k3s/k3s" ]; then
                echo "  Installing K3s binary from bundle..."
                cp "$SCRIPT_DIR/k3s/k3s" /usr/local/bin/k3s
                chmod +x /usr/local/bin/k3s

                # Use bundled install script for service setup
                if [ -f "$SCRIPT_DIR/k3s/k3s-install.sh" ]; then
                    INSTALL_K3S_SKIP_DOWNLOAD=true \\
                    INSTALL_K3S_VERSION="$K3S_VERSION" \\
                        bash "$SCRIPT_DIR/k3s/k3s-install.sh"
                fi
            else
                echo "  ERROR: K3s binary not found in bundle."
                echo "  Download manually: curl -sfL https://get.k3s.io | sh -"
                exit 1
            fi

            # Wait for K3s to be ready
            echo "  Waiting for K3s to start..."
            sleep 10
            k3s kubectl wait --for=condition=ready node --all --timeout=120s || true
            echo "  ✓ K3s installed and running"
        }}

        # ── Load airgap images ────────────────────────
        load_airgap_images() {{
            echo ""
            echo "Loading K3s airgap images..."

            # K3s system images
            K3S_IMAGES_DIR="/var/lib/rancher/k3s/agent/images/"
            mkdir -p "$K3S_IMAGES_DIR"

            for f in "$SCRIPT_DIR"/k3s/k3s-airgap-images-*; do
                if [ -f "$f" ]; then
                    echo "  Copying: $(basename "$f") → $K3S_IMAGES_DIR"
                    cp "$f" "$K3S_IMAGES_DIR/"
                fi
            done
        }}

        # ── Load container images ─────────────────────
        load_app_images() {{
            echo ""
            echo "Loading application container images..."
            if [ -d "$SCRIPT_DIR/images/" ]; then
                for img in "$SCRIPT_DIR"/images/*.tar; do
                    if [ -f "$img" ]; then
                        echo "  Importing: $(basename "$img")"
                        k3s ctr images import "$img" 2>/dev/null || true
                    fi
                done
                echo "  ✓ Container images loaded"
            else
                echo "  ⚠ No image tarballs found in images/"
                echo "  Pull images from images.txt manually"
            fi
        }}

        # ── Configure registry ────────────────────────
        setup_registry() {{
            echo ""
            echo "Configuring private registry..."
            mkdir -p /etc/rancher/k3s/
            if [ -f "$SCRIPT_DIR/registries.yaml" ]; then
                cp "$SCRIPT_DIR/registries.yaml" /etc/rancher/k3s/registries.yaml
                echo "  ✓ Registry configured"
            fi
        }}

        # ── Apply manifests ───────────────────────────
        apply_manifests() {{
            echo ""
            echo "Applying Kubernetes manifests..."
            for manifest in "$SCRIPT_DIR"/manifests/*.yaml; do
                if [ -f "$manifest" ]; then
                    echo "  Applying: $(basename "$manifest")"
                    k3s kubectl apply -f "$manifest"
                fi
            done
            echo "  ✓ Manifests applied"
        }}

        # ── Verify deployment ─────────────────────────
        verify() {{
            echo ""
            echo "Waiting for pods to be ready (timeout: 5m)..."
            k3s kubectl -n "$NAMESPACE" wait --for=condition=ready pod --all --timeout=300s 2>/dev/null || true
            echo ""
            echo "── Deployment Status ─────────────────────"
            k3s kubectl -n "$NAMESPACE" get pods -o wide 2>/dev/null || true
            echo ""
            k3s kubectl -n "$NAMESPACE" get svc 2>/dev/null || true
        }}

        # ── Main ──────────────────────────────────────
        main() {{
            check_root
            verify_checksums
            install_k3s
            load_airgap_images
            setup_registry
            load_app_images

            # Restart K3s to pick up airgap images and registry config
            echo ""
            echo "Restarting K3s to apply configuration..."
            systemctl restart k3s || true
            sleep 10

            apply_manifests
            verify

            echo ""
            echo "=========================================="
            echo " ✓ Deployment complete!"
            echo ""
            echo " Project: {project_name}"
            echo " Namespace: $NAMESPACE"
            echo "=========================================="
        }}

        main "$@"
    """)


def _build_iso_readme(project_name: str, images: list[str]) -> str:
    """Build README for the ISO deployment."""
    image_list = "\n".join(f"  - {img}" for img in sorted(images))
    return textwrap.dedent(f"""\
        # {project_name} — Air-Gap ISO Deployment

        Generated by KubeForge. This ISO contains everything needed to deploy
        {project_name} on an air-gapped on-premises server with no internet access.

        ## Quick Start

        1. Mount or burn this ISO on the target machine:
           ```
           mount -o loop {project_name}-*.iso /mnt/kubeforge
           ```

        2. Run the installer:
           ```
           cd /mnt/kubeforge/{project_name}
           sudo ./install.sh
           ```

        ## What's Included

        | Directory/File | Description |
        |---------------|-------------|
        | `k3s/` | K3s binary + airgap images (v{settings.packager.k3s_version}) |
        | `images/` | Application container images (pre-pulled) |
        | `manifests/` | Production-ready Kubernetes manifests |
        | `registries.yaml` | K3s private registry configuration |
        | `install.sh` | Automated deployment script |
        | `images.txt` | Full list of required container images |
        | `SHA256SUMS` | File integrity checksums |

        ## Prerequisites

        - Linux server (x86_64 or arm64)
        - Minimum 4 GB RAM, 2 CPUs
        - Storage: ~20 GB free (varies by application)
        - No internet required

        ## Container Images Bundled

        {image_list}

        ## Manual Installation

        If the automated script fails, you can install manually:

        ```bash
        # 1. Install K3s binary
        cp k3s/k3s /usr/local/bin/ && chmod +x /usr/local/bin/k3s

        # 2. Place airgap images
        mkdir -p /var/lib/rancher/k3s/agent/images/
        cp k3s/k3s-airgap-images-*.tar.* /var/lib/rancher/k3s/agent/images/

        # 3. Install K3s service
        INSTALL_K3S_SKIP_DOWNLOAD=true bash k3s/k3s-install.sh

        # 4. Import application images
        for img in images/*.tar; do k3s ctr images import "$img"; done

        # 5. Apply manifests
        for f in manifests/*.yaml; do k3s kubectl apply -f "$f"; done
        ```

        ## Verification

        ```bash
        k3s kubectl -n {project_name} get pods
        k3s kubectl -n {project_name} get svc
        ```
    """)
