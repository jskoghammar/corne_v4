#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from build_matrix import artifact_name, build_dir_name, load_build_matrix
except ModuleNotFoundError:
    from scripts.build_matrix import artifact_name, build_dir_name, load_build_matrix


BOOT_VOLUME_RE = re.compile(r"^nice[ _-]?nano", re.IGNORECASE)
DEFAULT_BOARD = "nice_nano_v2"
FLASH_FILENAME = "zmk.uf2"
FLASH_STATE_FILENAME = ".flash-state.json"


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def run_capture(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        die(f"command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if re.fullmatch(r"-?0x[0-9a-fA-F]+", raw):
        return int(raw, 16)
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_ioreg_usb_devices(text: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    device_marker_keys = {
        "idVendor",
        "idProduct",
        "locationID",
        "kUSBAddress",
        "USB Address",
        "kUSBSerialNumberString",
        "USB Serial Number",
        "kUSBProductString",
        "USB Product Name",
    }

    def append_if_device(candidate: dict[str, Any] | None) -> None:
        if candidate and any(key in candidate for key in device_marker_keys):
            devices.append(candidate)

    for line in text.splitlines():
        if "<class IOUSBHostDevice" in line:
            append_if_device(current)
            current = {}
            name_match = re.search(r"[-+| ]+o (.+?)@", line)
            if name_match:
                current["_name"] = name_match.group(1).strip()
            continue

        stripped = line.strip().lstrip("| ").strip()
        if stripped == "{":
            if current is None:
                current = {}
            continue
        if stripped == "}":
            append_if_device(current)
            current = None
            continue

        if current is None:
            continue

        match = re.match(r'^"([^"]+)"\s*=\s*(.+)$', stripped)
        if not match:
            continue

        key = match.group(1)
        value = parse_value(match.group(2))
        current[key] = value

    append_if_device(current)

    return devices


def coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        if re.fullmatch(r"-?0x[0-9a-fA-F]+", text):
            return int(text, 16)
    return None


def normalize_usb_device(dev: dict[str, Any]) -> dict[str, Any] | None:
    serial = dev.get("kUSBSerialNumberString") or dev.get("USB Serial Number")
    address = dev.get("kUSBAddress") or dev.get("USB Address")
    location = dev.get("locationID")
    vendor_id = dev.get("idVendor")
    vendor = dev.get("kUSBVendorString") or dev.get("USB Vendor Name") or ""
    product = dev.get("kUSBProductString") or dev.get("USB Product Name") or dev.get("_name") or ""

    normalized = {
        "kUSBSerialNumberString": str(serial) if serial is not None else "",
        "kUSBAddress": coerce_int(address),
        "locationId": coerce_int(location),
        "product": str(product),
        "vendor": str(vendor),
        "vendorId": coerce_int(vendor_id),
    }

    if (
        not normalized["kUSBSerialNumberString"]
        and normalized["kUSBAddress"] is None
        and normalized["vendorId"] is None
        and not normalized["vendor"]
        and not normalized["product"]
    ):
        return None

    return normalized


def list_usb_devices() -> list[dict[str, Any]]:
    # Full IOUSB tree scan (same as manual ioreg), then filter IOUSBHostDevice entries.
    # This includes direct devices and any devices behind hubs/docks.
    output = run_capture(["ioreg", "-p", "IOUSB", "-l", "-w", "0"])
    devices = parse_ioreg_usb_devices(output)

    normalized: list[dict[str, Any]] = []
    for dev in devices:
        parsed = normalize_usb_device(dev)
        if parsed is not None:
            normalized.append(parsed)

    return normalized


def read_simple_yaml(path: Path) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {}
    current: str | None = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current = line[:-1].strip()
            data[current] = {}
            continue
        if current is None:
            continue
        if not line.startswith("  "):
            continue
        kv = line.strip().split(":", 1)
        if len(kv) != 2:
            continue
        key = kv[0].strip()
        value_raw = kv[1].strip()
        value = parse_value(value_raw)
        data[current][key] = value

    return data


def list_volume_mounts() -> list[Path]:
    root = Path("/Volumes")
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())


def list_boot_mounts() -> list[Path]:
    return [p for p in list_volume_mounts() if BOOT_VOLUME_RE.search(p.name)]


def matches_side_identity(side: dict[str, Any], device: dict[str, Any]) -> bool:
    serial_match = (
        bool(side.get("kUSBSerialNumberString"))
        and side.get("kUSBSerialNumberString") == device.get("kUSBSerialNumberString")
    )
    location_match = side.get("locationId") is not None and side.get("locationId") == device.get("locationId")
    return serial_match or location_match


def wait_for_side_mount(
    side_name: str,
    side_identity: dict[str, Any],
    *,
    timeout_s: int,
) -> Path:
    baseline = {p.name for p in list_volume_mounts()}
    deadline = time.time() + timeout_s
    announced = False

    while time.time() < deadline:
        devices = [d for d in list_usb_devices() if matches_side_identity(side_identity, d)]
        mounts = list_boot_mounts()
        new_mounts = [m for m in mounts if m.name not in baseline]

        if not devices:
            if not announced:
                print(f"Waiting for {side_name} USB identity to appear...")
                announced = True
            time.sleep(1.0)
            continue

        if len(new_mounts) == 1:
            return new_mounts[0]
        if len(mounts) == 1:
            return mounts[0]
        if len(mounts) > 1:
            print("Multiple NICENANO volumes mounted. Keep only one connected for safe flashing.")
            time.sleep(1.0)
            continue

        if announced:
            time.sleep(1.0)
        else:
            print(f"Waiting for {side_name} boot volume to mount...")
            announced = True
            time.sleep(1.0)

    die(f"timed out waiting for mounted boot volume for {side_name}")


def resolve_firmware(
    side: str,
    explicit_path: Path | None,
    root_dir: Path,
    build_matrix_path: Path,
    board: str,
) -> Path:
    if explicit_path is not None:
        if not explicit_path.is_file():
            die(f"{side} firmware not found: {explicit_path}")
        return explicit_path

    candidates: list[Path] = []

    if build_matrix_path.is_file():
        try:
            matrix_entries = load_build_matrix(build_matrix_path)
        except (FileNotFoundError, ValueError) as exc:
            die(str(exc))

        side_entries = [entry for entry in matrix_entries if entry.board == board and entry.side == side]
        for entry in side_entries:
            matrix_artifact = root_dir / "firmware" / f"{artifact_name(entry)}.uf2"
            build_subdir = build_dir_name(entry)
            build_uf2 = root_dir / ".zmk" / "zmk" / "build" / build_subdir / "zephyr" / "zmk.uf2"
            candidates.extend([matrix_artifact, build_uf2])

    candidates.extend(
        [
            root_dir / ".zmk" / "zmk" / "build" / f"corne_{side}" / "zephyr" / "zmk.uf2",
            root_dir / ".zmk" / "zmk" / "build" / f"corne_{side}" / "zephyr" / "zephyr.uf2",
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    if candidates:
        tried = "\n  ".join(str(path) for path in candidates[:8])
        die(f"could not find default {side} firmware UF2 (tried:\n  {tried}\n)")
    die(f"could not find default {side} firmware UF2")


def local_build_cmd(board: str, build_matrix: Path | None) -> list[str]:
    """Argv for the build that --build runs before flashing.

    Spawned rather than imported: build_local.main() parses sys.argv itself, so
    in-process it would read this script's arguments, and its die() would exit
    here instead of handing back a code we can refuse to flash on.

    --build-matrix is only forwarded when it was given, so the default stays
    build_local's to resolve rather than being pinned from this side.
    """
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "build_local.py"),
        "--both",
        "--board",
        board,
    ]
    if build_matrix is not None:
        cmd += ["--build-matrix", str(build_matrix)]
    return cmd


def run_local_build(board: str, build_matrix: Path | None) -> None:
    cmd = local_build_cmd(board, build_matrix)
    print("Building both halves first:")
    print(f"  {' '.join(cmd)}\n")
    if subprocess.run(cmd, check=False).returncode != 0:
        die("local build failed; nothing was flashed")
    print()


def flash_file_to_mount(firmware: Path, mount_path: Path) -> None:
    destination = mount_path / FLASH_FILENAME
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if mount_path.is_dir() and os.access(mount_path, os.W_OK):
                break
            time.sleep(0.2)

        try:
            shutil.copyfile(firmware, destination)
            subprocess.run(["sync"], check=False)
            return
        except OSError as exc:
            if exc.errno not in {errno.EIO, errno.ENOENT, errno.EBUSY}:
                raise
            if attempt == max_attempts:
                die(f"failed to copy to {mount_path}: {exc}")
            time.sleep(0.6)


def unmount_volume(mount_path: Path) -> None:
    subprocess.run(["diskutil", "unmount", str(mount_path)], text=True, capture_output=True, check=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_flash_state(path: Path) -> dict[str, Any]:
    """Return the recorded per-side flash state, or {} if absent/unreadable.

    A missing or corrupt state file is not an error: it just means we cannot
    prove a side is up to date, so every side gets flashed.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_flash_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def plan_sides(
    candidates: list[tuple[str, str, Any, Path, str]],
    state: dict[str, Any],
    scope: str,
) -> tuple[list[tuple[str, str, Any, Path, str]], list[tuple[str, str]]]:
    """Split candidate sides into (to_flash, skipped).

    A side is skipped only when scope is "auto" AND we have a recorded digest
    for it AND that digest matches the image about to be flashed AND that
    image is the same artifact the digest was recorded against. Anything
    else -- no state, unreadable state, a changed image, a different build
    variant, scope="both" -- falls through to flashing, so uncertainty always
    costs a flash rather than leaving a half stale.

    The artifact-name check matters because resolve_firmware() falls back
    through several candidate paths: a failed rebuild can leave a previous
    variant's UF2 in place, and matching on the digest alone would treat that
    stale file as proof the side is current.
    """
    to_flash: list[tuple[str, str, Any, Path, str]] = []
    skipped: list[tuple[str, str]] = []
    for candidate in candidates:
        side_name, side_key, _identity, _firmware, digest = candidate
        previous = state.get(side_key) or {}
        same_image = previous.get("uf2_sha256") == digest
        same_artifact = previous.get("firmware") == _firmware.name
        if scope == "auto" and same_image and same_artifact:
            skipped.append((side_name, previous.get("flashed_at", "an earlier run")))
        else:
            to_flash.append(candidate)
    return to_flash, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Flash built Corne UF2 files to primary/secondary sides using saved USB identity")
    parser.add_argument(
        "--env-file",
        default=Path(__file__).resolve().parents[1] / ".env",
        type=Path,
        help="Path to YAML identity file written by identify_sides.py (default: project .env)",
    )
    parser.add_argument("--left-uf2", type=Path, help="Path to left UF2 (default: auto-detect from local build output)")
    parser.add_argument("--right-uf2", type=Path, help="Path to right UF2 (default: auto-detect from local build output)")
    parser.add_argument(
        "--state-file",
        type=Path,
        help=f"Path to the flash state file (default: <repo>/{FLASH_STATE_FILENAME})",
    )
    parser.add_argument(
        "--scope",
        choices=("auto", "both"),
        default="auto",
        help=(
            "auto (default): flash only sides whose UF2 differs from the one last flashed to them. "
            "both: flash both sides regardless"
        ),
    )
    parser.add_argument("--timeout", type=int, default=180, help="Seconds to wait per side (default: 180)")
    parser.add_argument("--no-unmount", action="store_true", help="Do not unmount volumes after copying")
    parser.add_argument("--build-matrix", type=Path, help="Path to build matrix YAML (default: <repo>/build.yaml)")
    parser.add_argument("--board", default=DEFAULT_BOARD, help=f"Board to select from build matrix (default: {DEFAULT_BOARD})")
    parser.add_argument(
        "--build",
        action="store_true",
        help=(
            "Build both halves locally first, then flash what that produces. "
            "Incremental; run build_local.py directly for --pristine"
        ),
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        die("this script currently supports macOS only (uses ioreg and diskutil)")
    if not args.env_file.is_file():
        die(f"missing env file: {args.env_file}. Run scripts/identify_sides.py first.")

    root_dir = Path(__file__).resolve().parents[1]
    build_matrix_path = args.build_matrix or (root_dir / "build.yaml")
    env_data = read_simple_yaml(args.env_file)
    primary = env_data.get("primary")
    secondary = env_data.get("secondary")
    if not primary or not secondary:
        die(f"{args.env_file} must contain 'primary' and 'secondary' sections")
    for key in ("kUSBSerialNumberString", "kUSBAddress", "locationId"):
        if key not in primary or key not in secondary:
            die(f"{args.env_file} is missing required key '{key}' in primary/secondary")

    if args.build:
        run_local_build(args.board, args.build_matrix)

    left_firmware = resolve_firmware("left", args.left_uf2, root_dir, build_matrix_path, args.board)
    right_firmware = resolve_firmware("right", args.right_uf2, root_dir, build_matrix_path, args.board)

    print(f"Using left UF2:  {left_firmware}")
    print(f"Using right UF2: {right_firmware}")

    state_path = args.state_file or (root_dir / FLASH_STATE_FILENAME)
    state = read_flash_state(state_path)

    # A side only needs flashing if its image actually differs from the one
    # already on it. This is exact rather than heuristic: the peripheral is
    # built with CONFIG_ZMK_SPLIT_ROLE_CENTRAL unset, so the keymap is compiled
    # out of it entirely and keymap-only edits leave its UF2 byte-identical.
    candidates = [
        ("primary (left)", "left", primary, left_firmware, sha256_file(left_firmware)),
        ("secondary (right)", "right", secondary, right_firmware, sha256_file(right_firmware)),
    ]
    plan, skipped = plan_sides(candidates, state, args.scope)

    for side_name, flashed_at in skipped:
        print(f"Skipping {side_name}: identical to what was flashed on {flashed_at}.")
    if skipped:
        print("Pass --scope both to flash every side anyway.")

    if not plan:
        print("\nNothing to do: both sides already carry this firmware.")
        return 0

    for side_name, side_key, side_identity, firmware, digest in plan:
        print()
        print(f"Put {side_name} into bootloader mode so NICENANO mounts.")
        input("Press Enter to start detection...")
        mount_path = wait_for_side_mount(side_name, side_identity, timeout_s=args.timeout)
        print(f"Detected {side_name} mount at {mount_path}")
        print(f"Copying {firmware.name} -> {mount_path / FLASH_FILENAME}")
        flash_file_to_mount(firmware, mount_path)
        print(f"Flashed {side_name}.")
        if not args.no_unmount:
            unmount_volume(mount_path)

        # Record after each side so interrupting midway keeps the finished
        # side marked as up to date.
        state[side_key] = {
            "uf2_sha256": digest,
            "firmware": firmware.name,
            "flashed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        write_flash_state(state_path, state)

    print(f"\nDone. Flashed: {', '.join(name for name, _, _, _, _ in plan)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
