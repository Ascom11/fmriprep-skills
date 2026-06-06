"""Compact read-only run status collection for follow-up monitoring."""

from __future__ import annotations

import json
import platform
import shlex
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import archived_artifact_path, latest_audit_path, load_stage_artifact
from .models import RequestConfig, RequestPath
from .shell import argv_command, glob_paths, run_command, shell_command

DEFAULT_LOG_LINES = 20
DEFAULT_MAX_PATHS = 20
HARD_MAX_LOG_LINES = 50
HARD_MAX_PATHS = 50
MONITOR_PROBE_TIMEOUT_SECONDS = 20
STATUS_RECORD_TAG = "__FMRI_STATUS__"
FMRIPREP_REPORT_TAIL_BYTES = 12_288
FMRIPREP_REPORT_EXCERPT_CHARS = 600
FMRIPREP_NO_ERRORS_MARKER = "no errors to report"
SUBJECT_SUCCESS_MARKER = "finished successfully"


def collect_run_status(
    request: RequestConfig,
    *,
    audit_id: str | None = None,
    submission_id: str | None = None,
    target: str | None = None,
    log_lines: int = DEFAULT_LOG_LINES,
    max_paths: int = DEFAULT_MAX_PATHS,
) -> dict[str, Any]:
    log_lines = _bounded_positive_int(log_lines, maximum=HARD_MAX_LOG_LINES)
    max_paths = _bounded_positive_int(max_paths, maximum=HARD_MAX_PATHS)
    selection = _select_saved_context(request, audit_id=audit_id, submission_id=submission_id, target=target)
    if selection.get("status"):
        return _ambiguous_status_payload(request, selection["status"], selection["missing_evidence"], selection.get("targets"))
    selected_request = selection["request"]
    submission = selection["submission"]
    context = selection["context"]
    selected_target = selection["target"]
    output_tree = selected_request.resolve_pipeline_output_root(selected_target)
    missing_evidence: list[str] = []
    if submission is None:
        missing_evidence.append("submission-result")

    execution = dict((submission or {}).get("execution") or {})
    subjects = [subject for subject in execution.get("subjects") or [] if isinstance(subject, dict)]
    job_ids = _job_ids(subjects)
    pids = _pids(subjects)
    subject_keys = _subject_keys(subjects)
    if submission is not None and not job_ids and not pids:
        missing_evidence.append("job_id_or_pid")
    if not subject_keys:
        missing_evidence.append("subject_keys_for_crash_scan")
    if submission is not None and not _status_log_paths(execution, max_paths=max_paths):
        missing_evidence.append("stdout_stderr_log_paths")

    evidence = _probe_run_evidence(
        job_ids=job_ids,
        pids=pids,
        log_paths=_status_log_paths(execution, max_paths=max_paths),
        report_paths=_report_paths(output_tree, subject_keys, selected_target, max_paths=max_paths),
        crash_dirs=_crash_dirs(output_tree, subject_keys),
        remote_host=request.remote_host,
        log_lines=log_lines,
        max_paths=max_paths,
    )
    scheduler = evidence["scheduler"]
    processes = evidence["processes"]
    logs = evidence["logs"]
    reports = evidence["reports"]
    outputs = {"checked": False, "skipped": "default_log_only"}
    crashes = _mark_stale_crashes(evidence["crashes"], submission)
    primary_error = _primary_error(execution, logs=logs, crashes=crashes)
    status = _classify_status(
        execution,
        scheduler=scheduler,
        processes=processes,
        crashes=crashes,
        missing_evidence=missing_evidence,
        primary_error=primary_error,
    )
    summary = {
        "target": selected_target,
        "remote_host": request.remote_host,
        "output_tree": str(output_tree),
        "log_root": _log_root(selected_request),
        "job_ids": job_ids,
        "pids": pids,
        "scheduler": scheduler,
        "processes": processes,
        "pid_manifest": execution.get("pid_manifest"),
        "logs": logs,
        "reports": reports,
        "outputs": outputs,
        "crashes": crashes,
        "missing_evidence": missing_evidence,
        "next_action": _next_action(status, selected_target),
    }
    subject_statuses = _subject_statuses(subjects, logs=logs, reports=reports, output_tree=output_tree, target=selected_target)
    if subject_statuses:
        summary["subject_statuses"] = subject_statuses
    if primary_error is not None:
        summary["primary_error"] = primary_error

    return {
        "status": status,
        "command": "run-status",
        "summary": summary,
        "artifacts": _status_artifacts(selected_request, submission=submission, context=context),
    }


def _select_saved_context(
    request: RequestConfig,
    *,
    audit_id: str | None,
    submission_id: str | None,
    target: str | None,
) -> dict[str, Any]:
    targets = [target] if target in {"fmriprep", "xcpd"} else ["fmriprep", "xcpd"]
    candidates: list[dict[str, Any]] = []
    ambiguous_submission_targets: list[str] = []
    ambiguous_submission_fields: set[str] = set()
    for candidate_target in targets:
        candidate_request = replace(request, target=candidate_target)
        submission, context, ambiguous_submission_field = _load_selected_artifacts(
            candidate_request,
            audit_id=audit_id,
            submission_id=submission_id,
        )
        if ambiguous_submission_field:
            ambiguous_submission_targets.append(candidate_target)
            ambiguous_submission_fields.add(ambiguous_submission_field)
            continue
        selected_target = _selected_target(submission, context, candidate_target if target else None)
        if selected_target is None:
            continue
        if selected_target != candidate_target:
            continue
        if submission is not None or context is not None:
            candidates.append(
                {
                    "request": candidate_request,
                    "submission": submission,
                    "context": context,
                    "target": selected_target,
                }
            )
    if len(candidates) == 1:
        return candidates[0]
    if ambiguous_submission_targets:
        return {
            "status": "submission-ambiguous",
            "missing_evidence": sorted(ambiguous_submission_fields),
            "targets": ambiguous_submission_targets,
        }
    if target in {"fmriprep", "xcpd"}:
        candidate_request = replace(request, target=target)
        return {"request": candidate_request, "submission": None, "context": None, "target": target}
    return {
        "status": "target-ambiguous",
        "missing_evidence": ["target"],
        "targets": [str(candidate["target"]) for candidate in candidates],
    }


def _load_selected_artifacts(
    request: RequestConfig,
    *,
    audit_id: str | None,
    submission_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    selected_audit_id = audit_id
    selected_submission_id = submission_id
    if audit_id and submission_id is None:
        submission_ids = _submission_ids_for_audit(request, audit_id)
        if len(submission_ids) > 1:
            return None, None, "submission_id"
        if len(submission_ids) == 1:
            selected_submission_id = submission_ids[0]
    elif audit_id is None and submission_id is not None:
        audit_ids = _audit_ids_for_submission(request, submission_id)
        if len(audit_ids) > 1:
            return None, None, "audit_id"
        if len(audit_ids) == 0:
            return None, None, None
        selected_audit_id = audit_ids[0]
    lookup_audit_id = selected_audit_id if selected_submission_id else None
    submission = load_stage_artifact(
        request,
        "submission-result",
        audit_id=lookup_audit_id,
        submission_id=selected_submission_id,
    )
    if audit_id and _artifact_audit_id(submission, None) != audit_id:
        submission = None
    context_audit_id = _artifact_audit_id(submission, lookup_audit_id)
    context_submission_id = _artifact_submission_id(submission, selected_submission_id)
    if context_audit_id and context_submission_id:
        context = load_stage_artifact(
            request,
            "execution-context",
            audit_id=context_audit_id,
            submission_id=context_submission_id,
        )
    elif selected_audit_id or selected_submission_id:
        context = None
    else:
        context = load_stage_artifact(request, "execution-context")
    if audit_id and _artifact_audit_id(context, None) != audit_id:
        context = None
    return submission, context, None


def _submission_ids_for_audit(request: RequestConfig, audit_id: str) -> list[str]:
    audit_dir = latest_audit_path(request).parent / f"audit_{audit_id}"
    pattern = str(audit_dir / "submission_*" / "submission-result.json")
    ids: list[str] = []
    for path in glob_paths(pattern, request.remote_host):
        name = path.parent.name
        if name.startswith("submission_"):
            ids.append(name.removeprefix("submission_"))
    return sorted(set(ids))


def _audit_ids_for_submission(request: RequestConfig, submission_id: str) -> list[str]:
    artifact_root = latest_audit_path(request).parent
    pattern = str(artifact_root / "audit_*" / f"submission_{submission_id}" / "submission-result.json")
    ids: list[str] = []
    for path in glob_paths(pattern, request.remote_host):
        name = path.parent.parent.name
        if name.startswith("audit_"):
            ids.append(name.removeprefix("audit_"))
    return sorted(set(ids))


def _ambiguous_status_payload(
    request: RequestConfig,
    status: str,
    missing_evidence: list[str],
    targets: list[str] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "target": None,
        "remote_host": request.remote_host,
        "output_tree": None,
        "log_root": _log_root(request),
        "job_ids": [],
        "pids": [],
        "scheduler": {"checked": False, "visible": False, "states": []},
        "processes": {"checked": False, "visible_pids": []},
        "logs": [],
        "reports": [],
        "outputs": {"checked": False, "skipped": "target_not_selected"},
        "crashes": [],
        "missing_evidence": missing_evidence,
        "next_action": "select-target" if status == "target-ambiguous" else "select-submission",
    }
    if targets:
        summary["candidate_targets"] = sorted(set(targets))
    return {
        "status": status,
        "command": "run-status",
        "summary": summary,
        "artifacts": {},
    }


def _artifact_audit_id(artifact: dict[str, Any] | None, fallback: str | None) -> str | None:
    value = (artifact or {}).get("audit_id")
    return value if isinstance(value, str) and value else fallback


def _artifact_submission_id(artifact: dict[str, Any] | None, fallback: str | None) -> str | None:
    value = (artifact or {}).get("submission_id")
    return value if isinstance(value, str) and value else fallback


def _selected_target(
    submission: dict[str, Any] | None,
    context: dict[str, Any] | None,
    explicit_target: str | None,
) -> str | None:
    if explicit_target in {"fmriprep", "xcpd"}:
        return explicit_target
    for artifact in (submission, context):
        signature = (artifact or {}).get("request_signature")
        if isinstance(signature, dict) and signature.get("target") in {"fmriprep", "xcpd"}:
            return str(signature["target"])
    return None


def _job_ids(subjects: list[dict[str, Any]]) -> list[str]:
    return sorted({str(subject["job_id"]) for subject in subjects if subject.get("job_id")})


def _pids(subjects: list[dict[str, Any]]) -> list[int]:
    pids: set[int] = set()
    for subject in subjects:
        value = subject.get("pid")
        if value is None:
            continue
        try:
            pids.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(pids)


def _subject_keys(subjects: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for subject in subjects:
        value = subject.get("subject_key")
        if isinstance(value, str) and value:
            keys.append(value)
    return _dedupe(keys)


def _status_log_paths(execution: dict[str, Any], *, max_paths: int) -> list[str]:
    priority = _step_stderr_paths(execution) + _launcher_stderr_paths(execution)
    return _dedupe(priority + _log_paths(execution))[:max_paths]


def _crash_dirs(output_tree: RequestPath, subject_keys: list[str]) -> list[str]:
    paths = [str(output_tree / "logs")]
    paths.extend(str(output_tree / subject_key / "log") for subject_key in subject_keys)
    return _dedupe(paths)


def _report_paths(output_tree: RequestPath, subject_keys: list[str], target: str, *, max_paths: int) -> list[str]:
    if target != "fmriprep":
        return []
    return [str(output_tree / f"{subject_key}.html") for subject_key in subject_keys[:max_paths]]


def _probe_run_evidence(
    *,
    job_ids: list[str],
    pids: list[int],
    log_paths: list[str],
    report_paths: list[str],
    crash_dirs: list[str],
    remote_host: str | None,
    log_lines: int,
    max_paths: int,
) -> dict[str, Any]:
    if remote_host:
        return _remote_probe_run_evidence(
            job_ids=job_ids,
            pids=pids,
            log_paths=log_paths,
            report_paths=report_paths,
            crash_dirs=crash_dirs,
            remote_host=remote_host,
            log_lines=log_lines,
            max_paths=max_paths,
        )
    return {
        "scheduler": _probe_scheduler(job_ids, None) if job_ids else {"checked": False, "visible": False, "states": []},
        "processes": _probe_processes(pids, None) if pids else {"checked": False, "visible_pids": []},
        "logs": [_tail_log(path, lines=log_lines) for path in log_paths],
        "reports": [_inspect_report(path, tail_bytes=FMRIPREP_REPORT_TAIL_BYTES) for path in report_paths],
        "crashes": _inspect_crashes(crash_dirs, log_lines=log_lines, max_paths=max_paths),
    }


def _remote_probe_run_evidence(
    *,
    job_ids: list[str],
    pids: list[int],
    log_paths: list[str],
    report_paths: list[str],
    crash_dirs: list[str],
    remote_host: str,
    log_lines: int,
    max_paths: int,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "job_ids": job_ids,
            "pids": pids,
            "log_paths": log_paths,
            "report_paths": report_paths,
            "report_tail_bytes": FMRIPREP_REPORT_TAIL_BYTES,
            "report_excerpt_chars": FMRIPREP_REPORT_EXCERPT_CHARS,
            "report_marker": FMRIPREP_NO_ERRORS_MARKER,
            "crash_dirs": crash_dirs,
            "log_lines": int(log_lines),
            "max_paths": int(max_paths),
            "timeout": MONITOR_PROBE_TIMEOUT_SECONDS,
        },
        separators=(",", ":"),
    )
    script = r'''
import json
import os
import shlex
import subprocess
import sys

TAG = "__FMRI_STATUS__"
request = json.loads(sys.argv[1])
timeout = int(request["timeout"])
log_lines = int(request["log_lines"])
max_paths = int(request["max_paths"])
report_tail_bytes = int(request["report_tail_bytes"])
report_excerpt_chars = int(request["report_excerpt_chars"])
report_marker = str(request["report_marker"]).lower()


def emit(kind, payload):
    print(TAG, kind, json.dumps(payload, separators=(",", ":")), flush=True)


def short(text):
    return " ".join((text or "").split())[:500]


def excerpt_around(text, index, marker_len):
    if index < 0:
        return ""
    half = max(1, report_excerpt_chars // 2)
    start = max(0, index - half)
    end = min(len(text), index + marker_len + half)
    return " ".join(text[start:end].split())


def run(parts):
    try:
        return subprocess.run(parts, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


job_ids = [str(value) for value in request.get("job_ids") or []]
if job_ids:
    result = run(["squeue", "-h", "-j", ",".join(job_ids), "-o", "%i %T"])
    if result is None:
        emit("scheduler", {"checked": True, "visible": False, "states": [], "timed_out": True})
    else:
        states = []
        for line in (result.stdout or "").splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                states.append(parts[1])
            elif parts:
                states.append(parts[0])
        payload = {"checked": True, "visible": bool(states), "states": states}
        if result.returncode != 0:
            payload["error"] = short(result.stderr or result.stdout)
        emit("scheduler", payload)
else:
    emit("scheduler", {"checked": False, "visible": False, "states": []})

pids = []
for value in request.get("pids") or []:
    try:
        pids.append(int(value))
    except (TypeError, ValueError):
        pass
if pids:
    result = run(["ps", "-p", ",".join(str(pid) for pid in pids), "-o", "pid=,stat=,cmd="])
    if result is None:
        emit("process", {"checked": True, "visible_pids": [], "timed_out": True})
    else:
        visible = []
        for line in (result.stdout or "").splitlines():
            parts = line.split(maxsplit=1)
            if not parts:
                continue
            try:
                visible.append(int(parts[0]))
            except ValueError:
                pass
        payload = {"checked": True, "visible_pids": sorted(set(visible))}
        if result.returncode != 0 and not visible:
            payload["error"] = short(result.stderr or result.stdout)
        emit("process", payload)
else:
    emit("process", {"checked": False, "visible_pids": []})

for path in [str(value) for value in request.get("log_paths") or []]:
    if not os.path.exists(path):
        emit("log", {"path": path, "exists": False, "tail": [], "truncated": False})
        continue
    if not os.path.isfile(path):
        emit("log", {"path": path, "exists": None, "tail": [], "truncated": False, "error": "stderr path is not a regular file"})
        continue
    result = run(["tail", "-n", str(log_lines + 1), path])
    if result is None:
        emit("log", {"path": path, "exists": None, "tail": [], "truncated": False, "timed_out": True})
        continue
    if result.returncode != 0:
        emit("log", {"path": path, "exists": None, "tail": [], "truncated": False, "error": short(result.stderr or result.stdout)})
        continue
    lines = (result.stdout or "").splitlines()
    emit("log", {"path": path, "exists": True, "tail": lines[-log_lines:], "truncated": len(lines) > log_lines})

for path in [str(value) for value in request.get("report_paths") or []]:
    if not os.path.exists(path):
        emit("report", {"path": path, "exists": False, "no_errors": False, "excerpt": "", "truncated": False})
        continue
    if not os.path.isfile(path):
        emit("report", {"path": path, "exists": None, "no_errors": False, "excerpt": "", "truncated": False, "error": "report path is not a regular file"})
        continue
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - report_tail_bytes)
            handle.seek(start)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        emit("report", {"path": path, "exists": None, "no_errors": False, "excerpt": "", "truncated": False, "error": short(str(exc))})
        continue
    index = text.lower().rfind(report_marker)
    emit("report", {
        "path": path,
        "exists": True,
        "no_errors": index >= 0,
        "excerpt": excerpt_around(text, index, len(report_marker)),
        "truncated": size > report_tail_bytes,
    })

crash_paths = []
for directory in [str(value) for value in request.get("crash_dirs") or []]:
    if len(crash_paths) >= max_paths:
        break
    if not os.path.isdir(directory):
        continue
    remaining = max_paths - len(crash_paths)
    result = run(["bash", "-lc", "find " + shlex.quote(directory) + " -maxdepth 3 -type f -name 'crash*' -print | head -n " + str(remaining)])
    if result is None:
        emit("crash", {"source": "crash_scan", "path": directory, "excerpt": [], "timed_out": True})
        continue
    if result.returncode != 0:
        emit("crash", {"source": "crash_scan", "path": directory, "excerpt": [], "error": short(result.stderr or result.stdout)})
        continue
    for line in (result.stdout or "").splitlines():
        if line:
            crash_paths.append(line)
for path in crash_paths:
    if not os.path.isfile(path):
        emit("crash", {"path": path, "excerpt": [], "exists": False})
        continue
    result = run(["tail", "-n", str(log_lines + 1), path])
    if result is None:
        emit("crash", {"path": path, "excerpt": [], "exists": None, "timed_out": True})
        continue
    if result.returncode != 0:
        emit("crash", {"path": path, "excerpt": [], "exists": None, "error": short(result.stderr or result.stdout)})
        continue
    lines = (result.stdout or "").splitlines()
    emit("crash", {"path": path, "excerpt": lines[-log_lines:], "exists": True, "truncated": len(lines) > log_lines, "mtime": os.path.getmtime(path)})
'''
    command = (
        "if command -v python3 >/dev/null 2>&1; then py=python3; else py=python; fi; "
        f'"$py" -c {shlex.quote(script)} {shlex.quote(payload)}'
    )
    try:
        result = run_command(
            shell_command(command),
            remote_host=remote_host,
            check=False,
            timeout=MONITOR_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _failed_probe_evidence(job_ids, pids, log_paths, report_paths, crash_dirs, timed_out=True)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return _failed_probe_evidence(job_ids, pids, log_paths, report_paths, crash_dirs, error=_short_text(str(exc)))
    evidence = _parse_status_records(result.stdout or "")
    if result.returncode != 0 and not evidence["logs"] and not evidence["reports"] and not evidence["crashes"]:
        return _failed_probe_evidence(job_ids, pids, log_paths, report_paths, crash_dirs, error=_short_text(result.stderr or result.stdout))
    return evidence


def _parse_status_records(stdout: str) -> dict[str, Any]:
    scheduler = {"checked": False, "visible": False, "states": []}
    processes = {"checked": False, "visible_pids": []}
    logs: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    crashes: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.startswith(STATUS_RECORD_TAG + " "):
            continue
        parts = line.split(" ", 2)
        if len(parts) != 3:
            continue
        try:
            payload = json.loads(parts[2])
        except json.JSONDecodeError:
            continue
        if parts[1] == "scheduler" and isinstance(payload, dict):
            scheduler = payload
        elif parts[1] == "process" and isinstance(payload, dict):
            processes = payload
        elif parts[1] == "log" and isinstance(payload, dict):
            logs.append(payload)
        elif parts[1] == "report" and isinstance(payload, dict):
            reports.append(payload)
        elif parts[1] == "crash" and isinstance(payload, dict):
            crashes.append(payload)
    return {"scheduler": scheduler, "processes": processes, "logs": logs, "reports": reports, "crashes": crashes}


def _failed_probe_evidence(
    job_ids: list[str],
    pids: list[int],
    log_paths: list[str],
    report_paths: list[str],
    crash_dirs: list[str],
    *,
    timed_out: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {"timed_out": True} if timed_out else {"error": error or "probe failed"}
    scheduler = {"checked": bool(job_ids), "visible": False, "states": [], **extra}
    processes = {"checked": bool(pids), "visible_pids": [], **extra}
    logs = [{"path": path, "exists": None, "tail": [], "truncated": False, **extra} for path in log_paths]
    reports = [{"path": path, "exists": None, "no_errors": False, "excerpt": "", "truncated": False, **extra} for path in report_paths]
    crashes = [{"source": "crash_scan", "path": path, "excerpt": [], **extra} for path in crash_dirs]
    return {"scheduler": scheduler, "processes": processes, "logs": logs, "reports": reports, "crashes": crashes}


def _probe_scheduler(job_ids: list[str], remote_host: str | None) -> dict[str, Any]:
    if _is_native_windows_local(remote_host):
        return {"checked": False, "visible": False, "states": [], "skipped": "native_windows_local"}
    command = shell_command("squeue -h -j " + shlex.quote(",".join(job_ids)) + " -o '%i %T'")
    try:
        result = run_command(
            command,
            remote_host=remote_host,
            check=False,
            timeout=MONITOR_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"checked": True, "visible": False, "states": [], "timed_out": True}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"checked": True, "visible": False, "states": [], "error": _short_text(str(exc))}
    states: list[str] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            states.append(parts[1])
        elif parts:
            states.append(parts[0])
    payload: dict[str, Any] = {"checked": True, "visible": bool(states), "states": states}
    if result.returncode != 0:
        payload["error"] = _short_text(result.stderr or result.stdout)
    return payload


def _probe_processes(pids: list[int], remote_host: str | None) -> dict[str, Any]:
    if _is_native_windows_local(remote_host):
        return {
            "checked": False,
            "visible_pids": [],
            "launcher_pids": sorted(set(pids)),
            "visibility_scope": "host_launcher",
            "skipped": "native_windows_local",
        }
    command = shell_command("ps -p " + shlex.quote(",".join(str(pid) for pid in pids)) + " -o pid=,stat=,cmd=")
    try:
        result = run_command(
            command,
            remote_host=remote_host,
            check=False,
            timeout=MONITOR_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"checked": True, "visible_pids": [], "timed_out": True}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"checked": True, "visible_pids": [], "error": _short_text(str(exc))}
    visible: list[int] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split(maxsplit=1)
        if not parts:
            continue
        try:
            visible.append(int(parts[0]))
        except ValueError:
            continue
    payload: dict[str, Any] = {"checked": True, "visible_pids": sorted(set(visible))}
    if result.returncode != 0 and not visible:
        payload["error"] = _short_text(result.stderr or result.stdout)
    return payload


def _log_paths(execution: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("launcher_stderr", "launcher_stdout"):
        value = execution.get(key)
        if value:
            paths.append(str(value))
    for record in list(execution.get("pre_steps") or []) + list(execution.get("subjects") or []):
        if not isinstance(record, dict):
            continue
        for key in ("launcher_stderr", "launcher_stdout", "stderr_path", "stdout_path"):
            value = record.get(key)
            if value:
                paths.append(str(value))
        for step in record.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for key in ("stderr_path", "stdout_path"):
                value = step.get(key)
                if value:
                    paths.append(str(value))
    return _dedupe(paths)


def _subject_log_paths(subject: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("launcher_stderr", "launcher_stdout", "stderr_path", "stdout_path"):
        value = subject.get(key)
        if value:
            paths.append(str(value))
    for step in subject.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for key in ("stderr_path", "stdout_path"):
            value = step.get(key)
            if value:
                paths.append(str(value))
    return _dedupe(paths)


def _subject_statuses(
    subjects: list[dict[str, Any]],
    *,
    logs: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    output_tree: RequestPath,
    target: str,
) -> list[dict[str, Any]]:
    if target != "fmriprep":
        return []
    log_by_path = {str(log.get("path")): log for log in logs if log.get("path")}
    report_by_path = {str(report.get("path")): report for report in reports if report.get("path")}
    statuses: list[dict[str, Any]] = []
    for subject in subjects:
        subject_key = subject.get("subject_key")
        if not isinstance(subject_key, str) or not subject_key:
            continue
        evidence: list[str] = []
        if any(_record_contains(log_by_path.get(path), SUBJECT_SUCCESS_MARKER) for path in _subject_log_paths(subject)):
            evidence.append("log_finished_successfully")
        report_path = str(output_tree / f"{subject_key}.html")
        if report_by_path.get(report_path, {}).get("no_errors") is True:
            evidence.append("report_no_errors")
        saved_status = str(subject.get("status") or "unknown")
        inferred_status = "likely_completed" if set(evidence) >= {"log_finished_successfully", "report_no_errors"} else saved_status
        statuses.append(
            {
                "subject_key": subject_key,
                "saved_status": saved_status,
                "inferred_status": inferred_status,
                "evidence": evidence,
                "report_path": report_path,
            }
        )
    return statuses


def _record_contains(record: dict[str, Any] | None, marker: str) -> bool:
    if not record:
        return False
    needle = marker.lower()
    return any(needle in str(line).lower() for line in record.get("tail") or [])


def _primary_error(
    execution: dict[str, Any],
    *,
    logs: list[dict[str, Any]],
    crashes: list[dict[str, Any]],
) -> dict[str, str] | None:
    log_by_path = {str(log.get("path")): log for log in logs if log.get("path")}

    for path in _step_stderr_paths(execution):
        record = log_by_path.get(path)
        if record is None:
            continue
        if record.get("timed_out"):
            return {"source": "step_stderr", "path": path, "kind": "timeout", "message": "Timed out while reading stderr log."}
        if record.get("error"):
            return {"source": "step_stderr", "path": path, "kind": "stderr_read_error", "message": str(record["error"])}
        extracted = _extract_key_error(record.get("tail") or [])
        if extracted is not None:
            return {"source": "step_stderr", "path": path, **extracted}
    for crash in crashes:
        if crash.get("ignored"):
            continue
        extracted = _extract_key_error(crash.get("excerpt") or [])
        if extracted is not None:
            return {"source": "crash", "path": str(crash.get("path") or ""), **extracted}
    for path in _launcher_stderr_paths(execution):
        record = log_by_path.get(path)
        if record is None:
            continue
        if record.get("timed_out"):
            return {"source": "launcher_stderr", "path": path, "kind": "timeout", "message": "Timed out while reading stderr log."}
        if record.get("error"):
            return {"source": "launcher_stderr", "path": path, "kind": "stderr_read_error", "message": str(record["error"])}
        message = _first_nonempty_line(record.get("tail") or [])
        if message:
            return {"source": "launcher_stderr", "path": path, "kind": "launcher_stderr", "message": message}
    return None


def _step_stderr_paths(execution: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for record in list(execution.get("pre_steps") or []) + list(execution.get("subjects") or []):
        if not isinstance(record, dict):
            continue
        if record.get("stderr_path"):
            paths.append(str(record["stderr_path"]))
        for step in record.get("steps") or []:
            if isinstance(step, dict) and step.get("stderr_path"):
                paths.append(str(step["stderr_path"]))
    return _dedupe(paths)


def _launcher_stderr_paths(execution: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if execution.get("launcher_stderr"):
        paths.append(str(execution["launcher_stderr"]))
    for record in list(execution.get("pre_steps") or []) + list(execution.get("subjects") or []):
        if isinstance(record, dict) and record.get("launcher_stderr"):
            paths.append(str(record["launcher_stderr"]))
    return _dedupe(paths)


def _extract_key_error(lines: list[str]) -> dict[str, str] | None:
    patterns = (
        "ProxyError",
        "FileNotFoundError",
        "ConnectionError",
        "ReadTimeout",
        "OSError",
        "RuntimeError",
        "ValueError",
        "Traceback",
    )
    for pattern in patterns:
        for line in lines:
            stripped = line.strip()
            if pattern in stripped:
                return {"kind": pattern, "message": stripped}
    return None


def _first_nonempty_line(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _tail_log(path: str, *, lines: int) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"path": path, "exists": False, "tail": [], "truncated": False}
    if not target.is_file():
        return {
            "path": path,
            "exists": None,
            "tail": [],
            "truncated": False,
            "error": "stderr path is not a regular file",
        }
    if platform.system() != "Windows":
        requested_lines = int(lines)
        try:
            result = run_command(
                argv_command(["tail", "-n", str(requested_lines + 1), path]),
                check=False,
                timeout=MONITOR_PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {"path": path, "exists": None, "tail": [], "truncated": False, "timed_out": True}
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return {"path": path, "exists": None, "tail": [], "truncated": False, "error": _short_text(str(exc))}
        if result.returncode != 0:
            return {"path": path, "exists": None, "tail": [], "truncated": False, "error": _short_text(result.stderr or result.stdout)}
        lines_found = (result.stdout or "").splitlines()
        return {"path": path, "exists": True, "tail": lines_found[-requested_lines:], "truncated": len(lines_found) > requested_lines}
    try:
        with target.open("rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"path": path, "exists": None, "tail": [], "truncated": False, "error": _short_text(str(exc))}
    all_lines = text.splitlines()
    tail = all_lines[-lines:]
    return {"path": path, "exists": True, "tail": tail, "truncated": len(all_lines) > lines}


def _inspect_report(path: str, *, tail_bytes: int) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"path": path, "exists": False, "no_errors": False, "excerpt": "", "truncated": False}
    if not target.is_file():
        return {
            "path": path,
            "exists": None,
            "no_errors": False,
            "excerpt": "",
            "truncated": False,
            "error": "report path is not a regular file",
        }
    try:
        with target.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - int(tail_bytes)))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"path": path, "exists": None, "no_errors": False, "excerpt": "", "truncated": False, "error": _short_text(str(exc))}
    index = text.lower().rfind(FMRIPREP_NO_ERRORS_MARKER)
    return {
        "path": path,
        "exists": True,
        "no_errors": index >= 0,
        "excerpt": _excerpt_around(text, index, len(FMRIPREP_NO_ERRORS_MARKER)) if index >= 0 else "",
        "truncated": size > int(tail_bytes),
    }


def _excerpt_around(text: str, index: int, marker_len: int) -> str:
    half = max(1, FMRIPREP_REPORT_EXCERPT_CHARS // 2)
    start = max(0, index - half)
    end = min(len(text), index + marker_len + half)
    return " ".join(text[start:end].split())


def _inspect_crashes(crash_dirs: list[str], *, log_lines: int, max_paths: int) -> list[dict[str, Any]]:
    if platform.system() != "Windows":
        quoted_dirs = " ".join(shlex.quote(path) for path in crash_dirs)
        command = (
            f"for d in {quoted_dirs}; do "
            '[ -d "$d" ] || continue; '
            'find "$d" -maxdepth 3 -type f -name \'crash*\' -print; '
            f"done | head -n {int(max_paths)}"
        )
        try:
            result = run_command(
                shell_command(command),
                check=False,
                timeout=MONITOR_PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return [{"source": "crash_scan", "path": path, "excerpt": [], "timed_out": True} for path in crash_dirs]
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return [{"source": "crash_scan", "path": path, "excerpt": [], "error": _short_text(str(exc))} for path in crash_dirs]
        paths = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    else:
        paths = []
        for directory_name in crash_dirs:
            directory = Path(directory_name)
            if not directory.is_dir():
                continue
            for path in directory.rglob("crash*"):
                paths.append(str(path))
                if len(paths) >= max_paths:
                    break
            if len(paths) >= max_paths:
                break
    crashes: list[dict[str, Any]] = []
    for path in paths:
        tail = _tail_log(path, lines=log_lines)
        record = {"path": path, "excerpt": tail.get("tail") or []}
        for key in ("exists", "timed_out", "error"):
            if key in tail:
                record[key] = tail[key]
        if tail.get("exists") is True:
            try:
                record["mtime"] = Path(path).stat().st_mtime
            except OSError:
                pass
        crashes.append(record)
    return crashes


def _mark_stale_crashes(crashes: list[dict[str, Any]], submission: dict[str, Any] | None) -> list[dict[str, Any]]:
    cutoff = _execution_started_timestamp((submission or {}).get("execution") if isinstance(submission, dict) else None)
    if cutoff is None:
        return crashes
    marked: list[dict[str, Any]] = []
    for crash in crashes:
        record = dict(crash)
        try:
            mtime = float(record.get("mtime"))
        except (TypeError, ValueError):
            marked.append(record)
            continue
        if record.get("exists") is True and mtime < cutoff:
            record["stale"] = True
            record["ignored"] = True
            record.pop("mtime", None)
        marked.append(record)
    return marked


def _execution_started_timestamp(execution: Any) -> float | None:
    if not isinstance(execution, dict):
        return None
    values: list[float] = []
    for record in list(execution.get("pre_steps") or []) + list(execution.get("subjects") or []):
        if isinstance(record, dict):
            timestamp = _parse_timestamp(record.get("started_at"))
            if timestamp is not None:
                values.append(timestamp)
    return min(values) if values else None


def _parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _classify_status(
    execution: dict[str, Any],
    *,
    scheduler: dict[str, Any],
    processes: dict[str, Any],
    crashes: list[dict[str, Any]],
    missing_evidence: list[str],
    primary_error: dict[str, str] | None,
) -> str:
    if "submission-result" in missing_evidence:
        return "unknown"
    execution_status = str(execution.get("status") or "unknown")
    strong_primary_error = primary_error is not None and primary_error.get("source") in {"step_stderr", "crash"}
    current_crashes = [
        crash
        for crash in crashes
        if crash.get("source") != "crash_scan" and not crash.get("ignored")
    ]
    if execution_status == "failed" or current_crashes or strong_primary_error:
        return "failed"
    states = [str(state).upper() for state in scheduler.get("states") or []]
    if states:
        if all(state in {"PENDING", "CONFIGURING"} for state in states):
            return "queued"
        return "running"
    if processes.get("visible_pids"):
        return "running"
    if execution_status in {"submitted", "launched"}:
        return "launched-but-not-visible"
    if execution_status in {"success", "completed"}:
        return "completed"
    return "unknown"


def _next_action(status: str, target: str) -> str:
    if status in {"running", "queued"}:
        return "wait"
    if status in {"failed", "launched-but-not-visible"}:
        return "inspect-log"
    if status == "completed" and target == "fmriprep":
        return "request-xcpd"
    return "unknown"


def _status_artifacts(
    request: RequestConfig,
    *,
    submission: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> dict[str, str]:
    artifacts = {"latest_archive": str(latest_audit_path(request))}
    for key, command, artifact in (
        ("execution_context_archive", "execution-context", context),
        ("submission_result_archive", "submission-result", submission),
    ):
        audit_id = _artifact_audit_id(artifact, None)
        submission_id = _artifact_submission_id(artifact, None)
        if audit_id and submission_id:
            artifacts[key] = str(archived_artifact_path(request, command, audit_id=audit_id, submission_id=submission_id))
    return artifacts


def _log_root(request: RequestConfig) -> str | None:
    try:
        return str(request.resolve_log_root())
    except ValueError:
        return None


def _short_text(value: str | None) -> str:
    return " ".join((value or "").split())[:500]


def _bounded_positive_int(value: int, *, maximum: int) -> int:
    return max(1, min(int(value), maximum))


def _is_native_windows_local(remote_host: str | None) -> bool:
    return remote_host is None and platform.system() == "Windows"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = ["HARD_MAX_LOG_LINES", "HARD_MAX_PATHS", "collect_run_status"]
