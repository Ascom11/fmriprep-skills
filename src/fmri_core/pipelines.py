"""Translate audit results into executable subject-level processing steps."""

from __future__ import annotations

import shlex
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import BindMount, ContainerSpec, PipelineStepName, RequestConfig
from .templateflow_audit import required_templateflow_templates

TEMPLATEFLOW_CONTAINER_HOME = "/templateflow"
FREESURFER_SUBJECTS_CONTAINER_HOME = "/fsdir"
FREESURFER_PREWARM_STEP = "freesurfer-prewarm"
FMRIPREP_CONTAINER_PROBE_STEP = "fmriprep-container-probe"
FMRIPREP_CUSTOM_ARG_FLAGS = {
    "ignore": "--ignore",
    "force": "--force",
    "bold2anat_init": "--bold2anat-init",
    "bold2anat_dof": "--bold2anat-dof",
    "slice_time_ref": "--slice-time-ref",
    "dummy_scans": "--dummy-scans",
    "fallback_total_readout_time": "--fallback-total-readout-time",
    "mem": "--mem",
    "mem_mb": "--mem-mb",
    "random_seed": "--random-seed",
    "me_t2s_fit_method": "--me-t2s-fit-method",
    "skull_strip_template": "--skull-strip-template",
    "me_output_echos": "--me-output-echos",
    "low_mem": "--low-mem",
    "return_all_components": "--return-all-components",
    "fd_spike_threshold": "--fd-spike-threshold",
    "dvars_spike_threshold": "--dvars-spike-threshold",
    "aggregate_session_reports": "--aggregate-session-reports",
    "medial_surface_nan": "--medial-surface-nan",
    "md_only_boilerplate": "--md-only-boilerplate",
    "msm": "--msm",
    "project_goodvoxels": "--project-goodvoxels",
    "skull_strip_fixed_seed": "--skull-strip-fixed-seed",
    "skull_strip_t1w": "--skull-strip-t1w",
    "fmap_bspline": "--fmap-bspline",
    "fmap_no_demean": "--fmap-no-demean",
    "use_syn_sdc": "--use-syn-sdc",
    "verbose": "--verbose",
    "resource_monitor": "--resource-monitor",
    "stop_on_first_crash": "--stop-on-first-crash",
}
XCPD_CUSTOM_ARG_FLAGS = {
    "dummy_scans": "--dummy-scans",
    "smoothing": "--smoothing",
    "combine_runs": "--combine-runs",
    "skip": "--skip",
    "head_radius": "--head-radius",
    "fd_thresh": "--fd-thresh",
    "output_type": "--output-type",
    "disable_bandpass_filter": "--disable-bandpass-filter",
    "lower_bpf": "--lower-bpf",
    "upper_bpf": "--upper-bpf",
    "bpf_order": "--bpf-order",
    "min_coverage": "--min-coverage",
    "output_run_wise_correlations": "--output-run-wise-correlations",
    "atlases": "--atlases",
    "nuisance_regressors": "--nuisance-regressors",
    "create_matrices": "--create-matrices",
    "random_seed": "--random-seed",
    "linc_qc": "--linc-qc",
    "abcc_qc": "--abcc-qc",
    "report_output_level": "--report-output-level",
    "aggregate_session_reports": "--aggregate-session-reports",
    "low_mem": "--low-mem",
    "md_only_boilerplate": "--md-only-boilerplate",
    "resource_monitor": "--resource-monitor",
    "stop_on_first_crash": "--stop-on-first-crash",
    "verbose": "-v",
}
FREESURFER_PREWARM_SCRIPT = textwrap.dedent(
    f"""
    import fcntl
    import os
    import shutil
    from pathlib import Path

    source_root = Path(os.environ.get("FREESURFER_HOME", "/opt/freesurfer")) / "subjects"
    target_root = Path("{FREESURFER_SUBJECTS_CONTAINER_HOME}")
    lock_path = target_root / ".fsaverage.lock"
    ready_path = target_root / ".fsaverage.ready"
    selected_subject_names = [
        name
        for name in os.environ.get("FREESURFER_PREWARM_SUBJECTS", "fsaverage").split(",")
        if name
    ]

    def selected_source_dirs():
        return [
            source_root / name
            for name in selected_subject_names
            if (source_root / name).is_dir()
        ]

    def shared_subjects_ready():
        if not ready_path.exists():
            return False
        for source_dir in selected_source_dirs():
            if not (target_root / source_dir.name).exists():
                return False
        return True

    if not source_root.exists():
        raise SystemExit(f"Missing FreeSurfer subjects root: {{source_root}}")

    target_root.mkdir(parents=True, exist_ok=True)
    if not selected_source_dirs() or shared_subjects_ready():
        raise SystemExit(0)

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        if shared_subjects_ready():
            raise SystemExit(0)
        for source_dir in selected_source_dirs():
            for current_root, dirnames, filenames in os.walk(source_dir):
                current_path = Path(current_root)
                relative = current_path.relative_to(source_dir)
                destination_root = target_root / source_dir.name / relative
                destination_root.mkdir(parents=True, exist_ok=True)
                for dirname in dirnames:
                    (destination_root / dirname).mkdir(parents=True, exist_ok=True)
                for filename in filenames:
                    source_path = current_path / filename
                    destination_path = destination_root / filename
                    if destination_path.exists() or destination_path.is_symlink():
                        continue
                    if source_path.is_symlink():
                        destination_path.symlink_to(os.readlink(source_path))
                    else:
                        shutil.copy2(source_path, destination_path)
        ready_path.write_text("ready\\n", encoding="utf-8")
    """
).strip()


def build_execution_plan(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    runnable_subjects: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    """Build the subject-level execution plan for the request.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.
        runtime_audit (dict[str, Any]): Runtime audit summary for the current request.
        runnable_subjects (list[dict[str, Any]]): Runnable subject records from
            the dataset debug artifact.
        run_id (str): Run identifier for the current workflow.

    Returns:
        dict[str, Any]: Per-subject execution plan and shared run settings.
    """
    subjects: list[dict[str, Any]] = []
    for subject_audit in runnable_subjects:
        subject_id = str(subject_audit["subject_id"])
        subject_key = f"sub-{subject_id}"
        planned_steps = list(subject_audit.get("steps") or [])
        session_ids = list(subject_audit.get("session_ids") or [])
        step_specs = []
        for step in planned_steps:
            work_dir = replace(request, target=step).resolve_work_root() / run_id / subject_key
            output_dir = request.resolve_pipeline_output_root(step)
            log_dir = request.resolve_log_root() / run_id / subject_key
            stdout_path = log_dir / f"{step}.stdout.log"
            stderr_path = log_dir / f"{step}.stderr.log"
            step_spec = {
                "step": step,
                "command": build_step_command(
                    step=step,
                    subject_id=subject_id,
                    request=request,
                    runtime_audit=runtime_audit,
                    work_dir=work_dir,
                    output_dir=output_dir,
                    session_ids=session_ids,
                ),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "work_dir": str(work_dir),
                "output_dir": str(output_dir),
            }
            if step == "fmriprep" and request.sessions:
                step_spec["bids_filter"] = {
                    "path": str(work_dir / "bids-filter.json"),
                    "content": _bids_filter_content(session_ids),
                }
            if step == "fmriprep":
                step_specs.append(
                    {
                        "step": FMRIPREP_CONTAINER_PROBE_STEP,
                        "command": build_fmriprep_container_probe_command(
                            subject_id=subject_id,
                            request=request,
                            runtime_audit=runtime_audit,
                            work_dir=work_dir,
                            output_dir=output_dir,
                        ),
                        "stdout_path": str(log_dir / f"{FMRIPREP_CONTAINER_PROBE_STEP}.stdout.log"),
                        "stderr_path": str(log_dir / f"{FMRIPREP_CONTAINER_PROBE_STEP}.stderr.log"),
                        "work_dir": str(work_dir),
                        "output_dir": str(output_dir),
                    }
                )
            step_specs.append(step_spec)
        subjects.append(
            {
                "subject_id": subject_id,
                "session_ids": session_ids,
                "subject_key": subject_key,
                "status": "ready",
                "reason_codes": [],
                "steps": step_specs,
            }
        )
    pre_steps = _build_pre_steps(request, runtime_audit, run_id, subjects)
    plan = {
        "target": request.target,
        "backend": runtime_audit["selected_executor_policy"],
        "runtime": runtime_audit["selected_runtime"],
        "resources": dict(runtime_audit["resources"]),
        "execution_unit": "subject",
        "execution_strategy": runtime_audit.get("execution_strategy", "worker_pool"),
        "max_concurrency": runtime_audit["resources"]["max_jobs"],
        "pre_steps": pre_steps,
        "subjects": subjects,
    }
    warnings = _execution_plan_warnings(request, runtime_audit)
    if warnings:
        plan["warnings"] = warnings
    return plan


def build_step_command(
    step: PipelineStepName,
    subject_id: str,
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    work_dir: Path,
    output_dir: Path,
    session_ids: list[str] | None = None,
) -> list[str]:
    """Build the concrete command for one pipeline step.

    Inputs:
        step (str): Pipeline step name.
        subject_id (str): Subject identifier.
        request (RequestConfig): Workflow request after CLI/config normalization.
        runtime_audit (dict[str, Any]): Runtime audit summary for the current request.
        work_dir (Path): Work directory path for the current step.
        output_dir (Path): Output directory path for the current step.
        session_ids (list[str] | None): Session identifiers kept together for one
            subject.

    Returns:
        list[str]: Container command for the requested pipeline step.
    """
    runtime = runtime_audit["selected_runtime"] or "apptainer"
    runtime_executable = runtime_audit.get("selected_runtime_executable")
    resolved_images = runtime_audit.get("resolved_images") or {}
    resources = runtime_audit["resources"]
    if step == "fmriprep":
        container_env, templateflow_bind = _templateflow_env_and_bind(runtime_audit)
        if request.fs_license is None:
            raise ValueError("missing explicit fs_license")
        license_path = request.fs_license
        image = resolved_images.get("fmriprep") or request.fmriprep_image
        if not image:
            raise ValueError("missing explicit fmriprep image")
        binds = [
            BindMount(str(request.resolve_bids_root()), "/data", True),
            BindMount(str(output_dir), "/out", False),
            BindMount(str(work_dir), "/work", False),
            BindMount(str(license_path), "/opt/freesurfer/license.txt", True),
        ] + templateflow_bind
        args = [
            "/data",
            "/out",
            "participant",
            "--participant-label",
            subject_id,
            "-w",
            "/work",
            "--nthreads",
            str(resources["nthreads_per_job"]),
            "--omp-nthreads",
            str(resources["omp_nthreads"]),
            "--notrack",
            "--output-spaces",
            *request.output_spaces,
        ]
        args.extend(["--fs-license-file", "/opt/freesurfer/license.txt"])
        if request.fs_no_reconall:
            args.append("--fs-no-reconall")
        else:
            freesurfer_subjects_dir = _shared_freesurfer_subjects_dir(output_dir)
            binds.extend(
                [
                    BindMount(str(freesurfer_subjects_dir), FREESURFER_SUBJECTS_CONTAINER_HOME, False),
                ]
            )
            args.extend(["--fs-subjects-dir", FREESURFER_SUBJECTS_CONTAINER_HOME])
        if request.cifti_output == "91k":
            args.extend(["--cifti-output", "91k"])
        if request.task_id:
            args.extend(["--task-id", request.task_id])
        if request.echo_idx is not None:
            args.extend(["--echo-idx", str(request.echo_idx)])
        if request.anat_only:
            args.append("--anat-only")
        args.extend(_fmriprep_custom_args(request.fmriprep_custom_args))
        if request.sessions:
            args.extend(["--bids-filter-file", "/work/bids-filter.json"])
        if request.skip_bids_validation:
            args.append("--skip-bids-validation")
    elif step == "xcpd":
        container_env, templateflow_bind = _templateflow_env_and_bind(runtime_audit, require_ready=True)
        image = resolved_images.get("xcpd") or request.xcpd_image
        if not image:
            raise ValueError("missing explicit xcpd image")
        container_env.update(
            {
                "OMP_NUM_THREADS": str(resources["omp_nthreads"]),
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            }
        )
        binds = [
            BindMount(str(request.resolve_pipeline_output_root("fmriprep")), "/fmriprep", True),
            BindMount(str(output_dir), "/out", False),
            BindMount(str(work_dir), "/work", False),
        ] + templateflow_bind
        if request.fs_license is not None:
            binds.append(BindMount(str(request.fs_license), "/opt/freesurfer/license.txt", True))
        if request.xcpd_bids_filter_file is not None:
            binds.append(BindMount(str(request.xcpd_bids_filter_file), "/xcpd_bids_filter.json", True))
        for alias, dataset_path in sorted(request.xcpd_datasets.items()):
            binds.append(BindMount(str(dataset_path), f"/xcpd_datasets/{alias}", True))
        args = [
            "/fmriprep",
            "/out",
            "participant",
            "--participant-label",
            subject_id,
            "--mode",
            request.xcpd_mode,
            "--input-type",
            "fmriprep",
            "-w",
            "/work",
            "--nthreads",
            str(resources["nthreads_per_job"]),
            "--omp-nthreads",
            str(resources["omp_nthreads"]),
            "--notrack",
        ]
        if request.fs_license is not None:
            args.extend(["--fs-license-file", "/opt/freesurfer/license.txt"])
        if session_ids:
            args.append("--session-id")
            args.extend(session_ids)
        if request.xcpd_task_ids:
            args.append("--task-id")
            args.extend(request.xcpd_task_ids)
        if request.xcpd_bids_filter_file is not None:
            args.extend(["--bids-filter-file", "/xcpd_bids_filter.json"])
        if request.xcpd_datasets:
            args.append("--datasets")
            args.extend(f"{alias}=/xcpd_datasets/{alias}" for alias in sorted(request.xcpd_datasets))
        if request.xcpd_mem_mb is not None:
            args.extend(["--mem-mb", str(request.xcpd_mem_mb)])
        if request.xcpd_min_time > 0 or request.xcpd_min_time_explicit:
            args.extend(["--min-time", str(request.xcpd_min_time)])
        if request.xcpd_motion_filter_type and request.xcpd_motion_filter_type != "none":
            args.extend(["--motion-filter-type", request.xcpd_motion_filter_type])
            if request.xcpd_band_stop_min is not None:
                args.extend(["--band-stop-min", _format_xcpd_number(request.xcpd_band_stop_min)])
            if request.xcpd_band_stop_max is not None:
                args.extend(["--band-stop-max", _format_xcpd_number(request.xcpd_band_stop_max)])
            if request.xcpd_motion_filter_order is not None:
                args.extend(["--motion-filter-order", str(request.xcpd_motion_filter_order)])
        if request.xcpd_despike is not None:
            args.extend(["--despike", request.xcpd_despike])
        args.extend(_xcpd_custom_args(request.xcpd_custom_args))
    else:
        raise ValueError(f"Unsupported step: {step}")

    return build_container_command(
        ContainerSpec(engine=runtime, executable=runtime_executable, image=image, cleanenv=True, env=container_env),
        binds,
        args,
    )


def _format_xcpd_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _fmriprep_custom_args(values: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key in sorted(values):
        flag = FMRIPREP_CUSTOM_ARG_FLAGS[key]
        value = values[key]
        if key == "msm" and value is False:
            args.append("--no-msm")
        elif isinstance(value, bool):
            if value:
                args.append(flag)
        elif isinstance(value, list):
            args.append(flag)
            args.extend(str(item) for item in value)
        else:
            args.extend([flag, str(value)])
    return args


def _xcpd_custom_args(values: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key in sorted(values):
        flag = XCPD_CUSTOM_ARG_FLAGS[key]
        value = values[key]
        if key == "verbose":
            args.extend([flag] * int(value))
        elif isinstance(value, bool):
            if value:
                args.append(flag)
        elif isinstance(value, list):
            args.append(flag)
            args.extend(str(item) for item in value)
        else:
            args.extend([flag, str(value)])
    return args


def build_fmriprep_container_probe_command(
    *,
    subject_id: str,
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    work_dir: Path,
    output_dir: Path,
) -> list[str]:
    runtime = runtime_audit["selected_runtime"] or "apptainer"
    runtime_executable = runtime_audit.get("selected_runtime_executable")
    resolved_images = runtime_audit.get("resolved_images") or {}
    image = resolved_images.get("fmriprep") or request.fmriprep_image
    if not image:
        raise ValueError("missing explicit fmriprep image")
    if request.fs_license is None:
        raise ValueError("missing explicit fs_license")

    container_env, templateflow_bind = _templateflow_env_and_bind(runtime_audit)
    binds = [
        BindMount(str(request.resolve_bids_root()), "/data", True),
        BindMount(str(output_dir), "/out", False),
        BindMount(str(work_dir), "/work", False),
        BindMount(str(request.fs_license), "/opt/freesurfer/license.txt", True),
    ] + templateflow_bind
    if not request.fs_no_reconall:
        freesurfer_subjects_dir = _shared_freesurfer_subjects_dir(output_dir)
        binds.extend(
            [
                BindMount(str(freesurfer_subjects_dir), FREESURFER_SUBJECTS_CONTAINER_HOME, False),
            ]
        )
    is_docker = runtime.lower() == "docker"
    script = _python_exec_arg(
        _fmriprep_container_probe_script(
            subject_id,
            required_templateflow_templates(request),
            include_freesurfer=not request.fs_no_reconall,
            include_license=True,
        )
    )
    return build_container_command(
        ContainerSpec(
            engine=runtime,
            executable=runtime_executable,
            image=image,
            cleanenv=True,
            env=container_env,
            extra_args=["--entrypoint", "python"] if is_docker else [],
        ),
        binds,
        ["-c", script] if is_docker else ["python", "-c", script],
        singularity_action="exec",
    )


def _templateflow_env_and_bind(
    runtime_audit: dict[str, Any],
    *,
    require_ready: bool = False,
) -> tuple[dict[str, str], list[BindMount]]:
    templateflow_home = runtime_audit.get("templateflow_home")
    if not templateflow_home:
        return {}, []
    if require_ready and runtime_audit.get("templateflow_container_import_ready") is not True:
        return {}, []
    return {"TEMPLATEFLOW_HOME": TEMPLATEFLOW_CONTAINER_HOME, "TEMPLATEFLOW_AUTOUPDATE": "false"}, [
        BindMount(str(templateflow_home), TEMPLATEFLOW_CONTAINER_HOME, True)
    ]


def _execution_plan_warnings(request: RequestConfig, runtime_audit: dict[str, Any]) -> list[dict[str, str]]:
    if (
        request.target == "xcpd"
        and runtime_audit.get("templateflow_home")
        and runtime_audit.get("templateflow_container_import_ready") is not True
    ):
        return [
            {
                "code": "xcpd_templateflow_bind_skipped",
                "message": (
                    "XCP-D TemplateFlow bind was skipped because the saved "
                    "TemplateFlow container-import proof is advisory and not ready."
                ),
                "templateflow_home": str(runtime_audit["templateflow_home"]),
            }
        ]
    return []


def _fmriprep_container_probe_script(
    subject_id: str,
    required_templates: list[str],
    *,
    include_freesurfer: bool = True,
    include_license: bool = True,
) -> str:
    template_values = [f"    {template!r}," for template in required_templates]
    freesurfer_checks = []
    if include_license:
        freesurfer_checks.append("require_readable(Path(\"/opt/freesurfer/license.txt\"))")
    if include_freesurfer:
        freesurfer_checks.append("probe_writable_marker(\"/fsdir\")")
    return "\n".join(
        [
            "import os",
            "import subprocess",
            "from pathlib import Path",
            "from uuid import uuid4",
            "",
            f"subject_id = {subject_id!r}",
            "",
            "def require_readable(path):",
            "    path = Path(path)",
            "    if not path.exists():",
            "        raise RuntimeError(f'Missing path: {path}')",
            "    if path.is_dir():",
            "        next(path.iterdir(), None)",
            "        return",
            "    with path.open('rb') as handle:",
            "        handle.read(1)",
            "",
            "def probe_writable_marker(root):",
            "    marker_root = Path(root) / '.fmri_process'",
            "    marker_dir = marker_root / 'fmriprep-container-probe'",
            "    marker_dir.mkdir(parents=True, exist_ok=True)",
            "    marker_path = marker_dir / f'sub-{subject_id}_writable_probe.{uuid4().hex}.txt'",
            "    with marker_path.open(\"x\", encoding=\"utf-8\") as handle:",
            "        handle.write('ok\\n')",
            "    try:",
            "        marker_path.unlink()",
            "    except OSError:",
            "        return",
            "",
            "def require_readonly(path):",
            "    path = Path(path)",
            "    require_readable(path)",
            "    readonly_flag = getattr(os, 'ST_RDONLY', 1)",
            "    if not os.statvfs(path).f_flag & readonly_flag:",
            "        raise RuntimeError(f'Expected read-only mount: {path}')",
            "",
            "def require_template_dir(templateflow_root, template):",
            "    template_dir = templateflow_root / f\"tpl-{template}\"",
            "    if not template_dir.is_dir():",
            "        raise RuntimeError(f'Missing TemplateFlow template directory: {template_dir}')",
            "",
            "version = subprocess.run([\"fmriprep\", \"--version\"], capture_output=True, text=True, check=False)",
            "print((version.stdout or version.stderr).strip(), flush=True)",
            "if version.returncode != 0:",
            "    raise SystemExit(version.returncode)",
            "require_readonly(Path(\"/data\"))",
            "probe_writable_marker(\"/out\")",
            "probe_writable_marker(\"/work\")",
            *freesurfer_checks,
            "if \"TEMPLATEFLOW_HOME\" in os.environ:",
            "    import templateflow",
            "    templateflow_root = Path(os.environ[\"TEMPLATEFLOW_HOME\"])",
            "    if not templateflow_root.is_dir():",
            "        raise RuntimeError(f'Missing TEMPLATEFLOW_HOME directory: {templateflow_root}')",
            "    require_readonly(templateflow_root)",
            "    required_templates = [",
            *template_values,
            "    ]",
            "    for template in required_templates:",
            "        require_template_dir(templateflow_root, template)",
        ]
    )


def _python_exec_arg(script: str) -> str:
    return f"exec({script!r})"


def build_container_command(
    spec: ContainerSpec,
    binds: list[BindMount],
    container_args: list[str],
    *,
    singularity_action: str = "run",
) -> list[str]:
    engine = spec.engine.lower()
    executable = spec.executable or engine
    if engine in {"apptainer", "singularity"}:
        cmd = [executable, singularity_action]
        if spec.cleanenv:
            cmd.append("--cleanenv")
        for key, value in spec.env.items():
            cmd.extend(["--env", f"{key}={value}"])
        cmd.extend(spec.extra_args)
        for bind in binds:
            cmd.extend(["-B", _render_bind(bind)])
        cmd.append(spec.image)
        cmd.extend(container_args)
        return cmd
    if engine == "docker":
        cmd = [executable, "run", "--rm"]
        for key, value in spec.env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend(spec.extra_args)
        for bind in binds:
            cmd.extend(["--mount", _render_docker_mount(bind)])
        cmd.append(spec.image.removeprefix("docker://"))
        cmd.extend(container_args)
        return cmd
    raise ValueError(f"Unsupported container engine: {spec.engine}")


def _render_bind(bind: BindMount) -> str:
    suffix = ":ro" if bind.read_only else ""
    return f"{bind.source}:{bind.target}{suffix}"


def _render_docker_mount(bind: BindMount) -> str:
    suffix = ",readonly" if bind.read_only else ""
    return f"type=bind,source={bind.source},target={bind.target}{suffix}"


def _build_pre_steps(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    run_id: str,
    subjects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if request.fs_no_reconall:
        return []
    if not any(any(step["step"] == "fmriprep" for step in subject.get("steps", [])) for subject in subjects):
        return []
    return [_build_freesurfer_prewarm_step(request, runtime_audit, run_id)]


def _build_freesurfer_prewarm_step(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    output_dir = _shared_freesurfer_subjects_dir(request.resolve_pipeline_output_root("fmriprep"))
    log_dir = request.resolve_log_root() / run_id / "_shared"
    return {
        "step": FREESURFER_PREWARM_STEP,
        "command": build_freesurfer_prewarm_command(request, runtime_audit, output_dir),
        "stdout_path": str(log_dir / f"{FREESURFER_PREWARM_STEP}.stdout.log"),
        "stderr_path": str(log_dir / f"{FREESURFER_PREWARM_STEP}.stderr.log"),
        "output_dir": str(output_dir),
    }


def build_freesurfer_prewarm_command(
    request: RequestConfig,
    runtime_audit: dict[str, Any],
    freesurfer_subjects_dir: Path,
) -> list[str]:
    runtime = runtime_audit["selected_runtime"] or "apptainer"
    runtime_executable = runtime_audit.get("selected_runtime_executable")
    resolved_images = runtime_audit.get("resolved_images") or {}
    image = resolved_images.get("fmriprep") or request.fmriprep_image
    if not image:
        raise ValueError("missing explicit fmriprep image")
    is_docker = runtime.lower() == "docker"
    container_env: dict[str, str] = {
        "FREESURFER_PREWARM_SUBJECTS": ",".join(_selected_freesurfer_prewarm_subjects(request.output_spaces))
    }
    binds = [BindMount(str(freesurfer_subjects_dir), FREESURFER_SUBJECTS_CONTAINER_HOME, False)]
    return build_container_command(
        ContainerSpec(
            engine=runtime,
            executable=runtime_executable,
            image=image,
            cleanenv=True,
            env=container_env,
            extra_args=["--entrypoint", "python"] if is_docker else [],
        ),
        binds,
        ["-c", FREESURFER_PREWARM_SCRIPT] if is_docker else ["python", "-c", FREESURFER_PREWARM_SCRIPT],
        singularity_action="exec",
    )


def _shared_freesurfer_subjects_dir(output_dir: Path) -> Path:
    return output_dir / "sourcedata" / "freesurfer"


def _selected_freesurfer_prewarm_subjects(output_spaces: list[str] | None) -> list[str]:
    subjects = ["fsaverage"]
    for output_space in output_spaces or []:
        parts = output_space.split(":")
        name = parts[0]
        modifiers = set(parts[1:])
        if name == "fsaverage5" or (name == "fsaverage" and "den-10k" in modifiers):
            subjects.append("fsaverage5")
        if name == "fsaverage6" or (name == "fsaverage" and "den-41k" in modifiers):
            subjects.append("fsaverage6")
    return list(dict.fromkeys(subjects))


def _bids_filter_content(session_ids: list[str] | None) -> dict[str, dict[str, list[str]]]:
    return {"bold": {"session": [str(value) for value in session_ids or []]}}
