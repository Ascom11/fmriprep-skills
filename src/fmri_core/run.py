"""Execute planned fMRI commands directly once runtime preparation is complete."""

from __future__ import annotations

import json
import os
import signal
import shlex
import subprocess
import sys
import textwrap
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .models import ProgressCallback, RequestConfig
from .shell import mkdir_p, render_shell, run_command, shell_command, write_text

STARTUP_CHECK_SECONDS = 90.0
XCPD_STARTUP_CHECK_SECONDS = 30.0
STARTUP_CHECK_INTERVAL_SECONDS = 5.0
LOCAL_LAUNCH_GRACE_SECONDS = STARTUP_CHECK_SECONDS
SLURM_STARTED_STATES = {"COMPLETING", "RUNNING"}
SLURM_FAILED_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}
PRE_STEP_TIMEOUT_SECONDS = 300.0
REMOTE_LAUNCH_COMMAND_TIMEOUT_SECONDS = 30
REMOTE_STARTUP_PROBE_TIMEOUT_SECONDS = 10
REMOTE_FILE_IO_TIMEOUT_SECONDS = 20
REMOTE_PRE_STEP_TRANSPORT_TIMEOUT_SECONDS = PRE_STEP_TIMEOUT_SECONDS + 30
PRE_STEP_PROGRESS_INTERVAL_SECONDS = 30.0
PRE_STEP_TIMEOUT_RETURNCODE = 124
PRE_STEP_TAIL_LINES = 80
PRE_STEP_TAIL_MAX_CHARS = 8192


def execute_plan(
    request: RequestConfig,
    execution_plan: dict[str, Any],
    *,
    execution_context_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Execute or submit an already-approved subject plan."""
    stage = "run-xcpd" if request.target == "xcpd" else "run-fmriprep"
    _emit_progress(progress, stage=stage, status="started", message="Preparing execution plan submission")
    backend = str(execution_plan.get("backend") or "").strip().lower()
    if not backend:
        raise ValueError("execution_plan.backend is required")
    if backend not in {"local", "slurm"}:
        raise ValueError(f"Unsupported execution backend: {backend}")
    pre_steps = list(execution_plan.get("pre_steps", []))
    runnable_subjects = [subject for subject in execution_plan.get("subjects", []) if subject.get("steps")]
    skipped_subjects = [subject for subject in execution_plan.get("subjects", []) if not subject.get("steps")]
    if not runnable_subjects:
        return {
            "status": "nothing_to_do",
            "mode": "none",
            "pre_steps": [],
            "subjects": [],
            "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
        }
    max_concurrency = _max_concurrency(execution_plan)
    if backend == "slurm":
        pre_step_result = _run_steps(pre_steps, remote_host=request.remote_host, progress=progress)
        mode = "remote-slurm" if request.remote_host else "local-slurm"
        if pre_step_result["status"] != "success":
            return {
                "status": "failed",
                "mode": mode,
                "pre_steps": pre_step_result["steps"],
                "subjects": [],
                "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
            }
        submission = _submit_subjects_with_slurm_job(
            request,
            execution_plan,
            runnable_subjects,
            max_concurrency=max_concurrency,
            stage=stage,
            progress=progress,
        )
        records = list(submission["subjects"])
        status = _aggregate_launch_status(records)
        return {
            "status": status,
            "mode": mode,
            "startup_check": _aggregate_startup_check(records),
            "launcher_stdout": submission["launcher_stdout"],
            "launcher_stderr": submission["launcher_stderr"],
            "pre_steps": pre_step_result["steps"],
            "subjects": records,
            "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
        }
    if request.remote_host:
        pre_step_result = _run_remote_steps(request.remote_host, pre_steps, progress=progress)
        if pre_step_result["status"] != "success":
            return {
                "status": "failed",
                "mode": "remote-local",
                "pre_steps": pre_step_result["steps"],
                "subjects": [],
                "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
            }
        _emit_progress(
            progress,
            stage=stage,
            status="started",
            message=f"Launching {len(runnable_subjects)} remote-local subject job(s)",
            remote_host=request.remote_host,
        )
        submission = _launch_remote_local_subjects(
            request,
            runnable_subjects,
            max_concurrency=max_concurrency,
            progress=progress,
        )
        records = list(submission["subjects"])
        _emit_progress(
            progress,
            stage=stage,
            status="finished",
            message=f"Launched {len(records)} remote-local subject job(s)",
            remote_host=request.remote_host,
        )
        return {
            "status": _aggregate_launch_status(records),
            "mode": "remote-local",
            "startup_check": _aggregate_startup_check(records),
            "launcher_stdout": submission["launcher_stdout"],
            "launcher_stderr": submission["launcher_stderr"],
            "pid_manifest": submission["pid_manifest"],
            "pool_manager": submission["pool_manager"],
            "pre_steps": pre_step_result["steps"],
            "subjects": records,
            "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
        }
    pre_step_result = _run_local_steps(pre_steps, progress=progress)
    if pre_step_result["status"] != "success":
        return {
            "status": "failed",
            "mode": "local-submitted",
            "pre_steps": pre_step_result["steps"],
            "subjects": [],
            "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
        }
    _emit_progress(progress, stage=stage, status="started", message=f"Launching {len(runnable_subjects)} local subject job(s)")
    submission = _launch_local_subjects_with_pool(
        request,
        runnable_subjects,
        max_concurrency=max_concurrency,
    )
    records = list(submission["subjects"])
    _emit_progress(progress, stage=stage, status="finished", message=f"Launched {len(records)} local subject job(s)")
    return {
        "status": _aggregate_launch_status(records),
        "mode": "local-submitted",
        "startup_check": _aggregate_startup_check(records),
        "launcher_stdout": submission["launcher_stdout"],
        "launcher_stderr": submission["launcher_stderr"],
        "pid_manifest": submission["pid_manifest"],
        "pool_manager": submission["pool_manager"],
        "pre_steps": pre_step_result["steps"],
        "subjects": records,
        "skipped_subjects": _summarize_skipped_subjects(skipped_subjects),
    }


def _max_concurrency(execution_plan: dict[str, Any]) -> int:
    raw = execution_plan.get("max_concurrency", 1)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("execution_plan.max_concurrency must be a positive integer") from exc
    if value < 1:
        raise ValueError("execution_plan.max_concurrency must be a positive integer")
    return value


def _submit_subjects_with_slurm_job(
    request: RequestConfig,
    execution_plan: dict[str, Any],
    subject_plans: list[dict[str, Any]],
    *,
    max_concurrency: int,
    stage: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    run_id = request.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    launcher_dir = request.resolve_log_root() / run_id / "_launcher" / _new_remote_launcher_id()
    scripts_dir = launcher_dir / "scripts"
    array_script = scripts_dir / "slurm-array.sbatch"
    manifest_path = scripts_dir / "subjects.tsv"
    launcher_prefix = "remote-sbatch-launch" if request.remote_host else "local-sbatch-launch"
    launch_stdout, launch_stderr = _launcher_log_paths(launcher_dir, launcher_prefix)
    mkdir_p(launcher_dir, remote_host=request.remote_host)
    mkdir_p(scripts_dir, remote_host=request.remote_host)

    write_text(manifest_path, _render_remote_subject_manifest(subject_plans), remote_host=request.remote_host)
    write_text(
        array_script,
        _render_slurm_job_script(
            request=request,
            resources=execution_plan["resources"],
            launcher_dir=launcher_dir,
            manifest_path=manifest_path,
            subject_count=len(subject_plans),
            max_concurrency=max_concurrency,
        ),
        remote_host=request.remote_host,
    )

    _emit_progress(progress, stage=stage, status="started", message=f"Submitting {len(subject_plans)} subject job(s) with Slurm")
    submit_command = (
        f"{render_shell(['sbatch', '--parsable', str(array_script)])} "
        f"> {shlex.quote(str(launch_stdout))} 2> {shlex.quote(str(launch_stderr))}"
    )
    result = run_command(
        shell_command(submit_command),
        remote_host=request.remote_host,
        check=False,
    )
    launcher_stdout_text = _read_text_if_exists(launch_stdout, remote_host=request.remote_host)
    launcher_stderr_text = _read_text_if_exists(launch_stderr, remote_host=request.remote_host)
    job_id = launcher_stdout_text.strip().split(";")[0] if launcher_stdout_text.strip() else None
    launch = {"status": "failed", "startup_check": _startup_check("failed", checks=[])}
    if result.returncode == 0:
        launch = _verify_slurm_submission(
            job_id,
            remote_host=request.remote_host,
            startup_check_seconds=_startup_check_seconds_for_target(request.target),
        )
    launch_status = str(launch["status"])
    _emit_progress(
        progress,
        stage=stage,
        status="finished" if result.returncode == 0 else "failed",
        message=f"Slurm submission returned {launch_status}",
        remote_host=request.remote_host,
    )

    records: list[dict[str, Any]] = []
    for index, subject_plan in enumerate(subject_plans):
        launcher_stdout = _resolve_array_launcher_path(launcher_dir, job_id, index, stream="stdout")
        launcher_stderr = _resolve_array_launcher_path(launcher_dir, job_id, index, stream="stderr")
        record = {
            "subject_id": subject_plan["subject_id"],
            "session_id": subject_plan.get("session_id"),
            "subject_key": subject_plan["subject_key"],
            "status": launch_status,
            "startup_check": launch["startup_check"],
            "job_id": job_id,
            "array_task_id": index,
            "launcher_stdout": str(launcher_stdout),
            "launcher_stderr": str(launcher_stderr),
            "steps": _step_refs(subject_plan),
        }
        error = (launcher_stderr_text or result.stderr or "").strip()
        if launch_status == "failed" and error:
            record["error"] = error
        records.append(record)
    return {
        "subjects": records,
        "launcher_stdout": str(launch_stdout),
        "launcher_stderr": str(launch_stderr),
    }


def _render_slurm_job_script(
    *,
    request: RequestConfig,
    resources: dict[str, Any],
    launcher_dir: Path,
    manifest_path: Path,
    subject_count: int,
    max_concurrency: int = 1,
) -> str:
    cpus = max(1, int(resources["nthreads_per_job"]))
    max_concurrency = max(1, int(max_concurrency))
    lines = [
        "#!/usr/bin/env bash",
        "#SBATCH -J fmri_array",
        f"#SBATCH -c {cpus}",
        f"#SBATCH --array=0-{subject_count - 1}%{max_concurrency}",
        f"#SBATCH -o {launcher_dir}/%x_%A_%a.stdout.log",
        f"#SBATCH -e {launcher_dir}/%x_%A_%a.stderr.log",
    ]
    if request.scheduler_partition:
        lines.append(f"#SBATCH -p {request.scheduler_partition}")
    slurm_mem_gb = resources.get("slurm_mem_gb")
    if slurm_mem_gb is None:
        slurm_mem_gb = request.slurm_mem_gb
    if slurm_mem_gb is not None:
        lines.append(f"#SBATCH --mem={int(slurm_mem_gb)}G")
    lines.append("set -euo pipefail")
    lines.extend(_render_remote_subject_runner(manifest_path))
    lines.extend(
        [
            "array_task_id=\"${SLURM_ARRAY_TASK_ID:-}\"",
            "if [ -z \"$array_task_id\" ]; then",
            "  echo \"Missing SLURM_ARRAY_TASK_ID\" >&2",
            "  exit 1",
            "fi",
            "run_rows_for_index \"$SLURM_ARRAY_TASK_ID\"",
        ]
    )
    return "\n".join(lines) + "\n"


def _resolve_array_launcher_path(launcher_dir: Path, job_id: str | None, index: int, *, stream: str) -> Path:
    suffix = "stdout" if stream == "stdout" else "stderr"
    if job_id:
        return launcher_dir / f"fmri_array_{job_id}_{index}.{suffix}.log"
    return launcher_dir / f"fmri_array_%A_{index}.{suffix}.log"


def _new_remote_launcher_id() -> str:
    return f"run-{uuid4().hex[:8]}"


def _aggregate_launch_status(records: list[dict[str, Any]]) -> str:
    statuses = {str(record.get("status") or "") for record in records}
    if not statuses:
        return "failed"
    if statuses == {"launched"}:
        return "launched"
    if "failed" in statuses:
        return "failed"
    if "submitted" in statuses:
        return "submitted"
    return next(iter(statuses))


def _aggregate_startup_check(records: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [record.get("startup_check") for record in records if isinstance(record.get("startup_check"), dict)]
    if not checks:
        return _startup_check("skipped", checks=[])
    statuses = {str(check.get("status") or "") for check in checks}
    if "failed" in statuses:
        status = "failed"
    elif statuses == {"passed"}:
        status = "passed"
    elif "not_confirmed" in statuses:
        status = "not_confirmed"
    else:
        status = "skipped"
    return {
        "status": status,
        "duration_seconds": max(float(check.get("duration_seconds") or 0.0) for check in checks),
        "checks": checks,
    }


def _startup_check(status: str, *, checks: list[dict[str, Any]], duration_seconds: float | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "duration_seconds": STARTUP_CHECK_SECONDS if duration_seconds is None else duration_seconds,
        "checks": checks,
    }


def _startup_check_seconds_for_target(target: str) -> float:
    return XCPD_STARTUP_CHECK_SECONDS if target == "xcpd" else STARTUP_CHECK_SECONDS


def _verify_slurm_submission(
    job_id: str | None,
    *,
    remote_host: str | None,
    startup_check_seconds: float | None = None,
) -> dict[str, Any]:
    check_seconds = STARTUP_CHECK_SECONDS if startup_check_seconds is None else startup_check_seconds
    if not job_id:
        return {"status": "submitted", "startup_check": _startup_check("not_confirmed", checks=[])}
    started = time.monotonic()
    checks: list[dict[str, Any]] = []
    while True:
        result = run_command(
            shell_command(render_shell(["squeue", "-h", "-j", str(job_id), "-o", "%T"])),
            remote_host=remote_host,
            check=False,
        )
        elapsed = max(0.0, time.monotonic() - started)
        state = (result.stdout or "").strip()
        checks.append(
            {
                "kind": "slurm",
                "job_id": str(job_id),
                "elapsed_seconds": elapsed,
                "returncode": result.returncode,
                "state": state,
            }
        )
        state_status = _slurm_startup_state(state)
        if result.returncode != 0 or state_status == "failed":
            return {
                "status": "failed",
                "startup_check": _startup_check("failed", checks=checks, duration_seconds=elapsed),
            }
        if state_status == "missing":
            return {
                "status": "submitted",
                "startup_check": _startup_check("not_confirmed", checks=checks, duration_seconds=elapsed),
            }
        if elapsed >= check_seconds:
            if state_status != "started":
                return {
                    "status": "submitted",
                    "startup_check": _startup_check("not_confirmed", checks=checks, duration_seconds=elapsed),
                }
            return {
                "status": "launched",
                "startup_check": _startup_check("passed", checks=checks, duration_seconds=elapsed),
            }
        _sleep_for_startup_check(elapsed, startup_check_seconds=check_seconds)


def _slurm_startup_state(raw_state: str) -> str:
    states = {line.strip().upper() for line in raw_state.splitlines() if line.strip()}
    if not states:
        return "missing"
    if states & SLURM_FAILED_STATES:
        return "failed"
    if states & SLURM_STARTED_STATES:
        return "started"
    return "pending"


def _verify_local_process_launch(process: subprocess.Popen[str]) -> dict[str, Any]:
    poll = getattr(process, "poll", None)
    if callable(poll):
        returncode = poll()
        if returncode is not None:
            return {"status": "failed", "returncode": returncode}
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=LOCAL_LAUNCH_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return {"status": "launched", "returncode": None}
        return {"status": "failed", "returncode": getattr(process, "returncode", None)}
    return {"status": "submitted", "returncode": None}


def _verify_local_pool_manager_launch(
    process: subprocess.Popen[str],
    *,
    startup_check_seconds: float | None = None,
    pid_manifest: Path | None = None,
    expected_start_count: int | None = None,
) -> dict[str, Any]:
    check_seconds = STARTUP_CHECK_SECONDS if startup_check_seconds is None else startup_check_seconds
    if pid_manifest is not None and expected_start_count is not None:
        started = time.monotonic()
        expected = max(1, expected_start_count)
        checks: list[dict[str, Any]] = []
        while True:
            elapsed = max(0.0, time.monotonic() - started)
            pid_by_subject = _parse_remote_local_pids(_read_text_if_exists(pid_manifest, remote_host=None))
            manifest_check = {
                "kind": "local-worker-pool-pid-manifest",
                "path": str(pid_manifest),
                "elapsed_seconds": elapsed,
                "recorded": len(pid_by_subject),
                "expected": expected,
                "returncode": 0 if len(pid_by_subject) >= expected else 1,
            }
            checks.append(manifest_check)
            if len(pid_by_subject) >= expected:
                return {
                    "status": "launched",
                    "returncode": None,
                    "startup_check": _startup_check("passed", checks=checks, duration_seconds=elapsed),
                }
            poll = getattr(process, "poll", None)
            if callable(poll):
                returncode = poll()
                if returncode is not None:
                    return {
                        "status": "failed",
                        "returncode": returncode,
                        "startup_check": _startup_check("failed", checks=checks, duration_seconds=elapsed),
                    }
            if elapsed >= check_seconds:
                wait = getattr(process, "wait", None)
                if callable(wait) and not callable(getattr(process, "poll", None)):
                    try:
                        wait(timeout=0)
                    except subprocess.TimeoutExpired:
                        pass
                    else:
                        return {
                            "status": "failed",
                            "returncode": getattr(process, "returncode", None),
                            "startup_check": _startup_check("failed", checks=checks, duration_seconds=elapsed),
                        }
                return {
                    "status": "submitted",
                    "returncode": None,
                    "startup_check": _startup_check("not_confirmed", checks=checks, duration_seconds=elapsed),
                }
            _sleep_for_startup_check(elapsed, startup_check_seconds=check_seconds)
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=check_seconds)
        except subprocess.TimeoutExpired:
            return {
                "status": "launched",
                "returncode": None,
                "startup_check": _startup_check("passed", checks=[], duration_seconds=check_seconds),
            }
        returncode = getattr(process, "returncode", None)
        return {"status": "failed", "returncode": returncode, "startup_check": _startup_check("failed", checks=[])}
    poll = getattr(process, "poll", None)
    if callable(poll):
        returncode = poll()
        if returncode not in {None, 0}:
            return {"status": "failed", "returncode": returncode, "startup_check": _startup_check("failed", checks=[])}
    return {"status": "submitted", "returncode": None, "startup_check": _startup_check("not_confirmed", checks=[])}


def _sleep_for_startup_check(elapsed: float, *, startup_check_seconds: float | None = None) -> None:
    check_seconds = STARTUP_CHECK_SECONDS if startup_check_seconds is None else startup_check_seconds
    remaining = check_seconds - elapsed
    if remaining <= 0:
        return
    interval = STARTUP_CHECK_INTERVAL_SECONDS if STARTUP_CHECK_INTERVAL_SECONDS > 0 else remaining
    time.sleep(min(interval, remaining))


def _launch_local_subjects_with_pool(
    request: RequestConfig,
    subject_plans: list[dict[str, Any]],
    *,
    max_concurrency: int,
) -> dict[str, Any]:
    run_id = request.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    launcher_dir = request.resolve_log_root() / run_id / "_launcher" / _new_remote_launcher_id()
    scripts_dir = launcher_dir / "scripts"
    subjects_dir = scripts_dir / "subjects"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    subjects_dir.mkdir(parents=True, exist_ok=True)

    pid_manifest = launcher_dir / "local-worker-pool-pids.tsv"
    launch_stdout, launch_stderr = _launcher_log_paths(launcher_dir, "local-worker-pool")
    pool_path = scripts_dir / "local-worker-pool.json"
    pool_subjects = []
    records: list[dict[str, Any]] = []
    for subject_plan in subject_plans:
        subject_key = str(subject_plan["subject_key"])
        subject_path = subjects_dir / f"{subject_key}.json"
        subject_launcher_stdout, subject_launcher_stderr = _remote_local_launcher_logs(launcher_dir, subject_key)
        subject_path.write_text(json.dumps(subject_plan, sort_keys=True) + "\n", encoding="utf-8")
        pool_subjects.append(
            {
                "subject_key": subject_key,
                "subject_path": str(subject_path),
                "launcher_stdout": str(subject_launcher_stdout),
                "launcher_stderr": str(subject_launcher_stderr),
            }
        )
        records.append(
            {
                "subject_id": subject_plan["subject_id"],
                "session_id": subject_plan.get("session_id"),
                "subject_key": subject_key,
                "launcher_stdout": str(subject_launcher_stdout),
                "launcher_stderr": str(subject_launcher_stderr),
                "steps": _step_refs(subject_plan),
            }
        )
    pool_path.write_text(
        json.dumps(
            {
                "max_concurrency": max_concurrency,
                "pid_manifest": str(pid_manifest),
                "subjects": pool_subjects,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with open(launch_stdout, "w", encoding="utf-8") as stdout_handle, open(
        launch_stderr,
        "w",
        encoding="utf-8",
    ) as stderr_handle:
        process = subprocess.Popen(
            [sys.executable, "-c", _local_pool_manager_script(), str(pool_path)],
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )
    launch = _verify_local_pool_manager_launch(
        process,
        startup_check_seconds=_startup_check_seconds_for_target(request.target),
        pid_manifest=pid_manifest,
        expected_start_count=min(max_concurrency, len(subject_plans)),
    )
    pid_by_subject = (
        _parse_remote_local_pids(_read_text_if_exists(pid_manifest, remote_host=None))
        if launch["status"] != "failed"
        else {}
    )
    for record in records:
        subject_key = str(record["subject_key"])
        child_pid = pid_by_subject.get(subject_key)
        subject_launch = _local_pool_subject_launch(child_pid, manager_launch=launch)
        record["status"] = subject_launch["status"]
        record["startup_check"] = subject_launch["startup_check"]
        record["pid"] = child_pid or process.pid
        if subject_launch.get("returncode") is not None:
            record["returncode"] = subject_launch["returncode"]
        if subject_launch.get("error"):
            record["error"] = subject_launch["error"]
    return {
        "subjects": records,
        "launcher_stdout": str(launch_stdout),
        "launcher_stderr": str(launch_stderr),
        "pid_manifest": str(pid_manifest),
        "pool_manager": {
            "pid": process.pid,
            "max_concurrency": max_concurrency,
            "manifest": str(pool_path),
        },
    }


def _launch_remote_local_subjects(
    request: RequestConfig,
    subject_plans: list[dict[str, Any]],
    *,
    max_concurrency: int,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    remote_host = request.remote_host
    if not remote_host:
        raise ValueError("remote_host is required for remote-local launch")
    run_id = request.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    launcher_dir = request.resolve_log_root() / run_id / "_launcher" / _new_remote_launcher_id()
    scripts_dir = launcher_dir / "scripts"
    runner_path = scripts_dir / "remote-local-launch.sh"
    manifest_path = scripts_dir / "subjects.tsv"
    pid_manifest = launcher_dir / "remote-local-pids.tsv"
    manager_stdout, manager_stderr = _launcher_log_paths(launcher_dir, "remote-local-launch")

    mkdir_p(launcher_dir, remote_host=remote_host)
    mkdir_p(scripts_dir, remote_host=remote_host)
    write_text(manifest_path, _render_remote_subject_manifest(subject_plans), remote_host=remote_host)
    write_text(
        runner_path,
        _render_remote_local_launch_script(manifest_path, launcher_dir, max_concurrency=max_concurrency),
        remote_host=remote_host,
    )

    _emit_progress(
        progress,
        stage="remote-local",
        status="started",
        message=f"Launching {len(subject_plans)} remote-local subject job(s)",
        remote_host=remote_host,
    )
    launch_command = (
        f"nohup {render_shell(['bash', str(runner_path), '--manager'])} "
        f"> {shlex.quote(str(manager_stdout))} 2> {shlex.quote(str(manager_stderr))} "
        f"< /dev/null & echo $!"
    )
    launch_error = None
    try:
        result = run_command(
            shell_command(launch_command),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_LAUNCH_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(
            shell_command(launch_command).body,
            PRE_STEP_TIMEOUT_RETURNCODE,
            "",
            "",
        )
        launch_error = (
            "remote-local launch command timed out after "
            f"{_format_seconds(float(REMOTE_LAUNCH_COMMAND_TIMEOUT_SECONDS))}"
        )
    manager_pid = _parse_single_pid(result.stdout)
    launcher_stdout_text = _read_text_if_exists(manager_stdout, remote_host=remote_host)
    launcher_stderr_text = _read_text_if_exists(manager_stderr, remote_host=remote_host)
    manager_launch = (
        _verify_remote_local_pool(
            manager_pid,
            pid_manifest=pid_manifest,
            subject_keys=[str(subject_plan["subject_key"]) for subject_plan in subject_plans],
            remote_host=remote_host,
            startup_check_seconds=_startup_check_seconds_for_target(request.target),
        )
        if result.returncode == 0 and manager_pid is not None
        else {
            "status": "failed",
            "startup_check": _startup_check("failed", checks=[]),
            "error": launch_error or "remote-local launch did not return a manager pid",
            "pid_by_subject": {},
            "launch_by_subject": {},
        }
    )
    pid_by_subject = dict(manager_launch.get("pid_by_subject") or {})
    launch_by_subject = dict(manager_launch.get("launch_by_subject") or {})
    records: list[dict[str, Any]] = []
    for subject_plan in subject_plans:
        subject_key = str(subject_plan["subject_key"])
        child_pid = pid_by_subject.get(subject_key)
        pid = child_pid or manager_pid
        launch = launch_by_subject.get(subject_key)
        if launch is None:
            launch = (
                dict(manager_launch) if manager_launch["status"] == "failed" else {
                "status": "submitted",
                "startup_check": _startup_check("not_confirmed", checks=[]),
                "error": "remote-local child pid was not recorded before startup check completed",
            }
            )
        if result.returncode != 0:
            launch = {
                "status": "failed",
                "startup_check": _startup_check("failed", checks=[]),
                "error": (
                    launcher_stderr_text
                    or launcher_stdout_text
                    or result.stderr
                    or result.stdout
                    or ""
                ).strip()
                or launch_error
                or "remote-local launch command failed",
            }
        _emit_progress(
            progress,
            stage="remote-local",
            status=launch["status"],
            message=f"Remote-local subject {subject_key} launch returned {launch['status']}",
            remote_host=remote_host,
            pid=pid,
        )
        launcher_stdout, launcher_stderr = _remote_local_launcher_logs(launcher_dir, subject_key)
        payload = {
            "subject_id": subject_plan["subject_id"],
            "session_id": subject_plan.get("session_id"),
            "subject_key": subject_key,
            "status": launch["status"],
            "pid": pid,
            "launcher_stdout": str(launcher_stdout),
            "launcher_stderr": str(launcher_stderr),
            "steps": _step_refs(subject_plan),
        }
        if launch.get("error"):
            payload["error"] = launch["error"]
        payload["startup_check"] = launch["startup_check"]
        records.append(payload)
    return {
        "subjects": records,
        "launcher_stdout": str(manager_stdout),
        "launcher_stderr": str(manager_stderr),
        "pid_manifest": str(pid_manifest),
        "pool_manager": {
            "pid": manager_pid,
            "max_concurrency": max_concurrency,
            "script": str(runner_path),
        },
    }


def _parse_single_pid(text: str | None) -> int | None:
    pid = None
    for token in (text or "").split():
        if token.isdigit():
            pid = int(token)
    return pid


def _parse_remote_local_pids(text: str) -> dict[str, int]:
    pids: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.rstrip("\n").split("\t")
        if not fields or fields[0] == "subject_key" or len(fields) < 5 or not fields[1].isdigit():
            continue
        pids[fields[0]] = int(fields[1])
    return pids


def _verify_remote_local_pool(
    manager_pid: int,
    *,
    pid_manifest: Path,
    subject_keys: list[str],
    remote_host: str,
    startup_check_seconds: float | None = None,
) -> dict[str, Any]:
    check_seconds = STARTUP_CHECK_SECONDS if startup_check_seconds is None else startup_check_seconds
    started = time.monotonic()
    checks: list[dict[str, Any]] = []
    pid_by_subject: dict[str, int] = {}
    launch_by_subject: dict[str, dict[str, Any]] = {}
    pending = set(subject_keys)
    while True:
        elapsed = max(0.0, time.monotonic() - started)
        manager_check = _remote_local_pid_check(manager_pid, remote_host=remote_host, elapsed=elapsed)
        checks.append(manager_check)
        if manager_check["returncode"] != 0:
            return {
                "status": "failed",
                "startup_check": _startup_check("failed", checks=checks, duration_seconds=elapsed),
                "error": manager_check.get("error") or f"remote process {manager_pid} is not running",
                "pid_by_subject": pid_by_subject,
                "launch_by_subject": {
                    subject_key: {
                        "status": "failed",
                        "startup_check": _startup_check("failed", checks=checks, duration_seconds=elapsed),
                        "error": manager_check.get("error") or f"remote process {manager_pid} is not running",
                    }
                    for subject_key in subject_keys
                },
            }
        pid_by_subject.update(_parse_remote_local_pids(_read_text_if_exists(pid_manifest, remote_host=remote_host)))
        for subject_key in sorted(pending & set(pid_by_subject)):
            child_pid = pid_by_subject[subject_key]
            child_check = _remote_local_pid_check(child_pid, remote_host=remote_host, elapsed=elapsed)
            checks.append(child_check)
            if child_check["returncode"] == 0:
                launch_by_subject[subject_key] = {
                    "status": "launched",
                    "startup_check": _startup_check("passed", checks=[child_check], duration_seconds=elapsed),
                }
                pending.discard(subject_key)
        if not pending:
            return {
                "status": "launched",
                "startup_check": _startup_check("passed", checks=checks, duration_seconds=elapsed),
                "pid_by_subject": pid_by_subject,
                "launch_by_subject": launch_by_subject,
            }
        if elapsed >= check_seconds:
            return {
                "status": "submitted",
                "startup_check": _startup_check("not_confirmed", checks=checks, duration_seconds=elapsed),
                "pid_by_subject": pid_by_subject,
                "launch_by_subject": launch_by_subject,
            }
        _sleep_for_startup_check(elapsed, startup_check_seconds=check_seconds)


def _local_pool_subject_launch(child_pid: int | None, *, manager_launch: dict[str, Any]) -> dict[str, Any]:
    if manager_launch["status"] == "failed":
        return dict(manager_launch)
    if child_pid is None:
        return {
            "status": "submitted",
            "startup_check": _startup_check("not_confirmed", checks=[]),
            "error": "local worker-pool child pid was not recorded before startup check completed",
        }
    if manager_launch["status"] != "launched":
        return {
            "status": "submitted",
            "startup_check": _startup_check("not_confirmed", checks=[]),
        }
    duration = float((manager_launch.get("startup_check") or {}).get("duration_seconds") or STARTUP_CHECK_SECONDS)
    check = {
        "kind": "local-worker",
        "pid": child_pid,
        "elapsed_seconds": duration,
        "returncode": 0 if _local_pid_running(child_pid) else 1,
    }
    if check["returncode"] == 0:
        return {
            "status": "launched",
            "startup_check": _startup_check("passed", checks=[check], duration_seconds=duration),
        }
    return {
        "status": "failed",
        "startup_check": _startup_check("failed", checks=[check], duration_seconds=duration),
        "error": f"local worker process {child_pid} is not running",
    }


def _local_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _launcher_log_paths(launcher_dir: Path, prefix: str) -> tuple[Path, Path]:
    return (
        launcher_dir / f"{prefix}.stdout.log",
        launcher_dir / f"{prefix}.stderr.log",
    )


def _read_text_if_exists(path: Path, *, remote_host: str | None) -> str:
    if remote_host is None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""
    quoted = shlex.quote(str(path))
    try:
        result = run_command(
            shell_command(f"test -f {quoted} && cat {quoted}"),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_FILE_IO_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _remote_local_launcher_logs(launcher_dir: Path, subject_key: str) -> tuple[Path, Path]:
    return (
        launcher_dir / f"{subject_key}.launcher.stdout.log",
        launcher_dir / f"{subject_key}.launcher.stderr.log",
    )


def _render_remote_local_launch_script(manifest_path: Path, launcher_dir: Path, *, max_concurrency: int = 1) -> str:
    max_concurrency = max(1, int(max_concurrency))
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"launcher_dir={shlex.quote(str(launcher_dir))}",
        f"max_concurrency={max_concurrency}",
        "pids_path=\"$launcher_dir/remote-local-pids.tsv\"",
        *_render_remote_subject_runner(manifest_path),
        "if [ \"${1:-}\" = \"--index\" ]; then",
        "  run_rows_for_index \"${2:-}\"",
        "  exit 0",
        "fi",
        "if [ \"${1:-}\" = \"--manager\" ]; then",
        "  printf 'subject_key\\tpid\\tlauncher_stdout\\tlauncher_stderr\\tstarted_at\\n' > \"$pids_path\"",
        "last_array_index=",
        "while IFS=$'\\t' read -r array_index subject_key _rest; do",
        "  [ -n \"$array_index\" ] || continue",
        "  [ \"$array_index\" != \"$last_array_index\" ] || continue",
        "  last_array_index=\"$array_index\"",
    ]
    lines.extend(
        [
            "  launcher_stdout=\"$launcher_dir/$subject_key.launcher.stdout.log\"",
            "  launcher_stderr=\"$launcher_dir/$subject_key.launcher.stderr.log\"",
            "  while [ \"$(jobs -pr | wc -l)\" -ge \"$max_concurrency\" ]; do",
            "    sleep 1",
            "  done",
            "  nohup bash \"$0\" --index \"$array_index\" > \"$launcher_stdout\" 2> \"$launcher_stderr\" < /dev/null &",
            "  pid=\"$!\"",
            "  started_at=$(date -Iseconds)",
            "  printf '%s\\t%s\\t%s\\t%s\\t%s\\n' \"$subject_key\" \"$pid\" \"$launcher_stdout\" \"$launcher_stderr\" \"$started_at\" >> \"$pids_path\"",
        "done < \"$subjects_manifest\"",
            "  wait || true",
            "  exit 0",
            "fi",
            "echo \"Usage: $0 --manager or $0 --index <array_index>\" >&2",
            "exit 2",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_remote_subject_manifest(subject_plans: list[dict[str, Any]]) -> str:
    lines = []
    for array_index, subject in enumerate(subject_plans):
        for step in subject.get("steps") or []:
            bids_filter = step.get("bids_filter")
            bids_filter_path = "-"
            bids_filter_json = "-"
            if isinstance(bids_filter, dict) and bids_filter.get("path"):
                bids_filter_path = str(bids_filter["path"])
                bids_filter_json = json.dumps(bids_filter.get("content") or {}, sort_keys=True, separators=(",", ":"))
            fields = [
                str(array_index),
                str(subject["subject_key"]),
                str(subject["subject_id"]),
                str(step["step"]),
                str(step["work_dir"]),
                str(step["output_dir"]),
                str(step["stdout_path"]),
                str(step["stderr_path"]),
                bids_filter_path,
                bids_filter_json,
                render_shell([str(token) for token in step.get("command", [])]),
            ]
            if any("\t" in field or "\n" in field for field in fields):
                raise ValueError(f"subject manifest field contains a tab or newline: {subject['subject_key']}")
            lines.append("\t".join(fields))
    return "\n".join(lines) + "\n"


def _render_remote_subject_runner(manifest_path: Path) -> list[str]:
    lines = [
        f"subjects_manifest={shlex.quote(str(manifest_path))}",
        "run_rows_for_index() {",
        "  target_index=\"$1\"",
        "  found=0",
        "  while IFS=$'\\t' read -r array_index subject_key subject_id step_name work_dir output_dir stdout_path stderr_path bids_filter_path bids_filter_json command_shell; do",
        "    [ -n \"$array_index\" ] || continue",
        "    [ \"$array_index\" = \"$target_index\" ] || continue",
        "    found=1",
        "    log_dir=$(dirname \"$stdout_path\")",
        "    mkdir -p \"$work_dir\" \"$output_dir\" \"$log_dir\"",
        "    if [ \"$bids_filter_path\" != \"-\" ]; then",
        "      mkdir -p \"$(dirname \"$bids_filter_path\")\"",
        "      printf '%s\\n' \"$bids_filter_json\" > \"$bids_filter_path\"",
        "    fi",
        "    bash -c \"$command_shell\" > \"$stdout_path\" 2> \"$stderr_path\"",
        "  done < \"$subjects_manifest\"",
        "  if [ \"$found\" != \"1\" ]; then",
        "    echo \"Unknown subject index: $target_index\" >&2",
        "    return 2",
        "  fi",
        "}",
    ]
    return lines


def _remote_local_pid_check(pid: int, *, remote_host: str, elapsed: float) -> dict[str, Any]:
    try:
        result = run_command(
            shell_command(render_shell(["kill", "-0", str(pid)])),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_STARTUP_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(
            render_shell(["kill", "-0", str(pid)]),
            PRE_STEP_TIMEOUT_RETURNCODE,
            "",
            (
                "remote pid check timed out after "
                f"{_format_seconds(float(REMOTE_STARTUP_PROBE_TIMEOUT_SECONDS))}"
            ),
        )
    check = {
        "kind": "remote-local",
        "pid": pid,
        "elapsed_seconds": elapsed,
        "returncode": result.returncode,
    }
    if result.returncode != 0:
        check["error"] = (result.stderr or result.stdout or "").strip() or f"remote process {pid} is not running"
    return check


def _verify_remote_local_process(pid: int | None, *, remote_host: str) -> dict[str, Any]:
    if pid is None:
        return {
            "status": "failed",
            "startup_check": _startup_check("failed", checks=[]),
            "error": "remote-local launch did not return a pid",
        }
    started = time.monotonic()
    checks: list[dict[str, Any]] = []
    while True:
        elapsed = max(0.0, time.monotonic() - started)
        check = _remote_local_pid_check(pid, remote_host=remote_host, elapsed=elapsed)
        checks.append(check)
        if check["returncode"] != 0:
            return {
                "status": "failed",
                "startup_check": _startup_check("failed", checks=checks, duration_seconds=elapsed),
                "error": check.get("error") or f"remote process {pid} is not running",
            }
        if elapsed >= STARTUP_CHECK_SECONDS:
            return {
                "status": "launched",
                "startup_check": _startup_check("passed", checks=checks, duration_seconds=elapsed),
            }
        _sleep_for_startup_check(elapsed)


def _local_subject_runner_script() -> str:
    return textwrap.dedent(
        """
        import json
        import subprocess
        import sys
        from pathlib import Path

        def ensure_step_dirs(step):
            Path(step["work_dir"]).mkdir(parents=True, exist_ok=True)
            Path(step["output_dir"]).mkdir(parents=True, exist_ok=True)
            Path(step["stdout_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(step["stderr_path"]).parent.mkdir(parents=True, exist_ok=True)

        def write_bids_filter(step):
            bids_filter = step.get("bids_filter")
            if not isinstance(bids_filter, dict) or not bids_filter.get("path"):
                return
            filter_path = Path(str(bids_filter["path"]))
            filter_path.parent.mkdir(parents=True, exist_ok=True)
            filter_path.write_text(
                json.dumps(bids_filter.get("content") or {}, sort_keys=True) + "\\n",
                encoding="utf-8",
            )

        def run_subject(subject_path):
            subject_plan = json.loads(Path(subject_path).read_text(encoding="utf-8"))
            for step in subject_plan.get("steps", []):
                ensure_step_dirs(step)
                write_bids_filter(step)
                with open(step["stdout_path"], "w", encoding="utf-8") as stdout_handle, open(
                    step["stderr_path"],
                    "w",
                    encoding="utf-8",
                ) as stderr_handle:
                    result = subprocess.run(
                        step["command"],
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        check=False,
                    )
                if result.returncode != 0:
                    return result.returncode
            return 0

        if __name__ == "__main__":
            raise SystemExit(run_subject(sys.argv[1]))
        """
    ).strip() + "\n"


def _local_pool_manager_script() -> str:
    subject_runner = _local_subject_runner_script()
    return textwrap.dedent(
        f"""
        import json
        import subprocess
        import sys
        import time
        from pathlib import Path

        SUBJECT_RUNNER = {subject_runner!r}

        def launch_subject(entry):
            stdout_path = Path(entry["launcher_stdout"])
            stderr_path = Path(entry["launcher_stderr"])
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_path.open("w", encoding="utf-8")
            stderr_handle = stderr_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, "-c", SUBJECT_RUNNER, entry["subject_path"]],
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
            )
            return process, stdout_handle, stderr_handle

        def close_handles(handles):
            for handle in handles:
                try:
                    handle.close()
                except OSError:
                    pass

        def run_pool(pool_path):
            pool = json.loads(Path(pool_path).read_text(encoding="utf-8"))
            max_concurrency = max(1, int(pool.get("max_concurrency") or 1))
            pending = list(pool.get("subjects") or [])
            active = []
            pids_path = Path(pool["pid_manifest"])
            pids_path.parent.mkdir(parents=True, exist_ok=True)
            with pids_path.open("w", encoding="utf-8") as stream:
                stream.write("subject_key\\tpid\\tlauncher_stdout\\tlauncher_stderr\\tstarted_at\\n")
                while pending or active:
                    while pending and len(active) < max_concurrency:
                        entry = pending.pop(0)
                        process, stdout_handle, stderr_handle = launch_subject(entry)
                        stream.write(
                            f"{{entry['subject_key']}}\\t{{process.pid}}\\t{{entry['launcher_stdout']}}\\t"
                            f"{{entry['launcher_stderr']}}\\t{{time.strftime('%Y-%m-%dT%H:%M:%S%z')}}\\n"
                        )
                        stream.flush()
                        active.append((entry, process, stdout_handle, stderr_handle))
                    remaining = []
                    for entry, process, stdout_handle, stderr_handle in active:
                        if process.poll() is None:
                            remaining.append((entry, process, stdout_handle, stderr_handle))
                        else:
                            close_handles([stdout_handle, stderr_handle])
                    active = remaining
                    if pending or active:
                        time.sleep(1)
            return 0

        if __name__ == "__main__":
            raise SystemExit(run_pool(sys.argv[1]))
        """
    ).strip() + "\n"


def _run_local_steps(steps: list[dict[str, Any]], *, progress: ProgressCallback | None = None) -> dict[str, Any]:
    completed_steps: list[dict[str, Any]] = []
    for step in steps:
        completed_steps.append(_run_local_step(step, progress=progress))
        if completed_steps[-1]["returncode"] != 0:
            return {"status": "failed", "steps": completed_steps}
    return {"status": "success", "steps": completed_steps}


def _run_steps(
    steps: list[dict[str, Any]],
    *,
    remote_host: str | None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if remote_host:
        return _run_remote_steps(remote_host, steps, progress=progress)
    return _run_local_steps(steps, progress=progress)


def _run_local_step(step: dict[str, Any], *, progress: ProgressCallback | None = None) -> dict[str, Any]:
    _ensure_step_dirs(step)
    step_name = str(step["step"])
    timeout_seconds = float(PRE_STEP_TIMEOUT_SECONDS)
    progress_interval = float(PRE_STEP_PROGRESS_INTERVAL_SECONDS)
    started = time.monotonic()
    next_progress = progress_interval
    _emit_progress(
        progress,
        stage="pre-step",
        status="started",
        message=(
            f"Starting pre-step {step_name}; timeout={_format_seconds(timeout_seconds)}; "
            f"stdout={step['stdout_path']}; stderr={step['stderr_path']}"
        ),
        stdout_path=step["stdout_path"],
        stderr_path=step["stderr_path"],
    )
    with open(step["stdout_path"], "w", encoding="utf-8") as stdout_handle, open(
        step["stderr_path"],
        "w",
        encoding="utf-8",
    ) as stderr_handle:
        process = subprocess.Popen(
            step["command"],
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )
        while True:
            returncode = process.poll()
            elapsed = time.monotonic() - started
            if returncode is not None:
                _emit_progress(
                    progress,
                    stage="pre-step",
                    status="finished",
                    message=f"Completed pre-step {step_name}; elapsed={_format_seconds(elapsed)}; returncode={returncode}",
                    stdout_path=step["stdout_path"],
                    stderr_path=step["stderr_path"],
                    elapsed_seconds=round(elapsed, 2),
                )
                payload = _pre_step_payload(step, returncode, elapsed_seconds=round(elapsed, 2))
                if returncode != 0:
                    stderr_tail = _local_tail(step["stderr_path"])
                    stdout_tail = _local_tail(step["stdout_path"])
                    payload = _pre_step_failure_payload(
                        step,
                        step_name,
                        returncode,
                        stderr_tail,
                        stdout_tail,
                        elapsed_seconds=round(elapsed, 2),
                    )
                return payload
            if elapsed >= timeout_seconds:
                _terminate_process_group(process)
                _emit_progress(
                    progress,
                    stage="pre-step",
                    status="failed",
                    message=(
                        f"Timed out pre-step {step_name} after {_format_seconds(timeout_seconds)}; "
                        f"stdout={step['stdout_path']}; stderr={step['stderr_path']}"
                    ),
                    stdout_path=step["stdout_path"],
                    stderr_path=step["stderr_path"],
                    elapsed_seconds=round(elapsed, 2),
                )
                stderr_tail = _local_tail(step["stderr_path"])
                stdout_tail = _local_tail(step["stdout_path"])
                return _pre_step_failure_payload(
                    step,
                    step_name,
                    PRE_STEP_TIMEOUT_RETURNCODE,
                    stderr_tail,
                    stdout_tail,
                    elapsed_seconds=round(elapsed, 2),
                    timed_out=True,
                    timeout_seconds=timeout_seconds,
                )
            if progress_interval > 0 and elapsed >= next_progress:
                _emit_progress(
                    progress,
                    stage="pre-step",
                    status="running",
                    message=(
                        f"Running pre-step {step_name}; elapsed={_format_seconds(elapsed)}; "
                        f"timeout={_format_seconds(timeout_seconds)}; stderr={step['stderr_path']}"
                    ),
                    stdout_path=step["stdout_path"],
                    stderr_path=step["stderr_path"],
                    elapsed_seconds=round(elapsed, 2),
                )
                next_progress += progress_interval
            time.sleep(min(1.0, max(0.01, progress_interval / 2 if progress_interval > 0 else 0.1)))


def _emit_progress(progress: ProgressCallback | None, **event: Any) -> None:
    if progress is not None:
        progress(event)


def _format_seconds(seconds: float) -> str:
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:.2f}s"


def _pre_step_payload(
    step: dict[str, Any],
    returncode: int,
    *,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    payload = {
        "step": step["step"],
        "returncode": returncode,
        "stdout_path": step["stdout_path"],
        "stderr_path": step["stderr_path"],
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = elapsed_seconds
    return payload


def _pre_step_failure_payload(
    step: dict[str, Any],
    step_name: str,
    returncode: int,
    stderr_tail: str,
    stdout_tail: str,
    *,
    elapsed_seconds: float | None = None,
    timed_out: bool = False,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    payload = _pre_step_payload(step, returncode, elapsed_seconds=elapsed_seconds)
    if timed_out:
        payload["timed_out"] = True
        payload["timeout_seconds"] = timeout_seconds
    payload.update(
        {
            "error": _pre_step_error(
                step_name,
                returncode,
                stderr_tail,
                stdout_tail,
                timed_out=timed_out,
                timeout_seconds=timeout_seconds,
            ),
            "stderr_tail": stderr_tail,
            "stdout_tail": stdout_tail,
        }
    )
    return payload


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()


def _local_tail(path: str | os.PathLike[str]) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return _cap_tail("".join(deque(handle, maxlen=PRE_STEP_TAIL_LINES)))
    except OSError:
        return ""


def _remote_tail(remote_host: str, path: str | os.PathLike[str]) -> str:
    try:
        result = run_command(
            shell_command(render_shell(["tail", "-n", str(PRE_STEP_TAIL_LINES), str(path)])),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_FILE_IO_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return _cap_tail(result.stdout or "")


def _cap_tail(text: str) -> str:
    return text[-PRE_STEP_TAIL_MAX_CHARS:].strip()


def _pre_step_error(
    step_name: str,
    returncode: int,
    stderr_tail: str,
    stdout_tail: str,
    *,
    timed_out: bool = False,
    timeout_seconds: float | None = None,
) -> str:
    if timed_out:
        seconds = _format_seconds(float(timeout_seconds or PRE_STEP_TIMEOUT_SECONDS))
        return f"pre-step {step_name} timed out after {seconds}"
    detail = stderr_tail.strip() or stdout_tail.strip()
    if detail:
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        return " ".join((lines[-1] if lines else detail).split())[:500]
    return f"pre-step {step_name} failed with returncode={returncode}"


def _run_remote_steps(
    remote_host: str,
    steps: list[dict[str, Any]],
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    completed_steps: list[dict[str, Any]] = []
    for step in steps:
        completed_steps.append(_run_remote_step(remote_host, step, progress=progress))
        if completed_steps[-1]["returncode"] != 0:
            return {"status": "failed", "steps": completed_steps}
    return {"status": "success", "steps": completed_steps}


def _run_remote_step(
    remote_host: str,
    step: dict[str, Any],
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    step_name = str(step["step"])
    timeout_seconds = float(PRE_STEP_TIMEOUT_SECONDS)
    started = time.monotonic()
    dirs = [
        str(PurePosixPath(str(step["stdout_path"])).parent),
        str(PurePosixPath(str(step["stderr_path"])).parent),
        str(step["output_dir"]),
    ]
    if step.get("work_dir"):
        dirs.append(str(step["work_dir"]))
    shell_body = "\n".join(
        [
            "set -euo pipefail",
            "mkdir -p "
            + " ".join(shlex.quote(value) for value in dict.fromkeys(dirs)),
            f"{render_shell(step['command'])} > {shlex.quote(str(step['stdout_path']))} 2> {shlex.quote(str(step['stderr_path']))}",
        ]
    )
    command = f"timeout -k 5s {_format_seconds(timeout_seconds)} bash -c {shlex.quote(shell_body)}"
    _emit_progress(
        progress,
        stage="pre-step",
        status="started",
        message=f"Starting remote pre-step {step_name}; timeout={_format_seconds(timeout_seconds)}",
        remote_host=remote_host,
        stdout_path=step["stdout_path"],
        stderr_path=step["stderr_path"],
    )
    try:
        result = run_command(
            shell_command(command),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_PRE_STEP_TRANSPORT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(
            shell_command(command).body,
            PRE_STEP_TIMEOUT_RETURNCODE,
            "",
            "",
        )
    elapsed = time.monotonic() - started
    timed_out = result.returncode == PRE_STEP_TIMEOUT_RETURNCODE
    _emit_progress(
        progress,
        stage="pre-step",
        status="finished" if result.returncode == 0 else "failed",
        message=(
            f"Timed out remote pre-step {step_name} after {_format_seconds(timeout_seconds)}"
            if timed_out
            else f"Completed remote pre-step {step_name}; elapsed={_format_seconds(elapsed)}; returncode={result.returncode}"
        ),
        remote_host=remote_host,
        stdout_path=step["stdout_path"],
        stderr_path=step["stderr_path"],
        elapsed_seconds=round(elapsed, 2),
    )
    payload = _pre_step_payload(step, result.returncode, elapsed_seconds=round(elapsed, 2))
    if result.returncode != 0:
        stderr_tail = _remote_tail(remote_host, step["stderr_path"])
        stdout_tail = _remote_tail(remote_host, step["stdout_path"])
        payload = _pre_step_failure_payload(
            step,
            step_name,
            result.returncode,
            stderr_tail,
            stdout_tail,
            elapsed_seconds=round(elapsed, 2),
            timed_out=timed_out,
            timeout_seconds=timeout_seconds if timed_out else None,
        )
    return payload


def _step_refs(subject_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "step": step["step"],
            "stdout_path": step["stdout_path"],
            "stderr_path": step["stderr_path"],
            "work_dir": step["work_dir"],
            "output_dir": step["output_dir"],
        }
        for step in subject_plan.get("steps", [])
    ]


def _summarize_skipped_subjects(subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "subject_id": subject["subject_id"],
            "session_id": subject.get("session_id"),
            "subject_key": subject.get("subject_key"),
            "status": subject.get("status"),
            "reason_codes": list(subject.get("reason_codes", [])),
        }
        for subject in subjects
    ]


def _ensure_step_dirs(step: dict[str, Any]) -> None:
    if step.get("work_dir"):
        Path(step["work_dir"]).mkdir(parents=True, exist_ok=True)
    Path(step["output_dir"]).mkdir(parents=True, exist_ok=True)
    Path(step["stdout_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(step["stderr_path"]).parent.mkdir(parents=True, exist_ok=True)
