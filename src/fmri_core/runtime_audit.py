"""Audit runtime capabilities for pre-execution checks."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .issue_codes import ISSUE_DESCRIPTIONS, issue_bucket_findings
from .models import (
    ContainerRuntimeName,
    EnvironmentKindName,
    ExecutionStrategyName,
    ExecutorPolicyName,
    LocalPlatformName,
    ProgressCallback,
    RequestConfig,
    RequestPath,
    ResolvedContainerRuntimeName,
    ResolvedExecutorPolicyName,
    RuntimeResources,
    validate_remote_request_paths,
)
from .shell import (
    argv_command,
    command_available,
    path_writable,
    run_command,
    shell_command,
)
from .disk import describe_storage_target, detect_wsl2
from . import image_audit
from . import templateflow_audit
from . import runtime_probe


def _runtime_command_available(request: RequestConfig, command: str) -> bool:
    return command_available(command, request.remote_host)


def _path_is_readable_file(path: RequestPath, remote_host: str | None) -> bool:
    if remote_host:
        command = f"test -f {shlex.quote(str(path))} && test -r {shlex.quote(str(path))}"
        try:
            result = run_command(shell_command(command), remote_host=remote_host, check=False, timeout=20)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
    return Path(path).is_file() and os.access(path, os.R_OK)


def _fs_license_readable(request: RequestConfig, remote_probe: dict[str, Any] | None) -> bool:
    if request.fs_license is None:
        return False
    if request.remote_host and runtime_probe.remote_probe_succeeded(remote_probe):
        shared_paths = remote_probe.get("shared_paths") if isinstance(remote_probe, dict) else {}
        if isinstance(shared_paths, dict) and "fs_license" in shared_paths:
            return bool(shared_paths["fs_license"])
        return True
    return _path_is_readable_file(request.fs_license, request.remote_host)


def _scheduler_partition_blocker_detail(request: RequestConfig) -> str | None:
    partition = request.scheduler_partition
    if partition is None:
        return None
    if not partition or partition.strip() != partition or any(ch.isspace() or ord(ch) < 32 for ch in partition):
        return "Scheduler partition must be one non-empty name without whitespace or control characters."
    try:
        result = run_command(
            argv_command(["sinfo", "-h", "-o", "%P"]),
            remote_host=request.remote_host,
            check=False,
            timeout=20,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return f"Scheduler partition {partition!r} could not be verified with sinfo: {_compact_probe_error(str(exc))}"
    if result.returncode != 0:
        detail = _compact_probe_error((result.stderr or "").strip() or (result.stdout or "").strip())
        return f"Scheduler partition {partition!r} could not be verified with sinfo: {detail or result.returncode}"
    available = {line.strip().replace("*", "") for line in (result.stdout or "").splitlines() if line.strip()}
    if partition not in available:
        return f"Scheduler partition {partition!r} was not found in sinfo partitions: {', '.join(sorted(available)) or 'none'}."
    return None


def audit_runtime(
    request: RequestConfig,
    progress: ProgressCallback | None = None,
    *,
    reusable_proofs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit the runtime and backend capabilities for the request.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.

    Returns:
        dict[str, Any]: Compact runtime summary consumed by later stages.
            Returned keys:
            - ``selected_runtime``: Resolved runtime. One of ``apptainer``,
              ``singularity``, ``docker``, or ``None`` when no usable runtime
              is available.
            - ``selected_runtime_executable``: Absolute executable path used for
              command construction, or ``None`` when unresolved.
            - ``resolved_images``: Effective image mapping for the requested
              pipelines. Keys are a subset of ``fmriprep`` / ``xcpd``; values
              may be a local image path, a registry / remote image reference,
              or ``None``.
            - ``image_root``: Shared image-cache directory used for persisted
              fMRIPrep / XCP-D images.
            - ``selected_executor_policy``: One of ``local`` or ``slurm``.
            - ``environment_kind``: One of ``workstation``, ``linux_server``,
              or ``hpc_cluster``.
            - ``execution_strategy``: One of ``slurm`` or ``worker_pool``.
            - ``slurm_available``: Whether ``sbatch`` is available on the
              execution host.
            - ``in_slurm_allocation``: Whether the current shell already runs
              inside a Slurm allocation.
            - ``local_execution_allowed``: Whether direct local execution is
              allowed for the current host / allocation state.
            - ``resources``: Runtime resource summary with these keys:
              ``cpu_total`` (positive integer), ``slurm_mem_gb`` (positive integer
              per-job hint or ``None``), ``nthreads_per_job`` (positive
              integer), ``omp_nthreads`` (positive integer), and ``max_jobs``
              (positive integer).
            - ``templateflow_home``: Existing TEMPLATEFLOW path string or
              ``None``.
            - ``warnings``: Ordered warning code list. May be empty.
            - ``warning_details``: Human-readable warning messages derived from
              ``warnings``. May be empty.
            - ``blockers``: Ordered blocker code list. May be empty.
            - ``blocker_details``: Human-readable blocker messages derived from
              ``blockers``. May be empty.
    """
    required_templates = templateflow_audit.required_templateflow_templates(request)
    reusable_proofs = reusable_proofs or {}
    environment_proof = _reusable_environment_proof(request, reusable_proofs) if request.target == "xcpd" else None
    environment_data = _proof_data(environment_proof)
    _emit_progress(
        progress,
        stage="runtime-audit",
        status="started",
        message="Auditing runtime environment",
        remote_host=request.remote_host,
        templateflow_templates=required_templates,
    )
    if request.remote_host:
        validate_remote_request_paths(request)
    local_platform = _detect_local_platform(request.remote_host)
    if environment_proof is not None:
        remote_probe = None
        availability = {name: False for name in ("apptainer", "singularity", "docker")}
        selected_runtime = _optional_runtime_name(environment_data.get("selected_runtime"))
        if selected_runtime in availability:
            availability[selected_runtime] = True
        docker_daemon_available = bool(environment_data.get("docker_daemon_available"))
        docker_daemon_error = runtime_probe.coerce_remote_optional_text(environment_data.get("docker_daemon_error"))
        effective_availability = dict(availability)
        effective_availability["docker"] = bool(availability["docker"] and docker_daemon_available)
        selected_runtime_executable = runtime_probe.coerce_remote_optional_text(
            environment_data.get("selected_runtime_executable")
        )
        selected_executor_policy = _optional_executor_policy(environment_data.get("selected_executor_policy"))
        environment_kind = _optional_environment_kind(environment_data.get("environment_kind"))
        execution_strategy = _optional_execution_strategy(environment_data.get("execution_strategy"))
        has_slurm = bool(environment_data.get("slurm_available"))
        in_slurm_allocation = bool(environment_data.get("in_slurm_allocation"))
        local_execution_allowed = bool(environment_data.get("local_execution_allowed"))
        slurm_job_id = runtime_probe.coerce_remote_optional_text(environment_data.get("slurm_job_id"))
        current_host_is_slurm_node = False
        cpu_total = runtime_probe.coerce_remote_int(environment_data.get("cpu_total"), default=1)
        memory_gb = runtime_probe.coerce_remote_optional_int(environment_data.get("memory_gb"))
        write_permission_failures = list(environment_data.get("write_permission_failures") or [])
    else:
        # Remote configuration is collected in one SSH round-trip to minimize latency
        remote_probe = (
            runtime_probe.probe_remote_runtime(
                request,
                required_templates=required_templates,
                required_images=image_audit.required_images(request),
                local_path_predicate=image_audit.looks_like_local_path,
            )
            if request.remote_host
            else None
        )
        if request.remote_host and not runtime_probe.remote_probe_succeeded(remote_probe):
            payload = _remote_probe_failed_payload(
                request,
                local_platform=local_platform,
                required_templates=required_templates,
                remote_probe=remote_probe,
            )
            _emit_progress(
                progress,
                stage="runtime-audit",
                status="finished",
                message="Runtime audit finished with remote probe failure blocker",
                remote_host=request.remote_host,
                templateflow_templates=required_templates,
            )
            return payload

    if environment_proof is None and remote_probe is None:
        # local environment / no remote probe is available => check command availability directly,
        # which may be slower and less robust but avoids SSH failures and parsing issues
        availability = {name: False for name in ("apptainer", "singularity", "docker")}
        local_runtime_commands = ("docker",) if local_platform == "windows" else ("apptainer", "singularity", "docker")
        for name in local_runtime_commands:
            availability[name] = _runtime_command_available(request, name)
        docker_daemon = {"available": False, "error": None}
        if availability["docker"]:
            docker_daemon = _docker_daemon_status(None)
        has_slurm = _runtime_command_available(request, "sbatch")
        selected_runtime_executable = None
        slurm_job_id = _detect_local_slurm_job_id()
        current_host_is_slurm_node = False
        cpu_total = _detect_cpu_total(request.remote_host)
        memory_gb = _detect_memory_gb(request.remote_host)
    elif environment_proof is None:
        commands = remote_probe.get("commands") or {}
        availability = {name: bool(commands.get(name, False)) for name in ("apptainer", "singularity", "docker")}
        docker_daemon = {
            "available": bool(remote_probe.get("docker_daemon_available", False)) if availability["docker"] else False,
            "error": remote_probe.get("docker_daemon_error"),
        }
        has_slurm = bool(commands.get("sbatch", False))
        selected_runtime_executable = None
        slurm_job_id = runtime_probe.coerce_remote_optional_text(remote_probe.get("slurm_job_id"))
        current_host_is_slurm_node = runtime_probe.coerce_remote_bool(
            remote_probe.get("current_host_is_slurm_node", False)
        )
        cpu_total = runtime_probe.coerce_remote_int(remote_probe.get("cpu_total"), default=1)
        memory_gb = runtime_probe.coerce_remote_optional_int(remote_probe.get("memory_gb"))
    if environment_proof is None:
        in_slurm_allocation = slurm_job_id is not None
        local_execution_allowed = _local_execution_allowed(
            has_slurm,
            in_slurm_allocation or current_host_is_slurm_node,
        )
        docker_daemon_available = bool(docker_daemon.get("available"))
        docker_daemon_error = runtime_probe.coerce_remote_optional_text(docker_daemon.get("error"))
        effective_availability = dict(availability)
        effective_availability["docker"] = bool(availability["docker"] and docker_daemon_available)
        selected_runtime, _ = _select_runtime(request.container_runtime, effective_availability, local_platform)
        if remote_probe is None:
            selected_runtime_executable = _resolve_command_path(selected_runtime, request.remote_host)
        else:
            selected_runtime_executable = runtime_probe.remote_probe_command_path(remote_probe, selected_runtime)
        selected_executor_policy = _select_executor_policy(request.executor_policy, has_slurm)
        environment_kind = _classify_environment(request.remote_host, has_slurm, local_platform)
        execution_strategy = _select_execution_strategy(selected_executor_policy, environment_kind)

    resource_proof = (
        _reusable_resource_proof(
            request,
            reusable_proofs,
            cpu_total=cpu_total,
            memory_gb=memory_gb,
            execution_strategy=execution_strategy,
            environment_kind=environment_kind,
        )
        if request.target == "xcpd"
        else None
    )
    if resource_proof is not None:
        resource_data = _proof_data(resource_proof)
        resource_values = dict(resource_data.get("resources") or {})
        resource_plan = {
            "proof_signature": dict(resource_proof.get("signature") or {}),
            "resources": resource_values,
            "warnings": list(resource_data.get("warnings") or []),
        }
        resources = RuntimeResources(
            cpu_total=int(resource_values.get("cpu_total") or 1),
            slurm_mem_gb=runtime_probe.coerce_remote_optional_int(resource_values.get("slurm_mem_gb")),
            nthreads_per_job=int(resource_values.get("nthreads_per_job") or 1),
            omp_nthreads=int(resource_values.get("omp_nthreads") or 1),
            max_jobs=int(resource_values.get("max_jobs") or 1),
        )
    else:
        resource_plan = _resolve_resource_plan(
            cpu_total=cpu_total,
            memory_gb=memory_gb,
            requested_threads=request.nthreads_per_job,
            requested_omp=request.omp_nthreads,
            requested_slurm_mem_gb=request.slurm_mem_gb,
            requested_max_jobs=request.max_jobs,
            execution_strategy=execution_strategy,
            environment_kind=environment_kind,
        )
        resources = resource_plan["resources"]
    image_root = request.resolve_image_root()
    templateflow_home = templateflow_audit.resolve_templateflow_home(request, required_templates=required_templates)
    asset_signature = asset_proof_signature(request, required_templates=required_templates)
    candidate_resolved_images = image_audit.resolve_images(request, selected_runtime, image_root, remote_probe=remote_probe)
    image_kind = f"image.{request.target}"
    image_proof = _reusable_component_proof(
        reusable_proofs,
        image_kind,
        image_fmriprep_proof_signature(
            request,
            selected_runtime=selected_runtime,
            image_root=image_root,
            resolved_image=candidate_resolved_images.get(request.target),
        ),
    )
    reusable_template_proofs = _reusable_templateflow_template_proofs(
        reusable_proofs,
        request,
        templateflow_home=templateflow_home,
        required_templates=required_templates,
    )
    templates_to_check = [template for template in required_templates if template not in reusable_template_proofs]

    if request.action == "submit":
        phase = "submit"
    elif request.action == "runtime-audit":
        phase = "audit"
    else:
        phase = "preflight"
    actionable = phase in {"audit", "submit"}
    execution_visibility = phase in {"audit", "submit"}
    derivatives_storage = _detect_derivatives_storage(request)
    if environment_proof is None:
        write_permission_failures = _write_permission_failures(
            request,
            remote_probe=remote_probe,
        )

    warnings: list[str] = []
    blockers: list[str] = []
    prepare_required: list[str] = []
    warning_details: list[str] = []
    blocker_details: list[str] = []
    prepare_required_details: list[str] = []
    warnings.extend(derivatives_storage["warnings"])
    warning_details.extend(derivatives_storage["warning_details"])
    warnings.extend(list(resource_plan.get("warnings") or []))
    if write_permission_failures:
        blockers.append("runtime_write_permission_denied")
        blocker_details.append(_write_permission_detail(write_permission_failures))
    if image_proof is None:
        resolved_images = candidate_resolved_images
        image_config = image_audit.image_configuration_findings(
            request,
            selected_runtime,
            resolved_images,
            actionable=actionable,
        )
        image_validation = image_audit.image_validation_findings(
            request,
            selected_runtime,
            resolved_images=resolved_images,
            image_root=image_root,
            remote_probe=remote_probe,
            actionable=actionable,
        )
        image_prepare_codes = image_audit.image_prepare_required_codes(request, selected_runtime, resolved_images)
    else:
        image_data = _proof_data(image_proof)
        resolved_images = dict(image_data.get("resolved_images") or image_audit.required_images(request))
        image_prepare_codes = [str(code) for code in image_data.get("image_prepare_codes") or []]
        image_config = {"warnings": [], "blockers": []}
        image_validation = {"warnings": [], "blockers": []}

    if templates_to_check:
        templateflow_diagnostics = templateflow_audit.templateflow_diagnostics(
            request,
            templateflow_home=templateflow_home,
            remote_probe=remote_probe,
            required_templates=templates_to_check,
        )
        templateflow_cache_status = str(templateflow_diagnostics.get("cache_status") or "unknown")
        templateflow_container_ready = _templateflow_container_gate(
            request,
            selected_runtime=selected_runtime,
            resolved_images=resolved_images,
            image_prepare_codes=image_prepare_codes,
            templateflow_home=templateflow_home,
            templateflow_cache_status=templateflow_cache_status,
            required_templates=required_templates,
            remote_probe=remote_probe,
        )
        templateflow_template_proofs = _build_templateflow_template_proofs(
            request,
            templateflow_home=templateflow_home,
            checked_templates=templates_to_check,
            reused_template_proofs=reusable_template_proofs,
            templateflow_diagnostics=templateflow_diagnostics,
            templateflow_container_ready=templateflow_container_ready,
        )
        templateflow_cache_status = _templateflow_template_cache_status(
            templateflow_template_proofs,
            required_templates=required_templates,
        )
        templateflow_container_ready = _templateflow_template_container_ready(
            templateflow_template_proofs,
            required_templates=required_templates,
        )
        registry_probe = image_audit.remote_registry_probe_check(
            request,
            selected_runtime=selected_runtime,
            image_prepare_codes=image_prepare_codes,
        )
        warnings.extend(image_config["warnings"])
        warnings.extend(image_validation["warnings"])
        blockers.extend(image_config["blockers"])
        blockers.extend(image_validation["blockers"])
        warning_details.extend(_issue_details(image_config["warnings"]))
        warning_details.extend(_issue_details(image_validation["warnings"]))
        blocker_details.extend(_issue_details(image_config["blockers"]))
        blocker_details.extend(_issue_details(image_validation["blockers"]))
    else:
        templateflow_template_proofs = reusable_template_proofs
        templateflow_cache_status = _templateflow_template_cache_status(
            templateflow_template_proofs,
            required_templates=required_templates,
        )
        templateflow_container_ready = _templateflow_template_container_ready(
            templateflow_template_proofs,
            required_templates=required_templates,
        )
        templateflow_diagnostics = _combined_reused_templateflow_diagnostics(
            templateflow_template_proofs,
            templateflow_home=templateflow_home,
            required_templates=required_templates,
        )
        registry_probe = {"status": "not_applicable", "command": None, "detail": None}
        if request.target == "xcpd" and required_templates and templateflow_cache_status == "ready":
            fresh_container_ready = _templateflow_container_gate(
                request,
                selected_runtime=selected_runtime,
                resolved_images=resolved_images,
                image_prepare_codes=image_prepare_codes,
                templateflow_home=templateflow_home,
                templateflow_cache_status=templateflow_cache_status,
                required_templates=required_templates,
                remote_probe=remote_probe,
            )
            if fresh_container_ready is not None:
                templateflow_container_ready = fresh_container_ready
    if not templates_to_check:
        warnings.extend(image_config["warnings"])
        warnings.extend(image_validation["warnings"])
        blockers.extend(image_config["blockers"])
        blockers.extend(image_validation["blockers"])
        warning_details.extend(_issue_details(image_config["warnings"]))
        warning_details.extend(_issue_details(image_validation["warnings"]))
        blocker_details.extend(_issue_details(image_config["blockers"]))
        blocker_details.extend(_issue_details(image_validation["blockers"]))

    if local_platform == "windows" and request.container_runtime in {"apptainer", "singularity"}:
        blockers.append("native_windows_requires_docker")
    if phase in {"audit", "submit"} and selected_runtime is None:
        docker_was_candidate = bool(
            availability["docker"]
            and not docker_daemon_available
            and (
                request.container_runtime == "docker"
                or (request.container_runtime == "auto" and not any(effective_availability.values()))
            )
        )
        if docker_was_candidate:
            blockers.append("docker_daemon_unavailable")
    if (
        phase in {"audit", "submit"}
        and selected_runtime is None
        and "docker_daemon_unavailable" not in blockers
    ):
        blockers.append("missing_container_runtime")

    fs_license_readable = _reusable_license_ready(request, reusable_proofs)
    if fs_license_readable is None:
        fs_license_readable = _fs_license_readable(request, remote_probe) if request.fs_license is not None else False
    if execution_visibility and request.target == "fmriprep":
        if request.fs_license is None:
            blockers.append("missing_fs_license")
        elif not fs_license_readable:
            blockers.append("missing_fs_license")
    if execution_visibility and request.target == "xcpd" and request.fs_license is not None and not fs_license_readable:
        blockers.append("missing_fs_license")
    if execution_visibility:
        scheduler_partition_detail = _scheduler_partition_blocker_detail(request)
        if scheduler_partition_detail is not None:
            blockers.append("invalid_scheduler_partition")
            blocker_details.append(scheduler_partition_detail)
        if request.executor_policy == "local" and not local_execution_allowed:
            warnings.append("explicit_local_requires_slurm_allocation")
        if request.remote_host and selected_executor_policy == "local":
            warnings.append("remote_local_execution_current_node")
        if request.remote_host and selected_runtime == "docker" and selected_executor_policy == "slurm":
            blockers.append("remote_docker_slurm_daemon_unverified")
        blockers.extend(_generated_path_blockers(request))
    if templates_to_check:
        if (
            request.target != "xcpd"
            and selected_runtime is not None
            and required_templates
            and templateflow_cache_status == "missing"
        ):
            prepare_required.append(templateflow_audit.TEMPLATEFLOW_CACHE_PREPARE_CODE)
        if (
            selected_runtime is not None
            and required_templates
            and (
                templateflow_cache_status not in {"ready", "missing"}
                or (request.target == "xcpd" and templateflow_cache_status == "missing")
            )
        ):
            warnings.append(templateflow_audit.TEMPLATEFLOW_UNVERIFIED_WARNING_CODE)
    if (
        request.target != "xcpd"
        and selected_runtime is not None
        and required_templates
        and templateflow_cache_status == "ready"
        and templateflow_container_ready is False
    ):
        prepare_required.append(templateflow_audit.TEMPLATEFLOW_CONTAINER_IMPORT_PREPARE_CODE)
    if (
        request.target == "xcpd"
        and selected_runtime is not None
        and required_templates
        and templateflow_cache_status == "ready"
        and templateflow_container_ready is False
    ):
        warnings.append(templateflow_audit.TEMPLATEFLOW_UNVERIFIED_WARNING_CODE)
    if image_proof is None:
        prepare_required.extend(image_prepare_codes)
        image_findings = image_audit.resolved_image_findings(
            request,
            resolved_images,
            image_root=image_root,
            actionable=actionable,
            remote_probe=remote_probe,
        )
        warnings.extend(image_findings["warnings"])
        blockers.extend(image_findings["blockers"])
    warning_details.extend(_issue_details(warnings))
    blocker_details.extend(_issue_details(blockers))
    prepare_required_details.extend(_issue_details(prepare_required))
    prepare_required = _dedupe(prepare_required)
    asset_proof = _build_asset_proof(
        status=_proof_status(blockers, prepare_required),
        proof_signature=asset_signature,
        reused_from_audit_id=None,
        resolved_images=resolved_images,
        image_root=image_root,
        fs_license_readable=fs_license_readable,
        templateflow_home=templateflow_home,
        templateflow_cache_status=templateflow_cache_status,
        templateflow_container_ready=templateflow_container_ready,
        templateflow_diagnostics=templateflow_diagnostics,
        required_templates=required_templates,
        templateflow_template_proofs=templateflow_template_proofs,
        image_prepare_codes=image_prepare_codes,
        warnings=warnings,
        prepare_required=prepare_required,
        blockers=blockers,
    )
    execution_environment_proof = _build_execution_environment_proof(
        request,
        selected_runtime=selected_runtime,
        selected_runtime_executable=selected_runtime_executable,
        docker_daemon_available=docker_daemon_available,
        docker_daemon_error=docker_daemon_error,
        selected_executor_policy=selected_executor_policy,
        environment_kind=environment_kind,
        execution_strategy=execution_strategy,
        slurm_available=has_slurm,
        in_slurm_allocation=in_slurm_allocation,
        local_execution_allowed=local_execution_allowed,
        slurm_job_id=slurm_job_id,
        cpu_total=cpu_total,
        memory_gb=memory_gb,
        write_permission_failures=write_permission_failures,
        remote_probe=remote_probe,
    )

    payload = {
        "asset_proof": asset_proof,
        "execution_environment_proof": execution_environment_proof,
        "resource_plan": {
            "proof_signature": resource_plan["proof_signature"],
            "resources": resources.to_dict(),
            "warnings": list(resource_plan["warnings"]),
        },
        "selected_runtime": selected_runtime,
        "selected_runtime_executable": selected_runtime_executable,
        "docker_daemon_available": docker_daemon_available,
        "docker_daemon_error": docker_daemon_error,
        "resolved_images": resolved_images,
        "image_root": str(image_root),
        "derivatives_storage_filesystem": derivatives_storage["filesystem"],
        "selected_executor_policy": selected_executor_policy,
        "environment_kind": environment_kind,
        "execution_strategy": execution_strategy,
        "slurm_available": has_slurm,
        "in_slurm_allocation": in_slurm_allocation,
        "local_execution_allowed": local_execution_allowed,
        "slurm_job_id": slurm_job_id,
        "resources": resources.to_dict(),
        "templateflow_home": templateflow_home,
        "templateflow_cache_status": templateflow_cache_status,
        "templateflow_container_import_ready": templateflow_container_ready,
        "templateflow_diagnostics": templateflow_diagnostics,
        "required_templateflow_templates": required_templates,
        "write_permission_failures": write_permission_failures,
        "remote_probe": remote_probe,
        "warnings": _dedupe(warnings),
        "prepare_required": prepare_required,
        "prepare_requirements": _prepare_requirements(
            request,
            selected_runtime=selected_runtime,
            resolved_images=resolved_images,
            image_root=image_root,
            templateflow_home=templateflow_home,
            required_templates=required_templates,
            templateflow_diagnostics=templateflow_diagnostics,
            prepare_required=prepare_required,
            registry_probe=registry_probe,
        ),
        "blockers": _dedupe(blockers),
        "warning_details": _dedupe(warning_details),
        "prepare_required_details": _dedupe(prepare_required_details),
        "blocker_details": _dedupe(blocker_details),
    }
    payload["findings"] = issue_bucket_findings(
        blockers=payload["blockers"],
        prepare_required=payload["prepare_required"],
        warnings=payload["warnings"],
    )
    _emit_progress(
        progress,
        stage="runtime-audit",
        status="finished",
        message=f"Runtime audit finished with {len(payload['blockers'])} blocker(s) and {len(payload['warnings'])} warning(s)",
        remote_host=request.remote_host,
        templateflow_templates=required_templates,
    )
    return payload


def _remote_probe_failed_payload(
    request: RequestConfig,
    *,
    local_platform: LocalPlatformName,
    required_templates: list[str],
    remote_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    image_root = request.resolve_image_root()
    templateflow_home = templateflow_audit.resolve_templateflow_home(request, required_templates=required_templates)
    blockers = ["remote_runtime_probe_failed"]
    resource_plan = {
        "proof_signature": _resource_plan_signature(request, None, None, None, None),
        "resources": {},
        "warnings": [],
    }
    asset_proof = _build_asset_proof(
        status="blocked",
        proof_signature=asset_proof_signature(request, required_templates=required_templates),
        reused_from_audit_id=None,
        resolved_images=dict(image_audit.required_images(request)),
        image_root=image_root,
        fs_license_readable=False,
        templateflow_home=templateflow_home,
        templateflow_cache_status="unknown" if required_templates else "ready",
        templateflow_container_ready=None,
        templateflow_diagnostics=templateflow_audit.templateflow_diagnostics(
            request,
            templateflow_home=templateflow_home,
            remote_probe=remote_probe,
            required_templates=required_templates,
        ),
        required_templates=required_templates,
        templateflow_template_proofs={},
        image_prepare_codes=[],
        warnings=[],
        prepare_required=[],
        blockers=blockers,
    )
    payload = {
        "asset_proof": asset_proof,
        "execution_environment_proof": _build_execution_environment_proof(
            request,
            selected_runtime=None,
            selected_runtime_executable=None,
            docker_daemon_available=False,
            docker_daemon_error=None,
            selected_executor_policy=None,
            environment_kind=None,
            execution_strategy=None,
            slurm_available=None,
            in_slurm_allocation=None,
            local_execution_allowed=None,
            slurm_job_id=None,
            cpu_total=None,
            memory_gb=None,
            write_permission_failures=[],
            remote_probe=remote_probe,
        ),
        "resource_plan": resource_plan,
        "selected_runtime": None,
        "selected_runtime_executable": None,
        "docker_daemon_available": False,
        "docker_daemon_error": None,
        "resolved_images": dict(image_audit.required_images(request)),
        "image_root": str(image_root),
        "derivatives_storage_filesystem": None,
        "selected_executor_policy": None,
        "environment_kind": None,
        "execution_strategy": None,
        "slurm_available": None,
        "in_slurm_allocation": None,
        "local_execution_allowed": None,
        "slurm_job_id": None,
        "resources": {},
        "templateflow_home": templateflow_home,
        "templateflow_cache_status": "unknown" if required_templates else "ready",
        "templateflow_container_import_ready": None,
        "templateflow_diagnostics": templateflow_audit.templateflow_diagnostics(
            request,
            templateflow_home=templateflow_home,
            remote_probe=remote_probe,
            required_templates=required_templates,
        ),
        "required_templateflow_templates": required_templates,
        "write_permission_failures": [],
        "remote_probe": remote_probe or runtime_probe.default_remote_runtime_probe(
            request.remote_host or "remote",
            required_templates=required_templates,
            error="remote runtime probe failed",
        ),
        "warnings": [],
        "prepare_required": [],
        "prepare_requirements": [],
        "blockers": blockers,
        "warning_details": [],
        "prepare_required_details": [],
        "blocker_details": _issue_details(blockers),
        "findings": issue_bucket_findings(blockers=blockers, prepare_required=[], warnings=[]),
    }
    return payload


def _emit_progress(progress: ProgressCallback | None, **event: Any) -> None:
    if progress is not None:
        progress(event)


def _docker_daemon_status(remote_host: str | None) -> dict[str, Any]:
    try:
        result = run_command(argv_command(["docker", "info"]), remote_host=remote_host, check=False, timeout=20)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"available": False, "error": str(exc)}
    if result.returncode == 0:
        return {"available": True, "error": None}
    detail = _compact_probe_error((result.stderr or "").strip() or (result.stdout or "").strip())
    return {"available": False, "error": detail or f"docker info exited with {result.returncode}"}


def _compact_probe_error(text: str) -> str:
    return " ".join(text.split())[:500]


def _detect_derivatives_storage(request: RequestConfig) -> dict[str, Any]:
    if request.remote_host is not None:
        return {"filesystem": None, "warnings": [], "warning_details": []}
    try:
        output_root = request.resolve_output_root()
    except ValueError:
        return {"filesystem": None, "warnings": [], "warning_details": []}

    target = describe_storage_target(
        output_root,
        wsl_vhdx_path=request.wsl_vhdx_path,
        windows_host_drive=request.windows_host_drive,
    )
    filesystem = str(target.get("filesystem") or "").strip() or None
    warnings: list[str] = []
    warning_details: list[str] = []
    if filesystem and target.get("host_drive") and filesystem.lower() == "exfat":
        warnings.append("derivatives_storage_exfat_symlink_risk")
        warning_details.append(_derivatives_exfat_detail(target, filesystem))
    return {
        "filesystem": filesystem,
        "warnings": warnings,
        "warning_details": warning_details,
    }


def _derivatives_exfat_detail(target: dict[str, Any], filesystem: str) -> str:
    path = str(target.get("path") or "output_root")
    volume = str(target.get("volume_label") or f"{target.get('host_drive') or 'unknown'}:")
    return (
        f"Derivatives target {path} resolves to Windows volume {volume} with filesystem {filesystem}; "
        "FreeSurfer symlink creation can fail there. Prefer NTFS or a native Linux filesystem."
    )


def _templateflow_container_gate(
    request: RequestConfig,
    *,
    selected_runtime: str | None,
    resolved_images: dict[str, str | None],
    image_prepare_codes: list[str],
    templateflow_home: str | None,
    templateflow_cache_status: str,
    required_templates: list[str],
    remote_probe: dict[str, Any] | None,
) -> bool | None:
    if request.target not in {"fmriprep", "xcpd"} or not required_templates or templateflow_cache_status != "ready":
        return None
    pipeline = request.target
    if f"prepare_runtime_required_{pipeline}_image" in image_prepare_codes:
        return None
    requested_image = request.fmriprep_image if pipeline == "fmriprep" else request.xcpd_image
    image = resolved_images.get(pipeline) or requested_image
    if not image or not templateflow_home:
        return None
    if image_audit.looks_like_remote_image(str(image)) and selected_runtime != "docker":
        return None
    remote_persisted_image_ready = (
        request.remote_host
        and str(image) == str(request.resolve_image_root() / f"{pipeline}.sif")
        and runtime_probe.remote_probe_persisted_image_valid(remote_probe, pipeline)
    )
    if not remote_persisted_image_ready and not image_audit.image_entry_ready(request, selected_runtime, pipeline, image):
        return None
    return templateflow_audit.templateflow_container_import_ready(
        request,
        selected_runtime,
        image,
        templateflow_home,
        required_templates,
    )


def _prepare_requirements(
    request: RequestConfig,
    *,
    selected_runtime: str | None,
    resolved_images: dict[str, str | None],
    image_root: RequestPath,
    templateflow_home: str | None,
    required_templates: list[str],
    templateflow_diagnostics: dict[str, Any],
    prepare_required: list[str],
    registry_probe: dict[str, str | None],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    target_host = request.remote_host or "local"
    for pipeline in image_audit.runtime_requirement_pipelines(request):
        code = f"prepare_runtime_required_{pipeline}_image"
        if code not in prepare_required:
            continue
        source = image_audit.prepare_image_source(resolved_images.get(pipeline), selected_runtime)
        if selected_runtime == "docker":
            target = "docker-engine-cache"
        elif selected_runtime in {"apptainer", "singularity"}:
            target = str(image_root / f"{pipeline}.sif")
        else:
            target = None
        requirements.append(
            {
                "code": code,
                "kind": "image",
                "pipeline": pipeline,
                "runtime": selected_runtime,
                "source": source,
                "target": target,
                "target_host": target_host,
                "network_check": image_audit.prepare_network_check(
                    request,
                    source=source,
                    registry_probe=registry_probe,
                ),
            }
        )
    templateflow_code = None
    if templateflow_audit.TEMPLATEFLOW_CACHE_PREPARE_CODE in prepare_required:
        templateflow_code = templateflow_audit.TEMPLATEFLOW_CACHE_PREPARE_CODE
    elif templateflow_audit.TEMPLATEFLOW_CONTAINER_IMPORT_PREPARE_CODE in prepare_required:
        templateflow_code = templateflow_audit.TEMPLATEFLOW_CONTAINER_IMPORT_PREPARE_CODE
    if templateflow_code is not None:
        requirements.append(
            {
                "code": templateflow_code,
                "kind": "templateflow",
                "runtime": selected_runtime,
                "target": templateflow_home,
                "target_host": target_host,
                "required_templates": list(required_templates),
                "backends": list(templateflow_audit.TEMPLATEFLOW_PREPARE_BACKENDS),
                "toolchain": templateflow_diagnostics.get("toolchain"),
                "archive_url": templateflow_audit.TEMPLATEFLOW_ARCHIVE_URL,
                "network_check": {"status": "not_applicable", "command": None, "detail": None},
            }
        )
    return requirements


def _proof_status(blockers: list[str], prepare_required: list[str]) -> str:
    if blockers:
        return "blocked"
    if prepare_required:
        return "needs_prepare"
    return "ready"


def asset_proof_signature(request: RequestConfig, *, required_templates: list[str]) -> dict[str, Any]:
    return {
        "target": request.target,
        "remote_host": request.remote_host,
        "download_root": str(request.resolve_download_root()),
        "image_root": str(request.resolve_image_root()),
        "fs_license": str(request.fs_license) if request.fs_license is not None else None,
        "fs_no_reconall": request.fs_no_reconall,
        "templateflow_home": str(request.templateflow_home) if request.templateflow_home is not None else None,
        "required_templates": list(required_templates),
        "output_spaces": list(request.output_spaces),
        "cifti_output": request.cifti_output,
        "xcpd_mode": request.xcpd_mode,
        "fmriprep_image": request.fmriprep_image,
        "xcpd_image": request.xcpd_image,
        "container_runtime": request.container_runtime,
    }


def image_fmriprep_proof_signature(
    request: RequestConfig,
    *,
    selected_runtime: str | None,
    image_root: RequestPath,
    resolved_image: str | None,
) -> dict[str, Any]:
    pipeline = request.target
    return {
        "target": pipeline,
        "image_root": str(image_root),
        "image": request.xcpd_image if pipeline == "xcpd" else request.fmriprep_image,
        "resolved_image": resolved_image,
        "container_runtime": request.container_runtime,
        "selected_runtime": selected_runtime,
    }


def templateflow_template_proof_signature(
    request: RequestConfig,
    *,
    template: str,
    templateflow_home: str | None,
) -> dict[str, Any]:
    return {
        "templateflow_home": templateflow_home,
        "template": template,
        "proof_mode": "tool",
        "templateflow_tool_bins": list(request.templateflow_tool_bins),
    }


def _reusable_component_proof(
    reusable_proofs: dict[str, dict[str, Any]],
    kind: str,
    signature: dict[str, Any],
) -> dict[str, Any] | None:
    proof = reusable_proofs.get(kind)
    if not isinstance(proof, dict):
        return None
    if proof.get("status") != "ready":
        return None
    if proof.get("signature") != signature:
        return None
    return dict(proof)


def _proof_data(proof: dict[str, Any] | None) -> dict[str, Any]:
    data = proof.get("data") if isinstance(proof, dict) else None
    return dict(data) if isinstance(data, dict) else {}


def _optional_runtime_name(value: Any) -> ResolvedContainerRuntimeName | None:
    return value if value in {"apptainer", "singularity", "docker"} else None


def _optional_executor_policy(value: Any) -> ResolvedExecutorPolicyName | None:
    return value if value in {"local", "slurm"} else None


def _optional_environment_kind(value: Any) -> EnvironmentKindName | None:
    return value if value in {"workstation", "linux_server", "hpc_cluster"} else None


def _optional_execution_strategy(value: Any) -> ExecutionStrategyName | None:
    return value if value in {"worker_pool", "slurm"} else None


def _reusable_environment_proof(
    request: RequestConfig,
    reusable_proofs: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    kind = "environment.remote" if request.remote_host else "environment.local"
    proof = reusable_proofs.get(kind)
    if not isinstance(proof, dict) or proof.get("status") != "ready":
        return None
    data = _proof_data(proof)
    signature = dict(proof.get("signature") or {})
    expected = {
        "remote_host": request.remote_host,
        "output_root": str(request.resolve_output_root()),
        "work_root": str(request.resolve_work_root()),
        "log_root": str(request.resolve_log_root()),
        "executor_policy": request.executor_policy,
        "scheduler_partition": request.scheduler_partition,
        "container_runtime": request.container_runtime,
        "selected_runtime": data.get("selected_runtime"),
        "selected_executor_policy": data.get("selected_executor_policy"),
        "execution_strategy": data.get("execution_strategy"),
    }
    return dict(proof) if signature == expected else None


def _reusable_resource_proof(
    request: RequestConfig,
    reusable_proofs: dict[str, dict[str, Any]],
    *,
    cpu_total: int | None,
    memory_gb: int | None,
    execution_strategy: ExecutionStrategyName | None,
    environment_kind: EnvironmentKindName | None,
) -> dict[str, Any] | None:
    return _reusable_component_proof(
        reusable_proofs,
        "resources",
        _resource_plan_signature(request, cpu_total, memory_gb, execution_strategy, environment_kind),
    )


def _reusable_license_ready(
    request: RequestConfig,
    reusable_proofs: dict[str, dict[str, Any]],
) -> bool | None:
    proof = _reusable_component_proof(
        reusable_proofs,
        "license.freesurfer",
        {"fs_license": str(request.fs_license) if request.fs_license is not None else None},
    )
    if proof is None:
        return None
    return bool(_proof_data(proof).get("fs_license_readable"))


def _reusable_templateflow_template_proofs(
    reusable_proofs: dict[str, dict[str, Any]],
    request: RequestConfig,
    *,
    templateflow_home: str | None,
    required_templates: list[str],
) -> dict[str, dict[str, Any]]:
    reusable: dict[str, dict[str, Any]] = {}
    for template in required_templates:
        expected = templateflow_template_proof_signature(
            request,
            template=template,
            templateflow_home=templateflow_home,
        )
        proof = _reusable_component_proof(reusable_proofs, f"templateflow.template.{template}", expected)
        if proof is None:
            continue
        data = _proof_data(proof)
        reusable[template] = dict(data)
        reusable[template]["reused_from_audit_id"] = proof.get("reused_from_audit_id")
    return reusable


def _build_templateflow_template_proofs(
    request: RequestConfig,
    *,
    templateflow_home: str | None,
    checked_templates: list[str],
    reused_template_proofs: dict[str, dict[str, Any]],
    templateflow_diagnostics: dict[str, Any],
    templateflow_container_ready: bool | None,
) -> dict[str, dict[str, Any]]:
    proofs = {template: dict(proof) for template, proof in reused_template_proofs.items()}
    status = "ready" if templateflow_diagnostics.get("status") == "ready" else str(
        templateflow_diagnostics.get("status") or "failed"
    )
    for template in checked_templates:
        proof = {
            "status": status,
            "reused_from_audit_id": None,
            "proof_mode": templateflow_diagnostics.get("proof_mode") or templateflow_diagnostics.get("probe_mode"),
            "failed_path": templateflow_diagnostics.get("failed_path"),
            "failure_reason": templateflow_diagnostics.get("failure_reason"),
            "container_import_ready": templateflow_container_ready,
        }
        proofs[template] = proof
    return proofs


def _templateflow_template_cache_status(
    template_proofs: dict[str, dict[str, Any]],
    *,
    required_templates: list[str],
) -> str:
    if not required_templates:
        return "ready"
    statuses = [str(template_proofs.get(template, {}).get("status") or "missing") for template in required_templates]
    if all(status == "ready" for status in statuses):
        return "ready"
    if any(status == "deferred" for status in statuses):
        return "unverified"
    return "missing"


def _templateflow_template_container_ready(
    template_proofs: dict[str, dict[str, Any]],
    *,
    required_templates: list[str],
) -> bool | None:
    if not required_templates:
        return True
    values = [template_proofs.get(template, {}).get("container_import_ready") for template in required_templates]
    if all(value is True for value in values):
        return True
    if any(value is False for value in values):
        return False
    return None


def _combined_reused_templateflow_diagnostics(
    template_proofs: dict[str, dict[str, Any]],
    *,
    templateflow_home: str | None,
    required_templates: list[str],
) -> dict[str, Any]:
    return {
        "status": "ready",
        "probe_mode": "reused",
        "proof_mode": "reused",
        "home": templateflow_home,
        "required_templates": list(required_templates),
        "failed_template": None,
        "failed_path": None,
        "failure_reason": None,
        "cache_status": _templateflow_template_cache_status(
            template_proofs,
            required_templates=required_templates,
        ),
        "reused_templates": sorted(template_proofs),
    }


def _build_asset_proof(
    *,
    status: str,
    proof_signature: dict[str, Any],
    reused_from_audit_id: str | None,
    resolved_images: dict[str, str | None],
    image_root: RequestPath,
    fs_license_readable: bool,
    templateflow_home: str | None,
    templateflow_cache_status: str,
    templateflow_container_ready: bool | None,
    templateflow_diagnostics: dict[str, Any],
    required_templates: list[str],
    templateflow_template_proofs: dict[str, dict[str, Any]],
    image_prepare_codes: list[str],
    warnings: list[str],
    prepare_required: list[str],
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "proof_signature": dict(proof_signature),
        "reused_from_audit_id": reused_from_audit_id,
        "resolved_images": dict(resolved_images),
        "image_root": str(image_root),
        "fs_license_readable": fs_license_readable,
        "templateflow_home": templateflow_home,
        "templateflow_cache_status": templateflow_cache_status,
        "templateflow_container_import_ready": templateflow_container_ready,
        "templateflow_diagnostics": dict(templateflow_diagnostics),
        "required_templateflow_templates": list(required_templates),
        "templateflow_template_proofs": {
            template: dict(proof) for template, proof in templateflow_template_proofs.items()
        },
        "image_prepare_codes": list(image_prepare_codes),
        "warnings": _dedupe(list(warnings)),
        "prepare_required": _dedupe(list(prepare_required)),
        "blockers": _dedupe(list(blockers)),
    }


def _build_execution_environment_proof(
    request: RequestConfig,
    *,
    selected_runtime: str | None,
    selected_runtime_executable: str | None,
    docker_daemon_available: bool,
    docker_daemon_error: str | None,
    selected_executor_policy: str | None,
    environment_kind: str | None,
    execution_strategy: str | None,
    slurm_available: bool | None,
    in_slurm_allocation: bool | None,
    local_execution_allowed: bool | None,
    slurm_job_id: str | None,
    cpu_total: int | None,
    memory_gb: int | None,
    write_permission_failures: list[dict[str, str]],
    remote_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    proof_signature = {
        "remote_host": request.remote_host,
        "output_root": str(request.resolve_output_root()),
        "work_root": str(request.resolve_work_root()),
        "log_root": str(request.resolve_log_root()),
        "executor_policy": request.executor_policy,
        "scheduler_partition": request.scheduler_partition,
        "container_runtime": request.container_runtime,
        "nthreads_per_job": request.nthreads_per_job,
        "omp_nthreads": request.omp_nthreads,
        "slurm_mem_gb": request.slurm_mem_gb,
        "max_jobs": request.max_jobs,
        "selected_runtime": selected_runtime,
        "selected_executor_policy": selected_executor_policy,
        "execution_strategy": execution_strategy,
    }
    return {
        "proof_signature": proof_signature,
        "selected_runtime": selected_runtime,
        "selected_runtime_executable": selected_runtime_executable,
        "docker_daemon_available": docker_daemon_available,
        "docker_daemon_error": docker_daemon_error,
        "selected_executor_policy": selected_executor_policy,
        "environment_kind": environment_kind,
        "execution_strategy": execution_strategy,
        "slurm_available": slurm_available,
        "in_slurm_allocation": in_slurm_allocation,
        "local_execution_allowed": local_execution_allowed,
        "slurm_job_id": slurm_job_id,
        "cpu_total": cpu_total,
        "memory_gb": memory_gb,
        "write_permission_failures": list(write_permission_failures),
        "remote_probe": remote_probe,
    }


def _generated_path_blockers(request: RequestConfig) -> list[str]:
    """Collect blockers for generated paths that are unsafe.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.

    Returns:
        list[str]: List containing the computed values.
    """
    if not request.output_inside_bids_root():
        return []
    blocked: list[str] = []
    paths = {
        "work_root": request.resolve_work_root(),
        "log_root": request.resolve_log_root(),
    }
    for label, candidate in paths.items():
        if _is_inside_bids(request, candidate):
            if label == "log_root" and _log_root_allowed_inside_derivatives(request, candidate):
                continue
            blocked.append(f"{label}_inside_bids")
    return blocked


def _log_root_allowed_inside_derivatives(request: RequestConfig, candidate: RequestPath) -> bool:
    derivatives_root = request.resolve_bids_root() / "derivatives"
    try:
        candidate.relative_to(derivatives_root)
    except ValueError:
        return False
    return True


def _write_permission_failures(
    request: RequestConfig,
    *,
    remote_probe: dict[str, Any] | None,
) -> list[dict[str, str]]:
    checks: list[tuple[str, RequestPath]] = [
        ("output_root", request.resolve_output_root()),
        ("work_root", request.resolve_work_root()),
        ("log_root", request.resolve_log_root()),
    ]

    failures: list[dict[str, str]] = []
    remote_writable_paths = (
        remote_probe.get("writable_paths")
        if request.remote_host and runtime_probe.remote_probe_succeeded(remote_probe)
        else None
    )
    for label, path in checks:
        if isinstance(remote_writable_paths, dict):
            writable = bool(remote_writable_paths.get(label, True))
        else:
            writable = True if request.remote_host else path_writable(path, None)
        if not writable:
            failures.append({"label": label, "path": str(path)})
    return failures


def _write_permission_detail(failures: list[dict[str, str]]) -> str:
    paths = ", ".join(f"{item['label']}={item['path']}" for item in failures)
    return f"Runtime write permission denied for: {paths}."


def _issue_details(codes: list[str]) -> list[str]:
    return [_describe_runtime_issue(code) for code in codes]


def _describe_runtime_issue(code: str) -> str:
    if code in ISSUE_DESCRIPTIONS:
        return ISSUE_DESCRIPTIONS[code]
    if code.startswith("missing_") and code.endswith("_image"):
        pipeline = code.removeprefix("missing_").removesuffix("_image").upper()
        return f"{pipeline} image is missing or the referenced local image path does not exist."
    if code.startswith("invalid_cached_") and code.endswith("_image"):
        pipeline = code.removeprefix("invalid_cached_").removesuffix("_image").upper()
        return f"Cached {pipeline} image exists but failed runtime validation; prepare-runtime must materialize a usable image."
    if code.startswith("invalid_") and code.endswith("_image"):
        pipeline = code.removeprefix("invalid_").removesuffix("_image").upper()
        return f"{pipeline} image exists but failed runtime validation."
    if code.startswith("prepare_runtime_required_") and code.endswith("_image"):
        pipeline = code.removeprefix("prepare_runtime_required_").removesuffix("_image").upper()
        return f"{pipeline} still points to a remote image; run prepare-runtime first or provide a local image path."
    if code.endswith("_inside_bids"):
        label = code.removesuffix("_inside_bids")
        return f"{label} must stay outside the BIDS input tree."
    return code.replace("_", " ")


def _is_inside_bids(request: RequestConfig, candidate: RequestPath) -> bool:
    """Return whether a path falls inside the BIDS tree.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.
        candidate (Path): Candidate path to inspect.

    Returns:
        bool: Whether the condition is satisfied.
    """
    bids_root = request.resolve_bids_root()
    try:
        candidate.relative_to(bids_root)
    except ValueError:
        return False
    return True


def _select_runtime(
    preferred: ContainerRuntimeName,
    availability: dict[ResolvedContainerRuntimeName, bool],
    local_platform: LocalPlatformName,
) -> tuple[ResolvedContainerRuntimeName | None, str | None]:
    """Select the runtime from the request and availability.

    Inputs:
        preferred (str): User-requested preference to resolve.
        availability (dict[str, bool]): Availability map for the candidate options.
        local_platform (str): Local platform label such as linux, darwin, or windows.

    Returns:
        tuple[str | None, str | None]: Selected runtime and optional unavailability note.
    """
    if preferred != "auto":
        if not availability.get(preferred, False):
            return None, f"Requested container runtime is unavailable: {preferred}"
        return preferred, None
    if local_platform == "windows":
        candidates = ("docker",)
    else:
        candidates = ("apptainer", "singularity", "docker")
    for candidate in candidates:
        if availability.get(candidate, False):
            return candidate, None
    return None, None


def _resolve_command_path(command: ResolvedContainerRuntimeName | None, remote_host: str | None) -> str | None:
    """
    Resolve the executable path for one command.

    Inputs:
        command (str | None): Command name or shell command string.
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        str | None: Resolved string value, or ``None`` when unavailable.
    """
    if not command:
        return None
    if remote_host is None:
        return shutil.which(command)
    result = run_command(shell_command(f"command -v {shlex.quote(command)}"), remote_host=remote_host, check=False)
    if result.returncode != 0:
        return None
    resolved = result.stdout.strip().splitlines()
    return resolved[0] if resolved else None


def _detect_local_slurm_job_id() -> str | None:
    for key in ("SLURM_JOB_ID", "SLURM_JOBID", "SLURM_STEP_ID", "SLURM_STEPID"):
        value = os.environ.get(key)
        if value is None:
            continue
        resolved = value.strip()
        if resolved:
            return resolved
    return None


def _local_execution_allowed(has_slurm: bool, in_slurm_allocation: bool) -> bool:
    return (not has_slurm) or in_slurm_allocation


def _select_executor_policy(
    preferred: ExecutorPolicyName,
    has_slurm: bool,
) -> ResolvedExecutorPolicyName:
    """Select the executor policy for the current host.

    Inputs:
        preferred (str): User-requested preference to resolve.
        has_slurm (bool): Whether Slurm commands are available.

    Returns:
        str: Normalized string value.
    """
    if preferred == "local":
        return "local"
    if preferred == "slurm":
        return "slurm"
    if has_slurm:
        return "slurm"
    return "local"


def _classify_environment(
    remote_host: str | None,
    has_slurm: bool,
    local_platform: LocalPlatformName,
) -> EnvironmentKindName:
    """Classify the runtime environment for scheduling decisions.

    Inputs:
        remote_host (str | None): Remote host name for SSH-backed work.
        has_slurm (bool): Whether Slurm commands are available.
        local_platform (str): Local platform label such as linux, darwin, or windows.

    Returns:
        str: Normalized string value.
    """
    if has_slurm:
        return "hpc_cluster"
    if remote_host is not None:
        return "linux_server"
    if local_platform in {"windows", "wsl2"} or _looks_like_workstation():
        return "workstation"
    return "linux_server"


def _detect_local_platform(remote_host: str | None) -> LocalPlatformName:
    """Detect the effective local platform.

    Inputs:
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        str: Normalized string value.
    """
    if remote_host is not None:
        return "remote"
    if _is_native_windows():
        return "windows"
    if detect_wsl2():
        return "wsl2"
    return "linux"


def _is_native_windows() -> bool:
    """Return whether the current host is native Windows.

    Inputs:
        None.

    Returns:
        bool: Whether the condition is satisfied.
    """
    return platform.system() == "Windows" and not detect_wsl2()


def _select_execution_strategy(
    selected_executor_policy: ResolvedExecutorPolicyName,
    environment_kind: EnvironmentKindName,
) -> ExecutionStrategyName:
    """Select the execution strategy from the environment.

    Inputs:
        selected_executor_policy (str): Policy value for the selected executor decision.
        environment_kind (str): Resolved runtime environment classification.

    Returns:
        str: Normalized string value.
    """
    if selected_executor_policy == "slurm":
        return "slurm"
    return "worker_pool"


def _resource_plan_signature(
    request: RequestConfig,
    cpu_total: int | None,
    memory_gb: int | None,
    execution_strategy: ExecutionStrategyName | None,
    environment_kind: EnvironmentKindName | None,
) -> dict[str, Any]:
    return {
        "cpu_total": cpu_total,
        "detected_memory_gb": memory_gb,
        "execution_strategy": execution_strategy,
        "environment_kind": environment_kind,
        "nthreads_per_job": request.nthreads_per_job,
        "omp_nthreads": request.omp_nthreads,
        "slurm_mem_gb": request.slurm_mem_gb,
        "max_jobs": request.max_jobs,
    }


def _resolve_resource_plan(
    cpu_total: int,
    memory_gb: int | None,
    requested_threads: int | None,
    requested_omp: int | None,
    requested_slurm_mem_gb: int | None,
    requested_max_jobs: int | None,
    execution_strategy: ExecutionStrategyName,
    environment_kind: EnvironmentKindName,
) -> dict[str, Any]:
    """Resolve explicit resource requests and fill conservative defaults.

    Inputs:
        cpu_total (int): Total CPU cores available on the execution host.
        memory_gb (int | None): Total usable host memory in GiB.
        requested_threads (int | None): Preferred total thread count for one subject job.
        requested_omp (int | None): Preferred OMP thread count inside one job.
        requested_slurm_mem_gb (int | None): Preferred memory budget for one subject job in GiB.
        requested_max_jobs (int | None): Preferred upper bound for concurrent subject jobs.
        execution_strategy (ExecutionStrategyName): Scheduling mode used to derive concurrent limits.
        environment_kind (EnvironmentKindName): Host class used for default local subject concurrency.

    Returns:
        dict[str, Any]: Resource plan containing normalized resources,
            proof signature, and warnings. Explicit resource requests are
            preserved after positive-integer validation.
    """
    cpu_total = max(1, cpu_total)
    default_threads = 4 if execution_strategy == "slurm" else min(4, cpu_total)
    nthreads_per_job = _positive_resource_value(requested_threads, "nthreads_per_job") or default_threads
    omp_nthreads = _positive_resource_value(requested_omp, "omp_nthreads") or nthreads_per_job
    slurm_mem_gb = _positive_resource_value(requested_slurm_mem_gb, "slurm_mem_gb") if requested_slurm_mem_gb is not None else None
    if execution_strategy == "slurm":
        default_max_jobs = 4
    elif environment_kind == "workstation":
        default_max_jobs = 1
    else:
        default_max_jobs = min(4, max(1, cpu_total // nthreads_per_job))
    max_jobs = _positive_resource_value(requested_max_jobs, "max_jobs") or default_max_jobs
    warnings: list[str] = []
    if execution_strategy != "slurm":
        requested_cpu_parallelism = requested_threads is not None or requested_max_jobs is not None
        cpu_warning_limit = cpu_total
        if requested_cpu_parallelism:
            cpu_warning_limit = max(1, cpu_total - (2 if cpu_total >= 8 else 1))
        if max_jobs * nthreads_per_job > cpu_warning_limit:
            warnings.append("resource_plan_cpu_overcommit")
        if slurm_mem_gb is not None and memory_gb is not None and max_jobs * slurm_mem_gb > memory_gb:
            warnings.append("resource_plan_memory_overcommit")
    if omp_nthreads > nthreads_per_job:
        warnings.append("resource_plan_omp_exceeds_threads")
    resources = RuntimeResources(
        cpu_total=cpu_total,
        slurm_mem_gb=slurm_mem_gb,
        nthreads_per_job=nthreads_per_job,
        omp_nthreads=omp_nthreads,
        max_jobs=max_jobs,
    )
    return {
        "proof_signature": {
            "cpu_total": cpu_total,
            "detected_memory_gb": memory_gb,
            "execution_strategy": execution_strategy,
            "environment_kind": environment_kind,
            "nthreads_per_job": requested_threads,
            "omp_nthreads": requested_omp,
            "slurm_mem_gb": requested_slurm_mem_gb,
            "max_jobs": requested_max_jobs,
        },
        "resources": resources,
        "warnings": warnings,
    }


def _positive_resource_value(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _looks_like_workstation() -> bool:
    """Return whether the host looks like a workstation.

    Inputs:
        None.

    Returns:
        bool: Whether the condition is satisfied.
    """
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    power_supply = Path("/sys/class/power_supply")
    if power_supply.exists():
        return any(path.name.startswith("BAT") for path in power_supply.iterdir())
    return False


def _detect_cpu_total(remote_host: str | None) -> int:
    """Detect the available CPU count.

    Inputs:
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        int: Integer status or computed numeric value.
    """
    if remote_host is None:
        return max(1, os.cpu_count() or 1)
    result = run_command(shell_command("nproc"), remote_host=remote_host, check=False)
    if result.returncode == 0 and result.stdout.strip().isdigit():
        return max(1, int(result.stdout.strip()))
    return 1


def _detect_memory_gb(remote_host: str | None) -> int | None:
    """Detect the available memory in gigabytes.

    Inputs:
        remote_host (str | None): Remote host name for SSH-backed work.

    Returns:
        int | None: Resolved integer value, or ``None`` when unavailable.
    """
    if remote_host is None:
        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names and "SC_PHYS_PAGES" in os.sysconf_names:
            total_bytes = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
            return max(1, total_bytes // (1024**3))
        return None
    result = run_command(
        shell_command("awk '/MemTotal/ {print int($2/1024/1024)}' /proc/meminfo"),
        remote_host=remote_host,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip().isdigit():
        return max(1, int(result.stdout.strip()))
    return None


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
