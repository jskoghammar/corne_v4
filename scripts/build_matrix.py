#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildMatrixEntry:
    board: str
    shield: str
    snippet: str | None = None
    artifact_name: str | None = None
    cmake_args: str | None = None

    @property
    def side(self) -> str | None:
        root_shield = self.shield.split()[0] if self.shield else ""
        if root_shield.endswith("_left"):
            return "left"
        if root_shield.endswith("_right"):
            return "right"
        return None


def _parse_scalar(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def load_build_matrix(path: Path) -> list[BuildMatrixEntry]:
    if not path.is_file():
        raise FileNotFoundError(f"build matrix file not found: {path}")

    entries_raw: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_include = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or stripped == "---":
            continue

        indent = len(line) - len(line.lstrip(" "))

        if indent == 0 and stripped.endswith(":"):
            if stripped[:-1] == "include":
                in_include = True
                continue
            if in_include:
                break
            continue

        if not in_include:
            continue

        if indent == 2 and stripped.startswith("-"):
            if current is not None:
                entries_raw.append(current)
            current = {}
            item = stripped[1:].strip()
            if item:
                if ":" not in item:
                    raise ValueError(f"unsupported list item in {path}: {line}")
                key, value = item.split(":", 1)
                current[key.strip()] = _parse_scalar(value)
            continue

        if current is None or indent < 4:
            continue

        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = _parse_scalar(value)

    if current is not None:
        entries_raw.append(current)

    entries: list[BuildMatrixEntry] = []
    for item in entries_raw:
        board = item.get("board", "").strip()
        shield = item.get("shield", "").strip()
        if not board or not shield:
            continue
        entries.append(
            BuildMatrixEntry(
                board=board,
                shield=shield,
                snippet=item.get("snippet") or None,
                artifact_name=item.get("artifact-name") or None,
                cmake_args=item.get("cmake-args") or None,
            )
        )

    if not entries:
        raise ValueError(f"no usable include entries found in {path}")
    return entries


def artifact_name(entry: BuildMatrixEntry) -> str:
    return entry.artifact_name or f"{entry.shield}-{entry.board}-zmk"


def build_dir_name(entry: BuildMatrixEntry) -> str:
    scope = entry.shield
    if entry.snippet:
        scope = f"{scope} snippet {entry.snippet}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", scope).strip("_")


def select_entries(
    entries: list[BuildMatrixEntry],
    *,
    board: str,
    target: str,
    all_variants: bool,
) -> list[BuildMatrixEntry]:
    if target not in {"left", "right", "both"}:
        raise ValueError(f"invalid target: {target}")

    sides = ["left", "right"] if target == "both" else [target]
    selected: list[BuildMatrixEntry] = []
    for side in sides:
        side_entries = [entry for entry in entries if entry.board == board and entry.side == side]
        if not side_entries:
            raise ValueError(f"no build.yaml include entry for board={board} side={side}")
        if all_variants:
            selected.extend(side_entries)
        else:
            selected.append(side_entries[0])
    return selected
