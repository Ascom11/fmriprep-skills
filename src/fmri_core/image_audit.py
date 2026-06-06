"""Image source normalization and readiness probes."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from . import runtime_probe
from .disk import WINDOWS_PATH_RE
from .models import DEFAULT_FMRIPREP_IMAGE, DEFAULT_XCPD_IMAGE, RequestConfig, RequestPath
from .shell import argv_command, path_exists, run_command, shell_command


REMOTE_REGISTRY_PROBE_PARTS = ("curl", "-I", "-L", "--max-time", "10", "https://registry-1.docker.io/v2/")
REMOTE_REGISTRY_PROBE_COMMAND = " ".join(REMOTE_REGISTRY_PROBE_PARTS)
REMOTE_REGISTRY_PROBE_TIMEOUT_SECONDS = 10
IMAGE_VALIDATE_TIMEOUT_SECONDS = 300


def required_images(request: RequestConfig) -> dict[str, str | None]:
    if request.target == "fmriprep":
        return {"fmriprep": request.fmriprep_image or DEFAULT_FMRIPREP_IMAGE}
    return {"xcpd": request.xcpd_image or DEFAULT_XCPD_IMAGE}


def resolve_images(
    request: RequestConfig,
    selected_runtime: str | None,
    image_root: RequestPath,
    *,
    remote_probe: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    images = dict(required_images(request))
    if selected_runtime in {"apptainer", "singularity"}:
        for pipeline in runtime_requirement_pipelines(request):
            if not _pipeline_image_omitted(request, pipeline):
                continue
            persisted_image = image_root / f"{pipeline}.sif"
            if request.remote_host:
                if runtime_probe.remote_probe_persisted_image_valid(remote_probe, pipeline):
                    images[pipeline] = str(persisted_image)
            elif image_entry_ready(request, selected_runtime, pipeline, persisted_image):
                images[pipeline] = str(persisted_image)
    if selected_runtime == "docker":
        for pipeline, image in list(images.items()):
            if image and _pipeline_image_omitted(request, pipeline):
                images[pipeline] = _docker_registry_image(image)
    return images


def _pipeline_image_omitted(request: RequestConfig, pipeline: str) -> bool:
    if pipeline == "fmriprep":
        return request.fmriprep_image is None
    if pipeline == "xcpd":
        return request.xcpd_image is None
    return False


def _persisted_image_usable(
    request: RequestConfig,
    selected_runtime: str | None,
    pipeline: str,
    image_path: RequestPath,
) -> bool:
    if selected_runtime not in {"apptainer", "singularity"}:
        return False
    command = _sif_validation_command(selected_runtime, pipeline, image_path)
    try:
        result = run_command(
            shell_command(command),
            remote_host=request.remote_host,
            check=False,
            timeout=IMAGE_VALIDATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _sif_validation_command(selected_runtime: str, pipeline: str, image_path: RequestPath) -> str:
    image = shlex.quote(str(image_path))
    if pipeline == "fmriprep":
        return f'{selected_runtime} exec --cleanenv {image} python -c "{_sif_validation_python()}"'
    command_name = "xcp_d" if pipeline == "xcpd" else pipeline
    return f"{selected_runtime} exec --cleanenv {image} {shlex.quote(command_name)} --version"


def _sif_validation_python() -> str:
    return "import shutil, importlib.metadata as m; assert shutil.which('fmriprep'); print(m.version('fmriprep'))"


def _plain_docker_registry_image(value: str | None) -> bool:
    if not value:
        return False
    image = value.strip()
    if not image or "://" in image:
        return False
    return not looks_like_local_path(image)


def _docker_registry_image(value: str) -> str:
    return value.removeprefix("docker://")


def _docker_image_present(request: RequestConfig, image: str) -> bool:
    try:
        result = run_command(
            argv_command(["docker", "image", "inspect", image]),
            remote_host=request.remote_host,
            check=False,
            timeout=20,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _docker_validation_command_parts(pipeline: str, image: str) -> list[str]:
    if pipeline == "fmriprep":
        return [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--entrypoint",
            "python",
            image,
            "-c",
            _sif_validation_python(),
        ]
    command_name = "xcp_d" if pipeline == "xcpd" else pipeline
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--entrypoint",
        command_name,
        image,
        "--version",
    ]


def _docker_image_usable(request: RequestConfig, pipeline: str, image: str) -> bool:
    try:
        result = run_command(
            argv_command(_docker_validation_command_parts(pipeline, image)),
            remote_host=request.remote_host,
            check=False,
            timeout=IMAGE_VALIDATE_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def image_entry_ready(
    request: RequestConfig,
    selected_runtime: str | None,
    pipeline: str,
    image: str | RequestPath,
) -> bool:
    """Return whether a prepared image is usable by the selected runtime."""
    image_text = str(image)
    if selected_runtime == "docker":
        registry_image = _docker_registry_image(image_text) if image_text.startswith("docker://") else image_text
        return _docker_image_present(request, registry_image) and _docker_image_usable(request, pipeline, registry_image)
    if selected_runtime in {"apptainer", "singularity"}:
        path: RequestPath = PurePosixPath(image_text) if request.remote_host else Path(image_text)
        return path_exists(str(path), request.remote_host) and _persisted_image_usable(
            request,
            selected_runtime,
            pipeline,
            path,
        )
    return False


def looks_like_remote_image(value: str) -> bool:
    return value.startswith(("docker://", "library://", "oras://", "http://", "https://"))


def looks_like_local_path(value: str) -> bool:
    if looks_like_remote_image(value):
        return False
    return bool(WINDOWS_PATH_RE.match(value)) or value.startswith("/") or value.endswith((".sif", ".simg"))


def image_configuration_findings(
    request: RequestConfig,
    selected_runtime: str | None,
    resolved_images: dict[str, str | None],
    *,
    actionable: bool,
) -> dict[str, list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    bucket = blockers if actionable else warnings
    for pipeline in runtime_requirement_pipelines(request):
        image = resolved_images.get(pipeline)
        if not image:
            continue
        if selected_runtime == "docker":
            if looks_like_remote_image(image) or looks_like_local_path(image):
                bucket.append("docker_runtime_requires_registry_image")
            continue
        if selected_runtime in {"apptainer", "singularity"} and WINDOWS_PATH_RE.match(image):
            bucket.append("posix_runtime_requires_posix_image_path")
    return {"blockers": _dedupe(blockers), "warnings": _dedupe(warnings)}


def image_validation_findings(
    request: RequestConfig,
    selected_runtime: str | None,
    *,
    resolved_images: dict[str, str | None],
    image_root: RequestPath,
    remote_probe: dict[str, Any] | None,
    actionable: bool,
) -> dict[str, list[str]]:
    if selected_runtime not in {"apptainer", "singularity", "docker"}:
        return {"warnings": [], "blockers": []}
    warnings: list[str] = []
    blockers: list[str] = []
    requested_images = required_images(request)
    for pipeline in runtime_requirement_pipelines(request):
        image = requested_images.get(pipeline)
        if selected_runtime == "docker":
            docker_image_present = _docker_image_present(request, image) if image else False
            if image and _plain_docker_registry_image(image) and docker_image_present:
                if not image_entry_ready(request, selected_runtime, pipeline, image):
                    (blockers if actionable else warnings).append(f"invalid_{pipeline}_image")
            continue
        if image and looks_like_local_path(image):
            if request.remote_host and runtime_probe.remote_probe_succeeded(remote_probe):
                exists = runtime_probe.remote_probe_requested_local_image_exists(remote_probe, pipeline)
                valid = runtime_probe.remote_probe_requested_local_image_valid(remote_probe, pipeline)
            else:
                exists = path_exists(image, request.remote_host)
                valid = exists and image_entry_ready(request, selected_runtime, pipeline, image)
            if exists and not valid:
                (blockers if actionable else warnings).append(f"invalid_{pipeline}_image")
            continue
    return {"warnings": _dedupe(warnings), "blockers": _dedupe(blockers)}


def image_prepare_required_codes(
    request: RequestConfig,
    selected_runtime: str | None,
    resolved_images: dict[str, str | None],
) -> list[str]:
    if selected_runtime not in {"apptainer", "singularity", "docker"}:
        return []
    codes: list[str] = []
    for pipeline in runtime_requirement_pipelines(request):
        image = resolved_images.get(pipeline)
        if selected_runtime == "docker":
            docker_image_present = _docker_image_present(request, image) if image else False
            if image and _plain_docker_registry_image(image) and not docker_image_present:
                codes.append(f"prepare_runtime_required_{pipeline}_image")
            continue
        if image and _sif_pull_source_image(image):
            codes.append(f"prepare_runtime_required_{pipeline}_image")
    return _dedupe(codes)


def _sif_pull_source_image(image: str | None) -> bool:
    if not image:
        return False
    return looks_like_remote_image(image) or _plain_docker_registry_image(image)


def prepare_image_source(image: str | None, selected_runtime: str | None) -> str | None:
    if image and selected_runtime in {"apptainer", "singularity"} and _plain_docker_registry_image(image):
        return f"docker://{image}"
    return image


def prepare_network_check(
    request: RequestConfig,
    *,
    source: str | None,
    registry_probe: dict[str, str | None],
) -> dict[str, str | None]:
    if not request.remote_host or not source or not (looks_like_remote_image(source) or _plain_docker_registry_image(source)):
        return {"status": "not_applicable", "command": None, "detail": None}
    if registry_probe.get("status") == "failed":
        return {
            "status": "failed",
            "command": REMOTE_REGISTRY_PROBE_COMMAND,
            "detail": registry_probe.get("detail"),
        }
    if registry_probe.get("status") == "passed":
        return {"status": "passed", "command": REMOTE_REGISTRY_PROBE_COMMAND, "detail": None}
    return {"status": "not_applicable", "command": None, "detail": None}


def remote_registry_probe_check(
    request: RequestConfig,
    *,
    selected_runtime: str | None,
    image_prepare_codes: list[str],
) -> dict[str, str | None]:
    if (
        not request.remote_host
        or selected_runtime not in {"apptainer", "docker", "singularity"}
        or not image_prepare_codes
    ):
        return {"status": "not_applicable", "detail": None}
    result = remote_registry_probe(request)
    if result.returncode == 0:
        return {"status": "passed", "detail": None}
    stderr = (result.stderr or "").strip()
    suffix = f" Probe stderr: {stderr}" if stderr else ""
    return {
        "status": "failed",
        "detail": (
            f"{request.remote_host} likely has no outbound network or registry access; "
            f"`{REMOTE_REGISTRY_PROBE_COMMAND}` failed before image prepare.{suffix}"
        ),
    }


def remote_registry_probe(request: RequestConfig) -> subprocess.CompletedProcess[str]:
    try:
        return run_command(
            argv_command(REMOTE_REGISTRY_PROBE_PARTS),
            remote_host=request.remote_host,
            check=False,
            timeout=REMOTE_REGISTRY_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return _timeout_completed_process(
            exc,
            f"registry probe timed out after {REMOTE_REGISTRY_PROBE_TIMEOUT_SECONDS}s",
        )


def _timeout_completed_process(exc: subprocess.TimeoutExpired, message: str) -> subprocess.CompletedProcess[str]:
    stdout = exc.output.decode("utf-8", errors="replace") if isinstance(exc.output, bytes) else (exc.output or "")
    stderr_value = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    stderr = "\n".join(value for value in [stderr_value.strip(), message] if value)
    return subprocess.CompletedProcess(exc.cmd, 124, stdout, stderr)


def runtime_requirement_pipelines(request: RequestConfig) -> list[str]:
    if request.target == "fmriprep":
        return ["fmriprep"]
    return ["xcpd"]


def resolved_image_findings(
    request: RequestConfig,
    resolved_images: dict[str, str | None],
    *,
    image_root: RequestPath,
    remote_probe: dict[str, Any] | None = None,
    actionable: bool,
) -> dict[str, list[str]]:
    warnings: list[str] = []
    blockers: list[str] = []
    required_pipelines = set(runtime_requirement_pipelines(request))
    for pipeline, image in resolved_images.items():
        if pipeline not in required_pipelines:
            continue
        missing = not image
        if not missing and looks_like_local_path(image):
            if request.remote_host and runtime_probe.remote_probe_succeeded(remote_probe):
                requested_image = required_images(request).get(pipeline)
                if requested_image == image:
                    missing = not runtime_probe.remote_probe_requested_local_image_exists(remote_probe, pipeline)
                    if not missing:
                        continue
                persisted = str(image_root / f"{pipeline}.sif")
                if image == persisted:
                    missing = not runtime_probe.remote_probe_persisted_image_exists(remote_probe, pipeline)
                else:
                    missing = not runtime_probe.remote_probe_requested_local_image_exists(remote_probe, pipeline)
            else:
                missing = not path_exists(image, request.remote_host)
        if not missing:
            continue
        code = f"missing_{pipeline}_image"
        if actionable:
            blockers.append(code)
        else:
            warnings.append(code)
    return {"warnings": warnings, "blockers": blockers}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
