"""Estimate storage needs for dataset-audit reporting."""

from __future__ import annotations

import json
import re
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Any

from .image_metadata import load_image_metadata
from .models import DEFAULT_FMRIPREP_IMAGE, DEFAULT_XCPD_IMAGE, PipelineStepName, RequestConfig
from .disk import GB, describe_storage_target
from .shell import run_command, shell_command


STORAGE_CHECK_VERSION = 1
WORKDIR_MIN_MULTIPLIER = 3.0
WORKDIR_MAX_MULTIPLIER = 4.0
XCPD_WORKDIR_MULTIPLIER = 3.0
NIFTI_GZ_ESTIMATE_MULTIPLIER = 0.85
NICHART_DENOISED_BOLD_GZ_ESTIMATE_MULTIPLIER = 0.5
MNI152NLIN6ASYM_RES2_SHAPE = (91, 109, 91)
MNI152NLIN6ASYM_RES1_SHAPE = (182, 218, 182)
MNI152NLIN2009CASYM_RES1_SHAPE = (193, 229, 193)
MNI152NLIN2009CASYM_RES2_SHAPE = (97, 115, 97)
FSLR_32K_VERTICES_PER_HEMISPHERE = 32_492
FSLR_32K_FACES_PER_HEMISPHERE = 64_980
FSLR_91K_BOLD_GRAYORDINATES = 91_282
FSLR_91K_ANAT_GRAYORDINATES = 59_412
FSNATIVE_VERTICES_PER_HEMISPHERE = 163_842
FSNATIVE_FACES_PER_HEMISPHERE = 327_680
FSNATIVE_SURFACE_VERTICES_PER_HEMISPHERE = 150_000
FSNATIVE_SURFACE_FACES_PER_HEMISPHERE = 300_000
SPHERE_SURFACE_VERTICES_PER_HEMISPHERE = 164_000
SPHERE_SURFACE_FACES_PER_HEMISPHERE = 328_000
INVENTORY_SPEC_PATH = Path(__file__).with_name("resources") / "storage_check_inventory.json"
TEMPLATE_SHAPES = {
    "MNI152NLin6Asym_res1": MNI152NLIN6ASYM_RES1_SHAPE,
    "MNI152NLin6Asym_res2": MNI152NLIN6ASYM_RES2_SHAPE,
    "MNI152NLin2009cAsym_res1": MNI152NLIN2009CASYM_RES1_SHAPE,
    "MNI152NLin2009cAsym_res2": MNI152NLIN2009CASYM_RES2_SHAPE,
}
BOLD_RUN_ENTITY_PATTERN = re.compile(r"_run-[^_]+")
BOLD_ECHO_ENTITY_PATTERN = re.compile(r"_echo-[^_]+")
XCPD_ATLAS_PARCEL_COUNTS = {
    "4S1056Parcels": 1056,
    "4S156Parcels": 156,
    "4S256Parcels": 256,
    "4S356Parcels": 356,
    "4S456Parcels": 456,
    "4S556Parcels": 556,
    "4S656Parcels": 656,
    "4S756Parcels": 756,
    "4S856Parcels": 856,
    "4S956Parcels": 956,
    "Glasser": 360,
    "Gordon": 333,
    "HCP": 18,
    "Tian": 54,
}
IGNORED_SMALL_FILE_TYPES = (".html", ".json", ".tsv", ".txt")
MODELED_TSV_BYTES_PER_VALUE = 12


def run_storage_check(request: RequestConfig, dataset_subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate storage for dataset-audit reporting only."""
    report, _ = run_storage_check_with_warnings(request, dataset_subjects)
    return report


def run_storage_check_with_warnings(
    request: RequestConfig,
    dataset_subjects: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    subject_plans = _build_storage_subject_plans(request, dataset_subjects)
    if not subject_plans:
        return (
            _storage_check_skip_report(
                request,
                status="skipped_dataset_not_ready",
                reason="dataset_not_ready",
            ),
            [],
        )

    items = [_estimate_item(subject_plan, request) for subject_plan in subject_plans]
    aggregate = _aggregate_estimates(items)
    volume_info = _storage_space_summary(request, aggregate)
    storage_estimation_status = "estimated_remote" if request.remote_host is not None else "estimated_local"
    warnings = _dedupe(
        list(volume_info.get("warnings") or [])
        + [warning for item in items for warning in list(item.get("warnings") or [])]
    )

    return {
        "phase": "storage-check",
        "version": STORAGE_CHECK_VERSION,
        "storage_estimation_status": storage_estimation_status,
        "storage_estimation_reason": None,
        "estimated_final_derivatives_gb": aggregate["estimated_final_derivatives_gb"],
        "estimated_work_peak_min_gb": aggregate["estimated_work_peak_min_gb"],
        "estimated_work_peak_gb": aggregate["estimated_work_peak_gb"],
        "estimated_total_peak_increment_gb": (
            aggregate["estimated_total_peak_increment_gb"] if volume_info["same_volume"] is True else None
        ),
        "estimated_image_pull_gb": aggregate["estimated_image_pull_gb"],
        "estimated_fmriprep_strict_derivatives_gb": round(
            sum(float(item.get("estimated_fmriprep_strict_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_fmriprep_modeled_derivatives_gb": round(
            sum(float(item.get("estimated_fmriprep_modeled_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_fmriprep_derivatives_gb": round(
            sum(float(item.get("estimated_fmriprep_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_freesurfer_strict_derivatives_gb": round(
            sum(float(item.get("estimated_freesurfer_strict_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_freesurfer_modeled_derivatives_gb": round(
            sum(float(item.get("estimated_freesurfer_modeled_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_freesurfer_derivatives_gb": round(
            sum(float(item.get("estimated_freesurfer_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_xcpd_strict_derivatives_gb": round(
            sum(float(item.get("estimated_xcpd_strict_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_xcpd_modeled_derivatives_gb": round(
            sum(float(item.get("estimated_xcpd_modeled_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_xcpd_derivatives_gb": round(
            sum(float(item.get("estimated_xcpd_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_strict_derivatives_gb": round(
            sum(float(item.get("estimated_strict_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "estimated_modeled_derivatives_gb": round(
            sum(float(item.get("estimated_modeled_derivatives_gb", 0.0)) for item in items),
            2,
        ),
        "work_root_explicitly_provided": request.work_root is not None,
        "work_root": str(request.resolve_work_root()),
        "output_root": str(request.resolve_output_root()),
        "work_root_same_volume_as_output": volume_info["same_volume"],
        "output_root_free_gb": volume_info["output_root_free_gb"],
        "work_root_free_gb": volume_info["work_root_free_gb"],
        "current_free_gb": volume_info["comparison_free_gb"],
        "comparison_mode": volume_info["comparison_mode"],
        "comparison_entries": volume_info["comparison_entries"],
        "comparison_text": volume_info["comparison_text"],
        "fits_current_free_space": volume_info["fits_current_free_space"],
        "items": items,
    }, warnings


def _build_storage_subject_plans(request: RequestConfig, dataset_subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subject_plans: list[dict[str, Any]] = []
    for subject in dataset_subjects:
        pipeline = _storage_pipeline_for_subject(request, subject)
        if pipeline is None:
            continue
        subject_plans.append(
            {
                "subject": subject,
                "subject_id": str(subject.get("subject_id")),
                "session_ids": list(subject.get("session_ids") or []),
                "pipeline": pipeline,
            }
        )
    return subject_plans


def _storage_pipeline_for_subject(request: RequestConfig, subject_audit: dict[str, Any]) -> PipelineStepName | None:
    fmriprep_ready = _pipeline_status(subject_audit, "fmriprep") == "ready"
    xcpd_ready = _pipeline_status(subject_audit, "xcpd") == "ready"
    if request.target == "fmriprep":
        return "fmriprep" if fmriprep_ready else None
    return "xcpd" if xcpd_ready else None


def _storage_check_skip_report(request: RequestConfig, *, status: str, reason: str) -> dict[str, Any]:
    return {
        "phase": "storage-check",
        "version": STORAGE_CHECK_VERSION,
        "storage_estimation_status": status,
        "storage_estimation_reason": reason,
        "estimated_total_peak_increment_gb": None,
        "estimated_final_derivatives_gb": None,
        "estimated_work_peak_gb": None,
        "estimated_work_peak_min_gb": None,
        "estimated_image_pull_gb": None,
        "current_free_gb": None,
        "comparison_mode": None,
        "comparison_entries": [],
        "comparison_text": None,
        "fits_current_free_space": None,
        "work_root": str(request.resolve_work_root()),
        "output_root": str(request.resolve_output_root()),
        "items": [],
        "warnings": [],
    }


def _storage_space_summary(request: RequestConfig, aggregate: dict[str, float]) -> dict[str, Any]:
    work_root = request.resolve_work_root()
    output_root = request.resolve_output_root()
    if request.remote_host is not None:
        return _remote_storage_space_summary(request, aggregate, str(work_root), str(output_root))
    work_target = describe_storage_target(
        work_root,
        wsl_vhdx_path=request.wsl_vhdx_path,
        windows_host_drive=request.windows_host_drive,
        allow_wsl_vhdx_scan=True,
    )
    output_target = describe_storage_target(
        output_root,
        wsl_vhdx_path=request.wsl_vhdx_path,
        windows_host_drive=request.windows_host_drive,
        allow_wsl_vhdx_scan=True,
    )
    return _build_storage_space_summary(aggregate, work_root, output_root, work_target, output_target)


def _build_storage_space_summary(
    aggregate: dict[str, float],
    work_root: Any,
    output_root: Any,
    work_target: dict[str, Any],
    output_target: dict[str, Any],
) -> dict[str, Any]:
    same_volume = work_target["volume_key"] == output_target["volume_key"]
    work_free_gb = _round_optional_float(work_target.get("free_gb"))
    output_free_gb = _round_optional_float(output_target.get("free_gb"))
    work_estimate_min_gb = round(float(aggregate["estimated_work_peak_min_gb"]), 2)
    work_estimate_gb = round(float(aggregate["estimated_work_peak_gb"]), 2)
    derivatives_estimate_gb = round(float(aggregate["estimated_final_derivatives_gb"]), 2)
    if same_volume:
        comparison_entries = [
            _comparison_entry(
                label="work + derivatives",
                estimated_min_gb=round(work_estimate_min_gb + derivatives_estimate_gb, 2),
                estimated_max_gb=round(work_estimate_gb + derivatives_estimate_gb, 2),
                estimated_gb=round(work_estimate_gb + derivatives_estimate_gb, 2),
                free_gb=output_free_gb,
                volume_label=_storage_volume_label(output_target, output_root),
            )
        ]
        comparison_mode = "shared_volume"
        comparison_free_gb = output_free_gb
        free_text = _format_gb(output_free_gb) if output_free_gb is not None else "unknown"
        comparison_text = (
            f"derivatives ({_format_gb(derivatives_estimate_gb)} GB) + "
            f"work ({_format_gb(work_estimate_min_gb)} GB ~ {_format_gb(work_estimate_gb)} GB) "
            f"< {_storage_volume_label(output_target, output_root)} free {free_text} GB"
        )
    else:
        comparison_entries = [
            _comparison_entry(
                label="work",
                estimated_min_gb=work_estimate_min_gb,
                estimated_max_gb=work_estimate_gb,
                estimated_gb=work_estimate_gb,
                free_gb=work_free_gb,
                volume_label=_storage_volume_label(work_target, work_root),
            ),
            _comparison_entry(
                label="derivatives",
                estimated_min_gb=derivatives_estimate_gb,
                estimated_max_gb=derivatives_estimate_gb,
                estimated_gb=derivatives_estimate_gb,
                free_gb=output_free_gb,
                volume_label=_storage_volume_label(output_target, output_root),
            ),
        ]
        comparison_mode = "split_volumes"
        comparison_free_gb = None
        comparison_text = "; ".join(_comparison_entry_text(entry) for entry in comparison_entries)
    return {
        "same_volume": same_volume,
        "output_root_free_gb": output_free_gb,
        "work_root_free_gb": work_free_gb,
        "comparison_free_gb": comparison_free_gb,
        "comparison_mode": comparison_mode,
        "comparison_entries": comparison_entries,
        "comparison_text": comparison_text,
        "fits_current_free_space": all(bool(entry["fits"]) for entry in comparison_entries),
        "warnings": _storage_target_warnings(work_target, output_target),
    }


def _remote_storage_space_summary(
    request: RequestConfig,
    aggregate: dict[str, float],
    work_root: str,
    output_root: str,
) -> dict[str, Any]:
    assert request.remote_host is not None
    probe = _probe_remote_storage_roots(request.remote_host, work_root, output_root)
    if not probe["ok"]:
        return _remote_storage_unavailable_summary(str(probe["reason"]))
    try:
        work_target = _remote_storage_target(probe["payload"]["work"])
        output_target = _remote_storage_target(probe["payload"]["output"])
    except (KeyError, TypeError, ValueError) as exc:
        return _remote_storage_unavailable_summary(f"invalid remote storage probe payload: {exc}")
    return _build_storage_space_summary(aggregate, work_root, output_root, work_target, output_target)


def _probe_remote_storage_roots(remote_host: str, work_root: str, output_root: str) -> dict[str, Any]:
    script = r"""
import json
import os
import shutil
import sys

paths = json.load(sys.stdin)

def nearest_existing(raw):
    current = os.path.abspath(raw)
    while not os.path.exists(current):
        parent = os.path.dirname(current.rstrip(os.sep)) or os.sep
        if parent == current:
            break
        current = parent
    usage = shutil.disk_usage(current)
    stat = os.stat(current)
    return {
        "requested": raw,
        "existing": current,
        "st_dev": stat.st_dev,
        "free": usage.free,
        "total": usage.total,
    }

print(json.dumps({name: nearest_existing(path) for name, path in paths.items()}, sort_keys=True))
"""
    result = run_command(
        shell_command(f"python -c {shlex.quote(script)}"),
        remote_host=remote_host,
        input_text=json.dumps({"work": work_root, "output": output_root}),
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return {"ok": False, "reason": _bounded_remote_probe_reason(result.stderr or result.stdout)}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": f"invalid JSON from remote storage probe: {exc}"}
    return {"ok": True, "payload": payload}


def _remote_storage_target(payload: dict[str, Any]) -> dict[str, Any]:
    existing = str(payload["existing"])
    st_dev = int(payload["st_dev"])
    return {
        "path": existing,
        "volume_key": f"remote-device:{st_dev}",
        "volume_kind": "remote_device",
        "volume_label": existing,
        "free_gb": round(float(payload["free"]) / GB, 2),
        "total_gb": round(float(payload["total"]) / GB, 2),
        "source": "remote_statvfs",
    }


def _remote_storage_unavailable_summary(reason: str) -> dict[str, Any]:
    return {
        "same_volume": None,
        "output_root_free_gb": None,
        "work_root_free_gb": None,
        "comparison_free_gb": None,
        "comparison_mode": None,
        "comparison_entries": [],
        "comparison_text": f"Remote free-space comparison unavailable: {_bounded_remote_probe_reason(reason)}",
        "fits_current_free_space": None,
        "warnings": [],
    }


def _storage_target_warnings(*targets: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for target in targets:
        if (
            target.get("source") == "wsl_vhdx_host_drive"
            and target.get("host_drive") is None
            and target.get("free_gb") is None
        ):
            warnings.append("wsl_vhdx_host_drive_unknown")
    return _dedupe(warnings)


def _bounded_remote_probe_reason(reason: str) -> str:
    text = " ".join(str(reason or "unknown reason").split())
    return text[:180] + "..." if len(text) > 180 else text


def _format_gb(value: float) -> str:
    rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    return rendered or "0"


def _comparison_entry(
    *,
    label: str,
    estimated_min_gb: float,
    estimated_max_gb: float,
    estimated_gb: float,
    free_gb: float | None,
    volume_label: str,
) -> dict[str, Any]:
    fits = None if free_gb is None else estimated_max_gb < free_gb
    return {
        "label": label,
        "estimated_min_gb": round(estimated_min_gb, 2),
        "estimated_max_gb": round(estimated_max_gb, 2),
        "estimated_gb": round(estimated_gb, 2),
        "free_gb": _round_optional_float(free_gb),
        "volume_label": volume_label,
        "fits": fits,
    }


def _comparison_entry_text(entry: dict[str, Any]) -> str:
    free_gb = entry.get("free_gb")
    relation = "<" if entry.get("fits") is not False else ">"
    free_text = _format_gb(free_gb) if free_gb is not None else "unknown"
    estimated_text = _format_gb_range(
        float(entry.get("estimated_min_gb", entry["estimated_gb"])),
        float(entry.get("estimated_max_gb", entry["estimated_gb"])),
    )
    return (
        f"{entry['label']}: {estimated_text} GB "
        f"{relation} {entry['volume_label']} free {free_text} GB"
    )


def _format_gb_range(min_value: float, max_value: float) -> str:
    if round(min_value, 2) == round(max_value, 2):
        return _format_gb(max_value)
    return f"{_format_gb(min_value)}-{_format_gb(max_value)}"


def _storage_volume_label(target: dict[str, Any], fallback_path: Path) -> str:
    label = target.get("volume_label")
    if label:
        return str(label)
    return str(fallback_path.anchor or fallback_path)


def _round_optional_float(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _load_inventory_spec() -> dict[str, Any]:
    """Load the static inventory specification from the bundled JSON resource."""
    payload = _read_json(INVENTORY_SPEC_PATH)
    if payload is None:
        raise FileNotFoundError(INVENTORY_SPEC_PATH)
    return payload


def _estimate_item(subject_plan: dict[str, Any], request: RequestConfig) -> dict[str, Any]:
    """Estimate storage for one subject plan item.

    Inputs:
        subject_plan (dict[str, Any]): Subject-level execution plan entry.
        request (RequestConfig): Workflow request after CLI/config normalization.

    Returns:
        dict[str, Any]: Summary payload returned by the helper.
    """
    subject_audit = dict(subject_plan["subject"])
    pipeline = subject_plan["pipeline"]
    session_audits = list(subject_audit.get("sessions") or [])
    input_records = _collect_subject_input_records(session_audits)
    xcpd_input_format = _xcpd_input_format(subject_audit) if pipeline == "xcpd" else None
    t1_paths = [Path(record["path"]) for record in input_records["t1w"]]
    bold_records = [] if pipeline == "fmriprep" and request.anat_only else input_records["bold"]
    if pipeline == "xcpd":
        bold_records = _xcpd_storage_bold_records(bold_records, xcpd_input_format)
    bold_paths = [Path(record["path"]) for record in bold_records]
    allow_local_fallback = request.remote_host is None
    anat_metadata = _resolve_image_metadata_list(input_records["t1w"], t1_paths, allow_local_fallback=allow_local_fallback)
    bold_metadata = _resolve_image_metadata_list(bold_records, bold_paths, allow_local_fallback=allow_local_fallback)
    bold_count = len(bold_paths)
    anat_input_gb, bold_input_gb, input_size_gb = _input_size_components_gb(
        input_records["t1w"],
        bold_records,
        t1_paths,
        bold_paths,
        allow_local_fallback=allow_local_fallback,
    )
    anat_signal_gb = sum(_anat_metadata_payload_gb(metadata) for metadata in anat_metadata)
    bold_signal_gb = sum(_bold_metadata_payload_gb(metadata) for metadata in bold_metadata)
    bold_duration_hours = sum(_bold_duration_hours(metadata) for metadata in bold_metadata)
    metadata_signal_gb = anat_signal_gb + bold_signal_gb
    representative_anat_metadata = _representative_anat_metadata(anat_metadata)

    fmriprep_items = _fmriprep_derivative_inventory(
        pipeline,
        representative_anat_metadata,
        bold_metadata,
        request.output_spaces,
        request.cifti_output,
        anat_only=request.anat_only,
    )
    freesurfer_items = _freesurfer_derivative_inventory(
        pipeline,
        representative_anat_metadata,
        fs_no_reconall=request.fs_no_reconall,
    )
    xcpd_items = _xcpd_derivative_inventory(
        pipeline,
        request.xcpd_mode,
        bold_metadata,
        xcpd_input_format,
    )
    estimated_fmriprep_strict_derivatives_gb = _inventory_bytes_gb(fmriprep_items)
    estimated_fmriprep_modeled_derivatives_gb = _inventory_modeled_bytes_gb(fmriprep_items)
    estimated_fmriprep_derivatives_gb = estimated_fmriprep_strict_derivatives_gb + estimated_fmriprep_modeled_derivatives_gb
    estimated_freesurfer_strict_derivatives_gb = _inventory_bytes_gb(freesurfer_items)
    estimated_freesurfer_modeled_derivatives_gb = _inventory_modeled_bytes_gb(freesurfer_items)
    estimated_freesurfer_derivatives_gb = (
        estimated_freesurfer_strict_derivatives_gb + estimated_freesurfer_modeled_derivatives_gb
    )
    estimated_xcpd_strict_derivatives_gb = _inventory_bytes_gb(xcpd_items)
    estimated_xcpd_modeled_derivatives_gb = _inventory_modeled_bytes_gb(xcpd_items)
    estimated_xcpd_derivatives_gb = estimated_xcpd_strict_derivatives_gb + estimated_xcpd_modeled_derivatives_gb
    all_derivative_items = fmriprep_items + freesurfer_items + xcpd_items
    tracked_derivative_items = [item for item in all_derivative_items if not _is_ignored_small_output(item)]
    strict_output_item_count = sum(1 for item in tracked_derivative_items if item["bytes"] is not None)
    modeled_output_names = [item["name"] for item in tracked_derivative_items if item["modeled_bytes"] is not None]
    unestimated_output_names = [
        item["name"]
        for item in tracked_derivative_items
        if item["bytes"] is None and item["modeled_bytes"] is None
    ]
    estimated_output_file_count = strict_output_item_count + len(modeled_output_names)

    final_gb = round(
        estimated_fmriprep_derivatives_gb
        + estimated_freesurfer_derivatives_gb
        + estimated_xcpd_derivatives_gb
    , 2)
    workdir_reference_gb, work_peak_min_gb, work_peak_max_gb = _estimate_workdir_policy(
        pipeline,
        fmriprep_items,
        estimated_xcpd_derivatives_gb,
    )

    image_pull_gb = _image_pull_estimate(request, pipeline)
    warnings: list[str] = []
    if pipeline == "xcpd" and estimated_xcpd_derivatives_gb == 0:
        warnings.append("xcpd_storage_estimate_unresolved")
    return {
        "subject_id": subject_plan["subject_id"],
        "session_ids": list(subject_plan.get("session_ids") or []),
        "pipeline": pipeline,
        "input_size_gb": round(input_size_gb, 2),
        "metadata_scale_gb": round(metadata_signal_gb, 2),
        "bold_duration_hours": round(bold_duration_hours, 2),
        "fmriprep_output_inventory": fmriprep_items,
        "freesurfer_output_inventory": freesurfer_items,
        "xcpd_output_inventory": xcpd_items,
        "estimated_fmriprep_strict_derivatives_gb": round(estimated_fmriprep_strict_derivatives_gb, 2),
        "estimated_fmriprep_modeled_derivatives_gb": round(estimated_fmriprep_modeled_derivatives_gb, 2),
        "estimated_fmriprep_derivatives_gb": round(estimated_fmriprep_derivatives_gb, 2),
        "estimated_freesurfer_strict_derivatives_gb": round(estimated_freesurfer_strict_derivatives_gb, 2),
        "estimated_freesurfer_modeled_derivatives_gb": round(estimated_freesurfer_modeled_derivatives_gb, 2),
        "estimated_freesurfer_derivatives_gb": round(estimated_freesurfer_derivatives_gb, 2),
        "estimated_xcpd_strict_derivatives_gb": round(estimated_xcpd_strict_derivatives_gb, 2),
        "estimated_xcpd_modeled_derivatives_gb": round(estimated_xcpd_modeled_derivatives_gb, 2),
        "estimated_xcpd_derivatives_gb": round(estimated_xcpd_derivatives_gb, 2),
        "estimated_strict_derivatives_gb": round(
            estimated_fmriprep_strict_derivatives_gb
            + estimated_freesurfer_strict_derivatives_gb
            + estimated_xcpd_strict_derivatives_gb,
            2,
        ),
        "estimated_modeled_derivatives_gb": round(
            estimated_fmriprep_modeled_derivatives_gb
            + estimated_freesurfer_modeled_derivatives_gb
            + estimated_xcpd_modeled_derivatives_gb,
            2,
        ),
        "strict_output_item_count": strict_output_item_count,
        "modeled_output_item_count": len(modeled_output_names),
        "estimated_output_file_count": estimated_output_file_count,
        "unresolved_output_item_count": len(unestimated_output_names),
        "modeled_output_names": modeled_output_names,
        "unestimated_output_file_count": len(unestimated_output_names),
        "unestimated_output_names": unestimated_output_names,
        "estimated_workdir_reference_gb": workdir_reference_gb,
        "estimated_work_peak_min_gb": round(work_peak_min_gb, 2),
        "estimated_final_derivatives_gb": round(final_gb, 2),
        "estimated_work_peak_gb": round(work_peak_max_gb, 2),
        "estimated_image_pull_gb": round(image_pull_gb, 2),
        "estimated_total_peak_increment_gb": round(final_gb + work_peak_max_gb + image_pull_gb, 2),
        "warnings": warnings,
    }


def compact_storage_check_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted storage-check payload used in dataset-audit artifacts."""
    compact_report = dict(report)
    compact_report.pop("warnings", None)
    compact_report["items"] = [_compact_storage_item(item) for item in report.get("items") or []]
    return compact_report


def _compact_storage_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": item["subject_id"],
        "session_ids": list(item.get("session_ids") or []),
        "pipeline": item["pipeline"],
        "estimated_final_derivatives_gb": item["estimated_final_derivatives_gb"],
        "estimated_work_peak_min_gb": item["estimated_work_peak_min_gb"],
        "estimated_work_peak_gb": item["estimated_work_peak_gb"],
    }

def _aggregate_estimates(items: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate subject estimates across the selected subject set.

    Inputs:
        items (list[dict[str, Any]]): Per-subject storage estimate items.

    Returns:
        dict[str, float]: Numeric estimate values returned by the helper.
    """
    if not items:
        return {
            "estimated_final_derivatives_gb": 0.0,
            "estimated_work_peak_min_gb": 0.0,
            "estimated_work_peak_gb": 0.0,
            "estimated_image_pull_gb": 0.0,
            "estimated_total_peak_increment_gb": 0.0,
        }
    total_final = sum(float(item["estimated_final_derivatives_gb"]) for item in items)
    shared_image = max(float(item["estimated_image_pull_gb"]) for item in items)
    work_peak_mins = [float(item.get("estimated_work_peak_min_gb", 0.0)) for item in items]
    work_peak_maxes = [float(item["estimated_work_peak_gb"]) for item in items]
    total_work_peak_min = sum(work_peak_mins)
    total_work_peak_max = sum(work_peak_maxes)
    total_peak = total_final + total_work_peak_max + shared_image
    return {
        "estimated_final_derivatives_gb": round(total_final, 2),
        "estimated_work_peak_min_gb": round(total_work_peak_min, 2),
        "estimated_work_peak_gb": round(total_work_peak_max, 2),
        "estimated_image_pull_gb": round(shared_image, 2),
        "estimated_total_peak_increment_gb": round(total_peak, 2),
    }


def _image_pull_estimate(request: RequestConfig, pipeline: PipelineStepName) -> float:
    """Estimate storage needed to pull runtime images.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.
        pipeline (str): Pipeline selected for this storage estimate.

    Returns:
        float: Computed floating-point value.
    """
    images: list[str | None] = []
    if pipeline == "fmriprep":
        images.append(request.fmriprep_image or DEFAULT_FMRIPREP_IMAGE)
    else:
        images.append(request.xcpd_image or DEFAULT_XCPD_IMAGE)
    estimates = 0.0
    for image in images:
        if image and image.startswith(("docker://", "library://", "oras://", "http://", "https://")):
            estimates += 8.0
    return estimates

def _inventory_item(
    product: str,
    path_pattern: str,
    *,
    strict_bytes: int | None,
    modeled_bytes: int | None,
    estimation_mode: str,
    unresolved_reason: str | None = None,
) -> dict[str, Any]:
    estimated_bytes = strict_bytes if strict_bytes is not None else modeled_bytes
    return {
        "product": product,
        "path_pattern": path_pattern,
        "name": path_pattern,
        "bytes": strict_bytes,
        "modeled_bytes": modeled_bytes,
        "estimated_bytes": estimated_bytes,
        "estimation_mode": estimation_mode,
        "strictly_estimable": strict_bytes is not None,
        "estimated_gb": round(estimated_bytes / GB, 6) if estimated_bytes is not None else None,
        "unresolved_reason": unresolved_reason,
    }


def _inventory_bytes_gb(items: list[dict[str, Any]]) -> float:
    """Return the total GiB for inventory entries with strict byte estimates."""
    total = 0
    for item in items:
        if item["bytes"] is not None:
            total += int(item["bytes"])
    return total / GB


def _inventory_modeled_bytes_gb(items: list[dict[str, Any]]) -> float:
    """Return the GiB contributed by modeled-only inventory entries."""
    total = 0
    for item in items:
        if item["modeled_bytes"] is not None:
            total += int(item["modeled_bytes"])
    return total / GB


def _inventory_workdir_reference_gb(items: list[dict[str, Any]]) -> float:
    """Return the GiB that should drive transient workdir sizing."""
    total = 0
    for item in items:
        if "sourcedata/" in str(item.get("path_pattern", "")):
            continue
        estimated_bytes = item.get("estimated_bytes")
        if estimated_bytes is not None:
            total += int(estimated_bytes)
    return total / GB


def _estimate_workdir_policy(
    pipeline: PipelineStepName,
    fmriprep_items: list[dict[str, Any]],
    estimated_xcpd_derivatives_gb: float,
) -> tuple[float, float, float]:
    fmriprep_reference_gb = round(_inventory_workdir_reference_gb(fmriprep_items), 2)
    fmriprep_work_peak_min_gb, fmriprep_work_peak_max_gb = _estimate_work_peak_range_gb(fmriprep_reference_gb)
    xcpd_reference_gb = round(estimated_xcpd_derivatives_gb, 2)
    xcpd_work_peak_gb = round(xcpd_reference_gb * XCPD_WORKDIR_MULTIPLIER, 2)

    if pipeline == "xcpd":
        return xcpd_reference_gb, xcpd_work_peak_gb, xcpd_work_peak_gb
    return fmriprep_reference_gb, fmriprep_work_peak_min_gb, fmriprep_work_peak_max_gb


def _is_ignored_small_output(item: dict[str, Any]) -> bool:
    path_pattern = str(item.get("path_pattern", "")).lower()
    return any(path_pattern.endswith(suffix) for suffix in IGNORED_SMALL_FILE_TYPES)


def _build_inventory(
    product: str,
    item_specs: list[dict[str, Any]],
    *,
    anat_metadata: dict[str, Any] | None,
    bold_metadata: list[dict[str, Any] | None],
    atlas_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item_spec in item_specs:
        scope = _inventory_scope(item_spec)
        if scope == "subject":
            items.append(
                _instantiate_inventory_item(
                    product,
                    item_spec,
                    anat_metadata=anat_metadata,
                    bold_metadata=None,
                    atlas_name=None,
                )
            )
            continue
        if scope == "bold_run":
            for bold_item in bold_metadata:
                if bold_item is None:
                    continue
                items.append(
                    _instantiate_inventory_item(
                        product,
                        item_spec,
                        anat_metadata=anat_metadata,
                        bold_metadata=bold_item,
                        atlas_name=None,
                    )
                )
            continue
        if scope == "atlas_run":
            for bold_item in bold_metadata:
                if bold_item is None:
                    continue
                for atlas_name in atlas_names or []:
                    items.append(
                        _instantiate_inventory_item(
                            product,
                            item_spec,
                            anat_metadata=anat_metadata,
                            bold_metadata=bold_item,
                            atlas_name=atlas_name,
                        )
                    )
    return items


def _inventory_scope(item_spec: dict[str, Any]) -> str:
    repeat = item_spec.get("repeat")
    if repeat in {"once", "anat"}:
        return "subject"
    if repeat == "bold":
        return "bold_run"
    if "{atlas_name}" in str(item_spec.get("path_pattern", "")):
        return "atlas_run"
    return str(item_spec.get("scope", "subject"))


def _instantiate_inventory_item(
    product: str,
    item_spec: dict[str, Any],
    *,
    anat_metadata: dict[str, Any] | None,
    bold_metadata: dict[str, Any] | None,
    atlas_name: str | None,
) -> dict[str, Any]:
    path_pattern = item_spec["path_pattern"].format(atlas_name=atlas_name) if atlas_name else item_spec["path_pattern"]
    resolved_bytes = _resolve_inventory_bytes(
        item_spec,
        anat_metadata=anat_metadata,
        bold_metadata=bold_metadata,
        atlas_name=atlas_name,
    )
    if resolved_bytes is not None and path_pattern.lower().endswith(".nii.gz"):
        default_multiplier = 1.0 if product == "xcpd" else NIFTI_GZ_ESTIMATE_MULTIPLIER
        multiplier = float(item_spec.get("compression_multiplier", default_multiplier))
        resolved_bytes = int(resolved_bytes * multiplier)
    unresolved_reason = item_spec.get("unresolved_reason")
    estimate_mode = item_spec.get("estimate_mode") or item_spec.get("estimation_mode", "unresolved")
    if resolved_bytes is None and estimate_mode != "unresolved":
        unresolved_reason = unresolved_reason or _default_unresolved_reason(item_spec)
    strict_bytes: int | None = None
    modeled_bytes: int | None = None
    estimation_mode = "unresolved"
    if estimate_mode == "strict" and resolved_bytes is not None:
        strict_bytes = resolved_bytes
        estimation_mode = "strict"
    elif estimate_mode == "modeled" and resolved_bytes is not None:
        modeled_bytes = resolved_bytes
        estimation_mode = "modeled"
    return _inventory_item(
        product,
        path_pattern,
        strict_bytes=strict_bytes,
        modeled_bytes=modeled_bytes,
        estimation_mode=estimation_mode,
        unresolved_reason=unresolved_reason,
    )


def _resolve_inventory_bytes(
    item_spec: dict[str, Any],
    *,
    anat_metadata: dict[str, Any] | None,
    bold_metadata: dict[str, Any] | None,
    atlas_name: str | None,
) -> int | None:
    rule = _inventory_rule_name(item_spec)
    if rule == "unresolved":
        return None
    if rule == "voxel_payload":
        metadata_source = item_spec.get("metadata_source")
        if metadata_source is None:
            metadata_source = "anat" if item_spec.get("size_rule") == "anat_voxel" else "bold"
        metadata = anat_metadata if metadata_source == "anat" else bold_metadata
        if metadata is None:
            return None
        return _voxel_payload_bytes(
            metadata,
            dims=int(item_spec.get("dims", 3)),
            bytes_per_value=int(item_spec["bytes_per_value"]),
        )
    if rule == "shape_payload":
        shape = _template_shape(item_spec["shape_name"])
        return _shape_payload_bytes(shape, bytes_per_value=int(item_spec["bytes_per_value"]))
    if rule == "shape_payload_with_timepoints":
        if bold_metadata is None:
            return None
        shape = _template_shape(item_spec["shape_name"]) + (max(1, int(bold_metadata.get("timepoints") or 0)),)
        return _shape_payload_bytes(shape, bytes_per_value=int(item_spec["bytes_per_value"]))
    if rule == "fslr_32k_func":
        if bold_metadata is None:
            return None
        return FSLR_32K_VERTICES_PER_HEMISPHERE * max(1, int(bold_metadata.get("timepoints") or 0)) * 4
    if rule == "fslr_91k_bold":
        if bold_metadata is None:
            return None
        return FSLR_91K_BOLD_GRAYORDINATES * max(1, int(bold_metadata.get("timepoints") or 0)) * 4
    if rule == "fslr_91k_bold_scalar":
        return FSLR_91K_BOLD_GRAYORDINATES * 4
    if rule == "fixed_fslr_surface":
        return _fixed_fslr_surface_bytes()
    if rule == "fslr_91k_anat_scalar":
        return FSLR_91K_ANAT_GRAYORDINATES * 4
    if rule == "template_transform_h5":
        shape = _template_shape(item_spec["shape_name"])
        return _shape_payload_bytes(shape, bytes_per_value=12)
    if rule == "freesurfer_subject_dir_major_outputs":
        if anat_metadata is None:
            return None
        return _modeled_freesurfer_subject_dir_bytes(anat_metadata)
    if rule == "surface_proxy":
        return _fixed_fsnative_surface_bytes()
    if rule == "atlas_pscalar":
        if atlas_name is None:
            return None
        parcel_count = _xcpd_atlas_parcel_count(atlas_name)
        return None if parcel_count is None else parcel_count * 4
    if rule == "atlas_ptseries":
        if atlas_name is None or bold_metadata is None:
            return None
        parcel_count = _xcpd_atlas_parcel_count(atlas_name)
        if parcel_count is None:
            return None
        return parcel_count * max(1, int(bold_metadata.get("timepoints") or 0)) * 4
    if rule == "atlas_pconn":
        if atlas_name is None:
            return None
        parcel_count = _xcpd_atlas_parcel_count(atlas_name)
        return None if parcel_count is None else parcel_count * parcel_count * 4
    if rule == "atlas_timeseries_tsv":
        if atlas_name is None or bold_metadata is None:
            return None
        parcel_count = _xcpd_atlas_parcel_count(atlas_name)
        if parcel_count is None:
            return None
        return parcel_count * max(1, int(bold_metadata.get("timepoints") or 0)) * MODELED_TSV_BYTES_PER_VALUE
    if rule == "atlas_relmat_tsv":
        if atlas_name is None:
            return None
        parcel_count = _xcpd_atlas_parcel_count(atlas_name)
        return None if parcel_count is None else parcel_count * parcel_count * MODELED_TSV_BYTES_PER_VALUE
    raise ValueError(f"Unsupported inventory rule: {rule}")


def _inventory_rule_name(item_spec: dict[str, Any]) -> str | None:
    rule = item_spec.get("rule")
    if rule:
        return str(rule)
    size_rule = item_spec.get("size_rule")
    mapping = {
        "none": "unresolved",
        "anat_voxel": "voxel_payload",
        "bold_voxel": "voxel_payload",
        "shape_fixed": "shape_payload",
        "shape_fixed_4d": "shape_payload_with_timepoints",
        "fslr_bold_func": "fslr_32k_func",
        "fslr_bold_dtseries": "fslr_91k_bold",
        "fslr_bold_scalar": "fslr_91k_bold_scalar",
        "fslr_surface": "fixed_fslr_surface",
        "fslr_anat_scalar": "fslr_91k_anat_scalar",
        "template_xfm": "template_transform_h5",
        "freesurfer_subject_dir": "freesurfer_subject_dir_major_outputs",
        "fsnative_surface": "surface_proxy",
        "sphere_surface": "surface_proxy",
        "atlas_pscalar": "atlas_pscalar",
        "atlas_ptseries": "atlas_ptseries",
        "atlas_pconn": "atlas_pconn",
        "atlas_timeseries_tsv": "atlas_timeseries_tsv",
        "atlas_relmat_tsv": "atlas_relmat_tsv",
    }
    return mapping.get(str(size_rule)) if size_rule is not None else None


def _default_unresolved_reason(item_spec: dict[str, Any]) -> str | None:
    size_rule = item_spec.get("size_rule")
    if size_rule in {"atlas_pscalar", "atlas_ptseries", "atlas_pconn", "atlas_timeseries_tsv", "atlas_relmat_tsv"}:
        return "Atlas parcel counts or BOLD timing are not available in the current code path."
    return item_spec.get("unresolved_reason")


def _template_shape(shape_name: str) -> tuple[int, ...]:
    try:
        return TEMPLATE_SHAPES[shape_name]
    except KeyError as exc:
        raise ValueError(f"Unknown template shape: {shape_name}") from exc


def _modeled_freesurfer_subject_dir_bytes(anat_metadata: dict[str, Any]) -> int:
    """Estimate dominant FreeSurfer sourcedata outputs from anatomical geometry only."""
    anat_volume_bytes = _voxel_payload_bytes(anat_metadata, dims=3, bytes_per_value=2)
    talairach_bytes = _shape_payload_bytes(_template_shape("MNI152NLin2009cAsym_res1"), bytes_per_value=4)
    # Approximate the subject directory by the major recurring outputs:
    # eight anatomy-like volumes, one Talairach deformation, and thirty-two dense fsnative meshes.
    return anat_volume_bytes * 8 + talairach_bytes + _fixed_fsnative_surface_bytes() * 32


def _fixed_fsnative_surface_bytes() -> int:
    return (
        FSNATIVE_VERTICES_PER_HEMISPHERE * 3 * 4
        + FSNATIVE_FACES_PER_HEMISPHERE * 3 * 4
    )


def _fmriprep_derivative_inventory(
    pipeline: PipelineStepName,
    anat_metadata: dict[str, Any] | None,
    bold_metadata: list[dict[str, Any] | None],
    output_spaces: list[str],
    cifti_output: str | None,
    *,
    anat_only: bool = False,
) -> list[dict[str, Any]]:
    if pipeline == "xcpd":
        return []
    inventory_spec = _load_inventory_spec()
    requested_output_spaces = set(output_spaces)
    item_specs = [
        item
        for item in inventory_spec["fmriprep"]["items"]
        if item.get("output_space") is None or str(item["output_space"]) in requested_output_spaces
    ]
    item_specs = [
        item
        for item in item_specs
        if item.get("cifti_output") is None or item["cifti_output"] == cifti_output
    ]
    if anat_only:
        item_specs = [
            item
            for item in item_specs
            if item.get("repeat") != "bold" and item.get("cifti_output") is None
        ]
    return _build_inventory(
        "fmriprep",
        item_specs,
        anat_metadata=anat_metadata,
        bold_metadata=bold_metadata,
    )


def _freesurfer_derivative_inventory(
    pipeline: PipelineStepName,
    anat_metadata: dict[str, Any] | None,
    *,
    fs_no_reconall: bool = False,
) -> list[dict[str, Any]]:
    if pipeline == "xcpd" or anat_metadata is None or fs_no_reconall:
        return []
    inventory_spec = _load_inventory_spec()
    return _build_inventory(
        "freesurfer",
        inventory_spec["freesurfer"]["items"],
        anat_metadata=anat_metadata,
        bold_metadata=[],
    )


def _xcpd_derivative_inventory(
    pipeline: PipelineStepName,
    xcpd_mode: str,
    bold_metadata: list[dict[str, Any] | None],
    input_format: str | None = None,
) -> list[dict[str, Any]]:
    if pipeline == "fmriprep":
        return []
    if not any(metadata is not None for metadata in bold_metadata):
        return []
    inventory_spec = _load_inventory_spec()["xcpd"]
    base_item_specs = _xcpd_mode_item_specs(inventory_spec["items"], xcpd_mode)
    if xcpd_mode == "abcd":
        item_specs = _xcpd_format_item_specs(
            base_item_specs + _xcpd_atlas_item_specs(inventory_spec["atlas_items"], xcpd_mode),
            input_format,
        )
        atlas_names = inventory_spec["default_atlases"]
    else:
        item_specs = _nichart_item_specs(base_item_specs) + _xcpd_atlas_item_specs(
            inventory_spec["atlas_items"],
            xcpd_mode,
        )
        atlas_names = inventory_spec["default_atlases"]
    return _build_inventory(
        "xcpd",
        item_specs,
        anat_metadata=None,
        bold_metadata=bold_metadata,
        atlas_names=atlas_names,
    )


def _xcpd_input_format(subject_audit: dict[str, Any]) -> str | None:
    formats: list[str] = []
    xcpd = subject_audit.get("xcpd") or {}
    if xcpd.get("input_format"):
        formats.append(str(xcpd["input_format"]))
    for session in subject_audit.get("sessions") or []:
        session_xcpd = session.get("xcpd") or {}
        if session_xcpd.get("input_format"):
            formats.append(str(session_xcpd["input_format"]))
    normalized = _dedupe([value for value in formats if value in {"cifti", "nifti"}])
    return normalized[0] if len(normalized) == 1 else None


def _xcpd_storage_bold_records(records: list[dict[str, Any]], input_format: str | None) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = _xcpd_logical_bold_key(str(record.get("path") or ""))
        grouped.setdefault(key, []).append(record)

    selected: list[dict[str, Any]] = []
    for candidates in grouped.values():
        preferred = candidates
        if input_format == "cifti":
            preferred = [record for record in candidates if _xcpd_record_is_cifti(record)] or candidates
        elif input_format == "nifti":
            preferred = [record for record in candidates if not _xcpd_record_is_cifti(record)] or candidates
        selected.append(_xcpd_record_with_best_timing(preferred[0], candidates))
    return selected


def _xcpd_record_is_cifti(record: dict[str, Any]) -> bool:
    path = str(record.get("path") or "")
    return path.endswith((".dtseries.nii", ".dscalar.nii", ".ptseries.nii", ".pconn.nii", ".pscalar.nii"))


def _xcpd_record_with_best_timing(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if _xcpd_metadata_has_timing(selected.get("metadata")):
        return selected
    for candidate in candidates:
        if _xcpd_metadata_has_timing(candidate.get("metadata")):
            merged = dict(selected)
            merged["metadata"] = candidate["metadata"]
            return merged
    return selected


def _xcpd_metadata_has_timing(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    try:
        return int(metadata.get("timepoints") or 0) > 1
    except (TypeError, ValueError):
        return False


def _xcpd_logical_bold_key(path: str) -> str:
    name = Path(path).name
    name = re.sub(r"\.(nii|nii\.gz)$", "", name)
    name = re.sub(r"_(space|res|den|desc)-[^_]+", "", name)
    name = re.sub(r"_bold.*$", "_bold", name)
    return name


def _xcpd_format_item_specs(item_specs: list[dict[str, Any]], input_format: str | None) -> list[dict[str, Any]]:
    if input_format == "cifti":
        return [item for item in item_specs if not _xcpd_item_is_functional_nifti(item)]
    if input_format == "nifti":
        return [item for item in item_specs if not _xcpd_item_is_cifti(item)]
    return item_specs


def _xcpd_item_is_functional_nifti(item_spec: dict[str, Any]) -> bool:
    path_pattern = str(item_spec.get("path_pattern", ""))
    return path_pattern.startswith("func/") and path_pattern.endswith(".nii.gz")


def _xcpd_item_is_cifti(item_spec: dict[str, Any]) -> bool:
    return str(item_spec.get("path_pattern", "")).endswith(
        (".dtseries.nii", ".dscalar.nii", ".ptseries.nii", ".pconn.nii", ".pscalar.nii")
    )


def _xcpd_mode_item_specs(item_specs: list[dict[str, Any]], xcpd_mode: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in item_specs:
        modes = item.get("xcpd_modes")
        if modes is None or (isinstance(modes, list) and xcpd_mode in modes):
            selected.append(item)
    return selected


def _nichart_item_specs(item_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for item in item_specs:
        path_pattern = str(item.get("path_pattern", ""))
        if path_pattern.endswith((".dtseries.nii", ".dscalar.nii")):
            continue
        if "desc-denoisedSmoothed" in path_pattern:
            continue
        normalized = dict(item)
        if path_pattern.endswith(".nii.gz") and normalized.get("shape_name") == "MNI152NLin6Asym_res2":
            normalized["shape_name"] = "MNI152NLin2009cAsym_res2"
        if path_pattern.endswith("desc-denoised_bold.nii.gz"):
            normalized["compression_multiplier"] = NICHART_DENOISED_BOLD_GZ_ESTIMATE_MULTIPLIER
        specs.append(normalized)
    return specs


def _xcpd_atlas_item_specs(item_specs: list[dict[str, Any]], xcpd_mode: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in item_specs:
        modes = item.get("xcpd_modes")
        if xcpd_mode == "nichart":
            if isinstance(modes, list) and "nichart" in modes:
                selected.append(item)
            continue
        if modes is None or (isinstance(modes, list) and xcpd_mode in modes):
            selected.append(item)
    return selected


def _xcpd_atlas_parcel_count(atlas_name: str) -> int | None:
    return XCPD_ATLAS_PARCEL_COUNTS.get(atlas_name)


def _fixed_fslr_surface_bytes() -> int:
    return (
        FSLR_32K_VERTICES_PER_HEMISPHERE * 3 * 4
        + FSLR_32K_FACES_PER_HEMISPHERE * 3 * 4
    )


def _estimate_work_peak_range_gb(reference_gb: float) -> tuple[float, float]:
    """Return the workdir range as 3x to 4x the chosen workdir reference."""
    minimum = max(0.0, round(reference_gb * WORKDIR_MIN_MULTIPLIER, 2))
    maximum = max(minimum, round(reference_gb * WORKDIR_MAX_MULTIPLIER, 2))
    return minimum, maximum


def _remote_inputs_support_estimation(
    dataset_index: dict[str, dict[str, Any]],
    execution_plan: dict[str, Any],
) -> bool:
    """Return whether every runnable remote subject exposes enough metadata."""
    subjects = [subject for subject in execution_plan.get("subjects", []) if subject.get("steps")]
    if not subjects:
        return False
    return all(_subject_has_image_metadata(dataset_index.get(subject["subject_key"], {})) for subject in subjects)


def _subject_has_image_metadata(subject_audit: dict[str, Any]) -> bool:
    """Return whether the subject audit carries any input image metadata."""
    for session in subject_audit.get("sessions") or []:
        metadata_map = session.get("input_image_metadata") or {}
        if metadata_map.get("t1w") or metadata_map.get("bold"):
            return True
    return False


def _input_size_components_gb(
    anat_records: list[dict[str, Any]],
    bold_records: list[dict[str, Any]],
    t1_paths: list[Path],
    bold_paths: list[Path],
    *,
    allow_local_fallback: bool = True,
) -> tuple[float, float, float]:
    """Resolve anatomy, BOLD, and total input sizes in gigabytes."""
    anat_bytes = _sum_known_sizes([record.get("size_bytes") for record in anat_records])
    bold_bytes = _sum_known_sizes([record.get("size_bytes") for record in bold_records])
    if allow_local_fallback and anat_bytes == 0:
        anat_bytes = sum(path.stat().st_size for path in t1_paths if path.exists())
    if allow_local_fallback and bold_bytes == 0:
        bold_bytes = sum(path.stat().st_size for path in bold_paths if path.exists())
    total_bytes = anat_bytes + bold_bytes
    return anat_bytes / GB, bold_bytes / GB, total_bytes / GB


def _sum_known_sizes(values: list[Any]) -> int:
    """Sum best-effort byte measurements from a payload list."""
    return sum(size for size in (_coerce_size_bytes(value) for value in values) if size is not None)


def _coerce_size_bytes(value: Any) -> int | None:
    """Normalize one byte-size value."""
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _safe_load_image_metadata(path: Path) -> dict[str, Any] | None:
    """Load image metadata without raising hard failures.

    Inputs:
        path (Path): Filesystem path being inspected or normalized.

    Returns:
        dict[str, Any] | None: Summary payload, or ``None`` when unavailable.
    """
    if not path.exists():
        return None
    try:
        return load_image_metadata(path)
    except Exception:  # noqa: BLE001
        return None


def _resolve_image_metadata_list(
    records: list[dict[str, Any]],
    paths: list[Path],
    *,
    allow_local_fallback: bool = True,
) -> list[dict[str, Any] | None]:
    """Resolve image metadata, preferring pre-probed summaries over local file reads."""
    metadata: list[dict[str, Any] | None] = []
    for record, path in zip(records, paths):
        probed = record.get("metadata")
        if isinstance(probed, dict):
            metadata.append(probed)
        elif allow_local_fallback:
            metadata.append(_safe_load_image_metadata(path))
        else:
            metadata.append(None)
    return metadata


def _representative_anat_metadata(items: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Pick one anatomical metadata record for subject-level anat output estimation."""
    available = [item for item in items if item is not None]
    if not available:
        return None
    return max(available, key=_representative_anat_sort_key)


def _representative_anat_sort_key(metadata: dict[str, Any]) -> tuple[Any, ...]:
    """Prefer the largest voxel payload, then fall back to stable metadata content ordering."""
    return (
        _voxel_payload_bytes(metadata, dims=3),
        tuple(int(value) for value in (metadata.get("shape") or [])),
        max(1, int(metadata.get("bitpix") or 0) // 8),
        int(metadata.get("timepoints") or 0),
        tuple(float(value) for value in (metadata.get("zooms") or [])),
        str(metadata.get("dtype") or ""),
    )


def _anat_metadata_payload_gb(metadata: dict[str, Any] | None) -> float:
    """Estimate raw anatomical payload size from header metadata only."""
    if metadata is None:
        return 0.0
    return _voxel_payload_gb(metadata, dims=3)


def _bold_metadata_payload_gb(metadata: dict[str, Any] | None) -> float:
    """Estimate raw functional payload size from header metadata only."""
    if metadata is None:
        return 0.0
    return _voxel_payload_gb(metadata, dims=4)


def _shape_payload_bytes(shape: tuple[int, ...], *, bytes_per_value: int) -> int:
    """Return the payload bytes for one array shape and item width."""
    total = 1
    for value in shape:
        total *= max(1, int(value))
    return total * bytes_per_value


def _voxel_payload_bytes(
    metadata: dict[str, Any],
    *,
    dims: int,
    bytes_per_value: int | None = None,
) -> int:
    """Return raw payload bytes for the requested dimensions of one image."""
    shape = metadata.get("shape") or []
    item_width = bytes_per_value or max(1, int(metadata.get("bitpix") or 0) // 8)
    total = 1
    for value in shape[:dims]:
        total *= max(1, int(value))
    return total * item_width


def _voxel_payload_gb(metadata: dict[str, Any], *, dims: int) -> float:
    """Estimate uncompressed voxel payload size from header metadata only."""
    return _voxel_payload_bytes(metadata, dims=dims) / GB


def _bold_duration_hours(metadata: dict[str, Any] | None) -> float:
    """Estimate BOLD duration in hours.

    Inputs:
        metadata (dict[str, Any] | None): Image metadata summary to read fields from.

    Returns:
        float: Computed floating-point value.
    """
    if metadata is None:
        return 0.0
    repetition_time = metadata.get("repetition_time")
    if repetition_time is None:
        return 0.0
    return float(metadata.get("timepoints") or 0) * float(repetition_time) / 3600.0


def _collect_subject_input_records(session_audits: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    records = {
        "t1w": _collect_input_records(session_audits, "t1w"),
        "bold": _collect_input_records(session_audits, "bold"),
    }
    return records


def _collect_input_records(session_audits: list[dict[str, Any]], modality: str) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for audit in session_audits:
        paths = list((audit.get("input_paths") or {}).get(modality) or [])
        metadata = list((audit.get("input_image_metadata") or {}).get(modality) or [])
        size_bytes = list((audit.get("input_size_bytes") or {}).get(modality) or [])
        for index, raw_path in enumerate(paths):
            path = str(raw_path)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            ordered.append(
                {
                    "path": path,
                    "metadata": metadata[index] if index < len(metadata) else None,
                    "size_bytes": size_bytes[index] if index < len(size_bytes) else None,
                }
            )
    return ordered


def _nearest_existing_path(path_hint: Path) -> Path:
    """Return the nearest existing parent path.

    Inputs:
        path_hint (Path): Path hint used to locate the nearest existing path.

    Returns:
        Path: Resolved path value.
    """
    candidate = path_hint
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else Path("/")


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


def _pipeline_status(subject_audit: dict[str, Any], pipeline: str) -> str:
    return str((subject_audit.get(pipeline) or {}).get("status") or "blocked")


def _subject_runnable_for_target(target: str, subject_audit: dict[str, Any]) -> bool:
    if target == "fmriprep":
        return _pipeline_status(subject_audit, "fmriprep") == "ready"
    return _pipeline_status(subject_audit, "xcpd") == "ready"
