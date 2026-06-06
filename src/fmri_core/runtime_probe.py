"""Remote runtime probe rendering and parsing."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from . import templateflow_audit
from .models import RequestConfig
from .shell import clean_remote_startup_output, run_command, shell_command


REMOTE_RUNTIME_COMMANDS = ("apptainer", "singularity", "docker", "sbatch")
REMOTE_RUNTIME_PROBE_TIMEOUT_SECONDS = 180

def _compact_probe_error(text: str) -> str:
    return " ".join(text.split())[:500]


def probe_remote_runtime(
    request: RequestConfig,
    *,
    required_templates: list[str] | None = None,
    required_images: Mapping[str, str | None] | None = None,
    local_path_predicate: Callable[[str], bool] | None = None,
) -> dict[str, Any] | None:
    """Collect remote runtime facts in one SSH round-trip when ``remote_host`` is set.

    Inputs:
        request (RequestConfig): Workflow request after CLI/config normalization.

    Returns:
        dict[str, Any] | None: Remote runtime probe payload. The dictionary may
            contain:
            - ``probe_ok``: Whether the batched probe completed successfully.
            - ``hostname``: Resolved remote hostname.
            - ``commands``: Availability map for ``apptainer``,
              ``singularity``, ``docker``, and ``sbatch``.
            - ``command_paths``: Executable paths for the same command set when
              available.
            - ``cpu_total``: Total detected CPU count.
            - ``memory_gb``: Total detected memory in gigabytes.
            - ``home``: Resolved remote home directory.
            - ``templateflow_home``: Resolved remote TemplateFlow cache path.
            - ``shared_paths``: Shared cache-path existence flags.
            - ``persisted_images``: Shared cached image existence flags.
            - ``requested_local_images``: Explicit local image existence flags.
            - ``requested_local_image_valid``: Explicit local image validation flags.
            Returns ``None`` when the request does not target a remote host.
    """
    remote_host = request.remote_host
    if remote_host is None:
        return None
    template_names = list(required_templates if required_templates is not None else templateflow_audit.required_templateflow_templates(request))
    image_requests = dict(required_images or {})
    is_local_path = local_path_predicate or (lambda value: value.startswith(("/", "./", "../", "~")))
    image_root = request.resolve_image_root()
    output_root = request.resolve_output_root()
    work_root = request.resolve_work_root()
    log_root = request.resolve_log_root()
    fs_license = request.fs_license
    templateflow_home = request.templateflow_home
    if templateflow_home is None and template_names:
        templateflow_home = request.resolve_download_root() / "templateflow"
    explicit_templateflow_block = ""
    if templateflow_home is not None:
        explicit_templateflow_block = f"""
templateflow_home={shlex.quote(str(templateflow_home))}
"""
    explicit_local_image_block = ""
    for pipeline, image in image_requests.items():
        if not image or not is_local_path(image):
            continue
        explicit_local_image_block += f"""
if [ -e {shlex.quote(image)} ]; then
  printf 'requested_local_images.{pipeline}=true\\n'
  check_image_valid requested_local_image_valid {shlex.quote(pipeline)} {shlex.quote(image)}
else
  printf 'requested_local_images.{pipeline}=false\\n'
  printf 'requested_local_image_valid.{pipeline}=false\\n'
fi
"""
    command = """
hostname_value="$(hostname 2>/dev/null || printf '%s' \"$HOSTNAME\")"
home_value="${HOME:-}"
slurm_job_id="${SLURM_JOB_ID:-${SLURM_JOBID:-${SLURM_STEP_ID:-${SLURM_STEPID:-}}}}"
current_host_is_slurm_node=false
if command -v sinfo >/dev/null 2>&1 && [ -n "$hostname_value" ]; then
  if sinfo -h -N -n "$hostname_value" 2>/dev/null | awk '{print $1}' | grep -Fx -- "$hostname_value" >/dev/null 2>&1; then
    current_host_is_slurm_node=true
  fi
fi
cpu_total="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1')"
memory_kb="$(awk '/MemTotal/ {print $2; exit}' /proc/meminfo 2>/dev/null)"
memory_gb=""
if [ -n "$memory_kb" ]; then
  memory_gb="$((memory_kb / 1024 / 1024))"
if [ "$memory_gb" -lt 1 ]; then
    memory_gb="1"
  fi
fi
templateflow_home=""
__EXPLICIT_TEMPLATEFLOW_CHECK__
shared_image_root=__IMAGE_ROOT__
output_root=__OUTPUT_ROOT__
work_root=__WORK_ROOT__
log_root=__LOG_ROOT__
fs_license=__FS_LICENSE__
templateflow_probe_root=""
if [ -n "$templateflow_home" ] && [ -e "$templateflow_home" ]; then
  templateflow_probe_root="$templateflow_home"
fi
printf 'hostname=%s\\n' "$hostname_value"
printf 'home=%s\\n' "$home_value"
printf 'slurm_job_id=%s\\n' "$slurm_job_id"
printf 'current_host_is_slurm_node=%s\\n' "$current_host_is_slurm_node"
for name in apptainer singularity docker sbatch; do
  if path="$(command -v "$name" 2>/dev/null)"; then
    printf 'commands.%s=true\\n' "$name"
    printf 'command_paths.%s=%s\\n' "$name" "$path"
  else
    printf 'commands.%s=false\\n' "$name"
    printf 'command_paths.%s=\\n' "$name"
  fi
done
if command -v docker >/dev/null 2>&1; then
  if docker_info_error="$(docker info 2>&1 >/dev/null)"; then
    printf 'docker_daemon_available=true\\n'
    printf 'docker_daemon_error=\\n'
  else
    docker_info_error="$(printf '%s' "$docker_info_error" | tr '\\n' ' ')"
    printf 'docker_daemon_available=false\\n'
    printf 'docker_daemon_error=%s\\n' "$docker_info_error"
  fi
else
  printf 'docker_daemon_available=false\\n'
  printf 'docker_daemon_error=\\n'
fi
printf 'cpu_total=%s\\n' "$cpu_total"
printf 'memory_gb=%s\\n' "$memory_gb"
printf 'templateflow_home=%s\\n' "$templateflow_home"
printf 'templateflow_probe_root=%s\\n' "$templateflow_probe_root"
if [ -n "$templateflow_probe_root" ]; then
  printf 'shared_paths.templateflow=true\\n'
else
  printf 'shared_paths.templateflow=false\\n'
fi
if [ -n "$fs_license" ] && [ -f "$fs_license" ] && [ -r "$fs_license" ]; then
  printf 'shared_paths.fs_license=true\\n'
else
  printf 'shared_paths.fs_license=false\\n'
fi
(
__TEMPLATEFLOW_TOOL_PATH__
__TEMPLATEFLOW_ANNEX_PROBE_FUNCTIONS__
annex_args=""
for template in __TEMPLATEFLOW_REQUIRED__; do
  annex_args="$annex_args tpl-$template"
done
# shellcheck disable=SC2086
annex_probe_output="$(check_templateflow_annex "$templateflow_probe_root" $annex_args)"
printf '%s\\n' "$annex_probe_output"
)
check_image_valid() {
  key="$1"
  pipeline="$2"
  candidate="$3"
  for runtime in apptainer singularity; do
    if command -v "$runtime" >/dev/null 2>&1; then
      if [ "$pipeline" = "fmriprep" ]; then
        python_code="import shutil, importlib.metadata as m; assert shutil.which('fmriprep'); print(m.version('fmriprep'))"
        if timeout 300 "$runtime" exec --cleanenv "$candidate" python -c "$python_code" >/dev/null 2>&1; then
          printf '%s.%s=true\\n' "$key" "$pipeline"
          return
        fi
      elif [ "$pipeline" = "xcpd" ]; then
        if timeout 300 "$runtime" exec --cleanenv "$candidate" xcp_d --version >/dev/null 2>&1; then
          printf '%s.%s=true\\n' "$key" "$pipeline"
          return
        fi
      else
        printf '%s.%s=false\\n' "$key" "$pipeline"
        return
      fi
    fi
  done
  printf '%s.%s=false\\n' "$key" "$pipeline"
}
for pipeline in fmriprep xcpd; do
  candidate="$shared_image_root/$pipeline.sif"
  if [ -e "$candidate" ]; then
    printf 'persisted_images.%s=true\\n' "$pipeline"
    check_image_valid persisted_image_valid "$pipeline" "$candidate"
  else
    printf 'persisted_images.%s=false\\n' "$pipeline"
    printf 'persisted_image_valid.%s=false\\n' "$pipeline"
  fi
done
check_writable() {
  key="$1"
  target="$2"
  if [ -e "$target" ]; then
    if [ -d "$target" ] && [ -w "$target" ] && [ -x "$target" ]; then
      printf 'writable_paths.%s=true\\n' "$key"
    else
      printf 'writable_paths.%s=false\\n' "$key"
    fi
    return
  fi
  parent="$(dirname "$target")"
  while [ ! -e "$parent" ]; do
    next="$(dirname "$parent")"
    if [ "$next" = "$parent" ]; then
      printf 'writable_paths.%s=false\\n' "$key"
      return
    fi
    parent="$next"
  done
  if [ -d "$parent" ] && [ -w "$parent" ] && [ -x "$parent" ]; then
    printf 'writable_paths.%s=true\\n' "$key"
  else
    printf 'writable_paths.%s=false\\n' "$key"
  fi
}
check_writable output_root "$output_root"
check_writable work_root "$work_root"
check_writable log_root "$log_root"
__REQUESTED_LOCAL_IMAGE_CHECKS__
    """.replace("__EXPLICIT_TEMPLATEFLOW_CHECK__", explicit_templateflow_block).replace(
        "__REQUESTED_LOCAL_IMAGE_CHECKS__", explicit_local_image_block
    ).replace(
        "__TEMPLATEFLOW_TOOL_PATH__", templateflow_audit.remote_templateflow_tool_path_line(request)
    ).replace(
        "__TEMPLATEFLOW_ANNEX_PROBE_FUNCTIONS__", templateflow_audit.templateflow_remote_annex_probe_functions()
    ).replace(
        "__IMAGE_ROOT__", shlex.quote(str(image_root))
    ).replace(
        "__OUTPUT_ROOT__", shlex.quote(str(output_root))
    ).replace(
        "__WORK_ROOT__", shlex.quote(str(work_root))
    ).replace(
        "__LOG_ROOT__", shlex.quote(str(log_root))
    ).replace(
        "__FS_LICENSE__", shlex.quote(str(fs_license)) if fs_license is not None else "''"
    ).replace(
        "__TEMPLATEFLOW_REQUIRED__", " ".join(shlex.quote(name) for name in template_names)
    ).strip()
    try:
        result = run_command(
            shell_command(command),
            remote_host=remote_host,
            check=False,
            timeout=REMOTE_RUNTIME_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return default_remote_runtime_probe(
            remote_host,
            required_templates=template_names,
            error=_compact_probe_error(str(exc)),
        )
    if result.returncode != 0:
        return default_remote_runtime_probe(
            remote_host,
            required_templates=template_names,
            error=_compact_probe_error((result.stderr or "").strip() or (result.stdout or "").strip())
            or f"remote runtime probe exited with {result.returncode}",
            returncode=result.returncode,
            stdout=_compact_probe_error(result.stdout or ""),
            stderr=_compact_probe_error(result.stderr or ""),
        )
    return _parse_remote_runtime_probe(result.stdout, remote_host, required_templates=template_names)


def default_remote_runtime_probe(
    remote_host: str,
    *,
    required_templates: list[str] | tuple[str, ...] | None = None,
    error: str | None = None,
    returncode: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> dict[str, Any]:
    """Return a safe fallback payload when the batched remote probe fails."""
    return {
        "probe_ok": False,
        "error": error or "remote runtime probe failed",
        "returncode": returncode,
        "stdout": stdout or "",
        "stderr": stderr or "",
        "hostname": remote_host,
        "commands": {name: False for name in REMOTE_RUNTIME_COMMANDS},
        "command_paths": {name: None for name in REMOTE_RUNTIME_COMMANDS},
        "docker_daemon_available": False,
        "docker_daemon_error": None,
        "cpu_total": 1,
        "memory_gb": None,
        "home": None,
        "slurm_job_id": None,
        "current_host_is_slurm_node": False,
        "templateflow_home": None,
        "templateflow_probe_root": None,
        "templateflow_annex_status": "skipped",
        "templateflow_annex_missing_paths": "",
        "templateflow_annex_error": "",
        "shared_paths": {"templateflow": False, "fs_license": False},
        "persisted_images": {"fmriprep": False, "xcpd": False},
        "persisted_image_valid": {"fmriprep": False, "xcpd": False},
        "requested_local_images": {"fmriprep": False, "xcpd": False},
        "requested_local_image_valid": {"fmriprep": False, "xcpd": False},
        "writable_paths": {
            "output_root": False,
            "work_root": False,
            "log_root": False,
        },
    }


def _parse_remote_runtime_probe(
    stdout: str,
    remote_host: str,
    *,
    required_templates: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Parse one remote runtime probe payload from JSON or simple key/value lines."""
    payload = clean_remote_startup_output(stdout).strip()
    if not payload:
        return default_remote_runtime_probe(
            remote_host,
            required_templates=required_templates,
            error="remote runtime probe returned no output",
        )
    if payload.startswith("{"):
        try:
            return _normalize_remote_runtime_probe(json.loads(payload), remote_host, required_templates=required_templates)
        except json.JSONDecodeError:
            return default_remote_runtime_probe(
                remote_host,
                required_templates=required_templates,
                error="remote runtime probe returned invalid JSON",
            )
    raw: dict[str, str] = {}
    for line in payload.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        raw[key.strip()] = value
    if not raw:
        return default_remote_runtime_probe(
            remote_host,
            required_templates=required_templates,
            error="remote runtime probe returned no parseable facts",
        )
    return _normalize_remote_runtime_probe(raw, remote_host, required_templates=required_templates)


def _normalize_remote_runtime_probe(
    payload: dict[str, Any],
    remote_host: str,
    *,
    required_templates: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Normalize a remote runtime probe into one predictable dictionary."""
    template_names = list(required_templates if required_templates is not None else templateflow_audit.REQUIRED_TEMPLATEFLOW_TEMPLATES)
    defaults = default_remote_runtime_probe(remote_host, required_templates=template_names)
    if "probe_ok" in payload and not coerce_remote_bool(payload.get("probe_ok")):
        return default_remote_runtime_probe(
            remote_host,
            required_templates=template_names,
            error=_compact_probe_error(str(payload.get("error") or "")) or "remote runtime probe reported failure",
            returncode=_coerce_probe_returncode(payload.get("returncode")),
            stdout=_compact_probe_error(str(payload.get("stdout") or "")),
            stderr=_compact_probe_error(str(payload.get("stderr") or "")),
        )
    commands_payload = payload.get("commands") if isinstance(payload.get("commands"), dict) else {}
    command_paths_payload = payload.get("command_paths") if isinstance(payload.get("command_paths"), dict) else {}
    shared_paths_payload = payload.get("shared_paths") if isinstance(payload.get("shared_paths"), dict) else {}
    persisted_images_payload = payload.get("persisted_images") if isinstance(payload.get("persisted_images"), dict) else {}
    persisted_image_valid_payload = (
        payload.get("persisted_image_valid") if isinstance(payload.get("persisted_image_valid"), dict) else {}
    )
    requested_local_images_payload = (
        payload.get("requested_local_images") if isinstance(payload.get("requested_local_images"), dict) else {}
    )
    requested_local_image_valid_payload = (
        payload.get("requested_local_image_valid") if isinstance(payload.get("requested_local_image_valid"), dict) else {}
    )
    writable_paths_payload = payload.get("writable_paths") if isinstance(payload.get("writable_paths"), dict) else {}
    commands: dict[str, bool] = {}
    command_paths: dict[str, str | None] = {}
    shared_paths = {
        "templateflow": coerce_remote_bool(
            shared_paths_payload.get(
                "templateflow",
                payload.get("shared_paths.templateflow", defaults["shared_paths"]["templateflow"]),
            )
        ),
        "fs_license": coerce_remote_bool(
            shared_paths_payload.get(
                "fs_license",
                payload.get("shared_paths.fs_license", defaults["shared_paths"]["fs_license"]),
            )
        ),
    }
    persisted_images: dict[str, bool] = {}
    persisted_image_valid: dict[str, bool] = {}
    requested_local_images: dict[str, bool] = {}
    requested_local_image_valid: dict[str, bool] = {}
    writable_paths: dict[str, bool] = {}
    for name in REMOTE_RUNTIME_COMMANDS:
        raw_command = commands_payload.get(
            name,
            payload.get(
                f"commands.{name}",
                payload.get(f"command.{name}", payload.get(f"command_{name}", defaults["commands"][name])),
            ),
        )
        raw_path = command_paths_payload.get(
            name,
            payload.get(
                f"command_paths.{name}",
                payload.get(f"command_path.{name}", payload.get(f"command_path_{name}", defaults["command_paths"][name])),
            ),
        )
        commands[name] = coerce_remote_bool(raw_command)
        command_paths[name] = coerce_remote_optional_text(raw_path)
    for pipeline in ("fmriprep", "xcpd"):
        persisted_images[pipeline] = coerce_remote_bool(
            persisted_images_payload.get(
                pipeline,
                payload.get(f"persisted_images.{pipeline}", defaults["persisted_images"][pipeline]),
            )
        )
        persisted_image_valid[pipeline] = coerce_remote_bool(
            persisted_image_valid_payload.get(
                pipeline,
                payload.get(
                    f"persisted_image_valid.{pipeline}",
                    defaults["persisted_image_valid"][pipeline],
                ),
            )
        )
        requested_local_images[pipeline] = coerce_remote_bool(
            requested_local_images_payload.get(
                pipeline,
                payload.get(
                    f"requested_local_images.{pipeline}",
                    defaults["requested_local_images"][pipeline],
                ),
            )
        )
        requested_local_image_valid[pipeline] = coerce_remote_bool(
            requested_local_image_valid_payload.get(
                pipeline,
                payload.get(
                    f"requested_local_image_valid.{pipeline}",
                    defaults["requested_local_image_valid"][pipeline],
                ),
            )
        )
    for name in (
        "output_root",
        "work_root",
        "log_root",
    ):
        writable_paths[name] = coerce_remote_bool(
            writable_paths_payload.get(
                name,
                payload.get(f"writable_paths.{name}", defaults["writable_paths"][name]),
            )
        )
    return {
        "probe_ok": True,
        "hostname": coerce_remote_optional_text(payload.get("hostname")) or remote_host,
        "commands": commands,
        "command_paths": command_paths,
        "docker_daemon_available": coerce_remote_bool(
            payload.get("docker_daemon_available", defaults["docker_daemon_available"])
        ),
        "docker_daemon_error": coerce_remote_optional_text(
            payload.get("docker_daemon_error", defaults["docker_daemon_error"])
        ),
        "cpu_total": coerce_remote_int(payload.get("cpu_total"), default=1),
        "memory_gb": coerce_remote_optional_int(payload.get("memory_gb")),
        "home": coerce_remote_optional_text(payload.get("home")),
        "slurm_job_id": coerce_remote_optional_text(payload.get("slurm_job_id")),
        "current_host_is_slurm_node": coerce_remote_bool(
            payload.get("current_host_is_slurm_node", defaults["current_host_is_slurm_node"])
        ),
        "templateflow_home": coerce_remote_optional_text(payload.get("templateflow_home")),
        "templateflow_probe_root": coerce_remote_optional_text(payload.get("templateflow_probe_root")),
        "templateflow_annex_status": coerce_remote_optional_text(payload.get("templateflow_annex_status")) or "skipped",
        "templateflow_annex_missing_paths": coerce_remote_optional_text(payload.get("templateflow_annex_missing_paths")) or "",
        "templateflow_annex_error": coerce_remote_optional_text(payload.get("templateflow_annex_error")) or "",
        "shared_paths": shared_paths,
        "persisted_images": persisted_images,
        "persisted_image_valid": persisted_image_valid,
        "requested_local_images": requested_local_images,
        "requested_local_image_valid": requested_local_image_valid,
        "writable_paths": writable_paths,
    }


def coerce_remote_bool(value: Any) -> bool:
    """Interpret a remote probe scalar as boolean."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def coerce_remote_int(value: Any, default: int) -> int:
    """Interpret a remote probe scalar as integer, with a floor of 1."""
    if isinstance(value, int):
        return max(1, value)
    try:
        return max(1, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


def coerce_remote_optional_int(value: Any) -> int | None:
    """Interpret a remote probe scalar as optional integer."""
    if value in {None, ""}:
        return None
    try:
        return max(1, int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _coerce_probe_returncode(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def coerce_remote_optional_text(value: Any) -> str | None:
    """Interpret a remote probe scalar as stripped text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def remote_probe_command_path(remote_probe: dict[str, Any], command: str | None) -> str | None:
    """Resolve one command path from the batched remote runtime probe."""
    if not command:
        return None
    command_paths = remote_probe.get("command_paths") or {}
    return coerce_remote_optional_text(command_paths.get(command))


def remote_probe_succeeded(remote_probe: dict[str, Any] | None) -> bool:
    """Return whether the batched remote probe completed successfully."""
    return bool(remote_probe and remote_probe.get("probe_ok"))


def remote_probe_persisted_image_exists(remote_probe: dict[str, Any] | None, pipeline: str) -> bool:
    return bool((remote_probe or {}).get("persisted_images", {}).get(pipeline))


def remote_probe_persisted_image_valid(remote_probe: dict[str, Any] | None, pipeline: str) -> bool:
    probe = remote_probe or {}
    return remote_probe_succeeded(remote_probe) and bool(probe.get("persisted_images", {}).get(pipeline)) and bool(
        probe.get("persisted_image_valid", {}).get(pipeline)
    )


def remote_probe_requested_local_image_exists(remote_probe: dict[str, Any] | None, pipeline: str) -> bool:
    return bool((remote_probe or {}).get("requested_local_images", {}).get(pipeline))


def remote_probe_requested_local_image_valid(remote_probe: dict[str, Any] | None, pipeline: str) -> bool:
    return bool((remote_probe or {}).get("requested_local_image_valid", {}).get(pipeline))
