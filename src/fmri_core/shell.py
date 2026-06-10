"""Wrap local and remote shell helpers used by the workflow."""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class ShellCommand:
    body: str


@dataclass(frozen=True)
class ArgvCommand:
    parts: tuple[str, ...]


CommandIntent = ShellCommand | ArgvCommand
ENV_KEY_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
_ENV_KEY_RE = re.compile(rf"^{ENV_KEY_PATTERN}$")


def shell_command(body: str) -> ShellCommand:
    if not isinstance(body, str):
        raise TypeError("shell command body must be a string")
    if not body.strip():
        raise ValueError("shell command cannot be empty")
    return ShellCommand(body)


def argv_command(parts: Sequence[str]) -> ArgvCommand:
    values = tuple(parts)
    if not values:
        raise ValueError("argv command cannot be empty")
    if not all(isinstance(part, str) for part in values):
        raise TypeError("argv command parts must be strings")
    return ArgvCommand(values)


def validate_env_key(key: str, *, field: str = "env") -> str:
    if not isinstance(key, str):
        raise ValueError(f"{field} key must be a string")
    if not _ENV_KEY_RE.fullmatch(key):
        raise ValueError(f"invalid {field} key {key!r}; expected {ENV_KEY_PATTERN}")
    return key


def clean_remote_startup_output(text: str) -> str:
    """Remove leading SSH shell startup warnings from a remote output stream."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _is_remote_startup_warning(line):
            continue
        if index > 0 and not line.strip():
            continue
        if index == 0:
            return text
        return "\n".join(lines[index:])
    return "" if lines else text


def _is_remote_startup_warning(line: str) -> bool:
    stripped = line.strip()
    lower = stripped.lower()
    setlocale_warning = (
        lower.startswith(("setlocale:", "warning: setlocale:"))
        or lower.startswith(
            ("bash: warning: setlocale:", "-bash: warning: setlocale:", "sh: warning: setlocale:")
        )
    )
    return (
        (setlocale_warning and ("warning" in lower or "cannot change locale" in lower))
        or (lower.startswith("manpath:") and "locale" in lower)
        or lower.startswith("perl: warning:")
        or stripped in {"LANGUAGE =", "are supported and installed on your system."}
        or stripped.startswith(("LANGUAGE =", "LANG =", "LC_"))
        or lower.startswith("please check that your locale settings")
        or lower.startswith("falling back to the standard locale")
    )


def _remote_command_args(remote_host: str, command: str) -> list[str]:
    return ["ssh", remote_host, f"bash -c {shlex.quote(command)}"]


def _remote_env_command(command: str, env: dict[str, str] | None) -> str:
    if not env:
        return command
    exports = "; ".join(
        f"export {validate_env_key(key)}={_remote_env_value(key, value)}" for key, value in env.items()
    )
    return f"{exports}; {command}"


def _remote_env_value(key: str, value: str) -> str:
    if key == "PATH" and value.endswith(":$PATH"):
        prefix = value[: -len(":$PATH")]
        return f"{shlex.quote(prefix)}:$PATH"
    return shlex.quote(value)


def _clean_remote_stream(value: Any) -> Any:
    if isinstance(value, str):
        return clean_remote_startup_output(value)
    if isinstance(value, bytes):
        return clean_remote_startup_output(value.decode("utf-8", errors="replace")).encode("utf-8")
    return value


def _run_remote_command(
    remote_host: str,
    command: str,
    *,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    input_data: str | bytes | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        _remote_command_args(remote_host, _remote_env_command(command, env)),
        capture_output=capture_output,
        text=text,
        check=False,
        input=input_data,
        timeout=timeout,
    )
    completed.stdout = _clean_remote_stream(completed.stdout)
    completed.stderr = _clean_remote_stream(completed.stderr)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def run_command(
    command: CommandIntent,
    *,
    remote_host: str | None = None,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    input_text: str | bytes | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if not isinstance(command, (ShellCommand, ArgvCommand)):
        raise TypeError("Command intent required: use shell_command(...) or argv_command(...)")
    if remote_host is not None:
        body = command.body if isinstance(command, ShellCommand) else render_shell(list(command.parts))
        return _run_remote_command(
            remote_host,
            body,
            capture_output=capture_output,
            text=text,
            check=check,
            input_data=input_text,
            timeout=timeout,
            env=env,
        )
    if isinstance(command, ShellCommand):
        if _is_native_windows_local():
            raise RuntimeError(
                "Native Windows local shell commands are unsupported; use argv_command or a platform-specific path."
            )
        return subprocess.run(
            ["bash", "-c", command.body],
            capture_output=capture_output,
            text=text,
            check=check,
            input=input_text,
            timeout=timeout,
            env=env,
        )
    return subprocess.run(
        list(command.parts),
        capture_output=capture_output,
        text=text,
        check=check,
        input=input_text,
        timeout=timeout,
        env=env,
    )


def render_shell(parts: list[str]) -> str:
    """Render a safely quoted shell command string.

    Inputs:
        parts (list[str]): Command arguments to execute.

    Returns:
        str: Safely quoted shell command string.
    """
    return " ".join(shlex.quote(part) for part in parts)


def _is_native_windows_local() -> bool:
    return platform.system() == "Windows"


def probe_remote_dataset(
    remote_host: str,
    paths: list[str] | None = None,
    glob_patterns: list[str] | None = None,
    text_paths: list[str] | None = None,
    *,
    image_metadata_backend: str = "nibabel",
    inspect_glob_matches: bool = True,
    include_image_metadata: bool = True,
) -> dict[str, Any]:
    """Collect a batched remote dataset probe."""
    payload, _ = probe_remote_dataset_with_metrics(
        remote_host,
        paths=paths,
        glob_patterns=glob_patterns,
        text_paths=text_paths,
        image_metadata_backend=image_metadata_backend,
        inspect_glob_matches=inspect_glob_matches,
        include_image_metadata=include_image_metadata,
    )
    return payload


def probe_remote_dataset_with_metrics(
    remote_host: str,
    paths: list[str] | None = None,
    glob_patterns: list[str] | None = None,
    text_paths: list[str] | None = None,
    *,
    image_metadata_backend: str = "nibabel",
    inspect_glob_matches: bool = True,
    include_image_metadata: bool = True,
) -> tuple[dict[str, Any], int]:
    """Collect a batched remote dataset probe and report stdout payload bytes."""
    spec = {
        "paths": [str(path) for path in paths or []],
        "globs": [str(pattern) for pattern in glob_patterns or []],
        "texts": [str(path) for path in text_paths or []],
    }
    if image_metadata_backend != "nibabel":
        spec["image_metadata_backend"] = image_metadata_backend
    if not inspect_glob_matches:
        spec["inspect_glob_matches"] = False
    if not include_image_metadata:
        spec["include_image_metadata"] = False
    script = r"""
import glob
import gzip
import json
import os
import struct
import sys
from pathlib import Path

spec = json.loads(sys.stdin.read() or "{}")
result = {"paths": {}, "globs": {}, "texts": {}}
DTYPE_NAMES = {
    2: "uint8",
    4: "int16",
    8: "int32",
    16: "float32",
    64: "float64",
    256: "int8",
    512: "uint16",
    768: "uint32",
}
IMAGE_METADATA_BACKEND = spec.get("image_metadata_backend", "nibabel")
INSPECT_GLOB_MATCHES = bool(spec.get("inspect_glob_matches", True))
INCLUDE_IMAGE_METADATA = bool(spec.get("include_image_metadata", True))


def _find_repetition_time(payload):
    if isinstance(payload, dict):
        if "RepetitionTime" in payload:
            return payload["RepetitionTime"]
        for value in payload.values():
            found = _find_repetition_time(value)
            if found is not None:
                return found
        return None
    if isinstance(payload, list):
        for value in payload:
            found = _find_repetition_time(value)
            if found is not None:
                return found
    return None


def _normalize_repetition_time(value):
    if value is None:
        return None
    repetition_time = float(value)
    if repetition_time > 100:
        return repetition_time / 1000.0
    return repetition_time


def _sidecar_path(path):
    if path.name.endswith(".nii.gz"):
        return path.with_name(path.name[:-7] + ".json")
    if path.suffix == ".nii":
        return path.with_suffix(".json")
    return None


def _load_sidecar_repetition_time(path):
    sidecar_path = _sidecar_path(path)
    if sidecar_path is None or not sidecar_path.exists():
        return None
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _normalize_repetition_time(_find_repetition_time(payload))


def _finalize_image_metadata(path, shape, zooms, bitpix, dtype, repetition_time):
    assumptions = []
    if repetition_time is None:
        assumptions.append("assumption_missing_sidecar_or_tr")
    return {
        "path": str(path),
        "shape": shape,
        "zooms": zooms,
        "timepoints": int(shape[3]) if len(shape) >= 4 else 1,
        "bitpix": int(bitpix),
        "dtype": dtype,
        "repetition_time": repetition_time,
        "assumptions": assumptions,
    }


def _inspect_image_metadata_stdlib(path):
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as handle:
        header = handle.read(348)
    if len(header) < 108:
        return None
    dims = struct.unpack("<8h", header[40:56])
    pixdims = struct.unpack("<8f", header[76:108])
    datatype = struct.unpack("<h", header[70:72])[0]
    bitpix = struct.unpack("<h", header[72:74])[0]
    ndim = max(0, min(int(dims[0]), 7))
    shape = [int(dims[index]) for index in range(1, ndim + 1)]
    zooms = [round(float(pixdims[index]), 6) for index in range(1, ndim + 1)]
    repetition_time = _load_sidecar_repetition_time(path)
    return _finalize_image_metadata(path, shape, zooms, bitpix, DTYPE_NAMES.get(datatype, str(datatype)), repetition_time)


def _inspect_image_metadata_nibabel(path):
    import nibabel as nib

    image = nib.load(str(path))
    header = image.header
    shape = [int(value) for value in image.shape]
    zooms = [round(float(value), 6) for value in header.get_zooms()[: len(shape)]]
    bitpix = int(header["bitpix"])
    dtype = str(image.get_data_dtype().name)
    repetition_time = _load_sidecar_repetition_time(path)
    return _finalize_image_metadata(path, shape, zooms, bitpix, dtype, repetition_time)


def _inspect_image_metadata(path):
    if IMAGE_METADATA_BACKEND == "stdlib":
        return _inspect_image_metadata_stdlib(path)
    if IMAGE_METADATA_BACKEND == "nibabel":
        try:
            return _inspect_image_metadata_nibabel(path)
        except Exception:
            return _inspect_image_metadata_stdlib(path)
    return _inspect_image_metadata_stdlib(path)


def inspect_path(raw_path):
    path = Path(raw_path)
    exists = path.exists()
    size_bytes = None
    image_metadata = None
    if exists:
        try:
            size_bytes = path.stat().st_size if path.is_file() else None
        except OSError:
            size_bytes = None
        if INCLUDE_IMAGE_METADATA and path.is_file() and path.name.endswith((".nii", ".nii.gz")):
            try:
                image_metadata = _inspect_image_metadata(path)
            except Exception:
                image_metadata = None
    return {
        "exists": exists,
        "is_dir": path.is_dir() if exists else False,
        "is_symlink": path.is_symlink(),
        "readable": exists and os.access(path, os.R_OK),
        "size_bytes": size_bytes,
        "image_metadata": image_metadata,
    }


for raw_path in spec.get("paths", []):
    result["paths"][raw_path] = inspect_path(raw_path)

for pattern in spec.get("globs", []):
    matches = sorted(glob.glob(pattern, recursive=True))
    result["globs"][pattern] = matches
    if INSPECT_GLOB_MATCHES:
        for match in matches:
            result["paths"].setdefault(match, inspect_path(match))

for raw_path in spec.get("texts", []):
    path = Path(raw_path)
    result["texts"][raw_path] = path.read_text(encoding="utf-8") if path.exists() else ""

print(json.dumps(result))
"""
    result = run_command(
        shell_command(f"python -c {shlex.quote(script)}"),
        remote_host=remote_host,
        check=True,
        input_text=json.dumps(spec),
    )
    stdout = clean_remote_startup_output(result.stdout or "{}")
    return json.loads(stdout), len(stdout.encode("utf-8"))


def command_available(command: str, remote_host: str | None = None) -> bool:
    """Return whether a command is available.

    Inputs:
        command (str): Command name or shell command string.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        bool: True when the command is available in the target environment.
    """
    if remote_host is None:
        return shutil.which(command) is not None
    result = run_command(
        shell_command(f"command -v {shlex.quote(command)} >/dev/null 2>&1"),
        remote_host=remote_host,
        check=False,
    )
    return result.returncode == 0


def path_exists(path: str | Path, remote_host: str | None = None) -> bool:
    """Return whether a path exists.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        bool: True when the path exists in the target environment.
    """
    if remote_host is None:
        return Path(path).exists()
    target = shlex.quote(str(path))
    result = run_command(shell_command(f"test -e {target}"), remote_host=remote_host, check=False)
    return result.returncode == 0


def glob_exists(pattern: str, remote_host: str | None = None) -> bool:
    """Return whether a glob pattern matches anything.

    Inputs:
        pattern (str): Glob or text pattern to evaluate.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        bool: True when the glob pattern matches at least one path.
    """
    if remote_host is None:
        return bool(glob.glob(pattern, recursive=True))
    result = run_command(
        shell_command(f"compgen -G {shlex.quote(pattern)} >/dev/null 2>&1"),
        remote_host=remote_host,
        check=False,
    )
    return result.returncode == 0


def glob_paths(pattern: str, remote_host: str | None = None) -> list[Path | PurePosixPath]:
    """Return the paths that match one glob pattern.

    Inputs:
        pattern (str): Glob or text pattern to evaluate.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        list[Path]: Paths matched by the glob pattern.
    """
    if remote_host is None:
        return [Path(match) for match in glob.glob(pattern)]
    result = run_command(shell_command(f"compgen -G {shlex.quote(pattern)} || true"), remote_host=remote_host, check=False)
    return [_remote_path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def mkdir_p(path: str | Path, remote_host: str | None = None) -> None:
    """Create a directory locally or remotely.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        None. Ensures that the requested directory exists.
    """
    if remote_host is None:
        Path(path).mkdir(parents=True, exist_ok=True)
        return
    run_command(shell_command(f"mkdir -p {shlex.quote(str(path))}"), remote_host=remote_host, check=True)


def path_is_symlink(path: str | Path, remote_host: str | None = None) -> bool:
    """Return whether a path is a symbolic link.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        bool: True when the path is a symbolic link.
    """
    if remote_host is None:
        return Path(path).is_symlink()
    target = shlex.quote(str(path))
    result = run_command(shell_command(f"test -L {target}"), remote_host=remote_host, check=False)
    return result.returncode == 0


def path_readable(path: str | Path, remote_host: str | None = None) -> bool:
    """Return whether a path is readable.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        bool: True when the path is readable.
    """
    if remote_host is None:
        target = Path(path)
        if not target.exists() or not os.access(target, os.R_OK):
            return False
        try:
            with target.open("rb") as handle:
                handle.read(1)
        except OSError:
            return False
        return True
    target = shlex.quote(str(path))
    result = run_command(
        shell_command(f"test -r {target} && head -c 1 {target} >/dev/null 2>&1"),
        remote_host=remote_host,
        check=False,
    )
    return result.returncode == 0


def path_writable(path: str | Path, remote_host: str | None = None) -> bool:
    """Return whether a directory path exists or can be created by the current account."""
    if remote_host is None:
        target = Path(path)
        if target.exists():
            return target.is_dir() and os.access(target, os.W_OK | os.X_OK)
        parent = target.parent
        while not parent.exists():
            if parent == parent.parent:
                return False
            parent = parent.parent
        return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)

    target = shlex.quote(str(path))
    command = f"""
target={target}
if [ -e "$target" ]; then
  test -d "$target" && test -w "$target" && test -x "$target"
else
  parent="$(dirname "$target")"
  while [ ! -e "$parent" ]; do
    next="$(dirname "$parent")"
    if [ "$next" = "$parent" ]; then
      exit 1
    fi
    parent="$next"
  done
  test -d "$parent" && test -w "$parent" && test -x "$parent"
fi
""".strip()
    result = run_command(shell_command(command), remote_host=remote_host, check=False)
    return result.returncode == 0


def write_text(path: str | Path, content: str, remote_host: str | None = None) -> None:
    """Write text locally or remotely.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.
        content (str): Text content to write or inspect.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        None. Writes text content locally or remotely.
    """
    if remote_host is None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return
    target = str(path)
    mkdir_p(PurePosixPath(target).parent, remote_host=remote_host)
    _run_remote_command(
        remote_host,
        f"cat > {shlex.quote(target)}",
        input_data=content.encode("utf-8"),
        text=False,
        check=True,
    )


def read_text(path: str | Path, remote_host: str | None = None) -> str:
    """Read text locally or remotely.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        str: Text content read from the requested path.
    """
    if remote_host is None:
        target = Path(path)
        return target.read_text(encoding="utf-8")
    result = run_command(shell_command(f"cat {shlex.quote(str(path))}"), remote_host=remote_host, check=True)
    return result.stdout


def read_texts(paths: list[str | Path], remote_host: str | None = None) -> dict[str, dict[str, str | bool | None]]:
    """Read multiple text files and preserve missing-vs-empty status."""
    if remote_host is None:
        result: dict[str, dict[str, str | bool | None]] = {}
        for raw_path in paths:
            path = Path(raw_path)
            key = str(raw_path)
            if not path.exists():
                result[key] = {"exists": False, "text": None}
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = None
            result[key] = {"exists": True, "text": text}
        return result

    script = r"""
import json
import sys
from pathlib import Path

paths = json.loads(sys.stdin.read() or "[]")
result = {}
for raw_path in paths:
    path = Path(raw_path)
    if not path.exists():
        result[raw_path] = {"exists": False, "text": None}
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = None
    result[raw_path] = {"exists": True, "text": text}
print(json.dumps(result))
"""
    completed = run_command(
        shell_command(f"python -c {shlex.quote(script)}"),
        remote_host=remote_host,
        check=True,
        input_text=json.dumps([str(path) for path in paths]),
    )
    return json.loads(clean_remote_startup_output(completed.stdout or "{}"))


def _remote_path(value: str) -> Path | PurePosixPath:
    return PurePosixPath(value) if value.startswith("/") else Path(value)
