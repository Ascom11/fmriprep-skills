"""Public CLI for fmri process routing, audits, and execution."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from fmri_core import runtime_probe
from fmri_core.audit import (
    archived_artifact_path,
    build_dataset_audit_artifacts,
    dataset_audit_status,
    latest_audit_path,
    load_stage_artifact,
    load_stage_artifacts,
    new_audit_id,
    runtime_proof_id,
    runtime_proofs_path,
    runtime_audit_status,
    validate_stage_artifact,
    write_dataset_audit_debug_artifact,
    write_stage_artifact,
)
from fmri_core.dataset_audit import audit_dataset, audit_xcpd_derivatives, discover_xcpd_derivative_subjects
from fmri_core.issue_codes import XCPD_ISSUE_CODES, issue_bucket_findings
from fmri_core.image_audit import image_entry_ready
from fmri_core.models import (
    ProgressCallback,
    RequestConfig,
    SubjectEntry,
    VALID_BACKENDS,
    VALID_RUNTIMES,
    VALID_XCPD_MOTION_FILTER_TYPES,
    VALID_XCPD_MODES,
    VALID_XCPD_YN_VALUES,
    validate_remote_request_paths,
)
from fmri_core.monitor import HARD_MAX_LOG_LINES, HARD_MAX_PATHS, collect_run_status
from fmri_core.path_probe import RemotePathProbeError, VALID_PATH_NAMES, run_path_probe
from fmri_core.runtime_audit import audit_runtime
from fmri_core.runtime_proofs import (
    expand_runtime_audit_with_proofs,
    reusable_runtime_proofs,
    write_runtime_component_proofs,
)
from fmri_core.templateflow_audit import (
    TEMPLATEFLOW_CACHE_PREPARE_CODE,
    TEMPLATEFLOW_CONTAINER_IMPORT_PREPARE_CODE,
    TEMPLATEFLOW_UNVERIFIED_WARNING_CODE,
    required_templateflow_templates,
    templateflow_container_import_ready,
    templateflow_diagnostics,
)
from fmri_core.shell import (
    path_writable,
    run_command,
    shell_command,
)
import fmri_process.execution_flow as execution_flow
import fmri_process.xcpd_context as xcpd_context
from fmri_process.request_config import (
    PROCESS_TARGET,
    VALID_CIFTI_OUTPUTS,
    default_run_id,
    explicit_request_fields,
    path_probe_values_from_args,
    request_from_args,
)

NEXT_ACTION_PROCESS = "process"
NEXT_ACTION_DATASET_AUDIT = "dataset-audit"
NEXT_ACTION_RUNTIME_AUDIT = "runtime-audit"
NEXT_ACTION_PREPARE_RUNTIME = "prepare-runtime"
NEXT_ACTION_RUN_FMRIPREP = "run-fmriprep"
NEXT_ACTION_RUN_XCPD = "run-xcpd"
XCPD_DATASET_AUDIT_COMMAND = execution_flow.XCPD_DATASET_AUDIT_COMMAND
XCPD_DATASET_AUDIT_DEBUG_COMMAND = execution_flow.XCPD_DATASET_AUDIT_DEBUG_COMMAND
XCPD_RUNTIME_AUDIT_COMMAND = execution_flow.XCPD_RUNTIME_AUDIT_COMMAND
BIDS_ROOT_HELP = "BIDS dataset root."
FMRIPREP_DERIVATIVES_HELP = "fMRIPrep derivatives root used as XCP-D input."
OUTPUT_ROOT_HELP = "Output root. Defaults to <bids_root>/derivatives."
SUBJECT_SELECTOR_HELP = "Subject selector(s); repeatable, wildcards allowed."
SUBJECT_FILE_HELP = (
    "Local text file with one subject selector per nonempty line. "
    "Blank lines and # comments are ignored; values may use sub-* wildcards and combine with --subject."
)
REMOTE_HOST_HELP = "Remote host name."
SESSION_HELP = "Optional session filter(s)."
WORK_ROOT_HELP = "Work directory override."
LOG_ROOT_HELP = "Log directory override."
DOWNLOAD_ROOT_HELP = "Download/cache root override for prepared images and TemplateFlow."
FS_LICENSE_HELP = "FreeSurfer license path."
TEMPLATEFLOW_PATH_HELP = "TemplateFlow cache path override."
TEMPLATEFLOW_TOOL_BIN_HELP = "Bin directory for commands used to check TemplateFlow; repeat for multiple PATH prefixes."
FMRIPREP_IMAGE_HELP = "fMRIPrep image path or docker:// ref."
XCPD_IMAGE_HELP = "XCP-D image path or docker:// ref."
CONTAINER_RUNTIME_HELP = "Container runtime override."
EXECUTOR_POLICY_HELP = "Execution backend override."
SCHEDULER_PARTITION_HELP = "Scheduler partition override."
NTHREADS_PER_JOB_HELP = "Threads per job."
OMP_NTHREADS_HELP = "OMP threads per job."
SLURM_MEM_GB_HELP = "Memory per job in GB."
MAX_JOBS_HELP = "Max concurrent jobs."
SKIP_BIDS_VALIDATION_HELP = "Skip BIDS validation."
OUTPUT_SPACES_HELP = "fMRIPrep output space token(s); repeatable."
CIFTI_OUTPUT_HELP = "fMRIPrep CIFTI output density."
FS_NO_RECONALL_HELP = "Run fMRIPrep with --fs-no-reconall; requires volume-only outputs."
TASK_ID_HELP = "fMRIPrep task selector."
ECHO_IDX_HELP = "fMRIPrep echo index selector."
ANAT_ONLY_HELP = "Run fMRIPrep anatomical-only workflow."
XCPD_MODE_HELP = "XCP-D mode override."
XCPD_MIN_TIME_HELP = "XCP-D minimum duration threshold."
XCPD_TASK_ID_HELP = "XCP-D task selector; repeatable."
XCPD_BIDS_FILTER_FILE_HELP = "XCP-D BIDS filter JSON path."
XCPD_DATASET_HELP = "Extra XCP-D dataset binding as alias=/path; repeatable."
XCPD_MEM_MB_HELP = "XCP-D internal memory limit in MB."
FMRIPREP_CUSTOM_ARG_HELP = "fMRIPrep custom argument as key=value; repeatable."
XCPD_CUSTOM_ARG_HELP = "XCP-D custom argument as key=value; repeatable."
XCPD_MOTION_FILTER_TYPE_HELP = "XCP-D motion filter type."
XCPD_BAND_STOP_MIN_HELP = "XCP-D motion filter lower frequency in breaths per minute."
XCPD_BAND_STOP_MAX_HELP = "XCP-D motion filter upper frequency in breaths per minute."
XCPD_MOTION_FILTER_ORDER_HELP = "XCP-D motion filter order."
XCPD_DESPIKE_HELP = "XCP-D despike setting."
WSL_VHDX_PATH_HELP = "WSL VHDX path override."
WINDOWS_HOST_DRIVE_HELP = "Windows host drive override."
DOCKER_WSL_STORAGE_PATH_HELP = "Docker WSL storage path override."
RUN_ID_HELP = "Run/log grouping key."
RESUME_FROM_HELP = "Explicit archived audit artifact path, audit dir, or audit id."
AUTO_APPROVE_HELP = "Run after clean audits only."
REAUDIT_RUNTIME_HELP = "Re-run runtime audit while reusing a validated dataset audit."
REUSE_DATASET_FROM_HELP = "Reuse a validated dataset audit from an audit id, audit dir, or dataset-audit.json."
REUSE_CONTEXT_FROM_HELP = (
    "Seed missing XCP-D audit context from a saved fMRIPrep audit id, audit dir, or artifact."
)
PREPARE_PROBE_FROM_HELP = "Runtime audit id, audit dir, or runtime-audit.json to probe after manual preparation."
PREPARE_PROBE_KIND_HELP = "Prepared target to probe."
DATASET_AFFECTING_FIELDS = {"bids_root", "remote_host", "subjects", "sessions"}
DATASET_STORAGE_ESTIMATE_FIELDS = {"output_spaces", "cifti_output", "fs_no_reconall", "task_id", "echo_idx", "anat_only"}
RUNTIME_AFFECTING_FIELDS = {
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
    "nthreads_per_job",
    "omp_nthreads",
    "slurm_mem_gb",
    "max_jobs",
    "fs_no_reconall",
    "task_id",
    "echo_idx",
    "anat_only",
    "fmriprep_custom_args",
    "wsl_vhdx_path",
    "windows_host_drive",
    "docker_wsl_storage_path",
}
EXECUTION_ONLY_FIELDS = {
    "work_root",
    "log_root",
    "scheduler_partition",
    "skip_bids_validation",
    "output_spaces",
    "cifti_output",
    "run_id",
}

class CLIArgumentParseError(ValueError):
    """Raised when CLI input fails argparse validation."""


class CLIArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that reports parse errors without exiting the process."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # noqa: D401
        raise CLIArgumentParseError(message)


@dataclass
class _ProcessInputs:
    request: RequestConfig
    dataset_artifact: dict[str, Any]
    dataset_debug_artifact: dict[str, Any] | None
    runtime_artifact: dict[str, Any]
    process_audit_id: str
    last_completed_stage: str | None
    auto_approve: bool
    user_pinned_fields: list[str]
    worker_results: list[dict[str, Any]]

def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run the requested workflow stage."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "--prepare-runtime":
        emit_json(
            {
                "status": "error",
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "command": None,
                "message": "unrecognized arguments: --prepare-runtime",
            }
        )
        return 1
    argv = raw_argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except CLIArgumentParseError as exc:
        emit_json(
            {
                "status": "error",
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "command": _requested_command(argv),
                "message": str(exc),
            }
        )
        return 1
    progress = _stderr_progress
    if args.command == "path-probe":
        try:
            path_probe_values = path_probe_values_from_args(args)
            emit_json(run_path_probe(**path_probe_values))
            return 0
        except (CLIArgumentParseError, ValueError, RemotePathProbeError) as exc:
            emit_json({"status": "error", "command": args.command, "error": str(exc)})
            return 1
    try:
        request = request_from_args(args)
    except ValueError as exc:
        emit_json(
            {
                "status": "error",
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "command": args.command,
                "message": str(exc),
            }
        )
        return 1
    if args.command == "process":
        return run_process_command(
            request,
            auto_approve=bool(getattr(args, "auto_approve", False)),
            reaudit_runtime_requested=bool(getattr(args, "reaudit_runtime", False)),
            reuse_dataset_from=getattr(args, "reuse_dataset_from", None),
            user_pinned_fields=explicit_request_fields(args),
            progress=progress,
        )
    if args.command == "prepare-probe":
        return run_prepare_probe_command(
            request,
            from_runtime_audit=getattr(args, "from_runtime_audit", None),
            kind=getattr(args, "kind", "all"),
        )
    if args.command == "runtime-audit":
        return run_runtime_audit_command(request, progress=progress)
    if args.command == "dataset-audit":
        return run_dataset_audit_command(request, progress=progress)
    if args.command == "xcpd-audit":
        return run_xcpd_audit_command(
            request,
            reuse_context_from=getattr(args, "reuse_context_from", None),
            user_pinned_fields=explicit_request_fields(args),
            progress=progress,
        )
    if args.command == "run-status":
        run_status_target = getattr(args, "target", None)
        if run_status_target is None and "target" in explicit_request_fields(args):
            run_status_target = request.target
        return run_status_command(
            request,
            audit_id=getattr(args, "audit_id", None),
            submission_id=getattr(args, "submission_id", None),
            target=run_status_target,
            log_lines=int(getattr(args, "log_lines", 20)),
            max_paths=int(getattr(args, "max_paths", 20)),
        )
    if args.command == "run-fmriprep" and not str(getattr(args, "resume_from", "") or "").strip():
        emit_json(
            {
                "status": "error",
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "command": "run-fmriprep",
                "message": "run-fmriprep requires --resume-from <audit_id|audit_dir|artifact.json>",
            }
        )
        return 1
    return run_execute_command(
        request,
        command=args.command,
        resume_from=getattr(args, "resume_from", None),
        user_pinned_fields=explicit_request_fields(args),
        progress=progress,
    )


def _stderr_progress(event: dict[str, Any]) -> None:
    stage = str(event.get("stage") or "progress")
    message = str(event.get("message") or event.get("status") or "").strip() or "progress"
    print(f"[fmri-process] {stage}: {message}", file=sys.stderr, flush=True)


def _write_permission_blocker_payload(request: RequestConfig, command: str) -> dict[str, Any] | None:
    checks = _early_write_permission_checks(request, command)
    failures: list[dict[str, str]] = []
    for label, path in checks:
        if request.remote_host:
            probe = _remote_write_path_status(path, request.remote_host)
            if probe["status"] == "writable":
                continue
            if probe["status"] == "probe_failed":
                return {
                    "status": "blocked",
                    "command": command,
                    "summary": {
                        "blockers": ["remote_runtime_probe_failed"],
                        "findings": issue_bucket_findings(blockers=["remote_runtime_probe_failed"]),
                        "remote_probe": probe["remote_probe"],
                    },
                    "artifacts": {},
                }
        elif path_writable(path, None):
            continue
        failures.append({"label": label, "path": str(path)})
    if not failures:
        return None
    return {
        "status": "blocked",
        "command": command,
        "summary": {
            "blockers": ["runtime_write_permission_denied"],
            "findings": issue_bucket_findings(blockers=["runtime_write_permission_denied"]),
            "write_permission_failures": failures,
        },
        "artifacts": {},
    }


def _early_write_permission_checks(request: RequestConfig, command: str) -> list[tuple[str, Any]]:
    checks: list[tuple[str, Any]] = [("output_root", request.resolve_output_root())]
    if command in {"process", "runtime-audit"}:
        checks.append(("log_root", request.resolve_log_root()))
    return checks


def _remote_write_path_status(path: Any, remote_host: str) -> dict[str, Any]:
    command = "\n".join(
        [
            f"target={shlex.quote(str(path))}",
            "if [ -e \"$target\" ]; then",
            "  if [ -d \"$target\" ] && [ -w \"$target\" ] && [ -x \"$target\" ]; then",
            "    printf 'writable\\n'",
            "  else",
            "    printf 'unwritable\\n'",
            "  fi",
            "  exit 0",
            "fi",
            "parent=$(dirname \"$target\")",
            "while [ ! -e \"$parent\" ]; do",
            "  next=$(dirname \"$parent\")",
            "  if [ \"$next\" = \"$parent\" ]; then",
            "    printf 'unwritable\\n'",
            "    exit 0",
            "  fi",
            "  parent=\"$next\"",
            "done",
            "if [ -d \"$parent\" ] && [ -w \"$parent\" ] && [ -x \"$parent\" ]; then",
            "  printf 'writable\\n'",
            "else",
            "  printf 'unwritable\\n'",
            "fi",
        ]
    )
    try:
        result = run_command(shell_command(command), remote_host=remote_host, check=False, timeout=20)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {
            "status": "probe_failed",
            "remote_probe": {"probe_ok": False, "error": _compact_remote_probe_error(str(exc))},
        }
    stdout = (result.stdout or "").strip().splitlines()
    status = stdout[-1].strip() if stdout else ""
    if result.returncode != 0 or status not in {"writable", "unwritable"}:
        return {
            "status": "probe_failed",
            "remote_probe": {
                "probe_ok": False,
                "returncode": result.returncode,
                "error": _compact_remote_probe_error((result.stderr or "").strip() or (result.stdout or "").strip())
                or f"remote output-root probe exited with {result.returncode}",
                "stdout": _compact_remote_probe_error(result.stdout or ""),
                "stderr": _compact_remote_probe_error(result.stderr or ""),
            },
        }
    return {"status": status, "remote_probe": {"probe_ok": True}}


def _compact_remote_probe_error(text: str) -> str:
    return " ".join(text.split())[:500]


def build_parser() -> argparse.ArgumentParser:
    parser = CLIArgumentParser(
        prog="fmri-process",
        description="fMRI preprocessing CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser(
        "process",
        help="Recommended entrypoint for fresh requests.",
        description="Run the default preprocessing router.",
    )
    add_process_stage_arguments(process_parser)
    add_process_arguments(process_parser)

    runtime_audit_parser = subparsers.add_parser(
        "runtime-audit",
        help="Run runtime checks only.",
        description="Run runtime checks only.",
    )
    add_runtime_audit_arguments(runtime_audit_parser)

    prepare_probe_parser = subparsers.add_parser(
        "prepare-probe",
        help="Probe prepared runtime targets from a saved runtime audit.",
        description="Probe prepared runtime targets from a saved runtime audit.",
    )
    add_prepare_probe_arguments(prepare_probe_parser)

    dataset_audit_parser = subparsers.add_parser(
        "dataset-audit",
        help="Run dataset checks only.",
        description="Run dataset checks only.",
    )
    add_locator_arguments(dataset_audit_parser)
    add_fmriprep_run_parameter_arguments(dataset_audit_parser, include_custom_args=False)
    add_output_selection_arguments(dataset_audit_parser)
    dataset_audit_parser.add_argument(
        "--fs-no-reconall",
        action="store_true",
        default=argparse.SUPPRESS,
        help=FS_NO_RECONALL_HELP,
    )

    xcpd_audit = subparsers.add_parser(
        "xcpd-audit",
        help="Run XCP-D checks only.",
        description="Run XCP-D checks only.",
    )
    add_xcpd_stage_arguments(xcpd_audit)

    run_fmriprep = subparsers.add_parser(
        "run-fmriprep",
        help="Submit/execute fMRIPrep stage from existing audit artifacts.",
        description="Submit or execute fMRIPrep.",
    )
    add_run_fmriprep_arguments(run_fmriprep)

    run_xcpd = subparsers.add_parser(
        "run-xcpd",
        help="Submit/execute XCP-D stage from existing audit artifacts.",
        description="Submit or execute XCP-D.",
    )
    add_run_xcpd_arguments(run_xcpd)
    run_xcpd.add_argument("--resume-from", default=argparse.SUPPRESS, help=RESUME_FROM_HELP)

    run_status = subparsers.add_parser(
        "run-status",
        help="Inspect the latest launched run without submitting work.",
        description="Inspect saved launch evidence and bounded logs.",
    )
    add_run_status_arguments(run_status)

    path_probe = subparsers.add_parser(
        "path-probe",
        help="Probe user-provided path hints before workflow commands.",
        description="Probe user-provided path hints before workflow commands.",
    )
    add_path_probe_arguments(path_probe)

    return parser


def add_artifact_locator_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--bids-root", default=argparse.SUPPRESS, help=BIDS_ROOT_HELP)
    sub.add_argument("--output-root", default=argparse.SUPPRESS, help=OUTPUT_ROOT_HELP)
    sub.add_argument("--remote-host", default=argparse.SUPPRESS, help=REMOTE_HOST_HELP)


def add_subject_scope_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--subject",
        dest="subjects",
        action="extend",
        nargs="+",
        default=argparse.SUPPRESS,
        help=SUBJECT_SELECTOR_HELP,
    )
    sub.add_argument("--subject-file", default=argparse.SUPPRESS, help=SUBJECT_FILE_HELP)
    sub.add_argument(
        "--session",
        dest="sessions",
        action="extend",
        nargs="+",
        default=argparse.SUPPRESS,
        help=SESSION_HELP,
    )


def add_locator_arguments(sub: argparse.ArgumentParser) -> None:
    add_artifact_locator_arguments(sub)
    add_subject_scope_arguments(sub)


def add_output_selection_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--output-spaces",
        dest="output_spaces",
        action="extend",
        nargs="+",
        default=argparse.SUPPRESS,
        help=OUTPUT_SPACES_HELP,
    )
    sub.add_argument(
        "--cifti-output",
        choices=VALID_CIFTI_OUTPUTS,
        default=argparse.SUPPRESS,
        help=CIFTI_OUTPUT_HELP,
    )


def add_fmriprep_run_parameter_arguments(sub: argparse.ArgumentParser, *, include_custom_args: bool = True) -> None:
    sub.add_argument("--task-id", default=argparse.SUPPRESS, help=TASK_ID_HELP)
    sub.add_argument("--echo-idx", type=int, default=argparse.SUPPRESS, help=ECHO_IDX_HELP)
    sub.add_argument("--anat-only", action="store_true", default=argparse.SUPPRESS, help=ANAT_ONLY_HELP)
    if not include_custom_args:
        return
    sub.add_argument(
        "--fmriprep-custom-arg",
        dest="fmriprep_custom_arg_items",
        action="append",
        default=argparse.SUPPRESS,
        help=FMRIPREP_CUSTOM_ARG_HELP,
    )


def add_process_stage_arguments(sub: argparse.ArgumentParser) -> None:
    add_locator_arguments(sub)
    sub.add_argument("--work-root", default=argparse.SUPPRESS, help=WORK_ROOT_HELP)
    sub.add_argument("--log-root", default=argparse.SUPPRESS, help=LOG_ROOT_HELP)
    sub.add_argument("--download-root", default=argparse.SUPPRESS, help=DOWNLOAD_ROOT_HELP)
    sub.add_argument("--fs-license", default=argparse.SUPPRESS, help=FS_LICENSE_HELP)
    sub.add_argument("--templateflow-home", default=argparse.SUPPRESS, help=TEMPLATEFLOW_PATH_HELP)
    sub.add_argument(
        "--templateflow-tool-bin",
        dest="templateflow_tool_bins",
        action="append",
        default=argparse.SUPPRESS,
        help=TEMPLATEFLOW_TOOL_BIN_HELP,
    )
    sub.add_argument("--fmriprep-image", default=argparse.SUPPRESS, help=FMRIPREP_IMAGE_HELP)
    sub.add_argument(
        "--container-runtime",
        choices=VALID_RUNTIMES,
        default=argparse.SUPPRESS,
        help=CONTAINER_RUNTIME_HELP,
    )
    sub.add_argument(
        "--executor-policy",
        choices=VALID_BACKENDS,
        default=argparse.SUPPRESS,
        help=EXECUTOR_POLICY_HELP,
    )
    sub.add_argument("--scheduler-partition", default=argparse.SUPPRESS, help=SCHEDULER_PARTITION_HELP)
    sub.add_argument("--nthreads-per-job", type=int, default=argparse.SUPPRESS, help=NTHREADS_PER_JOB_HELP)
    sub.add_argument("--omp-nthreads", type=int, default=argparse.SUPPRESS, help=OMP_NTHREADS_HELP)
    sub.add_argument("--slurm-mem-gb", type=int, default=argparse.SUPPRESS, help=SLURM_MEM_GB_HELP)
    sub.add_argument("--max-jobs", type=int, default=argparse.SUPPRESS, help=MAX_JOBS_HELP)
    sub.add_argument("--fs-no-reconall", action="store_true", default=argparse.SUPPRESS, help=FS_NO_RECONALL_HELP)
    sub.add_argument("--skip-bids-validation", action="store_true", default=argparse.SUPPRESS, help=SKIP_BIDS_VALIDATION_HELP)
    add_fmriprep_run_parameter_arguments(sub)
    add_output_selection_arguments(sub)
    sub.add_argument("--wsl-vhdx-path", default=argparse.SUPPRESS, help=WSL_VHDX_PATH_HELP)
    sub.add_argument("--windows-host-drive", default=argparse.SUPPRESS, help=WINDOWS_HOST_DRIVE_HELP)
    sub.add_argument("--docker-wsl-storage-path", default=argparse.SUPPRESS, help=DOCKER_WSL_STORAGE_PATH_HELP)
    sub.add_argument("--run-id", default=argparse.SUPPRESS, help=RUN_ID_HELP)


def add_runtime_audit_arguments(sub: argparse.ArgumentParser) -> None:
    add_artifact_locator_arguments(sub)
    sub.add_argument("--work-root", default=argparse.SUPPRESS, help=WORK_ROOT_HELP)
    sub.add_argument("--log-root", default=argparse.SUPPRESS, help=LOG_ROOT_HELP)
    sub.add_argument("--download-root", default=argparse.SUPPRESS, help=DOWNLOAD_ROOT_HELP)
    sub.add_argument("--fs-license", default=argparse.SUPPRESS, help=FS_LICENSE_HELP)
    sub.add_argument("--templateflow-home", default=argparse.SUPPRESS, help=TEMPLATEFLOW_PATH_HELP)
    sub.add_argument(
        "--templateflow-tool-bin",
        dest="templateflow_tool_bins",
        action="append",
        default=argparse.SUPPRESS,
        help=TEMPLATEFLOW_TOOL_BIN_HELP,
    )
    sub.add_argument("--fmriprep-image", default=argparse.SUPPRESS, help=FMRIPREP_IMAGE_HELP)
    sub.add_argument(
        "--container-runtime",
        choices=VALID_RUNTIMES,
        default=argparse.SUPPRESS,
        help=CONTAINER_RUNTIME_HELP,
    )
    sub.add_argument(
        "--executor-policy",
        choices=VALID_BACKENDS,
        default=argparse.SUPPRESS,
        help=EXECUTOR_POLICY_HELP,
    )
    sub.add_argument("--skip-bids-validation", action="store_true", default=argparse.SUPPRESS, help=SKIP_BIDS_VALIDATION_HELP)
    sub.add_argument("--nthreads-per-job", type=int, default=argparse.SUPPRESS, help=NTHREADS_PER_JOB_HELP)
    sub.add_argument("--omp-nthreads", type=int, default=argparse.SUPPRESS, help=OMP_NTHREADS_HELP)
    sub.add_argument("--slurm-mem-gb", type=int, default=argparse.SUPPRESS, help=SLURM_MEM_GB_HELP)
    sub.add_argument("--max-jobs", type=int, default=argparse.SUPPRESS, help=MAX_JOBS_HELP)
    sub.add_argument("--fs-no-reconall", action="store_true", default=argparse.SUPPRESS, help=FS_NO_RECONALL_HELP)
    sub.add_argument("--wsl-vhdx-path", default=argparse.SUPPRESS, help=WSL_VHDX_PATH_HELP)
    sub.add_argument("--windows-host-drive", default=argparse.SUPPRESS, help=WINDOWS_HOST_DRIVE_HELP)
    sub.add_argument("--docker-wsl-storage-path", default=argparse.SUPPRESS, help=DOCKER_WSL_STORAGE_PATH_HELP)
    add_fmriprep_run_parameter_arguments(sub)
    add_output_selection_arguments(sub)


def add_xcpd_stage_arguments(sub: argparse.ArgumentParser) -> None:
    add_run_xcpd_arguments(sub)
    add_subject_scope_arguments(sub)
    sub.add_argument("--reuse-context-from", default=argparse.SUPPRESS, help=REUSE_CONTEXT_FROM_HELP)


def add_run_fmriprep_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--bids-root", default=argparse.SUPPRESS, help=BIDS_ROOT_HELP)
    sub.add_argument("--output-root", default=argparse.SUPPRESS, help=OUTPUT_ROOT_HELP)
    sub.add_argument("--remote-host", default=argparse.SUPPRESS, help=REMOTE_HOST_HELP)
    sub.add_argument("--resume-from", default=argparse.SUPPRESS, help=RESUME_FROM_HELP)
    sub.add_argument("--run-id", default=argparse.SUPPRESS, help=RUN_ID_HELP)


def add_run_xcpd_arguments(sub: argparse.ArgumentParser) -> None:
    add_artifact_locator_arguments(sub)
    sub.add_argument("--fmriprep-derivatives", default=argparse.SUPPRESS, help=FMRIPREP_DERIVATIVES_HELP)
    sub.add_argument("--work-root", default=argparse.SUPPRESS, help=WORK_ROOT_HELP)
    sub.add_argument("--log-root", default=argparse.SUPPRESS, help=LOG_ROOT_HELP)
    sub.add_argument("--download-root", default=argparse.SUPPRESS, help=DOWNLOAD_ROOT_HELP)
    sub.add_argument("--fs-license", default=argparse.SUPPRESS, help=FS_LICENSE_HELP)
    sub.add_argument("--templateflow-home", default=argparse.SUPPRESS, help=TEMPLATEFLOW_PATH_HELP)
    sub.add_argument(
        "--templateflow-tool-bin",
        dest="templateflow_tool_bins",
        action="append",
        default=argparse.SUPPRESS,
        help=TEMPLATEFLOW_TOOL_BIN_HELP,
    )
    sub.add_argument("--xcpd-image", default=argparse.SUPPRESS, help=XCPD_IMAGE_HELP)
    sub.add_argument(
        "--container-runtime",
        choices=VALID_RUNTIMES,
        default=argparse.SUPPRESS,
        help=CONTAINER_RUNTIME_HELP,
    )
    sub.add_argument(
        "--executor-policy",
        choices=VALID_BACKENDS,
        default=argparse.SUPPRESS,
        help=EXECUTOR_POLICY_HELP,
    )
    sub.add_argument("--scheduler-partition", default=argparse.SUPPRESS, help=SCHEDULER_PARTITION_HELP)
    sub.add_argument("--nthreads-per-job", type=int, default=argparse.SUPPRESS, help=NTHREADS_PER_JOB_HELP)
    sub.add_argument("--omp-nthreads", type=int, default=argparse.SUPPRESS, help=OMP_NTHREADS_HELP)
    sub.add_argument("--slurm-mem-gb", type=int, default=argparse.SUPPRESS, help=SLURM_MEM_GB_HELP)
    sub.add_argument("--max-jobs", type=int, default=argparse.SUPPRESS, help=MAX_JOBS_HELP)
    sub.add_argument("--xcpd-mode", choices=VALID_XCPD_MODES, default=argparse.SUPPRESS, help=XCPD_MODE_HELP)
    sub.add_argument("--xcpd-min-time", type=int, default=argparse.SUPPRESS, help=XCPD_MIN_TIME_HELP)
    sub.add_argument(
        "--xcpd-task-id",
        dest="xcpd_task_ids",
        action="append",
        default=argparse.SUPPRESS,
        help=XCPD_TASK_ID_HELP,
    )
    sub.add_argument("--xcpd-bids-filter-file", default=argparse.SUPPRESS, help=XCPD_BIDS_FILTER_FILE_HELP)
    sub.add_argument(
        "--xcpd-dataset",
        dest="xcpd_dataset_items",
        action="append",
        default=argparse.SUPPRESS,
        help=XCPD_DATASET_HELP,
    )
    sub.add_argument("--xcpd-mem-mb", type=int, default=argparse.SUPPRESS, help=XCPD_MEM_MB_HELP)
    sub.add_argument(
        "--xcpd-custom-arg",
        dest="xcpd_custom_arg_items",
        action="append",
        default=argparse.SUPPRESS,
        help=XCPD_CUSTOM_ARG_HELP,
    )
    sub.add_argument(
        "--xcpd-motion-filter-type",
        choices=VALID_XCPD_MOTION_FILTER_TYPES,
        default=argparse.SUPPRESS,
        help=XCPD_MOTION_FILTER_TYPE_HELP,
    )
    sub.add_argument("--xcpd-band-stop-min", type=float, default=argparse.SUPPRESS, help=XCPD_BAND_STOP_MIN_HELP)
    sub.add_argument("--xcpd-band-stop-max", type=float, default=argparse.SUPPRESS, help=XCPD_BAND_STOP_MAX_HELP)
    sub.add_argument(
        "--xcpd-motion-filter-order",
        type=int,
        default=argparse.SUPPRESS,
        help=XCPD_MOTION_FILTER_ORDER_HELP,
    )
    sub.add_argument("--xcpd-despike", choices=VALID_XCPD_YN_VALUES, default=argparse.SUPPRESS, help=XCPD_DESPIKE_HELP)
    sub.add_argument("--wsl-vhdx-path", default=argparse.SUPPRESS, help=WSL_VHDX_PATH_HELP)
    sub.add_argument("--windows-host-drive", default=argparse.SUPPRESS, help=WINDOWS_HOST_DRIVE_HELP)
    sub.add_argument("--docker-wsl-storage-path", default=argparse.SUPPRESS, help=DOCKER_WSL_STORAGE_PATH_HELP)
    sub.add_argument("--run-id", default=argparse.SUPPRESS, help=RUN_ID_HELP)


def add_run_status_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--bids-root", default=argparse.SUPPRESS, help=BIDS_ROOT_HELP)
    sub.add_argument("--output-root", default=argparse.SUPPRESS, help=OUTPUT_ROOT_HELP)
    sub.add_argument("--remote-host", default=argparse.SUPPRESS, help=REMOTE_HOST_HELP)
    sub.add_argument("--target", choices=("fmriprep", "xcpd"), default=argparse.SUPPRESS, help="Execution target to inspect.")
    sub.add_argument("--audit-id", default=argparse.SUPPRESS, help="Audit id to inspect. Defaults to latest.")
    sub.add_argument("--submission-id", default=argparse.SUPPRESS, help="Submission id to inspect. Defaults to latest.")
    sub.add_argument("--log-lines", type=int, default=20, help=f"Maximum lines to read from each log; hard cap {HARD_MAX_LOG_LINES}.")
    sub.add_argument("--max-paths", type=int, default=20, help=f"Maximum log, report, or crash paths to report; hard cap {HARD_MAX_PATHS}.")


def add_path_probe_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", choices=("fmriprep", "xcpd"), default=argparse.SUPPRESS, help="Pipeline path hints to probe.")
    sub.add_argument("--bids-root", default=argparse.SUPPRESS, help=BIDS_ROOT_HELP)
    sub.add_argument("--user-dataset-path", default=argparse.SUPPRESS, help="User-provided dataset path or parent directory.")
    sub.add_argument("--output-root", default=argparse.SUPPRESS, help=OUTPUT_ROOT_HELP)
    sub.add_argument("--templateflow-home", default=argparse.SUPPRESS, help="User-provided TemplateFlow path or parent directory.")
    sub.add_argument("--fs-license", default=argparse.SUPPRESS, help=FS_LICENSE_HELP)
    sub.add_argument("--fmriprep-image", default=argparse.SUPPRESS, help=FMRIPREP_IMAGE_HELP)
    sub.add_argument("--xcpd-image", default=argparse.SUPPRESS, help=XCPD_IMAGE_HELP)
    sub.add_argument("--remote-host", default=argparse.SUPPRESS, help=REMOTE_HOST_HELP)
    sub.add_argument(
        "--require-path",
        dest="required_paths",
        action="append",
        choices=sorted(VALID_PATH_NAMES),
        default=argparse.SUPPRESS,
        help="Require an applicable path category to resolve before workflow CLI execution.",
    )


def add_prepare_probe_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--bids-root", default=argparse.SUPPRESS, help=BIDS_ROOT_HELP)
    sub.add_argument("--output-root", default=argparse.SUPPRESS, help=OUTPUT_ROOT_HELP)
    sub.add_argument("--remote-host", default=argparse.SUPPRESS, help=REMOTE_HOST_HELP)
    sub.add_argument("--target", choices=("fmriprep", "xcpd"), default=argparse.SUPPRESS, help="Runtime audit target to verify.")
    sub.add_argument("--from-runtime-audit", required=True, default=argparse.SUPPRESS, help=PREPARE_PROBE_FROM_HELP)
    sub.add_argument(
        "--kind",
        choices=("image", "templateflow", "license", "all"),
        default="all",
        help=PREPARE_PROBE_KIND_HELP,
    )


def add_process_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--auto-approve", action="store_true", default=argparse.SUPPRESS, help=AUTO_APPROVE_HELP)
    sub.add_argument("--reaudit-runtime", action="store_true", default=argparse.SUPPRESS, help=REAUDIT_RUNTIME_HELP)
    sub.add_argument("--reuse-dataset-from", default=argparse.SUPPRESS, help=REUSE_DATASET_FROM_HELP)


def run_process_command(
    request: RequestConfig,
    *,
    auto_approve: bool,
    reaudit_runtime_requested: bool,
    reuse_dataset_from: str | None,
    user_pinned_fields: list[str],
    progress: ProgressCallback | None = None,
) -> int:
    output_blocker = _write_permission_blocker_payload(request, "process")
    if output_blocker is not None:
        emit_json(output_blocker)
        return 1
    if reuse_dataset_from is not None and not reaudit_runtime_requested:
        emit_json(
            {
                "status": "error",
                "command": "process",
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "message": "process --reuse-dataset-from requires --reaudit-runtime",
            }
        )
        return 1
    if reuse_dataset_from is None and reaudit_runtime_requested:
        emit_json(
            {
                "status": "error",
                "command": "process",
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "message": "process --reaudit-runtime requires --reuse-dataset-from",
            }
        )
        return 1
    inputs = (
        _reused_dataset_process_inputs(
            request,
            reuse_dataset_from=reuse_dataset_from,
            auto_approve=auto_approve,
            user_pinned_fields=user_pinned_fields,
            progress=progress,
        )
        if reuse_dataset_from is not None
        else _fresh_process_inputs(
            request,
            auto_approve=auto_approve,
            user_pinned_fields=user_pinned_fields,
            progress=progress,
        )
    )
    if isinstance(inputs, dict):
        emit_json(inputs["payload"])
        return inputs["exit_code"]
    return _run_process_flow(inputs, progress=progress)


def _fresh_process_inputs(
    request: RequestConfig,
    *,
    auto_approve: bool,
    user_pinned_fields: list[str],
    progress: ProgressCallback | None = None,
) -> _ProcessInputs:
    current_audit_id = new_audit_id()
    worker_results: list[dict[str, Any]] = []

    dataset_result = _dataset_audit_result(request, audit_id=current_audit_id, progress=progress)
    dataset_artifact = dataset_result["artifact"]
    worker_results.append({"command": "dataset-audit", "status": dataset_result["payload"]["status"]})

    runtime_result = _runtime_audit_result(request, audit_id=current_audit_id, progress=progress)
    runtime_artifact = runtime_result["artifact"]
    worker_results.append({"command": "runtime-audit", "status": runtime_result["payload"]["status"]})

    return _ProcessInputs(
        request=request,
        dataset_artifact=dataset_artifact,
        dataset_debug_artifact=None,
        runtime_artifact=runtime_artifact,
        process_audit_id=current_audit_id,
        last_completed_stage="runtime-audit",
        auto_approve=auto_approve,
        user_pinned_fields=user_pinned_fields,
        worker_results=worker_results,
    )


def _reused_dataset_process_inputs(
    request: RequestConfig,
    *,
    reuse_dataset_from: str,
    auto_approve: bool,
    user_pinned_fields: list[str],
    progress: ProgressCallback | None = None,
) -> _ProcessInputs | dict[str, Any]:
    try:
        locator_request, source_audit_id = execution_flow.artifact_execution_locator(
            request,
            resume_from=reuse_dataset_from,
            option_name="--reuse-dataset-from",
        )
    except ValueError as exc:
        return execution_flow.argument_error_payload("process", str(exc))

    loaded_artifacts = load_stage_artifacts(
        locator_request,
        ["dataset-audit", "dataset-audit-debug", "runtime-audit"],
        audit_id=source_audit_id,
    )
    source_dataset_signature = (
        loaded_artifacts["dataset-audit"].get("request_signature")
        if isinstance(loaded_artifacts.get("dataset-audit"), dict)
        else None
    )
    source_remote_host = (
        execution_flow.optional_text(source_dataset_signature.get("remote_host"))
        if isinstance(source_dataset_signature, dict)
        else locator_request.remote_host
    )
    dataset_validation_request = replace(locator_request, remote_host=source_remote_host)
    dataset_reaudit_reason: str | None = None
    dataset_artifact, result = execution_flow.validated_stage_artifact(
        dataset_validation_request,
        command="process",
        artifact_command="dataset-audit",
        artifact=loaded_artifacts["dataset-audit"],
        audit_id=source_audit_id,
        load_missing=False,
    )
    if result is not None:
        storage_only_match = (
            validate_stage_artifact(
                dataset_validation_request,
                "dataset-audit",
                loaded_artifacts["dataset-audit"],
                require_storage_estimate_signature=False,
            )
            is None
        )
        if not storage_only_match:
            return result
        dataset_reaudit_reason = "storage_estimate_signature_mismatch"

    current_audit_id = new_audit_id()
    if dataset_reaudit_reason is not None:
        dataset_result = _dataset_audit_result(locator_request, audit_id=current_audit_id, progress=progress)
        copied_dataset = dataset_result["artifact"]
        copied_dataset_debug = load_stage_artifact(locator_request, "dataset-audit-debug", audit_id=current_audit_id)
    else:
        dataset_debug_artifact, result = execution_flow.validated_stage_artifact(
            dataset_validation_request,
            command="process",
            artifact_command="dataset-audit-debug",
            artifact=loaded_artifacts["dataset-audit-debug"],
            audit_id=source_audit_id,
            load_missing=False,
        )
        if result is not None:
            return result
        copied_dataset = write_stage_artifact(
            locator_request,
            command="dataset-audit",
            status=str(dataset_artifact.get("status") or "blocked"),
            stage_payload=dict(dataset_artifact.get("dataset_audit") or {}),
            audit_id=current_audit_id,
        )
        copied_dataset_debug = write_dataset_audit_debug_artifact(
            locator_request,
            status=str(dataset_debug_artifact.get("status") or copied_dataset.get("status") or "blocked"),
            stage_payload=dict(dataset_debug_artifact.get("dataset_audit") or {}),
            audit_id=current_audit_id,
        )
    reusable_proofs = reusable_runtime_proofs(
        locator_request,
        loaded_artifacts.get("runtime-audit"),
        source_audit_id=source_audit_id,
    )
    runtime_result = _runtime_audit_result(
        locator_request,
        audit_id=current_audit_id,
        progress=progress,
        reusable_proofs=reusable_proofs,
    )
    dataset_worker_result = {
        "command": "dataset-audit",
        "status": copied_dataset["status"],
        "reuse_source_audit_id": source_audit_id,
    }
    if dataset_reaudit_reason is not None:
        dataset_worker_result["reaudit_reason"] = dataset_reaudit_reason
    worker_results = [
        dataset_worker_result,
        {"command": "runtime-audit", "status": runtime_result["payload"]["status"]},
    ]
    return _ProcessInputs(
        request=locator_request,
        dataset_artifact=copied_dataset,
        dataset_debug_artifact=copied_dataset_debug,
        runtime_artifact=runtime_result["artifact"],
        process_audit_id=current_audit_id,
        last_completed_stage="runtime-audit",
        auto_approve=auto_approve,
        user_pinned_fields=user_pinned_fields,
        worker_results=worker_results,
    )


def run_runtime_audit_command(request: RequestConfig, *, progress: ProgressCallback | None = None) -> int:
    output_blocker = _write_permission_blocker_payload(request, "runtime-audit")
    if output_blocker is not None:
        emit_json(output_blocker)
        return 1
    result = _runtime_audit_result(request, progress=progress)
    emit_json(result["payload"])
    return result["exit_code"]


def run_dataset_audit_command(request: RequestConfig, *, progress: ProgressCallback | None = None) -> int:
    if request.bids_root is None:
        emit_json(
            {
                "status": "error",
                "command": "dataset-audit",
                "error_type": "argument_parse",
                "error_code": "missing_required_input",
                "message": "bids_root is required",
            }
        )
        return 1
    output_blocker = _write_permission_blocker_payload(request, "dataset-audit")
    if output_blocker is not None:
        emit_json(output_blocker)
        return 1
    result = _dataset_audit_result(request, progress=progress)
    emit_json(result["payload"])
    return result["exit_code"]


def run_prepare_probe_command(
    request: RequestConfig,
    *,
    from_runtime_audit: str | None,
    kind: str,
) -> int:
    if not str(from_runtime_audit or "").strip():
        result = execution_flow.argument_error_payload("prepare-probe", "prepare-probe requires --from-runtime-audit")
        emit_json(result["payload"])
        return result["exit_code"]
    try:
        locator_request, audit_id = execution_flow.artifact_execution_locator(
            request,
            resume_from=from_runtime_audit,
            option_name="--from-runtime-audit",
        )
    except ValueError as exc:
        result = execution_flow.argument_error_payload("prepare-probe", str(exc))
        emit_json(result["payload"])
        return result["exit_code"]

    artifact_command = XCPD_RUNTIME_AUDIT_COMMAND if request.target == "xcpd" else "runtime-audit"
    runtime_artifact = load_stage_artifact(locator_request, artifact_command, audit_id=audit_id)
    if runtime_artifact is None:
        _, result = execution_flow.validated_stage_artifact(
            locator_request,
            command="prepare-probe",
            artifact_command=artifact_command,
            artifact=None,
            audit_id=audit_id,
            load_missing=False,
        )
        emit_json(result["payload"])
        return result["exit_code"]
    effective_request = (
        execution_flow.xcpd_request_from_runtime_artifact(locator_request, runtime_artifact=runtime_artifact)
        if request.target == "xcpd"
        else execution_flow.fmriprep_request_from_artifacts(
            locator_request,
            runtime_artifact=runtime_artifact,
            dataset_artifact=None,
        )
    )
    runtime_artifact, result = execution_flow.validated_stage_artifact(
        effective_request,
        command="prepare-probe",
        artifact_command=artifact_command,
        artifact=runtime_artifact,
        audit_id=audit_id,
        load_missing=False,
    )
    if result is not None:
        emit_json(result["payload"])
        return result["exit_code"]

    runtime_audit = expand_runtime_audit_with_proofs(effective_request, dict(runtime_artifact.get("runtime_audit") or {}))
    if runtime_audit.get("proof_resolution_error"):
        payload = execution_flow.artifact_blocked_payload(
            effective_request,
            command="prepare-probe",
            blocker=str(runtime_audit["proof_resolution_error"]),
            artifact_command=artifact_command,
            artifact=runtime_artifact,
        )
        emit_json(payload)
        return 1
    checks = _prepare_probe_checks(
        effective_request,
        runtime_audit,
        kind,
    )
    failed_codes = _dedupe_strings(
        [str(check["code"]) for check in checks if check["status"] == "failed" and check.get("code")]
    )
    status = "ready" if not failed_codes else "blocked"
    payload = {
        "status": status,
        "command": "prepare-probe",
        "kind": kind,
        "audit_id": runtime_artifact["audit_id"],
        "target": effective_request.target,
        "summary": {
            "checks": checks,
            "blockers": failed_codes,
            "findings": issue_bucket_findings(blockers=failed_codes),
        },
        "artifacts": execution_flow.stage_artifacts(effective_request, {artifact_command: runtime_artifact}),
    }
    emit_json(payload)
    return 0 if status == "ready" else 1


def _prepare_probe_checks(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    kind: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    selected = {kind} if kind != "all" else {"image", "templateflow", "license"}
    if request.target == "xcpd" and request.fs_license is None:
        selected.discard("license")
    if "image" in selected:
        checks.append(_prepare_probe_image_check(request, runtime_audit))
    if "templateflow" in selected:
        checks.extend(_prepare_probe_templateflow_checks(request, runtime_audit))
    if "license" in selected:
        checks.append(_prepare_probe_license_check(request))
    return checks


def _prepare_probe_image_check(request: RequestConfig, runtime_audit: dict[str, Any]) -> dict[str, Any]:
    selected_runtime = execution_flow.optional_text(runtime_audit.get("selected_runtime"))
    pipeline = request.target
    image = _runtime_audit_image(runtime_audit, request, pipeline)
    ready = bool(image and image_entry_ready(request, selected_runtime, pipeline, image))
    return {
        "kind": "image",
        "target": image,
        "runtime": selected_runtime,
        "status": "ready" if ready else "failed",
        "code": None if ready else f"missing_{pipeline}_image",
    }


def _prepare_probe_templateflow_checks(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    templateflow_home = execution_flow.optional_text(runtime_audit.get("templateflow_home"))
    required_templates = [
        str(value)
        for value in runtime_audit.get("required_templateflow_templates", required_templateflow_templates(request))
    ]
    if not required_templates:
        return [
            {
                "kind": "templateflow",
                "target": templateflow_home,
                "status": "ready",
                "code": None,
                "detail": "no templates required",
            }
        ]
    remote_probe = None
    if request.remote_host:
        remote_probe = runtime_probe.probe_remote_runtime(
            request,
            required_templates=required_templates,
        )
    elif isinstance(runtime_audit.get("remote_probe"), dict):
        remote_probe = runtime_audit.get("remote_probe")
    diagnostics = templateflow_diagnostics(
        request,
        templateflow_home=templateflow_home,
        remote_probe=remote_probe,
        required_templates=required_templates,
    )
    entries_ready = diagnostics.get("status") == "ready"
    entry_failure_code = None
    if not entries_ready:
        if diagnostics.get("status") == "deferred":
            entry_failure_code = TEMPLATEFLOW_UNVERIFIED_WARNING_CODE
        else:
            entry_failure_code = TEMPLATEFLOW_CACHE_PREPARE_CODE
    entry_check = {
        "kind": "templateflow",
        "target": templateflow_home,
        "required_templates": required_templates,
        "status": "ready" if entries_ready else "failed",
        "code": entry_failure_code,
        "diagnostics": diagnostics,
    }
    if entry_failure_code == TEMPLATEFLOW_UNVERIFIED_WARNING_CODE:
        entry_check["detail"] = "TemplateFlow folder is visible, but required tool proof is still unverified."
    checks = [entry_check]
    if not entries_ready:
        checks.append(
            {
                "kind": "templateflow-container-import",
                "target": templateflow_home,
                "image": _runtime_audit_image(runtime_audit, request, request.target),
                "runtime": execution_flow.optional_text(runtime_audit.get("selected_runtime")),
                "status": "skipped",
                "code": None,
                "detail": "Skipped until the TemplateFlow files are proven ready.",
            }
        )
        return checks
    selected_runtime = execution_flow.optional_text(runtime_audit.get("selected_runtime"))
    image = _runtime_audit_image(runtime_audit, request, request.target)
    container_ready = bool(
        image
        and templateflow_home
        and templateflow_container_import_ready(
            request,
            selected_runtime,
            image,
            templateflow_home,
            required_templates,
        )
    )
    container_diagnostics = {
        "status": "ready" if container_ready else "failed",
        "probe_mode": "container_import",
        "home": templateflow_home,
        "required_templates": required_templates,
        "failed_template": None,
        "failed_path": None,
        "failure_reason": None if container_ready else "container_import_failed",
    }
    checks.append(
        {
            "kind": "templateflow-container-import",
            "target": templateflow_home,
            "image": image,
            "runtime": selected_runtime,
            "status": "ready" if container_ready else "failed",
            "code": None if container_ready else TEMPLATEFLOW_CONTAINER_IMPORT_PREPARE_CODE,
            "diagnostics": container_diagnostics,
        }
    )
    return checks


def _prepare_probe_license_check(request: RequestConfig) -> dict[str, Any]:
    target = str(request.fs_license) if request.fs_license is not None else None
    ready = bool(request.fs_license is not None and _path_is_readable_file(request.fs_license, request.remote_host))
    return {
        "kind": "license",
        "target": target,
        "status": "ready" if ready else "failed",
        "code": None if ready else "missing_fs_license",
    }


def _runtime_audit_image(runtime_audit: dict[str, Any], request: RequestConfig, pipeline: str) -> str | None:
    selected_runtime = execution_flow.optional_text(runtime_audit.get("selected_runtime"))
    if selected_runtime in {"apptainer", "singularity"}:
        target = _prepare_requirement_image_target(runtime_audit, pipeline)
        if target:
            return target
    resolved_images = runtime_audit.get("resolved_images")
    if isinstance(resolved_images, dict):
        image = execution_flow.optional_text(resolved_images.get(pipeline))
        if image:
            return image
    return request.xcpd_image if pipeline == "xcpd" else request.fmriprep_image


def _prepare_requirement_image_target(runtime_audit: dict[str, Any], pipeline: str) -> str | None:
    requirements = runtime_audit.get("prepare_requirements")
    if not isinstance(requirements, list):
        return None
    for item in requirements:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "image" or item.get("pipeline") != pipeline:
            continue
        target = execution_flow.optional_text(item.get("target"))
        if target:
            return target
    return None


def _path_is_readable_file(path: Path | PurePosixPath, remote_host: str | None) -> bool:
    if remote_host:
        command = f"test -f {shlex.quote(str(path))} && test -r {shlex.quote(str(path))}"
        try:
            result = run_command(shell_command(command), remote_host=remote_host, check=False, timeout=20)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
    return Path(path).is_file() and os.access(path, os.R_OK)


def run_xcpd_audit_command(
    request: RequestConfig,
    *,
    reuse_context_from: str | None = None,
    user_pinned_fields: list[str] | None = None,
    progress: ProgressCallback | None = None,
) -> int:
    subject_scope: list[SubjectEntry] | None = None
    reusable_proofs: dict[str, dict[str, Any]] | None = None
    if str(reuse_context_from or "").strip():
        seeded = xcpd_context.seed_from_fmriprep_context(
            request,
            reuse_context_from=str(reuse_context_from),
            user_pinned_fields=user_pinned_fields or [],
        )
        if isinstance(seeded, dict):
            emit_json(seeded["payload"])
            return seeded["exit_code"]
        request = seeded.request
        subject_scope = seeded.subject_scope
        reusable_proofs = seeded.reusable_proofs
    audit_id = new_audit_id()
    dataset_result = _xcpd_dataset_audit_result(
        request,
        audit_id=audit_id,
        progress=progress,
        subject_scope=subject_scope,
    )
    runtime_result = _xcpd_runtime_audit_result(
        request,
        audit_id=audit_id,
        progress=progress,
        reusable_proofs=reusable_proofs,
    )
    dataset_artifact = dataset_result["artifact"]
    runtime_artifact = runtime_result["artifact"]
    payload = {
        "status": _xcpd_audit_status(dataset_artifact, runtime_artifact),
        "command": "xcpd-audit",
        "next_action": _xcpd_next_action(dataset_artifact, runtime_artifact),
        "summary": {
            "dataset": execution_flow.dataset_stage_summary(dataset_artifact),
            "runtime": execution_flow.runtime_stage_summary(runtime_artifact),
        },
        "artifacts": execution_flow.stage_artifacts(
            request,
            {
                XCPD_DATASET_AUDIT_COMMAND: dataset_artifact,
                XCPD_RUNTIME_AUDIT_COMMAND: runtime_artifact,
            },
        ),
    }
    payload["artifacts"]["xcpd_dataset_audit_debug_archive"] = str(
        archived_artifact_path(request, XCPD_DATASET_AUDIT_DEBUG_COMMAND, audit_id=audit_id)
    )
    emit_json(payload)
    return 0 if payload["status"] == "ready" else 1


def run_status_command(
    request: RequestConfig,
    *,
    audit_id: str | None,
    submission_id: str | None,
    target: str | None,
    log_lines: int,
    max_paths: int,
) -> int:
    emit_json(
        collect_run_status(
            request,
            audit_id=audit_id,
            submission_id=submission_id,
            target=target,
            log_lines=log_lines,
            max_paths=max_paths,
        )
    )
    return 0


def run_execute_command(
    request: RequestConfig,
    *,
    command: str,
    resume_from: str | None = None,
    user_pinned_fields: list[str] | None = None,
    progress: ProgressCallback | None = None,
) -> int:
    if not str(resume_from or "").strip():
        required_command = "run-xcpd" if command == "run-xcpd" else "run-fmriprep"
        result = {
            "exit_code": 1,
            "payload": {
                "status": "error",
                "command": command,
                "error_type": "argument_parse",
                "error_code": "invalid_arguments",
                "message": f"{required_command} requires --resume-from <audit_id|audit_dir|artifact.json>",
            },
        }
    elif command == "run-xcpd":
        request, artifacts, result = execution_flow.ready_xcpd_execution_artifacts(
            request,
            command=command,
            resume_from=resume_from,
            user_pinned_fields=user_pinned_fields or [],
        )
    else:
        request, artifacts, result = execution_flow.ready_fmriprep_execution_artifacts(
            request,
            command=command,
            resume_from=resume_from,
        )
    if result is None:
        result = execution_flow.execute_from_artifacts(
            request,
            command=command,
            runtime_artifact=artifacts["runtime_artifact"],
            dataset_artifact=artifacts["dataset_artifact"],
            dataset_debug_artifact=artifacts["dataset_debug_artifact"],
            progress=progress,
        )
    execution_flow.print_execution_failure_summary(result["payload"])
    emit_json(result["payload"])
    return result["exit_code"]


def _run_process_flow(inputs: _ProcessInputs, *, progress: ProgressCallback | None = None) -> int:
    request = inputs.request
    dataset_artifact = inputs.dataset_artifact
    dataset_debug_artifact = inputs.dataset_debug_artifact
    runtime_artifact = inputs.runtime_artifact
    process_audit_id = inputs.process_audit_id
    last_completed_stage = inputs.last_completed_stage
    auto_approve = inputs.auto_approve
    user_pinned_fields = inputs.user_pinned_fields
    worker_results = inputs.worker_results

    decision = _process_decision(
        dataset_artifact=dataset_artifact,
        runtime_artifact=runtime_artifact,
        auto_approve=auto_approve,
    )

    if decision["action"] == "run-fmriprep":
        artifacts, execute_result = execution_flow.ready_execution_artifacts(
            request,
            command="run-fmriprep",
            runtime_artifact=runtime_artifact,
            dataset_artifact=dataset_artifact,
            dataset_debug_artifact=dataset_debug_artifact,
            audit_id=process_audit_id,
        )
        if execute_result is None:
            execute_result = execution_flow.execute_from_artifacts(
                request,
                command="run-fmriprep",
                runtime_artifact=artifacts["runtime_artifact"],
                dataset_artifact=artifacts["dataset_artifact"],
                dataset_debug_artifact=artifacts["dataset_debug_artifact"],
                progress=progress,
            )
        execution_flow.print_execution_failure_summary(execute_result["payload"])
        worker_results.append({"command": "run-fmriprep", "status": execute_result["payload"]["status"]})
        last_completed_stage = "run-fmriprep"
        return _finish_process(
            request,
            status=execution_flow.process_execution_status(execute_result),
            next_action=NEXT_ACTION_PROCESS,
            awaiting_user_continue=False,
            auto_approve=auto_approve,
            last_completed_stage=last_completed_stage,
            current_stage="fmri-process",
            paused_reason=None if execute_result["exit_code"] == 0 else "run_fmriprep_blocked",
            gate=decision["gate"],
            payload={
                "dataset": execution_flow.dataset_stage_summary(dataset_artifact),
                "runtime": execution_flow.runtime_stage_summary(runtime_artifact),
                "execution": execute_result["payload"],
            },
            process_audit_id=process_audit_id,
            user_pinned_fields=user_pinned_fields,
            worker_results=worker_results,
            exit_code=execute_result["exit_code"],
        )

    return _finish_process(
        request,
        status=decision["status"],
        next_action=decision["next_action"],
        awaiting_user_continue=decision["awaiting_user_continue"],
        auto_approve=auto_approve,
        last_completed_stage=last_completed_stage,
        current_stage=decision["current_stage"],
        paused_reason=decision["paused_reason"],
        gate=decision["gate"],
        payload=decision["payload"],
        process_audit_id=process_audit_id,
        user_pinned_fields=user_pinned_fields,
        worker_results=worker_results,
        exit_code=0 if decision["status"] == "paused" else 1,
    )


def _finish_process(
    request: RequestConfig,
    *,
    status: str,
    next_action: str,
    awaiting_user_continue: bool,
    auto_approve: bool,
    last_completed_stage: str | None,
    current_stage: str,
    paused_reason: str | None,
    gate: dict[str, bool],
    payload: dict[str, Any],
    process_audit_id: str,
    user_pinned_fields: list[str],
    worker_results: list[dict[str, Any]],
    exit_code: int,
) -> int:
    process_state = {
        "status": status,
        "current_stage": current_stage,
        "last_completed_stage": last_completed_stage,
        "next_action": next_action,
        "paused_reason": paused_reason,
        "awaiting_user_continue": awaiting_user_continue,
        "auto_approve": auto_approve,
        "dataset_execution_clean": gate["dataset_execution_clean"],
        "runtime_execution_clean": gate["runtime_execution_clean"],
        "runtime_prepare_eligible": gate["runtime_prepare_eligible"],
        "audit_id": process_audit_id,
    }
    emit_json(
        {
            "status": status,
            "command": "process",
            "next_action": next_action,
            "awaiting_user_continue": awaiting_user_continue,
            "auto_approve": auto_approve,
            "artifacts": {"latest_audit_index": str(latest_audit_path(request))},
            "process_state": process_state,
            "worker_results": worker_results,
            "payload": payload,
        }
    )
    return exit_code


def _xcpd_audit_status(dataset_artifact: dict[str, Any], runtime_artifact: dict[str, Any]) -> str:
    if execution_flow.artifact_status(dataset_artifact) != "ready":
        return "blocked"
    runtime_status = execution_flow.artifact_status(runtime_artifact)
    if runtime_status == "ready":
        return "ready"
    if execution_flow.runtime_prepare_eligible(runtime_artifact):
        return "needs_prepare"
    return "blocked"


def _xcpd_next_action(dataset_artifact: dict[str, Any], runtime_artifact: dict[str, Any]) -> str:
    if execution_flow.artifact_status(dataset_artifact) != "ready":
        return "xcpd-audit"
    if execution_flow.runtime_prepare_eligible(runtime_artifact):
        return "xcpd-audit"
    if execution_flow.artifact_status(runtime_artifact) == "ready":
        return NEXT_ACTION_RUN_XCPD
    return "xcpd-audit"


def _runtime_audit_result(
    request: RequestConfig,
    *,
    audit_id: str | None = None,
    progress: ProgressCallback | None = None,
    reusable_proofs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_audit_id = audit_id or new_audit_id()
    audit_request = replace(request, action="runtime-audit") if request.action == "process" else request
    full_runtime_audit = audit_runtime(audit_request, progress=progress, reusable_proofs=reusable_proofs)
    runtime_audit = _runtime_artifact_payload(
        audit_request,
        full_runtime_audit,
        reusable_proofs=reusable_proofs,
    )
    status = runtime_audit_status(runtime_audit)
    artifact = write_stage_artifact(
        request,
        command="runtime-audit",
        status=status,
        stage_payload=runtime_audit,
        audit_id=resolved_audit_id,
    )
    payload = {
        "status": status,
        "command": "runtime-audit",
        "summary": execution_flow.runtime_audit_summary(runtime_audit),
        "artifacts": {
            "runtime_audit_archive": str(archived_artifact_path(request, "runtime-audit", audit_id=resolved_audit_id)),
            "runtime_proofs": str(runtime_proofs_path(audit_request)),
            "latest_audit_index": str(latest_audit_path(request)),
        },
    }
    return {
        "exit_code": 0 if status == "ready" else 1,
        "payload": payload,
        "artifact": artifact,
    }


def _dataset_audit_result(
    request: RequestConfig,
    *,
    audit_id: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    resolved_audit_id = audit_id or new_audit_id()
    summary_payload, debug_payload = build_dataset_audit_artifacts(request, audit_dataset(request, progress=progress))
    status = dataset_audit_status(summary_payload)
    artifact = write_stage_artifact(
        request,
        command="dataset-audit",
        status=status,
        stage_payload=summary_payload,
        audit_id=resolved_audit_id,
    )
    write_dataset_audit_debug_artifact(
        request,
        status=status,
        stage_payload=debug_payload,
        audit_id=resolved_audit_id,
    )
    payload = {
        "status": status,
        "command": "dataset-audit",
        "summary": {
            **dict(summary_payload.get("summary") or {}),
            "warnings": list(summary_payload.get("warnings") or []),
            "findings": dict(summary_payload.get("findings") or {}),
            "subject_exclusions": list(summary_payload.get("subject_exclusions") or []),
        },
        "artifacts": {
            "dataset_audit_archive": str(archived_artifact_path(request, "dataset-audit", audit_id=resolved_audit_id)),
            "dataset_audit_debug_archive": str(
                archived_artifact_path(request, "dataset-audit-debug", audit_id=resolved_audit_id)
            ),
            "latest_audit_index": str(latest_audit_path(request)),
        },
    }
    return {
        "exit_code": 0 if status == "ready" else 1,
        "payload": payload,
        "artifact": artifact,
    }


def _runtime_artifact_payload(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    *,
    reusable_proofs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    proof_refs, readiness = write_runtime_component_proofs(request, runtime_audit)
    if request.target == "fmriprep":
        warnings = _without_xcpd_issue_codes(list(runtime_audit.get("warnings") or []))
        prepare_required = _without_xcpd_issue_codes(list(runtime_audit.get("prepare_required") or []))
        blockers = _without_xcpd_issue_codes(list(runtime_audit.get("blockers") or []))
        warning_details = _without_xcpd_details(list(runtime_audit.get("warning_details") or []))
        prepare_required_details = _without_xcpd_details(list(runtime_audit.get("prepare_required_details") or []))
        blocker_details = _without_xcpd_details(list(runtime_audit.get("blocker_details") or []))
    else:
        warnings = list(runtime_audit.get("warnings") or [])
        prepare_required = list(runtime_audit.get("prepare_required") or [])
        blockers = list(runtime_audit.get("blockers") or [])
        warning_details = list(runtime_audit.get("warning_details") or [])
        prepare_required_details = list(runtime_audit.get("prepare_required_details") or [])
        blocker_details = list(runtime_audit.get("blocker_details") or [])
    resource_summary = dict(runtime_audit.get("resources") or {})
    resource_summary_payload = {
        "max_jobs": resource_summary.get("max_jobs"),
        "nthreads_per_job": resource_summary.get("nthreads_per_job"),
        "omp_nthreads": resource_summary.get("omp_nthreads"),
        "slurm_mem_gb": resource_summary.get("slurm_mem_gb"),
    }
    cpu_parallelism = execution_flow.cpu_parallelism_summary(
        resource_summary,
        runtime_audit.get("execution_strategy"),
    )
    if cpu_parallelism is not None:
        resource_summary_payload["cpu_parallelism"] = cpu_parallelism
    payload = {
        "runtime_context": {
            "target": request.target,
            "remote_host": request.remote_host,
            "container_runtime": runtime_audit.get("selected_runtime"),
            "executor_policy": runtime_audit.get("selected_executor_policy"),
            "execution_strategy": runtime_audit.get("execution_strategy"),
        },
        "required_proofs": list(proof_refs),
        "proof_refs": proof_refs,
        "readiness": readiness,
        "resource_summary": resource_summary_payload,
        "required_templateflow_templates": list(runtime_audit.get("required_templateflow_templates") or []),
        "warnings": warnings,
        "prepare_required": prepare_required,
        "prepare_requirements": list(runtime_audit.get("prepare_requirements") or []),
        "blockers": blockers,
        "warning_details": warning_details,
        "prepare_required_details": prepare_required_details,
        "blocker_details": blocker_details,
        "findings": issue_bucket_findings(
            warnings=warnings,
            prepare_required=prepare_required,
            blockers=blockers,
        ),
    }
    reuse_payload = _runtime_proof_reuse_payload(reusable_proofs)
    if reuse_payload is not None:
        payload["runtime_proof_reuse"] = reuse_payload
    return payload


def _runtime_proof_reuse_payload(reusable_proofs: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
    if not reusable_proofs:
        return None
    skipped_by_kind = {
        "environment.local": ["environment_probe"],
        "environment.remote": ["environment_probe"],
        "resources": ["resource_planning"],
        "image.fmriprep": ["image_resolution", "image_validation"],
        "image.xcpd": ["image_resolution", "image_validation"],
        "license.freesurfer": ["fs_license_readability"],
    }
    reused: list[dict[str, Any]] = []
    for kind, proof in sorted(reusable_proofs.items()):
        if kind.startswith("templateflow.template."):
            skipped_checks = ["templateflow_template_tool_proof"]
        else:
            skipped_checks = skipped_by_kind.get(kind)
        if not skipped_checks:
            continue
        status = proof.get("status")
        data = proof.get("data")
        signature = proof.get("signature")
        if not isinstance(status, str) or not isinstance(data, dict) or not isinstance(signature, dict):
            continue
        reused.append(
            {
                "kind": kind,
                "proof_id": runtime_proof_id(kind, signature, status=status, data=data),
                "reused_from_audit_id": proof.get("reused_from_audit_id"),
                "skipped_checks": skipped_checks,
            }
        )
    if not reused:
        return None
    return {"reused_proofs": reused}


def _without_xcpd_issue_codes(values: list[Any]) -> list[Any]:
    return [value for value in values if str(value) not in XCPD_ISSUE_CODES]


def _without_xcpd_details(values: list[Any]) -> list[Any]:
    return [value for value in values if "xcpd" not in str(value).lower() and "xcp-d" not in str(value).lower()]


def _xcpd_runtime_audit_result(
    request: RequestConfig,
    *,
    audit_id: str | None = None,
    progress: ProgressCallback | None = None,
    reusable_proofs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_audit_id = audit_id or new_audit_id()
    audit_request = replace(request, action="runtime-audit", target="xcpd")
    full_runtime_audit = audit_runtime(audit_request, progress=progress, reusable_proofs=reusable_proofs)
    runtime_audit = _runtime_artifact_payload(
        audit_request,
        full_runtime_audit,
        reusable_proofs=reusable_proofs,
    )
    status = runtime_audit_status(runtime_audit)
    artifact = write_stage_artifact(
        audit_request,
        command=XCPD_RUNTIME_AUDIT_COMMAND,
        status=status,
        stage_payload=runtime_audit,
        audit_id=resolved_audit_id,
    )
    payload = {
        "status": status,
        "command": "xcpd-runtime-audit",
        "summary": execution_flow.runtime_audit_summary(runtime_audit),
        "artifacts": {
            "xcpd_runtime_audit_archive": str(
                archived_artifact_path(audit_request, XCPD_RUNTIME_AUDIT_COMMAND, audit_id=resolved_audit_id)
            ),
            "runtime_proofs": str(runtime_proofs_path(audit_request)),
            "latest_audit_index": str(latest_audit_path(audit_request)),
        },
    }
    return {
        "exit_code": 0 if status == "ready" else 1,
        "payload": payload,
        "artifact": artifact,
    }


def _xcpd_dataset_audit_result(
    request: RequestConfig,
    *,
    audit_id: str | None = None,
    progress: ProgressCallback | None = None,
    subject_scope: list[SubjectEntry] | None = None,
) -> dict[str, Any]:
    resolved_audit_id = audit_id or new_audit_id()
    audit_request = replace(request, action="dataset-audit", target="xcpd")
    if subject_scope is None and audit_request.fmriprep_derivatives is not None:
        subject_scope = discover_xcpd_derivative_subjects(audit_request)
    dataset_payload = (
        audit_xcpd_derivatives(audit_request, subject_scope, progress=progress)
        if subject_scope is not None
        else audit_dataset(audit_request, progress=progress)
    )
    summary_payload, debug_payload = build_dataset_audit_artifacts(
        audit_request,
        dataset_payload,
    )
    status = dataset_audit_status(summary_payload)
    artifact = write_stage_artifact(
        audit_request,
        command=XCPD_DATASET_AUDIT_COMMAND,
        status=status,
        stage_payload=summary_payload,
        audit_id=resolved_audit_id,
    )
    write_stage_artifact(
        audit_request,
        command=XCPD_DATASET_AUDIT_DEBUG_COMMAND,
        status=status,
        stage_payload=debug_payload,
        audit_id=resolved_audit_id,
    )
    payload = {
        "status": status,
        "command": "xcpd-dataset-audit",
        "summary": dict(summary_payload.get("summary") or {}),
        "artifacts": {
            "xcpd_dataset_audit_archive": str(
                archived_artifact_path(audit_request, XCPD_DATASET_AUDIT_COMMAND, audit_id=resolved_audit_id)
            ),
            "xcpd_dataset_audit_debug_archive": str(
                archived_artifact_path(audit_request, XCPD_DATASET_AUDIT_DEBUG_COMMAND, audit_id=resolved_audit_id)
            ),
            "latest_audit_index": str(latest_audit_path(audit_request)),
        },
    }
    return {
        "exit_code": 0 if status == "ready" else 1,
        "payload": payload,
        "artifact": artifact,
    }


def _process_decision(
    *,
    dataset_artifact: dict[str, Any],
    runtime_artifact: dict[str, Any],
    auto_approve: bool,
) -> dict[str, Any]:
    payload = {
        "dataset": execution_flow.dataset_stage_summary(dataset_artifact),
        "runtime": execution_flow.runtime_stage_summary(runtime_artifact),
    }
    next_action = _planned_next_action(dataset_artifact=dataset_artifact, runtime_artifact=runtime_artifact)
    gate = _process_gate_snapshot(dataset_artifact=dataset_artifact, runtime_artifact=runtime_artifact)
    dataset_status = execution_flow.artifact_status(dataset_artifact)
    runtime_status = execution_flow.artifact_status(runtime_artifact)

    if dataset_status != "ready":
        return {
            "action": "blocked",
            "status": "blocked",
            "next_action": next_action,
            "paused_reason": "dataset_audit_blocked",
            "awaiting_user_continue": False,
            "current_stage": "fmri-process",
            "gate": gate,
            "payload": payload,
        }
    if runtime_status == "blocked":
        return {
            "action": "blocked",
            "status": "blocked",
            "next_action": next_action,
            "paused_reason": "runtime_audit_blocked",
            "awaiting_user_continue": False,
            "current_stage": "fmri-process",
            "gate": gate,
            "payload": payload,
        }
    if auto_approve and gate["dataset_execution_clean"] and gate["runtime_execution_clean"]:
        return {
            "action": "run-fmriprep",
            "status": "running",
            "next_action": next_action,
            "paused_reason": None,
            "awaiting_user_continue": False,
            "current_stage": "fmri-process",
            "gate": gate,
            "payload": payload,
        }
    if not auto_approve:
        if gate["runtime_prepare_eligible"]:
            return {
                "action": "pause",
                "status": "paused",
                "next_action": NEXT_ACTION_PREPARE_RUNTIME,
                "paused_reason": "runtime_prepare_requires_explicit_prepare",
                "awaiting_user_continue": False,
                "current_stage": "fmri-process",
                "gate": gate,
                "payload": payload,
            }
        return {
            "action": "pause",
            "status": "paused",
            "next_action": next_action,
            "paused_reason": "audit_complete_waiting_for_user_continue",
            "awaiting_user_continue": True,
            "current_stage": "fmri-process",
            "gate": gate,
            "payload": payload,
        }
    return {
        "action": "pause",
        "status": "paused",
        "next_action": next_action,
        "paused_reason": "runtime_prepare_requires_explicit_prepare"
        if gate["runtime_prepare_eligible"]
        else "audit_requires_manual_review",
        "awaiting_user_continue": False if gate["runtime_prepare_eligible"] else True,
        "current_stage": "fmri-process",
        "gate": gate,
        "payload": payload,
    }


def _planned_next_action(*, dataset_artifact: dict[str, Any], runtime_artifact: dict[str, Any]) -> str:
    dataset_status = execution_flow.artifact_status(dataset_artifact)
    runtime_status = execution_flow.artifact_status(runtime_artifact)
    if dataset_status != "ready":
        return NEXT_ACTION_DATASET_AUDIT
    if runtime_status == "blocked":
        return NEXT_ACTION_RUNTIME_AUDIT
    if runtime_status == "needs_prepare":
        return NEXT_ACTION_PREPARE_RUNTIME
    return NEXT_ACTION_RUN_FMRIPREP


def _process_gate_snapshot(
    *,
    dataset_artifact: dict[str, Any],
    runtime_artifact: dict[str, Any],
) -> dict[str, bool]:
    return {
        "dataset_execution_clean": _dataset_execution_clean(dataset_artifact),
        "runtime_execution_clean": _runtime_execution_clean(runtime_artifact),
        "runtime_prepare_eligible": execution_flow.runtime_prepare_eligible(runtime_artifact),
    }


def _dataset_execution_clean(artifact: dict[str, Any]) -> bool:
    return execution_flow.artifact_status(artifact) == "ready"


def _runtime_execution_clean(artifact: dict[str, Any]) -> bool:
    if execution_flow.artifact_status(artifact) != "ready":
        return False
    runtime_audit = artifact.get("runtime_audit") or {}
    return not runtime_audit.get("blockers") and not runtime_audit.get("prepare_required")


def _requested_command(argv: list[str] | None) -> str | None:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    for value in raw_args:
        if not value.startswith("-"):
            return value
    return None


def _dedupe_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
