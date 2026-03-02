#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NICE_VENDOR_ID = 9114  # 0x239A
KEYBOARD_HINT_KEYWORDS = ("nice", "nano", "uf2", "nrf", "adafruit")
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

    # Ignore infrastructure/controller blocks that do not represent a USB device.
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


def is_keyboard_candidate(device: dict[str, Any]) -> bool:
    if device.get("vendorId") == NICE_VENDOR_ID:
        return True

    searchable = f'{device.get("vendor", "")} {device.get("product", "")}'.lower()
    return any(keyword in searchable for keyword in KEYBOARD_HINT_KEYWORDS)


def device_key(device: dict[str, Any]) -> tuple[str, int | None, int | None, str]:
    return (
        device["kUSBSerialNumberString"],
        device["locationId"],
        device["kUSBAddress"],
        device["product"],
    )


def format_device(device: dict[str, Any]) -> str:
    return (
        f'serial={device["kUSBSerialNumberString"]} '
        f'address={device["kUSBAddress"]} '
        f'locationId={device["locationId"]} '
        f'vendor="{device.get("vendor", "")}" '
        f'product="{device["product"]}"'
    )


def pick_device_interactively(
    role: str,
    *,
    baseline_keys: set[tuple[str, int | None, int | None, str]],
    exclude_keys: set[tuple[str, int | None, int | None, str]],
    timeout_s: int,
    debug: bool = False,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_count = -1
    last_snapshot: set[tuple[str, int | None, int | None, str]] | None = None

    while time.time() < deadline:
        all_devices = [d for d in list_usb_devices() if device_key(d) not in exclude_keys]
        candidate_devices = [d for d in all_devices if is_keyboard_candidate(d)]
        new_candidate_devices = [d for d in candidate_devices if device_key(d) not in baseline_keys]
        snapshot = {device_key(d) for d in all_devices}

        if debug and snapshot != last_snapshot:
            print(
                f"[debug] {role}: usb_devices={len(all_devices)} "
                f"keyboard_candidates={len(candidate_devices)}"
            )
            for dev in all_devices:
                label = " [candidate]" if is_keyboard_candidate(dev) else ""
                print(f"  - {format_device(dev)}{label}")
            last_snapshot = snapshot

        if len(new_candidate_devices) == 1:
            chosen = new_candidate_devices[0]
            print(f"Detected {role}: {format_device(chosen)}")
            return chosen

        if len(candidate_devices) == 1:
            chosen = candidate_devices[0]
            print(f"Detected {role}: {format_device(chosen)}")
            return chosen

        if len(candidate_devices) > 1:
            print(f"Multiple keyboard-like devices detected for {role}:")
            for i, dev in enumerate(candidate_devices, start=1):
                print(f"  {i}. {format_device(dev)}")
            selected = input("Choose device number and press Enter: ").strip()
            if selected.isdigit():
                index = int(selected)
                if 1 <= index <= len(candidate_devices):
                    chosen = candidate_devices[index - 1]
                    print(f"Selected {role}: {format_device(chosen)}")
                    return chosen
            print("Invalid selection. Retrying...")
            time.sleep(0.5)
            continue

        new_usb_devices = [d for d in all_devices if device_key(d) not in baseline_keys]
        if len(new_usb_devices) == 1:
            chosen = new_usb_devices[0]
            print(f'Detected {role} (fallback USB match): {format_device(chosen)}')
            return chosen

        if len(new_usb_devices) > 1:
            print(f"Multiple new USB devices detected for {role}:")
            for i, dev in enumerate(new_usb_devices, start=1):
                print(f"  {i}. {format_device(dev)}")
            selected = input("Choose device number and press Enter: ").strip()
            if selected.isdigit():
                index = int(selected)
                if 1 <= index <= len(new_usb_devices):
                    chosen = new_usb_devices[index - 1]
                    print(f"Selected {role}: {format_device(chosen)}")
                    return chosen
            print("Invalid selection. Retrying...")
            time.sleep(0.5)
            continue

        if not candidate_devices:
            if last_count != 0:
                print(f"No matching keyboard device found for {role}. Waiting...")
                last_count = 0
            time.sleep(1.0)
            continue
        time.sleep(1.0)

    die(f"timed out while waiting for {role} device")


def unmount_boot_volumes() -> None:
    volumes_dir = Path("/Volumes")
    if not volumes_dir.is_dir():
        return

    to_unmount = [p for p in volumes_dir.iterdir() if p.is_dir() and BOOT_VOLUME_RE.search(p.name)]
    if not to_unmount:
        return

    for mount in to_unmount:
        subprocess.run(["diskutil", "unmount", str(mount)], text=True, capture_output=True, check=False)


def write_env_yaml(path: Path, primary: dict[str, Any], secondary: dict[str, Any]) -> None:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = [
        "primary:",
        '  side: "left"',
        f'  kUSBSerialNumberString: "{primary["kUSBSerialNumberString"]}"',
        f'  kUSBAddress: {primary["kUSBAddress"]}',
        f'  locationId: {primary["locationId"]}',
        "secondary:",
        '  side: "right"',
        f'  kUSBSerialNumberString: "{secondary["kUSBSerialNumberString"]}"',
        f'  kUSBAddress: {secondary["kUSBAddress"]}',
        f'  locationId: {secondary["locationId"]}',
        "meta:",
        f'  generatedAt: "{generated}"',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Identify primary/secondary Corne sides via ioreg and write .env YAML")
    parser.add_argument(
        "--env-file",
        default=Path(__file__).resolve().parents[1] / ".env",
        type=Path,
        help="Output YAML file path (default: project .env)",
    )
    parser.add_argument("--timeout", type=int, default=120, help="Seconds to wait per side (default: 120)")
    parser.add_argument("--debug", action="store_true", help="Print detected USB devices while scanning")
    args = parser.parse_args()

    if sys.platform != "darwin":
        die("this script currently supports macOS only (uses ioreg and diskutil)")

    print("Disconnect both halves.")
    input("Press Enter to capture baseline with both halves disconnected...")
    baseline = {device_key(d) for d in list_usb_devices()}
    print("Connect only PRIMARY (left).")
    input("Press Enter to start scanning PRIMARY...")

    primary = pick_device_interactively(
        "primary (left)",
        baseline_keys=baseline,
        exclude_keys=set(),
        timeout_s=args.timeout,
        debug=args.debug,
    )

    print("Attempting to unmount any mounted NICENANO volumes...")
    unmount_boot_volumes()

    print("Disconnect PRIMARY and keep SECONDARY disconnected for baseline capture.")
    input("Press Enter to capture baseline before connecting SECONDARY...")
    secondary_baseline = {device_key(d) for d in list_usb_devices()}
    print("Connect only SECONDARY (right).")
    input("Press Enter to start scanning SECONDARY...")
    secondary = pick_device_interactively(
        "secondary (right)",
        baseline_keys=secondary_baseline,
        exclude_keys={device_key(primary)},
        timeout_s=args.timeout,
        debug=args.debug,
    )

    args.env_file.parent.mkdir(parents=True, exist_ok=True)
    write_env_yaml(args.env_file, primary, secondary)
    print(f"\nWrote USB identity data to {args.env_file}")
    print(f"PRIMARY:   {format_device(primary)}")
    print(f"SECONDARY: {format_device(secondary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
