"""Define the shared data models used by the fmri workflow tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, Callable, Literal, TypeAlias

TargetName: TypeAlias = Literal["fmriprep", "xcpd"]
ExecutorPolicyName: TypeAlias = Literal["auto", "local", "slurm"]
ResolvedExecutorPolicyName: TypeAlias = Literal["local", "slurm"]
ContainerRuntimeName: TypeAlias = Literal["auto", "apptainer", "singularity", "docker"]
ResolvedContainerRuntimeName: TypeAlias = Literal["apptainer", "singularity", "docker"]
PipelineStepName: TypeAlias = Literal["fmriprep", "xcpd"]
ExecutionStrategyName: TypeAlias = Literal["slurm", "worker_pool"]
LocalPlatformName: TypeAlias = Literal["remote", "windows", "wsl2", "linux"]
EnvironmentKindName: TypeAlias = Literal["hpc_cluster", "linux_server", "workstation"]
XCPDModeName: TypeAlias = Literal["abcd", "nichart"]
XCPDMotionFilterName: TypeAlias = Literal["lp", "notch", "none"]
XCPDYesNoName: TypeAlias = Literal["y", "n"]
ProgressCallback: TypeAlias = Callable[[dict[str, Any]], None]

VALID_TARGETS: tuple[TargetName, ...] = ("fmriprep", "xcpd")
VALID_BACKENDS: tuple[ExecutorPolicyName, ...] = ("auto", "local", "slurm")
VALID_RUNTIMES: tuple[ContainerRuntimeName, ...] = ("auto", "apptainer", "singularity", "docker")
VALID_XCPD_MODES: tuple[XCPDModeName, ...] = ("abcd", "nichart")
VALID_XCPD_MOTION_FILTER_TYPES: tuple[XCPDMotionFilterName, ...] = ("lp", "notch", "none")
VALID_XCPD_YN_VALUES: tuple[XCPDYesNoName, ...] = ("y", "n")
DEFAULT_FMRIPREP_OUTPUT_SPACES = ("MNI152NLin2009cAsym:res-2", "MNI152NLin6Asym:res-2")
DEFAULT_FMRIPREP_NO_RECONALL_OUTPUT_SPACES = ("MNI152NLin2009cAsym:res-2",)
DEFAULT_FMRIPREP_CIFTI_OUTPUT = "91k"
DEFAULT_FMRIPREP_IMAGE = "docker://nipreps/fmriprep:25.2.5"
DEFAULT_XCPD_IMAGE = "docker://pennlinc/xcp_d:26.0.2"
RequestPath: TypeAlias = Path | PurePosixPath
REMOTE_FILESYSTEM_FIELDS: tuple[str, ...] = (
    "bids_root",
    "fmriprep_derivatives",
    "output_root",
    "work_root",
    "log_root",
    "download_root",
    "fs_license",
    "templateflow_home",
    "xcpd_bids_filter_file",
)

@dataclass(frozen=True)
class SubjectEntry:
    """Represent one subject and optional session selection."""
    subject_id: str
    session_id: str | None = None
    site: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def subject_label(self) -> str:
        """Return the subject label.

        Inputs:
            None.

        Returns:
            str: Normalized string value.
        """
        return f"sub-{self.subject_id}"

    @property
    def session_label(self) -> str | None:
        """Return the session label.

        Inputs:
            None.

        Returns:
            str | None: Resolved string value, or ``None`` when unavailable.
        """
        if self.session_id is None:
            return None
        return f"ses-{self.session_id}"

    @property
    def key(self) -> str:
        """Return the normalized subject or subject/session key.

        Inputs:
            None.

        Returns:
            str: Normalized string value.
        """
        if self.session_label:
            return f"{self.subject_label}_{self.session_label}"
        return self.subject_label

    def to_dict(self) -> dict[str, Any]:
        """Return the subject entry as a dictionary.

        Inputs:
            None.

        Returns:
            dict[str, Any]: Summary payload returned by the helper.
        """
        return asdict(self)

@dataclass(frozen=True)
class BindMount:
    """Describe one host-to-container bind mount."""
    source: str
    target: str
    read_only: bool = False


@dataclass(frozen=True)
class ContainerSpec:
    """Describe one container runtime invocation."""
    engine: str
    image: str
    executable: str | None = None
    cleanenv: bool = True
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResources:
    """Store normalized runtime resource limits."""
    cpu_total: int
    slurm_mem_gb: int | None
    nthreads_per_job: int
    omp_nthreads: int
    max_jobs: int

    def to_dict(self) -> dict[str, Any]:
        """Return the runtime resources as a dictionary.

        Inputs:
            None.

        Returns:
            dict[str, Any]: Summary payload returned by the helper.
        """
        return asdict(self)

@dataclass(frozen=True)
class RequestConfig:
    """Store normalized workflow request inputs."""
    action: str
    bids_root: RequestPath | None = None
    fmriprep_derivatives: RequestPath | None = None
    output_root: RequestPath | None = None
    target: TargetName = "fmriprep"
    remote_host: str | None = None
    subjects: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    work_root: RequestPath | None = None
    log_root: RequestPath | None = None
    download_root: RequestPath | None = None
    fs_license: RequestPath | None = None
    templateflow_home: RequestPath | None = None
    templateflow_tool_bins: list[str] = field(default_factory=list)
    fmriprep_image: str | None = None
    xcpd_image: str | None = None
    container_runtime: ContainerRuntimeName = "auto"
    executor_policy: ExecutorPolicyName = "auto"
    scheduler_partition: str | None = None
    nthreads_per_job: int | None = None
    omp_nthreads: int | None = None
    slurm_mem_gb: int | None = None
    max_jobs: int | None = None
    fs_no_reconall: bool = False
    skip_bids_validation: bool = False
    task_id: str | None = None
    echo_idx: int | None = None
    anat_only: bool = False
    fmriprep_custom_args: dict[str, Any] = field(default_factory=dict)
    output_spaces: list[str] = field(default_factory=lambda: list(DEFAULT_FMRIPREP_OUTPUT_SPACES))
    cifti_output: str | None = None
    xcpd_mode: XCPDModeName = "abcd"
    xcpd_min_time: int | None = None
    xcpd_min_time_explicit: bool = False
    xcpd_motion_filter_type: XCPDMotionFilterName | None = None
    xcpd_band_stop_min: float | None = None
    xcpd_band_stop_max: float | None = None
    xcpd_motion_filter_order: int | None = None
    xcpd_despike: XCPDYesNoName | None = None
    xcpd_task_ids: list[str] = field(default_factory=list)
    xcpd_bids_filter_file: RequestPath | None = None
    xcpd_datasets: dict[str, RequestPath] = field(default_factory=dict)
    xcpd_mem_mb: int | None = None
    xcpd_custom_args: dict[str, Any] = field(default_factory=dict)
    wsl_vhdx_path: RequestPath | None = None
    windows_host_drive: str | None = None
    docker_wsl_storage_path: RequestPath | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        if self.xcpd_min_time is None:
            default_min_time = 0 if self.xcpd_mode == "nichart" else 240
            object.__setattr__(self, "xcpd_min_time", default_min_time)
        if self.xcpd_mode == "abcd" and self.xcpd_motion_filter_type is None:
            object.__setattr__(self, "xcpd_motion_filter_type", "lp")
        if self.xcpd_mode == "abcd" and self.xcpd_motion_filter_type != "none":
            if self.xcpd_band_stop_min is None:
                object.__setattr__(self, "xcpd_band_stop_min", 12)
            if self.xcpd_motion_filter_order is None:
                object.__setattr__(self, "xcpd_motion_filter_order", 4)
            if self.xcpd_despike is None:
                object.__setattr__(self, "xcpd_despike", "y")
        if self.xcpd_motion_filter_type == "lp" and self.xcpd_band_stop_min is None:
            raise ValueError("xcpd_band_stop_min is required when xcpd_motion_filter_type is lp")
        if self.xcpd_motion_filter_type == "notch" and (
            self.xcpd_band_stop_min is None or self.xcpd_band_stop_max is None
        ):
            raise ValueError("xcpd_band_stop_min and xcpd_band_stop_max are required when xcpd_motion_filter_type is notch")
        if self.xcpd_motion_filter_type == "none":
            object.__setattr__(self, "xcpd_band_stop_min", None)
            object.__setattr__(self, "xcpd_band_stop_max", None)
            object.__setattr__(self, "xcpd_motion_filter_order", None)
        if self.xcpd_mem_mb is not None and self.xcpd_mem_mb <= 0:
            raise ValueError("xcpd_mem_mb must be positive")
        if self.fs_no_reconall:
            if self.cifti_output is not None:
                raise ValueError("fs_no_reconall cannot be combined with surface or CIFTI outputs")
            if self.output_spaces == list(DEFAULT_FMRIPREP_OUTPUT_SPACES):
                object.__setattr__(self, "output_spaces", list(DEFAULT_FMRIPREP_NO_RECONALL_OUTPUT_SPACES))
            elif any(_is_surface_output_space(value) for value in self.output_spaces):
                raise ValueError("fs_no_reconall cannot be combined with surface or CIFTI outputs")

    def resolve_bids_root(self) -> RequestPath:
        """Resolve bids root.

        Inputs:
            None.

        Returns:
            Path: Resolved path value.
        """
        if self.bids_root is None:
            raise ValueError("bids_root is required")
        return self.bids_root

    def resolve_output_root(self) -> RequestPath:
        """Resolve output root.

        Inputs:
            None.

        Returns:
            Path: Resolved path value.
        """
        if self.output_root is not None:
            return self.output_root
        if self.fmriprep_derivatives is not None:
            return self.fmriprep_derivatives.parent
        if self.bids_root is not None:
            return self.bids_root / "derivatives"
        raise ValueError("output_root is required")

    def resolve_fmriprep_derivatives_root(self) -> RequestPath:
        """Resolve the fMRIPrep derivatives root used as XCP-D input."""
        if self.fmriprep_derivatives is not None:
            return self.fmriprep_derivatives
        return self.resolve_output_root() / "fmriprep"

    def resolve_work_root(self) -> RequestPath:
        """Resolve work root.

        Inputs:
            None.

        Returns:
            Path: Resolved path value.
        """
        if self.work_root is not None:
            return self.work_root
        if self.bids_root is not None:
            return self.bids_root.parent / "work" / self._default_work_root_name()
        if self.output_root is not None or self.fmriprep_derivatives is not None:
            return self.resolve_output_root().parent / "work" / self._default_work_root_name()
        raise ValueError("bids_root is required")

    def resolve_pipeline_output_root(self, pipeline: PipelineStepName | None = None) -> RequestPath:
        """Resolve the concrete derivatives directory for one pipeline.

        Inputs:
            pipeline (PipelineStepName | None): Pipeline name. When omitted, use
                the primary pipeline implied by the current target.

        Returns:
            Path: Resolved path value.
        """
        if pipeline is None:
            pipeline = "xcpd" if self.target == "xcpd" else "fmriprep"
        if pipeline == "fmriprep":
            return self.resolve_fmriprep_derivatives_root()
        return self.resolve_output_root() / "xcp_d"

    def resolve_log_root(self) -> RequestPath:
        """Resolve log root.

        Inputs:
            None.

        Returns:
            Path: Resolved path value.
        """
        if self.log_root is not None:
            return self.log_root
        log_dir = "xcpd_logs" if self.target == "xcpd" else "fmriprep_logs"
        return self.resolve_output_root() / "_artifacts" / log_dir

    def resolve_image_root(self) -> RequestPath:
        """Resolve image root.

        Inputs:
            None.

        Returns:
            Path: Resolved path value.
        """
        return self.resolve_download_root() / "images"

    def resolve_download_root(self) -> RequestPath:
        """Resolve shared download/cache root."""
        if self.download_root is not None:
            return self.download_root
        output_root = self.resolve_output_root()
        if self.bids_root is not None and output_root == self.bids_root / "derivatives":
            return self.bids_root.parent / "_downloads"
        return output_root.parent / "_downloads"

    def to_dict(self) -> dict[str, Any]:
        """Return the request as a JSON-safe dictionary.

        Inputs:
            None.

        Returns:
            dict[str, Any]: Summary payload returned by the helper.
        """
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, PurePath):
                payload[key] = str(value)
            elif isinstance(value, list):
                payload[key] = [str(item) if isinstance(item, PurePath) else item for item in value]
            elif isinstance(value, dict):
                payload[key] = {
                    item_key: str(item_value) if isinstance(item_value, PurePath) else item_value
                    for item_key, item_value in value.items()
                }
        return payload

    def output_inside_bids_root(self) -> bool:
        """Return whether the output root falls inside the BIDS tree.

        Inputs:
            None.

        Returns:
            bool: Whether the condition is satisfied.
        """
        if self.bids_root is None:
            return False
        return _is_relative_to(self.resolve_output_root(), self.bids_root)

    def _default_work_root_name(self) -> str:
        if self.target == "xcpd":
            return "work_xcpd"
        return "work_fmriprep"


def validate_remote_request_paths(request: RequestConfig) -> None:
    if not request.remote_host:
        return
    for field in REMOTE_FILESYSTEM_FIELDS:
        value = getattr(request, field)
        if value is None:
            continue
        if not isinstance(value, PurePosixPath):
            raise ValueError(
                f"{field} must be a remote POSIX path when --remote-host is set, got: {value}"
            )
    for alias, value in request.xcpd_datasets.items():
        if not isinstance(value, PurePosixPath):
            raise ValueError(
                f"xcpd_datasets.{alias} must be a remote POSIX path when --remote-host is set, got: {value}"
            )


def _is_surface_output_space(value: str) -> bool:
    return value == "fsnative" or value.startswith("fsLR") or value.startswith("fsaverage")


def _is_relative_to(path: RequestPath, root: RequestPath) -> bool:
    """Return whether ``path`` is relative to ``root``.

    Inputs:
        path (Path): Filesystem path being inspected or normalized.
        root (Path): Root path used for containment or normalization checks.

    Returns:
        bool: Whether the condition is satisfied.
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

def subject_key(subject_id: str, session_id: str | None = None) -> str:
    """Return the stable subject or subject/session key.

    Inputs:
        subject_id (str): Subject identifier.
        session_id (str | None): Session identifier.

    Returns:
        str: Normalized string value.
    """
    if session_id:
        return f"sub-{subject_id}_ses-{session_id}"
    return f"sub-{subject_id}"
