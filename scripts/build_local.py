#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ZMK_IMAGE_TAG = "zmk-local-build:latest"
WORKSPACE_IN_CONTAINER = "/workspaces/zmk"
CONFIG_IN_CONTAINER = "/workspaces/zmk-config/config"


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=check,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}")


def resolve_container_cmd() -> list[str]:
    if shutil.which("docker") is not None:
        return ["docker"]
    if shutil.which("colima") is not None:
        return ["colima", "nerdctl"]
    if shutil.which("nerdctl") is not None:
        return ["nerdctl"]
    die("no container runtime found; install docker CLI or colima")


def ensure_container_backend(container_cmd: list[str]) -> None:
    if sys.platform == "darwin" and shutil.which("colima") is not None:
        print("Ensuring Colima is running...")
        run(["colima", "start"])

    info = run([*container_cmd, "info"], check=False, capture=True)
    if (
        info.returncode != 0
        and container_cmd == ["colima", "nerdctl"]
        and "nerdctl only supports containerd runtime" in (info.stderr or "")
    ):
        print("Colima runtime is docker; switching Colima to containerd for nerdctl...")
        run(["colima", "stop"], check=False)
        run(["colima", "start", "--runtime", "containerd"])
        info = run([*container_cmd, "info"], check=False, capture=True)

    if info.returncode != 0 and container_cmd == ["docker"] and sys.platform == "darwin" and shutil.which("colima") is not None:
        inspect = run(["docker", "context", "inspect", "colima"], check=False, capture=True)
        if inspect.returncode == 0:
            run(["docker", "context", "use", "colima"], check=False, capture=True)
            info = run([*container_cmd, "info"], check=False, capture=True)

    if info.returncode != 0:
        details = info.stderr.strip() or info.stdout.strip() or "no details"
        die(
            "container runtime is not available; start Colima (or your backend) and retry\n"
            f"command: {' '.join(container_cmd)} info\n"
            f"details: {details}"
        )


def ensure_image(container_cmd: list[str], zmk_dir: Path) -> None:
    devcontainer_dir = zmk_dir / ".devcontainer"
    run([*container_cmd, "build", "-t", ZMK_IMAGE_TAG, "."], cwd=devcontainer_dir)


def run_in_container(container_cmd: list[str], zmk_dir: Path, root_dir: Path, command: str) -> None:
    mounts = [
        "--mount",
        f"type=bind,source={zmk_dir},target={WORKSPACE_IN_CONTAINER}",
        "--mount",
        f"type=bind,source={root_dir},target=/workspaces/zmk-config",
        "--mount",
        "type=volume,source=zmk-root-user,target=/root",
        "--mount",
        "type=volume,source=zmk-modules,target=/workspaces/zmk-modules",
        "--mount",
        f"type=volume,source=zmk-zephyr,target={WORKSPACE_IN_CONTAINER}/zephyr",
        "--mount",
        f"type=volume,source=zmk-zephyr-modules,target={WORKSPACE_IN_CONTAINER}/modules",
        "--mount",
        f"type=volume,source=zmk-zephyr-tools,target={WORKSPACE_IN_CONTAINER}/tools",
    ]
    run(
        [
            *container_cmd,
            "run",
            "--rm",
            "--security-opt",
            "label=disable",
            "-e",
            f"WORKSPACE_DIR={WORKSPACE_IN_CONTAINER}",
            "-e",
            "PROMPT_COMMAND=history -a",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            *mounts,
            ZMK_IMAGE_TAG,
            "bash",
            "-lc",
            command,
        ]
    )


def clean_nanopb_bytecode(zmk_dir: Path, side: str) -> None:
    build_dir = zmk_dir / "build" / f"corne_{side}" / "nanopb" / "generator"
    for pycache in (build_dir / "__pycache__", build_dir / "proto" / "__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)


def build_side(
    container_cmd: list[str],
    zmk_dir: Path,
    root_dir: Path,
    workspace_in_container: str,
    config_in_container: str,
    board: str,
    side: str,
    pristine: bool,
) -> None:
    shield = f"corne_{side}"
    clean_nanopb_bytecode(zmk_dir, side)
    build_dir = f"build/{shield}"
    pristine_flag = "-p " if pristine else ""
    cmd = (
        f"cd {workspace_in_container} && "
        f"west build {pristine_flag}-d {build_dir} -s app -b {board} -- "
        f"-DZMK_CONFIG={config_in_container} -DSHIELD={shield}"
    )
    print(f"Building {shield}...")
    run_in_container(container_cmd, zmk_dir, root_dir, cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Corne firmware using a container runtime")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--left", action="store_true", help="Build left half only")
    target.add_argument("--right", action="store_true", help="Build right half only")
    target.add_argument("--both", action="store_true", help="Build both halves (default)")
    parser.add_argument("-p", "--pristine", action="store_true", help="Run pristine builds")
    parser.add_argument("--skip-update", action="store_true", help="Skip west update")
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parents[1]
    zmk_dir = root_dir / ".zmk" / "zmk"
    config_dir = root_dir / "config"

    workspace_in_container = WORKSPACE_IN_CONTAINER
    config_in_container = CONFIG_IN_CONTAINER
    board = "nice_nano_v2"

    build_target = "both"
    if args.left:
        build_target = "left"
    elif args.right:
        build_target = "right"
    elif args.both:
        build_target = "both"

    if not (zmk_dir / ".devcontainer").is_dir():
        die(f"missing devcontainer setup at {zmk_dir / '.devcontainer'}")
    if not (config_dir / "corne.keymap").is_file():
        die(f"missing keymap: {config_dir / 'corne.keymap'}")

    container_cmd = resolve_container_cmd()
    print(f"Using container runtime: {' '.join(container_cmd)}")
    ensure_container_backend(container_cmd)

    print(f"Building local image ({ZMK_IMAGE_TAG})...")
    ensure_image(container_cmd, zmk_dir)

    print("Preparing west workspace in container...")
    run_in_container(
        container_cmd,
        zmk_dir,
        root_dir,
        f"cd {workspace_in_container} && if [ ! -d .west ]; then west init -l app; fi",
    )

    if not args.skip_update:
        run_in_container(container_cmd, zmk_dir, root_dir, f"cd {workspace_in_container} && west update")

    if build_target in ("left", "both"):
        build_side(
            container_cmd,
            zmk_dir,
            root_dir,
            workspace_in_container,
            config_in_container,
            board,
            "left",
            args.pristine,
        )
    if build_target in ("right", "both"):
        build_side(
            container_cmd,
            zmk_dir,
            root_dir,
            workspace_in_container,
            config_in_container,
            board,
            "right",
            args.pristine,
        )

    print("\nBuild complete.")
    print("Artifacts are under:")
    print(f"  {zmk_dir}/build/corne_left")
    print(f"  {zmk_dir}/build/corne_right")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
