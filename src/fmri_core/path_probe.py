"""Probe user-provided path hints before running the workflow CLI."""

from __future__ import annotations

import fnmatch
import os
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from fmri_core.shell import run_command, shell_command

MAX_DEPTH = 2
REMOTE_PATH_PROBE_TIMEOUT_SECONDS = 20
REMOTE_MARKER = "__fmri_path_probe__:"
IMAGE_REF_PREFIXES = ("docker://", "library://", "oras://", "http://", "https://")
VALID_PIPELINES = {"fmriprep", "xcpd"}
FMRIPREP_PATH_NAMES = {"dataset", "output_root", "templateflow_home", "fs_license", "fmriprep_image"}
XCPD_PATH_NAMES = {"bids_root", "fmriprep_derivatives", "output_root", "templateflow_home", "fs_license", "xcpd_image"}
XCPD_REQUIRED_PATH_NAMES = {"fmriprep_derivatives", "output_root"}
VALID_PATH_NAMES = FMRIPREP_PATH_NAMES | XCPD_PATH_NAMES
IMAGE_PATTERNS = {
    "fmriprep": ["*fmriprep*.sif", "*fmriprep*.simg"],
    "xcpd": ["*xcpd*.sif", "*xcpd*.simg", "*xcp_d*.sif", "*xcp_d*.simg"],
}


class RemotePathProbeError(RuntimeError):
    """Raised when the remote SSH probe itself failed."""


def run_path_probe(
    *,
    target: str = "fmriprep",
    bids_root: str | Path | PurePosixPath | None = None,
    user_dataset_path: str | Path | PurePosixPath | None = None,
    output_root: str | Path | PurePosixPath | None = None,
    user_templateflow_path: str | Path | PurePosixPath | None = None,
    fs_license: str | Path | PurePosixPath | None = None,
    fmriprep_image: str | Path | PurePosixPath | None = None,
    xcpd_image: str | Path | PurePosixPath | None = None,
    remote_host: str | None = None,
    required_paths: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    if target not in VALID_PIPELINES:
        raise ValueError("target must be one of: fmriprep, xcpd")
    required = set(required_paths or [])
    valid_required = FMRIPREP_PATH_NAMES if target == "fmriprep" else XCPD_REQUIRED_PATH_NAMES
    invalid = sorted(required - valid_required)
    if invalid:
        raise ValueError(f"required path is not valid for {target}: {', '.join(invalid)}")
    host_target = _target(remote_host)
    if target == "xcpd":
        return _run_xcpd_path_probe(
            host_target=host_target,
            remote_host=remote_host,
            bids_root=bids_root,
            user_dataset_path=user_dataset_path,
            output_root=output_root,
            user_templateflow_path=user_templateflow_path,
            fs_license=fs_license,
            xcpd_image=xcpd_image,
            required_paths=required,
        )
    raw_matches = _collect_matches(
        host_target=host_target,
        remote_host=remote_host,
        bids_root=bids_root,
        user_dataset_path=user_dataset_path,
        output_root=output_root,
        user_templateflow_path=user_templateflow_path,
        fs_license=fs_license,
        fmriprep_image=fmriprep_image,
    )
    paths = {
        "dataset": _dataset_result(
            bids_root,
            user_dataset_path,
            raw_matches["dataset_description"],
            host_target,
            exact_matches=raw_matches.get("dataset_description_exact", []),
            required="dataset" in required,
        ),
        "output_root": _output_root_result(
            output_root,
            raw_matches["output_root"],
            host_target,
            raw_matches.get("output_root_status", []),
            required="output_root" in required,
        ),
        "templateflow_home": _templateflow_result(
            user_templateflow_path,
            raw_matches["templateflow_tpl"],
            host_target,
            exact_matches=raw_matches.get("templateflow_tpl_exact", []),
            required="templateflow_home" in required,
        ),
        "fs_license": _file_result(
            fs_license,
            raw_matches["fs_license"],
            host_target,
            exact_matches=raw_matches.get("fs_license_exact", []),
            required="fs_license" in required,
        ),
        "fmriprep_image": _image_result(
            fmriprep_image,
            raw_matches["fmriprep_image"],
            host_target,
            pipeline="fmriprep",
            exact_matches=raw_matches.get("fmriprep_image_exact", []),
            required="fmriprep_image" in required,
        ),
    }
    preflight = _preflight_decision(paths)
    return {
        "status": _overall_status(paths),
        "command": "path-probe",
        "pipeline": target,
        "probe_target": host_target,
        "remote_host": remote_host,
        **preflight,
        "paths": paths,
    }


def _run_xcpd_path_probe(
    *,
    host_target: str,
    remote_host: str | None,
    bids_root: str | Path | PurePosixPath | None,
    user_dataset_path: str | Path | PurePosixPath | None,
    output_root: str | Path | PurePosixPath | None,
    user_templateflow_path: str | Path | PurePosixPath | None,
    fs_license: str | Path | PurePosixPath | None,
    xcpd_image: str | Path | PurePosixPath | None,
    required_paths: set[str],
) -> dict[str, Any]:
    xcpd_bids_root = bids_root or _bids_root_from_fmriprep_derivatives_hint(user_dataset_path)
    xcpd_output_root = output_root or _output_root_from_fmriprep_derivatives_hint(user_dataset_path)
    dataset_values = _xcpd_dataset_values(bids_root, user_dataset_path)
    explicit_fmriprep_derivatives = "fmriprep_derivatives" in required_paths and user_dataset_path is not None
    fmriprep_derivatives_hint = (
        str(_path_obj(user_dataset_path)) if explicit_fmriprep_derivatives else _fmriprep_derivatives_hint(user_dataset_path)
    )
    direct_bids_root_hint = None if bids_root is not None or fmriprep_derivatives_hint else user_dataset_path
    fmriprep_bids_roots = _dedupe(_provided_values(xcpd_bids_root, direct_bids_root_hint))
    roots = {
        "dataset_description": _path_roots(*dataset_values),
        "fmriprep_derivatives": _fmriprep_derivatives_roots(
            fmriprep_bids_roots,
            xcpd_output_root,
            fmriprep_derivatives_hint=fmriprep_derivatives_hint,
        ),
        "templateflow_tpl": _path_roots(user_templateflow_path)
        + _download_roots(xcpd_bids_root, xcpd_output_root, "templateflow"),
        "fs_license": _path_roots(fs_license) + _parent_roots(xcpd_bids_root),
        "xcpd_image": _image_roots(xcpd_image, xcpd_bids_root, xcpd_output_root, host_target),
        "output_root": _output_roots(xcpd_output_root),
    }
    specs = {
        "dataset_description": ("file", ["dataset_description.json"]),
        "fmriprep_derivatives": ("file", ["dataset_description.json"]),
        "templateflow_tpl": ("dir", ["tpl-*"]),
        "fs_license": ("file", ["license.txt"]),
        "xcpd_image": ("file", IMAGE_PATTERNS["xcpd"]),
        "output_root": ("dir", [_output_pattern(output_root)] if output_root else []),
    }
    if host_target == "remote":
        roots["output_root_status"] = [str(xcpd_output_root)] if xcpd_output_root is not None else []
        specs["output_root_status"] = ("status", [])
        raw_matches = _remote_find(
            roots,
            specs,
            remote_host,
            exact_specs={
                "dataset_description": _remote_dataset_exact_commands(*dataset_values),
                "fmriprep_derivatives": _remote_fmriprep_derivatives_exact_commands(
                    fmriprep_bids_roots,
                    xcpd_output_root,
                    fmriprep_derivatives_hint=fmriprep_derivatives_hint,
                ),
                "templateflow_tpl": _remote_templateflow_exact_commands(user_templateflow_path),
                "fs_license": _remote_file_exact_commands(fs_license),
                "xcpd_image": _remote_image_exact_commands(xcpd_image),
            },
        )
    else:
        exact_matches = {
            "dataset_description": _first_exact_dataset(dataset_values, host_target),
            "fmriprep_derivatives": _exact_fmriprep_derivatives(
                fmriprep_bids_roots,
                xcpd_output_root,
                host_target,
                fmriprep_derivatives_hint=fmriprep_derivatives_hint,
            ),
            "templateflow_tpl": _exact_templateflow(user_templateflow_path, host_target),
            "fs_license": _exact_file(fs_license, host_target),
            "xcpd_image": None if _image_ref(xcpd_image) else _exact_image(xcpd_image, host_target, pipeline="xcpd"),
            "output_root": _exact_output_root(xcpd_output_root, host_target),
        }
        raw_matches = {f"{key}_exact": [value] if value is not None else [] for key, value in exact_matches.items()}
        for key, (kind, patterns) in specs.items():
            raw_matches[key] = (
                []
                if exact_matches.get(key) is not None
                else _find_matches(roots[key], kind=kind, patterns=patterns, target=host_target)
            )
    xcpd_effective_bids_root = xcpd_bids_root
    if xcpd_effective_bids_root is None and raw_matches.get("dataset_description_exact"):
        xcpd_effective_bids_root = raw_matches["dataset_description_exact"][0]
    xcpd_effective_output_root = xcpd_output_root
    if xcpd_effective_output_root is None and raw_matches.get("fmriprep_derivatives_exact"):
        xcpd_effective_output_root = _output_root_from_fmriprep_derivatives_hint(
            raw_matches["fmriprep_derivatives_exact"][0]
        )
    output_status_matches = raw_matches.get("output_root_status", [])
    if (
        host_target == "remote"
        and xcpd_effective_output_root is not None
        and raw_matches.get("fmriprep_derivatives_exact")
    ):
        output_status_matches = [*output_status_matches, "ok"]
    paths = {
        "bids_root": _dataset_result(
            xcpd_effective_bids_root,
            None if fmriprep_derivatives_hint else user_dataset_path,
            raw_matches["dataset_description"],
            host_target,
            exact_matches=raw_matches.get("dataset_description_exact", []),
            required="bids_root" in required_paths,
        ),
        "fmriprep_derivatives": _fmriprep_derivatives_result(
            fmriprep_bids_roots,
            xcpd_effective_output_root,
            raw_matches["fmriprep_derivatives"],
            host_target,
            fmriprep_derivatives_hint=fmriprep_derivatives_hint,
            exact_matches=raw_matches.get("fmriprep_derivatives_exact", []),
            required="fmriprep_derivatives" in required_paths,
        ),
        "output_root": _output_root_result(
            xcpd_effective_output_root,
            raw_matches["output_root"],
            host_target,
            output_status_matches,
            required="output_root" in required_paths,
        ),
        "templateflow_home": _templateflow_result(
            user_templateflow_path,
            raw_matches["templateflow_tpl"],
            host_target,
            exact_matches=raw_matches.get("templateflow_tpl_exact", []),
            required="templateflow_home" in required_paths,
        ),
        "fs_license": _file_result(
            fs_license,
            raw_matches["fs_license"],
            host_target,
            exact_matches=raw_matches.get("fs_license_exact", []),
            required="fs_license" in required_paths,
        ),
        "xcpd_image": _image_result(
            xcpd_image,
            raw_matches["xcpd_image"],
            host_target,
            pipeline="xcpd",
            exact_matches=raw_matches.get("xcpd_image_exact", []),
            required="xcpd_image" in required_paths,
        )
    }
    preflight = _preflight_decision(paths)
    return {
        "status": _overall_status(paths),
        "command": "path-probe",
        "pipeline": "xcpd",
        "probe_target": host_target,
        "remote_host": remote_host,
        **preflight,
        "paths": paths,
    }


def _target(remote_host: str | None) -> str:
    if remote_host:
        return "remote"
    if os.name == "nt":
        return "windows"
    return "local"


def _collect_matches(
    *,
    host_target: str,
    remote_host: str | None,
    bids_root: str | Path | PurePosixPath | None,
    user_dataset_path: str | Path | PurePosixPath | None,
    output_root: str | Path | PurePosixPath | None,
    user_templateflow_path: str | Path | PurePosixPath | None,
    fs_license: str | Path | PurePosixPath | None,
    fmriprep_image: str | Path | PurePosixPath | None,
) -> dict[str, list[str]]:
    roots = {
        "dataset_description": _path_roots(user_dataset_path, bids_root),
        "templateflow_tpl": _path_roots(user_templateflow_path)
        + _download_roots(bids_root, output_root, "templateflow"),
        "fs_license": _path_roots(fs_license) + _parent_roots(bids_root),
        "fmriprep_image": _image_roots(fmriprep_image, bids_root, output_root, host_target),
        "output_root": _output_roots(output_root),
    }
    specs = {
        "dataset_description": ("file", ["dataset_description.json"]),
        "templateflow_tpl": ("dir", ["tpl-*"]),
        "fs_license": ("file", ["license.txt"]),
        "fmriprep_image": ("file", IMAGE_PATTERNS["fmriprep"]),
        "output_root": ("dir", [_output_pattern(output_root)] if output_root else []),
    }
    if host_target == "remote":
        roots["output_root_status"] = [str(output_root)] if output_root is not None else []
        specs["output_root_status"] = ("status", [])
        return _remote_find(
            roots,
            specs,
            remote_host,
            exact_specs={
                "dataset_description": _remote_dataset_exact_commands(bids_root, user_dataset_path),
                "templateflow_tpl": _remote_templateflow_exact_commands(user_templateflow_path),
                "fs_license": _remote_file_exact_commands(fs_license),
                "fmriprep_image": _remote_image_exact_commands(fmriprep_image),
            },
        )
    exact_matches = {
        "dataset_description": _exact_dataset(bids_root, host_target) or _exact_dataset(user_dataset_path, host_target),
        "templateflow_tpl": _exact_templateflow(user_templateflow_path, host_target),
        "fs_license": _exact_file(fs_license, host_target),
        "fmriprep_image": None if _image_ref(fmriprep_image) else _exact_image(fmriprep_image, host_target, pipeline="fmriprep"),
        "output_root": _exact_output_root(output_root, host_target),
    }
    raw_matches: dict[str, list[str]] = {
        f"{key}_exact": [value] if value is not None else [] for key, value in exact_matches.items()
    }
    for key, (kind, patterns) in specs.items():
        raw_matches[key] = (
            []
            if exact_matches.get(key) is not None
            else _find_matches(roots[key], kind=kind, patterns=patterns, target=host_target)
        )
    return raw_matches


def _path_roots(*values: str | Path | PurePosixPath | None) -> list[str]:
    roots: list[str] = []
    for value in values:
        if value is None:
            continue
        raw = str(value)
        if not raw:
            continue
        roots.append(raw)
        parent = str(PurePosixPath(raw).parent) if raw.startswith("/") else str(Path(raw).parent)
        if parent and parent != ".":
            roots.append(parent)
    return _dedupe(roots)


def _parent_roots(value: str | Path | PurePosixPath | None) -> list[str]:
    if value is None:
        return []
    raw = str(value)
    parent = str(PurePosixPath(raw).parent) if raw.startswith("/") else str(Path(raw).parent)
    return [] if parent in {"", "."} else [parent]


def _download_roots(
    bids_root: str | Path | PurePosixPath | None,
    output_root: str | Path | PurePosixPath | None,
    leaf: str,
) -> list[str]:
    roots: list[str] = []
    if bids_root is not None:
        bids = str(bids_root)
        parent = PurePosixPath(bids).parent if bids.startswith("/") else Path(bids).parent
        roots.append(str(parent / "_downloads" / leaf))
    if output_root is not None:
        output = str(output_root)
        parent = PurePosixPath(output).parent if output.startswith("/") else Path(output).parent
        roots.append(str(parent / "_downloads" / leaf))
    return _dedupe(roots)


def _path_obj(value: str | Path | PurePosixPath) -> Path | PurePosixPath:
    raw = str(value)
    return PurePosixPath(raw) if raw.startswith("/") else Path(raw)


def _child_path(value: str | Path | PurePosixPath, *parts: str) -> str:
    return str(_path_obj(value).joinpath(*parts))


def _fmriprep_derivatives_hint(value: str | Path | PurePosixPath | None) -> str | None:
    if value is None:
        return None
    path = _path_obj(value)
    if path.name == "fmriprep" and path.parent.name == "derivatives":
        return str(path)
    if path.name == "derivatives":
        return str(path / "fmriprep")
    return None


def _bids_root_from_fmriprep_derivatives_hint(value: str | Path | PurePosixPath | None) -> str | None:
    hint = _fmriprep_derivatives_hint(value)
    if hint is None:
        return None
    return str(_path_obj(hint).parent.parent)


def _output_root_from_fmriprep_derivatives_hint(value: str | Path | PurePosixPath | None) -> str | None:
    hint = _fmriprep_derivatives_hint(value)
    if hint is None:
        return None
    return str(_path_obj(hint).parent)


def _xcpd_dataset_values(
    bids_root: str | Path | PurePosixPath | None,
    user_dataset_path: str | Path | PurePosixPath | None,
) -> list[str | Path | PurePosixPath | None]:
    if bids_root is not None:
        return [bids_root]
    derived = _bids_root_from_fmriprep_derivatives_hint(user_dataset_path)
    if derived is not None:
        return [derived]
    return [user_dataset_path]


def _fmriprep_derivative_candidates(
    bids_root: str | Path | PurePosixPath | list[str | Path | PurePosixPath] | None,
    output_root: str | Path | PurePosixPath | None,
    *,
    fmriprep_derivatives_hint: str | None = None,
) -> list[str]:
    candidates: list[str] = []
    if fmriprep_derivatives_hint is not None:
        candidates.append(fmriprep_derivatives_hint)
        return _dedupe(candidates)
    if output_root is not None:
        candidates.append(_child_path(output_root, "fmriprep"))
        return _dedupe(candidates)
    for root in _path_values(bids_root):
        candidates.append(_child_path(root, "derivatives", "fmriprep"))
    return _dedupe(candidates)


def _fmriprep_derivatives_roots(
    bids_root: str | Path | PurePosixPath | list[str | Path | PurePosixPath] | None,
    output_root: str | Path | PurePosixPath | None,
    *,
    fmriprep_derivatives_hint: str | None = None,
) -> list[str]:
    roots: list[str] = []
    if output_root is not None:
        roots.append(str(output_root))
    else:
        for root in _path_values(bids_root):
            roots.append(_child_path(root, "derivatives"))
    roots.extend(
        _fmriprep_derivative_candidates(
            bids_root,
            output_root,
            fmriprep_derivatives_hint=fmriprep_derivatives_hint,
        )
    )
    return _dedupe(roots)


def _image_roots(
    fmriprep_image: str | Path | PurePosixPath | None,
    bids_root: str | Path | PurePosixPath | None,
    output_root: str | Path | PurePosixPath | None,
    target: str,
) -> list[str]:
    if target == "windows" or _image_ref(fmriprep_image):
        return []
    return _dedupe(
        _path_roots(fmriprep_image)
        + _parent_roots(bids_root)
        + _download_roots(bids_root, output_root, "images")
    )


def _output_roots(output_root: str | Path | PurePosixPath | None) -> list[str]:
    if output_root is None:
        return []
    raw = str(output_root)
    parent = str(PurePosixPath(raw).parent) if raw.startswith("/") else str(Path(raw).parent)
    return [] if parent in {"", "."} else [parent]


def _output_pattern(output_root: str | Path | PurePosixPath | None) -> str:
    if output_root is None:
        return ""
    raw = str(output_root)
    name = PurePosixPath(raw).name if raw.startswith("/") else Path(raw).name
    return f"{name}*"


def _find_matches(roots: list[str], *, kind: str, patterns: list[str], target: str) -> list[str]:
    if not patterns:
        return []
    if target == "windows":
        return _windows_find(roots, kind=kind, patterns=patterns)
    matches: list[str] = []
    for root in roots:
        command = _find_command(root, kind=kind, patterns=patterns)
        result = run_command(shell_command(command), check=False)
        if result.returncode == 0:
            matches.extend(line for line in result.stdout.splitlines() if line.strip())
    return sorted(_dedupe(matches))


def _windows_find(roots: list[str], *, kind: str, patterns: list[str], max_depth: int = MAX_DEPTH) -> list[str]:
    if kind not in {"file", "dir"}:
        raise ValueError(f"kind must be 'file' or 'dir', got {kind!r}")

    matches: list[str] = []
    seen: set[str] = set()
    for raw_root in roots:
        root = Path(raw_root)
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue

        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        name = entry.name
                        entry_path = Path(entry.path)
                        entry_depth = depth + 1

                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            is_file = entry.is_file(follow_symlinks=False)
                        except OSError:
                            continue

                        matched = any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
                        if entry_depth <= max_depth:
                            if kind == "file" and is_file and matched:
                                path_str = str(entry_path)
                                if path_str not in seen:
                                    seen.add(path_str)
                                    matches.append(path_str)
                            elif kind == "dir" and is_dir and matched:
                                path_str = str(entry_path)
                                if path_str not in seen:
                                    seen.add(path_str)
                                    matches.append(path_str)

                        if is_dir and entry_depth < max_depth:
                            stack.append((entry_path, entry_depth))
            except OSError:
                continue
    return sorted(matches)


def _remote_find(
    roots: dict[str, list[str]],
    specs: dict[str, tuple[str, list[str]]],
    remote_host: str | None,
    *,
    exact_specs: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    if remote_host is None:
        raise ValueError("remote_host is required for remote path probe")
    blocks: list[str] = ["set +e"]
    for label, (kind, patterns) in specs.items():
        exact_commands = (exact_specs or {}).get(label, [])
        if exact_commands:
            blocks.append(f"printf '%s\\n' {shlex.quote(REMOTE_MARKER + label + '_exact')}")
            blocks.append("__fmri_exact_found=0")
            blocks.extend(exact_commands)
        blocks.append(f"printf '%s\\n' {shlex.quote(REMOTE_MARKER + label)}")
        if kind == "status":
            for root in roots[label]:
                blocks.append(_remote_output_status_command(root))
            continue
        if not patterns:
            continue
        for root in roots[label]:
            command = _find_command(root, kind=kind, patterns=patterns)
            if exact_commands:
                command = f'if [ "$__fmri_exact_found" -eq 0 ]; then {command}; fi'
            blocks.append(command)
    try:
        result = run_command(
            shell_command("\n".join(blocks)),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_PATH_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        message = _compact_error(str(exc))
        raise RemotePathProbeError(f"remote path probe failed on {remote_host}: {message}") from exc
    if result.returncode != 0:
        detail = _compact_error((result.stderr or "").strip() or (result.stdout or "").strip())
        message = detail or f"ssh exited with {result.returncode}"
        raise RemotePathProbeError(f"remote path probe failed on {remote_host}: {message}")
    parse_specs = dict(specs)
    if exact_specs:
        parse_specs.update({f"{label}_exact": ("exact", []) for label in exact_specs})
    return _parse_remote_stdout(result.stdout, parse_specs)


def _compact_error(text: str) -> str:
    return " ".join(text.split())[:500]


def _preflight_decision(paths: dict[str, dict[str, Any]]) -> dict[str, Any]:
    normalized_args: dict[str, str] = {}
    unresolved_questions: list[dict[str, Any]] = []
    for name, result in paths.items():
        status = str(result.get("status") or "")
        correction = result.get("correction")
        if status in {"exact", "unique_correction"} and isinstance(correction, str) and correction:
            normalized_args[name] = correction
        elif status in {"ambiguous", "missing"}:
            question: dict[str, Any] = {
                "kind": "path",
                "name": name,
                "reason": status,
                "input": result.get("input"),
            }
            candidates = result.get("candidates")
            if candidates:
                question["candidates"] = candidates
            unresolved_questions.append(question)
    return {
        "preflight_decision": "pause_required" if unresolved_questions else "ready",
        "unresolved_questions": unresolved_questions,
        "normalized_args": normalized_args,
    }


def _find_command(root: str, *, kind: str, patterns: list[str]) -> str:
    kind_arg = "f" if kind == "file" else "d"
    if len(patterns) == 1:
        name_expr = f"-name {shlex.quote(patterns[0])}"
    else:
        joined = " -o ".join(f"-name {shlex.quote(pattern)}" for pattern in patterns)
        name_expr = f"\\( {joined} \\)"
    return f"find {shlex.quote(root)} -maxdepth {MAX_DEPTH} -type {kind_arg} {name_expr} -print 2>/dev/null"


def _remote_output_status_command(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        f"target={quoted}; parent=$(dirname \"$target\"); "
        'if [ -e "$target" ] || [ -d "$parent" ]; then printf \'%s\\n\' ok; fi'
    )


def _remote_dataset_exact_commands(*values: Any) -> list[str]:
    commands: list[str] = []
    for value in _provided_values(*values):
        quoted = shlex.quote(value)
        commands.append(
            f"candidate={quoted}; "
            'description="$candidate/dataset_description.json"; '
            'if [ -d "$candidate" ] && [ -f "$description" ] && [ -r "$description" ]; then '
            'printf \'%s\\n\' "$candidate"; __fmri_exact_found=1; fi'
        )
    return commands


def _remote_templateflow_exact_commands(*values: Any) -> list[str]:
    commands: list[str] = []
    for value in _provided_values(*values):
        quoted = shlex.quote(value)
        commands.append(
            f"candidate={quoted}; "
            'if [ -d "$candidate" ] && [ -r "$candidate" ]; then '
            'base=$(basename "$candidate"); parent=$(dirname "$candidate"); '
            'case "$base" in '
            'tpl-*) printf \'%s\\n\' "$parent"; __fmri_exact_found=1 ;; '
            '*) for child in "$candidate"/tpl-*; do '
            '[ -d "$child" ] || continue; '
            'printf \'%s\\n\' "$candidate"; __fmri_exact_found=1; break; '
            "done ;; "
            "esac; fi"
        )
    return commands


def _remote_file_exact_commands(*values: Any) -> list[str]:
    commands: list[str] = []
    for value in _provided_values(*values):
        quoted = shlex.quote(value)
        commands.append(
            f"candidate={quoted}; "
            'if [ -f "$candidate" ] && [ -r "$candidate" ]; then '
            'printf \'%s\\n\' "$candidate"; __fmri_exact_found=1; fi'
        )
    return commands


def _remote_fmriprep_derivatives_exact_commands(
    bids_root: str | Path | PurePosixPath | list[str | Path | PurePosixPath] | None,
    output_root: str | Path | PurePosixPath | None,
    *,
    fmriprep_derivatives_hint: str | None = None,
) -> list[str]:
    commands: list[str] = []
    for value in _fmriprep_derivative_candidates(
        bids_root,
        output_root,
        fmriprep_derivatives_hint=fmriprep_derivatives_hint,
    ):
        quoted = shlex.quote(value)
        commands.append(
            f"candidate={quoted}; "
            'description="$candidate/dataset_description.json"; '
            'if [ -d "$candidate" ] && [ -f "$description" ] && [ -r "$description" ]; then '
            'printf \'%s\\n\' "$candidate"; __fmri_exact_found=1; fi'
        )
    return commands


def _remote_image_exact_commands(*values: Any) -> list[str]:
    commands: list[str] = []
    for value in _provided_values(*values):
        if _image_ref(value):
            continue
        quoted = shlex.quote(value)
        commands.append(
            f"candidate={quoted}; "
            'case "$candidate" in '
            '*.sif|*.simg) '
            'if [ -f "$candidate" ] && [ -r "$candidate" ]; then '
            'printf \'%s\\n\' "$candidate"; __fmri_exact_found=1; fi ;; '
            "esac"
        )
    return commands


def _provided_values(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        raw = str(value)
        if raw:
            result.append(raw)
    return _dedupe(result)


def _path_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None and str(item)]
    return [value] if str(value) else []


def _parse_remote_stdout(stdout: str, specs: dict[str, tuple[str, list[str]]]) -> dict[str, list[str]]:
    matches = {label: [] for label in specs}
    current: str | None = None
    for line in stdout.splitlines():
        if line.startswith(REMOTE_MARKER):
            label = line.removeprefix(REMOTE_MARKER)
            current = label if label in matches else None
            continue
        if current and line.strip():
            matches[current].append(line.strip())
    return {key: sorted(_dedupe(values)) for key, values in matches.items()}


def _dataset_result(
    bids_root: str | Path | PurePosixPath | None,
    user_dataset_path: str | Path | PurePosixPath | None,
    matches: list[str],
    target: str,
    *,
    exact_matches: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    if exact_matches:
        return _result("exact", exact_matches[0], input_value=bids_root or user_dataset_path)
    exact = _exact_dataset(bids_root, target) or _exact_dataset(user_dataset_path, target)
    if exact is not None:
        return _result("exact", exact, input_value=bids_root or user_dataset_path)
    candidates = sorted(_dedupe(str(PurePosixPath(match).parent) for match in matches))
    with_subjects = [] if target == "remote" else [candidate for candidate in candidates if _has_subject_dir(candidate, target)]
    return _candidate_result(bids_root or user_dataset_path, with_subjects or candidates, required=required)


def _fmriprep_derivatives_result(
    bids_root: str | Path | PurePosixPath | None,
    output_root: str | Path | PurePosixPath | None,
    matches: list[str],
    target: str,
    *,
    fmriprep_derivatives_hint: str | None = None,
    exact_matches: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    if exact_matches:
        return _result("exact", exact_matches[0], input_value=output_root or bids_root)
    exact = _exact_fmriprep_derivatives(
        bids_root,
        output_root,
        target,
        fmriprep_derivatives_hint=fmriprep_derivatives_hint,
    )
    if exact is not None:
        return _result("exact", exact, input_value=output_root or bids_root)
    if fmriprep_derivatives_hint is not None:
        return _candidate_result(fmriprep_derivatives_hint, [], required=required)
    candidates = sorted(
        _dedupe(
            str(PurePosixPath(match).parent)
            for match in matches
            if PurePosixPath(match).parent.name == "fmriprep"
        )
    )
    return _candidate_result(output_root or bids_root, candidates, required=required)


def _templateflow_result(
    value: str | Path | PurePosixPath | None,
    matches: list[str],
    target: str,
    *,
    exact_matches: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    if exact_matches:
        return _result("exact", exact_matches[0], input_value=value)
    exact = _exact_templateflow(value, target)
    if exact is not None:
        return _result("exact", exact, input_value=value)
    candidates = sorted(_dedupe(str(PurePosixPath(match).parent) for match in matches))
    return _candidate_result(value, candidates, required=required)


def _file_result(
    value: str | Path | PurePosixPath | None,
    matches: list[str],
    target: str,
    *,
    exact_matches: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    if exact_matches:
        return _result("exact", exact_matches[0], input_value=value)
    exact = _exact_file(value, target)
    if exact is not None:
        return _result("exact", exact, input_value=value)
    return _candidate_result(value, matches, required=required)


def _image_result(
    value: str | Path | PurePosixPath | None,
    matches: list[str],
    target: str,
    *,
    pipeline: str,
    exact_matches: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    if target == "windows":
        return {
            "status": "skipped",
            "input": str(value) if value is not None else None,
            "reason": "native_windows_uses_registry_image",
            "candidates": [],
        }
    if _image_ref(value):
        return _result("exact", str(value), input_value=value)
    if exact_matches:
        return _result("exact", exact_matches[0], input_value=value)
    exact = _exact_image(value, target, pipeline=pipeline)
    if exact is not None:
        return _result("exact", exact, input_value=value)
    return _candidate_result(value, matches, required=required)


def _output_root_result(
    value: str | Path | PurePosixPath | None,
    matches: list[str],
    target: str,
    status_matches: list[str],
    *,
    required: bool = False,
) -> dict[str, Any]:
    if value is None:
        return _candidate_result(value, [], required=required)
    raw = str(value)
    if target == "remote":
        if "ok" in status_matches:
            return _result("exact", raw, input_value=value)
        return _candidate_result(value, matches)
    if _path_exists(raw, target) or _parent_exists(raw, target):
        return _result("exact", raw, input_value=value)
    return _candidate_result(value, matches)


def _candidate_result(value: Any, candidates: list[str], *, required: bool = False) -> dict[str, Any]:
    candidates = sorted(_dedupe(candidates))
    if not candidates:
        return {"status": "missing" if required or value is not None else "skipped", "input": _text(value), "candidates": []}
    if len(candidates) == 1:
        return _result("unique_correction", candidates[0], input_value=value)
    return {
        "status": "ambiguous",
        "input": _text(value),
        "candidates": [{"path": candidate} for candidate in candidates],
    }


def _result(status: str, correction: str, *, input_value: Any) -> dict[str, Any]:
    return {
        "status": status,
        "input": _text(input_value),
        "correction": correction,
        "candidates": [{"path": correction}],
    }


def _skipped() -> dict[str, Any]:
    return {"status": "skipped", "input": None, "candidates": []}


def _overall_status(paths: dict[str, dict[str, Any]]) -> str:
    statuses = {str(result.get("status")) for result in paths.values()}
    if "ambiguous" in statuses:
        return "needs_user_input"
    if "missing" in statuses:
        return "missing"
    return "ok"


def _exact_dataset(value: Any, target: str) -> str | None:
    if value is None:
        return None
    if target == "remote":
        return None
    raw = str(value)
    return raw if _path_exists(str(PurePosixPath(raw) / "dataset_description.json"), target) else None


def _first_exact_dataset(values: list[str | Path | PurePosixPath | None], target: str) -> str | None:
    for value in values:
        exact = _exact_dataset(value, target)
        if exact is not None:
            return exact
    return None


def _exact_templateflow(value: Any, target: str) -> str | None:
    if value is None:
        return None
    if target == "remote":
        return None
    raw = str(value)
    if _has_direct_tpl_dir(raw):
        return raw
    path = PurePosixPath(raw)
    if path.name.startswith("tpl-"):
        parent = str(path.parent)
        if _path_exists(raw, target):
            return parent
    return None


def _exact_file(value: Any, target: str) -> str | None:
    if value is None:
        return None
    if target == "remote":
        return None
    raw = str(value)
    return raw if _readable_file(raw, target) else None


def _exact_image(value: Any, target: str, *, pipeline: str) -> str | None:
    if value is None:
        return None
    if target == "remote":
        return None
    raw = str(value)
    suffix = Path(raw).suffix.lower()
    if suffix in {".sif", ".simg"} and _readable_file(raw, target):
        return raw
    return None


def _exact_output_root(value: Any, target: str) -> str | None:
    if value is None or target == "remote":
        return None
    raw = str(value)
    return raw if _path_exists(raw, target) or _parent_exists(raw, target) else None


def _exact_fmriprep_derivatives(
    bids_root: str | Path | PurePosixPath | list[str | Path | PurePosixPath] | None,
    output_root: str | Path | PurePosixPath | None,
    target: str,
    *,
    fmriprep_derivatives_hint: str | None = None,
) -> str | None:
    if target == "remote":
        return None
    for candidate in _fmriprep_derivative_candidates(
        bids_root,
        output_root,
        fmriprep_derivatives_hint=fmriprep_derivatives_hint,
    ):
        description = _child_path(candidate, "dataset_description.json")
        if _path_exists(description, target):
            return candidate
    return None


def _has_subject_dir(path: str, target: str) -> bool:
    return bool(_find_matches([path], kind="dir", patterns=["sub-*"], target=target))


def _has_direct_tpl_dir(path: str) -> bool:
    root = Path(path)
    if not root.exists() or not root.is_dir():
        return False
    return any(child.is_dir() and fnmatch.fnmatch(child.name, "tpl-*") for child in root.iterdir())


def _path_exists(path: str, target: str) -> bool:
    if target == "remote":
        return True
    return Path(path).exists()


def _readable_file(path: str, target: str) -> bool:
    if target == "remote":
        return False
    candidate = Path(path)
    return candidate.is_file() and os.access(candidate, os.R_OK)


def _parent_exists(path: str, target: str) -> bool:
    if target == "remote":
        return True
    return Path(path).parent.exists()


def _image_ref(value: Any) -> bool:
    if value is None:
        return False
    raw = str(value)
    if raw.startswith(IMAGE_REF_PREFIXES):
        return True
    return "/" in raw and ":" in raw and not raw.startswith("/")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
