#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BOOT_VOLUME_RE = re.compile(r"^nice[ _-]?nano", re.IGNORECASE)


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


def resolve_firmware(side: str, explicit_path: Path | None, root_dir: Path) -> Path:
    if explicit_path is not None:
        if not explicit_path.is_file():
            die(f"{side} firmware not found: {explicit_path}")
        return explicit_path

    candidates = [
        root_dir / ".zmk" / "zmk" / "build" / f"corne_{side}" / "zephyr" / "zmk.uf2",
        root_dir / ".zmk" / "zmk" / "build" / f"corne_{side}" / "zephyr" / "zephyr.uf2",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    die(f"could not find default {side} firmware UF2 (looked in {candidates[0].parent})")


def flash_file_to_mount(firmware: Path, mount_path: Path) -> None:
    destination = mount_path / firmware.name
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if mount_path.is_dir() and os.access(mount_path, os.W_OK):
                break
            time.sleep(0.2)

        try:
            shutil.copy2(firmware, destination)
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
    parser.add_argument("--timeout", type=int, default=180, help="Seconds to wait per side (default: 180)")
    parser.add_argument("--no-unmount", action="store_true", help="Do not unmount volumes after copying")
    args = parser.parse_args()

    if sys.platform != "darwin":
        die("this script currently supports macOS only (uses ioreg and diskutil)")
    if not args.env_file.is_file():
        die(f"missing env file: {args.env_file}. Run scripts/identify_sides.py first.")

    root_dir = Path(__file__).resolve().parents[1]
    env_data = read_simple_yaml(args.env_file)
    primary = env_data.get("primary")
    secondary = env_data.get("secondary")
    if not primary or not secondary:
        die(f"{args.env_file} must contain 'primary' and 'secondary' sections")
    for key in ("kUSBSerialNumberString", "kUSBAddress", "locationId"):
        if key not in primary or key not in secondary:
            die(f"{args.env_file} is missing required key '{key}' in primary/secondary")

    left_firmware = resolve_firmware("left", args.left_uf2, root_dir)
    right_firmware = resolve_firmware("right", args.right_uf2, root_dir)

    print(f"Using left UF2:  {left_firmware}")
    print(f"Using right UF2: {right_firmware}")

    plan = [
        ("primary (left)", primary, left_firmware),
        ("secondary (right)", secondary, right_firmware),
    ]

    for side_name, side_identity, firmware in plan:
        print()
        print(f"Put {side_name} into bootloader mode so NICENANO mounts.")
        input("Press Enter to start detection...")
        mount_path = wait_for_side_mount(side_name, side_identity, timeout_s=args.timeout)
        print(f"Detected {side_name} mount at {mount_path}")
        print(f"Copying {firmware.name} -> {mount_path}")
        flash_file_to_mount(firmware, mount_path)
        print(f"Flashed {side_name}.")
        if not args.no_unmount:
            unmount_volume(mount_path)

    print("\nDone. Both sides flashed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
