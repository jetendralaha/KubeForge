"""KubeForge CLI — Typer-based command-line interface.

Commands:
  serve          Start the KubeForge API server
  version        Show version info
  project        Create / list / show / delete projects
  upload         Upload a deployment artifact (Compose, K8s YAML, Helm .tgz)
  analyze        Run AI risk analysis
  recommend      Get K3s best-practice recommendations
  generate       Generate production K3s manifests
  images         Resolve and list container images
  package        Create offline deployment bundle
  iso            Create air-gap ISO image
  deploy         Deploy generated manifests to a cluster
  status         Check packaging job status
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx
import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="kubeforge",
    help="KubeForge — AI-Powered K3s Deployment Platform",
    no_args_is_help=True,
)

console = Console()
BASE_URL = "http://localhost:8080/api/v1"


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _raise_with_detail(resp: httpx.Response) -> None:
    """Raise an error that includes the API's error message."""
    if resp.is_success:
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    raise typer.Exit(code=1) if not detail else SystemExit(
        f"Error {resp.status_code}: {detail}"
    )


def _get(path: str) -> dict:
    resp = httpx.get(_url(path), timeout=30.0)
    _raise_with_detail(resp)
    return resp.json()


def _post(path: str, json_body: dict | None = None, **kwargs) -> dict:
    resp = httpx.post(_url(path), json=json_body, timeout=120.0, **kwargs)
    _raise_with_detail(resp)
    return resp.json()


def _delete(path: str) -> None:
    resp = httpx.delete(_url(path), timeout=10.0)
    _raise_with_detail(resp)


# ── Server ──────────────────────────────────────────────────────────


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8080, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
):
    """Start the KubeForge API server."""
    import uvicorn
    rprint(f"[bold green]Starting KubeForge server on {host}:{port}[/bold green]")
    uvicorn.run("kubeforge.app:app", host=host, port=port, reload=reload)


# ── Version ─────────────────────────────────────────────────────────


@app.command()
def version():
    """Show version information."""
    from kubeforge.version import version_info
    info = version_info()
    for k, v in info.items():
        rprint(f"  [cyan]{k}[/cyan]: {v}")


# ── Project commands ────────────────────────────────────────────────


project_app = typer.Typer(help="Manage projects")
app.add_typer(project_app, name="project")


@project_app.command("create")
def project_create(
    name: str = typer.Argument(..., help="Project name"),
    description: str = typer.Option("", "--desc", help="Project description"),
):
    """Create a new project."""
    data = _post("/projects", {"name": name, "description": description})
    rprint(f"[green]Created project:[/green] {data['id']}  ({data['name']})")


@project_app.command("list")
def project_list():
    """List all projects."""
    projects = _get("/projects")
    table = Table(title="Projects")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Created")
    for p in projects:
        table.add_row(p["id"], p["name"], p["status"], p["created_at"][:19])
    console.print(table)


@project_app.command("show")
def project_show(project_id: str = typer.Argument(..., help="Project ID")):
    """Show project details."""
    p = _get(f"/projects/{project_id}")
    rprint(f"  [cyan]ID[/cyan]:          {p['id']}")
    rprint(f"  [cyan]Name[/cyan]:        {p['name']}")
    rprint(f"  [cyan]Description[/cyan]: {p['description']}")
    rprint(f"  [cyan]Status[/cyan]:      {p['status']}")
    rprint(f"  [cyan]Created[/cyan]:     {p['created_at']}")


@project_app.command("delete")
def project_delete(project_id: str = typer.Argument(..., help="Project ID")):
    """Delete a project."""
    _delete(f"/projects/{project_id}")
    rprint(f"[red]Deleted project {project_id}[/red]")


# ── Upload ──────────────────────────────────────────────────────────


@app.command()
def upload(
    project_id: str = typer.Argument(..., help="Project ID"),
    filepath: Path = typer.Argument(..., help="Path to manifest file or Helm chart .tgz", exists=True),
    values: Path = typer.Option(None, "--values", "-f", help="Path to Helm values.yml override file", exists=True),
    namespace: str = typer.Option("default", "--namespace", "-n", help="Target namespace for deployment"),
    role: str = typer.Option("app", "--role", help="Chart role: 'app' or 'paas' (for multi-chart ordering)"),
):
    """Upload a deployment artifact (Compose, K8s YAML, or Helm chart .tgz).

    For Helm charts, use --values/-f to provide a custom values.yml and
    --namespace/-n to set the target namespace.  For multi-chart projects
    (PaaS + App), use --role paas for the infrastructure chart.
    """
    # Determine content type based on file extension
    fn_lower = filepath.name.lower()
    is_binary = fn_lower.endswith((".tgz", ".tar.gz"))
    content_type = "application/gzip" if is_binary else "application/x-yaml"

    files_dict: dict = {
        "file": (filepath.name, open(filepath, "rb"), content_type),
    }

    # Attach values file if provided
    if values:
        files_dict["values_file"] = (values.name, open(values, "rb"), "application/x-yaml")

    # Form data for additional parameters
    data = {
        "namespace": namespace,
        "chart_role": role,
    }

    resp = httpx.post(
        _url(f"/artifacts/{project_id}/upload"),
        files=files_dict,
        data=data,
        timeout=60.0,
    )
    _raise_with_detail(resp)
    result = resp.json()
    rprint(f"[green]Uploaded:[/green] {result['filename']}")
    rprint(f"  ID:         {result['id']}")
    rprint(f"  Type:       {result['artifact_type']} (confidence: {result.get('confidence', 0):.0%})")
    rprint(f"  Status:     {result['status']}")
    rprint(f"  Namespace:  {result.get('namespace', 'default')}")
    rprint(f"  Chart role: {result.get('chart_role', 'app')}")
    if result.get('has_values'):
        rprint(f"  Values:     [green]attached[/green]")
    if result['status'] == 'failed':
        rprint("[yellow]  ⚠ Auto-parse failed. 'generate' will retry parsing.[/yellow]")
        rprint("[dim]    Check server logs for details (e.g. helm not installed).[/dim]")


# ── Analyze ─────────────────────────────────────────────────────────


@app.command()
def analyze(artifact_id: str = typer.Argument(..., help="Artifact ID")):
    """Run AI risk analysis on an artifact."""
    with console.status("Analyzing risks..."):
        data = _post(f"/analysis/{artifact_id}/risks")

    rprint(f"\n[bold]Risk Analysis[/bold] — {data['summary']}\n")
    table = Table()
    table.add_column("Severity", style="bold")
    table.add_column("Category")
    table.add_column("Title")
    table.add_column("Remediation")

    severity_colors = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim", "info": "dim"}
    for r in data.get("risks", []):
        color = severity_colors.get(r["severity"], "white")
        table.add_row(
            f"[{color}]{r['severity'].upper()}[/{color}]",
            r.get("category", ""),
            r["title"],
            r.get("remediation", "")[:80],
        )
    console.print(table)


# ── Recommend ───────────────────────────────────────────────────────


@app.command()
def recommend(artifact_id: str = typer.Argument(..., help="Artifact ID")):
    """Get K3s best-practice recommendations."""
    with console.status("Generating recommendations..."):
        data = _post(f"/analysis/{artifact_id}/recommend")

    rprint(f"\n[bold]{data['summary']}[/bold]\n")
    for r in data.get("recommendations", []):
        prio = r.get("priority", "medium")
        color = {"high": "red", "medium": "yellow", "low": "green"}.get(prio, "white")
        auto = " [auto-apply]" if r.get("auto_apply") else ""
        rprint(f"  [{color}][{prio.upper()}][/{color}] {r['title']}{auto}")
        rprint(f"         {r['description']}")
        rprint()


# ── Generate ────────────────────────────────────────────────────────


@app.command()
def generate(
    project_id: str = typer.Argument(..., help="Project ID"),
    namespace: str = typer.Option("", help="Target namespace"),
):
    """Generate production-ready K3s manifests."""
    with console.status("Generating manifests..."):
        data = _post(f"/manifests/{project_id}/generate", {"namespace": namespace} if namespace else None)

    rprint(f"[green]Generated combined deploy.yaml ({data['manifest_count']} resources):[/green]")
    for f in data.get("files", []):
        rprint(f"  [cyan]Output:[/cyan] {f}")
    sections = data.get("sections", [])
    if sections:
        rprint(f"\n  [dim]Sections ({len(sections)}):[/dim]")
        for s in sections:
            rprint(f"    - {s}")


# ── Images ──────────────────────────────────────────────────────────


@app.command()
def images(project_id: str = typer.Argument(..., help="Project ID")):
    """Resolve and list container images."""
    data = _post(f"/manifests/{project_id}/resolve-images")

    table = Table(title="Container Images")
    table.add_column("Reference")
    table.add_column("Registry")
    table.add_column("Tag")
    table.add_column("Cached")
    for img in data.get("images", []):
        table.add_row(img["reference"], img.get("registry", ""), img.get("tag", ""), str(img.get("cached", False)))
    console.print(table)


# ── Package ─────────────────────────────────────────────────────────


@app.command()
def package(project_id: str = typer.Argument(..., help="Project ID")):
    """Create an offline deployment bundle (tar.gz — lightweight, no images pulled)."""
    data = _post(f"/packages/{project_id}")
    rprint(f"[green]Packaging started:[/green] job_id={data['job_id']}")
    rprint(f"Check status: kubeforge status {data['job_id']}")


@app.command()
def iso(
    project_id: str = typer.Argument(..., help="Project ID"),
    bootable: bool = typer.Option(False, "--bootable", help="Create bootable ISO with embedded Debian Linux OS (boots into K3s + deploys app)"),
    no_images: bool = typer.Option(False, "--no-images", help="Skip pulling container images"),
    no_k3s: bool = typer.Option(False, "--no-k3s", help="Skip downloading K3s binaries"),
    arch: str = typer.Option("auto", "--arch", help="Target arch: amd64, arm64, or auto"),
    registry: str = typer.Option("", "--registry", help="Registry host for authentication"),
    registry_user: str = typer.Option("", "--registry-user", help="Registry username"),
    registry_pass: str = typer.Option("", "--registry-pass", help="Registry password or token"),
    auth_file: str = typer.Option("", "--auth-file", help="Path to Docker config.json for registry auth"),
    insecure_registry: str = typer.Option("", "--insecure-registry", help="Registry to treat as HTTP (skip TLS)"),
):
    """Create a full air-gap ISO for on-premises deployment.

    Includes K3s binary, container images, manifests, and install script.
    The resulting ISO can be mounted or burned on the target machine.

    Use --bootable to create a bootable ISO with Debian Linux that auto-deploys
    K3s and your workloads on first boot (ideal for Proxmox/VMware/bare-metal).

    For private registries, provide --registry-user and --registry-pass,
    or point to an existing Docker config.json with --auth-file.
    """
    params: dict = {
        "pull_images": not no_images,
        "download_k3s": not no_k3s,
        "target_arch": arch,
        "bootable": bootable,
    }

    # Registry auth
    if registry and registry_user and registry_pass:
        params["registry_credentials"] = [
            {"registry": registry, "username": registry_user, "password": registry_pass}
        ]
    if auth_file:
        params["auth_file"] = auth_file
    if insecure_registry:
        params["insecure_registries"] = [insecure_registry]

    data = _post(f"/packages/{project_id}/iso", params)
    iso_type = "Bootable ISO" if bootable else "ISO"
    rprint(f"[green]{iso_type} build started:[/green] job_id={data['job_id']}")
    rprint(f"Check status: kubeforge status {data['job_id']}")
    if bootable:
        rprint("[dim]This may take several minutes (building Debian rootfs + K3s + pulling images)...[/dim]")
    else:
        rprint("[dim]This may take several minutes (downloading K3s + pulling images)...[/dim]")


@app.command()
def status(job_id: str = typer.Argument(..., help="Job ID")):
    """Check packaging job status."""
    data = _get(f"/packages/{job_id}/status")
    rprint(f"  [cyan]Status[/cyan]:  {data['status']}")
    if data.get("output_path"):
        rprint(f"  [cyan]Output[/cyan]:  {data['output_path']}")
        rprint(f"  [cyan]Size[/cyan]:    {data['size_bytes']:,} bytes")
    if data.get("error"):
        rprint(f"  [red]Error[/red]:   {data['error']}")


# ── Deploy ──────────────────────────────────────────────────────────


@app.command("iso-validate")
def iso_validate(
    iso_path: str = typer.Argument(..., help="Path to the .iso file to validate"),
    boot_test: bool = typer.Option(False, "--boot-test", help="Quick-test boot with QEMU (requires qemu-system-x86_64)"),
):
    """Validate a bootable ISO has correct structure and is bootable.

    Checks: file format, ISOLINUX bootloader, kernel, initramfs, modloop,
    KubeForge payload (manifests, k3s, images), and optionally test-boots
    with QEMU.
    """
    import subprocess
    from pathlib import Path as P

    iso = P(iso_path)
    if not iso.exists():
        rprint(f"[red]ISO not found:[/red] {iso_path}")
        raise typer.Exit(1)

    rprint(f"[bold]Validating:[/bold] {iso.name} ({iso.stat().st_size / 1024 / 1024:.1f} MB)")
    rprint("")

    errors: list[str] = []
    warnings: list[str] = []

    # 1. Check file magic (should start with CD001 at offset 0x8001)
    with open(iso, "rb") as f:
        f.seek(0x8001)
        magic = f.read(5)
    if magic == b"CD001":
        rprint("  [green]✓[/green] Valid ISO 9660 format")
    else:
        errors.append("Not a valid ISO 9660 file (missing CD001 magic)")
        rprint("  [red]✗[/red] Not a valid ISO 9660 file")

    # 2. Use xorriso to list contents (if available)
    iso_files: list[str] = []
    if shutil.which("xorriso"):
        result = subprocess.run(
            ["xorriso", "-indev", str(iso), "-find", "/", "-exec", "lsdl"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # xorriso outputs file listing to stdout (result channel)
            # Format: -rwxr-xr-x ... '/path/to/file'
            # Paths are single-quoted, grab last token and strip quotes
            all_output = result.stdout + "\n" + result.stderr
            for line in all_output.splitlines():
                line = line.strip()
                if not line or line.startswith("xorriso"):
                    continue
                parts = line.split()
                if parts:
                    path = parts[-1].strip("'\"")
                    if "/" in path:
                        iso_files.append(path)
    elif shutil.which("isoinfo"):
        result = subprocess.run(
            ["isoinfo", "-l", "-i", str(iso)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            iso_files = [line.strip() for line in result.stdout.splitlines()]

    # 3. Check required boot files
    checks = {
        "isolinux/isolinux.bin": "ISOLINUX bootloader",
        "isolinux/isolinux.cfg": "ISOLINUX configuration",
        "live/vmlinuz": "Linux kernel",
        "live/initrd.img": "Initial ramdisk (initrd)",
    }
    payload_checks = {
        "kubeforge/autoinstall.sh": "Auto-install script",
        "kubeforge/manifests": "Kubernetes manifests",
        "live/filesystem.squashfs": "Root filesystem (squashfs)",
    }
    optional_checks = {
        "boot/modloop-lts": "Kernel modules (modloop)",
        "isolinux/ldlinux.c32": "ISOLINUX ldlinux module",
        "isolinux/menu.c32": "ISOLINUX menu module",
        "kubeforge/k3s": "K3s binaries",
        "kubeforge/images": "Container images",
    }

    def _file_exists_on_iso(path: str) -> bool:
        """Check if a path exists in the ISO file listing."""
        # Try exact match and with leading /
        for f in iso_files:
            if f.rstrip("/").endswith(path) or f"/{path}" in f:
                return True
        return False

    if iso_files:
        rprint("")
        rprint("  [bold]Boot components:[/bold]")
        for path, desc in checks.items():
            if _file_exists_on_iso(path):
                rprint(f"    [green]✓[/green] {desc} ({path})")
            else:
                errors.append(f"Missing required: {path} ({desc})")
                rprint(f"    [red]✗[/red] {desc} ({path}) — MISSING")

        rprint("")
        rprint("  [bold]KubeForge payload:[/bold]")
        for path, desc in payload_checks.items():
            if _file_exists_on_iso(path):
                rprint(f"    [green]✓[/green] {desc}")
            else:
                errors.append(f"Missing required: {path} ({desc})")
                rprint(f"    [red]✗[/red] {desc} — MISSING")

        for path, desc in optional_checks.items():
            if _file_exists_on_iso(path):
                rprint(f"    [green]✓[/green] {desc}")
            else:
                warnings.append(f"Missing optional: {path} ({desc})")
                rprint(f"    [yellow]○[/yellow] {desc} — not found (optional)")
    else:
        warnings.append("Could not list ISO contents (install xorriso or isoinfo)")
        rprint("  [yellow]![/yellow] Cannot inspect ISO contents (install xorriso for full validation)")

    # 4. Check El Torito boot record (at sector 17)
    with open(iso, "rb") as f:
        f.seek(0x8801)  # Sector 17, offset 1
        boot_indicator = f.read(5)
    if boot_indicator == b"CD001":
        # Sector 17 is the Boot Record Volume Descriptor
        f_check = open(iso, "rb")
        f_check.seek(0x8800)
        br_type = f_check.read(1)
        f_check.close()
        if br_type == b"\x00":
            rprint("\n  [green]✓[/green] El Torito boot record present")
        else:
            rprint("\n  [green]✓[/green] ISO has multiple volume descriptors")
    else:
        warnings.append("No El Torito boot record detected at sector 17")

    # 5. QEMU boot test
    if boot_test:
        rprint("\n  [bold]QEMU boot test:[/bold]")
        qemu = shutil.which("qemu-system-x86_64")
        if not qemu:
            rprint("    [yellow]![/yellow] qemu-system-x86_64 not found. Install: sudo apt install qemu-system-x86")
        else:
            rprint("    Starting QEMU (10 second timeout, headless)...")
            # Run QEMU in nographic mode, capture serial output
            result = subprocess.run(
                [
                    qemu, "-cdrom", str(iso), "-m", "512",
                    "-boot", "d", "-nographic",
                    "-serial", "mon:stdio",
                    "-no-reboot",
                ],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stdout + result.stderr
            if "boot" in output.lower() or "linux" in output.lower() or "isolinux" in output.lower():
                rprint("    [green]✓[/green] QEMU detected boot activity (kernel loading)")
            elif "no bootable" in output.lower():
                errors.append("QEMU: no bootable device found")
                rprint("    [red]✗[/red] QEMU reports: no bootable device")
            else:
                rprint(f"    [yellow]○[/yellow] QEMU output inconclusive (first 200 chars): {output[:200]}")

    # Summary
    rprint("")
    if errors:
        rprint(f"[bold red]FAILED[/bold red] — {len(errors)} error(s):")
        for e in errors:
            rprint(f"  • {e}")
        raise typer.Exit(1)
    elif warnings:
        rprint(f"[bold yellow]PASSED with warnings[/bold yellow] — {len(warnings)} warning(s):")
        for w in warnings:
            rprint(f"  • {w}")
    else:
        rprint("[bold green]PASSED[/bold green] — ISO looks good for booting!")

    rprint("")
    rprint("[dim]Tip: For visual boot test, run:[/dim]")
    rprint(f"[dim]  qemu-system-x86_64 -cdrom {iso_path} -m 2048 -boot d[/dim]")


@app.command()
def deploy(
    project_id: str = typer.Argument(..., help="Project ID"),
    kubeconfig: str = typer.Option("", "--kubeconfig", help="Path to kubeconfig file"),
    timeout: int = typer.Option(300, "--timeout", help="Readiness wait timeout in seconds"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for deployment to complete"),
):
    """Deploy generated manifests to a K3s/K8s cluster.

    For multi-chart projects (PaaS + App), the orchestrator deploys PaaS
    first, waits for pod readiness, then deploys the App chart.
    """
    params: dict = {}
    if kubeconfig:
        params["kubeconfig"] = kubeconfig
    if timeout:
        params["readiness_timeout"] = timeout

    data = _post(f"/deploy/{project_id}", params)
    deployment_id = data["deployment_id"]
    rprint(f"[green]Deployment started:[/green] deployment_id={deployment_id}")

    if not wait:
        rprint(f"Check status: kubeforge deploy-status {deployment_id}")
        return

    # Poll until complete
    import time
    with console.status("Deploying..."):
        while True:
            time.sleep(3)
            status_data = _get(f"/deploy/{deployment_id}/status")
            current = status_data.get("status", "")
            if current in ("succeeded", "failed", "rolled_back"):
                break

    if status_data["status"] == "succeeded":
        rprint("[bold green]Deployment succeeded![/bold green]")
    else:
        rprint(f"[bold red]Deployment {status_data['status']}[/bold red]")

    if status_data.get("log"):
        rprint("\n[dim]Deployment log:[/dim]")
        rprint(status_data["log"])


@app.command("deploy-status")
def deploy_status(deployment_id: str = typer.Argument(..., help="Deployment ID")):
    """Check deployment status and logs."""
    data = _get(f"/deploy/{deployment_id}/status")
    rprint(f"  [cyan]Status[/cyan]:    {data['status']}")
    rprint(f"  [cyan]Created[/cyan]:   {data['created_at'][:19]}")
    rprint(f"  [cyan]Updated[/cyan]:   {data['updated_at'][:19]}")
    if data.get("log"):
        rprint(f"\n[dim]Log:[/dim]")
        rprint(data["log"])


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
