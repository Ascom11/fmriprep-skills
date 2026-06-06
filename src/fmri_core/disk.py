"""Inspect host-backed disk space for local runtime and dataset reporting."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


GB = 1024**3
WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def detect_wsl2() -> bool:
    """Return whether the current process is running under WSL2."""
    if os.name == "nt":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    version = Path("/proc/version")
    if version.exists():
        content = version.read_text(encoding="utf-8", errors="ignore").lower()
        return "microsoft" in content
    return False


def detect_wsl_distro_name() -> str | None:
    """Detect the current WSL distro name."""
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro:
        return distro.strip() or None
    if not detect_wsl2():
        return None
    result = _run_windows_command(["wsl.exe", "-l", "-v"])
    if result is None or result.returncode != 0:
        return None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("*"):
            continue
        tokens = line[1:].strip().split()
        if len(tokens) >= 3:
            return " ".join(tokens[:-2]).strip() or None
    return None


def describe_storage_target(
    path_hint: Path | str,
    *,
    wsl_vhdx_path: Path | None = None,
    windows_host_drive: str | None = None,
    allow_wsl_vhdx_scan: bool = False,
) -> dict[str, Any]:
    """Resolve the real host-backed storage target for one local path."""
    raw_path = str(path_hint)
    candidate = _normalize_input_path(path_hint)
    existing = _nearest_existing_path(candidate)
    mounted_drive = _mounted_windows_drive(existing)

    if detect_wsl2():
        if mounted_drive:
            host = _host_drive_report(mounted_drive, "mounted_windows_path")
            target = _storage_target_from_host(existing, "windows-drive", host, "mounted_windows_path")
            target.update(_filesystem_fields(existing, windows_drive=mounted_drive))
            return target
        host = _resolve_wsl_host_report(
            wsl_vhdx_path=wsl_vhdx_path,
            windows_host_drive=windows_host_drive,
            allow_wsl_vhdx_scan=allow_wsl_vhdx_scan,
        )
        target = _storage_target_from_host(existing, "wsl-vhdx-host", host, "wsl_vhdx_host_drive")
        target.update(_filesystem_fields(existing))
        return target

    if WINDOWS_PATH_RE.match(raw_path) and os.name == "nt":
        windows_path = Path(raw_path)
        usage = shutil.disk_usage(windows_path if windows_path.exists() else windows_path.anchor)
        drive = _normalize_drive(raw_path[0])
        filesystem = _windows_drive_filesystem(drive)
        return {
            "path": str(windows_path),
            "volume_key": f"windows-drive:{drive or 'unknown'}",
            "volume_kind": "windows_native_path",
            "volume_label": _drive_label(drive) or str(windows_path.anchor or windows_path),
            "free_gb": round(usage.free / GB, 2),
            "total_gb": round(usage.total / GB, 2),
            "host_drive": drive,
            "filesystem": filesystem,
            "filesystem_source": "windows_drive_query" if filesystem else None,
            "source": "windows_native_path",
        }

    if WINDOWS_PATH_RE.match(raw_path):
        drive = _normalize_drive(raw_path[0])
        host = _host_drive_report(drive, "windows_native_path")
        target = _storage_target_from_host(existing, "windows-drive", host, "windows_native_path")
        target.update(_filesystem_fields(existing, windows_drive=drive))
        return target

    usage = shutil.disk_usage(existing)
    try:
        volume_key = f"linux-device:{existing.stat().st_dev}"
    except OSError:
        volume_key = f"linux-path:{existing.anchor or existing}"
    filesystem = _local_filesystem_type(existing)
    return {
        "path": str(existing),
        "volume_key": volume_key,
        "volume_kind": "linux_native_path",
        "volume_label": str(existing.anchor or existing),
        "free_gb": round(usage.free / GB, 2),
        "total_gb": round(usage.total / GB, 2),
        "host_drive": None,
        "filesystem": filesystem,
        "filesystem_source": "posix_stat" if filesystem else None,
        "source": "linux_native_path",
    }


def _resolve_wsl_host_report(
    *,
    wsl_vhdx_path: Path | None,
    windows_host_drive: str | None,
    allow_wsl_vhdx_scan: bool,
) -> dict[str, Any] | None:
    drive = _normalize_drive(windows_host_drive)
    if drive:
        return _host_drive_report(drive, "wsl_vhdx_host_drive")
    if wsl_vhdx_path is not None:
        drive = _host_drive_from_path(_normalize_input_path(wsl_vhdx_path))
        return _host_drive_report(drive, "wsl_vhdx_host_drive") if drive else None
    if not allow_wsl_vhdx_scan:
        return None
    localappdata_path = _localappdata_path(_get_windows_localappdata())
    vhdx_path, source, _ = _resolve_wsl_vhdx_path(detect_wsl_distro_name(), localappdata_path, None)
    drive = _host_drive_from_path(vhdx_path)
    return _host_drive_report(drive, source) if drive else None


def _resolve_wsl_vhdx_path(
    distro_name: str | None,
    localappdata_path: Path | None,
    override_path: Path | None,
) -> tuple[Path | None, str | None, str]:
    if override_path is not None:
        return _normalize_input_path(override_path), "override", "complete"
    if localappdata_path is None:
        return None, "windows_localappdata_unavailable", "unknown"
    candidates = _find_package_vhdx_candidates(localappdata_path)
    selected = _select_candidate(candidates, distro_name)
    if selected is not None:
        return selected, "packages_scan", "complete"
    if candidates:
        return None, "packages_scan_ambiguous", "partial"
    return None, "packages_scan_empty", "partial"


def _storage_target_from_host(
    path: Path,
    key_prefix: str,
    host_report: dict[str, Any] | None,
    source: str,
) -> dict[str, Any]:
    drive = host_report.get("drive") if host_report else None
    return {
        "path": str(path),
        "volume_key": f"{key_prefix}:{drive or 'unknown'}",
        "volume_kind": source,
        "volume_label": _drive_label(drive) if drive else str(path.anchor or path),
        "free_gb": _round_optional(host_report.get("free_gb") if host_report else None),
        "total_gb": _round_optional(host_report.get("total_gb") if host_report else None),
        "host_drive": drive,
        "source": source,
    }


def _filesystem_fields(path: Path, windows_drive: str | None = None) -> dict[str, Any]:
    if windows_drive:
        filesystem = _windows_drive_filesystem(windows_drive)
        if filesystem:
            return {
                "filesystem": filesystem,
                "filesystem_source": "windows_drive_query",
            }
    filesystem = _local_filesystem_type(path)
    return {
        "filesystem": filesystem,
        "filesystem_source": "posix_stat" if filesystem else None,
    }


def _find_package_vhdx_candidates(localappdata_path: Path) -> list[Path]:
    packages_root = localappdata_path / "Packages"
    if not packages_root.exists():
        return []
    try:
        return sorted(packages_root.glob("*/LocalState/ext4.vhdx"))
    except OSError:
        return []


def _select_candidate(candidates: list[Path], distro_name: str | None) -> Path | None:
    if not candidates:
        return None
    if distro_name:
        normalized = _normalize_name(distro_name)
        matched = [candidate for candidate in candidates if normalized and normalized in _normalize_name(str(candidate))]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _normalize_input_path(path: Path | str) -> Path:
    text = str(path)
    if WINDOWS_PATH_RE.match(text) and os.name != "nt":
        converted = _windows_to_wsl_path(text)
        return converted if converted is not None else Path(text)
    return Path(text)


def _localappdata_path(localappdata_windows: str | None) -> Path | None:
    if not localappdata_windows:
        return None
    if os.name == "nt":
        return Path(localappdata_windows)
    return _windows_to_wsl_path(localappdata_windows)


def _normalize_drive(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().rstrip(":").strip()
    if not stripped:
        return None
    return stripped[0].upper()


def _mounted_windows_drive(path: Path) -> str | None:
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return parts[2].upper()
    return None


def _host_drive_from_path(path: Path | None) -> str | None:
    if path is None:
        return None
    text = str(path)
    if WINDOWS_PATH_RE.match(text):
        return _normalize_drive(text[0])
    return _mounted_windows_drive(path)


def _host_drive_report(drive: str | None, source: str | None) -> dict[str, Any] | None:
    normalized = _normalize_drive(drive)
    if normalized is None:
        return None
    if os.name == "nt":
        root = Path(f"{normalized}:\\")
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            return {
                "drive": normalized,
                "path": str(root),
                "free_gb": None,
                "total_gb": None,
                "source": source,
            }
        return {
            "drive": normalized,
            "path": str(root),
            "free_gb": round(usage.free / GB, 2),
            "total_gb": round(usage.total / GB, 2),
            "source": source,
        }
    mount_path = Path("/mnt") / normalized.lower()
    if not mount_path.exists():
        return {
            "drive": normalized,
            "path": str(mount_path),
            "free_gb": None,
            "total_gb": None,
            "source": source,
        }
    usage = shutil.disk_usage(mount_path)
    return {
        "drive": normalized,
        "path": str(mount_path),
        "free_gb": round(usage.free / GB, 2),
        "total_gb": round(usage.total / GB, 2),
        "source": source,
    }


def _windows_drive_filesystem(drive: str | None) -> str | None:
    normalized = _normalize_drive(drive)
    if normalized is None:
        return None
    command = (
        f"$disk = Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='{normalized}:'\"; "
        "if ($disk) { $disk.FileSystem }"
    )
    result = _run_windows_command(["powershell.exe", "-NoProfile", "-Command", command])
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _local_filesystem_type(path: Path) -> str | None:
    if os.name == "nt":
        return None
    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _get_windows_localappdata() -> str | None:
    result = _run_windows_command(["cmd.exe", "/c", "echo", "%LOCALAPPDATA%"])
    if result and result.returncode == 0:
        value = result.stdout.strip()
        if value and "%" not in value:
            return value
    result = _run_windows_command(
        ["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('LocalApplicationData')"]
    )
    if result and result.returncode == 0:
        value = result.stdout.strip()
        if value:
            return value
    return None


def _windows_to_wsl_path(path: str | None) -> Path | None:
    if not path:
        return None
    normalized = path.replace("\\", "/")
    if len(normalized) < 2 or normalized[1] != ":":
        return Path(normalized)
    drive = normalized[0].lower()
    rest = normalized[2:].lstrip("/")
    if rest:
        return Path("/mnt") / drive / rest
    return Path("/mnt") / drive


def _run_windows_command(parts: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(parts, capture_output=True, text=True, check=False)
    except Exception:  # noqa: BLE001
        return None


def _nearest_existing_path(path_hint: Path) -> Path:
    candidate = path_hint
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else Path("/")


def _drive_label(drive: str | None) -> str | None:
    normalized = _normalize_drive(drive)
    return f"{normalized}:" if normalized else None


def _round_optional(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _dedupe(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered
