"""Saved artifact readiness and execution flow."""

from __future__ import annotations

import shlex
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from fmri_core.audit import (
    FMRIPREP_RUNTIME_REQUEST_SIGNATURE_FIELDS,
    XCPD_DATASET_REQUEST_SIGNATURE_FIELDS,
    XCPD_RUNTIME_REQUEST_SIGNATURE_FIELDS,
    archived_artifact_path,
    build_execution_readiness,
    build_request_signature,
    build_storage_estimate_signature,
    latest_audit_path,
    load_stage_artifact,
    load_stage_artifacts,
    new_submission_id,
    validate_stage_artifact,
    write_execution_context_artifact,
    write_submission_result_artifact,
)
from fmri_core.issue_codes import ISSUE_BY_CODE, issue_bucket_findings
from fmri_core.models import DEFAULT_FMRIPREP_OUTPUT_SPACES, ProgressCallback, RequestConfig
from fmri_core.pipelines import build_execution_plan
from fmri_core.run import execute_plan
from fmri_core.runtime_proofs import expand_runtime_audit_with_proofs
from fmri_core.templateflow_audit import required_templateflow_templates
from fmri_process.request_config import PROCESS_TARGET, path_value

XCPD_DATASET_AUDIT_COMMAND = "xcpd-dataset-audit"
XCPD_DATASET_AUDIT_DEBUG_COMMAND = "xcpd-dataset-audit-debug"
XCPD_RUNTIME_AUDIT_COMMAND = "xcpd-runtime-audit"

def argument_error_payload(command: str, message: str) -> dict[str, Any]:
    return {
        "exit_code": 1,
        "payload": {
            "status": "error",
            "command": command,
            "error_type": "argument_parse",
            "error_code": "invalid_arguments",
            "message": message,
        },
    }

def artifact_execution_locator(
    request: RequestConfig,
    *,
    resume_from: str | None,
    option_name: str = "--resume-from",
) -> tuple[RequestConfig, str | None]:
    raw_resume_from = (resume_from or "").strip()
    if not raw_resume_from:
        return request, None
    resume_output_root, audit_id = _resume_locator(raw_resume_from, request.remote_host, option_name=option_name)
    effective_request = replace(request, output_root=resume_output_root) if resume_output_root is not None else request
    if resume_output_root is None:
        try:
            effective_request.resolve_output_root()
        except ValueError as exc:
            raise ValueError(f"{option_name} audit id requires --output-root or --bids-root") from exc
    return effective_request, audit_id

def ready_fmriprep_execution_artifacts(
    request: RequestConfig,
    *,
    command: str,
    resume_from: str | None,
) -> tuple[RequestConfig, dict[str, dict[str, Any] | None] | None, dict[str, Any] | None]:
    try:
        locator_request, audit_id = artifact_execution_locator(request, resume_from=resume_from)
    except ValueError as exc:
        return (
            request,
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "error",
                    "command": command,
                    "error_type": "argument_parse",
                    "error_code": "invalid_arguments",
                    "message": str(exc),
                },
            },
        )
    loaded_artifacts = load_stage_artifacts(
        locator_request,
        ["runtime-audit", "dataset-audit", "dataset-audit-debug"],
        audit_id=audit_id,
    )
    effective_request = fmriprep_request_from_artifacts(
        locator_request,
        runtime_artifact=loaded_artifacts["runtime-audit"],
        dataset_artifact=loaded_artifacts["dataset-audit"],
    )
    artifacts, result = ready_execution_artifacts(
        effective_request,
        command=command,
        runtime_artifact=loaded_artifacts["runtime-audit"],
        dataset_artifact=loaded_artifacts["dataset-audit"],
        dataset_debug_artifact=loaded_artifacts["dataset-audit-debug"],
        audit_id=audit_id,
    )
    return effective_request, artifacts, result

def fmriprep_request_from_artifacts(
    request: RequestConfig,
    *,
    runtime_artifact: dict[str, Any] | None,
    dataset_artifact: dict[str, Any] | None,
) -> RequestConfig:
    runtime_signature = runtime_artifact.get("request_signature") if isinstance(runtime_artifact, dict) else None
    dataset_signature = dataset_artifact.get("request_signature") if isinstance(dataset_artifact, dict) else None
    dataset_payload = dataset_artifact.get("dataset_audit") if isinstance(dataset_artifact, dict) else None
    storage_signature = (
        dataset_payload.get("storage_estimate_signature") if isinstance(dataset_payload, dict) else None
    )
    if not isinstance(runtime_signature, dict):
        return request
    selector_signature = dataset_signature if isinstance(dataset_signature, dict) else {}
    remote_host = optional_text(runtime_signature.get("remote_host")) or request.remote_host
    preserve_remote = bool(remote_host)
    output_signature = storage_signature if isinstance(storage_signature, dict) else runtime_signature
    output_spaces = (
        [str(value) for value in output_signature.get("output_spaces")]
        if isinstance(output_signature, dict) and isinstance(output_signature.get("output_spaces"), list)
        else list(DEFAULT_FMRIPREP_OUTPUT_SPACES)
    )
    cifti_output = (
        str(output_signature.get("cifti_output"))
        if isinstance(output_signature, dict) and output_signature.get("cifti_output") is not None
        else None
    )
    fs_no_reconall = bool(runtime_signature.get("fs_no_reconall"))
    if isinstance(storage_signature, dict):
        fs_no_reconall = fs_no_reconall or bool(storage_signature.get("fs_no_reconall"))
    return RequestConfig(
        action="run-fmriprep",
        bids_root=_signature_path(runtime_signature, "bids_root", preserve_remote=preserve_remote),
        output_root=_signature_path(runtime_signature, "output_root", preserve_remote=preserve_remote),
        remote_host=remote_host,
        subjects=_signature_strings(selector_signature.get("subjects")),
        sessions=_signature_strings(selector_signature.get("sessions")),
        work_root=_signature_path(runtime_signature, "work_root", preserve_remote=preserve_remote),
        log_root=_signature_path(runtime_signature, "log_root", preserve_remote=preserve_remote),
        download_root=_signature_path(runtime_signature, "download_root", preserve_remote=preserve_remote),
        fs_license=_signature_path(runtime_signature, "fs_license", preserve_remote=preserve_remote),
        templateflow_home=_signature_path(runtime_signature, "templateflow_home", preserve_remote=preserve_remote),
        templateflow_tool_bins=_signature_strings(runtime_signature.get("templateflow_tool_bins")),
        fmriprep_image=optional_text(runtime_signature.get("fmriprep_image")),
        container_runtime=optional_text(runtime_signature.get("container_runtime")) or "auto",
        executor_policy=optional_text(runtime_signature.get("executor_policy")) or "auto",
        scheduler_partition=optional_text(runtime_signature.get("scheduler_partition")),
        nthreads_per_job=_optional_int(runtime_signature.get("nthreads_per_job")),
        omp_nthreads=_optional_int(runtime_signature.get("omp_nthreads")),
        slurm_mem_gb=_optional_int(runtime_signature.get("slurm_mem_gb")),
        max_jobs=_optional_int(runtime_signature.get("max_jobs")),
        fs_no_reconall=fs_no_reconall,
        skip_bids_validation=bool(runtime_signature.get("skip_bids_validation")),
        task_id=optional_text(runtime_signature.get("task_id")),
        echo_idx=_optional_int(runtime_signature.get("echo_idx")),
        anat_only=bool(runtime_signature.get("anat_only")),
        fmriprep_custom_args=(
            dict(runtime_signature.get("fmriprep_custom_args"))
            if isinstance(runtime_signature.get("fmriprep_custom_args"), dict)
            else {}
        ),
        output_spaces=output_spaces,
        cifti_output=cifti_output,
        wsl_vhdx_path=_signature_path(runtime_signature, "wsl_vhdx_path", preserve_remote=preserve_remote),
        windows_host_drive=optional_text(runtime_signature.get("windows_host_drive")),
        docker_wsl_storage_path=_signature_path(
            runtime_signature,
            "docker_wsl_storage_path",
            preserve_remote=preserve_remote,
        ),
        run_id=request.run_id,
    )

def xcpd_request_from_runtime_artifact(
    request: RequestConfig,
    *,
    runtime_artifact: dict[str, Any] | None,
) -> RequestConfig:
    runtime_signature = runtime_artifact.get("request_signature") if isinstance(runtime_artifact, dict) else None
    if not isinstance(runtime_signature, dict):
        return request
    remote_host = optional_text(runtime_signature.get("remote_host")) or request.remote_host
    preserve_remote = bool(remote_host)
    return RequestConfig(
        action="runtime-audit",
        bids_root=_signature_path(runtime_signature, "bids_root", preserve_remote=preserve_remote),
        fmriprep_derivatives=_signature_path(runtime_signature, "fmriprep_derivatives", preserve_remote=preserve_remote),
        output_root=_signature_path(runtime_signature, "output_root", preserve_remote=preserve_remote),
        target="xcpd",
        remote_host=remote_host,
        subjects=_signature_strings(runtime_signature.get("subjects")),
        sessions=_signature_strings(runtime_signature.get("sessions")),
        work_root=_signature_path(runtime_signature, "work_root", preserve_remote=preserve_remote),
        log_root=_signature_path(runtime_signature, "log_root", preserve_remote=preserve_remote),
        download_root=_signature_path(runtime_signature, "download_root", preserve_remote=preserve_remote),
        fs_license=_signature_path(runtime_signature, "fs_license", preserve_remote=preserve_remote),
        templateflow_home=_signature_path(runtime_signature, "templateflow_home", preserve_remote=preserve_remote),
        templateflow_tool_bins=_signature_strings(runtime_signature.get("templateflow_tool_bins")),
        xcpd_image=optional_text(runtime_signature.get("xcpd_image")),
        container_runtime=optional_text(runtime_signature.get("container_runtime")) or "auto",
        executor_policy=optional_text(runtime_signature.get("executor_policy")) or "auto",
        scheduler_partition=optional_text(runtime_signature.get("scheduler_partition")),
        nthreads_per_job=_optional_int(runtime_signature.get("nthreads_per_job")),
        omp_nthreads=_optional_int(runtime_signature.get("omp_nthreads")),
        slurm_mem_gb=_optional_int(runtime_signature.get("slurm_mem_gb")),
        max_jobs=_optional_int(runtime_signature.get("max_jobs")),
        xcpd_mode=optional_text(runtime_signature.get("xcpd_mode")) or "abcd",
        xcpd_min_time=_optional_int(runtime_signature.get("xcpd_min_time")),
        xcpd_min_time_explicit=bool(runtime_signature.get("xcpd_min_time_explicit")),
        xcpd_motion_filter_type=optional_text(runtime_signature.get("xcpd_motion_filter_type")),
        xcpd_band_stop_min=_optional_float(runtime_signature.get("xcpd_band_stop_min")),
        xcpd_band_stop_max=_optional_float(runtime_signature.get("xcpd_band_stop_max")),
        xcpd_motion_filter_order=_optional_int(runtime_signature.get("xcpd_motion_filter_order")),
        xcpd_despike=optional_text(runtime_signature.get("xcpd_despike")),
        xcpd_task_ids=_signature_strings(runtime_signature.get("xcpd_task_ids")),
        xcpd_bids_filter_file=_signature_path(runtime_signature, "xcpd_bids_filter_file", preserve_remote=preserve_remote),
        xcpd_datasets=_signature_path_mapping(runtime_signature, "xcpd_datasets", preserve_remote=preserve_remote),
        xcpd_mem_mb=_optional_int(runtime_signature.get("xcpd_mem_mb")),
        xcpd_custom_args=dict(runtime_signature.get("xcpd_custom_args"))
        if isinstance(runtime_signature.get("xcpd_custom_args"), dict)
        else {},
        wsl_vhdx_path=_signature_path(runtime_signature, "wsl_vhdx_path", preserve_remote=preserve_remote),
        windows_host_drive=optional_text(runtime_signature.get("windows_host_drive")),
        docker_wsl_storage_path=_signature_path(
            runtime_signature,
            "docker_wsl_storage_path",
            preserve_remote=preserve_remote,
        ),
        run_id=request.run_id,
    )

def xcpd_request_from_artifacts(
    request: RequestConfig,
    *,
    runtime_artifact: dict[str, Any] | None,
    dataset_artifact: dict[str, Any] | None,
) -> RequestConfig:
    runtime_signature = runtime_artifact.get("request_signature") if isinstance(runtime_artifact, dict) else None
    dataset_signature = dataset_artifact.get("request_signature") if isinstance(dataset_artifact, dict) else None
    if not isinstance(runtime_signature, dict):
        return request
    selector_signature = dataset_signature if isinstance(dataset_signature, dict) else {}
    remote_host = optional_text(runtime_signature.get("remote_host")) or request.remote_host
    preserve_remote = bool(remote_host)
    return RequestConfig(
        action="submit",
        bids_root=_signature_path(runtime_signature, "bids_root", preserve_remote=preserve_remote),
        fmriprep_derivatives=_signature_path(runtime_signature, "fmriprep_derivatives", preserve_remote=preserve_remote),
        output_root=_signature_path(runtime_signature, "output_root", preserve_remote=preserve_remote),
        target="xcpd",
        remote_host=remote_host,
        subjects=_signature_strings(selector_signature.get("subjects")),
        sessions=_signature_strings(selector_signature.get("sessions")),
        work_root=_signature_path(runtime_signature, "work_root", preserve_remote=preserve_remote),
        log_root=_signature_path(runtime_signature, "log_root", preserve_remote=preserve_remote),
        download_root=_signature_path(runtime_signature, "download_root", preserve_remote=preserve_remote),
        fs_license=_signature_path(runtime_signature, "fs_license", preserve_remote=preserve_remote),
        templateflow_home=_signature_path(runtime_signature, "templateflow_home", preserve_remote=preserve_remote),
        templateflow_tool_bins=_signature_strings(runtime_signature.get("templateflow_tool_bins")),
        xcpd_image=optional_text(runtime_signature.get("xcpd_image")),
        container_runtime=optional_text(runtime_signature.get("container_runtime")) or "auto",
        executor_policy=optional_text(runtime_signature.get("executor_policy")) or "auto",
        scheduler_partition=optional_text(runtime_signature.get("scheduler_partition")),
        nthreads_per_job=_optional_int(runtime_signature.get("nthreads_per_job")),
        omp_nthreads=_optional_int(runtime_signature.get("omp_nthreads")),
        slurm_mem_gb=_optional_int(runtime_signature.get("slurm_mem_gb")),
        max_jobs=_optional_int(runtime_signature.get("max_jobs")),
        xcpd_mode=optional_text(runtime_signature.get("xcpd_mode")) or "abcd",
        xcpd_min_time=_optional_int(selector_signature.get("xcpd_min_time")),
        xcpd_min_time_explicit=bool(selector_signature.get("xcpd_min_time_explicit")),
        xcpd_motion_filter_type=optional_text(runtime_signature.get("xcpd_motion_filter_type")),
        xcpd_band_stop_min=_optional_float(runtime_signature.get("xcpd_band_stop_min")),
        xcpd_band_stop_max=_optional_float(runtime_signature.get("xcpd_band_stop_max")),
        xcpd_motion_filter_order=_optional_int(runtime_signature.get("xcpd_motion_filter_order")),
        xcpd_despike=optional_text(runtime_signature.get("xcpd_despike")),
        xcpd_task_ids=_signature_strings(selector_signature.get("xcpd_task_ids")),
        xcpd_bids_filter_file=_signature_path(selector_signature, "xcpd_bids_filter_file", preserve_remote=preserve_remote),
        xcpd_datasets=_signature_path_mapping(selector_signature, "xcpd_datasets", preserve_remote=preserve_remote),
        xcpd_mem_mb=_optional_int(selector_signature.get("xcpd_mem_mb")),
        xcpd_custom_args=dict(selector_signature.get("xcpd_custom_args"))
        if isinstance(selector_signature.get("xcpd_custom_args"), dict)
        else {},
        wsl_vhdx_path=_signature_path(runtime_signature, "wsl_vhdx_path", preserve_remote=preserve_remote),
        windows_host_drive=optional_text(runtime_signature.get("windows_host_drive")),
        docker_wsl_storage_path=_signature_path(
            runtime_signature,
            "docker_wsl_storage_path",
            preserve_remote=preserve_remote,
        ),
        run_id=request.run_id,
    )

def _signature_path(signature: dict[str, Any], field: str, *, preserve_remote: bool) -> Path | PurePosixPath | None:
    value = optional_text(signature.get(field))
    return path_value(value, preserve_remote_posix=preserve_remote) if value is not None else None

def _signature_path_mapping(
    signature: dict[str, Any],
    field: str,
    *,
    preserve_remote: bool,
) -> dict[str, Path | PurePosixPath]:
    value = signature.get(field)
    if not isinstance(value, dict):
        return {}
    result: dict[str, Path | PurePosixPath] = {}
    for alias, raw_path in value.items():
        if not isinstance(alias, str):
            continue
        path = path_value(optional_text(raw_path), preserve_remote_posix=preserve_remote)
        if path is not None:
            result[alias] = path
    return result

def _signature_strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []

def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None

def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None

def validated_stage_artifact(
    request: RequestConfig,
    *,
    command: str,
    artifact_command: str,
    artifact: dict[str, Any] | None = None,
    audit_id: str | None = None,
    load_missing: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current_artifact = artifact
    if current_artifact is None and load_missing:
        current_artifact = load_stage_artifact(request, artifact_command, audit_id=audit_id)
    if current_artifact is None:
        blocker = _artifact_code(artifact_command, "missing")
        return (
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": [blocker],
                        "findings": issue_bucket_findings(blockers=[blocker]),
                    },
                    "artifacts": {
                        "latest_audit_index": str(latest_audit_path(request)),
                    },
                },
            },
        )
    error = validate_stage_artifact(request, artifact_command, current_artifact)
    if error is not None:
        return (
            None,
            {
                "exit_code": 1,
                "payload": artifact_blocked_payload(
                    request,
                    command=command,
                    blocker=error,
                    artifact_command=artifact_command,
                    artifact=current_artifact,
                ),
            },
        )
    return current_artifact, None

def ready_execution_artifacts(
    request: RequestConfig,
    *,
    command: str,
    runtime_artifact: dict[str, Any] | None = None,
    dataset_artifact: dict[str, Any] | None = None,
    dataset_debug_artifact: dict[str, Any] | None = None,
    audit_id: str | None = None,
) -> tuple[dict[str, dict[str, Any] | None] | None, dict[str, Any] | None]:
    batch_loaded = False
    if runtime_artifact is None and dataset_artifact is None and dataset_debug_artifact is None:
        batch_loaded = True
        loaded_artifacts = load_stage_artifacts(
            request,
            ["runtime-audit", "dataset-audit", "dataset-audit-debug"],
            audit_id=audit_id,
        )
        runtime_artifact = loaded_artifacts["runtime-audit"]
        dataset_artifact = loaded_artifacts["dataset-audit"]
        dataset_debug_artifact = loaded_artifacts["dataset-audit-debug"]

    runtime_artifact, result = validated_stage_artifact(
        request,
        command=command,
        artifact_command="runtime-audit",
        artifact=runtime_artifact,
        audit_id=audit_id,
        load_missing=not batch_loaded,
    )
    if result is not None:
        return None, result
    if runtime_artifact["status"] != "ready":
        return (
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["runtime_audit_not_ready"],
                        "findings": issue_bucket_findings(blockers=["runtime_audit_not_ready"]),
                        "runtime": runtime_stage_summary(runtime_artifact),
                    },
                    "artifacts": stage_artifacts(request, {"runtime-audit": runtime_artifact}),
                },
            },
        )

    dataset_artifact, result = validated_stage_artifact(
        request,
        command=command,
        artifact_command="dataset-audit",
        artifact=dataset_artifact,
        audit_id=audit_id,
        load_missing=not batch_loaded,
    )
    if result is not None:
        return None, result
    if dataset_artifact["status"] != "ready":
        return (
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["dataset_audit_not_ready"],
                        "findings": issue_bucket_findings(blockers=["dataset_audit_not_ready"]),
                        "dataset": dataset_stage_summary(dataset_artifact),
                    },
                    "artifacts": stage_artifacts(request, {"dataset-audit": dataset_artifact}),
                },
            },
        )

    dataset_debug_artifact, result = validated_stage_artifact(
        request,
        command=command,
        artifact_command="dataset-audit-debug",
        artifact=dataset_debug_artifact,
        audit_id=audit_id,
        load_missing=not batch_loaded,
    )
    if result is not None:
        return None, result
    if dataset_debug_artifact["status"] != "ready":
        return (
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["dataset_audit_debug_not_ready"],
                        "findings": issue_bucket_findings(blockers=["dataset_audit_debug_not_ready"]),
                        "dataset": dataset_stage_summary(dataset_debug_artifact),
                    },
                    "artifacts": stage_artifacts(request, {"dataset-audit-debug": dataset_debug_artifact}),
                },
            },
        )

    return (
        {
            "runtime_artifact": runtime_artifact,
            "dataset_artifact": dataset_artifact,
            "dataset_debug_artifact": dataset_debug_artifact,
        },
        None,
    )

def ready_xcpd_execution_artifacts(
    request: RequestConfig,
    *,
    command: str,
    resume_from: str | None,
    user_pinned_fields: list[str] | None = None,
    audit_id: str | None = None,
) -> tuple[RequestConfig, dict[str, dict[str, Any] | None] | None, dict[str, Any] | None]:
    try:
        locator_request, selected_audit_id = artifact_execution_locator(request, resume_from=resume_from)
    except ValueError as exc:
        return (
            request,
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "error",
                    "command": command,
                    "error_type": "argument_parse",
                    "error_code": "invalid_arguments",
                    "message": str(exc),
                },
            },
        )
    audit_id = audit_id or selected_audit_id
    loaded_artifacts = load_stage_artifacts(
        locator_request,
        [XCPD_RUNTIME_AUDIT_COMMAND, XCPD_DATASET_AUDIT_COMMAND, XCPD_DATASET_AUDIT_DEBUG_COMMAND],
        audit_id=audit_id,
    )
    pinned_result = _validate_xcpd_pinned_fields(
        locator_request,
        command=command,
        loaded_artifacts=loaded_artifacts,
        user_pinned_fields=user_pinned_fields or [],
    )
    if pinned_result is not None:
        return locator_request, None, pinned_result
    request = xcpd_request_from_artifacts(
        locator_request,
        runtime_artifact=loaded_artifacts[XCPD_RUNTIME_AUDIT_COMMAND],
        dataset_artifact=loaded_artifacts[XCPD_DATASET_AUDIT_COMMAND],
    )
    runtime_artifact, result = validated_stage_artifact(
        request,
        command=command,
        artifact_command=XCPD_RUNTIME_AUDIT_COMMAND,
        artifact=loaded_artifacts[XCPD_RUNTIME_AUDIT_COMMAND],
        audit_id=audit_id,
        load_missing=False,
    )
    if result is not None:
        return request, None, result
    if runtime_artifact["status"] != "ready":
        return (
            request,
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["xcpd_runtime_audit_not_ready"],
                        "findings": issue_bucket_findings(blockers=["xcpd_runtime_audit_not_ready"]),
                        "runtime": runtime_stage_summary(runtime_artifact),
                    },
                    "artifacts": stage_artifacts(request, {XCPD_RUNTIME_AUDIT_COMMAND: runtime_artifact}),
                },
            },
        )

    dataset_request = _xcpd_request_with_saved_subject_scope(
        request,
        loaded_artifacts[XCPD_DATASET_AUDIT_COMMAND],
    )
    dataset_artifact, result = validated_stage_artifact(
        dataset_request,
        command=command,
        artifact_command=XCPD_DATASET_AUDIT_COMMAND,
        artifact=loaded_artifacts[XCPD_DATASET_AUDIT_COMMAND],
        audit_id=audit_id,
        load_missing=False,
    )
    if result is not None:
        return request, None, result
    if dataset_artifact["status"] != "ready":
        return (
            request,
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["xcpd_dataset_audit_not_ready"],
                        "findings": issue_bucket_findings(blockers=["xcpd_dataset_audit_not_ready"]),
                        "dataset": dataset_stage_summary(dataset_artifact),
                    },
                    "artifacts": stage_artifacts(request, {XCPD_DATASET_AUDIT_COMMAND: dataset_artifact}),
                },
            },
        )

    dataset_debug_request = _xcpd_request_with_saved_subject_scope(
        request,
        loaded_artifacts[XCPD_DATASET_AUDIT_DEBUG_COMMAND],
    )
    dataset_debug_artifact, result = validated_stage_artifact(
        dataset_debug_request,
        command=command,
        artifact_command=XCPD_DATASET_AUDIT_DEBUG_COMMAND,
        artifact=loaded_artifacts[XCPD_DATASET_AUDIT_DEBUG_COMMAND],
        audit_id=audit_id,
        load_missing=False,
    )
    if result is not None:
        return request, None, result
    if dataset_debug_artifact["status"] != "ready":
        return (
            request,
            None,
            {
                "exit_code": 1,
                "payload": {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["xcpd_dataset_audit_debug_not_ready"],
                        "findings": issue_bucket_findings(blockers=["xcpd_dataset_audit_debug_not_ready"]),
                        "dataset": dataset_stage_summary(dataset_debug_artifact),
                    },
                    "artifacts": stage_artifacts(request, {XCPD_DATASET_AUDIT_DEBUG_COMMAND: dataset_debug_artifact}),
                },
            },
        )
    return (
        request,
        {
            "runtime_artifact": runtime_artifact,
            "dataset_artifact": dataset_artifact,
            "dataset_debug_artifact": dataset_debug_artifact,
        },
        None,
    )

def _validate_xcpd_pinned_fields(
    request: RequestConfig,
    *,
    command: str,
    loaded_artifacts: dict[str, dict[str, Any] | None],
    user_pinned_fields: list[str],
) -> dict[str, Any] | None:
    current = _request_signature_values(request)
    checks = (
        (XCPD_RUNTIME_AUDIT_COMMAND, set(XCPD_RUNTIME_REQUEST_SIGNATURE_FIELDS)),
        (XCPD_DATASET_AUDIT_COMMAND, set(XCPD_DATASET_REQUEST_SIGNATURE_FIELDS)),
    )
    for artifact_command, signature_fields in checks:
        artifact = loaded_artifacts.get(artifact_command)
        saved = artifact.get("request_signature") if isinstance(artifact, dict) else None
        if not isinstance(saved, dict):
            continue
        for field in user_pinned_fields:
            if field in signature_fields and current.get(field) != saved.get(field):
                return {
                    "exit_code": 1,
                    "payload": artifact_blocked_payload(
                        request,
                        command=command,
                        blocker=_artifact_code(artifact_command, "mismatch"),
                        artifact_command=artifact_command,
                        artifact=artifact,
                    ),
                }
    return None


def _resume_locator(value: str, remote_host: str | None, *, option_name: str = "--resume-from") -> tuple[Path | PurePosixPath | None, str]:
    audit_buckets = {"fmriprep_audit", "xcpd_audit"}
    has_separator = any(token in value for token in ("/", "\\"))
    if not has_separator:
        if value.startswith("audit_"):
            raise ValueError(f"{option_name} audit dir requires a path under <output_root>/_artifacts/<audit-bucket>/")
        return None, value
    path = path_value(value, preserve_remote_posix=bool(remote_host))
    if path is None:
        raise ValueError(f"{option_name} must not be empty")
    audit_dir = path.parent if str(path).endswith(".json") else path
    if (
        not audit_dir.name.startswith("audit_")
        or audit_dir.parent.name not in audit_buckets
        or audit_dir.parent.parent.name != "_artifacts"
    ):
        raise ValueError(f"{option_name} must point to an artifact JSON file or an audit_<id> directory")
    return audit_dir.parent.parent.parent, audit_dir.name.removeprefix("audit_")

def process_execution_status(execute_result: dict[str, Any]) -> str:
    if execute_result["exit_code"] != 0:
        return "blocked"
    execution_status = str(execute_result["payload"].get("status") or "").strip()
    if execution_status in {"submitted", "launched", "completed", "success"}:
        return "completed" if execution_status == "success" else execution_status
    return "blocked"

def _emit_progress(progress: ProgressCallback | None, **event: Any) -> None:
    if progress is not None:
        progress(event)

def _execution_summary(execution: dict[str, Any]) -> dict[str, Any]:
    subjects = list(execution.get("subjects") or [])
    pre_steps = list(execution.get("pre_steps") or [])
    summary = {
        "mode": execution.get("mode"),
        "subject_count": len(subjects),
        "submitted_subject_count": sum(1 for subject in subjects if subject.get("status") == "submitted"),
        "launched_subject_count": sum(1 for subject in subjects if subject.get("status") == "launched"),
        "successful_subject_count": sum(1 for subject in subjects if subject.get("status") == "success"),
        "failed_subject_count": sum(1 for subject in subjects if subject.get("status") == "failed"),
        "skipped_subject_count": len(execution.get("skipped_subjects") or []),
    }
    job_ids = sorted({str(subject.get("job_id")) for subject in subjects if subject.get("job_id")})
    if len(job_ids) == 1:
        summary["job_id"] = job_ids[0]
    elif job_ids:
        summary["job_ids"] = job_ids
    pids = sorted({int(subject.get("pid")) for subject in subjects if subject.get("pid") is not None})
    if len(pids) == 1:
        summary["pid"] = pids[0]
    elif pids:
        summary["pids"] = pids
    failed_pre_steps = [
        _failed_pre_step_summary(step)
        for step in pre_steps
        if int(step.get("returncode") or 0) != 0
    ]
    if failed_pre_steps:
        summary["failed_pre_steps"] = failed_pre_steps
    failed_subjects = [
        _failed_subject_summary(subject)
        for subject in subjects
        if subject.get("status") == "failed"
    ]
    if failed_subjects:
        summary["failed_subjects"] = failed_subjects
    for key in ("launcher_stdout", "launcher_stderr", "pid_manifest"):
        if execution.get(key):
            summary[key] = execution[key]
    return summary

def _single_subject_container_command(execution_plan: dict[str, Any], step_name: str) -> str | None:
    for subject in execution_plan.get("subjects") or []:
        for step in subject.get("steps") or []:
            if step.get("step") == step_name and step.get("command"):
                return shlex.join([str(part) for part in step["command"]])
    return None

def _failed_pre_step_summary(step: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "step": step.get("step"),
        "returncode": step.get("returncode"),
    }
    for key in ("stdout_path", "stderr_path"):
        if key in step:
            summary[key] = step[key]
    if step.get("timed_out") is True:
        summary["timed_out"] = True
    if "timeout_seconds" in step:
        summary["timeout_seconds"] = step["timeout_seconds"]
    summary["error"] = _short_error(step.get("error")) or _fallback_pre_step_error(summary)
    return summary

def _failed_subject_summary(subject: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "subject_id",
        "session_id",
        "subject_key",
        "status",
        "returncode",
        "pid",
        "job_id",
        "array_task_id",
        "launcher_stdout",
        "launcher_stderr",
    )
    summary = {key: subject[key] for key in fields if key in subject}
    error = _short_error(subject.get("error"))
    if not error:
        subject_name = subject.get("subject_key") or subject.get("subject_id") or "unknown"
        returncode = subject.get("returncode")
        if returncode is not None:
            error = f"subject {subject_name} failed with returncode={returncode}"
        else:
            error = f"subject {subject_name} failed"
    summary["error"] = error
    return summary

def _fallback_pre_step_error(step: dict[str, Any]) -> str:
    step_name = step.get("step") or "unknown"
    if step.get("timed_out") is True and step.get("timeout_seconds") is not None:
        return f"pre-step {step_name} timed out after {step['timeout_seconds']}s"
    return f"pre-step {step_name} failed with returncode={step.get('returncode')}"

def _short_error(value: Any) -> str:
    return " ".join(str(value or "").split())[:500]

def _summary_line(prefix: str, error: Any) -> str:
    detail = _short_error(error)
    if not detail or detail == prefix:
        return prefix
    return f"{prefix}: {detail}"

def print_execution_failure_summary(payload: dict[str, Any]) -> None:
    if payload.get("status") != "failed":
        return
    execution = (payload.get("summary") or {}).get("execution") or {}
    failed_pre_steps = list(execution.get("failed_pre_steps") or [])
    if failed_pre_steps:
        step = failed_pre_steps[0]
        prefix = (
            f"pre-step {step.get('step') or 'unknown'} failed "
            f"with returncode={step.get('returncode')}"
        )
        message = _summary_line(prefix, step.get("error"))
    else:
        failed_subjects = list(execution.get("failed_subjects") or [])
        if failed_subjects:
            subject = failed_subjects[0]
            subject_name = subject.get("subject_key") or subject.get("subject_id") or "unknown"
            if subject.get("returncode") is not None:
                prefix = f"subject {subject_name} failed with returncode={subject.get('returncode')}"
            else:
                prefix = f"subject {subject_name} failed"
            message = _summary_line(prefix, subject.get("error"))
        else:
            message = "execution failed"
    print(f"[fmri-process] execution failed: {message}", file=sys.stderr, flush=True)

def _execution_runnable_subjects(command: str, dataset_debug_audit: dict[str, Any]) -> list[dict[str, Any]]:
    runnable_subjects = list(dataset_debug_audit.get("runnable_subjects") or [])
    if command != "run-xcpd":
        return runnable_subjects
    normalized: list[dict[str, Any]] = []
    for subject in list(dataset_debug_audit.get("subject_readiness") or []):
        xcpd_status = str(((subject.get("xcpd") or {}).get("status")) or "blocked")
        if xcpd_status != "ready":
            continue
        raw_subject_id = str(subject.get("subject_id") or "").strip()
        if not raw_subject_id:
            continue
        subject_id = raw_subject_id.removeprefix("sub-")
        session_ids = [
            str(session_id).strip().removeprefix("ses-")
            for session_id in list(subject.get("session_ids") or [])
            if str(session_id).strip()
        ]
        normalized.append({"subject_id": subject_id, "session_ids": session_ids, "steps": ["xcpd"]})
    return normalized

def _safe_request_signature(request: RequestConfig) -> dict[str, Any] | None:
    try:
        return build_request_signature(request)
    except ValueError:
        return None

def _xcpd_request_with_saved_subject_scope(request: RequestConfig, artifact: dict[str, Any] | None) -> RequestConfig:
    signature = artifact.get("request_signature") if isinstance(artifact, dict) else {}
    if not isinstance(signature, dict):
        return request
    subjects = _string_list_value(signature.get("subjects"))
    sessions = _string_list_value(signature.get("sessions"))
    return replace(request, subjects=subjects, sessions=sessions)

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

def artifact_blocked_payload(
    request: RequestConfig,
    *,
    command: str,
    blocker: str,
    artifact_command: str,
    artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = {"blockers": [blocker], "findings": issue_bucket_findings(blockers=[blocker])}
    current_request_signature = _safe_request_signature(request)
    if current_request_signature is not None:
        summary["current_request_signature"] = current_request_signature
    saved_request_signature = artifact.get("request_signature") if isinstance(artifact, dict) else None
    if isinstance(saved_request_signature, dict):
        summary["saved_request_signature"] = saved_request_signature
    if isinstance(artifact, dict) and blocker.endswith("_request_mismatch"):
        summary["request_mismatch"] = _request_mismatch_summary(request, artifact_command, artifact)
        storage_mismatch = _storage_estimate_mismatch_summary(request, artifact_command, artifact)
        if storage_mismatch is not None:
            summary["storage_estimate_mismatch"] = storage_mismatch
    key = artifact_command.replace("-", "_")
    artifacts = {"latest_audit_index": str(latest_audit_path(request))}
    archive_path = _artifact_archive_path(request, artifact_command, artifact) if isinstance(artifact, dict) else None
    if archive_path is not None:
        artifacts[f"{key}_archive"] = str(archive_path)
    return {
        "status": "blocked",
        "command": command,
        "summary": summary,
        "artifacts": artifacts,
    }

def _request_mismatch_summary(
    request: RequestConfig,
    artifact_command: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    saved = artifact.get("request_signature") if isinstance(artifact.get("request_signature"), dict) else {}
    current = _request_signature_values(request)
    fields = list(_request_mismatch_fields(artifact_command))
    mismatched = [field for field in fields if saved.get(field) != current.get(field)]
    summary: dict[str, Any] = {}
    if artifact_command in {"runtime-audit", XCPD_RUNTIME_AUDIT_COMMAND}:
        runtime_audit = artifact.get("runtime_audit") if isinstance(artifact.get("runtime_audit"), dict) else {}
        saved_templates = _string_list_value(runtime_audit.get("required_templateflow_templates"))
        current_templates = required_templateflow_templates(request)
        current_cifti_output = request.cifti_output if request.target == PROCESS_TARGET else None
        saved_cifti_output = saved.get("cifti_output")
        current["required_templateflow_templates"] = current_templates
        saved["required_templateflow_templates"] = saved_templates
        current["cifti_output"] = current_cifti_output
        saved["cifti_output"] = saved_cifti_output
        summary.update(
            {
                "current_required_templateflow_templates": current_templates,
                "saved_required_templateflow_templates": saved_templates,
                "current_cifti_output": current_cifti_output,
                "saved_cifti_output": saved_cifti_output,
            }
        )
        if saved_templates != current_templates and "required_templateflow_templates" not in mismatched:
            mismatched.append("required_templateflow_templates")
        if saved_cifti_output != current_cifti_output and "cifti_output" not in mismatched:
            mismatched.append("cifti_output")
    summary.update(
        {
            "fields": mismatched,
            "current": {field: current.get(field) for field in mismatched},
            "saved": {field: saved.get(field) for field in mismatched},
        }
    )
    return summary

def _storage_estimate_mismatch_summary(
    request: RequestConfig,
    artifact_command: str,
    artifact: dict[str, Any],
) -> dict[str, Any] | None:
    if artifact_command not in {"dataset-audit", "dataset-audit-debug"}:
        return None
    payload = artifact.get("dataset_audit")
    if not isinstance(payload, dict):
        return None
    saved = payload.get("storage_estimate_signature")
    if not isinstance(saved, dict):
        return None
    current = build_storage_estimate_signature(request)
    fields = [
        field
        for field in (
            "output_spaces",
            "cifti_output",
            "fs_no_reconall",
            "task_id",
            "echo_idx",
            "anat_only",
            "wsl_vhdx_path",
            "windows_host_drive",
        )
        if saved.get(field) != current.get(field)
    ]
    if not fields:
        return None
    return {
        "fields": fields,
        "current": {field: current.get(field) for field in fields},
        "saved": {field: saved.get(field) for field in fields},
    }

def _request_mismatch_fields(artifact_command: str) -> tuple[str, ...]:
    if artifact_command in {XCPD_DATASET_AUDIT_COMMAND, XCPD_DATASET_AUDIT_DEBUG_COMMAND}:
        return XCPD_DATASET_REQUEST_SIGNATURE_FIELDS
    if artifact_command == "runtime-audit":
        return FMRIPREP_RUNTIME_REQUEST_SIGNATURE_FIELDS
    if artifact_command == XCPD_RUNTIME_AUDIT_COMMAND:
        return XCPD_RUNTIME_REQUEST_SIGNATURE_FIELDS
    return ("bids_root", "output_root", "remote_host", "subjects", "sessions")

def _request_signature_values(request: RequestConfig) -> dict[str, Any]:
    def resolved(value: Any) -> str | None:
        try:
            result = value()
        except ValueError:
            return None
        return str(result) if result is not None else None

    return {
        "bids_root": str(request.bids_root) if request.bids_root is not None else None,
        "fmriprep_derivatives": (
            str(request.fmriprep_derivatives) if request.fmriprep_derivatives is not None else None
        ),
        "output_root": resolved(request.resolve_output_root),
        "remote_host": request.remote_host,
        "subjects": sorted(request.subjects),
        "sessions": sorted(request.sessions),
        "fs_no_reconall": request.fs_no_reconall,
        "task_id": request.task_id,
        "echo_idx": request.echo_idx,
        "anat_only": request.anat_only,
        "output_spaces": list(request.output_spaces),
        "cifti_output": request.cifti_output,
        "fmriprep_custom_args": dict(request.fmriprep_custom_args),
        "work_root": resolved(request.resolve_work_root),
        "log_root": resolved(request.resolve_log_root),
        "download_root": resolved(request.resolve_download_root),
        "fs_license": str(request.fs_license) if request.fs_license is not None else None,
        "templateflow_home": str(request.templateflow_home) if request.templateflow_home is not None else None,
        "templateflow_tool_bins": list(request.templateflow_tool_bins),
        "fmriprep_image": request.fmriprep_image,
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
        "xcpd_bids_filter_file": str(request.xcpd_bids_filter_file) if request.xcpd_bids_filter_file is not None else None,
        "xcpd_datasets": {
            alias: str(path) for alias, path in sorted(request.xcpd_datasets.items())
        },
        "xcpd_mem_mb": request.xcpd_mem_mb,
        "xcpd_custom_args": dict(request.xcpd_custom_args),
        "container_runtime": request.container_runtime,
        "executor_policy": request.executor_policy,
        "scheduler_partition": request.scheduler_partition,
        "skip_bids_validation": request.skip_bids_validation,
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

def _string_list_value(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []

def _artifact_audit_id(artifact: dict[str, Any] | None) -> str | None:
    if not isinstance(artifact, dict):
        return None
    value = artifact.get("audit_id")
    return value if isinstance(value, str) and value else None

def _shared_audit_id(*artifacts: dict[str, Any] | None) -> str | None:
    audit_ids = {_artifact_audit_id(artifact) for artifact in artifacts if artifact is not None}
    audit_ids.discard(None)
    if len(audit_ids) != 1:
        return None
    return next(iter(audit_ids))

def _artifact_audit_ids(artifacts: dict[str, dict[str, Any] | None]) -> dict[str, str | None]:
    return {name: _artifact_audit_id(artifact) for name, artifact in artifacts.items()}

def artifact_status(artifact: dict[str, Any]) -> str | None:
    status = artifact.get("status")
    return status if isinstance(status, str) else None

def runtime_prepare_eligible(artifact: dict[str, Any]) -> bool:
    if artifact_status(artifact) != "needs_prepare":
        return False
    runtime_audit = artifact.get("runtime_audit") or {}
    prepare_required = list(runtime_audit.get("prepare_required") or [])
    blockers = list(runtime_audit.get("blockers") or [])
    return bool(prepare_required) and not blockers

def _artifact_archive_path(
    request: RequestConfig,
    command: str,
    artifact: dict[str, Any] | None,
) -> Path | PurePosixPath | None:
    audit_id = _artifact_audit_id(artifact)
    if audit_id is None:
        return None
    submission_id = None
    if command in {"execution-context", "submission-result"}:
        if not isinstance(artifact, dict):
            return None
        raw_submission_id = artifact.get("submission_id")
        if not isinstance(raw_submission_id, str) or not raw_submission_id:
            return None
        submission_id = raw_submission_id
    return archived_artifact_path(request, command, audit_id=audit_id, submission_id=submission_id)

def stage_artifacts(
    request: RequestConfig,
    artifacts: dict[str, dict[str, Any] | None],
) -> dict[str, str]:
    payload = {"latest_audit_index": str(latest_audit_path(request))}
    for command, artifact in artifacts.items():
        archive_path = _artifact_archive_path(request, command, artifact)
        if archive_path is None:
            continue
        payload[f"{command.replace('-', '_')}_archive"] = str(archive_path)
    return payload

def dataset_stage_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    dataset_audit = artifact.get("dataset_audit") or {}
    return {
        "status": artifact_status(artifact),
        "summary": dict(dataset_audit.get("summary") or {}),
        "warnings": list(dataset_audit.get("warnings") or []),
        "findings": dict(dataset_audit.get("findings") or {}),
        "subject_exclusions": list(dataset_audit.get("subject_exclusions") or []),
    }

def runtime_stage_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    runtime_audit = artifact.get("runtime_audit") or {}
    summary = runtime_audit_summary(runtime_audit)
    summary["status"] = artifact_status(artifact)
    return summary


def cpu_parallelism_summary(resources: dict[str, Any], execution_strategy: str | None) -> dict[str, Any] | None:
    if execution_strategy != "worker_pool":
        return None
    existing = resources.get("cpu_parallelism")
    if isinstance(existing, dict):
        return dict(existing)
    max_jobs = _positive_int(resources.get("max_jobs"))
    nthreads_per_job = _positive_int(resources.get("nthreads_per_job"))
    cpu_total = _positive_int(resources.get("cpu_total"))
    if max_jobs is None or nthreads_per_job is None or cpu_total is None:
        return None
    requested = max_jobs * nthreads_per_job
    recommended_limit = max(1, cpu_total - (2 if cpu_total >= 8 else 1))
    return {
        "requested": requested,
        "available": cpu_total,
        "recommended_limit": recommended_limit,
        "expression": f"{max_jobs} max_jobs * {nthreads_per_job} nthreads_per_job = {requested}",
    }


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def execute_from_artifacts(
    request: RequestConfig,
    *,
    command: str,
    runtime_artifact: dict[str, Any],
    dataset_artifact: dict[str, Any],
    dataset_debug_artifact: dict[str, Any] | None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    runtime_audit = runtime_artifact["runtime_audit"]
    dataset_audit = dataset_artifact["dataset_audit"]
    if command == "run-xcpd":
        if dataset_debug_artifact is None:
            return {
                "exit_code": 1,
                "payload": artifact_blocked_payload(
                    request,
                    command=command,
                    blocker="missing_xcpd_dataset_audit_debug_artifact",
                    artifact_command=XCPD_DATASET_AUDIT_DEBUG_COMMAND,
                    artifact=None,
                ),
            }
        dataset_subject_source = dataset_debug_artifact["dataset_audit"]
        artifact_commands = {
            "runtime": XCPD_RUNTIME_AUDIT_COMMAND,
            "dataset": XCPD_DATASET_AUDIT_COMMAND,
            "dataset_debug": XCPD_DATASET_AUDIT_DEBUG_COMMAND,
        }
        audit_id = _shared_audit_id(runtime_artifact, dataset_artifact, dataset_debug_artifact)
        audit_id_artifacts = {
            "runtime_audit": runtime_artifact,
            "dataset_audit": dataset_artifact,
            "dataset_audit_debug": dataset_debug_artifact,
        }
        stage_artifact_inputs = {
            artifact_commands["runtime"]: runtime_artifact,
            artifact_commands["dataset"]: dataset_artifact,
            artifact_commands["dataset_debug"]: dataset_debug_artifact,
        }
    else:
        if dataset_debug_artifact is None:
            return {
                "exit_code": 1,
                "payload": artifact_blocked_payload(
                    request,
                    command=command,
                    blocker="missing_dataset_audit_debug_artifact",
                    artifact_command="dataset-audit-debug",
                    artifact=None,
                ),
            }
        dataset_subject_source = dataset_debug_artifact["dataset_audit"]
        artifact_commands = {
            "runtime": "runtime-audit",
            "dataset": "dataset-audit",
        }
        audit_id = _shared_audit_id(runtime_artifact, dataset_artifact, dataset_debug_artifact)
        audit_id_artifacts = {
            "runtime_audit": runtime_artifact,
            "dataset_audit": dataset_artifact,
            "dataset_audit_debug": dataset_debug_artifact,
        }
        stage_artifact_inputs = {
            artifact_commands["runtime"]: runtime_artifact,
            artifact_commands["dataset"]: dataset_artifact,
        }
    runtime_audit = expand_runtime_audit_with_proofs(request, dict(runtime_audit), require_ready_proofs=True)
    if runtime_audit.get("proof_resolution_error"):
        return {
            "exit_code": 1,
            "payload": artifact_blocked_payload(
                request,
                command=command,
                blocker=str(runtime_audit["proof_resolution_error"]),
                artifact_command=artifact_commands["runtime"],
                artifact=runtime_artifact,
            ),
        }
    if audit_id is None:
        return {
            "exit_code": 1,
            "payload": {
                "status": "blocked",
                "command": command,
                "summary": {
                    "blockers": ["audit_snapshot_mismatch"],
                    "findings": issue_bucket_findings(blockers=["audit_snapshot_mismatch"]),
                    "audit_ids": _artifact_audit_ids(audit_id_artifacts),
                },
                "artifacts": stage_artifacts(request, stage_artifact_inputs),
            },
        }
    execution_plan = build_execution_plan(
        request,
        runtime_audit,
        _execution_runnable_subjects(command, dataset_subject_source),
        request.run_id or command,
    )
    readiness = build_execution_readiness(
        request,
        runtime_audit,
        dataset_audit,
        execution_plan,
    )
    artifacts = stage_artifacts(request, stage_artifact_inputs)
    if readiness["status"] == "blocked":
        summary: dict[str, Any] = {
            "runtime": runtime_stage_summary(runtime_artifact),
            "dataset": dataset_stage_summary(dataset_artifact),
            "execution_readiness": readiness,
        }
        if "no_runnable_subjects" in list(readiness.get("blockers") or []):
            summary["subject_exclusions"] = list(dataset_audit.get("subject_exclusions") or [])
        return {
            "exit_code": 1,
            "payload": {
                "status": "blocked",
                "command": command,
                "summary": summary,
                "artifacts": artifacts,
            },
        }

    submission_id = new_submission_id()
    write_execution_context_artifact(
        request,
        runtime_artifact=runtime_artifact,
        dataset_artifact=dataset_artifact,
        execution_plan=execution_plan,
        status=readiness["status"],
        audit_id=audit_id,
        submission_id=submission_id,
    )
    execution_context_archive = archived_artifact_path(
        request,
        "execution-context",
        audit_id=audit_id,
        submission_id=submission_id,
    )
    artifacts["execution_context_archive"] = str(execution_context_archive)
    _emit_progress(
        progress,
        stage="run-fmriprep" if command == "run-fmriprep" else command,
        status="started",
        message="Execution context archive written",
        path=str(execution_context_archive),
    )

    execution = execute_plan(
        request,
        execution_plan,
        execution_context_path=execution_context_archive,
        progress=progress,
    )
    submission_result = write_submission_result_artifact(
        request,
        execution_context_path=execution_context_archive,
        execution=execution,
        audit_id=audit_id,
        submission_id=submission_id,
    )
    artifacts["submission_result_archive"] = str(_artifact_archive_path(request, "submission-result", submission_result))
    execution_summary = _execution_summary(execution)
    execution_status = str(execution.get("status") or "")
    payload_status = "completed" if execution_status == "success" else execution_status
    success_statuses = {"submitted", "launched", "completed"}
    execution_step = {"run-fmriprep": "fmriprep", "run-xcpd": "xcpd"}.get(command)
    if execution_step and payload_status in success_statuses:
        single_subject_command = _single_subject_container_command(execution_plan, execution_step)
        if single_subject_command:
            execution_summary["single_subject_command"] = single_subject_command
    return {
        "exit_code": 0 if payload_status in success_statuses else 1,
        "payload": {
            "status": payload_status,
            "command": command,
            "summary": {
                "runtime": runtime_stage_summary(runtime_artifact),
                "dataset": dataset_stage_summary(dataset_artifact),
                "execution": execution_summary,
            },
            "artifacts": artifacts,
        },
    }


def runtime_audit_summary(runtime_audit: dict[str, Any]) -> dict[str, Any]:
    context = runtime_audit.get("runtime_context") if isinstance(runtime_audit.get("runtime_context"), dict) else {}
    resources = runtime_audit.get("resources") or runtime_audit.get("resource_summary") or {}
    execution_strategy = runtime_audit.get("execution_strategy", context.get("execution_strategy"))
    resource_summary = {
        "max_jobs": resources.get("max_jobs"),
        "nthreads_per_job": resources.get("nthreads_per_job"),
        "omp_nthreads": resources.get("omp_nthreads"),
    }
    cpu_parallelism = cpu_parallelism_summary(resources, execution_strategy)
    if cpu_parallelism is not None:
        resource_summary["cpu_parallelism"] = cpu_parallelism
    summary = {
        "selected_runtime": runtime_audit.get("selected_runtime", context.get("container_runtime")),
        "selected_executor_policy": runtime_audit.get("selected_executor_policy", context.get("executor_policy")),
        "execution_strategy": execution_strategy,
        "slurm_available": runtime_audit.get("slurm_available"),
        "in_slurm_allocation": runtime_audit.get("in_slurm_allocation"),
        "local_execution_allowed": runtime_audit.get("local_execution_allowed"),
        "slurm_job_id": runtime_audit.get("slurm_job_id"),
        "resources": resource_summary,
        "warning_count": len(runtime_audit.get("warnings", [])),
        "prepare_required_count": len(runtime_audit.get("prepare_required", [])),
        "blocker_count": len(runtime_audit.get("blockers", [])),
        "required_templateflow_templates": list(runtime_audit.get("required_templateflow_templates") or []),
        "warnings": runtime_audit.get("warnings", []),
        "prepare_required": runtime_audit.get("prepare_required", []),
        "prepare_requirements": list(runtime_audit.get("prepare_requirements") or []),
        "blockers": runtime_audit.get("blockers", []),
    }
    if "findings" in runtime_audit:
        summary["findings"] = dict(runtime_audit.get("findings") or {})
    return summary
