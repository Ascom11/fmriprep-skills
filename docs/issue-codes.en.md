# issue code index

This page lists every issue code in the current `fmri_core/resources/issue_catalog.json`. English READMEs should link here. During troubleshooting, trust the code, path, and advice returned by the agent audit report.

## How to read categories

| Category | Name | Meaning |
| --- | --- | --- |
| `blocker` | Blocker | Execution is not safe yet. Fix the path, runtime, permission, license, image, or input scope first. |
| `prepare-required` | Prepare required | The agent can prepare the missing image or TemplateFlow asset after user approval, then rerun the audit. |
| `warning` | Warning | Usually does not stop execution, but raises the chance of failure, storage pressure, stale reuse, or harder debugging. |
| `subject-exclusion` | Subject exclusion | One subject or session cannot run. Other runnable entries may continue unless none remain. |
| `request` | Request problem | A user-provided XCP-D parameter, filter file, or extra dataset is invalid and must be corrected. |
| `artifact-replay` | Saved artifact replay | A saved audit or execution artifact is missing, corrupt, mismatched, or not ready. |
| `advice` | Advice | Informational guidance. It does not block execution. |

## Common first-run risks

| code | Meaning | Advice |
| --- | --- | --- |
| `derivatives_storage_exfat_symlink_risk` | The output location is on exFAT. exFAT often cannot create symbolic links, so FreeSurfer may fail later during preprocessing. | Move the output location to NTFS or native Linux storage before running. Do not put output, work, container cache, or TemplateFlow preparation paths on exFAT: container image preparation may need symbolic links, and TemplateFlow pulls can be very slow on exFAT. |
| `wsl_image_storage_growth_risk` | Windows storage behind WSL or Docker may not have enough growth headroom. | Free space on the Windows host drive or move WSL/Docker storage before continuing. |
| `prepare_runtime_required_fmriprep_image` | The fMRIPrep image still needs to be prepared before preprocessing can be reconsidered. | Let the tool prepare and verify the image, then run a fresh readiness review before execution. |
| `prepare_runtime_required_templateflow_cache` | Required TemplateFlow files are missing or incomplete. | Let the tool prepare the required TemplateFlow files, verify them, then run a fresh readiness review. |
| `templateflow_unverified` | TemplateFlow could not be fully proven from DataLad/git-annex metadata in the target environment. | Proceed only if you accept that fMRIPrep or XCP-D may fail later because required TemplateFlow files are absent, unreadable, or need network access. |
| `missing_fs_license` | The FreeSurfer license file is not available in the place where preprocessing will run. | Provide a readable FreeSurfer license file for the selected machine. If you do not have one yet, register at https://surfer.nmr.mgh.harvard.edu/registration.html, then place the license where the run can read it. |
| `missing_t1w` | A required T1w anatomical image is missing for this subject or session. | Restore the T1w image or exclude this subject/session from the run. |
| `missing_bold` | Required BOLD functional files are missing for this subject or session. | Restore the BOLD files or exclude this subject/session from the run. |
| `annex_content_missing` | The dataset contains DataLad/git-annex pointers whose file contents are not present. | Materialize the missing file contents on the target filesystem before running. |
| `missing_fmriprep_derivatives` | Required fMRIPrep derivatives are missing for XCP-D. | Run or restore fMRIPrep outputs before running XCP-D. |
| `missing_xcpd_abcd_cifti_derivatives` | ABCD-mode XCP-D cannot find the required CIFTI derivatives from fMRIPrep. | Run or restore fMRIPrep CIFTI outputs before running ABCD-mode XCP-D for this subject. |
| `runtime_write_permission_denied` | The selected account cannot write to one or more run output, work, or log locations. | Move the affected location to writable storage or fix permissions before continuing. Use the path details from the report to identify the exact location. |

## Full code table

### Blocker `blocker`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `missing_fs_license` | `shared` | `2` | The FreeSurfer license file is not available in the place where preprocessing will run. | Provide a readable FreeSurfer license file for the selected machine. If you do not have one yet, register at https://surfer.nmr.mgh.harvard.edu/registration.html, then place the license where the run can read it. |
| `missing_fmriprep_image` | `fmriprep` | `4` | The fMRIPrep image path points to a file that is not available to the selected runtime. | Provide an image path that exists on the selected machine, or let the tool prepare the default fMRIPrep image before trying to run. |
| `invalid_fmriprep_image` | `fmriprep` | `3` | The fMRIPrep image is present, but a lightweight validation check could not confirm that it can start fMRIPrep. | Replace the image with a known-good fMRIPrep image, or let the tool prepare a fresh default image before trying to run. |
| `missing_container_runtime` | `shared` | `4` | The selected machine does not have a supported container runtime available. | Use a machine with Docker, Apptainer, or Singularity available, or enable one of those runtimes before trying to run. |
| `docker_daemon_unavailable` | `shared` | `4` | Docker is installed, but the Docker service is not reachable from the current environment. | Start Docker Desktop or the Docker daemon, fix permission or connection problems, then run the readiness check again. |
| `remote_runtime_probe_failed` | `shared` | `4` | The remote machine could not be checked, so runtime, image, TemplateFlow, and write-permission facts are unknown. | Fix the remote login or shell startup problem first, then run the same review again. Do not assume the image, TemplateFlow cache, or runtime is missing until the remote check succeeds. |
| `native_windows_requires_docker` | `shared` | `4` | Native Windows preprocessing can run only through Docker. | Use Docker on Windows, switch to WSL or Linux, or choose a remote Linux/HPC machine for Apptainer or Singularity. |
| `docker_runtime_requires_registry_image` | `shared` | `3` | Docker needs a registry image name, but the supplied image value is a local file path or unsupported reference. | Provide a Docker registry image such as `nipreps/fmriprep:<tag>` instead of a SIF/SIMG file or `docker://` reference. |
| `posix_runtime_requires_posix_image_path` | `shared` | `3` | Apptainer or Singularity needs a POSIX image path or remote image reference, but the supplied path is Windows-style. | Use a Linux, WSL, or remote POSIX image path, or provide a remote image reference supported by the selected runtime. |
| `remote_docker_slurm_daemon_unverified` | `shared` | `4` | Docker was checked on the SSH target, but Slurm compute nodes may not have access to the same Docker daemon. | Use Apptainer or Singularity for remote Slurm, switch to remote-local on a compute-capable node, or choose a host where Docker runs on the execution node. |
| `missing_templateflow_home_for_remote_cleanenv` | `shared` | `3` | The remote clean-environment run needs an explicit TemplateFlow folder that the container can see. | Provide a target-visible TemplateFlow folder, or use the default download location prepared by the tool. |
| `runtime_write_permission_denied` | `shared` | `5` | The selected account cannot write to one or more run output, work, or log locations. | Move the affected location to writable storage or fix permissions before continuing. Use the path details from the report to identify the exact location. |
| `invalid_scheduler_partition` | `shared` | `4` | The requested scheduler partition is not safe or is not available on the target scheduler. | Use one existing scheduler partition name from the target cluster. The value must be one name with no whitespace or control characters. |
| `work_root_inside_bids` | `shared` | `4` | The work folder is inside the BIDS input dataset. | Move the work folder outside the input dataset before running. |
| `log_root_inside_bids` | `shared` | `4` | The log folder is inside the BIDS input dataset. | Move the log folder outside the input dataset before running. |
| `no_runnable_subjects` | `shared` | `5` | None of the selected subject/session entries can run. | Review the listed exclusion reasons, then fix the data or choose a different selection. |
| `missing_templateflow_home_for_prepare` | `shared` | `4` | Prepare was asked to build TemplateFlow, but no target TemplateFlow path was available. | Pass `--templateflow-home` / `templateflow_home` explicitly and rerun prepare through the router. |
| `missing_xcpd_image` | `xcpd` | `4` | XCP-D image is missing or the supplied local path is unavailable. | Provide a valid registry reference or a local SIF/SIMG path visible to the target runtime. |
| `xcpd_abcd_requires_surface_or_cifti` | `xcpd` | `4` | XCP-D abcd mode was requested from a saved fMRIPrep audit that explicitly skipped FreeSurfer reconstruction. | Use nichart mode for this saved no-reconall fMRIPrep output, or rerun fMRIPrep with FreeSurfer/CIFTI outputs before using XCP-D abcd mode. |
| `remote_execution_requires_slurm` | `shared` | `4` | Remote execution must use Slurm. | Use the current remote-local or Slurm execution policy instead of relying on this legacy blocker. |

### Prepare required `prepare-required`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `prepare_runtime_required_fmriprep_image` | `fmriprep` | `3` | The fMRIPrep image still needs to be prepared before preprocessing can be reconsidered. | Let the tool prepare and verify the image, then run a fresh readiness review before execution. |
| `prepare_runtime_required_templateflow_cache` | `shared` | `3` | Required TemplateFlow files are missing or incomplete. | Let the tool prepare the required TemplateFlow files, verify them, then run a fresh readiness review. |
| `prepare_runtime_required_templateflow_container_import` | `shared` | `3` | TemplateFlow files are present, but the selected image has not proven it can read them inside the container. | Let the tool verify or repair TemplateFlow visibility for the selected image, then run a fresh readiness review. |
| `prepare_runtime_required_xcpd_image` | `xcpd` | `3` | A remote XCP-D image reference is available, but it must be materialized before execution can be reconsidered. | Run the XCP-D prepare route before execution. |

### Warning `warning`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `remote_local_execution_current_node` | `shared` | `4` | Remote-local execution will run on the current SSH target node rather than through the scheduler. | Confirm that this SSH target is the compute node you want to use, or connect to an appropriate compute node before authorizing execution. |
| `resource_plan_cpu_overcommit` | `shared` | `3` | The explicit local or remote-local resource plan requests more concurrent CPU work than the detected host capacity. | The requested resource values were preserved; reduce subject concurrency or per-job threads if the host cannot sustain the load. |
| `resource_plan_memory_overcommit` | `shared` | `3` | The explicit local or remote-local resource plan requests more total per-job memory than the detected host memory. | The requested memory value was preserved as a per-job request or hint; reduce concurrency or memory if the host cannot sustain the load. |
| `resource_plan_omp_exceeds_threads` | `shared` | `3` | The explicit OMP thread count is greater than the total thread count requested for each job. | The requested values were preserved; set OMP threads no higher than per-job total threads unless this mismatch is intentional. |
| `explicit_local_requires_slurm_allocation` | `shared` | `5` | Local execution was requested on a Slurm host without an active allocation. | Start an interactive Slurm allocation first, or let the workflow submit the job through Slurm. |
| `missing_wsl_vhdx_path` | `shared` | `2` | The tool cannot accurately check native WSL storage growth for this setup. | Provide the WSL virtual disk location if exact storage growth checks are needed; otherwise treat the storage estimate as less certain. |
| `missing_windows_host_drive` | `shared` | `2` | The Windows host drive behind WSL was not identified. | Tell the tool which Windows drive backs the selected storage so free-space checks can include it. |
| `wsl_vhdx_host_drive_unknown` | `shared` | `2` | The Windows host drive behind the WSL virtual disk could not be resolved for storage comparison. | Treat the free-space comparison as unavailable, or provide the Windows host drive before relying on storage headroom. |
| `wsl_image_storage_growth_risk` | `shared` | `4` | Windows storage behind WSL or Docker may not have enough growth headroom. | Free space on the Windows host drive or move WSL/Docker storage before continuing. |
| `derivatives_storage_exfat_symlink_risk` | `shared` | `5` | The output location is on exFAT. exFAT often cannot create symbolic links, so FreeSurfer may fail later during preprocessing. | Move the output location to NTFS or native Linux storage before running. Do not put output, work, container cache, or TemplateFlow preparation paths on exFAT: container image preparation may need symbolic links, and TemplateFlow pulls can be very slow on exFAT. |
| `templateflow_unverified` | `shared` | `2` | TemplateFlow could not be fully proven from DataLad/git-annex metadata in the target environment. | Proceed only if you accept that fMRIPrep or XCP-D may fail later because required TemplateFlow files are absent, unreadable, or need network access. |
| `existing_fmriprep_derivatives_detected` | `fmriprep` | `4` | Existing fMRIPrep outputs were found for this request; stale FreeSurfer subject state or IsRunning locks may stop reruns. | Reuse the existing outputs when that is intended. For a fresh rerun, use a clean fMRIPrep/FreeSurfer output area or resolve stale FreeSurfer IsRunning locks before launching. |
| `invalid_xcpd_image` | `xcpd` | `3` | The supplied or already-present XCP-D runtime image exists, but lightweight no-pull validation could not confirm it. | Keep the explicit image if you trust it; if execution fails, replace it with a valid image or use a registry reference and rerun XCP-D prepare. |
| `existing_xcpd_derivatives_detected` | `xcpd` | `2` | Existing XCP-D derivatives were found. | Reuse existing XCP-D outputs unless the user explicitly asks for a rerun. |
| `xcpd_min_time_not_met` | `xcpd` | `2` | Some BOLD runs are shorter than the XCP-D min-time threshold. | Review whether those runs should be excluded from XCP-D. |
| `xcpd_storage_estimate_unresolved` | `xcpd` | `2` | XCP-D storage estimation could not identify any estimable derivative outputs for a runnable subject. | Review the fMRIPrep derivative inventory and XCP-D mode before relying on the storage estimate. |
| `xcpd_bids_root_not_provided` | `xcpd` | `1` | XCP-D is using the provided fMRIPrep derivatives without a raw BIDS root. | This is acceptable when the fMRIPrep derivatives root is correct. Provide bids_root only when raw BIDS context is needed for discovery or reporting. |
| `invalid_cached_xcpd_image` | `xcpd` | `3` | Cached XCP-D image exists but failed runtime validation. | Rematerialize the XCP-D image or provide another valid image path. |

### Subject exclusion `subject-exclusion`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `missing_subject_dir` | `shared` | `3` | A requested subject folder was not found in the dataset. | Check the subject selection and dataset path, then correct the selection or restore the subject folder. |
| `missing_t1w` | `shared` | `3` | A required T1w anatomical image is missing for this subject or session. | Restore the T1w image or exclude this subject/session from the run. |
| `missing_bold` | `shared` | `3` | Required BOLD functional files are missing for this subject or session. | Restore the BOLD files or exclude this subject/session from the run. |
| `dataset_not_materialized` | `shared` | `2` | Some selected dataset files are referenced but their contents are not present on the audited filesystem. | Materialize or download the missing input files where the review and run will happen. |
| `annex_content_missing` | `shared` | `2` | The dataset contains DataLad/git-annex pointers whose file contents are not present. | Materialize the missing file contents on the target filesystem before running. |
| `datalad_get_required` | `shared` | `2` | Selected input files still need DataLad content materialization. | Materialize the selected subject/session inputs with DataLad on the target filesystem before running. |
| `git_annex_get_required` | `shared` | `2` | Selected input files still need git-annex content materialization. | Materialize the selected subject/session inputs with git-annex on the target filesystem before running. |
| `permission_denied` | `shared` | `3` | The selected account cannot read one or more input files. | Fix file permissions or run from an account that can read the selected input files. |
| `invalid_t1w_image` | `shared` | `4` | A T1w image failed validation. | Repair or re-download the T1w image before retrying this subject/session. |
| `invalid_bold_image` | `shared` | `4` | A BOLD image failed validation. | Repair or re-download the BOLD image before retrying this subject/session. |
| `missing_fmriprep_derivatives` | `xcpd` | `4` | Required fMRIPrep derivatives are missing for XCP-D. | Run or restore fMRIPrep outputs before running XCP-D. |
| `missing_xcpd_abcd_cifti_derivatives` | `xcpd` | `4` | ABCD-mode XCP-D cannot find the required CIFTI derivatives from fMRIPrep. | Run or restore fMRIPrep CIFTI outputs before running ABCD-mode XCP-D for this subject. |
| `missing_xcpd_nichart_nifti_derivatives` | `xcpd` | `4` | NiChart-mode XCP-D cannot find the required NIfTI derivatives from fMRIPrep. | Run or restore the required fMRIPrep NIfTI outputs before running NiChart-mode XCP-D for this subject. |
| `missing_xcpd_task_derivatives` | `xcpd` | `4` | XCP-D task filtering selected no matching fMRIPrep derivatives for this subject. | Check the saved XCP-D task filter or rerun fMRIPrep for the selected task before running XCP-D. |

### Request problem `request`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `invalid_xcpd_dataset_alias` | `xcpd` | `4` | An XCP-D extra dataset alias contains characters the wrapper cannot safely pass to XCP-D. | Use a short alias made only of letters, numbers, underscore, dot, or hyphen. Do not include slashes, equals signs, spaces, or an empty alias. |
| `missing_xcpd_dataset` | `xcpd` | `4` | An XCP-D extra dataset path is missing, not a directory, or not visible in the execution environment. | Provide a directory that exists on the target machine for each XCP-D extra dataset alias. |
| `missing_xcpd_dataset_description` | `xcpd` | `4` | An XCP-D extra dataset directory exists, but its dataset root is missing dataset_description.json. | Place dataset_description.json directly inside the extra derivative or atlas dataset root before running XCP-D. |
| `invalid_xcpd_dataset_type` | `xcpd` | `4` | An XCP-D extra dataset has an unsupported DatasetType in dataset_description.json. | Use DatasetType derivative for normal extra datasets. DatasetType atlas is allowed only for legacy atlas datasets and will still produce a warning. Any other value blocks XCP-D. |
| `missing_xcpd_bids_filter_file` | `xcpd` | `4` | The XCP-D BIDS filter file is missing, not a regular file, or not visible in the execution environment. | Provide a JSON file that exists on the target machine for xcpd_bids_filter_file before running XCP-D. |
| `invalid_xcpd_bids_filter_file` | `xcpd` | `4` | The XCP-D BIDS filter file is not valid JSON. | Fix the BIDS filter file so it parses as JSON before rerunning XCP-D audit. |

### Saved artifact replay `artifact-replay`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `missing_runtime_audit_artifact` | `shared` | `3` | A saved runtime review file is missing. | Run a fresh process review or provide the correct saved review folder. |
| `invalid_runtime_audit_artifact` | `shared` | `3` | A saved runtime review file is invalid or unsupported. | Run a fresh process review to recreate the saved runtime review. |
| `runtime_audit_request_mismatch` | `shared` | `3` | The saved runtime review does not match the current request. | Run a fresh process review for the current dataset, paths, and runtime choices. |
| `runtime_audit_not_ready` | `shared` | `3` | The saved runtime review is not ready for execution. | Resolve the listed runtime findings, then rerun the review or continue through the appropriate preparation route. |
| `missing_dataset_audit_artifact` | `shared` | `3` | A saved dataset review file is missing. | Run a fresh process review or provide the correct saved review folder. |
| `invalid_dataset_audit_artifact` | `shared` | `3` | A saved dataset review file is invalid or unsupported. | Run a fresh process review to recreate the saved dataset review. |
| `dataset_audit_request_mismatch` | `shared` | `3` | The saved dataset review does not match the current request. | Run a fresh process review for the current dataset and selection. |
| `dataset_audit_not_ready` | `shared` | `3` | The saved dataset review is not ready for execution. | Fix the listed dataset findings, then rerun the review or continue through the appropriate route. |
| `dataset_audit_debug_not_ready` | `shared` | `3` | The saved detailed dataset review is not ready. | Run a fresh process review; the user-facing report should still use only compact review facts. |
| `missing_dataset_audit_debug_artifact` | `shared` | `3` | A saved detailed dataset review file is missing. | Run a fresh process review or provide the correct saved review folder. |
| `invalid_dataset_audit_debug_artifact` | `shared` | `3` | A saved detailed dataset review file is invalid or unsupported. | Run a fresh process review; do not use the detailed file as a report substitute. |
| `dataset_audit_debug_request_mismatch` | `shared` | `3` | The saved detailed dataset review does not match the current request. | Run a fresh process review for the current dataset and selection. |
| `audit_snapshot_mismatch` | `shared` | `4` | The saved review files do not come from the same snapshot. | Run a fresh process review instead of mixing files from different runs. |
| `xcpd_runtime_audit_not_ready` | `xcpd` | `3` | XCP-D runtime audit is still blocked or not ready. | Resolve XCP-D runtime findings, then rerun or prepare through the XCP-D route. |
| `xcpd_dataset_audit_not_ready` | `xcpd` | `3` | XCP-D dataset audit is still blocked. | Fix XCP-D dataset findings, then rerun through the XCP-D route. |
| `xcpd_dataset_audit_debug_not_ready` | `xcpd` | `3` | XCP-D detailed dataset audit is still blocked. | Rerun XCP-D audit so saved subject readiness can be rebuilt. |
| `missing_xcpd_runtime_audit_artifact` | `xcpd` | `3` | Saved xcpd runtime audit artifact was not found. | Rerun the matching XCP-D audit or provide the correct archive/output root. |
| `invalid_xcpd_runtime_audit_artifact` | `xcpd` | `3` | Saved xcpd runtime audit artifact is corrupt or unsupported. | Rerun the matching XCP-D audit to recreate it. |
| `xcpd_runtime_audit_request_mismatch` | `xcpd` | `3` | Saved xcpd runtime audit artifact does not match the current request. | Rerun the XCP-D audit or use the matching saved request/archive. |
| `missing_xcpd_dataset_audit_artifact` | `xcpd` | `3` | Saved xcpd dataset audit artifact was not found. | Rerun the matching XCP-D audit or provide the correct archive/output root. |
| `invalid_xcpd_dataset_audit_artifact` | `xcpd` | `3` | Saved xcpd dataset audit artifact is corrupt or unsupported. | Rerun the matching XCP-D audit to recreate it. |
| `xcpd_dataset_audit_request_mismatch` | `xcpd` | `3` | Saved xcpd dataset audit artifact does not match the current request. | Rerun the XCP-D audit or use the matching saved request/archive. |
| `missing_xcpd_dataset_audit_debug_artifact` | `xcpd` | `3` | Saved xcpd dataset audit debug artifact was not found. | Rerun the matching XCP-D audit or provide the correct archive/output root. |
| `invalid_xcpd_dataset_audit_debug_artifact` | `xcpd` | `3` | Saved xcpd dataset audit debug artifact is corrupt or unsupported. | Rerun the matching XCP-D audit to recreate it. |
| `xcpd_dataset_audit_debug_request_mismatch` | `xcpd` | `3` | Saved xcpd dataset audit debug artifact does not match the current request. | Rerun the XCP-D audit or use the matching saved request/archive. |

### Advice `advice`

| code | scope | severity | meaning | advice |
| --- | --- | --- | --- | --- |
| `high_resolution_input_res2_default` | `fmriprep` | `1` | High-resolution input was detected while default res-2 outputs are selected. | Keep the default output resolution unless this study specifically needs higher-resolution derivatives. |
| `existing_derivatives_default_continue` | `shared` | `1` | Default action is to continue from existing results instead of rerunning preprocessing. | Ask for an explicit rerun only when existing derivatives should be discarded. |
| `existing_derivatives_rerun_requires_confirmation` | `shared` | `2` | Rerunning from scratch requires explicit user confirmation. | Confirm the rerun intent before replacing existing derivative outputs. |

## Update rule

If `issue_catalog.json` adds, removes, or renames a code, update this file and the Chinese version, then run an issue catalog or skill contract test.
