"""TemplateFlow readiness probes for runtime audits."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .models import RequestConfig, RequestPath
from .shell import argv_command, run_command, shell_command


TEMPLATEFLOW_REMOTE_READINESS_TIMEOUT_SECONDS = 180
TEMPLATEFLOW_CONTAINER_HOME = "/templateflow"
TEMPLATEFLOW_CONTAINER_READINESS_TIMEOUT_SECONDS = 300
TEMPLATEFLOW_ARCHIVE_URL = "https://www.templateflow.org/usage/archive/"
TEMPLATEFLOW_PREPARE_BACKENDS = ("datalad", "git-annex", "python-client")
TEMPLATEFLOW_CACHE_PREPARE_CODE = "prepare_runtime_required_templateflow_cache"
TEMPLATEFLOW_CONTAINER_IMPORT_PREPARE_CODE = "prepare_runtime_required_templateflow_container_import"
TEMPLATEFLOW_UNVERIFIED_WARNING_CODE = "templateflow_unverified"
REQUIRED_TEMPLATEFLOW_TEMPLATES = (
    "OASIS30ANTs",
    "MNI152NLin2009cAsym",
    "fsLR",
)
XCPD_MODE_TEMPLATEFLOW_TEMPLATES = {
    "abcd": ("MNI152NLin6Asym", "fsLR"),
    "nichart": ("MNI152NLin2009cAsym",),
}
_IGNORED_TEMPLATEFLOW_OUTPUT_SPACES = {
    "anat",
    "t1w",
    "func",
    "fsnative",
    "fsaverage",
    "fsaverage5",
    "fsaverage6",
}


def _dedupe(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered

def _coerce_remote_optional_text(value: Any) -> str | None:
    """Interpret a remote probe scalar as stripped text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _remote_probe_succeeded(remote_probe: dict[str, Any] | None) -> bool:
    """Return whether the batched remote probe completed successfully."""
    return bool(remote_probe and remote_probe.get("probe_ok"))


def _docker_registry_image(value: str) -> str:
    return value.removeprefix("docker://")


def required_templateflow_templates(request: RequestConfig) -> list[str]:
    """Return TemplateFlow templates required by the effective fMRIPrep request."""
    if request.target == "xcpd":
        return list(XCPD_MODE_TEMPLATEFLOW_TEMPLATES[request.xcpd_mode])
    templates: list[str] = ["OASIS30ANTs"]
    for output_space in request.output_spaces:
        token = str(output_space).strip()
        if not token:
            continue
        candidate = token.split(":", 1)[0].strip()
        if not candidate or candidate.lower() in _IGNORED_TEMPLATEFLOW_OUTPUT_SPACES:
            continue
        templates.append(candidate)
    if request.cifti_output == "91k":
        templates.extend(["MNI152NLin6Asym", "fsLR"])
    return _dedupe(templates)


def _templateflow_tool_path_prefix(tool_bins: list[str] | tuple[str, ...]) -> str:
    return ":".join(str(value).strip() for value in tool_bins if str(value).strip())


def _templateflow_toolchain(
    request: RequestConfig,
    *,
    required_templates: list[str] | tuple[str, ...],
    templateflow_home: str | None,
) -> dict[str, Any]:
    tool_bins = [str(value).strip() for value in request.templateflow_tool_bins if str(value).strip()]
    if not required_templates or not templateflow_home:
        return {
            "source": "skipped",
            "tool_bins": tool_bins,
            "path_prefix": None,
            "manual_command_prefix": None,
        }
    prefix = _templateflow_tool_path_prefix(tool_bins)
    if prefix:
        return {
            "source": "explicit_tool_bin",
            "tool_bins": tool_bins,
            "path_prefix": f"{prefix}:$PATH",
            "manual_command_prefix": f"PATH={prefix}:$PATH",
        }
    return {
        "source": "missing_tool_bin",
        "tool_bins": [],
        "path_prefix": None,
        "manual_command_prefix": None,
    }


def _templateflow_effective_local_path(request: RequestConfig) -> str | None:
    prefix = _templateflow_tool_path_prefix(request.templateflow_tool_bins)
    if not prefix:
        return None
    current_path = os.environ.get("PATH", "")
    return f"{prefix}{os.pathsep}{current_path}" if current_path else prefix


def _templateflow_tool_env(request: RequestConfig) -> dict[str, str] | None:
    prefix = _templateflow_tool_path_prefix(request.templateflow_tool_bins)
    if not prefix:
        return {"PATH": ""}
    if request.remote_host:
        return {"PATH": f"{prefix}:$PATH"}
    env = dict(os.environ)
    current_path = env.get("PATH", "")
    env["PATH"] = f"{prefix}{os.pathsep}{current_path}" if current_path else prefix
    return env


def remote_templateflow_tool_path_line(request: RequestConfig) -> str:
    prefix = _templateflow_tool_path_prefix(request.templateflow_tool_bins)
    if not prefix:
        return "PATH=; export PATH"
    return f"PATH={shlex.quote(prefix)}:$PATH; export PATH"


def templateflow_container_import_ready(
    request: RequestConfig,
    selected_runtime: str | None,
    image: str | RequestPath,
    templateflow_home: str | RequestPath,
    required_templates: list[str] | tuple[str, ...],
) -> bool:
    """Return whether the target container imports TemplateFlow read-only."""
    probe_command = _templateflow_container_probe_command(request.target)
    if selected_runtime in {"apptainer", "singularity"}:
        command = _templateflow_container_sif_command(
            selected_runtime,
            image,
            templateflow_home,
            required_templates,
            probe_command,
        )
        intent = shell_command(command)
    elif selected_runtime == "docker":
        image_text = str(image)
        registry_image = _docker_registry_image(image_text) if image_text.startswith("docker://") else image_text
        intent = argv_command(
            _templateflow_container_docker_parts(
                registry_image,
                templateflow_home,
                required_templates,
                probe_command,
            )
        )
    else:
        return False
    try:
        result = run_command(
            intent,
            remote_host=request.remote_host,
            check=False,
            timeout=TEMPLATEFLOW_CONTAINER_READINESS_TIMEOUT_SECONDS,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _templateflow_container_sif_command(
    selected_runtime: str,
    image: str | RequestPath,
    templateflow_home: str | RequestPath,
    required_templates: list[str] | tuple[str, ...],
    probe_command: list[str],
) -> str:
    parts = [
        selected_runtime,
        "exec",
        "--cleanenv",
        "--env",
        f"TEMPLATEFLOW_HOME={TEMPLATEFLOW_CONTAINER_HOME}",
        "--env",
        "TEMPLATEFLOW_AUTOUPDATE=false",
        "-B",
        f"{templateflow_home}:{TEMPLATEFLOW_CONTAINER_HOME}:ro",
        str(image),
        "python",
        "-c",
        _templateflow_container_import_code(required_templates, probe_command),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _templateflow_container_docker_parts(
    image: str,
    templateflow_home: str | RequestPath,
    required_templates: list[str] | tuple[str, ...],
    probe_command: list[str],
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "-e",
        f"TEMPLATEFLOW_HOME={TEMPLATEFLOW_CONTAINER_HOME}",
        "-e",
        "TEMPLATEFLOW_AUTOUPDATE=false",
        "--mount",
        f"type=bind,source={templateflow_home},target={TEMPLATEFLOW_CONTAINER_HOME},readonly",
        "--entrypoint",
        "python",
        image,
        "-c",
        _templateflow_container_import_code(required_templates, probe_command),
    ]


def _templateflow_container_probe_command(target: str) -> list[str]:
    if target == "xcpd":
        return ["xcp_d", "--version"]
    return ["fmriprep", "--version"]


def _templateflow_container_import_code(
    required_templates: list[str] | tuple[str, ...],
    probe_command: list[str],
) -> str:
    return "\n".join(
        [
            "import os",
            "import subprocess",
            "from pathlib import Path",
            "templateflow_home = os.environ.get('TEMPLATEFLOW_HOME')",
            "if not templateflow_home:",
            "    raise RuntimeError('Missing TEMPLATEFLOW_HOME')",
            "if os.environ.get('TEMPLATEFLOW_AUTOUPDATE') != 'false':",
            "    raise RuntimeError('TEMPLATEFLOW_AUTOUPDATE must be false')",
            "templateflow_root = Path(templateflow_home)",
            "if not templateflow_root.is_dir():",
            "    raise RuntimeError(f'Missing TEMPLATEFLOW_HOME directory: {templateflow_root}')",
            "import templateflow",
            f"required_templates = {list(required_templates)!r}",
            "for template in required_templates:",
            "    template_dir = templateflow_root / f'tpl-{template}'",
            "    if not template_dir.is_dir():",
            "        raise RuntimeError(f'Missing TemplateFlow template directory: {template_dir}')",
            f"version = subprocess.run({probe_command!r}, capture_output=True, text=True, check=False)",
            "print((version.stdout or version.stderr).strip(), flush=True)",
            "raise SystemExit(version.returncode)",
        ]
    )


def templateflow_remote_annex_probe_functions() -> str:
    return """
check_templateflow_annex() {
  templateflow_probe_root="$1"
  shift
  printf 'templateflow_annex_status=skipped\\n'
  printf 'templateflow_annex_missing_paths=\\n'
  printf 'templateflow_annex_error=\\n'
  [ -n "$templateflow_probe_root" ] || return 0
  [ -e "$templateflow_probe_root/.git" ] || [ -e "$templateflow_probe_root/.datalad" ] || return 0
  if ! command -v git >/dev/null 2>&1; then
    printf 'templateflow_annex_status=failed\\n'
    printf 'templateflow_annex_error=git unavailable\\n'
    return 0
  fi
  for template_arg in "$@"; do
    template_dir="$templateflow_probe_root/$template_arg"
    if [ ! -d "$template_dir" ]; then
      printf 'templateflow_annex_status=missing_content\\n'
      printf 'templateflow_annex_missing_paths=%s\\n' "$template_dir"
      return 0
    fi
  done
  timeout_bin="$(command -v timeout 2>/dev/null || true)"
  if [ -n "$timeout_bin" ]; then
    annex_output="$("$timeout_bin" 60 git -C "$templateflow_probe_root" annex find --not --in=here -- "$@" 2>&1)"
  else
    annex_output="$(git -C "$templateflow_probe_root" annex find --not --in=here -- "$@" 2>&1)"
  fi
  annex_status=$?
  annex_output="$(printf '%s\\n' "$annex_output" | grep -v -e 'setlocale' -e 'cannot change locale' -e "can't set the locale" -e '^manpath:' || true)"
  if [ "$annex_status" -ne 0 ]; then
    printf 'templateflow_annex_status=failed\\n'
    printf 'templateflow_annex_error=%s\\n' "$(printf '%s' "$annex_output" | tr '\\n' ' ' | cut -c 1-500)"
    return 0
  fi
  if [ -n "$annex_output" ]; then
    printf 'templateflow_annex_status=missing_content\\n'
    printf 'templateflow_annex_missing_paths=%s\\n' "$(printf '%s' "$annex_output" | tr '\\n' '|')"
    return 0
  fi
  printf 'templateflow_annex_status=clean\\n'
}
""".strip()


def resolve_templateflow_home(
    request: RequestConfig,
    *,
    required_templates: list[str],
) -> str | None:
    if request.templateflow_home is not None:
        return str(request.templateflow_home)
    if required_templates:
        return str(request.resolve_download_root() / "templateflow")
    return None


def templateflow_diagnostics(
    request: RequestConfig,
    *,
    templateflow_home: str | None,
    remote_probe: dict[str, Any] | None,
    required_templates: list[str],
) -> dict[str, Any]:
    toolchain = _templateflow_toolchain(
        request,
        required_templates=required_templates,
        templateflow_home=templateflow_home,
    )
    diagnostics: dict[str, Any] = {
        "status": "ready" if not required_templates else "failed",
        "probe_mode": "not_required" if not required_templates else "tool",
        "proof_mode": "not_required" if not required_templates else "tool",
        "home": templateflow_home,
        "required_templates": list(required_templates),
        "failed_template": None,
        "failed_path": None,
        "failure_reason": None if not required_templates else "missing_dir",
        "annex_status": "skipped",
        "annex_missing_paths": [],
        "toolchain": toolchain,
        "file_probe_status": "not_required" if not required_templates else "unknown",
        "first_existing_required_file": None,
        "fmriprep_readability": None,
        "annex_probe": {"status": "skipped"},
        "cache_status": "ready" if not required_templates else "unknown",
    }
    if not required_templates:
        return diagnostics
    if not templateflow_home:
        diagnostics["file_probe_status"] = "missing"
        diagnostics["fmriprep_readability"] = False
        diagnostics["cache_status"] = "missing"
        return diagnostics
    if request.remote_host:
        if _remote_probe_succeeded(remote_probe):
            diagnostics["annex_probe"] = _remote_templateflow_annex_probe(remote_probe)
            diagnostics["annex_status"] = str(diagnostics["annex_probe"].get("status") or "skipped")
            diagnostics["annex_missing_paths"] = list(diagnostics["annex_probe"].get("missing_paths") or [])
            annex_ready = _templateflow_annex_ready_status(diagnostics["annex_probe"])
            if annex_ready is not None:
                diagnostics["file_probe_status"] = "ready" if annex_ready else "missing"
                diagnostics["fmriprep_readability"] = annex_ready
                diagnostics["cache_status"] = "ready" if annex_ready else "missing"
                diagnostics["status"] = "ready" if annex_ready else "failed"
                diagnostics["probe_mode"] = "tool"
                diagnostics["proof_mode"] = "tool"
                if not annex_ready:
                    diagnostics["failure_reason"] = "annex_missing_content"
                    failed_path = diagnostics["annex_missing_paths"][0] if diagnostics["annex_missing_paths"] else None
                    diagnostics["failed_path"] = failed_path
                    diagnostics["failed_template"] = _templateflow_template_name_from_path(failed_path)
                else:
                    diagnostics["failure_reason"] = None
                return diagnostics
            if not _remote_templateflow_path_exists(remote_probe):
                diagnostics["file_probe_status"] = "missing"
                diagnostics["fmriprep_readability"] = False
                diagnostics["cache_status"] = "missing"
                diagnostics["status"] = "failed"
                diagnostics["failure_reason"] = "missing_dir"
                diagnostics["failed_path"] = templateflow_home
                return diagnostics
            diagnostics["file_probe_status"] = "deferred"
            diagnostics["fmriprep_readability"] = None
            diagnostics["cache_status"] = "unverified"
            diagnostics["status"] = "deferred"
            diagnostics["probe_mode"] = "tool"
            diagnostics["proof_mode"] = "tool"
            diagnostics["failure_reason"] = "tool_proof_deferred"
            return diagnostics
        return diagnostics
    templateflow_path = Path(templateflow_home)
    if not templateflow_path.exists():
        diagnostics["file_probe_status"] = "missing"
        diagnostics["fmriprep_readability"] = False
        diagnostics["cache_status"] = "missing"
        diagnostics["status"] = "failed"
        diagnostics["failure_reason"] = "missing_dir"
        diagnostics["failed_path"] = str(templateflow_path)
        return diagnostics
    diagnostics["annex_probe"] = _local_templateflow_annex_probe(
        Path(templateflow_home),
        required_templates,
        request=request,
    )
    diagnostics["annex_status"] = str(diagnostics["annex_probe"].get("status") or "skipped")
    diagnostics["annex_missing_paths"] = list(diagnostics["annex_probe"].get("missing_paths") or [])
    annex_ready = _templateflow_annex_ready_status(diagnostics["annex_probe"])
    if annex_ready is not None:
        diagnostics["file_probe_status"] = "ready" if annex_ready else "missing"
        diagnostics["fmriprep_readability"] = annex_ready
        diagnostics["cache_status"] = "ready" if annex_ready else "missing"
        diagnostics["status"] = "ready" if annex_ready else "failed"
        diagnostics["probe_mode"] = "tool"
        diagnostics["proof_mode"] = "tool"
        if not annex_ready:
            diagnostics["failure_reason"] = "annex_missing_content"
            failed_path = diagnostics["annex_missing_paths"][0] if diagnostics["annex_missing_paths"] else None
            diagnostics["failed_path"] = failed_path
            diagnostics["failed_template"] = _templateflow_template_name_from_path(failed_path)
        else:
            diagnostics["failure_reason"] = None
        return diagnostics
    diagnostics["file_probe_status"] = "deferred"
    diagnostics["fmriprep_readability"] = None
    diagnostics["cache_status"] = "unverified"
    diagnostics["status"] = "deferred"
    diagnostics["probe_mode"] = "tool"
    diagnostics["proof_mode"] = "tool"
    diagnostics["failure_reason"] = "tool_proof_deferred"
    return diagnostics

def _local_templateflow_annex_probe(
    templateflow_home: Path,
    required_templates: list[str],
    *,
    request: RequestConfig | None = None,
) -> dict[str, Any]:
    if not _looks_like_datalad_dataset(templateflow_home):
        return {"status": "skipped"}
    if request is None or not request.templateflow_tool_bins:
        return {"status": "failed", "error": "templateflow tool bin missing"}
    for template in required_templates:
        template_dir = templateflow_home / f"tpl-{template}"
        if not template_dir.is_dir():
            return {"status": "missing_content", "missing_paths": [str(template_dir)]}
    parts = [
        "git",
        "-C",
        str(templateflow_home),
        "annex",
        "find",
        "--not",
        "--in=here",
        "--",
        *[f"tpl-{template}" for template in required_templates],
    ]
    try:
        result = run_command(
            argv_command(parts),
            check=False,
            timeout=60,
            env=_templateflow_tool_env(request) if request is not None else None,
        )
    except FileNotFoundError:
        return {"status": "failed", "error": "git unavailable"}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "failed", "error": ((result.stderr or result.stdout or "").strip()[:500])}
    missing_paths = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if missing_paths:
        return {"status": "missing_content", "missing_paths": missing_paths}
    return {"status": "clean"}


def _looks_like_datalad_dataset(path: Path) -> bool:
    return (path / ".git").exists() or (path / ".datalad").exists()

def _remote_templateflow_annex_probe(remote_probe: dict[str, Any] | None) -> dict[str, Any]:
    status = _coerce_remote_optional_text((remote_probe or {}).get("templateflow_annex_status")) or "skipped"
    probe: dict[str, Any] = {"status": status}
    missing = _coerce_remote_optional_text((remote_probe or {}).get("templateflow_annex_missing_paths"))
    if missing:
        probe["missing_paths"] = [value for value in missing.split("|") if value]
    error = _coerce_remote_optional_text((remote_probe or {}).get("templateflow_annex_error"))
    if error:
        probe["error"] = error
    return probe


def _templateflow_annex_ready_status(probe: dict[str, Any]) -> bool | None:
    status = probe.get("status")
    if status == "clean":
        return True
    if status == "missing_content":
        return False
    return None


def _templateflow_template_name_from_path(path: str | None) -> str | None:
    if not path:
        return None
    for part in PurePosixPath(path).parts:
        if part.startswith("tpl-"):
            return part.removeprefix("tpl-")
    return None


def _remote_templateflow_path_exists(remote_probe: dict[str, Any] | None) -> bool:
    shared_paths = (remote_probe or {}).get("shared_paths") or {}
    return bool(isinstance(shared_paths, dict) and shared_paths.get("templateflow"))
