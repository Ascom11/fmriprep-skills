"""Runtime proof store construction and expansion."""

from __future__ import annotations

from typing import Any

from fmri_core.audit import (
    ARTIFACT_SCHEMA_VERSION,
    load_runtime_proofs,
    runtime_proof_id,
    write_runtime_proofs,
)
from fmri_core.models import RequestConfig
from fmri_core.runtime_audit import image_fmriprep_proof_signature, templateflow_template_proof_signature


def reusable_runtime_proofs(
    request: RequestConfig,
    runtime_artifact: dict[str, Any] | None,
    *,
    source_audit_id: str | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(runtime_artifact, dict):
        return {}
    runtime_payload = runtime_artifact.get("runtime_audit")
    if not isinstance(runtime_payload, dict):
        return {}
    proof_refs = runtime_payload.get("proof_refs")
    if not isinstance(proof_refs, dict):
        return {}
    proof_store = load_runtime_proofs(request)
    reusable: dict[str, dict[str, Any]] = {}
    for kind, proof_id in proof_refs.items():
        if not isinstance(kind, str) or not isinstance(proof_id, str):
            continue
        proof = proof_store.get(proof_id)
        if not isinstance(proof, dict) or proof.get("kind") != kind or proof.get("status") != "ready":
            continue
        reusable[kind] = {**proof, "reused_from_audit_id": source_audit_id}
    return reusable


def write_runtime_component_proofs(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    proofs, proof_refs, readiness = _runtime_component_proofs(request, runtime_audit)
    write_runtime_proofs(request, proofs)
    return proof_refs, readiness


def expand_runtime_audit_with_proofs(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    *,
    require_ready_proofs: bool = False,
) -> dict[str, Any]:
    proof_refs = runtime_audit.get("proof_refs")
    if not isinstance(proof_refs, dict):
        if require_ready_proofs:
            expanded = dict(runtime_audit)
            expanded["proof_resolution_error"] = "invalid_runtime_audit_artifact"
            expanded["missing_proofs"] = ["proof_refs"]
            return expanded
        return dict(runtime_audit)
    proof_store = load_runtime_proofs(request)
    expanded = dict(runtime_audit)
    missing: list[str] = []
    seen: set[str] = set()
    required_proofs = runtime_audit.get("required_proofs")
    if isinstance(required_proofs, list) and all(isinstance(kind, str) for kind in required_proofs):
        required_kinds = set(required_proofs)
    else:
        required_kinds = set()
        missing.append("required_proofs")
    required_templates = runtime_audit.get("required_templateflow_templates")
    if isinstance(required_templates, list):
        if request.target != "xcpd":
            for template in required_templates:
                if isinstance(template, str):
                    required_kinds.add(f"templateflow.template.{template}")
                else:
                    missing.append("required_templateflow_templates")
            if required_templates:
                required_kinds.add(f"templateflow.container_import.{request.target}")
    else:
        missing.append("required_templateflow_templates")
    image_kind = f"image.{request.target}"
    required_kinds.update({"resources", image_kind})
    if request.target != "xcpd" or request.fs_license is not None:
        required_kinds.add("license.freesurfer")
    if request.target == "xcpd":
        required_kinds = {
            kind
            for kind in required_kinds
            if not _xcpd_advisory_proof_kind(request, kind)
        }
    if require_ready_proofs:
        required_kinds.update(kind for kind in proof_refs if isinstance(kind, str) and kind.startswith("environment."))
    for kind, proof_id in proof_refs.items():
        if not isinstance(kind, str) or not isinstance(proof_id, str):
            missing.append(str(kind))
            continue
        proof = proof_store.get(proof_id)
        if not isinstance(proof, dict) or proof.get("kind") != kind:
            if _xcpd_advisory_proof_kind(request, kind):
                continue
            missing.append(kind)
            continue
        status = proof.get("status")
        if status not in {"ready", "needs_prepare", "blocked", "missing", "deferred"}:
            if _xcpd_advisory_proof_kind(request, kind):
                continue
            missing.append(kind)
            continue
        if require_ready_proofs and kind in required_kinds and status != "ready":
            missing.append(kind)
            continue
        data = proof.get("data")
        if not isinstance(data, dict):
            if _xcpd_advisory_proof_kind(request, kind):
                continue
            missing.append(kind)
            continue
        if not _known_proof_kind(kind):
            if _xcpd_advisory_proof_kind(request, kind):
                continue
            missing.append(kind)
            continue
        seen.add(kind)
        if kind.startswith("environment."):
            environment_proof = {
                "proof_signature": dict(proof.get("signature") or {}),
                "selected_runtime": data.get("selected_runtime"),
                "selected_runtime_executable": data.get("selected_runtime_executable"),
                "docker_daemon_available": data.get("docker_daemon_available"),
                "docker_daemon_error": data.get("docker_daemon_error"),
                "selected_executor_policy": data.get("selected_executor_policy"),
                "environment_kind": data.get("environment_kind"),
                "execution_strategy": data.get("execution_strategy"),
                "slurm_available": data.get("slurm_available"),
                "in_slurm_allocation": data.get("in_slurm_allocation"),
                "local_execution_allowed": data.get("local_execution_allowed"),
                "slurm_job_id": data.get("slurm_job_id"),
                "cpu_total": data.get("cpu_total"),
                "memory_gb": data.get("memory_gb"),
                "write_permission_failures": list(data.get("write_permission_failures") or []),
            }
            expanded.update(
                {
                    "execution_environment_proof": environment_proof,
                    "selected_runtime": data.get("selected_runtime"),
                    "selected_runtime_executable": data.get("selected_runtime_executable"),
                    "docker_daemon_available": data.get("docker_daemon_available"),
                    "docker_daemon_error": data.get("docker_daemon_error"),
                    "selected_executor_policy": data.get("selected_executor_policy"),
                    "environment_kind": data.get("environment_kind"),
                    "execution_strategy": data.get("execution_strategy"),
                    "slurm_available": data.get("slurm_available"),
                    "in_slurm_allocation": data.get("in_slurm_allocation"),
                    "local_execution_allowed": data.get("local_execution_allowed"),
                    "slurm_job_id": data.get("slurm_job_id"),
                    "write_permission_failures": list(data.get("write_permission_failures") or []),
                    "remote_probe": data.get("remote_probe_facts"),
                }
            )
        elif kind == "resources":
            resources = data.get("resources")
            if not _runtime_resources_are_complete(resources):
                missing.append(kind)
                continue
            expanded["resource_plan"] = {
                "proof_signature": dict(proof.get("signature") or {}),
                "resources": dict(resources),
                "warnings": list(data.get("warnings") or []),
            }
            expanded["resources"] = dict(resources)
        elif kind in {"image.fmriprep", "image.xcpd"}:
            pipeline = kind.removeprefix("image.")
            expanded["resolved_images"] = {pipeline: data.get("resolved_image")}
        elif kind.startswith("templateflow.template."):
            asset_proof = expanded.setdefault("asset_proof", {})
            template_proofs = asset_proof.setdefault("templateflow_template_proofs", {})
            template = kind.removeprefix("templateflow.template.")
            template_proofs[template] = dict(data)
        elif kind in {"templateflow.container_import.fmriprep", "templateflow.container_import.xcpd"}:
            expanded["templateflow_home"] = data.get("templateflow_home")
            expanded["templateflow_cache_status"] = data.get("templateflow_cache_status")
            expanded["templateflow_container_import_ready"] = data.get("templateflow_container_import_ready")
            expanded["templateflow_diagnostics"] = dict(data.get("templateflow_diagnostics") or {})
    if not any(kind.startswith("environment.") for kind in seen):
        missing.append("environment")
    for kind in sorted(kind for kind in required_kinds if not kind.startswith("environment.") and kind not in seen):
        missing.append(kind)
    required_fields = ("selected_executor_policy", "selected_runtime", "resources", "resolved_images")
    if missing or any(field not in expanded for field in required_fields):
        expanded["proof_resolution_error"] = "invalid_runtime_audit_artifact"
        expanded["missing_proofs"] = sorted(set(missing))
    return expanded


def _runtime_component_proofs(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
    proofs: dict[str, dict[str, Any]] = {}

    def add(kind: str, signature: dict[str, Any], status: str, data: dict[str, Any]) -> None:
        proof_id = runtime_proof_id(kind, signature, status=status, data=data)
        proofs[proof_id] = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "kind": kind,
            "status": status,
            "signature": signature,
            "data": data,
        }

    environment_proof = dict(runtime_audit.get("execution_environment_proof") or {})
    environment_signature = dict(environment_proof.get("proof_signature") or {})
    for key in ("nthreads_per_job", "omp_nthreads", "slurm_mem_gb", "max_jobs"):
        environment_signature.pop(key, None)
    environment_kind = "environment.remote" if request.remote_host else "environment.local"
    environment_blockers = {"remote_runtime_probe_failed", "runtime_write_permission_denied"}
    add(
        environment_kind,
        environment_signature,
        "blocked" if environment_blockers & set(runtime_audit.get("blockers") or []) else "ready",
        {
            "selected_runtime": runtime_audit.get("selected_runtime"),
            "selected_runtime_executable": runtime_audit.get("selected_runtime_executable"),
            "docker_daemon_available": runtime_audit.get("docker_daemon_available"),
            "docker_daemon_error": runtime_audit.get("docker_daemon_error"),
            "selected_executor_policy": runtime_audit.get("selected_executor_policy"),
            "environment_kind": runtime_audit.get("environment_kind"),
            "execution_strategy": runtime_audit.get("execution_strategy"),
            "slurm_available": runtime_audit.get("slurm_available"),
            "in_slurm_allocation": runtime_audit.get("in_slurm_allocation"),
            "local_execution_allowed": runtime_audit.get("local_execution_allowed"),
            "slurm_job_id": runtime_audit.get("slurm_job_id"),
            "cpu_total": environment_proof.get("cpu_total"),
            "memory_gb": environment_proof.get("memory_gb"),
            "write_permission_failures": list(runtime_audit.get("write_permission_failures") or []),
            "remote_probe_facts": _remote_probe_facts(runtime_audit.get("remote_probe")),
        },
    )

    resource_plan = dict(runtime_audit.get("resource_plan") or {})
    add(
        "resources",
        dict(resource_plan.get("proof_signature") or {}),
        "ready",
        {
            "resources": dict(runtime_audit.get("resources") or {}),
            "warnings": list(resource_plan.get("warnings") or []),
        },
    )

    pipeline = request.target
    image_kind = f"image.{pipeline}"
    container_import_kind = f"templateflow.container_import.{pipeline}"
    image_prepare_codes = list((runtime_audit.get("asset_proof") or {}).get("image_prepare_codes") or [])
    image_blockers = {
        code
        for code in list(runtime_audit.get("blockers") or [])
        if str(code).endswith(f"_{pipeline}_image") or str(code) in {"missing_container_runtime", "docker_daemon_unavailable"}
    }
    image_status = "needs_prepare" if image_prepare_codes else "blocked" if image_blockers else "ready"
    resolved_image = (
        (runtime_audit.get("resolved_images") or {}).get(pipeline)
        if isinstance(runtime_audit.get("resolved_images"), dict)
        else None
    )
    add(
        image_kind,
        image_fmriprep_proof_signature(
            request,
            selected_runtime=runtime_audit.get("selected_runtime"),
            image_root=request.resolve_image_root(),
            resolved_image=resolved_image,
        ),
        image_status,
        {
            "resolved_image": resolved_image,
            "image_prepare_codes": image_prepare_codes,
        },
    )

    asset_proof = dict(runtime_audit.get("asset_proof") or {})
    license_ready = bool(asset_proof.get("fs_license_readable"))
    if request.target != "xcpd" or request.fs_license is not None:
        add(
            "license.freesurfer",
            {"fs_license": str(request.fs_license) if request.fs_license is not None else None},
            "ready" if license_ready else "blocked",
            {"fs_license_readable": license_ready},
        )

    required_templates = [str(value) for value in runtime_audit.get("required_templateflow_templates") or []]
    template_proofs = asset_proof.get("templateflow_template_proofs")
    template_proofs = template_proofs if isinstance(template_proofs, dict) else {}
    for template in required_templates:
        template_proof = _slim_template_proof(template_proofs.get(template))
        signature = templateflow_template_proof_signature(
            request,
            template=template,
            templateflow_home=runtime_audit.get("templateflow_home"),
        )
        add(
            f"templateflow.template.{template}",
            signature,
            str(template_proof.get("status") or "missing"),
            template_proof,
        )

    import_signature = {
        "selected_runtime": runtime_audit.get("selected_runtime"),
        "image": (runtime_audit.get("resolved_images") or {}).get(pipeline)
        if isinstance(runtime_audit.get("resolved_images"), dict)
        else None,
        "templateflow_home": runtime_audit.get("templateflow_home"),
        "required_templates": required_templates,
    }
    add(
        container_import_kind,
        import_signature,
        "ready" if runtime_audit.get("templateflow_container_import_ready") is not False else "needs_prepare",
        {
            "templateflow_home": runtime_audit.get("templateflow_home"),
            "templateflow_cache_status": runtime_audit.get("templateflow_cache_status"),
            "templateflow_container_import_ready": runtime_audit.get("templateflow_container_import_ready"),
            "templateflow_diagnostics": dict(runtime_audit.get("templateflow_diagnostics") or {}),
        },
    )

    proof_refs = {proof["kind"]: proof_id for proof_id, proof in proofs.items()}
    readiness = {
        "environment": proofs[proof_refs[environment_kind]]["status"],
        "resources": proofs[proof_refs["resources"]]["status"],
        "image": proofs[proof_refs[image_kind]]["status"],
        "templateflow": (
            "ready"
            if all(
                proofs[proof_refs[f"templateflow.template.{template}"]]["status"] == "ready"
                for template in required_templates
            )
            else "needs_prepare"
        ),
        "templateflow_container_import": proofs[proof_refs[container_import_kind]]["status"],
    }
    readiness["license"] = proofs[proof_refs["license.freesurfer"]]["status"] if "license.freesurfer" in proof_refs else "not_applicable"
    return proofs, proof_refs, readiness


def _xcpd_advisory_proof_kind(request: RequestConfig, kind: str) -> bool:
    if request.target != "xcpd":
        return False
    if kind == "license.freesurfer":
        return request.fs_license is None
    return kind.startswith("templateflow.template.") or kind == "templateflow.container_import.xcpd"


def _known_proof_kind(kind: str) -> bool:
    return (
        kind.startswith("environment.")
        or kind == "resources"
        or kind in {"image.fmriprep", "image.xcpd"}
        or kind == "license.freesurfer"
        or kind.startswith("templateflow.template.")
        or kind in {"templateflow.container_import.fmriprep", "templateflow.container_import.xcpd"}
    )


def _remote_probe_facts(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "probe_ok",
        "hostname",
        "commands",
        "command_paths",
        "writable_paths",
        "shared_paths",
        "slurm_job_id",
        "current_host_is_slurm_node",
        "cpu_total",
        "memory_gb",
    )
    return {key: value.get(key) for key in keys if key in value}


def _slim_template_proof(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    return {
        "status": data.get("status") or "missing",
        "proof_mode": data.get("proof_mode"),
        "failed_path": data.get("failed_path"),
        "failure_reason": data.get("failure_reason"),
        "container_import_ready": data.get("container_import_ready"),
    }


def _runtime_resources_are_complete(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("max_jobs", "nthreads_per_job", "omp_nthreads"):
        if not isinstance(value.get(key), int) or value.get(key) < 1:
            return False
    return True
