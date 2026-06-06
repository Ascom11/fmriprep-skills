"""Artifact persistence helpers and stage summaries for fmri_process."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .issue_codes import (
    ISSUE_BY_CODE,
    ISSUE_DESCRIPTIONS,
    PREPARE_REQUIRED_RUNTIME_CODES,
    REQUIRED_BLOCKER_FIELDS,
    XCPD_ISSUE_CODES,
    issue_bucket_findings,
    issue_findings,
)
from .models import RequestConfig, RequestPath
from .shell import path_exists, read_text, read_texts, write_text
from .storage_check import compact_storage_check_report

ARTIFACT_SCHEMA_VERSION = 30
RUNTIME_PROOFS_FILENAME = "runtime-proofs.json"
AUDIT_BUCKETS = {
    "fmriprep": "fmriprep_audit",
    "xcpd": "xcpd_audit",
}
ARTIFACT_FILENAMES = {
    "runtime-audit": "runtime-audit.json",
    "dataset-audit": "dataset-audit.json",
    "dataset-audit-debug": "dataset-audit-debug.json",
    "xcpd-runtime-audit": "xcpd-runtime-audit.json",
    "xcpd-dataset-audit": "xcpd-dataset-audit.json",
    "xcpd-dataset-audit-debug": "xcpd-dataset-audit-debug.json",
    "execution-context": "execution-context.json",
    "submission-result": "submission-result.json",
}
LATEST_AUDIT_FILENAME = "latest.json"
ARTIFACT_PAYLOAD_KEYS = {
    "runtime-audit": "runtime_audit",
    "dataset-audit": "dataset_audit",
    "dataset-audit-debug": "dataset_audit",
    "xcpd-runtime-audit": "runtime_audit",
    "xcpd-dataset-audit": "dataset_audit",
    "xcpd-dataset-audit-debug": "dataset_audit",
}
XCPD_DATASET_REQUEST_SIGNATURE_FIELDS = (
    "fmriprep_derivatives",
    "output_root",
    "remote_host",
    "subjects",
    "sessions",
    "xcpd_mode",
    "xcpd_min_time",
    "xcpd_min_time_explicit",
    "xcpd_motion_filter_type",
    "xcpd_band_stop_min",
    "xcpd_band_stop_max",
    "xcpd_motion_filter_order",
    "xcpd_despike",
    "xcpd_task_ids",
    "xcpd_bids_filter_file",
    "xcpd_datasets",
    "xcpd_mem_mb",
    "xcpd_custom_args",
)
XCPD_RUNTIME_REQUEST_SIGNATURE_FIELDS = (
    "fmriprep_derivatives",
    "output_root",
    "remote_host",
    "work_root",
    "log_root",
    "download_root",
    "fs_license",
    "templateflow_home",
    "templateflow_tool_bins",
    "xcpd_image",
    "xcpd_mode",
    "xcpd_min_time",
    "xcpd_min_time_explicit",
    "xcpd_motion_filter_type",
    "xcpd_band_stop_min",
    "xcpd_band_stop_max",
    "xcpd_motion_filter_order",
    "xcpd_despike",
    "xcpd_task_ids",
    "xcpd_bids_filter_file",
    "xcpd_datasets",
    "xcpd_mem_mb",
    "xcpd_custom_args",
    "container_runtime",
    "executor_policy",
    "scheduler_partition",
    "nthreads_per_job",
    "omp_nthreads",
    "slurm_mem_gb",
    "max_jobs",
    "wsl_vhdx_path",
    "windows_host_drive",
    "docker_wsl_storage_path",
)
FMRIPREP_RUNTIME_REQUEST_SIGNATURE_FIELDS = (
    "bids_root",
    "output_root",
    "remote_host",
    "work_root",
    "log_root",
    "download_root",
    "fs_license",
    "templateflow_home",
    "templateflow_tool_bins",
    "fmriprep_image",
    "container_runtime",
    "executor_policy",
    "scheduler_partition",
    "skip_bids_validation",
    "nthreads_per_job",
    "omp_nthreads",
    "slurm_mem_gb",
    "max_jobs",
    "fs_no_reconall",
    "task_id",
    "echo_idx",
    "anat_only",
    "output_spaces",
    "cifti_output",
    "fmriprep_custom_args",
    "wsl_vhdx_path",
    "windows_host_drive",
    "docker_wsl_storage_path",
)

_SHARED_REQUEST_SIGNATURE_FIELDS = (
    "bids_root",
    "output_root",
    "remote_host",
    "subjects",
    "sessions",
    "fs_no_reconall",
    "task_id",
    "echo_idx",
    "anat_only",
)


def runtime_audit_status(runtime_audit: dict[str, Any]) -> str:
    if runtime_audit.get("blockers"):
        return "blocked"
    if runtime_audit.get("prepare_required"):
        return "needs_prepare"
    return "ready"


def dataset_audit_status(dataset_audit: dict[str, Any]) -> str:
    return "ready" if _dataset_subject_summary(dataset_audit)["runnable"] else "blocked"


def build_execution_readiness(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    dataset_audit: dict[str, Any],
    effective_execution_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build execution-time blockers and warnings from saved runtime and dataset facts."""
    runtime_blockers = list(runtime_audit.get("blockers", []))
    runtime_prepare_required = list(runtime_audit.get("prepare_required", []))
    warnings = list(runtime_audit.get("warnings", [])) + list(dataset_audit.get("warnings", []))
    subject_summary = _execution_subject_summary(dataset_audit, effective_execution_plan)
    if subject_summary["runnable"] == 0:
        return {
            "status": "blocked",
            "blockers": ["no_runnable_subjects"],
            "prepare_required": _dedupe(runtime_prepare_required),
            "warnings": _dedupe(warnings),
            "findings": issue_bucket_findings(
                blockers=["no_runnable_subjects"],
                prepare_required=_dedupe(runtime_prepare_required),
                warnings=_dedupe(warnings),
            ),
            "missing_required": [],
            "subject_summary": subject_summary,
        }

    blockers = list(runtime_blockers)
    execution_blockers = blockers + runtime_prepare_required

    missing_required = [REQUIRED_BLOCKER_FIELDS[value] for value in blockers if value in REQUIRED_BLOCKER_FIELDS]
    return {
        "status": "blocked" if execution_blockers else "ready",
        "blockers": _dedupe(blockers),
        "prepare_required": _dedupe(runtime_prepare_required),
        "warnings": _dedupe(warnings),
        "findings": issue_bucket_findings(
            blockers=_dedupe(blockers),
            prepare_required=_dedupe(runtime_prepare_required),
            warnings=_dedupe(warnings),
        ),
        "missing_required": _dedupe(missing_required),
        "subject_summary": subject_summary,
    }


def build_dataset_audit_artifacts(
    request: RequestConfig,
    dataset_audit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    subjects = list(dataset_audit.get("subjects") or [])
    existing_derivatives = _target_existing_derivatives(
        request.target,
        dict(dataset_audit.get("existing_derivatives") or {}),
    )
    storage_check = dataset_audit.get("storage_check")
    storage_warnings = list(storage_check.get("warnings") or []) if isinstance(storage_check, dict) else []
    warnings = _target_dataset_warnings(
        request.target,
        list(dataset_audit.get("warnings") or []) + storage_warnings,
    )
    summary = _build_dataset_summary(
        request.target,
        {
            **dataset_audit,
            "existing_derivatives": existing_derivatives,
            "warnings": warnings,
        },
        subjects,
    )
    advice = list(dataset_audit.get("advice") or [])
    warning_details = _target_warning_details(request.target, dataset_audit.get("warning_details"))
    storage_estimate_signature = build_storage_estimate_signature(request)
    runnable_subjects = _dataset_runnable_subjects(request.target, subjects)
    subject_exclusions = _dataset_subject_exclusions(request.target, subjects)
    summary_payload = {
        "dataset_type": dataset_audit.get("dataset_type"),
        "dataset_flags": list(dataset_audit.get("dataset_flags") or []),
        "storage_estimate_signature": storage_estimate_signature,
        "summary": summary,
        "warnings": warnings,
        "findings": issue_bucket_findings(warnings=warnings),
        "existing_derivatives": existing_derivatives,
        "subject_exclusions": subject_exclusions,
    }
    if advice:
        summary_payload["advice"] = advice
    if isinstance(storage_check, dict):
        summary_payload["storage_check"] = _summary_storage_check(storage_check)
    debug_payload = {
        "dataset_type": dataset_audit.get("dataset_type"),
        "dataset_flags": list(dataset_audit.get("dataset_flags") or []),
        "storage_estimate_signature": storage_estimate_signature,
        "summary": summary,
        "warnings": warnings,
        "existing_derivatives": existing_derivatives,
        "runnable_subjects": runnable_subjects,
        "subject_readiness": _dataset_subject_readiness(request.target, subjects),
        "subject_exclusions": _dataset_debug_subject_exclusions(request.target, subjects),
    }
    if advice:
        debug_payload["advice"] = advice
    if warning_details:
        debug_payload["warning_details"] = warning_details
    if isinstance(storage_check, dict):
        debug_payload["storage_check"] = _debug_storage_check(storage_check)
    return summary_payload, debug_payload


def build_request_signature(request: RequestConfig) -> dict[str, Any]:
    return {
        "target": request.target,
        "bids_root": str(request.bids_root) if request.bids_root is not None else None,
        "fmriprep_derivatives": str(request.resolve_fmriprep_derivatives_root()) if request.target == "xcpd" else None,
        "output_root": str(request.resolve_output_root()),
        "remote_host": request.remote_host,
        "subjects": sorted(request.subjects),
        "sessions": sorted(request.sessions),
        "fs_no_reconall": request.fs_no_reconall,
        "task_id": request.task_id,
        "echo_idx": request.echo_idx,
        "anat_only": request.anat_only,
    }


def _artifact_request_signature(request: RequestConfig, command: str) -> dict[str, Any]:
    signature = build_request_signature(request)
    if command in {"xcpd-dataset-audit", "xcpd-dataset-audit-debug"}:
        signature["xcpd_mode"] = request.xcpd_mode
        signature["xcpd_min_time"] = request.xcpd_min_time
        signature["xcpd_min_time_explicit"] = request.xcpd_min_time_explicit
        signature["xcpd_motion_filter_type"] = request.xcpd_motion_filter_type
        signature["xcpd_band_stop_min"] = request.xcpd_band_stop_min
        signature["xcpd_band_stop_max"] = request.xcpd_band_stop_max
        signature["xcpd_motion_filter_order"] = request.xcpd_motion_filter_order
        signature["xcpd_despike"] = request.xcpd_despike
        signature["xcpd_task_ids"] = list(request.xcpd_task_ids)
        signature["xcpd_bids_filter_file"] = (
            str(request.xcpd_bids_filter_file) if request.xcpd_bids_filter_file is not None else None
        )
        signature["xcpd_datasets"] = {
            alias: str(path) for alias, path in sorted(request.xcpd_datasets.items())
        }
        signature["xcpd_mem_mb"] = request.xcpd_mem_mb
        signature["xcpd_custom_args"] = dict(request.xcpd_custom_args)
    elif command == "runtime-audit":
        signature.update(
            {
                "work_root": str(request.resolve_work_root()),
                "log_root": str(request.resolve_log_root()),
                "download_root": str(request.resolve_download_root()),
                "fs_license": str(request.fs_license) if request.fs_license is not None else None,
                "templateflow_home": str(request.templateflow_home) if request.templateflow_home is not None else None,
                "templateflow_tool_bins": list(request.templateflow_tool_bins),
                "fmriprep_image": request.fmriprep_image,
                "container_runtime": request.container_runtime,
                "executor_policy": request.executor_policy,
                "scheduler_partition": request.scheduler_partition,
                "skip_bids_validation": request.skip_bids_validation,
                "nthreads_per_job": request.nthreads_per_job,
                "omp_nthreads": request.omp_nthreads,
                "slurm_mem_gb": request.slurm_mem_gb,
                "max_jobs": request.max_jobs,
                "fs_no_reconall": request.fs_no_reconall,
                "task_id": request.task_id,
                "echo_idx": request.echo_idx,
                "anat_only": request.anat_only,
                "output_spaces": list(request.output_spaces),
                "cifti_output": request.cifti_output,
                "fmriprep_custom_args": dict(request.fmriprep_custom_args),
                "wsl_vhdx_path": str(request.wsl_vhdx_path) if request.wsl_vhdx_path is not None else None,
                "windows_host_drive": request.windows_host_drive,
                "docker_wsl_storage_path": (
                    str(request.docker_wsl_storage_path) if request.docker_wsl_storage_path is not None else None
                ),
            }
        )
    elif command == "xcpd-runtime-audit":
        signature.update(
            {
                "work_root": str(request.resolve_work_root()),
                "log_root": str(request.resolve_log_root()),
                "download_root": str(request.resolve_download_root()),
                "fs_license": str(request.fs_license) if request.fs_license is not None else None,
                "templateflow_home": str(request.templateflow_home) if request.templateflow_home is not None else None,
                "templateflow_tool_bins": list(request.templateflow_tool_bins),
                "xcpd_image": request.xcpd_image,
                "xcpd_mode": request.xcpd_mode,
                "xcpd_min_time": request.xcpd_min_time,
                "xcpd_min_time_explicit": request.xcpd_min_time_explicit,
                "xcpd_motion_filter_type": request.xcpd_motion_filter_type,
                "xcpd_band_stop_min": request.xcpd_band_stop_min,
                "xcpd_band_stop_max": request.xcpd_band_stop_max,
                "xcpd_motion_filter_order": request.xcpd_motion_filter_order,
                "xcpd_despike": request.xcpd_despike,
                "xcpd_task_ids": list(request.xcpd_task_ids),
                "xcpd_bids_filter_file": (
                    str(request.xcpd_bids_filter_file) if request.xcpd_bids_filter_file is not None else None
                ),
                "xcpd_datasets": {
                    alias: str(path) for alias, path in sorted(request.xcpd_datasets.items())
                },
                "xcpd_mem_mb": request.xcpd_mem_mb,
                "xcpd_custom_args": dict(request.xcpd_custom_args),
                "container_runtime": request.container_runtime,
                "executor_policy": request.executor_policy,
                "scheduler_partition": request.scheduler_partition,
                "nthreads_per_job": request.nthreads_per_job,
                "omp_nthreads": request.omp_nthreads,
                "slurm_mem_gb": request.slurm_mem_gb,
                "max_jobs": request.max_jobs,
                "wsl_vhdx_path": str(request.wsl_vhdx_path) if request.wsl_vhdx_path is not None else None,
                "windows_host_drive": request.windows_host_drive,
                "docker_wsl_storage_path": (
                    str(request.docker_wsl_storage_path) if request.docker_wsl_storage_path is not None else None
                ),
            }
        )
    return signature


def build_storage_estimate_signature(request: RequestConfig) -> dict[str, Any]:
    return {
        "output_spaces": [str(value) for value in request.output_spaces],
        "cifti_output": request.cifti_output,
        "fs_no_reconall": request.fs_no_reconall,
        "task_id": request.task_id,
        "echo_idx": request.echo_idx,
        "anat_only": request.anat_only,
        "wsl_vhdx_path": str(request.wsl_vhdx_path) if request.wsl_vhdx_path is not None else None,
        "windows_host_drive": request.windows_host_drive,
    }


def _request_signature_fields_match(
    current_signature: dict[str, Any],
    saved_signature: Any,
    fields: tuple[str, ...],
) -> bool:
    if not isinstance(saved_signature, dict):
        return False
    for field in fields:
        if saved_signature.get(field) != current_signature.get(field):
            return False
    return True


def _storage_estimate_signature_matches(current_signature: dict[str, Any], saved_signature: Any) -> bool:
    if not isinstance(saved_signature, dict):
        return False
    return (
        saved_signature.get("output_spaces") == current_signature["output_spaces"]
        and saved_signature.get("cifti_output") == current_signature["cifti_output"]
        and saved_signature.get("fs_no_reconall") == current_signature["fs_no_reconall"]
        and saved_signature.get("task_id") == current_signature["task_id"]
        and saved_signature.get("echo_idx") == current_signature["echo_idx"]
        and saved_signature.get("anat_only") == current_signature["anat_only"]
        and saved_signature.get("wsl_vhdx_path") == current_signature["wsl_vhdx_path"]
        and saved_signature.get("windows_host_drive") == current_signature["windows_host_drive"]
    )


def write_stage_artifact(
    request: RequestConfig,
    *,
    command: str,
    status: str,
    stage_payload: dict[str, Any],
    audit_id: str | None = None,
) -> dict[str, Any]:
    resolved_audit_id = audit_id or new_audit_id()
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "command": command,
        "status": status,
        "request_signature": _artifact_request_signature(request, command),
        "created_at": datetime.now().astimezone().isoformat(),
        "audit_id": resolved_audit_id,
        ARTIFACT_PAYLOAD_KEYS[command]: stage_payload,
    }
    _write_stage_artifact_json(request, command, payload, audit_id=resolved_audit_id)
    return payload


def write_dataset_audit_debug_artifact(
    request: RequestConfig,
    *,
    status: str,
    stage_payload: dict[str, Any],
    audit_id: str | None = None,
) -> dict[str, Any]:
    resolved_audit_id = audit_id or new_audit_id()
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "command": "dataset-audit-debug",
        "status": status,
        "request_signature": build_request_signature(request),
        "created_at": datetime.now().astimezone().isoformat(),
        "audit_id": resolved_audit_id,
        "dataset_audit": stage_payload,
    }
    _write_stage_artifact_json(request, "dataset-audit-debug", payload, audit_id=resolved_audit_id)
    return payload


def write_execution_context_artifact(
    request: RequestConfig,
    *,
    runtime_artifact: dict[str, Any],
    dataset_artifact: dict[str, Any],
    execution_plan: dict[str, Any],
    status: str,
    audit_id: str,
    submission_id: str,
) -> dict[str, Any]:
    payload = _build_execution_context_payload(
        request,
        runtime_artifact=runtime_artifact,
        dataset_artifact=dataset_artifact,
        execution_plan=execution_plan,
        status=status,
        audit_id=audit_id,
        submission_id=submission_id,
    )
    _write_stage_artifact_json(
        request,
        "execution-context",
        payload,
        audit_id=audit_id,
        submission_id=submission_id,
    )
    return payload


def write_submission_result_artifact(
    request: RequestConfig,
    *,
    execution_context_path: RequestPath,
    execution: dict[str, Any],
    audit_id: str,
    submission_id: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "command": "submission-result",
        "status": str(execution.get("status") or "unknown"),
        "request_signature": build_request_signature(request),
        "created_at": datetime.now().astimezone().isoformat(),
        "audit_id": audit_id,
        "submission_id": submission_id,
        "execution_context_ref": str(execution_context_path),
        "execution": _compact_submission_execution(execution),
    }
    _write_stage_artifact_json(
        request,
        "submission-result",
        payload,
        audit_id=audit_id,
        submission_id=submission_id,
    )
    return payload


def _compact_submission_execution(execution: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "status": execution.get("status"),
        "mode": execution.get("mode"),
        "pre_steps": [_compact_execution_record(step, include_subject_fields=False) for step in execution.get("pre_steps") or []],
        "subjects": [_compact_execution_record(subject, include_subject_fields=True) for subject in execution.get("subjects") or []],
        "skipped_subjects": list(execution.get("skipped_subjects") or []),
    }
    for field in ("launcher_stdout", "launcher_stderr", "pid_manifest"):
        if execution.get(field):
            compact[field] = execution[field]
    return compact


def _compact_execution_record(record: dict[str, Any], *, include_subject_fields: bool) -> dict[str, Any]:
    fields = [
        "step",
        "status",
        "returncode",
        "pid",
        "job_id",
        "array_task_id",
        "launcher_stdout",
        "launcher_stderr",
        "stdout_path",
        "stderr_path",
        "timed_out",
        "timeout_seconds",
        "started_at",
        "error",
    ]
    if include_subject_fields:
        fields = ["subject_id", "session_id", "subject_key", *fields]
    compact = {field: record[field] for field in fields if field in record}
    steps = record.get("steps")
    if include_subject_fields and isinstance(steps, list):
        compact["steps"] = [_compact_step_ref(step) for step in steps if isinstance(step, dict)]
    return compact


def _compact_step_ref(step: dict[str, Any]) -> dict[str, Any]:
    fields = ("step", "stdout_path", "stderr_path", "work_dir", "output_dir")
    return {field: step[field] for field in fields if field in step}


def _build_execution_context_payload(
    request: RequestConfig,
    *,
    runtime_artifact: dict[str, Any],
    dataset_artifact: dict[str, Any],
    execution_plan: dict[str, Any],
    status: str,
    audit_id: str,
    submission_id: str,
) -> dict[str, Any]:
    subject_index: list[dict[str, Any]] = []
    step_templates: dict[str, dict[str, Any]] = {}
    for subject in execution_plan.get("subjects") or []:
        steps = list(subject.get("steps") or [])
        if not steps:
            continue
        subject_index.append(
            {
                "subject_id": str(subject.get("subject_id")),
                "subject_key": str(subject.get("subject_key")),
                "session_ids": [str(value) for value in subject.get("session_ids") or []],
                "steps": [str(step.get("step")) for step in steps],
            }
        )
        for step in steps:
            step_name = str(step.get("step"))
            if step_name not in step_templates:
                step_templates[step_name] = _build_step_template(step, subject)
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "command": "execution-context",
        "status": status,
        "request_signature": build_request_signature(request),
        "created_at": datetime.now().astimezone().isoformat(),
        "audit_id": audit_id,
        "submission_id": submission_id,
        "runtime_audit_ref": str(
            _artifact_reference_path(request, str(runtime_artifact.get("command") or "runtime-audit"), runtime_artifact)
        ),
        "dataset_audit_ref": str(
            _artifact_reference_path(request, str(dataset_artifact.get("command") or "dataset-audit"), dataset_artifact)
        ),
        "execution_plan_summary": _execution_plan_summary(execution_plan),
        "step_templates": step_templates,
        "subject_index": subject_index,
    }


def load_stage_artifact(
    request: RequestConfig,
    command: str,
    *,
    audit_id: str | None = None,
    submission_id: str | None = None,
) -> dict[str, Any] | None:
    if audit_id is not None:
        path = archived_artifact_path(request, command, audit_id=audit_id, submission_id=submission_id)
    else:
        path = _latest_artifact_archive_path(request, command)
        if path is None:
            return None
    if not path_exists(path, remote_host=request.remote_host):
        return None
    return json.loads(read_text(path, remote_host=request.remote_host))


def load_stage_artifacts(
    request: RequestConfig,
    commands: list[str],
    *,
    audit_id: str | None = None,
    submission_id_by_command: dict[str, str] | None = None,
) -> dict[str, dict[str, Any] | None]:
    paths_by_command = _artifact_paths_for_batch(
        request,
        commands,
        audit_id=audit_id,
        submission_id_by_command=submission_id_by_command or {},
    )
    readable_paths = [path for path in paths_by_command.values() if path is not None]
    text_by_path = read_texts(readable_paths, remote_host=request.remote_host) if readable_paths else {}
    loaded: dict[str, dict[str, Any] | None] = {}
    for command in commands:
        path = paths_by_command.get(command)
        if path is None:
            loaded[command] = None
            continue
        result = text_by_path.get(str(path))
        if not isinstance(result, dict) or result.get("exists") is not True:
            loaded[command] = None
            continue
        text = result.get("text")
        if not isinstance(text, str):
            loaded[command] = None
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        loaded[command] = payload if isinstance(payload, dict) else None
    return loaded


def validate_stage_artifact(
    request: RequestConfig,
    command: str,
    artifact: dict[str, Any],
    *,
    require_storage_estimate_signature: bool = True,
) -> str | None:
    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        return _artifact_code(command, "invalid")
    if artifact.get("command") != command:
        return _artifact_code(command, "invalid")
    if not isinstance(artifact.get("audit_id"), str) or not artifact.get("audit_id"):
        return _artifact_code(command, "invalid")
    if command in {"execution-context", "submission-result"} and (
        not isinstance(artifact.get("submission_id"), str) or not artifact.get("submission_id")
    ):
        return _artifact_code(command, "invalid")
    payload_key = ARTIFACT_PAYLOAD_KEYS.get(command)
    if payload_key is not None and payload_key not in artifact:
        return _artifact_code(command, "invalid")
    current_signature = _artifact_request_signature(request, command)
    if command in {"xcpd-dataset-audit", "xcpd-dataset-audit-debug"}:
        signature_fields = XCPD_DATASET_REQUEST_SIGNATURE_FIELDS
    elif command == "runtime-audit":
        signature_fields = FMRIPREP_RUNTIME_REQUEST_SIGNATURE_FIELDS
    elif command == "xcpd-runtime-audit":
        signature_fields = XCPD_RUNTIME_REQUEST_SIGNATURE_FIELDS
    else:
        signature_fields = _SHARED_REQUEST_SIGNATURE_FIELDS
    if not _request_signature_fields_match(current_signature, artifact.get("request_signature"), signature_fields):
        return _artifact_code(command, "mismatch")
    if require_storage_estimate_signature and command in {"dataset-audit", "dataset-audit-debug"}:
        payload = artifact.get(payload_key or "")
        if not isinstance(payload, dict):
            return _artifact_code(command, "invalid")
        if not _storage_estimate_signature_matches(
            build_storage_estimate_signature(request),
            payload.get("storage_estimate_signature"),
        ):
            return _artifact_code(command, "mismatch")
    if command == "runtime-audit":
        payload = artifact.get(payload_key or "")
        if not isinstance(payload, dict):
            return _artifact_code(command, "invalid")
        if _runtime_templateflow_signature_mismatch(request, payload):
            return _artifact_code(command, "mismatch")
    return None


def _runtime_templateflow_signature_mismatch(request: RequestConfig, runtime_audit: dict[str, Any]) -> bool:
    from .templateflow_audit import REQUIRED_TEMPLATEFLOW_TEMPLATES, required_templateflow_templates

    if request.target != "fmriprep":
        return False
    required = required_templateflow_templates(request)
    saved = runtime_audit.get("required_templateflow_templates")
    if isinstance(saved, list):
        return [str(value) for value in saved] != required
    return required != list(REQUIRED_TEMPLATEFLOW_TEMPLATES)


def latest_audit_path(request: RequestConfig) -> RequestPath:
    return _audit_artifact_root(request) / LATEST_AUDIT_FILENAME


def runtime_proofs_path(request: RequestConfig) -> RequestPath:
    return _audit_artifact_root(request) / RUNTIME_PROOFS_FILENAME


def runtime_proof_id(kind: str, signature: dict[str, Any], *, status: str, data: dict[str, Any]) -> str:
    body = json.dumps(
        {
            "kind": kind,
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "signature": signature,
            "status": status,
            "data": data,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_runtime_proofs(request: RequestConfig) -> dict[str, dict[str, Any]]:
    path = runtime_proofs_path(request)
    if not path_exists(path, remote_host=request.remote_host):
        return {}
    try:
        payload = json.loads(read_text(path, remote_host=request.remote_host))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        return {}
    raw_proofs = payload.get("proofs")
    if not isinstance(raw_proofs, dict):
        return {}
    proofs: dict[str, dict[str, Any]] = {}
    for kind, bucket in raw_proofs.items():
        if not isinstance(kind, str) or not isinstance(bucket, dict):
            continue
        for proof_id, proof in bucket.items():
            if not isinstance(proof_id, str) or not isinstance(proof, dict):
                continue
            signature = proof.get("signature")
            if not isinstance(signature, dict):
                continue
            status = proof.get("status")
            data = proof.get("data")
            if not isinstance(status, str) or not isinstance(data, dict):
                continue
            if runtime_proof_id(kind, signature, status=status, data=data) != proof_id:
                continue
            proofs[proof_id] = {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "kind": kind,
                **proof,
            }
    return proofs


def write_runtime_proofs(request: RequestConfig, proofs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    validated: dict[str, dict[str, Any]] = {}
    for proof_id, proof in proofs.items():
        status = proof.get("status")
        data = proof.get("data")
        if not isinstance(status, str) or not isinstance(data, dict):
            raise ValueError(f"invalid runtime proof payload for {proof_id}")
        if runtime_proof_id(
            str(proof.get("kind") or ""),
            dict(proof.get("signature") or {}),
            status=status,
            data=data,
        ) != proof_id:
            raise ValueError(f"invalid runtime proof id for {proof_id}")
        validated[proof_id] = proof
    buckets: dict[str, dict[str, dict[str, Any]]] = {}
    for proof_id, proof in validated.items():
        kind = str(proof.get("kind") or "")
        buckets.setdefault(kind, {})[proof_id] = {
            key: value
            for key, value in proof.items()
            if key not in {"schema_version", "kind"}
        }
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "updated_at": datetime.now().astimezone().isoformat(),
        "proofs": buckets,
    }
    write_text(
        runtime_proofs_path(request),
        json.dumps(payload, indent=2, ensure_ascii=False),
        remote_host=request.remote_host,
    )
    return validated


def archived_artifact_path(
    request: RequestConfig,
    command: str,
    *,
    audit_id: str,
    submission_id: str | None = None,
) -> RequestPath:
    audit_dir = _audit_artifact_root(request) / f"audit_{audit_id}"
    if command in {"execution-context", "submission-result"}:
        if submission_id is None:
            raise ValueError(f"submission_id is required for {command} archive paths")
        return audit_dir / f"submission_{submission_id}" / ARTIFACT_FILENAMES[command]
    return audit_dir / ARTIFACT_FILENAMES[command]


def _audit_artifact_root(request: RequestConfig) -> RequestPath:
    return request.resolve_output_root() / "_artifacts" / AUDIT_BUCKETS.get(request.target, "fmriprep_audit")


def new_audit_id() -> str:
    return _new_artifact_id()


def new_submission_id() -> str:
    return _new_artifact_id()


def _new_artifact_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _write_stage_artifact_json(
    request: RequestConfig,
    command: str,
    payload: dict[str, Any],
    *,
    audit_id: str,
    submission_id: str | None = None,
) -> None:
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    archive_path = archived_artifact_path(
        request,
        command,
        audit_id=audit_id,
        submission_id=submission_id,
    )
    write_text(archive_path, serialized, remote_host=request.remote_host)
    _write_latest_audit_index(
        request,
        command=command,
        archive_path=archive_path,
        audit_id=audit_id,
        submission_id=submission_id,
    )


def _write_latest_audit_index(
    request: RequestConfig,
    *,
    command: str,
    archive_path: RequestPath,
    audit_id: str,
    submission_id: str | None,
) -> None:
    latest_path = latest_audit_path(request)
    payload = _load_latest_audit_index(request)
    latest_artifacts = payload.setdefault("latest_artifacts", {})
    entry: dict[str, Any] = {
        "path": str(archive_path),
        "audit_id": audit_id,
    }
    if submission_id is not None:
        entry["submission_id"] = submission_id
    latest_artifacts[command] = entry
    payload["schema_version"] = ARTIFACT_SCHEMA_VERSION
    payload["updated_at"] = datetime.now().astimezone().isoformat()
    write_text(
        latest_path,
        json.dumps(payload, indent=2, ensure_ascii=False),
        remote_host=request.remote_host,
    )


def _load_latest_audit_index(request: RequestConfig) -> dict[str, Any]:
    latest_path = latest_audit_path(request)
    if not path_exists(latest_path, remote_host=request.remote_host):
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "updated_at": datetime.now().astimezone().isoformat(),
            "latest_artifacts": {},
        }
    try:
        payload = json.loads(read_text(latest_path, remote_host=request.remote_host))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "updated_at": datetime.now().astimezone().isoformat(),
            "latest_artifacts": {},
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "updated_at": datetime.now().astimezone().isoformat(),
            "latest_artifacts": {},
        }
    if not isinstance(payload.get("latest_artifacts"), dict):
        payload["latest_artifacts"] = {}
    return payload


def _latest_artifact_archive_path(request: RequestConfig, command: str) -> RequestPath | None:
    entry = (_load_latest_audit_index(request).get("latest_artifacts") or {}).get(command)
    return _latest_artifact_archive_path_from_entry(request, command, entry)


def _artifact_paths_for_batch(
    request: RequestConfig,
    commands: list[str],
    *,
    audit_id: str | None,
    submission_id_by_command: dict[str, str],
) -> dict[str, RequestPath | None]:
    if audit_id is not None:
        return {
            command: archived_artifact_path(
                request,
                command,
                audit_id=audit_id,
                submission_id=submission_id_by_command.get(command),
            )
            for command in commands
        }
    latest_artifacts = _load_latest_audit_index(request).get("latest_artifacts") or {}
    return {
        command: _latest_artifact_archive_path_from_entry(request, command, latest_artifacts.get(command))
        for command in commands
    }


def _latest_artifact_archive_path_from_entry(
    request: RequestConfig,
    command: str,
    entry: Any,
) -> RequestPath | None:
    if not isinstance(entry, dict):
        return None
    audit_id = entry.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        return None
    submission_id = entry.get("submission_id")
    if command in {"execution-context", "submission-result"}:
        if not isinstance(submission_id, str) or not submission_id:
            return None
    else:
        submission_id = None
    expected_path = archived_artifact_path(
        request,
        command,
        audit_id=audit_id,
        submission_id=submission_id if isinstance(submission_id, str) else None,
    )
    path_value = entry.get("path")
    if not isinstance(path_value, str) or path_value != str(expected_path):
        return None
    return expected_path


def _artifact_reference_path(
    request: RequestConfig,
    command: str,
    artifact: dict[str, Any],
) -> RequestPath:
    audit_id = artifact.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError(f"{command} artifact is missing audit_id")
    submission_id = artifact.get("submission_id") if command in {"execution-context", "submission-result"} else None
    if command in {"execution-context", "submission-result"}:
        if not isinstance(submission_id, str) or not submission_id:
            raise ValueError(f"{command} artifact is missing submission_id")
    return archived_artifact_path(
        request,
        command,
        audit_id=audit_id,
        submission_id=submission_id if isinstance(submission_id, str) else None,
    )


def _execution_plan_summary(execution_plan: dict[str, Any]) -> dict[str, Any]:
    runnable_subjects = [subject for subject in execution_plan.get("subjects") or [] if subject.get("steps")]
    skipped_subjects = [subject for subject in execution_plan.get("subjects") or [] if not subject.get("steps")]
    return {
        "target": execution_plan.get("target"),
        "backend": execution_plan.get("backend"),
        "runtime": execution_plan.get("runtime"),
        "execution_unit": execution_plan.get("execution_unit"),
        "execution_strategy": execution_plan.get("execution_strategy"),
        "resources": execution_plan.get("resources"),
        "max_concurrency": execution_plan.get("max_concurrency"),
        "pre_step_count": len(execution_plan.get("pre_steps") or []),
        "runnable_subject_count": len(runnable_subjects),
        "skipped_subject_count": len(skipped_subjects),
    }


def _build_step_template(step: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
    subject_id = str(subject.get("subject_id"))
    subject_key = str(subject.get("subject_key"))
    work_dir = str(step.get("work_dir"))
    output_dir = str(step.get("output_dir"))
    template = {
        "command_template": _command_template(
            [str(value) for value in step.get("command") or []],
            subject_id=subject_id,
            work_dir=work_dir,
            output_dir=output_dir,
        ),
        "work_dir_template": _subject_path_template(work_dir, subject_key),
        "output_dir_template": output_dir,
        "stdout_template": _subject_path_template(str(step.get("stdout_path")), subject_key),
        "stderr_template": _subject_path_template(str(step.get("stderr_path")), subject_key),
    }
    bids_filter = step.get("bids_filter")
    if isinstance(bids_filter, dict) and bids_filter.get("path"):
        template["bids_filter_path_template"] = _subject_path_template(str(bids_filter["path"]), subject_key)
    return template


def _command_template(
    command: list[str],
    *,
    subject_id: str,
    work_dir: str,
    output_dir: str,
) -> list[str]:
    template: list[str] = []
    for raw_token in command:
        token = raw_token.replace(work_dir, "{work_dir}").replace(output_dir, "{output_dir}")
        token = token.replace(subject_id, "{subject_id}")
        template.append(token)
    return template


def _subject_path_template(path_value: str, subject_key: str) -> str:
    return path_value.replace(subject_key, "{subject_key}")


def _artifact_code(command: str, kind: str) -> str:
    prefix = command.replace("-", "_")
    code = {
        "missing": f"missing_{prefix}_artifact",
        "invalid": f"invalid_{prefix}_artifact",
        "mismatch": f"{prefix}_request_mismatch",
        "not_ready": f"{prefix}_not_ready",
    }[kind]
    if code not in ISSUE_BY_CODE:
        raise ValueError(f"Artifact issue code is missing from catalog: {code}")
    return code


def _describe_issue(value: str) -> str:
    if value in ISSUE_DESCRIPTIONS:
        return ISSUE_DESCRIPTIONS[value]
    if value.startswith("prepare_runtime_required_") and value.endswith("_image"):
        pipeline = value.removeprefix("prepare_runtime_required_").removesuffix("_image").upper()
        return f"{pipeline} still points to a remote image; run prepare-runtime first or provide a local image path."
    return value


def _dataset_subject_summary(dataset_audit: dict[str, Any]) -> dict[str, Any]:
    summary = dataset_audit.get("summary") or {}
    discovered = int(summary.get("subjects_discovered", 0))
    runnable = int(summary.get("subjects_runnable", 0))
    blocked = int(summary.get("subjects_blocked", max(0, discovered - runnable)))
    return {
        "discovered": discovered,
        "runnable": runnable,
        "blocked": blocked,
    }


def _execution_subject_summary(dataset_audit: dict[str, Any], execution_plan: dict[str, Any]) -> dict[str, Any]:
    discovered = int((dataset_audit.get("summary") or {}).get("subjects_discovered", 0))
    runnable = len(execution_plan.get("subjects", []))
    blocked = max(0, discovered - runnable)
    return {
        "discovered": discovered,
        "runnable": runnable,
        "blocked": blocked,
    }


def _build_dataset_summary(target: str, dataset_audit: dict[str, Any], subjects: list[dict[str, Any]]) -> dict[str, Any]:
    missing_t1w_subjects = 0
    missing_bold_subjects = 0
    materialization_required_subjects = 0
    subjects_runnable = 0
    materialization_codes = {
        "dataset_not_materialized",
        "annex_content_missing",
        "datalad_get_required",
        "git_annex_get_required",
    }
    for subject in subjects:
        if _subject_runnable_for_target(target, subject):
            subjects_runnable += 1
        reason_codes = _subject_reason_codes_for_target(target, subject)
        if "missing_t1w" in reason_codes:
            missing_t1w_subjects += 1
        if "missing_bold" in reason_codes:
            missing_bold_subjects += 1
        if materialization_codes & set(reason_codes):
            materialization_required_subjects += 1
    detected = (dataset_audit.get("existing_derivatives") or {}).get("detected") or []
    warnings = [str(value) for value in dataset_audit.get("warnings") or []]
    return {
        "dataset_type": dataset_audit.get("dataset_type"),
        "subjects_discovered": len(subjects),
        "subjects_runnable": subjects_runnable,
        "subjects_blocked": max(0, len(subjects) - subjects_runnable),
        "missing_t1w_subjects": missing_t1w_subjects,
        "missing_bold_subjects": missing_bold_subjects,
        "materialization_required_subjects": materialization_required_subjects,
        "existing_derivatives": [str(value) for value in detected],
        "warnings": warnings,
    }


def _dataset_runnable_subjects(target: str, subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runnable_subjects: list[dict[str, Any]] = []
    for subject in subjects:
        steps = _steps_for_target(target, subject)
        if not steps:
            continue
        runnable_subjects.append(
            {
                "subject_id": str(subject.get("subject_id")),
                "session_ids": list(subject.get("session_ids") or []),
                "steps": steps,
            }
        )
    return runnable_subjects


def _dataset_subject_exclusions(target: str, subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _compact_excluded_subject(target, subject)
        for subject in subjects
        if not _subject_runnable_for_target(target, subject)
    ]


def _dataset_debug_subject_exclusions(target: str, subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _compact_problem_subject(target, subject)
        for subject in subjects
        if not _subject_runnable_for_target(target, subject)
    ]


def _dataset_subject_readiness(target: str, subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_compact_subject_readiness(target, subject) for subject in subjects]


def _target_existing_derivatives(target: str, existing_derivatives: dict[str, Any]) -> dict[str, Any]:
    if target != "fmriprep":
        return existing_derivatives
    pipelines = existing_derivatives.get("pipelines") if isinstance(existing_derivatives.get("pipelines"), dict) else {}
    payload = {
        "detected": [value for value in list(existing_derivatives.get("detected") or []) if value == "fmriprep"],
    }
    if "fmriprep" in pipelines:
        payload["pipelines"] = {"fmriprep": dict(pipelines.get("fmriprep") or {})}
    return payload


def _target_dataset_warnings(target: str, warnings: list[str]) -> list[str]:
    if target != "fmriprep":
        return warnings
    return [warning for warning in warnings if str(warning) not in XCPD_ISSUE_CODES]


def _target_warning_details(target: str, warning_details: Any) -> Any:
    if target != "fmriprep":
        return warning_details
    if not isinstance(warning_details, list):
        return warning_details
    filtered: list[Any] = []
    for detail in warning_details:
        if isinstance(detail, dict):
            code = str(detail.get("code") or "")
            if code in XCPD_ISSUE_CODES:
                continue
        elif "xcpd" in str(detail).lower() or "xcp-d" in str(detail).lower():
            continue
        filtered.append(detail)
    return filtered


def _summary_storage_check(storage_check: dict[str, Any]) -> dict[str, Any]:
    compact = compact_storage_check_report(storage_check)
    return {
        "storage_estimation_status": compact.get("storage_estimation_status"),
        "storage_estimation_reason": compact.get("storage_estimation_reason"),
        "estimated_final_derivatives_gb": compact.get("estimated_final_derivatives_gb"),
        "estimated_work_peak_min_gb": compact.get("estimated_work_peak_min_gb"),
        "estimated_work_peak_gb": compact.get("estimated_work_peak_gb"),
        "estimated_total_peak_increment_gb": compact.get("estimated_total_peak_increment_gb"),
        "estimated_image_pull_gb": compact.get("estimated_image_pull_gb"),
        "comparison_mode": compact.get("comparison_mode"),
        "comparison_text": compact.get("comparison_text"),
    }


def _debug_storage_check(storage_check: dict[str, Any]) -> dict[str, Any]:
    return compact_storage_check_report(storage_check)


def _compact_excluded_subject(target: str, subject: dict[str, Any]) -> dict[str, Any]:
    reason_codes = _subject_reason_codes_for_target(target, subject)
    return {
        "subject_id": str(subject.get("subject_id")),
        "session_ids": list(subject.get("session_ids") or []),
        "reason_codes": reason_codes,
        "findings": issue_findings(reason_codes, category="subject-exclusion"),
    }


def _compact_problem_subject(target: str, subject: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "subject_id": str(subject.get("subject_id")),
        "session_ids": list(subject.get("session_ids") or []),
        target: _compact_pipeline_status(subject.get(target) or {}),
    }
    sessions = [
        _compact_problem_session(target, session)
        for session in subject.get("sessions") or []
        if not _subject_runnable_for_target(target, session)
    ]
    if sessions:
        payload["sessions"] = sessions
    return payload


def _compact_subject_readiness(target: str, subject: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "subject_id": str(subject.get("subject_id")),
        "session_ids": list(subject.get("session_ids") or []),
        target: _compact_pipeline_status(subject.get(target) or {}),
    }
    sessions = [_compact_session_readiness(target, session) for session in subject.get("sessions") or []]
    if sessions:
        payload["sessions"] = sessions
    return payload


def _compact_problem_session(target: str, session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(session.get("session_id")),
        target: _compact_pipeline_status(session.get(target) or {}),
    }


def _compact_session_readiness(target: str, session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(session.get("session_id")),
        target: _compact_pipeline_status(session.get(target) or {}),
    }


def _compact_pipeline_status(pipeline: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "status": str(pipeline.get("status") or "blocked"),
        "reason_codes": list(pipeline.get("reason_codes") or []),
    }
    for field in ("inputs_materialized", "has_derivatives", "has_fmriprep_derivatives", "has_xcpd_derivatives"):
        if field in pipeline:
            payload[field] = pipeline[field]
    return payload


def _steps_for_target(target: str, subject: dict[str, Any]) -> list[str]:
    fmriprep_ready = _subject_pipeline_status(subject, "fmriprep") == "ready"
    xcpd_ready = _subject_pipeline_status(subject, "xcpd") == "ready"
    if target == "fmriprep":
        return ["fmriprep"] if fmriprep_ready else []
    return ["xcpd"] if xcpd_ready else []


def _dedupe(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def _subject_pipeline_status(subject: dict[str, Any], pipeline: str) -> str:
    return str((subject.get(pipeline) or {}).get("status") or "blocked")


def _subject_runnable_for_target(target: str, subject: dict[str, Any]) -> bool:
    if target == "fmriprep":
        return _subject_pipeline_status(subject, "fmriprep") == "ready"
    return _subject_pipeline_status(subject, "xcpd") == "ready"


def _subject_reason_codes_for_target(target: str, subject: dict[str, Any]) -> list[str]:
    if target == "fmriprep":
        return list((subject.get("fmriprep") or {}).get("reason_codes") or [])
    return list((subject.get("xcpd") or {}).get("reason_codes") or [])


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ARTIFACT_FILENAMES",
    "archived_artifact_path",
    "build_dataset_audit_artifacts",
    "build_execution_readiness",
    "build_request_signature",
    "build_storage_estimate_signature",
    "dataset_audit_status",
    "latest_audit_path",
    "load_stage_artifact",
    "load_stage_artifacts",
    "new_audit_id",
    "new_submission_id",
    "runtime_audit_status",
    "runtime_proof_id",
    "runtime_proofs_path",
    "validate_stage_artifact",
    "load_runtime_proofs",
    "write_runtime_proofs",
    "write_dataset_audit_debug_artifact",
    "write_execution_context_artifact",
    "write_submission_result_artifact",
    "write_stage_artifact",
]
