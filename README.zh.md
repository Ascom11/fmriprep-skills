# fMRIPrep Skills: Help Beginners Use fMRIPrep Easily

语言：[English](README.md) | 中文

> [!IMPORTANT]
> **News（2026.6.8）：XCP-D 26.0.2 可视化报告问题。** 当前默认的 XCP-D 镜像版本是 `26.0.2`。处理完成后的可视化报告阶段可能报错：`TypeError: _warn() got an unexpected keyword argument 'skip_file_prefixes'`，常见位置是 `plot_slices_T1/T2` / brainsprite。参考这个 [Neurostars 讨论](https://neurostars.org/t/xcp-d-26-0-2-fails-during-brainsprite-plot-slices-t1-t2/36172)。后续的 XCP-D `26.0.3` 已经修复，但截至 `2026.6.8`，Docker Hub 上的 `pennlinc/xcp_d:latest` 仍然指向 `26.0.2`，所以不要默认把 `latest` 当成已经修复的镜像。

本项目包含两个 skills：`$fmri-process`、`$fmri-followup`。核心目的：**让不懂代码、第一次处理 BIDS 数据集的初学者少踩坑，尽可能一次就跑通**。

这组 skills 能做什么？你只需要**用一句话告诉 agent**要处理哪个数据集。agent 会先看数据集是否完整，再结合你的电脑或服务器环境，判断应该下载哪个镜像文件、应该怎么拉取镜像、这个环境到底应该怎么跑，最后还可以继续帮你看每个被试跑到什么阶段。

你不需要先懂代码，也不需要记住 fMRIPrep 的长命令。更重要的是，你不需要自己琢磨这些风险：

- 应该下载哪个镜像文件、镜像该怎么拉
- derivatives 和 work 文件会不会撑爆磁盘
- 这么跑会不会跑崩

这些风险 agent 会在最开始的审查里告诉你，并在真正运行前停下来等你确认。

这组 skills 实现了**轻量级的 harness**，包括审查、准备环境、执行预处理、监测进度、记录 trace 文件、报错后的处理等等，把脏活累活都留给了 agent，在真正启动长任务前尽量提前发现路径、数据、镜像、TemplateFlow、license、存储和调度等各方面的风险，从而降低初学者第一次运行时的失败率。

适配各种 agent，包括 Codex、Claude、DeepSeek、MiMo等等。

## 处理流程

默认情况下，普通的处理请求（generic request）会先检查数据集和运行环境（audit）并暂停，报告检查结果和可能的风险项，待用户确认后再运行预处理。完整链路如下：

```text
用户自然语言请求
↓
路径预检查 / 补齐必要输入
↓
创建或读取 harness-trace.md
↓
数据集检查（dataset audit）：检查 BIDS 数据和被试可运行性，估计产物大小
↓
运行环境检查（runtime audit）：检查 license、镜像、容器、TemplateFlow、存储、Slurm 或服务器设置
↓
生成审查报告并默认暂停，反馈给用户
↓
如果运行环境没有准备好：用户确认准备环境
↓
准备运行环境（prepare-runtime / prepare-probe）：准备镜像、TemplateFlow
↓
重新检查
↓
用户确认运行
↓
提交运行（run-fmriprep / run-xcpd）
↓
$fmri-followup 监测进度、日志、崩溃记录和输出完整性
```

默认暂停有两个地方：

- 检查结束后暂停。agent 会告诉你哪些地方能跑、哪些地方不能跑、哪些地方建议先处理。
- 准备环境后再次暂停。准备镜像或 TemplateFlow 不等于授权开始运行。

只有你明确说"审查通过就直接跑"或"现在运行"，agent 才会提交 fMRIPrep 或 XCP-D。

如果你要跑 XCP-D，agent 会先检查 fMRIPrep 的输出是否完整。比如每个被试需要的文件是否存在、是否适合当前 XCP-D 模式、输出目录是否可读。fMRIPrep 跑完不会自动进入 XCP-D，必须由你明确提出。

## 快速开始

### 安装依赖

```bash
git clone https://github.com/Ascom11/fmriprep-skills.git
cd fmriprep-skills
python -m pip install -e .
python -m pip show fmri-proc-tools
```

### 复制 skills

Codex：

```bash
mkdir -p ~/.codex/skills
cp -a skills/fmri-process ~/.codex/skills/
cp -a skills/fmri-followup ~/.codex/skills/
```

Claude 或者其他基于 Claude Code 的 agents：
```bash
mkdir -p ~/.claude/skills
cp -a skills/fmri-process ~/.claude/skills/
cp -a skills/fmri-followup ~/.claude/skills/
```

其他运行前准备：

- 容器软件：Linux、WSL 和服务器推荐 Apptainer 或 Singularity。Windows 原生环境使用 Docker。
- `datalad` 和 `git-annex`：用于确认 TemplateFlow 里的模板文件是否已经完整下载。你可以让 agent 帮你安装。建议安装在运行这些 skills 的同一个 conda 环境里。
- FreeSurfer license：fMRIPrep 中的 freesurfer 需要，可以去 https://surfer.nmr.mgh.harvard.edu/registration.html 注册。注意：即使**不做皮层重建，fMRIPrep 也需要 FreeSurfer license**（参考 https://github.com/nipreps/fmriprep/issues/1747）

镜像和模板可以由 agent 在审查环境之后，由你授权下载。XCP-D 通常不强制要求 TemplateFlow，但某些配置/容器运行可能触发模板访问，因此建议预检，避免容器运行到一半才临时联网下载（容易报错）。

完整版本：

```text
$fmri-process 帮我处理 /path/to/bids_dataset，conda环境用conda_env，镜像在 /path/to/images ，templateflow 在 /path/to/templateflow ，同时跑10个被试
```

完整版本（需要准备环境）：

```text
$fmri-process 帮我处理 /path/to/bids_dataset，conda环境用conda_env，帮我准备环境
```

最简单的用法：

```text
$fmri-process 帮我处理 /path/to/bids_dataset
```

指定被试（可以用通配符）：

```text
$fmri-process 帮我处理 /path/to/bids_dataset 下的sub-00[1-5]
```

指定输出位置：

```text
$fmri-process 帮我处理 /path/to/bids_dataset 下的sub-00[1-5]，输出和工作文件放在e盘下的同名目录
```

在远程服务器运行：

```text
$fmri-process ssh到remote，帮我处理 /path/to/bids_dataset 下的sub-00[1-5]
```

远程服务器不能联网（但需要远端安装 `datalad` 和 `git-annex`），本地准备后再上传：

```text
$fmri-process ssh到remote，帮我处理 /path/to/bids_dataset 下的sub-00[1-5]，服务器没有网，你先帮我拉到本地再传上去
```

从 fMRIPrep 输出继续做 XCP-D：

```text
$fmri-process 对 /path/to/derivatives/fmriprep 下的所有被试继续做 XCP-D
```

检查已经提交的任务：

```text
$fmri-followup 监测一下 / 看看进度
```

### 使用配置文件

如果你已经有一批固定路径或旧容器命令里的参数，可以把它们写到配置文件里，让 agent 先读取 YAML，再转换成明确的 CLI 参数。配置文件是 agent 侧的翻译辅助；底层 CLI 不接受 `--config`，agent 解析完之后仍会用显式路径和参数继续检查或运行。

仓库里有两个示例：

- [config.fmriprep.example.yaml](config.fmriprep.example.yaml)：fMRIPrep 输入、输出、镜像、被试和资源参数。
- [config.xcpd.example.yaml](config.xcpd.example.yaml)：XCP-D 使用的 fMRIPrep derivatives、镜像、被试和滤波参数。

配置使用 `shared`、`fmriprep`、`xcpd` 三个 section。一次具体请求只填当前目标需要的 section；比如只跑 fMRIPrep 时填 `shared` 和 `fmriprep`，不要同时填完整的 `xcpd`。

你也可以直接粘贴以前跑过的容器命令，让 agent 帮你翻译，而不是自己手动改写。例如：

```text
$fmri-process 把这个旧 fMRIPrep 命令翻译成本项目的 CLI 请求，然后先审查：

apptainer run --cleanenv \
  -B /data/ds001:/data:ro \
  -B /data/derivatives:/out \
  -B /scratch/fmriprep_work:/work \
  -B /opt/freesurfer/license.txt:/license.txt:ro \
  docker://nipreps/fmriprep:25.2.5 \
  /data /out participant \
  --participant-label 001 \
  --fs-license-file /license.txt \
  --work-dir /work \
  --output-spaces MNI152NLin2009cAsym:res-2 \
  --cifti-output 91k \
  --nthreads 8 \
  --omp-nthreads 8
```

agent 应该抽取主机侧路径和参数，然后构造等价的显式请求，例如：

```bash
python -m fmri_process.cli process \
  --bids-root /data/ds001 \
  --output-root /data/derivatives \
  --subject 001 \
  --fs-license /opt/freesurfer/license.txt \
  --work-root /scratch/fmriprep_work \
  --fmriprep-image docker://nipreps/fmriprep:25.2.5 \
  --container-runtime apptainer \
  --output-spaces MNI152NLin2009cAsym:res-2 \
  --cifti-output 91k \
  --nthreads-per-job 8 \
  --omp-nthreads 8
```

这种翻译不会绕过 harness。agent 仍然会先审查数据集和运行环境，报告 blockers 和 warnings，并在执行前暂停，除非你明确授权运行。

常见说法和默认行为：

| 用户口谕 | 默认行为 |
| --- | --- |
| `帮我处理这个数据集` | 先检查数据和运行环境，报告后暂停。 |
| `审查数据集` | 只检查 BIDS 文件、T1w、BOLD、被试列表和文件是否真的在本机。 |
| `检查运行环境` | 只检查容器软件、镜像、license、TemplateFlow、磁盘、写权限和服务器设置。 |
| `帮我准备环境` | 在你确认后准备镜像或 TemplateFlow，不自动开始运行。 |
| `审查通过直接跑` | 检查没有硬问题后才运行；需要准备的内容会先暂停。 |
| `继续之前的检查` | 读取保存的检查记录，说明能否继续。不会把"继续"当成运行授权。 |
| `做 XCP-D` | 先检查 fMRIPrep 输出和 XCP-D 环境，报告后暂停。 |
| `监测一下` | 进入 `$fmri-followup`，只读检查进度、日志和崩溃记录。 |

## 不会自动做的事

以下事情不会默认自动执行：

- 不自动申请 FreeSurfer license。
- 不默认替用户 materialize DataLad/git-annex 数据内容，除非用户明确要求。
- 不默认删除旧 derivatives / work 目录。
- 不默认从 fMRIPrep 自动继续到 XCP-D；需要用户明确提出 XCP-D。
- 不把风险项升级到阻断项，但会在执行前提醒用户。
- 不保证数据/参数层面的问题；例如 XCP-D 的 `No runs survived high-motion outlier scrubbing`。

## 保存位置

如果没有指定输出位置，默认输出文件结构为：

```text
<output-root> = <bids-root>/derivatives

<output-root>/
  fmriprep/
  xcp_d/
  _artifacts/
    harness-trace.md
    fmriprep_audit/
    xcpd_audit/
    fmriprep_logs/
    xcpd_logs/

<bids-root>.parent/_downloads/
  images/
    fmriprep.sif
    xcpd.sif
  templateflow/

<bids-root>.parent/work/
  work_fmriprep/
  work_xcpd/
```

指定 `--output-root` 后，下载的镜像和模板默认放在 `<output-root>` 的父目录下的 `_downloads/`。最终路径以 agent 报告为准。

`_artifacts/` 目录下有检查记录和运行记录。`harness-trace.md` 是自然语言进度记录，每个数据集复用一个 `harness-trace.md` 。新对话或上下文压缩后，agent 会先读这个文件，恢复之前的进度。这个文件超过 200 KiB 时会由 agent 自动压缩。

## 运行环境

skills 内部包含了多种运行环境的检测和兼容：

- Linux
- WSL
- Windows 原生配 Docker
- 远程 Linux 或 HPC
- Slurm，推荐用于服务器批量运行

Windows 原生也能通过 SSH 连远程服务器，但不推荐作为首选。PowerShell、Git Bash/MSYS 和远程 Linux 路径混在一起时，可能触发路径改写或引号问题。agent 通常能修，但更加推荐 WSL（https://learn.microsoft.com/en-us/windows/wsl/install）。

容器软件会自动选择：有 Apptainer 就优先用 Apptainer，有 Singularity 就用 Singularity，Windows 原生环境使用 Docker。

## 默认设置

默认 fMRIPrep 镜像版本：

```text
docker://nipreps/fmriprep:25.2.5
```

默认 XCP-D 镜像版本：

```text
docker://pennlinc/xcp_d:26.0.2
```

默认 fMRIPrep 输出空间：

```text
MNI152NLin2009cAsym:res-2
MNI152NLin6Asym:res-2
```

默认 CIFTI：

```text
--cifti-output 91k
```

默认会运行 FreeSurfer。只有你明确使用 `--fs-no-reconall`，才会跳过 FreeSurfer 重建。

每个被试使用多少线程由运行环境检查决定，通常比较保守：

- Slurm 模式默认最多同时跑 4 个被试，每个被试默认 `--nthreads 4 --omp-nthreads 4` ，可以自行指定并发被试数。
- Linux 或远程服务器上的本地 worker pool 默认最多同时跑 `min(4, CPU / 每被试线程数)` 个被试；这里的 worker pool 指在同一台机器上同时启动多个被试任务。
- 本机运行默认一次跑 1 个被试。
- 你可以用 `--max-jobs`、`--nthreads-per-job`、`--omp-nthreads`、`--slurm-mem-gb` 调整，也可以直接用自然语言让 agent 调整运行资源。

## 风险项 / 阻断项检查

Agent 在对数据集和环境进行审查之后，会将检查报告反馈给用户，其中包含风险项（warnings，有可能会影响预处理，但短期内不致命，比如预估产物大小接近所在磁盘存储空间上下）和阻断项（blockers，该项不通过会导致预处理完全无法进行，比如缺少 FreeSurfer license）。

README 只列常见风险。完整的风险 / 阻断项列表（中文）：[issue-codes.zh.md](docs/issue-codes.zh.md)。

| 风险 | agent 会怎么说 |
| --- | --- |
| 被试缺少 T1w 或 BOLD | 标出不能跑的被试或 session。如果还有其他被试能跑，不会直接否定整个数据集。 |
| DataLad 或 git-annex 文件没下载 | 提醒你先把真实文件取回。agent 默认不会擅自对整个数据集执行 `datalad get`。 |
| FreeSurfer license 找不到 | 这会阻止 fMRIPrep 运行。需要提供运行机器能读到的 `license.txt`。 |
| fMRIPrep 或 XCP-D 镜像缺失 | agent 会说明缺哪个镜像。你确认后，它可以帮你准备。 |
| TemplateFlow 缺失或不完整 | agent 会说明缺哪些模板；你确认后，它可以下载并准备 TemplateFlow，再重新检查。 |
| 输出、工作或日志目录不可写 | 需要换目录或修复权限，否则不会继续运行。 |
| 磁盘空间接近上限 | agent 会估算输出和临时文件大小。估算不一定精确，尤其是 FreeSurfer 和不同输出空间会造成差异。 |
| 镜像拉到 C 盘 | 单个镜像加上缓存可能接近 10 GB，加上 TemplateFlow 的模板文件，容易把C盘撑爆。 |
| 输出目录在 exFAT 磁盘 | FreeSurfer 常需要创建符号链接，exFAT 上可能失败。建议改到 NTFS 或 Linux 文件系统。相关案例见 Neurostars: https://neurostars.org/t/symlink-permission-and-fmriprep-wsl/26202 |
| 远程 Docker 加 Slurm | 不推荐。服务器排队系统的计算节点不一定能访问登录节点上的 Docker 服务。远程批量运行更推荐 Apptainer 或 Singularity。 |
| 远程本地运行 | agent 会提醒任务跑在当前 SSH 节点上，而不是排队系统里。确认这个节点允许跑计算任务后再继续。 |

## 模板和 FreeSurfer

fMRIPrep 需要 TemplateFlow；XCP-D 通常不强制，但某些配置/容器运行可能触发模板访问，因此建议预检。在容器中临时联网下载模板容易失败，所以这里让 agent 在用户确认后先到目标环境下载并准备 TemplateFlow，再把它交给容器使用。这样能更早发现缺文件、没权限、网络慢或工具不可用的问题。

配置里的 `templateflow-tool-bins` 和 CLI 里的 `--templateflow-tool-bin <bin-dir>` 是命名遗留：它的实际含义是“包含 TemplateFlow 检查所需命令的目录”。Linux、WSL 和远程 Linux 上通常是 conda 环境的 `bin` 目录；Windows 原生环境通常没有这个 `bin`，不要为了匹配名字手动追加 `bin`。Windows 侧传包含 `datalad.cmd`、`git.cmd`、`git-annex.cmd` 的父目录即可，例如 conda 环境的 `Scripts` 目录或 Git 的 `cmd` 目录。

fMRIPrep 同时跑多个被试时，还可能同时初始化 FreeSurfer 的 `fsaverage` 目录。类似问题在 fMRIPrep issue 里出现过：https://github.com/nipreps/fmriprep/issues/3492

本项目会在真正提交被试任务前做一次短的 FreeSurfer 预热，把需要的 `fsaverage` 资源放到 `fmriprep/sourcedata/freesurfer`。这个预热只保护共享初始化阶段，不会让长时间运行的每个被试互相等待。使用 `--fs-no-reconall` 或没有 fMRIPrep 被试任务时会跳过这一步。XCP-D 没有这个 FreeSurfer 并发问题。

## 进度监测

`$fmri-followup` 只做运行后的检查，不提交、不重跑、不准备环境。它会优先读取保存的运行记录，然后查看 Slurm 任务、进程、stdout/stderr、崩溃记录和输出文件。

它会回答这些问题：

- 任务还在排队、运行、已完成，还是看不到了。
- 任务编号、进程号、日志路径在哪里。
- stdout 和 stderr 末尾有没有明显错误。
- 是否出现新的 crash 文件。
- fMRIPrep 或 XCP-D 输出是否已经到达可检查状态。
- 下一步应该等、看某个日志、重新检查，还是在 fMRIPrep 输出有效后继续 XCP-D。

每次检查、准备、运行和监测都会追加到同一个 `<output-root>/_artifacts/harness-trace.md`。下一次对话里的 agent 会先读这个文件，不需要从头猜之前发生了什么。

## Skills的具体结构

<details>
<summary>展开 skills、references 和 Python 文件说明</summary>

```text
fmriprep-skills/
  README.md
  README.zh.md
  LICENSE
  pyproject.toml
  MANIFEST.in
  config.fmriprep.example.yaml
  config.xcpd.example.yaml
  docs/
  skills/
  src/
```

顶层文件：

- `README.md`：面向用户的英文说明。
- `README.zh.md`：面向用户的中文说明。
- `LICENSE`：MIT license。
- `pyproject.toml`：Python 包配置，包名为 `fmri-proc-tools`。
- `MANIFEST.in`：打包时包含资源文件。
- `config.fmriprep.example.yaml`：fMRIPrep 配置示例。
- `config.xcpd.example.yaml`：XCP-D 配置示例。

`docs/`：

- `issue-codes.md`：问题代码语言索引。
- `issue-codes.zh.md`：中文问题代码说明。
- `issue-codes.en.md`：英文问题代码说明。

`skills/fmri-process/`：

- `SKILL.md`：主入口。判断用户想检查、准备、运行 fMRIPrep，还是从 fMRIPrep 输出继续做 XCP-D。

`skills/fmri-process/references/common/`：

- `append-harness-trace.py`：追加写入 `harness-trace.md` 的小脚本。
- `arguments.md`：fMRIPrep 和 XCP-D 共用参数说明。
- `audit-report.md`：检查报告的输出格式和用户确认边界。
- `cli.md`：本地 Python CLI 调用约定。
- `config.md`：配置文件读取和字段边界。
- `config.fmriprep.example.yaml`：fMRIPrep 配置示例副本。
- `config.xcpd.example.yaml`：XCP-D 配置示例副本。
- `execution-report.md`：提交运行后的报告格式。
- `harness-trace.md`：每个数据集一份进度记录的路径、写入和压缩规则。
- `path-preflight.md`：路径预检查规则。
- `path-preflight-unresolved.md`：路径或必要输入无法确定时的暂停规则。
- `prepare-image.md`：容器镜像准备规则。
- `prepare-runtime.md`：运行环境准备入口。
- `prepare-templateflow.md`：TemplateFlow 准备规则。
- `saved-execution.md`：从保存的检查或运行记录继续时的规则。

`skills/fmri-process/references/fmriprep/`：

- `route.md`：fMRIPrep 主流程。
- `dataset-audit.md`：BIDS 数据和被试可运行性检查。
- `runtime-audit.md`：fMRIPrep 运行环境检查。
- `workflow-gates.md`：检查、准备、运行之间的暂停和确认规则。
- `fmriprep-args.md`：fMRIPrep 参数说明。
- `custom-args.md`：fMRIPrep 额外参数的允许范围和风险说明。
- `saved-continuation.md`：继续旧 fMRIPrep 检查记录的规则。
- `saved-exec.md`：从保存的 fMRIPrep 运行记录再次提交的规则。

`skills/fmri-process/references/xcpd/`：

- `route.md`：XCP-D 主流程。
- `xcpd-audit.md`：XCP-D 前检查，包括 fMRIPrep 输出是否完整。
- `run-xcpd.md`：提交 XCP-D 运行的规则。
- `xcpd-args.md`：XCP-D 参数说明。
- `custom-args.md`：XCP-D 额外参数的允许范围和风险说明。
- `artifacts.md`：XCP-D 检查和运行记录的保存格式。

`skills/fmri-followup/`：

- `SKILL.md`：运行后检查入口，只读查看进度、日志、崩溃记录和输出。
- `references/run-inspection.md`：选择要检查的运行记录。
- `references/run-inspection-fmriprep.md`：fMRIPrep 运行后的检查规则。
- `references/run-inspection-xcpd.md`：XCP-D 运行后的检查规则。

`src/fmri_process/`：

- `__init__.py`：包初始化。
- `cli.py`：公开命令入口，包括 `process`、`dataset-audit`、`runtime-audit`、`xcpd-audit`、`run-fmriprep`、`run-xcpd`、`run-status`、`path-probe`。
- `request_config.py`：把命令行和配置文件值整理成统一请求对象。
- `execution_flow.py`：保存检查记录、继续旧记录、提交运行。
- `xcpd_context.py`：从 fMRIPrep 检查结果中提取 XCP-D 可复用的信息。

`src/fmri_core/`：

- `__init__.py`：包初始化。
- `audit.py`：组合数据检查和运行环境检查。
- `dataset_audit.py`：检查 BIDS 数据、被试、T1w、BOLD、DataLad/git-annex 内容和 fMRIPrep 输出。
- `disk.py`：磁盘容量和文件系统类型检查。
- `image_audit.py`：容器镜像检查。
- `image_metadata.py`：镜像元数据读取。
- `issue_codes.py`：问题代码加载和格式化。
- `models.py`：共享数据结构。
- `monitor.py`：运行后状态、日志、崩溃记录和输出检查。
- `path_probe.py`：路径预检查。
- `pipelines.py`：生成 fMRIPrep 和 XCP-D 容器命令，以及 FreeSurfer 预热步骤。
- `run.py`：提交 Slurm、本地运行和远程本地运行。
- `runtime_audit.py`：检查容器软件、镜像、license、TemplateFlow、写权限、资源和存储风险。
- `runtime_probe.py`：探测当前机器或远程机器的运行条件。
- `runtime_proofs.py`：保存运行环境准备证据。
- `shell.py`：本地和远程 shell 命令封装。
- `storage_check.py`：估算输出文件和临时文件大小。
- `templateflow_audit.py`：检查 TemplateFlow 文件是否准备好。
- `resources/issue_catalog.json`：审查的风险项和阻断项。
- `resources/storage_check_inventory.json`：输出体积估算用的文件清单。

</details>

## 许可证

MIT
