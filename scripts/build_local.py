#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from build_matrix import BuildMatrixEntry, artifact_name, build_dir_name, load_build_matrix, select_entries
except ModuleNotFoundError:
    from scripts.build_matrix import BuildMatrixEntry, artifact_name, build_dir_name, load_build_matrix, select_entries

ZMK_IMAGE_TAG = "zmk-local-build:latest"
WORKSPACE_IN_CONTAINER = "/workspaces/zmk"
CONFIG_IN_CONTAINER = "/workspaces/zmk-config/config"
DEFAULT_BOARD = "nice_nano_v2"


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


def clean_nanopb_bytecode(zmk_dir: Path, build_subdir: str) -> None:
    build_dir = zmk_dir / "build" / build_subdir / "nanopb" / "generator"
    for pycache in (build_dir / "__pycache__", build_dir / "proto" / "__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)


def build_entry(
    container_cmd: list[str],
    zmk_dir: Path,
    root_dir: Path,
    workspace_in_container: str,
    config_in_container: str,
    board: str,
    shield: str,
    snippet: str | None,
    cmake_args: str | None,
    artifact_dir: Path,
    artifact_stem: str,
    pristine: bool,
) -> tuple[Path, Path]:
    build_subdir = build_dir_name(
        BuildMatrixEntry(
            board=board,
            shield=shield,
            snippet=snippet,
        )
    )
    clean_nanopb_bytecode(zmk_dir, build_subdir)
    build_dir = f"build/{build_subdir}"
    pristine_flag = "-p " if pristine else ""
    snippet_arg = f"-S {shlex.quote(snippet)} " if snippet else ""
    cmake_arg_suffix = f" {cmake_args}" if cmake_args else ""
    cmd = (
        f"cd {shlex.quote(workspace_in_container)} && "
        f"west build {pristine_flag}-d {shlex.quote(build_dir)} -s app -b {shlex.quote(board)} "
        f"{snippet_arg}-- -DZMK_CONFIG={shlex.quote(config_in_container)} "
        f"-DSHIELD={shlex.quote(shield)}{cmake_arg_suffix}"
    )
    print(f"Building {shield}...")
    run_in_container(container_cmd, zmk_dir, root_dir, cmd)

    build_out = zmk_dir / build_dir / "zephyr" / "zmk.uf2"
    if not build_out.is_file():
        die(f"expected UF2 not found: {build_out}")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{artifact_stem}.uf2"
    shutil.copy2(build_out, artifact_path)
    return build_out, artifact_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Corne firmware using a container runtime")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--left", action="store_true", help="Build left half only")
    target.add_argument("--right", action="store_true", help="Build right half only")
    target.add_argument("--both", action="store_true", help="Build both halves (default)")
    parser.add_argument("-p", "--pristine", action="store_true", help="Run pristine builds")
    parser.add_argument("--skip-update", action="store_true", help="Skip west update")
    parser.add_argument("--all-variants", action="store_true", help="Build all matching build.yaml entries per side (default: first entry per side)")
    parser.add_argument("--board", default=DEFAULT_BOARD, help=f"Board to build (default: {DEFAULT_BOARD})")
    parser.add_argument("--build-matrix", type=Path, help="Path to build matrix YAML (default: <repo>/build.yaml)")
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parents[1]
    zmk_dir = root_dir / ".zmk" / "zmk"
    config_dir = root_dir / "config"
    build_matrix_path = args.build_matrix or (root_dir / "build.yaml")

    workspace_in_container = WORKSPACE_IN_CONTAINER
    config_in_container = CONFIG_IN_CONTAINER

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

    try:
        matrix_entries = load_build_matrix(build_matrix_path)
        build_plan = select_entries(
            matrix_entries,
            board=args.board,
            target=build_target,
            all_variants=args.all_variants,
        )
    except (FileNotFoundError, ValueError) as exc:
        die(str(exc))

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
    run_in_container(container_cmd, zmk_dir, root_dir, f"cd {workspace_in_container} && west zephyr-export")

    artifact_dir = root_dir / "firmware"
    built: list[tuple[str, Path, Path]] = []
    for entry in build_plan:
        stem = artifact_name(entry)
        source_uf2, copied_artifact = build_entry(
            container_cmd,
            zmk_dir,
            root_dir,
            workspace_in_container,
            config_in_container,
            entry.board,
            entry.shield,
            entry.snippet,
            entry.cmake_args,
            artifact_dir,
            stem,
            args.pristine,
        )
        built.append((entry.shield, source_uf2, copied_artifact))

    print("\nBuild complete.")
    print("Built targets:")
    for shield, source_uf2, copied_artifact in built:
        print(f"  {shield}")
        print(f"    UF2: {source_uf2}")
        print(f"    Artifact: {copied_artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
