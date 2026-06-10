"""Inspect the BIDS dataset and decide which subjects are runnable."""

from __future__ import annotations

import csv
import io
import json
import shlex
from dataclasses import replace
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from bids import BIDSLayout

from .image_metadata import load_image_metadata
from .models import ProgressCallback, RequestConfig, RequestPath, SubjectEntry, validate_remote_request_paths
from .shell import (
    glob_exists,
    glob_paths,
    path_exists,
    path_is_symlink,
    path_readable,
    probe_remote_dataset,
    probe_remote_dataset_with_metrics,
    read_text,
    run_command,
    shell_command,
)
from .storage_check import run_storage_check_with_warnings

HIGH_RESOLUTION_BOLD_THRESHOLD_MM = 1.5
HIGH_RESOLUTION_OUTPUT_ADVICE = {
    "code": "high_resolution_input_res2_default",
    "message": (
        "High-resolution BOLD inputs were detected. The default fMRIPrep command still uses fixed res-2 "
        "standard-space outputs for predictable group analysis. Preserve high-resolution derivatives only "
        "when the study needs that explicitly."
    ),
}
XCPD_ABCD_CIFTI_REASON = "missing_xcpd_abcd_cifti_derivatives"
XCPD_NICHART_NIFTI_REASON = "missing_xcpd_nichart_nifti_derivatives"
XCPD_BIDS_ROOT_NOT_PROVIDED_WARNING = "xcpd_bids_root_not_provided"
XCPD_POST_CENSOR_TIME_UNESTIMATED_WARNING = "xcpd_post_censor_time_unestimated"
XCPD_DATASET_ALIAS_PATTERN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
DEFAULT_XCPD_FD_THRESH = 0.3
DEFAULT_XCPD_HEAD_RADIUS_MM = 50.0
MOTION_COLUMNS = ("trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z")


def _xcpd_output_root(output_root: RequestPath) -> RequestPath:
    return output_root / "xcp_d"


def audit_dataset(request: RequestConfig, progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Audit the dataset and summarize runnable subject state.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.

    Returns:
        dict[str, Any]: Dataset audit payload for the request.
    """
    bids_root = request.resolve_bids_root()
    _emit_progress(
        progress,
        stage="dataset-audit",
        status="started",
        message=f"Inspecting dataset at {bids_root}",
        path=str(bids_root),
        remote_host=request.remote_host,
    )
    if request.remote_host:
        validate_remote_request_paths(request)
    if request.remote_host and not isinstance(bids_root, PurePosixPath):
        raise ValueError(f"Remote dataset audit requires a POSIX bids_root, got: {bids_root}")
    remote_probe = None
    if request.remote_host:
        remote_probe = _probe_remote_dataset(bids_root, request)
        dataset_flags = _detect_dataset_flags_from_probe(bids_root, remote_probe)
        subjects = _discover_remote_subjects(bids_root, request, remote_probe)
    else:
        layout = _layout_for_root(bids_root)
        dataset_flags = _detect_dataset_flags(bids_root, request.remote_host)
        subjects = discover_subjects(
            bids_root,
            requested_subjects=request.subjects,
            requested_sessions=request.sessions,
            layout=layout,
        )
    xcpd_datasets = _audit_xcpd_extra_datasets(request) if request.target == "xcpd" else {}
    xcpd_bids_filter_file = _audit_xcpd_bids_filter_file(request) if request.target == "xcpd" else {}
    xcpd_dataset_blockers = _xcpd_dataset_blockers(xcpd_datasets)
    xcpd_filter_blockers = list(xcpd_bids_filter_file.get("reason_codes") or [])
    audited_sessions: list[dict[str, Any]] = []
    warnings: list[str] = []
    warning_details: list[str] = []
    collect_xcpd_min_time_warnings = request.target == "xcpd"
    for subject in subjects:
        audited = _audit_subject(
            subject,
            request,
            dataset_flags,
            remote_probe,
            layout=None if request.remote_host else layout,
        )
        fmriprep_reason_codes = list((audited.get("fmriprep") or {}).get("reason_codes") or [])
        if any(
            code in fmriprep_reason_codes for code in {"dataset_not_materialized", "annex_content_missing"}
        ):
            instruction = _manual_materialization_instruction(subject, request, dataset_flags)
            if instruction is not None:
                warnings.append(instruction["reason_code"])
                warning_details.append(instruction["warning_detail"])
                audited["fmriprep"]["reason_codes"] = _dedupe(
                    fmriprep_reason_codes + [instruction["reason_code"]]
                )
        xcpd_min_time_warning_details = list(audited.pop("xcpd_min_time_warning_details", []))
        xcpd_unestimated_details = list(audited.pop("xcpd_post_censor_time_unestimated_details", []))
        xcpd_warning_codes = list(audited.pop("xcpd_warning_codes", []))
        xcpd_warning_details = list(audited.pop("xcpd_warning_details", []))
        if collect_xcpd_min_time_warnings:
            warnings.extend(xcpd_warning_codes)
            warning_details.extend(xcpd_warning_details)
            warning_details.extend(xcpd_min_time_warning_details)
            if xcpd_unestimated_details:
                warnings.append(XCPD_POST_CENSOR_TIME_UNESTIMATED_WARNING)
                warning_details.extend(xcpd_unestimated_details)
        xcpd_request_blockers = xcpd_dataset_blockers + xcpd_filter_blockers
        if xcpd_request_blockers:
            _add_xcpd_reason_codes(audited, xcpd_request_blockers)
        audited_sessions.append(audited)

    grouped_sessions = _group_audited_sessions(audited_sessions)
    storage_subjects = [_build_storage_subject(audits) for _, audits in grouped_sessions]
    artifact_subjects = [_build_artifact_subject(subject) for subject in storage_subjects]

    payload = {
        "dataset_type": _classify_dataset_type(dataset_flags),
        "dataset_flags": dataset_flags,
        "bids_root": str(bids_root),
        "subjects": artifact_subjects,
    }
    if request.target == "xcpd":
        payload["xcpd_datasets"] = xcpd_datasets
        if xcpd_bids_filter_file:
            payload["xcpd_bids_filter_file"] = xcpd_bids_filter_file
    advice = _collect_output_space_advice(storage_subjects)
    if advice:
        payload["advice"] = advice
    existing_derivatives = _review_existing_derivatives(request, storage_subjects)
    payload["existing_derivatives"] = existing_derivatives
    storage_check, storage_warnings = run_storage_check_with_warnings(request, storage_subjects)
    if collect_xcpd_min_time_warnings and any("remains after FD censoring" in detail for detail in warning_details):
        warnings.append("xcpd_min_time_not_met")
    payload["warnings"] = _dedupe(
        warnings
        + storage_warnings
        + _existing_derivatives_warning_codes(existing_derivatives, target=request.target)
    )
    payload["warning_details"] = _dedupe(warning_details)
    payload["storage_check"] = storage_check
    _emit_progress(
        progress,
        stage="dataset-audit",
        status="finished",
        message=f"Dataset audit finished for {len(artifact_subjects)} subject(s)",
        path=str(bids_root),
        remote_host=request.remote_host,
    )
    return payload


def audit_xcpd_derivatives(
    request: RequestConfig,
    subjects: list[SubjectEntry],
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Audit fMRIPrep derivatives as XCP-D inputs without rechecking raw BIDS."""
    output_root = request.resolve_output_root()
    fmriprep_root = request.resolve_fmriprep_derivatives_root()
    _emit_progress(
        progress,
        stage="xcpd-dataset-audit",
        status="started",
        message=f"Inspecting fMRIPrep derivatives at {fmriprep_root}",
        path=str(fmriprep_root),
        remote_host=request.remote_host,
    )
    if request.remote_host:
        validate_remote_request_paths(request)
    remote_probe = _probe_remote_xcpd_derivatives(output_root, fmriprep_root, request, subjects) if request.remote_host else None
    xcpd_datasets = _audit_xcpd_extra_datasets(request)
    xcpd_bids_filter_file = _audit_xcpd_bids_filter_file(request)
    xcpd_dataset_blockers = _xcpd_dataset_blockers(xcpd_datasets)
    xcpd_filter_blockers = list(xcpd_bids_filter_file.get("reason_codes") or [])
    audited_sessions: list[dict[str, Any]] = []
    xcpd_warning_codes: list[str] = []
    warning_details: list[str] = []
    unestimated_warning_details: list[str] = []
    for subject in subjects:
        audited = _audit_xcpd_derivative_subject(subject, request, remote_probe)
        xcpd_warning_codes.extend(list(audited.pop("xcpd_warning_codes", [])))
        warning_details.extend(list(audited.pop("xcpd_warning_details", [])))
        warning_details.extend(list(audited.pop("xcpd_min_time_warning_details", [])))
        unestimated_details = list(audited.pop("xcpd_post_censor_time_unestimated_details", []))
        unestimated_warning_details.extend(unestimated_details)
        xcpd_request_blockers = xcpd_dataset_blockers + xcpd_filter_blockers
        if xcpd_request_blockers:
            _add_xcpd_reason_codes(audited, xcpd_request_blockers)
        audited_sessions.append(audited)

    grouped_sessions = _group_audited_sessions(audited_sessions)
    storage_subjects = [_build_storage_subject(audits) for _, audits in grouped_sessions]
    artifact_subjects = [_build_artifact_subject(subject) for subject in storage_subjects]
    existing_derivatives = _review_existing_derivatives(request, storage_subjects)
    storage_check, storage_warnings = run_storage_check_with_warnings(request, storage_subjects)
    warnings = xcpd_warning_codes + list(storage_warnings) + _existing_derivatives_warning_codes(existing_derivatives, target=request.target)
    if unestimated_warning_details:
        warnings.append(XCPD_POST_CENSOR_TIME_UNESTIMATED_WARNING)
        warning_details.extend(unestimated_warning_details)
    if request.bids_root is None:
        warnings.append(XCPD_BIDS_ROOT_NOT_PROVIDED_WARNING)
    if request.xcpd_min_time > 0 and any("remains after FD censoring" in detail for detail in warning_details):
        warnings.append("xcpd_min_time_not_met")
    payload = {
        "dataset_type": "fmriprep_derivatives",
        "dataset_flags": ["fmriprep_derivatives"],
        "bids_root": str(request.bids_root) if request.bids_root is not None else None,
        "fmriprep_derivatives": str(fmriprep_root),
        "subjects": artifact_subjects,
        "xcpd_datasets": xcpd_datasets,
        "existing_derivatives": existing_derivatives,
        "warnings": _dedupe(warnings),
        "warning_details": _dedupe(warning_details),
        "storage_check": storage_check,
    }
    if xcpd_bids_filter_file:
        payload["xcpd_bids_filter_file"] = xcpd_bids_filter_file
    _emit_progress(
        progress,
        stage="xcpd-dataset-audit",
        status="finished",
        message=f"XCP-D derivatives audit finished for {len(artifact_subjects)} subject(s)",
        path=str(fmriprep_root),
        remote_host=request.remote_host,
    )
    return payload


def discover_xcpd_derivative_subjects(request: RequestConfig) -> list[SubjectEntry]:
    """Discover XCP-D subject scope from the fMRIPrep derivatives tree."""
    subject_ids = [_normalize_subject_id(value) for value in request.subjects]
    if not subject_ids:
        fmriprep_root = request.resolve_fmriprep_derivatives_root()
        if request.remote_host:
            probe = probe_remote_dataset(
                request.remote_host,
                glob_patterns=[str(fmriprep_root / "sub-*")],
                include_image_metadata=False,
            )
            subject_ids = sorted(
                _normalize_subject_id(path.name)
                for path in _remote_glob_values(probe)
                if path.name.startswith("sub-") and _probe_path_info(probe, path).get("is_dir") is True
            )
        else:
            subject_ids = sorted(
                _normalize_subject_id(path.name)
                for path in glob_paths(str(fmriprep_root / "sub-*"))
                if path.is_dir() and path.name.startswith("sub-")
            )
    session_ids = [_normalize_session_id(value) for value in request.sessions]
    if session_ids:
        return [
            SubjectEntry(subject_id=subject_id, session_id=session_id)
            for subject_id in _dedupe(subject_ids)
            for session_id in session_ids
            if session_id is not None
        ]
    return [SubjectEntry(subject_id=subject_id) for subject_id in _dedupe(subject_ids)]


def _audit_xcpd_bids_filter_file(request: RequestConfig) -> dict[str, Any]:
    path = request.xcpd_bids_filter_file
    if path is None:
        return {}
    reason_codes: list[str] = []
    if not _path_is_file(path, request.remote_host):
        reason_codes.append("missing_xcpd_bids_filter_file")
    elif _json_file_payload(path, request.remote_host) is None:
        reason_codes.append("invalid_xcpd_bids_filter_file")
    return {
        "path": str(path),
        "status": "blocked" if reason_codes else "ready",
        "reason_codes": _dedupe(reason_codes),
    }


def _audit_xcpd_extra_datasets(request: RequestConfig) -> dict[str, dict[str, Any]]:
    audited: dict[str, dict[str, Any]] = {}
    for alias, path in sorted(request.xcpd_datasets.items()):
        reason_codes: list[str] = []
        warnings: list[str] = []
        if not _valid_xcpd_dataset_alias(alias):
            reason_codes.append("invalid_xcpd_dataset_alias")
        if not _path_is_dir(path, request.remote_host):
            reason_codes.append("missing_xcpd_dataset")
        description_path = path / "dataset_description.json"
        dataset_type = None
        if not path_exists(description_path, request.remote_host):
            reason_codes.append("missing_xcpd_dataset_description")
        else:
            dataset_type = _xcpd_dataset_type(description_path, request.remote_host)
            if dataset_type == "atlas":
                warnings.append("xcpd_dataset_type_atlas")
            elif dataset_type != "derivative":
                reason_codes.append("invalid_xcpd_dataset_type")
        audited[alias] = {
            "path": str(path),
            "status": "blocked" if reason_codes else "ready",
            "reason_codes": _dedupe(reason_codes),
            "warnings": _dedupe(warnings),
        }
        if dataset_type is not None:
            audited[alias]["dataset_type"] = dataset_type
    return audited


def _valid_xcpd_dataset_alias(value: str) -> bool:
    return bool(value) and all(char in XCPD_DATASET_ALIAS_PATTERN for char in value)


def _path_is_dir(path: RequestPath, remote_host: str | None) -> bool:
    if remote_host is None:
        return isinstance(path, Path) and path.is_dir()
    result = run_command(shell_command(f"test -d {shlex.quote(str(path))}"), remote_host=remote_host, check=False, timeout=20)
    return result.returncode == 0


def _path_is_file(path: RequestPath, remote_host: str | None) -> bool:
    if remote_host is None:
        return isinstance(path, Path) and path.is_file()
    result = run_command(shell_command(f"test -f {shlex.quote(str(path))}"), remote_host=remote_host, check=False, timeout=20)
    return result.returncode == 0


def _xcpd_dataset_type(path: RequestPath, remote_host: str | None) -> str | None:
    payload = _json_file_payload(path, remote_host)
    value = payload.get("DatasetType") if isinstance(payload, dict) else None
    return str(value).strip().lower() if value is not None else None


def _json_file_payload(path: RequestPath, remote_host: str | None) -> Any:
    try:
        return json.loads(read_text(path, remote_host))
    except (OSError, json.JSONDecodeError):
        return None


def _xcpd_dataset_blockers(audited: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for dataset in audited.values():
        blockers.extend(str(code) for code in dataset.get("reason_codes") or [])
    return _dedupe(blockers)


def _add_xcpd_reason_codes(audited: dict[str, Any], reason_codes: list[str]) -> None:
    xcpd = audited.get("xcpd")
    if not isinstance(xcpd, dict):
        return
    xcpd["status"] = "blocked"
    xcpd["reason_codes"] = _dedupe(list(xcpd.get("reason_codes") or []) + reason_codes)


def _emit_progress(progress: ProgressCallback | None, **event: Any) -> None:
    if progress is not None:
        progress(event)


def discover_subjects(
    bids_root: Path,
    requested_subjects: list[str] | None = None,
    requested_sessions: list[str] | None = None,
    layout: BIDSLayout | None = None,
) -> list[SubjectEntry]:
    """Inputs:
        bids_root (Path): BIDS dataset root being inspected.
        requested_subjects (list[str] | None): Subject filter from the request, if any.
        requested_sessions (list[str] | None): Session filter from the request, if any.

    Returns:
        list[SubjectEntry]: Discovered subject entries for the request.
    """
    requested_session_ids = [
        session_id
        for value in (requested_sessions or [])
        if (session_id := _normalize_session_id(value)) is not None
    ]
    layout_subject_ids = set(_layout_subject_ids(layout))
    if requested_subjects:
        subjects = [_normalize_subject_id(value) for value in requested_subjects]
    else:
        subjects = sorted(layout_subject_ids)
        if not subjects:
            subjects = sorted(
                _normalize_subject_id(path.name)
                for path in bids_root.iterdir()
                if path.is_dir() and path.name.startswith("sub-")
            )
    discovered: list[SubjectEntry] = []
    for subject_id in subjects:
        subject_root = bids_root / f"sub-{subject_id}"
        available_sessions = _layout_session_ids(layout, subject_id)
        if not available_sessions and subject_root.exists():
            available_sessions = sorted(
                session_id
                for path in subject_root.iterdir()
                if path.is_dir() and path.name.startswith("ses-")
                if (session_id := _normalize_session_id(path.name)) is not None
            )
        discovered.extend(
            _resolve_subject_entries(
                subject_id=subject_id,
                requested_session_ids=requested_session_ids,
                available_session_ids=available_sessions,
                subject_exists=(subject_id in layout_subject_ids) or subject_root.exists(),
            )
        )
    return discovered


def _probe_remote_dataset(bids_root: Path, request: RequestConfig) -> dict[str, Any]:
    """Collect the remote dataset snapshot used by dataset audit."""
    probe, _ = _probe_remote_dataset_with_metrics(bids_root, request)
    return probe


def _probe_remote_dataset_with_metrics(
    bids_root: Path,
    request: RequestConfig,
    image_metadata_backend: str = "stdlib",
) -> tuple[dict[str, Any], int]:
    """Collect the two-stage remote dataset snapshot plus payload bytes."""
    output_root = request.resolve_output_root()
    first_stage, first_stage_bytes = probe_remote_dataset_with_metrics(
        request.remote_host or "",
        paths=_remote_probe_base_paths(bids_root, output_root, target=request.target),
        glob_patterns=_remote_discovery_patterns(bids_root, request),
        text_paths=[str(bids_root / ".git" / "config")],
        image_metadata_backend=image_metadata_backend,
    )
    subjects = _discover_remote_subjects(bids_root, request, first_stage)
    second_stage_patterns = _remote_subject_probe_patterns(bids_root, output_root, subjects, target=request.target)
    if not second_stage_patterns:
        return first_stage, first_stage_bytes
    second_stage, second_stage_bytes = probe_remote_dataset_with_metrics(
        request.remote_host or "",
        glob_patterns=second_stage_patterns,
        image_metadata_backend=image_metadata_backend,
    )
    return _merge_remote_probes(first_stage, second_stage), first_stage_bytes + second_stage_bytes


def _probe_remote_xcpd_derivatives(
    output_root: RequestPath,
    fmriprep_root: RequestPath,
    request: RequestConfig,
    subjects: list[SubjectEntry],
) -> dict[str, Any]:
    probe, _ = probe_remote_dataset_with_metrics(
        request.remote_host or "",
        paths=[
            str(fmriprep_root / "dataset_description.json"),
            str(_xcpd_output_root(output_root) / "dataset_description.json"),
        ],
        glob_patterns=_remote_xcpd_derivative_patterns(output_root, fmriprep_root, subjects),
        image_metadata_backend="stdlib",
    )
    return probe


def _remote_xcpd_derivative_patterns(
    output_root: RequestPath,
    fmriprep_root: RequestPath,
    subjects: list[SubjectEntry],
) -> list[str]:
    patterns: list[str] = []
    for subject in subjects:
        derivative_root = fmriprep_root / subject.subject_label
        if subject.session_label:
            derivative_root = derivative_root / subject.session_label
        patterns.extend(
            [
                str(derivative_root / "**" / "*_bold.dtseries.nii"),
                str(derivative_root / "**" / "*desc-preproc_bold.nii*"),
                str(derivative_root / "**" / "*space-MNI152NLin*_desc-preproc_bold.nii*"),
                str(derivative_root / "**" / "*space-MNI152NLin*_boldref.nii*"),
                str(derivative_root / "**" / "*space-MNI152NLin*_desc-brain_mask.nii*"),
                str(derivative_root / "**" / "*desc-confounds_timeseries.tsv"),
                str(derivative_root / "**" / "*desc-confounds_timeseries.json"),
            ]
        )
        xcpd_root = _xcpd_output_root(output_root) / subject.subject_label
        if subject.session_label:
            xcpd_root = xcpd_root / subject.session_label
        patterns.append(str(xcpd_root / "**" / "*desc-denoised*_bold*"))
    return _dedupe(patterns)


def _remote_probe_base_paths(bids_root: Path, output_root: Path, *, target: str) -> list[str]:
    paths = [
        str(bids_root / ".datalad"),
        str(bids_root / ".git" / "annex"),
        str(bids_root / ".git" / "config"),
        str(bids_root / "dataset_description.json"),
        str(output_root / "fmriprep" / "dataset_description.json"),
    ]
    if target == "xcpd":
        paths.append(str(_xcpd_output_root(output_root) / "dataset_description.json"))
    return paths


def _remote_discovery_patterns(bids_root: Path, request: RequestConfig) -> list[str]:
    subject_labels = [f"sub-{_normalize_subject_id(value)}" for value in request.subjects]
    session_labels = [
        f"ses-{session_id}"
        for value in request.sessions
        if (session_id := _normalize_session_id(value)) is not None
    ]
    patterns: list[str] = []
    if subject_labels:
        patterns.extend(str(bids_root / subject_label) for subject_label in subject_labels)
        patterns.extend(str(bids_root / subject_label / "ses-*") for subject_label in subject_labels)
        patterns.extend(
            str(bids_root / subject_label / session_label)
            for subject_label in subject_labels
            for session_label in session_labels
        )
        return _dedupe(patterns)
    patterns.append(str(bids_root / "sub-*"))
    patterns.append(str(bids_root / "sub-*" / "ses-*"))
    patterns.extend(str(bids_root / "sub-*" / session_label) for session_label in session_labels)
    return _dedupe(patterns)


def _remote_subject_probe_patterns(
    bids_root: Path,
    output_root: Path,
    subjects: list[SubjectEntry],
    *,
    target: str,
) -> list[str]:
    patterns: list[str] = []
    for subject in subjects:
        subject_root = bids_root / subject.subject_label
        derivative_root = output_root / "fmriprep" / subject.subject_label
        if subject.session_label:
            subject_root = subject_root / subject.session_label
            derivative_root = derivative_root / subject.session_label
        patterns.append(str(subject_root / "anat" / "*_T1w.nii*"))
        if subject.session_label:
            patterns.extend(
                [
                    str(bids_root / subject.subject_label / "anat" / "*_T1w.nii*"),
                    str(bids_root / subject.subject_label / "ses-*" / "anat" / "*_T1w.nii*"),
                ]
            )
        patterns.extend(
            [
                str(subject_root / "func" / "*_bold.nii*"),
                str(derivative_root / "**" / "*desc-preproc_bold.nii*"),
            ]
        )
        if target == "xcpd":
            patterns.extend(
                [
                    str(derivative_root / "**" / "*_bold.dtseries.nii"),
                    str(derivative_root / "**" / "*space-MNI152NLin*_desc-preproc_bold.nii*"),
                    str(derivative_root / "**" / "*space-MNI152NLin*_boldref.nii*"),
                    str(derivative_root / "**" / "*space-MNI152NLin*_desc-brain_mask.nii*"),
                    str(derivative_root / "**" / "*desc-confounds_timeseries.tsv"),
                    str(derivative_root / "**" / "*desc-confounds_timeseries.json"),
                ]
            )
            xcpd_root = _xcpd_output_root(output_root) / subject.subject_label
            if subject.session_label:
                xcpd_root = xcpd_root / subject.session_label
            patterns.append(str(xcpd_root / "**" / "*desc-denoised*_bold*"))
    return _dedupe(patterns)


def _merge_remote_probes(*probes: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"paths": {}, "globs": {}, "texts": {}}
    for probe in probes:
        merged["paths"].update(probe.get("paths", {}))
        merged["texts"].update(probe.get("texts", {}))
        for pattern, matches in probe.get("globs", {}).items():
            merged_matches = merged["globs"].setdefault(pattern, [])
            for match in matches:
                if match not in merged_matches:
                    merged_matches.append(match)
    return merged

def _detect_dataset_flags(bids_root: Path, remote_host: str | None = None) -> list[str]:
    """Detect dataset flags from local filesystem state.

    Inputs:
        bids_root (Path): BIDS dataset root being inspected.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        list[str]: List containing the computed values.
    """
    flags: list[str] = []
    if path_exists(bids_root / ".datalad", remote_host):
        flags.append("datalad")
    if path_exists(bids_root / ".git" / "annex", remote_host):
        flags.append("git-annex")
    git_config = bids_root / ".git" / "config"
    if path_exists(git_config, remote_host):
        content = read_text(git_config, remote_host=remote_host).lower()
        if "openneuro" in content:
            flags.append("openneuro")
    if path_exists(bids_root / "dataset_description.json", remote_host):
        flags.append("bids")
    return _dedupe(flags)


def _detect_dataset_flags_from_probe(bids_root: Path, probe: dict[str, Any]) -> list[str]:
    """Detect dataset flags from a remote probe payload.

    Inputs:
        bids_root (Path): BIDS dataset root being inspected.
        probe (dict[str, Any]): Remote dataset probe payload.

    Returns:
        list[str]: List containing the computed values.
    """
    flags: list[str] = []
    if _probe_path_exists(probe, bids_root / ".datalad"):
        flags.append("datalad")
    if _probe_path_exists(probe, bids_root / ".git" / "annex"):
        flags.append("git-annex")
    git_config = bids_root / ".git" / "config"
    if _probe_path_exists(probe, git_config):
        content = _probe_text(probe, git_config).lower()
        if "openneuro" in content:
            flags.append("openneuro")
    if _probe_path_exists(probe, bids_root / "dataset_description.json"):
        flags.append("bids")
    return _dedupe(flags)


def _classify_dataset_type(flags: list[str]) -> str:
    """Classify the dataset from the detected flags.

    Inputs:
        flags (list[str]): Detected dataset feature flags.

    Returns:
        str: Normalized string value.
    """
    if "openneuro" in flags:
        return "openneuro"
    if "datalad" in flags:
        return "datalad"
    if "git-annex" in flags:
        return "git-annex"
    return "bids"


def _discover_remote_subjects(bids_root: Path, request: RequestConfig, probe: dict[str, Any]) -> list[SubjectEntry]:
    """Build remote subject entries from the probe output."""
    sessions_by_subject: dict[str, list[str]] = {}
    for match_path in _remote_session_dirs(probe, bids_root):
        subject_id = _normalize_subject_id(match_path.parent.name)
        session_id = _normalize_session_id(match_path.name)
        if session_id is None:
            continue
        sessions = sessions_by_subject.setdefault(subject_id, [])
        if session_id not in sessions:
            sessions.append(session_id)

    subject_ids = [_normalize_subject_id(value) for value in request.subjects] if request.subjects else []
    discovered_subject_ids = [_normalize_subject_id(path.name) for path in _remote_subject_dirs(probe, bids_root)]
    if not subject_ids:
        subject_ids = discovered_subject_ids
    discovered: list[SubjectEntry] = []
    requested_session_ids = [
        session_id
        for value in request.sessions
        if (session_id := _normalize_session_id(value)) is not None
    ]
    known_subject_ids = set(discovered_subject_ids)
    for subject_id in subject_ids:
        discovered.extend(
            _resolve_subject_entries(
                subject_id=subject_id,
                requested_session_ids=requested_session_ids,
                available_session_ids=sessions_by_subject.get(subject_id, []),
                subject_exists=subject_id in known_subject_ids,
            )
        )
    return discovered


def _resolve_subject_entries(
    *,
    subject_id: str,
    requested_session_ids: list[str],
    available_session_ids: list[str],
    subject_exists: bool,
) -> list[SubjectEntry]:
    if requested_session_ids:
        if not available_session_ids:
            if subject_exists:
                raise ValueError(
                    f"session filter requires sessionized subject sub-{subject_id}; no session directories found."
                )
            return []
        return [
            SubjectEntry(subject_id=subject_id, session_id=session_id)
            for session_id in requested_session_ids
            if session_id in available_session_ids
        ]
    if available_session_ids:
        return [SubjectEntry(subject_id=subject_id, session_id=session_id) for session_id in available_session_ids]
    return [SubjectEntry(subject_id=subject_id)]

def _audit_subject(
    subject: SubjectEntry,
    request: RequestConfig,
    dataset_flags: list[str],
    remote_probe: dict[str, Any] | None = None,
    layout: BIDSLayout | None = None,
) -> dict[str, Any]:
    """Audit one subject and summarize its readiness."""
    bids_root = request.resolve_bids_root()
    subject_dir = bids_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label

    reason_codes: list[str] = []
    anat_dir = subject_dir / "anat"
    func_dir = subject_dir / "func"
    shared_anat_dir = bids_root / subject.subject_label / "anat"
    all_session_anat_pattern = str(bids_root / subject.subject_label / "ses-*" / "anat" / "*_T1w.nii*")
    if remote_probe is None and not path_exists(subject_dir, request.remote_host):
        reason_codes.append("missing_subject_dir")
        payload = {
            "subject_id": subject.subject_id,
            "session_id": subject.session_id,
            "fmriprep": {
                "status": "blocked",
                "reason_codes": list(reason_codes),
                "inputs_materialized": False,
                "has_derivatives": False,
            },
        }
        if request.target == "xcpd":
            payload["xcpd"] = {
                "status": "blocked",
                "reason_codes": ["missing_fmriprep_derivatives"],
                "has_fmriprep_derivatives": False,
                "has_xcpd_derivatives": False,
            }
        return payload

    session_t1_candidates: list[Path] = []
    shared_t1_candidates: list[Path] = []
    all_session_t1_candidates: list[Path] = []
    if remote_probe is None:
        session_t1_candidates = _layout_subject_candidates(layout, subject, datatype="anat", suffix="T1w")
        if not session_t1_candidates:
            session_t1_candidates = sorted(glob_paths(str(anat_dir / "*_T1w.nii*"), request.remote_host))
        if subject.session_label:
            shared_t1_candidates = sorted(glob_paths(str(shared_anat_dir / "*_T1w.nii*"), request.remote_host))
            all_session_t1_candidates = sorted(glob_paths(all_session_anat_pattern, request.remote_host))
        bold_candidates = _layout_subject_candidates(layout, subject, datatype="func", suffix="bold")
        if not bold_candidates:
            bold_candidates = sorted(glob_paths(str(func_dir / "*_bold.nii*"), request.remote_host))
    else:
        session_t1_candidates = _remote_subject_candidates(
            remote_probe,
            _remote_subject_input_patterns(bids_root, subject, datatype="anat", suffix="T1w"),
        )
        if subject.session_label:
            shared_t1_candidates = _remote_subject_candidates(
                remote_probe,
                [str(shared_anat_dir / "*_T1w.nii*")],
            )
            all_session_t1_candidates = sorted(
                path
                for path in _remote_glob_values(remote_probe)
                if _path_is_within(path, bids_root / subject.subject_label)
                and path.parent.name == "anat"
                and path.parent.parent.name.startswith("ses-")
                and "_T1w." in path.name
            )
        bold_candidates = _remote_subject_candidates(
            remote_probe,
            _remote_subject_input_patterns(bids_root, subject, datatype="func", suffix="bold"),
        )
    unfiltered_bold_candidates = list(bold_candidates)
    bold_candidates = _filter_bold_candidates(bold_candidates, request)

    if remote_probe is None:
        session_t1_status = _check_candidates(session_t1_candidates, dataset_flags, request.remote_host)
        shared_t1_status = _check_candidates(shared_t1_candidates, dataset_flags, request.remote_host)
        all_session_t1_status = _check_candidates(all_session_t1_candidates, dataset_flags, request.remote_host)
        bold_status = _check_candidates(bold_candidates, dataset_flags, request.remote_host)
    else:
        session_t1_status = _check_candidates_from_probe(session_t1_candidates, dataset_flags, remote_probe)
        shared_t1_status = _check_candidates_from_probe(shared_t1_candidates, dataset_flags, remote_probe)
        all_session_t1_status = _check_candidates_from_probe(all_session_t1_candidates, dataset_flags, remote_probe)
        bold_status = _check_candidates_from_probe(bold_candidates, dataset_flags, remote_probe)

    t1_candidates = session_t1_candidates
    t1_status = session_t1_status
    if subject.session_label and not session_t1_status["materialized"] and shared_t1_status["materialized"]:
        t1_candidates = shared_t1_candidates
        t1_status = shared_t1_status
    if (
        subject.session_label
        and not t1_status["materialized"]
        and not shared_t1_status["materialized"]
        and len(all_session_t1_candidates) == 1
        and all_session_t1_status["materialized"]
    ):
        t1_candidates = all_session_t1_candidates
        t1_status = all_session_t1_status

    if not t1_candidates:
        reason_codes.append("missing_t1w")
    if not bold_candidates and not request.anat_only:
        if request.task_id and _filter_bold_candidates(unfiltered_bold_candidates, replace(request, task_id=None)):
            reason_codes.append("missing_requested_task")
        elif request.echo_idx is not None and _filter_bold_candidates(unfiltered_bold_candidates, replace(request, echo_idx=None)):
            reason_codes.append("missing_requested_echo")
        else:
            reason_codes.append("missing_bold")

    if t1_candidates and not t1_status["materialized"]:
        reason_codes.extend(t1_status["reason_codes"])
    if bold_candidates and not bold_status["materialized"]:
        reason_codes.extend(bold_status["reason_codes"])

    input_size_bytes = _collect_input_size_bytes(t1_candidates, bold_candidates, remote_probe)
    input_image_metadata = _collect_input_image_metadata(t1_candidates, bold_candidates, remote_probe)
    t1_metadata_required = _image_metadata_required(t1_candidates, remote_probe)
    bold_metadata_required = _image_metadata_required(bold_candidates, remote_probe)
    if t1_candidates and t1_status["materialized"] and t1_metadata_required and not input_image_metadata["t1w"]:
        reason_codes.append("invalid_t1w_image")
    if bold_candidates and bold_status["materialized"] and bold_metadata_required and not input_image_metadata["bold"]:
        reason_codes.append("invalid_bold_image")

    if remote_probe is None:
        fmriprep_root = request.resolve_fmriprep_derivatives_root()
        has_fmriprep_derivatives = _has_fmriprep_derivatives(subject, fmriprep_root, request.remote_host)
        has_xcpd_derivatives = (
            _has_xcpd_derivatives(subject, request.resolve_output_root(), request.remote_host)
            if request.target == "xcpd"
            else False
        )
    else:
        fmriprep_root = request.resolve_fmriprep_derivatives_root()
        has_fmriprep_derivatives = _has_fmriprep_derivatives_from_probe(subject, fmriprep_root, remote_probe)
        has_xcpd_derivatives = (
            _has_xcpd_derivatives_from_probe(subject, request.resolve_output_root(), remote_probe)
            if request.target == "xcpd"
            else False
        )
    bold_ready = request.anat_only or (
        bool(bold_candidates)
        and bold_status["materialized"]
        and (not bold_metadata_required or bool(input_image_metadata["bold"]))
    )
    fmriprep_ready = (
        bool(t1_candidates)
        and t1_status["materialized"]
        and bold_ready
        and (not t1_metadata_required or bool(input_image_metadata["t1w"]))
    )
    fmriprep_reason_codes = _dedupe(reason_codes)
    xcpd_status = (
        _xcpd_input_status(
            subject,
            request.resolve_fmriprep_derivatives_root(),
            xcpd_mode=request.xcpd_mode,
            xcpd_task_ids=request.xcpd_task_ids,
            remote_host=request.remote_host,
            probe=remote_probe,
        )
        if request.target == "xcpd"
        else {"ready": False, "reason_codes": []}
    )
    payload = {
        "subject_id": subject.subject_id,
        "session_id": subject.session_id,
        "fmriprep": {
            "status": "ready" if fmriprep_ready else "blocked",
            "reason_codes": fmriprep_reason_codes,
            "inputs_materialized": t1_status["materialized"] and (request.anat_only or bold_status["materialized"]),
            "has_derivatives": has_fmriprep_derivatives,
        },
        "subject_dir": str(subject_dir),
        "input_paths": {
            "t1w": [str(path) for path in t1_candidates],
            "bold": [str(path) for path in bold_candidates],
        },
        "input_image_metadata": input_image_metadata,
        "input_size_bytes": input_size_bytes,
        "input_total_size_bytes": sum(input_size_bytes["t1w"]) + sum(input_size_bytes["bold"]),
    }
    if request.target == "xcpd":
        payload["xcpd"] = {
            "status": "ready" if xcpd_status["ready"] else "blocked",
            "reason_codes": list(xcpd_status["reason_codes"]),
            "has_fmriprep_derivatives": has_fmriprep_derivatives,
            "has_xcpd_derivatives": has_xcpd_derivatives,
            "input_mode": request.xcpd_mode,
        }
        if xcpd_status.get("input_format"):
            payload["xcpd"]["input_format"] = xcpd_status["input_format"]
        payload["xcpd_warning_codes"] = list(xcpd_status.get("warning_codes") or [])
        payload["xcpd_warning_details"] = _xcpd_warning_details(subject, xcpd_status)
        payload["xcpd_min_time_warning_details"] = _collect_xcpd_min_time_warning_details(
            subject,
            bold_candidates,
            request,
            remote_probe,
        )
        payload["xcpd_post_censor_time_unestimated_details"] = _collect_xcpd_post_censor_time_unestimated_details(
            subject,
            bold_candidates,
            request,
            remote_probe,
        )
    return payload


def _audit_xcpd_derivative_subject(
    subject: SubjectEntry,
    request: RequestConfig,
    remote_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_root = request.resolve_output_root()
    fmriprep_root = request.resolve_fmriprep_derivatives_root()
    subject_dir = fmriprep_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label
    if remote_probe is None:
        has_fmriprep_derivatives = _has_fmriprep_derivatives(subject, fmriprep_root, request.remote_host)
        has_xcpd_derivatives = _has_xcpd_derivatives(subject, output_root, request.remote_host)
        derivative_paths = _xcpd_subject_derivative_paths(subject_dir)
    else:
        has_fmriprep_derivatives = _has_fmriprep_derivatives_from_probe(subject, fmriprep_root, remote_probe)
        has_xcpd_derivatives = _has_xcpd_derivatives_from_probe(subject, output_root, remote_probe)
        derivative_paths = [path for path in _remote_glob_values(remote_probe) if _path_is_within(path, subject_dir)]
    xcpd_status = _xcpd_input_status(
        subject,
        fmriprep_root,
        xcpd_mode=request.xcpd_mode,
        xcpd_task_ids=request.xcpd_task_ids,
        remote_host=request.remote_host,
        probe=remote_probe,
    )
    bold_derivatives = [
        path
        for path in derivative_paths
        if _is_mni_preproc_bold(path) or _is_fslr_dtseries(path)
    ]
    bold_sizes = _collect_candidate_sizes(bold_derivatives, remote_probe)
    payload = {
        "subject_id": subject.subject_id,
        "session_id": subject.session_id,
        "fmriprep": {
            "status": "ready" if has_fmriprep_derivatives else "blocked",
            "reason_codes": [] if has_fmriprep_derivatives else ["missing_fmriprep_derivatives"],
            "inputs_materialized": True,
            "has_derivatives": has_fmriprep_derivatives,
        },
        "xcpd": {
            "status": "ready" if xcpd_status["ready"] else "blocked",
            "reason_codes": list(xcpd_status["reason_codes"]),
            "has_fmriprep_derivatives": has_fmriprep_derivatives,
            "has_xcpd_derivatives": has_xcpd_derivatives,
            "input_mode": request.xcpd_mode,
        },
        "subject_dir": str(subject_dir),
        "input_paths": {
            "t1w": [],
            "bold": [str(path) for path in bold_derivatives],
        },
        "input_image_metadata": {
            "t1w": [],
            "bold": [_path_image_metadata(path, remote_probe) or {} for path in bold_derivatives],
        },
        "input_size_bytes": {
            "t1w": [],
            "bold": bold_sizes,
        },
        "input_total_size_bytes": sum(bold_sizes),
    }
    if xcpd_status.get("input_format"):
        payload["xcpd"]["input_format"] = xcpd_status["input_format"]
    payload["xcpd_warning_codes"] = list(xcpd_status.get("warning_codes") or [])
    payload["xcpd_warning_details"] = _xcpd_warning_details(subject, xcpd_status)
    payload["xcpd_min_time_warning_details"] = _collect_xcpd_min_time_warning_details(
        subject,
        bold_derivatives,
        request,
        remote_probe,
    )
    payload["xcpd_post_censor_time_unestimated_details"] = _collect_xcpd_post_censor_time_unestimated_details(
        subject,
        bold_derivatives,
        request,
        remote_probe,
    )
    return payload


def _collect_xcpd_min_time_warning_details(
    subject: SubjectEntry,
    bold_candidates: list[Path],
    request: RequestConfig,
    probe: dict[str, Any] | None = None,
) -> list[str]:
    min_time_seconds = request.xcpd_min_time
    if min_time_seconds <= 0:
        return []
    details: list[str] = []
    for path in bold_candidates:
        estimate = _xcpd_post_censor_duration_estimate(path, request, probe)
        if estimate["status"] != "estimated":
            continue
        duration_seconds = estimate["usable_seconds"]
        if duration_seconds is None or duration_seconds >= float(min_time_seconds):
            continue
        fd_thresh = estimate["fd_thresh"]
        tr_seconds = estimate["tr_seconds"]
        kept = estimate["kept_volumes"]
        total = estimate["total_volumes"]
        detail_prefix = subject.subject_label
        if subject.session_label:
            detail_prefix = f"{detail_prefix} {subject.session_label}"
        detail = (
            f"{detail_prefix}: estimated {duration_seconds:g}s remains after FD censoring for {path.name}, "
            f"below XCP-D min-time {min_time_seconds}s; fd_thresh={fd_thresh:g}, TR={tr_seconds:g}s, "
            f"kept={kept}/{total} volumes"
        )
        motion_filter = _motion_filter_detail(request)
        if motion_filter:
            detail = f"{detail}, {motion_filter}, estimate"
        details.append(f"{detail}.")
    return _dedupe(details)


def _collect_xcpd_post_censor_time_unestimated_details(
    subject: SubjectEntry,
    bold_candidates: list[Path],
    request: RequestConfig,
    probe: dict[str, Any] | None = None,
) -> list[str]:
    if request.xcpd_min_time <= 0:
        return []
    details: list[str] = []
    for path in bold_candidates:
        estimate = _xcpd_post_censor_duration_estimate(path, request, probe)
        if estimate["status"] == "estimated":
            continue
        detail_prefix = subject.subject_label
        if subject.session_label:
            detail_prefix = f"{detail_prefix} {subject.session_label}"
        details.append(f"{detail_prefix}: cannot estimate post-censor usable time for {path.name}: {estimate['reason']}.")
    return _dedupe(details)


def _xcpd_post_censor_duration_estimate(
    bold_path: Path,
    request: RequestConfig,
    probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = _path_image_metadata(bold_path, probe)
    raw_duration = _bold_duration_seconds(metadata)
    tr_seconds = _bold_tr_seconds(metadata)
    total_volumes = _bold_timepoints(metadata)
    if raw_duration is None or tr_seconds is None or total_volumes is None:
        return {"status": "unestimated", "reason": "missing TR or BOLD timepoints"}
    fd_thresh = _resolve_xcpd_fd_thresh(request)
    if fd_thresh is None:
        return {"status": "unestimated", "reason": "unknown fd_thresh"}
    if fd_thresh <= 0:
        return {
            "status": "estimated",
            "usable_seconds": raw_duration,
            "tr_seconds": tr_seconds,
            "fd_thresh": fd_thresh,
            "kept_volumes": total_volumes,
            "total_volumes": total_volumes,
        }
    confounds_path = _matching_confounds_tsv(bold_path, probe)
    if confounds_path is None:
        return {"status": "unestimated", "reason": "missing confounds TSV"}
    confounds = _read_confounds_tsv(confounds_path, request.remote_host, probe)
    if not confounds:
        return {"status": "unestimated", "reason": "missing confounds TSV"}
    fd_values: list[float | None]
    if request.xcpd_motion_filter_type and request.xcpd_motion_filter_type != "none":
        motion_values = _motion_columns(confounds)
        if motion_values is None:
            return {"status": "unestimated", "reason": "missing motion columns"}
        fd_values = _fd_from_motion(motion_values)
    else:
        fd_values = _confound_column(confounds, "framewise_displacement")
        if fd_values is None:
            return {"status": "unestimated", "reason": "missing framewise_displacement"}
    total = min(total_volumes, len(fd_values))
    if total <= 0:
        return {"status": "unestimated", "reason": "missing framewise_displacement"}
    kept = sum(1 for value in fd_values[:total] if value is not None and value <= fd_thresh)
    return {
        "status": "estimated",
        "usable_seconds": round(kept * tr_seconds, 2),
        "tr_seconds": tr_seconds,
        "fd_thresh": fd_thresh,
        "kept_volumes": kept,
        "total_volumes": total,
    }


def _bold_duration_seconds(metadata: dict[str, Any] | None) -> float | None:
    tr_seconds = _bold_tr_seconds(metadata)
    timepoint_count = _bold_timepoints(metadata)
    if tr_seconds is None or timepoint_count is None:
        return None
    return round(tr_seconds * timepoint_count, 2)


def _bold_tr_seconds(metadata: dict[str, Any] | None) -> float | None:
    if not isinstance(metadata, dict):
        return None
    repetition_time = metadata.get("repetition_time")
    try:
        tr_seconds = float(repetition_time)
    except (TypeError, ValueError):
        zooms = metadata.get("zooms")
        if not isinstance(zooms, list) or len(zooms) < 4:
            return None
        try:
            tr_seconds = float(zooms[3])
        except (TypeError, ValueError):
            return None
    if tr_seconds <= 0:
        return None
    return tr_seconds


def _bold_timepoints(metadata: dict[str, Any] | None) -> int | None:
    if not isinstance(metadata, dict):
        return None
    try:
        timepoint_count = int(metadata.get("timepoints"))
    except (TypeError, ValueError):
        return None
    return timepoint_count if timepoint_count > 0 else None


def _resolve_xcpd_fd_thresh(request: RequestConfig) -> float | None:
    raw = request.xcpd_custom_args.get("fd_thresh", DEFAULT_XCPD_FD_THRESH)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _matching_confounds_tsv(bold_path: Path, probe: dict[str, Any] | None = None) -> RequestPath | None:
    candidates: list[RequestPath]
    if probe is None:
        candidates = sorted(bold_path.parent.glob("*desc-confounds_timeseries.tsv"))
    else:
        candidates = [path for path in _remote_glob_values(probe) if path.parent == bold_path.parent and _is_confounds_tsv(path)]
    if not candidates:
        return None
    bold_entities = _bids_entities(bold_path.name)
    matching = [
        path
        for path in candidates
        if _confounds_match_bold(_bids_entities(path.name), bold_entities)
    ]
    if len(matching) == 1:
        return matching[0]
    return candidates[0] if len(candidates) == 1 else None


def _confounds_match_bold(confounds_entities: dict[str, str], bold_entities: dict[str, str]) -> bool:
    for key in ("sub", "ses", "task", "acq", "ce", "rec", "dir", "run", "echo"):
        if confounds_entities.get(key) != bold_entities.get(key):
            return False
    return True


def _read_confounds_tsv(path: RequestPath, remote_host: str | None, probe: dict[str, Any] | None) -> list[dict[str, str]]:
    if probe is None:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return []
    else:
        text = _probe_text(probe, path)
        if not text:
            try:
                text = read_text(path, remote_host)
            except Exception:
                return []
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def _confound_column(rows: list[dict[str, str]], name: str) -> list[float | None] | None:
    if not rows or name not in rows[0]:
        return None
    return [_parse_optional_float(row.get(name)) for row in rows]


def _motion_columns(rows: list[dict[str, str]]) -> list[tuple[float, float, float, float, float, float]] | None:
    if not rows or any(name not in rows[0] for name in MOTION_COLUMNS):
        return None
    values = []
    for row in rows:
        parsed = [_parse_optional_float(row.get(name)) for name in MOTION_COLUMNS]
        if any(value is None for value in parsed):
            return None
        values.append(tuple(float(value) for value in parsed))
    return values


def _fd_from_motion(rows: list[tuple[float, float, float, float, float, float]]) -> list[float]:
    if not rows:
        return []
    fd = [0.0]
    for previous, current in zip(rows, rows[1:]):
        translation = sum(abs(current[index] - previous[index]) for index in range(3))
        rotation = sum(abs(current[index] - previous[index]) for index in range(3, 6))
        fd.append(translation + DEFAULT_XCPD_HEAD_RADIUS_MM * rotation)
    return fd


def _parse_optional_float(value: str | None) -> float | None:
    if value is None or value == "" or value.lower() in {"n/a", "nan"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _motion_filter_detail(request: RequestConfig) -> str:
    if not request.xcpd_motion_filter_type or request.xcpd_motion_filter_type == "none":
        return ""
    parts = [f"motion_filter={request.xcpd_motion_filter_type}"]
    if request.xcpd_band_stop_min is not None:
        parts.append(f"band_stop_min={request.xcpd_band_stop_min:g}")
    if request.xcpd_band_stop_max is not None:
        parts.append(f"band_stop_max={request.xcpd_band_stop_max:g}")
    if request.xcpd_motion_filter_order is not None:
        parts.append(f"order={request.xcpd_motion_filter_order}")
    return " ".join(parts)

def _check_candidates(paths: list[Path], dataset_flags: list[str], remote_host: str | None = None) -> dict[str, Any]:
    """Check whether candidate files exist and are readable.

    Inputs:
        paths (list[Path]): Path values to inspect.
        dataset_flags (list[str]): Detected dataset flags for the current BIDS tree.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        dict[str, Any]: Summary payload returned by the helper.
    """
    if not paths:
        return {"materialized": False, "reason_codes": []}
    materialized = False
    reason_codes: list[str] = []
    for path in paths:
        inspected = _inspect_path(path, dataset_flags, remote_host)
        if inspected["materialized"]:
            materialized = True
            break
        reason_codes.extend(inspected["reason_codes"])
    return {"materialized": materialized, "reason_codes": _dedupe(reason_codes)}


def _check_candidates_from_probe(paths: list[Path], dataset_flags: list[str], probe: dict[str, Any]) -> dict[str, Any]:
    """Check candidate files from remote probe data.

    Inputs:
        paths (list[Path]): Path values to inspect.
        dataset_flags (list[str]): Detected dataset flags for the current BIDS tree.
        probe (dict[str, Any]): Remote dataset probe payload.

    Returns:
        dict[str, Any]: Summary payload returned by the helper.
    """
    if not paths:
        return {"materialized": False, "reason_codes": []}
    materialized = False
    reason_codes: list[str] = []
    for path in paths:
        inspected = _inspect_path_from_probe(path, dataset_flags, probe)
        if inspected["materialized"]:
            materialized = True
            break
        reason_codes.extend(inspected["reason_codes"])
    return {"materialized": materialized, "reason_codes": _dedupe(reason_codes)}


def _inspect_path(path: Path, dataset_flags: list[str], remote_host: str | None = None) -> dict[str, Any]:
    """Inspect one local path for existence and readability.

    Inputs:
        path (Path): Filesystem path being inspected or normalized.
        dataset_flags (list[str]): Detected dataset flags for the current BIDS tree.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        dict[str, Any]: Summary payload returned by the helper.
    """
    reason_codes: list[str] = []
    if path_is_symlink(path, remote_host) and not path_exists(path, remote_host):
        reason_codes.append("annex_content_missing" if _uses_annex(dataset_flags) else "dataset_not_materialized")
        return {"materialized": False, "reason_codes": reason_codes}
    if not path_exists(path, remote_host):
        reason_codes.append("dataset_not_materialized")
        return {"materialized": False, "reason_codes": reason_codes}
    if not path_readable(path, remote_host):
        reason_codes.append("permission_denied")
        return {"materialized": False, "reason_codes": reason_codes}
    return {"materialized": True, "reason_codes": []}


def _inspect_path_from_probe(path: Path, dataset_flags: list[str], probe: dict[str, Any]) -> dict[str, Any]:
    """Inspect one remote path from probe metadata.

    Inputs:
        path (Path): Filesystem path being inspected or normalized.
        dataset_flags (list[str]): Detected dataset flags for the current BIDS tree.
        probe (dict[str, Any]): Remote dataset probe payload.

    Returns:
        dict[str, Any]: Summary payload returned by the helper.
    """
    reason_codes: list[str] = []
    info = _probe_path_info(probe, path)
    if info.get("is_symlink") and not info.get("exists"):
        reason_codes.append("annex_content_missing" if _uses_annex(dataset_flags) else "dataset_not_materialized")
        return {"materialized": False, "reason_codes": reason_codes}
    if not info.get("exists"):
        reason_codes.append("dataset_not_materialized")
        return {"materialized": False, "reason_codes": reason_codes}
    if not info.get("readable"):
        reason_codes.append("permission_denied")
        return {"materialized": False, "reason_codes": reason_codes}
    return {"materialized": True, "reason_codes": []}


def _has_fmriprep_derivatives(subject: SubjectEntry, fmriprep_root: RequestPath, remote_host: str | None = None) -> bool:
    """Check whether fMRIPrep derivatives already exist locally.

    Inputs:
        subject (SubjectEntry): Subject audit or subject status payload.
        fmriprep_root (Path): fMRIPrep derivatives root path.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        bool: Whether the condition is satisfied.
    """
    subject_dir = fmriprep_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label
    dataset_description = fmriprep_root / "dataset_description.json"
    return (
        path_exists(subject_dir, remote_host)
        and path_exists(dataset_description, remote_host)
        and (
            glob_exists(str(subject_dir / "**" / "*desc-preproc_bold.nii*"), remote_host)
            or glob_exists(str(subject_dir / "**" / "*_bold.dtseries.nii"), remote_host)
        )
    )


def _has_fmriprep_derivatives_from_probe(
    subject: SubjectEntry,
    fmriprep_root: RequestPath,
    probe: dict[str, Any],
) -> bool:
    """Check for fMRIPrep derivatives from probe metadata."""
    dataset_description = fmriprep_root / "dataset_description.json"
    if not _probe_path_exists(probe, dataset_description):
        return False
    subject_dir = fmriprep_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label
    for path in _remote_glob_values(probe):
        if _path_is_within(path, subject_dir) and ("desc-preproc_bold" in path.name or _is_fslr_dtseries(path)):
            return True
    return False


def _xcpd_input_status(
    subject: SubjectEntry,
    fmriprep_root: RequestPath,
    *,
    xcpd_mode: str,
    xcpd_task_ids: list[str],
    remote_host: str | None,
    probe: dict[str, Any] | None,
) -> dict[str, Any]:
    dataset_description = fmriprep_root / "dataset_description.json"
    subject_dir = fmriprep_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label

    if probe is None:
        has_dataset = path_exists(dataset_description, remote_host)
        has_subject = path_exists(subject_dir, remote_host)
        paths = _xcpd_subject_derivative_paths(subject_dir)
    else:
        has_dataset = _probe_path_exists(probe, dataset_description)
        has_subject = any(_path_is_within(path, subject_dir) for path in _remote_glob_values(probe))
        paths = [path for path in _remote_glob_values(probe) if _path_is_within(path, subject_dir)]
    if not has_dataset or not has_subject or not paths:
        return {"ready": False, "reason_codes": ["missing_fmriprep_derivatives"]}
    if xcpd_task_ids:
        filtered_paths = _filter_xcpd_task_paths(paths, xcpd_task_ids)
        if not filtered_paths:
            return {"ready": False, "reason_codes": ["missing_xcpd_task_derivatives"]}
        paths = filtered_paths

    if xcpd_mode == "nichart":
        nifti_set_ready = _xcpd_derivative_set_ready(
            paths,
            required=(
                _is_mni_preproc_bold,
                _is_mni_boldref,
                _is_mni_brain_mask,
                _is_confounds_tsv,
                _is_confounds_json,
            ),
        )
        return {
            # NiChart NIfTI matching is advisory; missing or ambiguous matches warn but do not block.
            "ready": True,
            "reason_codes": [],
            "warning_codes": [] if nifti_set_ready else [XCPD_NICHART_NIFTI_REASON],
            "input_format": "nifti" if nifti_set_ready else None,
        }

    ready = _xcpd_derivative_set_ready(
        paths,
        required=(
            _is_fslr_dtseries,
            _is_confounds_tsv,
            _is_confounds_json,
        ),
    )
    return {
        "ready": ready,
        "reason_codes": [] if ready else [XCPD_ABCD_CIFTI_REASON],
        "input_format": "cifti" if ready else None,
    }


def _xcpd_warning_details(subject: SubjectEntry, xcpd_status: dict[str, Any]) -> list[str]:
    warning_codes = set(xcpd_status.get("warning_codes") or [])
    if XCPD_NICHART_NIFTI_REASON not in warning_codes:
        return []
    detail_prefix = subject.subject_label
    if subject.session_label:
        detail_prefix = f"{detail_prefix} {subject.session_label}"
    return [
        (
            f"{detail_prefix}: NiChart NIfTI derivatives were not confidently matched as one coherent "
            "fMRIPrep derivative set; mixed MNI spaces can make this audit check conservative."
        )
    ]


def _filter_xcpd_task_paths(paths: list[RequestPath], task_ids: list[str]) -> list[RequestPath]:
    wanted = {value.removeprefix("task-") for value in task_ids}
    filtered: list[RequestPath] = []
    for path in paths:
        task = _bids_entities(path.name).get("task")
        if task is None or task in wanted:
            filtered.append(path)
    return filtered


def _xcpd_subject_derivative_paths(subject_dir: RequestPath) -> list[RequestPath]:
    if not isinstance(subject_dir, Path) or not subject_dir.exists():
        return []
    patterns = (
        "**/*_bold.dtseries.nii",
        "**/*desc-preproc_bold.nii*",
        "**/*space-MNI152NLin*_desc-preproc_bold.nii*",
        "**/*space-MNI152NLin*_boldref.nii*",
        "**/*space-MNI152NLin*_desc-brain_mask.nii*",
        "**/*desc-confounds_timeseries.tsv",
        "**/*desc-confounds_timeseries.json",
    )
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path for path in subject_dir.glob(pattern) if path.is_file())
    return sorted(paths)


def _xcpd_derivative_set_ready(
    paths: list[RequestPath],
    *,
    required: tuple[Callable[[RequestPath], bool], ...],
) -> bool:
    groups: dict[tuple[str, ...], list[RequestPath]] = {}
    for path in paths:
        key = _bids_run_key(path.name)
        if key:
            groups.setdefault(key, []).append(path)
    for group in groups.values():
        relevant = [path for path in group if any(predicate(path) for predicate in required)]
        if not relevant:
            continue
        if _coherent_required_subset_ready(relevant, required):
            return True
    return False


def _coherent_required_subset_ready(
    paths: list[RequestPath],
    required: tuple[Callable[[RequestPath], bool], ...],
) -> bool:
    spatial_keys = _spatial_entity_keys(paths)
    if not spatial_keys:
        return all(any(predicate(path) for path in paths) for predicate in required)
    for spatial_key in spatial_keys:
        subset = [
            path
            for path in paths
            if (key := _spatial_entity_key(path)) is None or key == spatial_key
        ]
        if all(any(predicate(path) for path in subset) for predicate in required):
            return True
    return False


def _bids_run_key(name: str) -> tuple[tuple[str, str], ...]:
    entities = _bids_entities(name)
    return tuple(
        (key, value)
        for key in ("sub", "ses", "task", "acq", "ce", "rec", "run", "dir", "echo", "part", "chunk")
        if (value := entities.get(key)) is not None
    )


def _spatial_entities_coherent(paths: list[RequestPath]) -> bool:
    return len(_spatial_entity_keys(paths)) <= 1


def _spatial_entity_keys(paths: list[RequestPath]) -> set[tuple[tuple[str, str], ...]]:
    return {key for path in paths if (key := _spatial_entity_key(path)) is not None}


def _spatial_entity_key(path: RequestPath) -> tuple[tuple[str, str], ...] | None:
    entities = _bids_entities(path.name)
    if entities.get("space") is None:
        return None
    return tuple(
        (key, value)
        for key in ("space", "res", "den")
        if (value := entities.get(key)) is not None
    )


def _bids_entities(name: str) -> dict[str, str]:
    entities: dict[str, str] = {}
    for part in name.split("_"):
        if "-" not in part:
            continue
        key, value = part.split("-", 1)
        if key and value:
            entities[key] = value
    return entities


def _filter_bold_candidates(paths: list[Path], request: RequestConfig) -> list[Path]:
    filtered: list[Path] = []
    for path in paths:
        entities = _bids_entities(path.name)
        if request.task_id and entities.get("task") != request.task_id:
            continue
        if request.echo_idx is not None and entities.get("echo") != str(request.echo_idx):
            continue
        filtered.append(path)
    return filtered


def _is_fslr_dtseries(path: RequestPath) -> bool:
    name = path.name
    return "space-fsLR" in name and name.endswith("_bold.dtseries.nii")


def _is_mni_preproc_bold(path: RequestPath) -> bool:
    name = path.name
    return "space-MNI152NLin" in name and "desc-preproc_bold.nii" in name


def _is_mni_boldref(path: RequestPath) -> bool:
    name = path.name
    return "space-MNI152NLin" in name and ("_boldref.nii" in name)


def _is_mni_brain_mask(path: RequestPath) -> bool:
    name = path.name
    return "space-MNI152NLin" in name and "desc-brain_mask.nii" in name


def _is_confounds_tsv(path: RequestPath) -> bool:
    return path.name.endswith("desc-confounds_timeseries.tsv")


def _is_confounds_json(path: RequestPath) -> bool:
    return path.name.endswith("desc-confounds_timeseries.json")


def _has_xcpd_derivatives(subject: SubjectEntry, output_root: Path, remote_host: str | None = None) -> bool:
    """Check whether XCP-D derivatives already exist."""
    xcpd_root = _xcpd_output_root(output_root)
    subject_dir = xcpd_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label
    dataset_description = xcpd_root / "dataset_description.json"
    return (
        path_exists(subject_dir, remote_host)
        and path_exists(dataset_description, remote_host)
        and glob_exists(str(subject_dir / "**" / "*desc-denoised*_bold*"), remote_host)
    )


def _has_xcpd_derivatives_from_probe(subject: SubjectEntry, output_root: Path, probe: dict[str, Any]) -> bool:
    """Check for XCP-D derivatives from probe metadata."""
    xcpd_root = _xcpd_output_root(output_root)
    dataset_description = xcpd_root / "dataset_description.json"
    if not _probe_path_exists(probe, dataset_description):
        return False
    subject_dir = xcpd_root / subject.subject_label
    if subject.session_label:
        subject_dir = subject_dir / subject.session_label
    for path in _remote_glob_values(probe):
        if "desc-denoised" in path.name and _path_is_within(path, subject_dir):
            return True
    return False


def _review_existing_derivatives(
    request: RequestConfig,
    audited_subjects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize visible existing derivatives for later router decisions."""
    has_fmriprep = any(bool((subject.get("fmriprep") or {}).get("has_derivatives")) for subject in audited_subjects)
    has_xcpd = (
        any(bool((subject.get("xcpd") or {}).get("has_xcpd_derivatives")) for subject in audited_subjects)
        if request.target == "xcpd"
        else False
    )
    detected: list[str] = []
    if has_fmriprep:
        detected.append("fmriprep")
    if has_xcpd:
        detected.append("xcpd")
    pipelines = {
        "fmriprep": {
            "path": str(request.resolve_pipeline_output_root("fmriprep")),
            "detected": has_fmriprep,
        },
    }
    if request.target == "xcpd":
        pipelines["xcpd"] = {
            "path": str(request.resolve_pipeline_output_root("xcpd")),
            "detected": has_xcpd,
        }
    return {
        "pipelines": pipelines,
        "detected": detected,
    }


def _group_audited_sessions(audited_sessions: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: list[tuple[str, list[dict[str, Any]]]] = []
    indexes: dict[str, int] = {}
    for audited in audited_sessions:
        subject_id = str(audited["subject_id"])
        if subject_id not in indexes:
            indexes[subject_id] = len(grouped)
            grouped.append((subject_id, [audited]))
            continue
        grouped[indexes[subject_id]][1].append(audited)
    return grouped


def _build_storage_subject(session_audits: list[dict[str, Any]]) -> dict[str, Any]:
    fmriprep_ready = all(_pipeline_status(audit, "fmriprep") == "ready" for audit in session_audits)
    include_xcpd = any("xcpd" in audit for audit in session_audits)
    payload = {
        "subject_id": session_audits[0]["subject_id"],
        "session_ids": _dedupe(
            [str(audit["session_id"]) for audit in session_audits if audit.get("session_id") is not None]
        ),
        "fmriprep": {
            "status": "ready" if fmriprep_ready else "blocked",
            "reason_codes": _dedupe(
                [
                    reason
                    for audit in session_audits
                    for reason in list((audit.get("fmriprep") or {}).get("reason_codes", []))
                ]
            ),
            "inputs_materialized": all(
                bool((audit.get("fmriprep") or {}).get("inputs_materialized"))
                for audit in session_audits
            ),
            "has_derivatives": all(
                bool((audit.get("fmriprep") or {}).get("has_derivatives"))
                for audit in session_audits
            ),
        },
        "sessions": [dict(audit) for audit in session_audits],
    }
    if include_xcpd:
        xcpd_ready = all(_pipeline_status(audit, "xcpd") == "ready" for audit in session_audits)
        input_modes = _dedupe(
            [
                str((audit.get("xcpd") or {}).get("input_mode"))
                for audit in session_audits
                if (audit.get("xcpd") or {}).get("input_mode")
            ]
        )
        input_formats = _dedupe(
            [
                str((audit.get("xcpd") or {}).get("input_format"))
                for audit in session_audits
                if (audit.get("xcpd") or {}).get("input_format")
            ]
        )
        payload["xcpd"] = {
            "status": "ready" if xcpd_ready else "blocked",
            "reason_codes": _dedupe(
                [
                    reason
                    for audit in session_audits
                    for reason in list((audit.get("xcpd") or {}).get("reason_codes", []))
                ]
            ),
            "has_fmriprep_derivatives": all(
                bool((audit.get("xcpd") or {}).get("has_fmriprep_derivatives"))
                for audit in session_audits
            ),
            "has_xcpd_derivatives": all(
                bool((audit.get("xcpd") or {}).get("has_xcpd_derivatives"))
                for audit in session_audits
            ),
        }
        if input_modes:
            payload["xcpd"]["input_mode"] = input_modes[0]
        if xcpd_ready and len(input_formats) == 1:
            payload["xcpd"]["input_format"] = input_formats[0]
    return payload


def _build_artifact_subject(subject: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "subject_id": subject["subject_id"],
        "session_ids": list(subject.get("session_ids") or []),
        "fmriprep": dict(subject.get("fmriprep") or {}),
        "sessions": [_build_artifact_session(session) for session in subject.get("sessions") or []],
    }
    if "xcpd" in subject:
        payload["xcpd"] = dict(subject.get("xcpd") or {})
    return payload


def _build_artifact_session(session: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "session_id": session.get("session_id"),
        "fmriprep": dict(session.get("fmriprep") or {}),
    }
    if "xcpd" in session:
        payload["xcpd"] = dict(session.get("xcpd") or {})
    return payload


def _pipeline_status(subject: dict[str, Any], pipeline: str) -> str:
    return str((subject.get(pipeline) or {}).get("status") or "blocked")


def _existing_derivatives_warning_codes(existing_derivatives: dict[str, Any], *, target: str) -> list[str]:
    warnings: list[str] = []
    detected = existing_derivatives.get("detected") or []
    if target != "xcpd" and "fmriprep" in detected:
        warnings.append("existing_fmriprep_derivatives_detected")
    if target == "xcpd" and "xcpd" in detected:
        warnings.append("existing_xcpd_derivatives_detected")
    return warnings


def _collect_output_space_advice(subjects: list[dict[str, Any]]) -> list[dict[str, str]]:
    for subject in subjects:
        for session in subject.get("sessions") or []:
            if _has_high_resolution_bold(session):
                return [dict(HIGH_RESOLUTION_OUTPUT_ADVICE)]
    return []


def _has_high_resolution_bold(session: dict[str, Any]) -> bool:
    metadata = (session.get("input_image_metadata") or {}).get("bold") or []
    return any(_bold_metadata_is_high_resolution(item) for item in metadata)


def _bold_metadata_is_high_resolution(metadata: dict[str, Any]) -> bool:
    zooms = metadata.get("zooms") if isinstance(metadata, dict) else None
    if not isinstance(zooms, (list, tuple)) or len(zooms) < 3:
        return False
    try:
        spatial_zooms = [float(value) for value in zooms[:3]]
    except (TypeError, ValueError):
        return False
    return min(spatial_zooms) <= HIGH_RESOLUTION_BOLD_THRESHOLD_MM


def _manual_materialization_instruction(
    subject: SubjectEntry,
    request: RequestConfig,
    dataset_flags: list[str],
) -> dict[str, str] | None:
    """Inputs:
        subject (SubjectEntry): Subject audit or subject status payload.
        request (RequestConfig): Workflow request after CLI/config normalization.
        dataset_flags (list[str]): Detected dataset flags for the current BIDS tree.

    Returns:
        dict[str, str] | None: Mapping result, or ``None`` when unavailable.
    """
    if not _uses_annex(dataset_flags):
        return None
    subject_path = request.resolve_bids_root() / subject.subject_label
    if subject.session_label:
        subject_path = subject_path / subject.session_label
    quoted_subject_path = shlex.quote(str(subject_path))
    if "datalad" in dataset_flags or "openneuro" in dataset_flags:
        return {
            "reason_code": "datalad_get_required",
            "warning_detail": f"manual_materialization_required: run `datalad get -r -J8 {quoted_subject_path}`",
        }
    return {
        "reason_code": "git_annex_get_required",
        "warning_detail": f"manual_materialization_required: run `git annex get {quoted_subject_path}`",
    }


def _uses_annex(dataset_flags: list[str]) -> bool:
    """Return whether the dataset uses annex-backed storage.

    Inputs:
        dataset_flags (list[str]): Detected dataset flags for the current BIDS tree.

    Returns:
        bool: Whether the condition is satisfied.
    """
    return "datalad" in dataset_flags or "git-annex" in dataset_flags or "openneuro" in dataset_flags


def _probe_path_info(probe: dict[str, Any], path: RequestPath) -> dict[str, Any]:
    """Read one path record from the remote probe.

    Inputs:
        probe (dict[str, Any]): Remote dataset probe payload.
        path (Path): Filesystem path being inspected or normalized.

    Returns:
        dict[str, Any]: Summary payload returned by the helper.
    """
    return probe.get("paths", {}).get(str(path), {})


def _collect_input_size_bytes(
    t1_candidates: list[Path],
    bold_candidates: list[Path],
    probe: dict[str, Any] | None = None,
) -> dict[str, list[int]]:
    """Collect best-effort input file sizes for storage estimation."""
    return {
        "t1w": _collect_candidate_sizes(t1_candidates, probe),
        "bold": _collect_candidate_sizes(bold_candidates, probe),
    }


def _collect_input_image_metadata(
    t1_candidates: list[Path],
    bold_candidates: list[Path],
    probe: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Collect best-effort image metadata for the provided candidate paths."""
    return {
        "t1w": _collect_candidate_image_metadata(t1_candidates, probe),
        "bold": _collect_candidate_image_metadata(bold_candidates, probe),
    }


def _collect_candidate_image_metadata(paths: list[Path], probe: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Collect known image metadata summaries for the provided candidate paths."""
    metadata: list[dict[str, Any]] = []
    for path in paths:
        image_metadata = _path_image_metadata(path, probe)
        if image_metadata is not None:
            metadata.append(image_metadata)
    return metadata


def _collect_candidate_sizes(paths: list[Path], probe: dict[str, Any] | None = None) -> list[int]:
    """Collect known file sizes for the provided candidate paths."""
    sizes: list[int] = []
    for path in paths:
        size_bytes = _path_size_bytes(path, probe)
        if size_bytes is not None:
            sizes.append(size_bytes)
    return sizes


def _path_size_bytes(path: Path, probe: dict[str, Any] | None = None) -> int | None:
    """Return one file size in bytes when it is available."""
    if probe is None:
        try:
            return int(path.stat().st_size) if path.exists() else None
        except OSError:
            return None
    value = _probe_path_info(probe, path).get("size_bytes")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _path_image_metadata(path: Path, probe: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return one image metadata summary when it is available from the probe."""
    if probe is None:
        try:
            return load_image_metadata(path)
        except Exception:
            return None
    value = _probe_path_info(probe, path).get("image_metadata")
    return value if isinstance(value, dict) else None


def _image_metadata_required(paths: list[Path], probe: dict[str, Any] | None = None) -> bool:
    if not paths:
        return False
    if probe is None:
        return True
    return any("image_metadata" in _probe_path_info(probe, path) for path in paths)


@lru_cache(maxsize=8)
def _layout_for_root(bids_root: Path) -> BIDSLayout:
    """Create and cache a PyBIDS layout for one dataset root."""
    return BIDSLayout(str(bids_root), validate=False)


def _layout_subject_ids(layout: BIDSLayout | None) -> list[str]:
    """Read subject identifiers from a layout when available."""
    if layout is None:
        return []
    return sorted(_normalize_subject_id(value) for value in layout.get(return_type="id", target="subject"))


def _layout_session_ids(layout: BIDSLayout | None, subject_id: str) -> list[str]:
    """Read session identifiers for one subject from a layout when available."""
    if layout is None:
        return []
    return sorted(
        _normalize_session_id(value)
        for value in layout.get(subject=subject_id, return_type="id", target="session")
        if _normalize_session_id(value) is not None
    )


def _layout_subject_candidates(
    layout: BIDSLayout | None,
    subject: SubjectEntry,
    *,
    datatype: str,
    suffix: str,
) -> list[Path]:
    """Resolve subject-level BIDS image candidates from a layout."""
    if layout is None:
        return []
    query: dict[str, Any] = {
        "subject": subject.subject_id,
        "datatype": datatype,
        "suffix": suffix,
        "extension": ["nii", "nii.gz"],
        "return_type": "file",
    }
    if subject.session_id is not None:
        query["session"] = subject.session_id
    return [Path(path) for path in layout.get(**query)]


def _probe_path_exists(probe: dict[str, Any], path: Path) -> bool:
    """Return whether the probe reports an existing path.

    Inputs:
        probe (dict[str, Any]): Remote dataset probe payload.
        path (Path): Filesystem path being inspected or normalized.

    Returns:
        bool: Whether the condition is satisfied.
    """
    return bool(_probe_path_info(probe, path).get("exists"))


def _probe_text(probe: dict[str, Any], path: RequestPath) -> str:
    """Read one text payload from the remote probe.

    Inputs:
        probe (dict[str, Any]): Remote dataset probe payload.
        path (Path): Filesystem path being inspected or normalized.

    Returns:
        str: Normalized string value.
    """
    return str(probe.get("texts", {}).get(str(path), ""))


def _remote_glob_matches(probe: dict[str, Any], pattern: str) -> list[RequestPath]:
    """Return remote glob matches from the probe payload."""
    return [_remote_probe_path(match) for match in probe.get("globs", {}).get(pattern, [])]


def _remote_glob_values(probe: dict[str, Any]) -> list[RequestPath]:
    ordered: list[RequestPath] = []
    for matches in probe.get("globs", {}).values():
        for match in matches:
            path = _remote_probe_path(match)
            if path not in ordered:
                ordered.append(path)
    return ordered


def _remote_subject_dirs(probe: dict[str, Any], bids_root: RequestPath) -> list[RequestPath]:
    ordered: list[RequestPath] = []
    for path in _remote_glob_values(probe):
        if path.parent == bids_root and path.name.startswith("sub-") and path not in ordered:
            ordered.append(path)
    return ordered


def _remote_session_dirs(probe: dict[str, Any], bids_root: RequestPath) -> list[RequestPath]:
    ordered: list[RequestPath] = []
    for path in _remote_glob_values(probe):
        if not _path_is_within(path, bids_root):
            continue
        if path.name.startswith("ses-") and path.parent.name.startswith("sub-") and path not in ordered:
            ordered.append(path)
    return ordered


def _remote_subject_input_patterns(
    bids_root: Path,
    subject: SubjectEntry,
    *,
    datatype: str,
    suffix: str,
) -> list[str]:
    subject_root = bids_root / subject.subject_label
    if subject.session_label:
        subject_root = subject_root / subject.session_label
    return [str(subject_root / datatype / f"*_{suffix}.nii*")]

def _remote_subject_candidates(probe: dict[str, Any], patterns: list[str]) -> list[RequestPath]:
    """Collect candidate subject paths from remote glob results."""
    matches: list[RequestPath] = []
    for pattern in patterns:
        for match in _remote_glob_matches(probe, pattern):
            if match not in matches:
                matches.append(match)
    return sorted(matches)

def _path_is_within(path: RequestPath, parent: RequestPath) -> bool:
    """Return whether a path is inside the requested parent.

    Inputs:
        path (Path): Filesystem path being inspected or normalized.
        parent (Path): Parent path used for containment checks.

    Returns:
        bool: Whether the condition is satisfied.
    """
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _remote_probe_path(value: str) -> PurePosixPath:
    """Normalize one remote probe path without inheriting local path semantics."""
    return PurePosixPath(value)


def _normalize_subject_id(value: str) -> str:
    """Normalize one subject identifier.

    Inputs:
        value (str): Raw string value being checked or normalized.

    Returns:
        str: Normalized string value.
    """
    return value.strip().removeprefix("sub-")


def _normalize_session_id(value: str | None) -> str | None:
    """Normalize one session identifier.

    Inputs:
        value (str | None): Optional string value being checked or normalized.

    Returns:
        str | None: Resolved string value, or ``None`` when unavailable.
    """
    if value in (None, ""):
        return None
    return value.strip().removeprefix("ses-")


def _dedupe(values: list[str]) -> list[str]:
    """Return values without duplicates while preserving order.

    Inputs:
        values (list[str]): Values to normalize or deduplicate.

    Returns:
        list[str]: List containing the computed values.
    """
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered
