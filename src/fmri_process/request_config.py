"""Build RequestConfig objects from explicit CLI args."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path as FilesystemPath, Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

from fmri_core.models import (
    DEFAULT_FMRIPREP_CIFTI_OUTPUT,
    DEFAULT_FMRIPREP_NO_RECONALL_OUTPUT_SPACES,
    DEFAULT_FMRIPREP_OUTPUT_SPACES,
    RequestConfig,
    VALID_BACKENDS,
    VALID_RUNTIMES,
    VALID_XCPD_MOTION_FILTER_TYPES,
    VALID_XCPD_MODES,
    VALID_XCPD_YN_VALUES,
    validate_remote_request_paths,
)
from fmri_core.shell import glob_paths, probe_remote_dataset

PROCESS_TARGET = "fmriprep"
ARTIFACT_LOCATOR_REQUEST_FIELDS = {
    "bids_root",
    "output_root",
    "remote_host",
}
LOCATOR_REQUEST_FIELDS = ARTIFACT_LOCATOR_REQUEST_FIELDS | {
    "subjects",
    "sessions",
}
XCPD_LOCATOR_REQUEST_FIELDS = LOCATOR_REQUEST_FIELDS | {
    "fmriprep_derivatives",
}
PROCESS_STAGE_REQUEST_FIELDS = LOCATOR_REQUEST_FIELDS | {
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
    "nthreads_per_job",
    "omp_nthreads",
    "slurm_mem_gb",
    "max_jobs",
    "skip_bids_validation",
    "wsl_vhdx_path",
    "windows_host_drive",
    "docker_wsl_storage_path",
    "run_id",
}
FMRIPREP_RUN_PARAMETER_REQUEST_FIELDS = {
    "task_id",
    "echo_idx",
    "anat_only",
    "fmriprep_custom_args",
}
OUTPUT_SELECTION_REQUEST_FIELDS = {"output_spaces", "cifti_output"}
RECON_MODE_REQUEST_FIELDS = {"fs_no_reconall"}
PROCESS_REQUEST_FIELDS = (
    PROCESS_STAGE_REQUEST_FIELDS
    | FMRIPREP_RUN_PARAMETER_REQUEST_FIELDS
    | OUTPUT_SELECTION_REQUEST_FIELDS
    | RECON_MODE_REQUEST_FIELDS
)
DATASET_AUDIT_REQUEST_FIELDS = (
    LOCATOR_REQUEST_FIELDS | FMRIPREP_RUN_PARAMETER_REQUEST_FIELDS | OUTPUT_SELECTION_REQUEST_FIELDS | RECON_MODE_REQUEST_FIELDS
)
RUNTIME_AUDIT_REQUEST_FIELDS = ARTIFACT_LOCATOR_REQUEST_FIELDS | {
    "work_root",
    "log_root",
    "download_root",
    "fs_license",
    "templateflow_home",
    "templateflow_tool_bins",
    "fmriprep_image",
    "container_runtime",
    "executor_policy",
    "skip_bids_validation",
    "nthreads_per_job",
    "omp_nthreads",
    "slurm_mem_gb",
    "max_jobs",
    "wsl_vhdx_path",
    "windows_host_drive",
    "docker_wsl_storage_path",
} | FMRIPREP_RUN_PARAMETER_REQUEST_FIELDS | OUTPUT_SELECTION_REQUEST_FIELDS | RECON_MODE_REQUEST_FIELDS
XCPD_STAGE_REQUEST_FIELDS = XCPD_LOCATOR_REQUEST_FIELDS | {
    "work_root",
    "log_root",
    "download_root",
    "fs_license",
    "templateflow_home",
    "templateflow_tool_bins",
    "xcpd_image",
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
    "run_id",
    "xcpd_mode",
    "xcpd_min_time",
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
}
RUN_FMRIPREP_REQUEST_FIELDS = {
    "bids_root",
    "output_root",
    "remote_host",
    "scheduler_partition",
    "run_id",
}
RUN_XCPD_REQUEST_FIELDS = ARTIFACT_LOCATOR_REQUEST_FIELDS | {
    "fmriprep_derivatives",
    "work_root",
    "log_root",
    "download_root",
    "fs_license",
    "templateflow_home",
    "templateflow_tool_bins",
    "xcpd_image",
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
    "run_id",
    "xcpd_mode",
    "xcpd_min_time",
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
}
PATH_PROBE_REQUEST_FIELDS = {
    "target",
    "bids_root",
    "user_dataset_path",
    "output_root",
    "templateflow_home",
    "fs_license",
    "fmriprep_image",
    "xcpd_image",
    "remote_host",
    "required_paths",
}
TEMPLATEFLOW_TOOL_BIN_REQUIRED_COMMANDS = {
    "process",
    "runtime-audit",
}
COMMAND_REQUEST_FIELDS = {
    "process": PROCESS_REQUEST_FIELDS,
    "runtime-audit": RUNTIME_AUDIT_REQUEST_FIELDS,
    "dataset-audit": DATASET_AUDIT_REQUEST_FIELDS,
    "xcpd-audit": XCPD_STAGE_REQUEST_FIELDS,
    "run-fmriprep": RUN_FMRIPREP_REQUEST_FIELDS,
    "run-xcpd": RUN_XCPD_REQUEST_FIELDS,
    "run-status": ARTIFACT_LOCATOR_REQUEST_FIELDS | {"target"},
}
CONTROL_ARGUMENT_FIELDS = {
    "command",
    "subject_file",
    "resume_from",
    "auto_approve",
    "reaudit_runtime",
    "reuse_dataset_from",
    "reuse_context_from",
    "from_runtime_audit",
    "kind",
    "audit_id",
    "submission_id",
    "log_lines",
    "max_paths",
}
VALID_CIFTI_OUTPUTS = ("91k",)
CIFTI_OUTPUT_SPACE_ALIASES = {
    "cifti:91k": "91k",
    "fsLR:den-91k": "91k",
}
FMRIPREP_CUSTOM_ARG_SPECS: dict[str, tuple[str, type | tuple[type, ...]]] = {
    "ignore": ("--ignore", list),
    "force": ("--force", list),
    "bold2anat_init": ("--bold2anat-init", str),
    "bold2anat_dof": ("--bold2anat-dof", int),
    "slice_time_ref": ("--slice-time-ref", float),
    "dummy_scans": ("--dummy-scans", int),
    "fallback_total_readout_time": ("--fallback-total-readout-time", float),
    "mem": ("--mem", (int, float)),
    "mem_mb": ("--mem-mb", int),
    "random_seed": ("--random-seed", int),
    "me_t2s_fit_method": ("--me-t2s-fit-method", str),
    "skull_strip_template": ("--skull-strip-template", str),
    "me_output_echos": ("--me-output-echos", bool),
    "low_mem": ("--low-mem", bool),
    "return_all_components": ("--return-all-components", bool),
    "fd_spike_threshold": ("--fd-spike-threshold", float),
    "dvars_spike_threshold": ("--dvars-spike-threshold", float),
    "aggregate_session_reports": ("--aggregate-session-reports", bool),
    "medial_surface_nan": ("--medial-surface-nan", bool),
    "md_only_boilerplate": ("--md-only-boilerplate", bool),
    "msm": ("--msm", bool),
    "project_goodvoxels": ("--project-goodvoxels", bool),
    "skull_strip_fixed_seed": ("--skull-strip-fixed-seed", bool),
    "skull_strip_t1w": ("--skull-strip-t1w", str),
    "fmap_bspline": ("--fmap-bspline", bool),
    "fmap_no_demean": ("--fmap-no-demean", bool),
    "use_syn_sdc": ("--use-syn-sdc", bool),
    "verbose": ("--verbose", int),
    "resource_monitor": ("--resource-monitor", bool),
    "stop_on_first_crash": ("--stop-on-first-crash", bool),
}
REJECTED_FMRIPREP_CUSTOM_KEYS = {
    "bids_filter_file",
    "bids_database_dir",
    "use_plugin",
    "config_file",
    "fs_subjects_dir",
    "clean_workdir",
    "derivatives",
    "anat_derivatives",
}
XCPD_CUSTOM_ARG_SPECS: dict[str, tuple[str, type | tuple[type, ...]]] = {
    "dummy_scans": ("--dummy-scans", (int, str)),
    "smoothing": ("--smoothing", (int, float, str)),
    "combine_runs": ("--combine-runs", str),
    "skip": ("--skip", list),
    "head_radius": ("--head-radius", (int, float, str)),
    "fd_thresh": ("--fd-thresh", (int, float, str)),
    "output_type": ("--output-type", str),
    "disable_bandpass_filter": ("--disable-bandpass-filter", bool),
    "lower_bpf": ("--lower-bpf", float),
    "upper_bpf": ("--upper-bpf", float),
    "bpf_order": ("--bpf-order", int),
    "min_coverage": ("--min-coverage", (int, float, str)),
    "output_run_wise_correlations": ("--output-run-wise-correlations", str),
    "atlases": ("--atlases", list),
    "nuisance_regressors": ("--nuisance-regressors", str),
    "create_matrices": ("--create-matrices", list),
    "random_seed": ("--random-seed", int),
    "linc_qc": ("--linc-qc", str),
    "abcc_qc": ("--abcc-qc", str),
    "report_output_level": ("--report-output-level", str),
    "aggregate_session_reports": ("--aggregate-session-reports", str),
    "low_mem": ("--low-mem", bool),
    "md_only_boilerplate": ("--md-only-boilerplate", bool),
    "resource_monitor": ("--resource-monitor", bool),
    "stop_on_first_crash": ("--stop-on-first-crash", bool),
    "verbose": ("-v", int),
}
REJECTED_XCPD_CUSTOM_KEYS = {
    "raw",
    "raw_args",
    "clean_workdir",
    "debug",
    "help",
    "version",
    "skip_parcellation",
    "input_type",
    "file_format",
    "output_layout",
    "config_file",
    "use_plugin",
    "bids_database_dir",
    "reports_only",
    "boilerplate_only",
    "write_graph",
    "warp_surfaces_native2std",
    "bids_filter_file",
    "datasets",
    "mem_mb",
}

def request_from_args(args: argparse.Namespace) -> RequestConfig:
    merged_values = _cli_request_overrides(args)
    subject_file = getattr(args, "subject_file", None)
    if subject_file is not None:
        if not isinstance(subject_file, str):
            raise ValueError("subject_file must be a string")
        subject_file = subject_file.strip() or None
    return _request_from_values(args.command, merged_values, subject_file=subject_file)


def explicit_request_fields(args: argparse.Namespace) -> list[str]:
    request_fields = _request_fields(args.command)
    fields: set[str] = set()
    cli_fields = {field for field in vars(args) if field not in CONTROL_ARGUMENT_FIELDS}
    fields.update(cli_fields)
    if "subject_file" in vars(args):
        fields.add("subjects")
    if "xcpd_dataset_items" in vars(args):
        fields.add("xcpd_datasets")
    if "fmriprep_custom_arg_items" in vars(args):
        fields.add("fmriprep_custom_args")
    if "xcpd_custom_arg_items" in vars(args):
        fields.add("xcpd_custom_args")
    fields.discard("subject_file")
    fields.discard("xcpd_dataset_items")
    fields.discard("fmriprep_custom_arg_items")
    fields.discard("xcpd_custom_arg_items")
    return sorted(field for field in fields if field in request_fields)


def path_probe_values_from_args(args: argparse.Namespace) -> dict[str, Any]:
    merged_values = _cli_request_overrides(args)
    unknown = sorted(set(merged_values) - PATH_PROBE_REQUEST_FIELDS)
    if unknown:
        details = ", ".join(unknown)
        raise ValueError(f"Unknown path-probe request field(s): {details}")
    return {
        "target": merged_values.get("target", "fmriprep"),
        "bids_root": path_value(_text_value(merged_values.get("bids_root"), "bids_root"), preserve_remote_posix=bool(merged_values.get("remote_host"))),
        "user_dataset_path": path_value(
            _text_value(merged_values.get("user_dataset_path"), "user_dataset_path"),
            preserve_remote_posix=bool(merged_values.get("remote_host")),
        ),
        "output_root": path_value(_text_value(merged_values.get("output_root"), "output_root"), preserve_remote_posix=bool(merged_values.get("remote_host"))),
        "user_templateflow_path": path_value(
            _text_value(merged_values.get("templateflow_home"), "templateflow_home"),
            preserve_remote_posix=bool(merged_values.get("remote_host")),
        ),
        "fs_license": path_value(_text_value(merged_values.get("fs_license"), "fs_license"), preserve_remote_posix=bool(merged_values.get("remote_host"))),
        "fmriprep_image": _text_value(merged_values.get("fmriprep_image"), "fmriprep_image"),
        "xcpd_image": _text_value(merged_values.get("xcpd_image"), "xcpd_image"),
        "remote_host": _text_value(merged_values.get("remote_host"), "remote_host"),
        "required_paths": _string_list_value(merged_values.get("required_paths"), "required_paths"),
    }


def path_value(value: str | None, *, preserve_remote_posix: bool = False) -> Path | PurePosixPath | None:
    if not value:
        return None
    if preserve_remote_posix:
        normalized = value.replace("\\", "/")
        if normalized.startswith("//"):
            return PurePosixPath("/" + normalized.lstrip("/"))
        if normalized.startswith("/"):
            return PurePosixPath(normalized)
        if re.match(r"^[A-Za-z]:[\\/]", value):
            raise ValueError(
                "Remote path was parsed as a Windows path. "
                "Use PowerShell/CMD, or disable MSYS path conversion, "
                "and pass a POSIX path like /gpfs/..."
            )
        raise ValueError(f"Remote path must be POSIX, got: {value}")
    normalized_mnt_path = _native_windows_mnt_path(value)
    if normalized_mnt_path is not None:
        return Path(normalized_mnt_path)
    return Path(value)


def default_run_id(command: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def _request_from_values(
    command: str,
    merged_values: dict[str, Any],
    *,
    subject_file: str | None,
) -> RequestConfig:
    def text(field: str) -> str | None:
        value = merged_values.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        stripped = value.strip()
        return stripped or None

    def string_list(field: str) -> list[str]:
        return _string_list_value(merged_values.get(field), field)

    def choice(field: str, *, default: str, choices: tuple[str, ...]) -> str:
        selected = text(field) or default
        if selected not in choices:
            allowed = ", ".join(choices)
            raise ValueError(f"{field} must be one of: {allowed}")
        return selected

    def boolean(field: str, *, default: bool) -> bool:
        return _bool_value(merged_values.get(field), field, default=default)

    def path(field: str, *, preserve_remote_posix: bool = False) -> Path | PurePosixPath | None:
        return path_value(text(field), preserve_remote_posix=preserve_remote_posix)

    remote_host = text("remote_host")
    bids_root = path("bids_root", preserve_remote_posix=bool(remote_host))
    fmriprep_derivatives = path("fmriprep_derivatives", preserve_remote_posix=bool(remote_host))
    subject_discovery_root = (
        fmriprep_derivatives
        if command == "xcpd-audit" and bids_root is None and fmriprep_derivatives is not None
        else bids_root
    )
    accepts_subject_scope = command in {"process", "dataset-audit", "xcpd-audit"}
    subjects = (
        _expand_subject_selectors(
            selectors=string_list("subjects"),
            subject_file=subject_file,
            bids_root=subject_discovery_root,
            remote_host=remote_host,
        )
        if accepts_subject_scope
        else []
    )
    sessions = string_list("sessions") if accepts_subject_scope else []
    fs_no_reconall = boolean("fs_no_reconall", default=False)
    fmriprep_run_parameters_allowed = command in {"process", "dataset-audit", "runtime-audit"}
    if command == "prepare-probe":
        target_hint = text("target") or PROCESS_TARGET
    elif command == "run-status":
        target_hint = text("target")
    elif command in {"xcpd-audit", "run-xcpd"}:
        target_hint = "xcpd"
    else:
        target_hint = PROCESS_TARGET
    if command in {"prepare-probe", "run-status"} and target_hint is not None and target_hint not in {PROCESS_TARGET, "xcpd"}:
        raise ValueError("target must be one of: fmriprep, xcpd")
    fmriprep_signature_defaults_allowed = target_hint == PROCESS_TARGET and command in {
        "process",
        "dataset-audit",
        "runtime-audit",
        "prepare-probe",
        "run-fmriprep",
    }
    output_spaces = string_list("output_spaces")
    if "output_spaces" in merged_values and not output_spaces:
        raise ValueError("output_spaces must include at least one non-empty string")
    output_spaces, cifti_output = _normalize_output_selection(output_spaces, merged_values.get("cifti_output"))
    if fs_no_reconall:
        if cifti_output is not None or any(_is_surface_output_space(value) for value in output_spaces):
            raise ValueError("fs_no_reconall cannot be combined with surface or CIFTI outputs")
        if "output_spaces" not in merged_values:
            output_spaces = list(DEFAULT_FMRIPREP_NO_RECONALL_OUTPUT_SPACES)
    elif fmriprep_signature_defaults_allowed and "cifti_output" not in merged_values:
        cifti_output = DEFAULT_FMRIPREP_CIFTI_OUTPUT

    if command == "process":
        action = "process"
        target = PROCESS_TARGET
    elif command == "runtime-audit":
        action = "runtime-audit"
        target = PROCESS_TARGET
    elif command == "dataset-audit":
        action = "dataset-audit"
        target = PROCESS_TARGET
    elif command == "prepare-probe":
        action = "runtime-audit"
        target = target_hint
    elif command == "xcpd-audit":
        action = "runtime-audit"
        target = "xcpd"
    elif command == "run-fmriprep":
        action = "submit"
        target = "fmriprep"
    elif command == "run-xcpd":
        action = "submit"
        target = "xcpd"
    elif command == "run-status":
        action = "run-status"
        target = target_hint
    else:
        raise ValueError(f"Unsupported command: {command}")

    xcpd_image_value = None
    xcpd_mode_value = "abcd"
    xcpd_min_time_value = 240
    xcpd_min_time_explicit = False
    xcpd_motion_filter_type_value = None
    xcpd_band_stop_min_value = None
    xcpd_band_stop_max_value = None
    xcpd_motion_filter_order_value = None
    xcpd_despike_value = None
    xcpd_task_ids_value: list[str] = []
    xcpd_bids_filter_file_value = None
    xcpd_datasets_value: dict[str, Path | PurePosixPath] = {}
    xcpd_mem_mb_value = None
    xcpd_custom_args_value: dict[str, Any] = {}
    if command in {"xcpd-audit", "run-xcpd"}:
        xcpd_image_value = text("xcpd_image")
        xcpd_mode_value = choice("xcpd_mode", default="abcd", choices=VALID_XCPD_MODES)
        xcpd_min_time_explicit = "xcpd_min_time" in merged_values
        xcpd_min_time_value = _int_value(
            merged_values.get("xcpd_min_time"),
            field="xcpd_min_time",
            default=_xcpd_default_min_time(xcpd_mode_value),
        )
        if "xcpd_motion_filter_type" in merged_values:
            xcpd_motion_filter_type_value = choice(
                "xcpd_motion_filter_type",
                default="none",
                choices=VALID_XCPD_MOTION_FILTER_TYPES,
            )
        xcpd_band_stop_min_value = _float_value(
            merged_values.get("xcpd_band_stop_min"),
            field="xcpd_band_stop_min",
        )
        xcpd_band_stop_max_value = _float_value(
            merged_values.get("xcpd_band_stop_max"),
            field="xcpd_band_stop_max",
        )
        xcpd_motion_filter_order_value = _int_value(
            merged_values.get("xcpd_motion_filter_order"),
            field="xcpd_motion_filter_order",
        )
        if "xcpd_despike" in merged_values:
            xcpd_despike_value = choice("xcpd_despike", default="n", choices=VALID_XCPD_YN_VALUES)
        xcpd_task_ids_value = _normalize_xcpd_task_ids(merged_values.get("xcpd_task_ids"))
        xcpd_bids_filter_file_value = path("xcpd_bids_filter_file", preserve_remote_posix=bool(remote_host))
        xcpd_datasets_value = _normalize_xcpd_datasets(
            merged_values.get("xcpd_datasets"),
            preserve_remote_posix=bool(remote_host),
        )
        xcpd_mem_mb_value = _int_value(merged_values.get("xcpd_mem_mb"), field="xcpd_mem_mb")
        xcpd_custom_args_value = _normalize_xcpd_custom_args(merged_values.get("xcpd_custom_args"))

    templateflow_tool_bins = _normalize_templateflow_tool_bins(string_list("templateflow_tool_bins"))
    if command in TEMPLATEFLOW_TOOL_BIN_REQUIRED_COMMANDS and not templateflow_tool_bins:
        raise ValueError(f"{command} requires --templateflow-tool-bin <bin-dir>")

    request = RequestConfig(
        action=action,
        bids_root=bids_root,
        fmriprep_derivatives=fmriprep_derivatives,
        output_root=path("output_root", preserve_remote_posix=bool(remote_host)),
        target=target,
        remote_host=remote_host,
        subjects=subjects,
        sessions=sessions,
        work_root=path("work_root", preserve_remote_posix=bool(remote_host)),
        log_root=path("log_root", preserve_remote_posix=bool(remote_host)),
        download_root=path("download_root", preserve_remote_posix=bool(remote_host)),
        fs_license=path("fs_license", preserve_remote_posix=bool(remote_host)),
        templateflow_home=path("templateflow_home", preserve_remote_posix=bool(remote_host)),
        templateflow_tool_bins=templateflow_tool_bins,
        fmriprep_image=text("fmriprep_image"),
        xcpd_image=xcpd_image_value,
        container_runtime=choice("container_runtime", default="auto", choices=VALID_RUNTIMES),
        executor_policy=choice("executor_policy", default="auto", choices=VALID_BACKENDS),
        scheduler_partition=text("scheduler_partition"),
        nthreads_per_job=_int_value(merged_values.get("nthreads_per_job"), field="nthreads_per_job"),
        omp_nthreads=_int_value(merged_values.get("omp_nthreads"), field="omp_nthreads"),
        slurm_mem_gb=_int_value(merged_values.get("slurm_mem_gb"), field="slurm_mem_gb"),
        max_jobs=_int_value(merged_values.get("max_jobs"), field="max_jobs"),
        fs_no_reconall=fs_no_reconall,
        skip_bids_validation=boolean("skip_bids_validation", default=False),
        task_id=text("task_id") if fmriprep_run_parameters_allowed else None,
        echo_idx=(
            _int_value(merged_values.get("echo_idx"), field="echo_idx")
            if fmriprep_run_parameters_allowed
            else None
        ),
        anat_only=boolean("anat_only", default=False) if fmriprep_run_parameters_allowed else False,
        fmriprep_custom_args=(
            _normalize_fmriprep_custom_args(merged_values.get("fmriprep_custom_args"))
            if fmriprep_run_parameters_allowed
            else {}
        ),
        output_spaces=output_spaces or list(DEFAULT_FMRIPREP_OUTPUT_SPACES),
        cifti_output=cifti_output,
        xcpd_mode=xcpd_mode_value,
        xcpd_min_time=xcpd_min_time_value,
        xcpd_min_time_explicit=xcpd_min_time_explicit,
        xcpd_motion_filter_type=xcpd_motion_filter_type_value,
        xcpd_band_stop_min=xcpd_band_stop_min_value,
        xcpd_band_stop_max=xcpd_band_stop_max_value,
        xcpd_motion_filter_order=xcpd_motion_filter_order_value,
        xcpd_despike=xcpd_despike_value,
        xcpd_task_ids=xcpd_task_ids_value,
        xcpd_bids_filter_file=xcpd_bids_filter_file_value,
        xcpd_datasets=xcpd_datasets_value,
        xcpd_mem_mb=xcpd_mem_mb_value,
        xcpd_custom_args=xcpd_custom_args_value,
        wsl_vhdx_path=path("wsl_vhdx_path", preserve_remote_posix=bool(remote_host)),
        windows_host_drive=text("windows_host_drive"),
        docker_wsl_storage_path=path("docker_wsl_storage_path", preserve_remote_posix=bool(remote_host)),
        run_id=text("run_id") or default_run_id(command),
    )
    validate_remote_request_paths(request)
    return request


def _request_fields(command: str) -> set[str]:
    fields = COMMAND_REQUEST_FIELDS.get(command)
    if fields is None:
        raise ValueError(f"Unsupported command: {command}")
    return fields


def _cli_request_overrides(args: argparse.Namespace) -> dict[str, Any]:
    values: dict[str, Any] = {}
    custom_args: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key in CONTROL_ARGUMENT_FIELDS:
            continue
        if key == "xcpd_dataset_items":
            values["xcpd_datasets"] = _xcpd_dataset_items_to_mapping(value)
            continue
        if key == "fmriprep_custom_arg_items":
            _merge_custom_arg_mapping(
                custom_args,
                _custom_arg_items_to_mapping(value, "--fmriprep-custom-arg"),
                "--fmriprep-custom-arg",
            )
            continue
        if key == "xcpd_custom_arg_items":
            values["xcpd_custom_args"] = _custom_arg_items_to_mapping(value, "--xcpd-custom-arg")
            continue
        values[key] = value
    if custom_args:
        values["fmriprep_custom_args"] = custom_args
    return values


def _xcpd_dataset_items_to_mapping(values: Any) -> dict[str, str]:
    items = _string_list_value(values, "xcpd_dataset_items")
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError("--xcpd-dataset must use alias=/path")
        alias, path = item.split("=", 1)
        alias = alias.strip()
        path = path.strip()
        if not alias or not path:
            raise ValueError("--xcpd-dataset must use alias=/path")
        result[alias] = path
    return result


def _custom_arg_items_to_mapping(values: Any, option_name: str) -> dict[str, Any]:
    items = _string_list_value(values, option_name)
    result: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"{option_name} must use key=value")
        raw_key, raw_value = item.split("=", 1)
        key = raw_key.strip().replace("-", "_")
        if not key:
            raise ValueError(f"{option_name} must include a non-empty key")
        if key in result:
            raise ValueError(f"Duplicate custom argument: {raw_key.strip()}")
        result[key] = _custom_arg_literal(raw_value.strip())
    return result


def _custom_arg_literal(value: str) -> Any:
    if value == "":
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _merge_custom_arg_mapping(target: dict[str, Any], incoming: dict[str, Any], option_name: str) -> None:
    for key, value in incoming.items():
        if key in target:
            raise ValueError(f"{option_name} duplicates custom argument: {key}")
        target[key] = value


def _text_value(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    stripped = value.strip()
    return stripped or None


def _string_list_value(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"{field} must be a string or list of strings")
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"{field} must be a string or list of strings")
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _bool_value(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a boolean")


def _normalize_fmriprep_custom_args(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("fmriprep_custom_args must be a mapping")
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("fmriprep_custom_args keys must be strings")
        key = raw_key.strip().replace("-", "_")
        if key in REJECTED_FMRIPREP_CUSTOM_KEYS or key not in FMRIPREP_CUSTOM_ARG_SPECS:
            raise ValueError(f"Unsupported fMRIPrep custom argument: {raw_key}")
        _, expected_type = FMRIPREP_CUSTOM_ARG_SPECS[key]
        normalized[key] = _coerce_custom_arg_value(raw_value, key, expected_type)
    return normalized


def _normalize_xcpd_datasets(value: Any, *, preserve_remote_posix: bool) -> dict[str, Path | PurePosixPath]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("xcpd_datasets must be a mapping")
    normalized: dict[str, Path | PurePosixPath] = {}
    for raw_alias, raw_path in value.items():
        if not isinstance(raw_alias, str):
            raise ValueError("xcpd_datasets aliases must be strings")
        alias = raw_alias.strip()
        if not _valid_xcpd_dataset_alias(alias):
            raise ValueError(f"Invalid XCP-D dataset alias: {raw_alias}")
        path = path_value(_text_value(raw_path, f"xcpd_datasets.{alias}"), preserve_remote_posix=preserve_remote_posix)
        if path is None:
            raise ValueError(f"xcpd_datasets.{alias} must be a path")
        normalized[alias] = path
    return normalized


def _normalize_xcpd_task_ids(value: Any) -> list[str]:
    return [item.removeprefix("task-") for item in _string_list_value(value, "xcpd_task_ids")]


def _valid_xcpd_dataset_alias(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value))


def _normalize_xcpd_custom_args(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("xcpd_custom_args must be a mapping")
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("xcpd_custom_args keys must be strings")
        key = raw_key.strip().replace("-", "_")
        if key in REJECTED_XCPD_CUSTOM_KEYS or key not in XCPD_CUSTOM_ARG_SPECS:
            raise ValueError(f"Unsupported XCP-D custom argument: {raw_key}")
        _, expected_type = XCPD_CUSTOM_ARG_SPECS[key]
        normalized[key] = _coerce_xcpd_custom_arg_value(raw_value, key, expected_type)
    return normalized


def _coerce_xcpd_custom_arg_value(value: Any, key: str, expected_type: type | tuple[type, ...]) -> Any:
    if expected_type is bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"xcpd_custom_args.{key} must be a boolean")
    if expected_type is int:
        if isinstance(value, int) and not isinstance(value, bool):
            if key == "verbose" and value < 0:
                raise ValueError("xcpd_custom_args.verbose must be a non-negative integer")
            return value
        raise ValueError(f"xcpd_custom_args.{key} must be an integer")
    if expected_type is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError(f"xcpd_custom_args.{key} must be a number")
    if expected_type is str:
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if key == "nuisance_regressors" and _looks_like_path(text):
                raise ValueError("xcpd_custom_args.nuisance_regressors must be a built-in strategy token")
            return text
        raise ValueError(f"xcpd_custom_args.{key} must be a string")
    if expected_type is list:
        return _string_list_value(value, f"xcpd_custom_args.{key}")
    if isinstance(expected_type, tuple):
        if str in expected_type and isinstance(value, str) and value.strip():
            return value.strip()
        if int in expected_type and isinstance(value, int) and not isinstance(value, bool):
            return value
        if float in expected_type and isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    raise ValueError(f"xcpd_custom_args.{key} has invalid type")


def _looks_like_path(value: str) -> bool:
    lowered = value.lower()
    return "/" in value or "\\" in value or lowered.endswith((".yaml", ".yml"))


def _coerce_custom_arg_value(value: Any, key: str, expected_type: type | tuple[type, ...]) -> Any:
    if expected_type is bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"fmriprep_custom_args.{key} must be a boolean")
    if expected_type is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ValueError(f"fmriprep_custom_args.{key} must be an integer")
    if expected_type is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        raise ValueError(f"fmriprep_custom_args.{key} must be a number")
    if expected_type is str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError(f"fmriprep_custom_args.{key} must be a string")
    if expected_type is list:
        return _string_list_value(value, f"fmriprep_custom_args.{key}")
    if isinstance(expected_type, tuple):
        if int in expected_type and isinstance(value, int) and not isinstance(value, bool):
            return value
        if float in expected_type and isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if str in expected_type and isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Unsupported fMRIPrep custom argument type: {key}")


def _normalize_templateflow_tool_bins(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        path = value.rstrip("/\\")
        if (
            path
            and not _templateflow_tool_bin_value_is_windows(path)
            and _templateflow_tool_bin_value_is_path(path)
            and _templateflow_tool_bin_leaf(path) != "bin"
        ):
            path = str(_templateflow_tool_bin_path(path) / "bin")
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def _templateflow_tool_bin_path(value: str) -> FilesystemPath | PureWindowsPath:
    if _templateflow_tool_bin_value_is_windows(value):
        return PureWindowsPath(value)
    return FilesystemPath(value)


def _templateflow_tool_bin_leaf(value: str) -> str:
    return _templateflow_tool_bin_path(value).name


def _templateflow_tool_bin_value_is_path(value: str) -> bool:
    return (
        "/" in value
        or "\\" in value
        or value.startswith((".", "~"))
        or (len(value) > 1 and value[1] == ":")
    )


def _templateflow_tool_bin_value_is_windows(value: str) -> bool:
    return "\\" in value or (len(value) > 1 and value[1] == ":")


def _expand_subject_selectors(
    *,
    selectors: list[str],
    subject_file: str | None,
    bids_root: Path | PurePosixPath | None,
    remote_host: str | None,
) -> list[str]:
    raw_selectors = [_normalize_subject_selector(value) for value in selectors]
    raw_selectors.extend(_normalize_subject_selector(value) for value in _subject_selectors_from_file(subject_file))
    raw_selectors = [value for value in raw_selectors if value]
    if not raw_selectors:
        return []
    if not any(_selector_has_wildcard(value) for value in raw_selectors):
        return _dedupe_strings(raw_selectors)

    available_subjects = _discover_available_subject_ids(bids_root=bids_root, remote_host=remote_host)
    expanded: list[str] = []
    for selector in raw_selectors:
        if _selector_has_wildcard(selector):
            matched = [subject_id for subject_id in available_subjects if fnmatch.fnmatchcase(subject_id, selector)]
            if not matched:
                raise ValueError(f"Subject selector '{selector}' matched no subjects under {bids_root}")
            expanded.extend(matched)
            continue
        expanded.append(selector)
    return _dedupe_strings(expanded)


def _subject_selectors_from_file(subject_file: str | None) -> list[str]:
    if subject_file is None:
        return []
    target = FilesystemPath(subject_file)
    if not target.exists():
        raise ValueError(f"Subject selector file does not exist: {target}")
    selectors: list[str] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            selectors.append(candidate)
    return selectors


def _normalize_subject_selector(value: str) -> str:
    return value.strip().removeprefix("sub-")


def _selector_has_wildcard(value: str) -> bool:
    return any(token in value for token in ("*", "?", "["))


def _discover_available_subject_ids(
    *,
    bids_root: Path | PurePosixPath | None,
    remote_host: str | None,
) -> list[str]:
    if bids_root is None:
        raise ValueError("bids_root is required when subject selectors contain wildcards")
    if remote_host:
        return _remote_available_subject_ids(bids_root, remote_host)
    return _local_available_subject_ids(bids_root)


def _local_available_subject_ids(bids_root: Path | PurePosixPath) -> list[str]:
    subject_ids: list[str] = []
    for match in glob_paths(str(bids_root / "sub-*")):
        match_path = FilesystemPath(str(match))
        if match_path.is_dir() and match_path.name.startswith("sub-"):
            subject_ids.append(match_path.name.removeprefix("sub-"))
    return sorted(_dedupe_strings(subject_ids))


def _remote_available_subject_ids(bids_root: Path | PurePosixPath, remote_host: str) -> list[str]:
    pattern = str(bids_root / "sub-*")
    probe = probe_remote_dataset(remote_host, glob_patterns=[pattern], include_image_metadata=False)
    subject_ids: list[str] = []
    for raw_path in probe.get("globs", {}).get(pattern, []):
        path = PurePosixPath(str(raw_path))
        if path.parent == bids_root and path.name.startswith("sub-"):
            subject_ids.append(path.name.removeprefix("sub-"))
    return sorted(_dedupe_strings(subject_ids))


def _dedupe_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def _normalize_output_selection(output_spaces: list[str], raw_cifti_output: Any) -> tuple[list[str], str | None]:
    cifti_output = _normalize_cifti_output(raw_cifti_output)
    normal_output_spaces: list[str] = []
    for output_space in output_spaces:
        alias_value = CIFTI_OUTPUT_SPACE_ALIASES.get(output_space)
        if alias_value is not None:
            cifti_output = alias_value
            continue
        if output_space.startswith("cifti:"):
            raise ValueError(f"Unsupported CIFTI output-space alias: {output_space}")
        normal_output_spaces.append(output_space)
    return normal_output_spaces, cifti_output


def _normalize_cifti_output(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("cifti_output must be one of: 91k")
    normalized = value.strip()
    if normalized not in VALID_CIFTI_OUTPUTS:
        raise ValueError("cifti_output must be one of: 91k")
    return normalized


def _is_surface_output_space(value: str) -> bool:
    return value == "fsnative" or value.startswith("fsLR") or value.startswith("fsaverage")


def _int_value(value: Any, *, field: str, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc


def _float_value(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc


def _xcpd_default_min_time(mode: str) -> int:
    return 0 if mode == "nichart" else 240


def _native_windows_mnt_path(value: str) -> str | None:
    if os.name != "nt":
        return None
    match = re.fullmatch(r"/mnt/([A-Za-z])/(.+)", value)
    if match is None:
        return None
    drive, tail = match.groups()
    windows_tail = tail.replace("/", "\\")
    return f"{drive.upper()}:\\{windows_tail}"
