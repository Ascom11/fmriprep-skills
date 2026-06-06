"""XCP-D context seeding from saved fMRIPrep artifacts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from fmri_core.audit import load_stage_artifacts
from fmri_core.issue_codes import issue_bucket_findings
from fmri_core.models import RequestConfig, SubjectEntry
from fmri_core.runtime_proofs import reusable_runtime_proofs
from fmri_process import execution_flow

NEXT_ACTION_PROCESS = "process"


@dataclass(frozen=True)
class _XcpdContextSeed:
    request: RequestConfig
    subject_scope: list[SubjectEntry]
    reusable_proofs: dict[str, dict[str, Any]]


def seed_from_fmriprep_context(
    request: RequestConfig,
    *,
    reuse_context_from: str,
    user_pinned_fields: list[str],
) -> _XcpdContextSeed | dict[str, Any]:
    try:
        locator_request, source_audit_id = execution_flow.artifact_execution_locator(
            request,
            resume_from=reuse_context_from,
            option_name="--reuse-context-from",
        )
    except ValueError as exc:
        return execution_flow.argument_error_payload("xcpd-audit", str(exc))
    locator_request = replace(locator_request, target="fmriprep")

    loaded_artifacts = load_stage_artifacts(
        locator_request,
        ["runtime-audit", "dataset-audit", "dataset-audit-debug"],
        audit_id=source_audit_id,
    )
    runtime_artifact = loaded_artifacts["runtime-audit"]
    dataset_artifact = loaded_artifacts["dataset-audit"]
    dataset_debug_artifact = loaded_artifacts["dataset-audit-debug"]
    if runtime_artifact is None or dataset_artifact is None or dataset_debug_artifact is None:
        return execution_flow.argument_error_payload(
            "xcpd-audit",
            "--reuse-context-from must point to a saved fMRIPrep audit with runtime-audit.json, dataset-audit.json, and dataset-audit-debug.json",
        )
    source_context = execution_flow.fmriprep_request_from_artifacts(
        locator_request,
        runtime_artifact=runtime_artifact,
        dataset_artifact=dataset_artifact,
    )
    runtime_artifact, result = execution_flow.validated_stage_artifact(
        source_context,
        command="xcpd-audit",
        artifact_command="runtime-audit",
        artifact=runtime_artifact,
        audit_id=source_audit_id,
        load_missing=False,
    )
    if result is not None:
        return result
    dataset_artifact, result = execution_flow.validated_stage_artifact(
        source_context,
        command="xcpd-audit",
        artifact_command="dataset-audit",
        artifact=dataset_artifact,
        audit_id=source_audit_id,
        load_missing=False,
    )
    if result is not None:
        return result

    if dataset_debug_artifact is not None:
        dataset_debug_artifact, result = execution_flow.validated_stage_artifact(
            source_context,
            command="xcpd-audit",
            artifact_command="dataset-audit-debug",
            artifact=dataset_debug_artifact,
            audit_id=source_audit_id,
            load_missing=False,
        )
        if result is not None:
            return result
    if source_context.fs_no_reconall and request.xcpd_mode == "abcd":
        return {
            "exit_code": 1,
            "payload": {
                "status": "blocked",
                "command": "xcpd-audit",
                "next_action": NEXT_ACTION_PROCESS,
                "summary": {
                    "blockers": ["xcpd_abcd_requires_surface_or_cifti"],
                    "findings": issue_bucket_findings(blockers=["xcpd_abcd_requires_surface_or_cifti"]),
                    "message": (
                        "The reused fMRIPrep audit was created with fs_no_reconall; "
                        "XCP-D abcd mode requires fsLR/CIFTI derivatives."
                    ),
                },
            },
        }
    source_subjects = _fmriprep_runnable_subject_selectors(dataset_debug_artifact)
    pinned = set(user_pinned_fields)

    def keep(field: str) -> bool:
        return field in pinned

    seeded_request = replace(
        request,
        bids_root=request.bids_root if keep("bids_root") or request.bids_root is not None else source_context.bids_root,
        fmriprep_derivatives=(
            request.fmriprep_derivatives
            if keep("fmriprep_derivatives") or request.fmriprep_derivatives is not None
            else source_context.resolve_fmriprep_derivatives_root()
        ),
        output_root=(
            request.output_root if keep("output_root") or request.output_root is not None else source_context.output_root
        ),
        remote_host=(
            request.remote_host if keep("remote_host") or request.remote_host is not None else source_context.remote_host
        ),
        subjects=request.subjects if keep("subjects") or request.subjects else source_subjects or source_context.subjects,
        sessions=request.sessions if keep("sessions") or request.sessions else source_context.sessions,
        work_root=request.work_root if keep("work_root") or request.work_root is not None else source_context.work_root,
        log_root=request.log_root if keep("log_root") or request.log_root is not None else source_context.log_root,
        download_root=(
            request.download_root
            if keep("download_root") or request.download_root is not None
            else source_context.download_root
        ),
        fs_license=(
            request.fs_license if keep("fs_license") or request.fs_license is not None else source_context.fs_license
        ),
        templateflow_home=(
            request.templateflow_home
            if keep("templateflow_home") or request.templateflow_home is not None
            else source_context.templateflow_home
        ),
        templateflow_tool_bins=(
            request.templateflow_tool_bins
            if keep("templateflow_tool_bins") or request.templateflow_tool_bins
            else source_context.templateflow_tool_bins
        ),
        container_runtime=request.container_runtime if keep("container_runtime") else source_context.container_runtime,
        executor_policy=request.executor_policy if keep("executor_policy") else source_context.executor_policy,
        scheduler_partition=(
            request.scheduler_partition
            if keep("scheduler_partition") or request.scheduler_partition is not None
            else source_context.scheduler_partition
        ),
        nthreads_per_job=(
            request.nthreads_per_job
            if keep("nthreads_per_job") or request.nthreads_per_job is not None
            else source_context.nthreads_per_job
        ),
        omp_nthreads=(
            request.omp_nthreads
            if keep("omp_nthreads") or request.omp_nthreads is not None
            else source_context.omp_nthreads
        ),
        slurm_mem_gb=request.slurm_mem_gb if keep("slurm_mem_gb") or request.slurm_mem_gb is not None else source_context.slurm_mem_gb,
        max_jobs=request.max_jobs if keep("max_jobs") or request.max_jobs is not None else source_context.max_jobs,
        wsl_vhdx_path=(
            request.wsl_vhdx_path
            if keep("wsl_vhdx_path") or request.wsl_vhdx_path is not None
            else source_context.wsl_vhdx_path
        ),
        windows_host_drive=(
            request.windows_host_drive
            if keep("windows_host_drive") or request.windows_host_drive is not None
            else source_context.windows_host_drive
        ),
        docker_wsl_storage_path=(
            request.docker_wsl_storage_path
            if keep("docker_wsl_storage_path") or request.docker_wsl_storage_path is not None
            else source_context.docker_wsl_storage_path
        ),
    )
    reusable_proofs = reusable_runtime_proofs(
        source_context,
        runtime_artifact,
        source_audit_id=source_audit_id,
    )
    return _XcpdContextSeed(
        request=seeded_request,
        subject_scope=xcpd_subject_scope_from_fmriprep_debug(dataset_debug_artifact, seeded_request),
        reusable_proofs=reusable_proofs,
    )

def _fmriprep_runnable_subject_selectors(dataset_debug_artifact: dict[str, Any] | None) -> list[str]:
    if not isinstance(dataset_debug_artifact, dict):
        return []
    dataset_audit = dataset_debug_artifact.get("dataset_audit")
    if not isinstance(dataset_audit, dict):
        return []
    selectors: list[str] = []
    for subject in dataset_audit.get("runnable_subjects") or []:
        if not isinstance(subject, dict):
            continue
        subject_id = execution_flow.optional_text(subject.get("subject_id"))
        if subject_id is not None:
            selectors.append(subject_id.removeprefix("sub-"))
    return _dedupe_strings(selectors)


def xcpd_subject_scope_from_fmriprep_debug(
    dataset_debug_artifact: dict[str, Any],
    request: RequestConfig,
) -> list[SubjectEntry]:
    dataset_audit = dataset_debug_artifact.get("dataset_audit")
    if not isinstance(dataset_audit, dict):
        return []
    requested_subjects = {_normalize_selector_id(value, prefix="sub-") for value in request.subjects}
    requested_sessions = {_normalize_selector_id(value, prefix="ses-") for value in request.sessions}
    scope: list[SubjectEntry] = []
    for subject in dataset_audit.get("runnable_subjects") or []:
        if not isinstance(subject, dict):
            continue
        subject_id = execution_flow.optional_text(subject.get("subject_id"))
        if subject_id is None:
            continue
        normalized_subject = _normalize_selector_id(subject_id, prefix="sub-")
        if requested_subjects and normalized_subject not in requested_subjects:
            continue
        session_ids = [
            _normalize_selector_id(value, prefix="ses-")
            for value in list(subject.get("session_ids") or [])
            if execution_flow.optional_text(value) is not None
        ]
        if not session_ids:
            if not requested_sessions:
                scope.append(SubjectEntry(subject_id=normalized_subject))
            continue
        for session_id in session_ids:
            if requested_sessions and session_id not in requested_sessions:
                continue
            scope.append(SubjectEntry(subject_id=normalized_subject, session_id=session_id))
    return scope


def _normalize_selector_id(value: Any, *, prefix: str) -> str:
    return str(value).strip().removeprefix(prefix)


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
