"""Bootable ISO builder -- creates a self-contained bootable Debian-based ISO.

This produces a hybrid bootable ISO (amd64 only) that:
1. Boots into a minimal Debian 12 (Bookworm) live environment
2. Automatically installs K3s from the bundled binary
3. Loads pre-pulled container images
4. Applies Kubernetes manifests
5. Results in a fully running K3s cluster on first boot

Build machine prerequisites:
    sudo apt install -y debootstrap squashfs-tools xorriso isolinux syslinux-common

The resulting ISO boots completely offline (air-gap safe).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
from datetime import UTC
from pathlib import Path

from kubeforge.config import settings
from kubeforge.iso_builder import ISOBuildError

logger = logging.getLogger("kubeforge.bootable_iso")


def _is_root() -> bool:
    """Check if running as root. Returns False on Windows."""
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


# Debian version configuration
DEBIAN_RELEASE = "bookworm"  # Debian 12
DEBIAN_MIRROR = "http://deb.debian.org/debian"
DEBIAN_PACKAGES = [
    "systemd",
    "systemd-sysv",
    "init",
    "iproute2",
    "isc-dhcp-client",
    "iputils-ping",
    "curl",
    "ca-certificates",
    "openssh-server",
    "sudo",
    "procps",
    "kmod",
    "udev",
    "dbus",
    "iptables",
    "util-linux",
    "bash",
    "coreutils",
    "mount",
    "findutils",
    "grep",
    "sed",
    "gawk",
    "tar",
    "gzip",
    "xz-utils",
    "less",
    "nano",
    "e2fsprogs",
    "linux-image-amd64",
    "live-boot",
    "live-boot-initramfs-tools",
]


def _check_build_prerequisites() -> None:
    """Check that all required tools are available on the build machine."""
    missing = []

    if not shutil.which("debootstrap"):
        missing.append("debootstrap (sudo apt install -y debootstrap)")

    if not shutil.which("mksquashfs"):
        missing.append("squashfs-tools (sudo apt install -y squashfs-tools)")

    if not shutil.which("xorriso") and not shutil.which("genisoimage"):
        missing.append("xorriso or genisoimage (sudo apt install -y xorriso)")

    isolinux_found = any(
        p.exists() for p in [
            Path("/usr/lib/ISOLINUX/isolinux.bin"),
            Path("/usr/share/syslinux/isolinux.bin"),
            Path("/usr/lib/syslinux/bios/isolinux.bin"),
        ]
    )
    if not isolinux_found:
        missing.append("isolinux (sudo apt install -y isolinux syslinux-common)")

    if missing:
        raise ISOBuildError(
            "Missing build prerequisites for bootable ISO:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\n\nQuick fix:\n"
            "  sudo apt install -y debootstrap squashfs-tools xorriso isolinux syslinux-common"
        )

    if not _is_root():
        logger.warning(
            "Not running as root. debootstrap requires root.\n"
            "Re-run with: sudo kubeforge iso <project-id> --bootable"
        )


async def _run_cmd(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> tuple[str, str]:
    """Run a command asynchronously and return stdout/stderr."""
    logger.debug(f"Running: {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    stdout_s = stdout.decode(errors="replace")
    stderr_s = stderr.decode(errors="replace")

    if check and proc.returncode != 0:
        raise ISOBuildError(
            f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
            f"  stdout: {stdout_s[:500]}\n"
            f"  stderr: {stderr_s[:500]}"
        )

    return stdout_s, stderr_s


async def _build_debian_rootfs(rootfs_dir: Path) -> None:
    """Build a minimal Debian amd64 rootfs using debootstrap."""
    logger.info(f"Building Debian {DEBIAN_RELEASE} rootfs (amd64) at {rootfs_dir}...")

    debootstrap_cmd = [
        "debootstrap",
        "--variant=minbase",
        "--arch=amd64",
        "--include=" + ",".join(DEBIAN_PACKAGES),
        DEBIAN_RELEASE,
        str(rootfs_dir),
        DEBIAN_MIRROR,
    ]

    if not _is_root():
        debootstrap_cmd = ["sudo"] + debootstrap_cmd

    logger.info("Running debootstrap (this may take 2-5 minutes)...")
    await _run_cmd(debootstrap_cmd)
    logger.info("debootstrap completed successfully")

    await _customize_rootfs(rootfs_dir)


async def _customize_rootfs(rootfs_dir: Path) -> None:
    """Customize the Debian rootfs for KubeForge auto-deployment."""
    logger.info("Customizing rootfs...")

    sudo = ["sudo"] if not _is_root() else []

    # 1. Hostname
    _write_file(rootfs_dir / "etc" / "hostname", "kubeforge\n", sudo)

    # 2. Networking -- DHCP on all interfaces via systemd-networkd
    network_config = (
        "[Match]\n"
        "Name=en* eth*\n"
        "\n"
        "[Network]\n"
        "DHCP=yes\n"
        "\n"
        "[DHCPv4]\n"
        "UseDNS=yes\n"
    )
    networkd_dir = rootfs_dir / "etc" / "systemd" / "network"
    networkd_dir.mkdir(parents=True, exist_ok=True)
    _write_file(networkd_dir / "20-dhcp.network", network_config, sudo)

    # 3. Enable systemd services
    services = ["systemd-networkd.service", "systemd-resolved.service", "ssh.service"]
    for svc in services:
        wants_dir = rootfs_dir / "etc" / "systemd" / "system" / "multi-user.target.wants"
        wants_dir.mkdir(parents=True, exist_ok=True)
        link = wants_dir / svc
        target = f"/lib/systemd/system/{svc}"
        if sudo:
            await _run_cmd(sudo + ["ln", "-sf", target, str(link)], check=False)
        else:
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(target)

    # 4. Root password empty (auto-login + SSH)
    _write_file(rootfs_dir / "etc" / "shadow", "root::19900:0:99999:7:::\n", sudo, mode="0640")

    sshd_config = (
        "PermitRootLogin yes\n"
        "PermitEmptyPasswords yes\n"
        "PasswordAuthentication yes\n"
        "UsePAM yes\n"
    )
    sshd_dir = rootfs_dir / "etc" / "ssh" / "sshd_config.d"
    sshd_dir.mkdir(parents=True, exist_ok=True)
    _write_file(sshd_dir / "kubeforge.conf", sshd_config, sudo)

    # 5. Auto-login on tty1
    autologin_dir = rootfs_dir / "etc" / "systemd" / "system" / "getty@tty1.service.d"
    autologin_dir.mkdir(parents=True, exist_ok=True)
    autologin_conf = (
        "[Service]\n"
        "ExecStart=\n"
        "ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM\n"
    )
    _write_file(autologin_dir / "autologin.conf", autologin_conf, sudo)

    # 6. KubeForge systemd service
    kubeforge_service = (
        "[Unit]\n"
        "Description=KubeForge Auto-Deployer\n"
        "After=network-online.target systemd-resolved.service\n"
        "Wants=network-online.target\n"
        "ConditionPathExists=!/var/lib/kubeforge-installed\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        "ExecStart=/usr/local/bin/kubeforge-autoinstall.sh\n"
        "StandardOutput=journal+console\n"
        "StandardError=journal+console\n"
        "TimeoutStartSec=900\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    _write_file(
        rootfs_dir / "etc" / "systemd" / "system" / "kubeforge-deploy.service",
        kubeforge_service, sudo,
    )

    # Enable kubeforge-deploy
    wants_dir = rootfs_dir / "etc" / "systemd" / "system" / "multi-user.target.wants"
    link = wants_dir / "kubeforge-deploy.service"
    target = "/etc/systemd/system/kubeforge-deploy.service"
    if sudo:
        await _run_cmd(sudo + ["ln", "-sf", target, str(link)], check=False)
    elif not link.exists():
        link.symlink_to(target)

    # 7. DNS
    _write_file(
        rootfs_dir / "etc" / "resolv.conf",
        "nameserver 8.8.8.8\nnameserver 1.1.1.1\n", sudo,
    )

    # 8. fstab
    _write_file(
        rootfs_dir / "etc" / "fstab",
        "tmpfs /tmp tmpfs defaults 0 0\n/dev/sr0 /media/cdrom iso9660 ro,noauto 0 0\n",
        sudo,
    )

    # 9. Empty machine-id (regenerated on first boot)
    _write_file(rootfs_dir / "etc" / "machine-id", "", sudo)

    logger.info("Rootfs customization complete")


def _write_file(path: Path, content: str, sudo: list[str], mode: str = "0644") -> None:
    """Write a file, using sudo tee if necessary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sudo:
        import subprocess
        subprocess.run(
            sudo + ["tee", str(path)],
            input=content.encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if mode != "0644":
            subprocess.run(sudo + ["chmod", mode, str(path)], check=False)
    else:
        path.write_text(content, encoding="utf-8")
        if mode != "0644":
            os.chmod(path, int(mode, 8))


def _build_autoinstall_script(project_name: str) -> str:
    """Build the auto-install script that runs on first boot via systemd."""
    k3s_version = settings.packager.k3s_version
    lines = [
        "#!/bin/bash",
        "# KubeForge Auto-Installer -- runs on first boot via systemd",
        "set -euo pipefail",
        "",
        f'PROJECT="{project_name}"',
        f'K3S_VERSION="{k3s_version}"',
        'LOG="/var/log/kubeforge-install.log"',
        "",
        'exec > >(tee -a "$LOG") 2>&1',
        "",
        'echo "==========================================="',
        'echo " KubeForge Bootable Installer (Debian)"',
        'echo " Project: $PROJECT"',
        'echo " K3s: $K3S_VERSION"',
        'echo " Date: $(date -u)"',
        'echo "==========================================="',
        'echo ""',
        "",
        "# Find KubeForge data on mounted media",
        "find_data() {",
        '    echo "[1/9] Locating KubeForge data..."',
        "    for mount_point in /run/live/medium /lib/live/mount/medium /media/cdrom /cdrom; do",
        '        if [ -d "$mount_point/kubeforge" ]; then',
        '            DATA_DIR="$mount_point/kubeforge"',
        '            echo "  Found: $DATA_DIR"',
        "            return 0",
        "        fi",
        "    done",
        "    mkdir -p /media/cdrom",
        "    for dev in /dev/sr0 /dev/cdrom /dev/hdc; do",
        '        if [ -b "$dev" ]; then',
        '            mount -o ro "$dev" /media/cdrom 2>/dev/null || true',
        '            if [ -d /media/cdrom/kubeforge ]; then',
        '                DATA_DIR="/media/cdrom/kubeforge"',
        '                echo "  Found: $DATA_DIR (mounted from $dev)"',
        "                return 0",
        "            fi",
        "            umount /media/cdrom 2>/dev/null || true",
        "        fi",
        "    done",
        '    echo "ERROR: Could not find kubeforge data on any media"',
        "    exit 1",
        "}",
        "",
        "# Show debug info about data payload",
        "show_data_info() {",
        '    echo ""',
        '    echo "  === KubeForge Data Contents ==="',
        '    echo "  Data dir: $DATA_DIR"',
        '    ls -la "$DATA_DIR/" 2>/dev/null | sed "s/^/    /"',
        '    echo ""',
        '    echo "  Manifests:"',
        '    ls "$DATA_DIR/manifests/" 2>/dev/null | sed "s/^/    /" || echo "    (none)"',
        '    echo "  Images:"',
        '    ls "$DATA_DIR/images/" 2>/dev/null | sed "s/^/    /" || echo "    (none)"',
        '    echo "  K3s:"',
        '    ls "$DATA_DIR/k3s/" 2>/dev/null | sed "s/^/    /" || echo "    (none)"',
        '    [ -f "$DATA_DIR/deploy.yaml" ] && echo "  deploy.yaml: present" || echo "  deploy.yaml: NOT FOUND"',
        '    echo "  =============================="',
        '    echo ""',
        "}",
        "",
        "# Setup networking",
        "setup_network() {",
        '    echo "[2/9] Verifying network..."',
        "    for i in $(seq 1 30); do",
        "        if ip route show default | grep -q default; then break; fi",
        "        sleep 1",
        "    done",
        "    local ip=$(ip -4 addr show | grep 'inet ' | grep -v 127.0.0.1 | awk '{print $2}' | head -1)",
        '    echo "  IP: ${ip:-waiting}"',
        "}",
        "",
        "# Prepare storage for K3s (overlayfs-on-overlayfs not supported in live-boot)",
        "prepare_storage() {",
        '    echo "[3/9] Preparing storage for K3s..."',
        "    # Live-boot uses squashfs+overlayfs for root; nested overlayfs fails.",
        "    # Create a tmpfs-backed ext4 filesystem for K3s data.",
        "    local K3S_DATA=/var/lib/rancher/k3s",
        "    if mount | grep -q 'on / type overlay'; then",
        '        echo "  Live-boot detected, creating ext4 backing store for K3s..."',
        "        mkdir -p /run/k3s-storage",
        "        mount -t tmpfs -o size=80% tmpfs /run/k3s-storage",
        "        dd if=/dev/zero of=/run/k3s-storage/k3s.img bs=1M count=4096 2>/dev/null",
        "        mkfs.ext4 -q -F /run/k3s-storage/k3s.img",
        "        mkdir -p $K3S_DATA",
        "        mount -o loop /run/k3s-storage/k3s.img $K3S_DATA",
        '        echo "  Mounted ext4 at $K3S_DATA (4GB)"',
        "    else",
        '        echo "  Standard filesystem detected, no workaround needed."',
        "    fi",
        "}",
        "",
        "# Install K3s",
        "install_k3s() {",
        '    echo "[4/9] Installing K3s..."',
        '    if [ ! -d "$DATA_DIR/k3s" ]; then',
        '        echo "ERROR: K3s binaries not found"; exit 1',
        "    fi",
        '    install -m 755 "$DATA_DIR/k3s/k3s" /usr/local/bin/k3s',
        "    mkdir -p /var/lib/rancher/k3s/agent/images/",
        "    mkdir -p /etc/rancher/k3s/",
        "",
        "    # Configure containerd snapshotter (native as fallback for live-boot)",
        "    if mount | grep -q 'on /var/lib/rancher/k3s type ext4'; then",
        '        echo "  ext4 backing store active, using overlayfs snapshotter"',
        "    else",
        '        echo "  Configuring native snapshotter (no overlayfs support)..."',
        "        mkdir -p /var/lib/rancher/k3s/agent/etc/containerd/",
        "        cat > /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl << 'CTEOF'",
        "[plugins.\"io.containerd.grpc.v1.cri\".containerd]",
        '  snapshotter = "native"',
        "CTEOF",
        "    fi",
        "",
        '    for f in "$DATA_DIR/k3s/"*.tar.zst "$DATA_DIR/k3s/"*.tar.gz; do',
        '        [ -f "$f" ] && cp "$f" /var/lib/rancher/k3s/agent/images/',
        "    done",
        '    if [ -f "$DATA_DIR/k3s/k3s-install.sh" ]; then',
        "        INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_BIN_DIR=/usr/local/bin \\",
        '            bash "$DATA_DIR/k3s/k3s-install.sh" --write-kubeconfig-mode 644',
        "    else",
        "        ln -sf /usr/local/bin/k3s /usr/local/bin/kubectl",
        "        ln -sf /usr/local/bin/k3s /usr/local/bin/crictl",
        "        ln -sf /usr/local/bin/k3s /usr/local/bin/ctr",
        "        cat > /etc/systemd/system/k3s.service << 'EOF'",
        "[Unit]",
        "Description=K3s - Lightweight Kubernetes",
        "After=network-online.target",
        "Wants=network-online.target",
        "[Service]",
        "Type=notify",
        "ExecStart=/usr/local/bin/k3s server --write-kubeconfig-mode 644 --snapshotter native",
        "KillMode=process",
        "Delegate=yes",
        "LimitNOFILE=1048576",
        "LimitNPROC=infinity",
        "LimitCORE=infinity",
        "TasksMax=infinity",
        "Restart=always",
        "RestartSec=5s",
        "[Install]",
        "WantedBy=multi-user.target",
        "EOF",
        "        systemctl daemon-reload",
        "        systemctl enable k3s",
        "        systemctl start k3s",
        "    fi",
        '    echo "  Waiting for K3s..."',
        "    for i in $(seq 1 90); do",
        "        if /usr/local/bin/k3s kubectl get nodes >/dev/null 2>&1; then",
        '            echo "  K3s ready! (~$((i*2))s)"; return 0',
        "        fi",
        "        sleep 2",
        "    done",
        '    echo "  WARNING: K3s may not be fully ready"',
        "}",
        "",
        "# Configure registry",
        "setup_registry() {",
        '    echo "[5/9] Configuring registry..."',
        "    mkdir -p /etc/rancher/k3s/",
        '    [ -f "$DATA_DIR/registries.yaml" ] && cp "$DATA_DIR/registries.yaml" /etc/rancher/k3s/',
        "}",
        "",
        "# Load container images",
        "load_images() {",
        '    echo "[6/9] Loading container images..."',
        '    if [ -d "$DATA_DIR/images" ]; then',
        "        local count=0",
        '        local total=$(find "$DATA_DIR/images" -type f -name "*.tar*" 2>/dev/null | wc -l)',
        '        echo "  Found $total image archives"',
        '        for img in "$DATA_DIR"/images/*.tar "$DATA_DIR"/images/*.tar.gz; do',
        '            [ -f "$img" ] || continue',
        '            echo "  Loading: $(basename $img) ($(du -h $img | cut -f1))"',
        '            /usr/local/bin/k3s ctr images import "$img" 2>&1 | sed "s/^/    /" || true',
        "            count=$((count+1))",
        "        done",
        '        echo "  Done: $count images loaded"',
        '        echo "  Imported images:"',
        "        /usr/local/bin/k3s ctr images list -q 2>/dev/null | head -20 | sed 's/^/    /'",
        "    else",
        '        echo "  WARNING: No images directory found at $DATA_DIR/images"',
        '        echo "  (Was the ISO built with --no-images?)"',
        "    fi",
        "}",
        "",
        "# Apply manifests",
        "apply_manifests() {",
        '    echo "[7/9] Applying manifests..."',
        '    # Prefer combined deploy.yaml for atomic apply (proper dependency order)',
        '    if [ -f "$DATA_DIR/deploy.yaml" ]; then',
        '        echo "  Applying combined deploy.yaml..."',
        '        /usr/local/bin/k3s kubectl apply -f "$DATA_DIR/deploy.yaml" 2>&1 | sed "s/^/    /"',
        '    elif [ -d "$DATA_DIR/manifests" ]; then',
        '        echo "  Applying individual manifest files..."',
        '        find "$DATA_DIR/manifests" \\( -name "*.yaml" -o -name "*.yml" \\) | sort | while read -r m; do',
        '            echo "  Applying: $(basename $m)"',
        '            /usr/local/bin/k3s kubectl apply -f "$m" 2>&1 | sed "s/^/    /"',
        "        done",
        "    else",
        '        echo "  WARNING: No manifests found!"',
        "    fi",
        '    echo ""',
        '    echo "  Applied resources:"',
        "    /usr/local/bin/k3s kubectl get all -A 2>/dev/null | head -40",
        "}",
        "",
        "# Wait for pods",
        "wait_for_pods() {",
        '    echo "[8/9] Waiting for pods..."',
        "    sleep 15",
        "    /usr/local/bin/k3s kubectl wait --for=condition=ready pod --all \\",
        "        --all-namespaces --timeout=600s 2>/dev/null || true",
        "}",
        "",
        "# Summary",
        "show_summary() {",
        '    echo "[9/9] Deployment summary:"',
        "    local vm_ip=$(ip -4 addr show | awk '/inet / && $2!~/127.0.0.1/ {split($2,a,\"/\"); print a[1]; exit}')",
        "    /usr/local/bin/k3s kubectl get nodes -o wide 2>/dev/null || true",
        "    echo ''",
        "    /usr/local/bin/k3s kubectl get pods --all-namespaces 2>/dev/null || true",
        "    echo ''",
        "    /usr/local/bin/k3s kubectl get svc --all-namespaces -o wide 2>/dev/null || true",
        "    echo ''",
        '    echo "==========================================="',
        '    echo " KubeForge deployment complete!"',
        '    echo " Project: $PROJECT"',
        '    echo " VM IP:       $vm_ip"',
        '    echo " SSH:         ssh root@$vm_ip"',
        '    echo " kubectl:     k3s kubectl get pods -A"',
        '    echo " logs:        journalctl -u kubeforge-deploy"',
        '    echo "==========================================="',
        "    touch /var/lib/kubeforge-installed",
        "}",
        "",
        "# Main",
        "main() {",
        "    find_data",
        "    show_data_info",
        "    setup_network",
        "    prepare_storage",
        "    setup_registry",
        "    install_k3s",
        "    load_images",
        "    apply_manifests",
        "    wait_for_pods",
        "    show_summary",
        "}",
        "",
        'main "$@"',
    ]
    return "\n".join(lines) + "\n"


def _build_isolinux_cfg(project_name: str) -> str:
    """Generate ISOLINUX boot configuration for Debian live-boot."""
    lines = [
        "SERIAL 0 115200",
        "DEFAULT kubeforge",
        "TIMEOUT 30",
        "PROMPT 0",
        "",
        "LABEL kubeforge",
        "    KERNEL /live/vmlinuz",
        "    INITRD /live/initrd.img",
        "    APPEND boot=live components quiet splash console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0",
    ]
    return "\n".join(lines) + "\n"


async def _setup_isolinux(staging_dir: Path) -> None:
    """Set up ISOLINUX bootloader files."""
    isolinux_dir = staging_dir / "isolinux"
    isolinux_dir.mkdir(parents=True, exist_ok=True)

    isolinux_bin = None
    for p in [
        Path("/usr/lib/ISOLINUX/isolinux.bin"),
        Path("/usr/share/syslinux/isolinux.bin"),
        Path("/usr/lib/syslinux/bios/isolinux.bin"),
    ]:
        if p.exists():
            isolinux_bin = p
            break

    if not isolinux_bin:
        raise ISOBuildError(
            "ISOLINUX not found. Install:\n  sudo apt install -y isolinux syslinux-common"
        )

    shutil.copy2(isolinux_bin, isolinux_dir / "isolinux.bin")

    required_modules = ["ldlinux.c32"]
    optional_modules = ["menu.c32", "libutil.c32", "libcom32.c32"]
    search_dirs = [
        Path("/usr/lib/syslinux/modules/bios"),
        Path("/usr/share/syslinux"),
        Path("/usr/lib/syslinux/bios"),
    ]

    for mod in required_modules + optional_modules:
        found = False
        for search in search_dirs:
            src = search / mod
            if src.exists():
                shutil.copy2(src, isolinux_dir / mod)
                found = True
                break
        if not found and mod in required_modules:
            raise ISOBuildError(
                f"Required ISOLINUX module not found: {mod}\n"
                f"Install: sudo apt install -y syslinux-common"
            )

    logger.info(f"ISOLINUX setup complete (source: {isolinux_bin.parent})")


async def _build_squashfs(rootfs_dir: Path, output_path: Path) -> None:
    """Create a squashfs image from the rootfs directory."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sudo = ["sudo"] if not _is_root() else []

    cmd = sudo + [
        "mksquashfs",
        str(rootfs_dir),
        str(output_path),
        "-comp", "xz",
        "-b", "1M",
        "-noappend",
        "-no-progress",
        "-wildcards",
        "-e", "boot/vmlinuz*", "boot/initrd*",
    ]

    logger.info("Creating squashfs filesystem (1-3 minutes)...")
    await _run_cmd(cmd)
    size = output_path.stat().st_size
    logger.info(f"Squashfs created: {output_path} ({size:,} bytes / {size / 1024 / 1024:.1f} MB)")


async def _extract_kernel_from_rootfs(rootfs_dir: Path, live_dir: Path) -> None:
    """Extract vmlinuz and initrd from the debootstrap rootfs."""
    live_dir.mkdir(parents=True, exist_ok=True)
    boot_dir = rootfs_dir / "boot"
    sudo = ["sudo"] if not _is_root() else []

    # Find kernel
    vmlinuz = None
    for f in sorted(boot_dir.glob("vmlinuz-*"), reverse=True):
        vmlinuz = f
        break
    if not vmlinuz:
        raise ISOBuildError(f"No kernel found in {boot_dir}")

    # Find initrd
    initrd = None
    for f in sorted(boot_dir.glob("initrd.img-*"), reverse=True):
        initrd = f
        break
    if not initrd:
        raise ISOBuildError(f"No initrd found in {boot_dir}")

    dest_vmlinuz = live_dir / "vmlinuz"
    dest_initrd = live_dir / "initrd.img"

    if sudo:
        await _run_cmd(sudo + ["cp", str(vmlinuz), str(dest_vmlinuz)])
        await _run_cmd(sudo + ["cp", str(initrd), str(dest_initrd)])
        await _run_cmd(sudo + ["chmod", "644", str(dest_vmlinuz), str(dest_initrd)])
    else:
        shutil.copy2(vmlinuz, dest_vmlinuz)
        shutil.copy2(initrd, dest_initrd)

    logger.info(f"Kernel: {vmlinuz.name} ({dest_vmlinuz.stat().st_size:,} bytes)")
    logger.info(f"Initrd: {initrd.name} ({dest_initrd.stat().st_size:,} bytes)")


async def _regenerate_initramfs(rootfs_dir: Path) -> None:
    """Regenerate initramfs to include live-boot hooks."""
    sudo = ["sudo"] if not _is_root() else []

    modules_dir = rootfs_dir / "lib" / "modules"
    if not modules_dir.exists():
        raise ISOBuildError("No kernel modules found in rootfs")

    kernel_versions = sorted(
        [d for d in modules_dir.iterdir() if d.is_dir()], reverse=True
    )
    if not kernel_versions:
        raise ISOBuildError("No kernel modules found")

    kver = kernel_versions[0].name
    logger.info(f"Regenerating initramfs for kernel {kver} (with live-boot hooks)...")

    # Mount virtual filesystems for chroot
    mounts = ["proc", "sys", "dev"]
    for m in mounts:
        await _run_cmd(
            sudo + ["mount", "--bind", f"/{m}", str(rootfs_dir / m)], check=False
        )

    try:
        await _run_cmd(
            sudo + ["chroot", str(rootfs_dir), "update-initramfs", "-u", "-k", kver]
        )
    finally:
        for m in reversed(mounts):
            await _run_cmd(sudo + ["umount", "-l", str(rootfs_dir / m)], check=False)

    logger.info("Initramfs regenerated with live-boot support")


async def _build_bootable_iso_image(
    source_dir: Path, output_path: Path, project_name: str
) -> Path:
    """Build a bootable hybrid ISO using xorriso or genisoimage."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    isohdpfx = None
    for p in [
        Path("/usr/lib/ISOLINUX/isohdpfx.bin"),
        Path("/usr/share/syslinux/isohdpfx.bin"),
        Path("/usr/lib/syslinux/bios/isohdpfx.bin"),
        Path("/usr/lib/syslinux/mbr/isohdpfx.bin"),
    ]:
        if p.exists():
            isohdpfx = p
            break

    vol_label = f"KUBEFORGE_{project_name.upper()[:20]}"

    if shutil.which("xorriso"):
        cmd = [
            "xorriso",
            "-as", "mkisofs",
            "-R", "-J",
            "-joliet-long",
            "-V", vol_label,
            "-b", "isolinux/isolinux.bin",
            "-c", "isolinux/boot.cat",
            "-no-emul-boot",
            "-boot-load-size", "4",
            "-boot-info-table",
            "-input-charset", "utf-8",
        ]
        if isohdpfx:
            cmd += ["-isohybrid-mbr", str(isohdpfx)]
        cmd += ["-o", str(output_path), str(source_dir)]

    elif shutil.which("genisoimage"):
        cmd = [
            "genisoimage",
            "-R", "-J",
            "-joliet-long",
            "-V", vol_label,
            "-b", "isolinux/isolinux.bin",
            "-c", "isolinux/boot.cat",
            "-no-emul-boot",
            "-boot-load-size", "4",
            "-boot-info-table",
            "-input-charset", "utf-8",
            "-o", str(output_path),
            str(source_dir),
        ]
    else:
        raise ISOBuildError("No ISO build tool found. Install: sudo apt install -y xorriso")

    logger.info(f"Building ISO: {' '.join(cmd)}")
    await _run_cmd(cmd)

    size = output_path.stat().st_size
    logger.info(f"Bootable ISO written: {output_path} ({size:,} bytes / {size / 1024 / 1024:.1f} MB)")
    return output_path


def _build_registries_yaml() -> str:
    """Simple registries config for air-gap K3s."""
    return (
        "# K3s registry mirrors -- air-gap mode\n"
        "mirrors:\n"
        '  "docker.io":\n'
        "    endpoint: []\n"
        '  "*":\n'
        "    endpoint: []\n"
    )


async def create_bootable_iso(
    project_name: str,
    manifests: dict[str, str],
    images: list[str],
    out: Path | None = None,
    pull_container_images: bool = True,
    download_k3s: bool = True,
    target_arch: str = "amd64",
    iso_backend: str = "auto",
    registry_credentials: list[dict] | None = None,
    auth_file: str = "",
    insecure_registries: list[str] | None = None,
    progress=None,
) -> str:
    """Create a bootable ISO with Debian Linux + K3s + app manifests (amd64 only).

    The ISO boots into a minimal Debian 12 (Bookworm) live environment,
    automatically installs K3s, loads container images, and deploys the
    application. Fully air-gap safe after build.

    Build requirements:
        sudo apt install -y debootstrap squashfs-tools xorriso isolinux syslinux-common
    """
    from kubeforge.image_puller import pull_images
    from kubeforge.iso_builder import compute_iso_checksum
    from kubeforge.k3s_downloader import download_all as download_k3s_all

    # Force amd64 -- only supported architecture for bootable ISO
    target_arch = "amd64"

    _check_build_prerequisites()

    out = out or Path(settings.packager.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    iso_name = f"{project_name}-bootable-{timestamp}.iso"
    iso_path = out / iso_name

    sudo = ["sudo"] if not _is_root() else []

    with tempfile.TemporaryDirectory(prefix="kubeforge-bootiso-") as tmp:
        staging = Path(tmp) / "iso_root"
        staging.mkdir()
        rootfs_dir = Path(tmp) / "rootfs"

        step = 0
        total_steps = 12

        def _progress(msg: str) -> None:
            nonlocal step
            step += 1
            if progress:
                progress(msg, step, total_steps)
            logger.info(f"[{step}/{total_steps}] {msg}")

        # 1. Build Debian rootfs
        _progress("Building Debian rootfs (debootstrap -- may take 2-5 min)...")
        await _build_debian_rootfs(rootfs_dir)

        # 2. Write autoinstall script
        _progress("Installing KubeForge auto-deploy script...")
        autoinstall_content = _build_autoinstall_script(project_name)
        _write_file(
            rootfs_dir / "usr" / "local" / "bin" / "kubeforge-autoinstall.sh",
            autoinstall_content, sudo, mode="0755",
        )

        # 3. Regenerate initramfs with live-boot hooks
        _progress("Regenerating initramfs with live-boot support...")
        await _regenerate_initramfs(rootfs_dir)

        # 4. Extract kernel + initrd
        _progress("Extracting kernel and initrd...")
        live_dir = staging / "live"
        await _extract_kernel_from_rootfs(rootfs_dir, live_dir)

        # 5. Create squashfs
        _progress("Creating squashfs filesystem (1-3 min)...")
        squashfs_path = live_dir / "filesystem.squashfs"
        await _build_squashfs(rootfs_dir, squashfs_path)

        # 6. Setup ISOLINUX
        _progress("Setting up ISOLINUX bootloader...")
        await _setup_isolinux(staging)
        isolinux_dir = staging / "isolinux"
        (isolinux_dir / "isolinux.cfg").write_text(
            _build_isolinux_cfg(project_name), encoding="utf-8"
        )

        # 7. Create data directory
        _progress("Creating KubeForge data payload...")
        data_dir = staging / "kubeforge"
        data_dir.mkdir()

        # Write a standalone autoinstall.sh on the ISO root payload
        # (the full script lives inside squashfs, this is a reference wrapper)
        autoinstall_wrapper = (
            "#!/bin/bash\n"
            "# KubeForge auto-install wrapper\n"
            "# The main autoinstall script is embedded in the live filesystem.\n"
            "# This wrapper is for manual invocation after mounting the ISO.\n"
            f"echo 'KubeForge project: {project_name}'\n"
            "echo 'This ISO is bootable — boot it directly for automatic deployment.'\n"
            "echo 'For manual deployment, see kubeforge/manifests/ and kubeforge/k3s/'\n"
        )
        (data_dir / "autoinstall.sh").write_text(autoinstall_wrapper, encoding="utf-8")
        (data_dir / "autoinstall.sh").chmod(0o755)

        # 8. Write manifests
        _progress("Writing manifests...")
        manifests_dir = data_dir / "manifests"
        manifests_dir.mkdir()
        for filename, content in sorted(manifests.items()):
            if filename == "deploy.yaml":
                # Combined manifest goes at top level for atomic apply
                (data_dir / "deploy.yaml").write_text(content, encoding="utf-8")
            else:
                manifest_path = manifests_dir / filename
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(content, encoding="utf-8")

        (data_dir / "images.txt").write_text(
            "\n".join(sorted(images)) + "\n", encoding="utf-8"
        )
        (data_dir / "registries.yaml").write_text(
            _build_registries_yaml(), encoding="utf-8"
        )

        # 9. Pull container images
        if pull_container_images and images:
            _progress(f"Pulling {len(images)} container images...")
            images_dir = data_dir / "images"
            await pull_images(
                images, images_dir,
                backend=settings.packager.image_pull_backend,
                progress=progress,
                max_concurrent=settings.packager.max_concurrent_pulls,
                credentials=registry_credentials,
                auth_file=auth_file,
                insecure_registries=insecure_registries,
            )

        # 10. Download K3s
        if download_k3s:
            _progress("Downloading K3s binaries...")
            k3s_dir = data_dir / "k3s"
            await download_k3s_all(k3s_dir, arch=target_arch, progress=progress)

        # 11. Checksums
        _progress("Computing checksums...")
        checksums: dict[str, str] = {}
        for f in data_dir.rglob("*"):
            if f.is_file() and f.name != "SHA256SUMS":
                rel = f.relative_to(data_dir)
                h = hashlib.sha256(f.read_bytes()).hexdigest()
                checksums[str(rel)] = h
        checksum_content = "\n".join(
            f"{sha}  {path}" for path, sha in sorted(checksums.items())
        ) + "\n"
        (data_dir / "SHA256SUMS").write_text(checksum_content, encoding="utf-8")

        # 12. Build ISO
        _progress("Building bootable ISO image...")
        await _build_bootable_iso_image(staging, iso_path, project_name)

    size = iso_path.stat().st_size
    checksum = compute_iso_checksum(iso_path)
    (out / f"{iso_name}.sha256").write_text(f"{checksum}  {iso_name}\n", encoding="utf-8")

    logger.info(
        f"Bootable ISO created: {iso_path} ({size:,} bytes / {size / 1024 / 1024:.1f} MB)"
    )
    logger.info(f"SHA256: {checksum}")
    logger.info(
        "Boot this ISO in any VM (Proxmox, VMware, VirtualBox) or write to USB.\n"
        "  Auto-deploys K3s + your application on first boot.\n"
        "  SSH: ssh root@<vm-ip> (no password)"
    )
    return str(iso_path)
