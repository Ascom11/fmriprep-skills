# issue code 中文索引

本页整理当前 `fmri_core/resources/issue_catalog.json` 中的全部 issue code。中文 README 引用本页。排查时以 agent 审查报告里的 code、路径和建议为准。

## 类别怎么读

| 类别 | 中文名 | 含义 |
| --- | --- | --- |
| `blocker` | 硬阻断 | 当前不能安全执行。需要先修复路径、运行环境、权限、license、镜像或输入范围。 |
| `prepare-required` | 可准备项 | 用户确认后，agent 可以准备镜像或 TemplateFlow，然后重新审查。 |
| `warning` | 风险提醒 | 通常不阻止执行，但会提高失败、磁盘占满、结果复用错误或后续排查困难的风险。 |
| `subject-exclusion` | 被试排除原因 | 某个 subject 或 session 暂时不能跑。其他合格 subject 仍可继续，除非没有可运行对象。 |
| `request` | 请求参数问题 | 用户提供的 XCP-D 参数、筛选文件或附加数据集不合法，继续前需要改请求。 |
| `artifact-replay` | 保存结果复用问题 | 继续旧 audit 或 saved execution 时，保存的 artifact 缺失、损坏、不匹配或还没 ready。 |
| `advice` | 建议 | 不会阻断流程，用来提示默认行为或更稳妥的选择。 |

## 新手最常见的风险

| code | 中文说明 | 建议 |
| --- | --- | --- |
| `derivatives_storage_exfat_symlink_risk` | 输出位置在 exFAT 上。FreeSurfer 常需要创建符号链接，exFAT 上可能失败。 | 把 output、work、镜像缓存和 TemplateFlow 准备路径换到 NTFS 或 Linux 文件系统。 |
| `wsl_image_storage_growth_risk` | WSL 或 Docker 背后的 Windows 存储增长空间可能不足。 | 释放 Windows 宿主盘空间，或把 WSL/Docker 存储迁到空间更充足的位置。 |
| `prepare_runtime_required_fmriprep_image` | fMRIPrep 镜像还需要准备。 | 用户确认后让 agent 准备并验证镜像，然后重新运行 readiness review。 |
| `prepare_runtime_required_templateflow_cache` | 需要的 TemplateFlow 文件缺失或不完整。 | 用户确认后让 agent 准备 TemplateFlow，验证后重新审查。 |
| `templateflow_unverified` | 目标环境里的 DataLad 或 git-annex 证据不足，TemplateFlow 不能完全证明。 | 只有在接受后续可能因模板文件缺失、不可读或需要联网而失败时才继续。 |
| `missing_fs_license` | 运行位置看不到可读的 FreeSurfer license。 | 提供目标机器可读的 `license.txt`。如果还没有 license，先到 FreeSurfer 官网注册，再把文件放到运行环境能读到的位置。 |
| `missing_t1w` | 该 subject 或 session 缺少必需的 T1w 解剖图像。 | 恢复 T1w 文件，或从本次运行中排除这个 subject 或 session。 |
| `missing_bold` | 该 subject 或 session 缺少必需的 BOLD 文件。 | 恢复 BOLD 文件，或从本次运行中排除这个 subject 或 session。 |
| `annex_content_missing` | 数据集中有 DataLad 或 git-annex 指针，但文件内容没取回。 | 在目标文件系统上用 DataLad 或 git-annex 取回缺失内容。 |
| `missing_fmriprep_derivatives` | XCP-D 需要的 fMRIPrep derivatives 缺失。 | 先运行或恢复 fMRIPrep 输出，再运行 XCP-D。 |
| `missing_xcpd_abcd_cifti_derivatives` | ABCD mode XCP-D 找不到所需 CIFTI derivatives。 | 先生成或恢复 fMRIPrep CIFTI 输出，或改用适合 NIfTI 的 mode。 |
| `runtime_write_permission_denied` | 当前账号不能写入一个或多个 output、work 或 log 位置。 | 根据报告里的具体路径修权限，或换到可写目录。 |

## 完整 code 表

### 硬阻断 `blocker`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `missing_fs_license` | `shared` | `2` | 运行位置看不到可读的 FreeSurfer license。 | 提供目标机器可读的 `license.txt`。如果还没有 license，先到 FreeSurfer 官网注册，再把文件放到运行环境能读到的位置。 |
| `missing_fmriprep_image` | `fmriprep` | `4` | 指定的 fMRIPrep 镜像在当前运行环境不可见。 | 改用目标机器可见的镜像路径，或让 agent 准备默认 fMRIPrep 镜像后重新审查。 |
| `invalid_fmriprep_image` | `fmriprep` | `3` | fMRIPrep 镜像文件存在，但轻量验证没有确认它能启动 fMRIPrep。 | 换成已知可用的 fMRIPrep 镜像，或让 agent 准备新的默认镜像。 |
| `missing_container_runtime` | `shared` | `4` | 目标机器没有可用的 Docker、Apptainer 或 Singularity。 | 启用一种容器运行时，或换到有容器运行时的 WSL、Linux、HPC 或远程机器。 |
| `docker_daemon_unavailable` | `shared` | `4` | Docker 已安装，但当前环境连不上 Docker daemon。 | 启动 Docker Desktop 或 Docker daemon，修复权限和连接问题后重新审查。 |
| `remote_runtime_probe_failed` | `shared` | `4` | 远程机器检查失败，runtime、镜像、TemplateFlow 和写权限都无法确认。 | 先修复 SSH 登录或 shell 启动问题，再重新审查。不要在远程检查失败时猜测镜像或 TemplateFlow 是否缺失。 |
| `native_windows_requires_docker` | `shared` | `4` | Windows 原生环境只能通过 Docker 跑预处理。 | 在 Windows 上使用 Docker，或切到 WSL、Linux、远程 Linux/HPC。 |
| `docker_runtime_requires_registry_image` | `shared` | `3` | Docker 需要 registry 镜像名，但当前值是本地文件路径或不支持的引用。 | 给 Docker 使用类似 `nipreps/fmriprep:<tag>` 的 registry 镜像。SIF/SIMG 文件适合 Apptainer 或 Singularity。 |
| `posix_runtime_requires_posix_image_path` | `shared` | `3` | Apptainer 或 Singularity 需要 POSIX 路径或远程镜像引用，但当前镜像路径是 Windows 风格。 | 在 Linux、WSL 或远程机器上使用 POSIX 路径，或提供目标 runtime 支持的远程镜像引用。 |
| `remote_docker_slurm_daemon_unverified` | `shared` | `4` | SSH 目标上能检查 Docker，但 Slurm 计算节点未必能访问同一个 Docker daemon。 | 远程 Slurm 推荐使用 Apptainer 或 Singularity。也可以改用 remote-local 计算节点或确认执行节点有 Docker。 |
| `missing_templateflow_home_for_remote_cleanenv` | `shared` | `3` | 远程 cleanenv 容器需要显式 TemplateFlow 目录，但当前没有可见路径。 | 提供目标机器可见的 TemplateFlow 目录，或让 agent 使用默认下载目录准备。 |
| `runtime_write_permission_denied` | `shared` | `5` | 当前账号不能写入一个或多个 output、work 或 log 位置。 | 根据报告里的具体路径修权限，或换到可写目录。 |
| `invalid_scheduler_partition` | `shared` | `4` | 请求的 Slurm partition 不存在、不安全，或包含不允许的字符。 | 使用目标集群上真实存在的单个 partition 名称，不要带空格或控制字符。 |
| `work_root_inside_bids` | `shared` | `4` | work 目录被放在 BIDS 输入数据集里面。 | 把 work 目录移到 BIDS 输入目录外。 |
| `log_root_inside_bids` | `shared` | `4` | log 目录被放在 BIDS 输入数据集里面。 | 把 log 目录移到 BIDS 输入目录外。 |
| `no_runnable_subjects` | `shared` | `5` | 选择范围内没有任何可运行的 subject 或 session。 | 先处理列出的排除原因，或换一组 subject/session。 |
| `missing_templateflow_home_for_prepare` | `shared` | `4` | prepare 被要求准备 TemplateFlow，但没有目标 TemplateFlow 路径。 | 显式提供 `--templateflow-home` 或 `templateflow_home`，再通过 router 重新 prepare。 |
| `missing_xcpd_image` | `xcpd` | `4` | XCP-D 镜像缺失，或提供的本地路径在目标环境不可见。 | 提供有效 registry reference，或提供目标 runtime 可见的 SIF/SIMG 路径。 |
| `xcpd_abcd_requires_surface_or_cifti` | `xcpd` | `4` | 从保存的 no-reconall fMRIPrep audit 请求 ABCD mode XCP-D，但源输出明确没有 surface/CIFTI。 | 对 no-reconall 输出使用 `nichart`，或重跑带 FreeSurfer/CIFTI 输出的 fMRIPrep。 |
| `remote_execution_requires_slurm` | `shared` | `4` | 远程执行必须使用 Slurm。 | 使用当前 remote-local 或 Slurm execution policy，不要依赖这个 legacy blocker。 |

### 可准备项 `prepare-required`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `prepare_runtime_required_fmriprep_image` | `fmriprep` | `3` | fMRIPrep 镜像还需要准备。 | 用户确认后让 agent 准备并验证镜像，然后重新运行 readiness review。 |
| `prepare_runtime_required_templateflow_cache` | `shared` | `3` | 需要的 TemplateFlow 文件缺失或不完整。 | 用户确认后让 agent 准备 TemplateFlow，验证后重新审查。 |
| `prepare_runtime_required_templateflow_container_import` | `shared` | `3` | TemplateFlow 文件存在，但还没有证明容器内部能读到。 | 让 agent 验证或修复 TemplateFlow 到容器的可见性，然后重新审查。 |
| `prepare_runtime_required_xcpd_image` | `xcpd` | `3` | 远程 XCP-D 镜像引用存在，但执行前需要 materialize。 | 先走 XCP-D prepare route，再重新考虑执行。 |

### 风险提醒 `warning`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `remote_local_execution_current_node` | `shared` | `4` | 远程 local 执行会直接跑在当前 SSH 连接到的节点上，而不是通过调度器提交。 | 确认这个 SSH 目标就是要使用的计算节点。否则改为 Slurm 提交或连接到正确节点。 |
| `resource_plan_cpu_overcommit` | `shared` | `3` | 显式资源设置会让本地或远程 local 并发 CPU 超过检测到的容量。 | 如果机器承受不了，降低 `--max-jobs` 或 `--nthreads-per-job`。 |
| `resource_plan_memory_overcommit` | `shared` | `3` | 显式资源设置可能让本地或远程 local 并发内存超过检测到的内存。 | 降低并发或每个 job 的内存设置。 |
| `resource_plan_omp_exceeds_threads` | `shared` | `3` | `--omp-nthreads` 大于每个 job 的总线程数。 | 让 OMP 线程数不超过 `--nthreads-per-job`，除非你明确知道这个设置是故意的。 |
| `explicit_local_requires_slurm_allocation` | `shared` | `5` | 在 Slurm 主机上显式要求 local 执行，但当前没有活动 Slurm allocation。 | 先申请交互式 Slurm allocation，或让 workflow 通过 Slurm 提交。 |
| `missing_wsl_vhdx_path` | `shared` | `2` | 工具无法精确检查 WSL 虚拟磁盘增长。 | 如果需要精确空间判断，提供 WSL VHDX 路径。否则把空间估计当成不完全可靠。 |
| `missing_windows_host_drive` | `shared` | `2` | WSL 背后的 Windows 宿主盘没有被识别。 | 告诉 agent 相关 Windows 盘符，便于一起检查宿主盘剩余空间。 |
| `wsl_vhdx_host_drive_unknown` | `shared` | `2` | WSL 虚拟磁盘背后的 Windows 宿主盘无法解析。 | 不要完全依赖这次空间比较。可以提供宿主盘信息后重新审查。 |
| `wsl_image_storage_growth_risk` | `shared` | `4` | WSL 或 Docker 背后的 Windows 存储增长空间可能不足。 | 释放 Windows 宿主盘空间，或把 WSL/Docker 存储迁到空间更充足的位置。 |
| `derivatives_storage_exfat_symlink_risk` | `shared` | `5` | 输出位置在 exFAT 上。FreeSurfer 常需要创建符号链接，exFAT 上可能失败。 | 把 output、work、镜像缓存和 TemplateFlow 准备路径换到 NTFS 或 Linux 文件系统。 |
| `templateflow_unverified` | `shared` | `2` | 目标环境里的 DataLad 或 git-annex 证据不足，TemplateFlow 不能完全证明。 | 只有在接受后续可能因模板文件缺失、不可读或需要联网而失败时才继续。 |
| `existing_fmriprep_derivatives_detected` | `fmriprep` | `4` | 请求范围内已有 fMRIPrep 输出，可能包含旧 FreeSurfer 状态或 IsRunning lock。 | 如果想复用旧结果可以继续。若要 fresh rerun，换干净输出区或先处理旧 FreeSurfer lock。 |
| `invalid_xcpd_image` | `xcpd` | `3` | XCP-D 镜像存在，但轻量 no-pull 验证无法确认它可用。 | 如果信任这个镜像可以继续；若执行失败，换有效镜像或重新准备 XCP-D 镜像。 |
| `existing_xcpd_derivatives_detected` | `xcpd` | `2` | 已有 XCP-D derivatives。 | 除非明确要重跑，否则默认复用现有 XCP-D 输出。 |
| `xcpd_min_time_not_met` | `xcpd` | `2` | 部分 BOLD run 短于 XCP-D min-time 阈值。 | 检查这些 run 是否应该从 XCP-D 中排除。 |
| `xcpd_storage_estimate_unresolved` | `xcpd` | `2` | XCP-D 空间估计找不到可估算的 derivative 输出。 | 在依赖空间估计前，检查 fMRIPrep derivative inventory 和 XCP-D mode。 |
| `xcpd_bids_root_not_provided` | `xcpd` | `1` | XCP-D 正在只使用 fMRIPrep derivatives，没有 raw BIDS root。 | 只要 fMRIPrep derivatives root 正确，这是可以接受的。需要 raw BIDS 上下文时再提供 `bids_root`。 |
| `invalid_cached_xcpd_image` | `xcpd` | `3` | 缓存中的 XCP-D 镜像存在，但 runtime 验证失败。 | 重新 materialize XCP-D 镜像，或提供另一个有效镜像路径。 |

### 被试排除原因 `subject-exclusion`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `missing_subject_dir` | `shared` | `3` | 请求的 subject 目录不存在。 | 检查 subject selector 和数据集路径，修正选择或恢复 subject 目录。 |
| `missing_t1w` | `shared` | `3` | 该 subject 或 session 缺少必需的 T1w 解剖图像。 | 恢复 T1w 文件，或从本次运行中排除这个 subject 或 session。 |
| `missing_bold` | `shared` | `3` | 该 subject 或 session 缺少必需的 BOLD 文件。 | 恢复 BOLD 文件，或从本次运行中排除这个 subject 或 session。 |
| `dataset_not_materialized` | `shared` | `2` | 部分选中文件只是引用，内容没有出现在被审查的文件系统上。 | 在审查和运行发生的目标文件系统上 materialize 或下载这些文件。 |
| `annex_content_missing` | `shared` | `2` | 数据集中有 DataLad 或 git-annex 指针，但文件内容没取回。 | 在目标文件系统上用 DataLad 或 git-annex 取回缺失内容。 |
| `datalad_get_required` | `shared` | `2` | 选中的输入文件需要 DataLad materialization。 | 在目标文件系统上对选中的 subject 或 session 执行 DataLad get。 |
| `git_annex_get_required` | `shared` | `2` | 选中的输入文件需要 git-annex materialization。 | 在目标文件系统上对选中的 subject 或 session 执行 git-annex get。 |
| `permission_denied` | `shared` | `3` | 当前账号不能读取一个或多个输入文件。 | 修复文件权限，或换能读取这些文件的账号运行。 |
| `invalid_t1w_image` | `shared` | `4` | T1w 图像验证失败。 | 修复或重新下载这个 T1w 文件，再重试对应 subject 或 session。 |
| `invalid_bold_image` | `shared` | `4` | BOLD 图像验证失败。 | 修复或重新下载这个 BOLD 文件，再重试对应 subject 或 session。 |
| `missing_fmriprep_derivatives` | `xcpd` | `4` | XCP-D 需要的 fMRIPrep derivatives 缺失。 | 先运行或恢复 fMRIPrep 输出，再运行 XCP-D。 |
| `missing_xcpd_abcd_cifti_derivatives` | `xcpd` | `4` | ABCD mode XCP-D 找不到所需 CIFTI derivatives。 | 先生成或恢复 fMRIPrep CIFTI 输出，或改用适合 NIfTI 的 mode。 |
| `missing_xcpd_nichart_nifti_derivatives` | `xcpd` | `4` | NiChart mode XCP-D 找不到所需 MNI-space NIfTI derivatives。 | 先生成或恢复所需 fMRIPrep NIfTI 输出。 |
| `missing_xcpd_task_derivatives` | `xcpd` | `4` | XCP-D task filter 没有选中任何匹配的 fMRIPrep derivatives。 | 检查保存的 XCP-D task filter，或为选中 task 重跑 fMRIPrep。 |

### 请求参数问题 `request`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `invalid_xcpd_dataset_alias` | `xcpd` | `4` | XCP-D extra dataset alias 含有 wrapper 不能安全传递的字符。 | alias 只使用字母、数字、下划线、点或短横线，不要包含斜杠、等号、空格或空值。 |
| `missing_xcpd_dataset` | `xcpd` | `4` | XCP-D extra dataset 路径缺失、不是目录，或目标环境不可见。 | 为每个 alias 提供目标机器上存在的目录。 |
| `missing_xcpd_dataset_description` | `xcpd` | `4` | XCP-D extra dataset 目录存在，但根目录缺少 `dataset_description.json`。 | 把 `dataset_description.json` 放在这个 derivative 或 atlas dataset 根目录。 |
| `invalid_xcpd_dataset_type` | `xcpd` | `4` | XCP-D extra dataset 的 `DatasetType` 不受支持。 | 普通 extra dataset 使用 `derivative`。旧 atlas dataset 可以用 `atlas`，其他类型会阻断 XCP-D。 |
| `missing_xcpd_bids_filter_file` | `xcpd` | `4` | XCP-D BIDS filter 文件缺失、不是普通文件，或目标环境不可见。 | 提供目标机器上存在的 JSON 文件。 |
| `invalid_xcpd_bids_filter_file` | `xcpd` | `4` | XCP-D BIDS filter 文件不是合法 JSON。 | 修复 JSON 格式后重新运行 XCP-D audit。 |

### 保存结果复用问题 `artifact-replay`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `missing_runtime_audit_artifact` | `shared` | `3` | 继续执行需要的 runtime audit 文件缺失。 | 重新运行 process review，或提供正确的 saved review 目录。 |
| `invalid_runtime_audit_artifact` | `shared` | `3` | 保存的 runtime audit 文件无效或版本不支持。 | 重新运行 process review 生成新的 runtime audit。 |
| `runtime_audit_request_mismatch` | `shared` | `3` | 保存的 runtime audit 与当前请求不匹配。 | 针对当前数据集、路径和 runtime 重新运行 process review。 |
| `runtime_audit_not_ready` | `shared` | `3` | 保存的 runtime audit 还没有达到可执行状态。 | 先处理 runtime findings，再重新审查或走对应 prepare 路由。 |
| `missing_dataset_audit_artifact` | `shared` | `3` | 继续执行需要的 dataset audit 文件缺失。 | 重新运行 process review，或提供正确的 saved review 目录。 |
| `invalid_dataset_audit_artifact` | `shared` | `3` | 保存的 dataset audit 文件无效或版本不支持。 | 重新运行 process review 生成新的 dataset audit。 |
| `dataset_audit_request_mismatch` | `shared` | `3` | 保存的 dataset audit 与当前数据集或选择范围不匹配。 | 针对当前数据集和 selector 重新运行 process review。 |
| `dataset_audit_not_ready` | `shared` | `3` | 保存的 dataset audit 还没有达到可执行状态。 | 先修复列出的 dataset findings，再重新审查或继续对应路线。 |
| `dataset_audit_debug_not_ready` | `shared` | `3` | 保存的详细 dataset audit 仍然不是 ready。 | 重新运行 process review。用户报告仍应只使用 compact audit facts。 |
| `missing_dataset_audit_debug_artifact` | `shared` | `3` | 保存的详细 dataset audit 文件缺失。 | 重新运行 process review，或提供正确的 saved review 目录。 |
| `invalid_dataset_audit_debug_artifact` | `shared` | `3` | 保存的详细 dataset audit 文件无效或版本不支持。 | 重新运行 process review。不要把 debug 文件当成用户报告替代品。 |
| `dataset_audit_debug_request_mismatch` | `shared` | `3` | 保存的详细 dataset audit 与当前请求不匹配。 | 针对当前数据集和 selector 重新运行 process review。 |
| `audit_snapshot_mismatch` | `shared` | `4` | 保存的 review 文件来自不同 snapshot，被混在一起使用。 | 不要混用不同 audit 目录的文件，重新运行 fresh process review。 |
| `xcpd_runtime_audit_not_ready` | `xcpd` | `3` | XCP-D runtime audit 仍被阻断或未 ready。 | 处理 XCP-D runtime findings 后重新 audit 或 prepare。 |
| `xcpd_dataset_audit_not_ready` | `xcpd` | `3` | XCP-D dataset audit 仍被阻断。 | 处理 XCP-D dataset findings 后重新运行 XCP-D route。 |
| `xcpd_dataset_audit_debug_not_ready` | `xcpd` | `3` | XCP-D 详细 dataset audit 仍被阻断。 | 重新运行 XCP-D audit，让保存的 subject readiness 重新生成。 |
| `missing_xcpd_runtime_audit_artifact` | `xcpd` | `3` | 保存的 XCP-D runtime audit artifact 找不到。 | 重新运行匹配的 XCP-D audit，或提供正确 archive/output root。 |
| `invalid_xcpd_runtime_audit_artifact` | `xcpd` | `3` | 保存的 XCP-D runtime audit artifact 损坏或不支持。 | 重新运行匹配的 XCP-D audit。 |
| `xcpd_runtime_audit_request_mismatch` | `xcpd` | `3` | 保存的 XCP-D runtime audit artifact 与当前请求不匹配。 | 重新运行 XCP-D audit，或使用匹配的 saved request/archive。 |
| `missing_xcpd_dataset_audit_artifact` | `xcpd` | `3` | 保存的 XCP-D dataset audit artifact 找不到。 | 重新运行匹配的 XCP-D audit，或提供正确 archive/output root。 |
| `invalid_xcpd_dataset_audit_artifact` | `xcpd` | `3` | 保存的 XCP-D dataset audit artifact 损坏或不支持。 | 重新运行匹配的 XCP-D audit。 |
| `xcpd_dataset_audit_request_mismatch` | `xcpd` | `3` | 保存的 XCP-D dataset audit artifact 与当前请求不匹配。 | 重新运行 XCP-D audit，或使用匹配的 saved request/archive。 |
| `missing_xcpd_dataset_audit_debug_artifact` | `xcpd` | `3` | 保存的 XCP-D dataset audit debug artifact 找不到。 | 重新运行匹配的 XCP-D audit，或提供正确 archive/output root。 |
| `invalid_xcpd_dataset_audit_debug_artifact` | `xcpd` | `3` | 保存的 XCP-D dataset audit debug artifact 损坏或不支持。 | 重新运行匹配的 XCP-D audit。 |
| `xcpd_dataset_audit_debug_request_mismatch` | `xcpd` | `3` | 保存的 XCP-D dataset audit debug artifact 与当前请求不匹配。 | 重新运行 XCP-D audit，或使用匹配的 saved request/archive。 |

### 建议 `advice`

| code | scope | severity | 中文说明 | 建议 |
| --- | --- | --- | --- | --- |
| `high_resolution_input_res2_default` | `fmriprep` | `1` | 检测到高分辨率输入，但当前使用默认 res-2 输出。 | 除非研究确实需要更高分辨率 derivatives，否则保留默认输出分辨率。 |
| `existing_derivatives_default_continue` | `shared` | `1` | 默认会从现有结果继续，而不是从头重跑预处理。 | 只有确实要替换旧 derivatives 时才明确要求 rerun。 |
| `existing_derivatives_rerun_requires_confirmation` | `shared` | `2` | 从头重跑需要用户明确确认。 | 确认 rerun 意图后再替换现有 derivative 输出。 |

## 更新规则

如果 `issue_catalog.json` 新增、删除或改名 code，请同步更新本页和英文版，并至少运行一次 issue catalog 或 skill contract 相关测试。
