# fMRIPrep Skills: Help Beginners Use fMRIPrep Easily

Languages: English | [中文](README.zh.md)

> [!IMPORTANT]
> **News (2026-06-10): XCP-D default and harness trace update.** The current default XCP-D image is `pennlinc/xcp_d:26.1.0`. Native Windows XCP-D runs can still fail more easily; use WSL, Linux, or a server when available. Do not switch back to `26.0.2` unless you specifically need that version, because older runs may fail during visual report generation; see the [Neurostars report](https://neurostars.org/t/xcp-d-26-0-2-fails-during-brainsprite-plot-slices-t1-t2/36172). Harness trace monitoring now keeps only the first and latest two `run-status` entries per target, so repeated status checks do not crowd out useful context.

This project provides two skills: `$fmri-process` and `$fmri-followup`. The core goal is **to help beginners who are processing a BIDS dataset for the first time avoid common mistakes and get a successful run with fewer retries**.

What can these skills do? **Tell the agent, in one sentence**, which dataset to process. The agent checks whether the dataset is complete, looks at your local computer or server environment, decides which image file to download and how to pull it, chooses a run strategy for that environment, and can later tell you which stage each subject has reached.

You do not need to understand the code or memorize long fMRIPrep commands. More importantly, you do not need to work through these decisions and risks yourself:

- which image file to download and how to pull it
- whether derivatives and work files may fill the disk
- whether this run may overwhelm your computer

The agent surfaces these risks during the first audit and pauses before the real run.

These skills implement a **lightweight harness** around auditing, runtime preparation, preprocessing execution, progress monitoring, trace logging, and failure follow-up. The agent handles the operational work and checks paths, data, images, TemplateFlow, licenses, storage, scheduler settings, and other risks before launching a long-running job.

The skills are designed for agent environments such as Codex, Claude, DeepSeek, MiMo, and similar tools.

## Workflow

By default, a generic processing request first audits the dataset and runtime, then pauses and reports the result before running preprocessing. The full flow is:

```text
User natural-language request
↓
Path preflight / fill required inputs
↓
Create or read harness-trace.md
↓
Dataset audit: check BIDS data, runnable subjects, and estimated output size
↓
Runtime audit: check license, image, container, TemplateFlow, storage, Slurm or server settings
↓
Generate audit report, pause by default, and show the report to the user
↓
If the runtime is not ready: user confirms runtime preparation
↓
Prepare runtime (prepare-runtime / prepare-probe): prepare images and TemplateFlow
↓
Recheck
↓
User confirms the run
↓
Submit run (run-fmriprep / run-xcpd)
↓
$fmri-followup monitors progress, logs, crash files, and output completeness
```

There are two default pause points:

- After the audit. The agent reports what can run, what cannot run, and what should be handled first.
- After runtime preparation. Preparing an image or TemplateFlow does not authorize starting the run.

The agent submits fMRIPrep or XCP-D only when you explicitly say something like "run after audit passes" or "run now".

If you want to run XCP-D, the agent first checks whether the fMRIPrep outputs are complete. For example, it checks whether each subject has the required files, whether the outputs match the requested XCP-D mode, and whether the output directory is readable. Finishing fMRIPrep does not automatically start XCP-D; you must explicitly ask for XCP-D.

## Quick Start

### Install Dependencies

```bash
if [ -d fmriprep-skills/.git ]; then
  cd fmriprep-skills
  git pull --ff-only
else
  git clone https://github.com/Ascom11/fmriprep-skills.git
  cd fmriprep-skills
fi
python -m pip install -e .
python -m pip show fmri-proc-tools
```

### Copy Skills

Run these commands from the `fmriprep-skills` repository root. Remove the old
skill directories first so deleted or renamed files do not remain installed.

Linux, macOS, WSL, or remote shell for Codex:

```bash
rm -rf ~/.codex/skills/fmri-process ~/.codex/skills/fmri-followup
mkdir -p ~/.codex/skills/fmri-process ~/.codex/skills/fmri-followup
cp -a skills/fmri-process/. ~/.codex/skills/fmri-process/
cp -a skills/fmri-followup/. ~/.codex/skills/fmri-followup/
```

Linux, macOS, WSL, or remote shell for Claude or other Claude Code based agents:

```bash
rm -rf ~/.claude/skills/fmri-process ~/.claude/skills/fmri-followup
mkdir -p ~/.claude/skills/fmri-process ~/.claude/skills/fmri-followup
cp -a skills/fmri-process/. ~/.claude/skills/fmri-process/
cp -a skills/fmri-followup/. ~/.claude/skills/fmri-followup/
```

Windows PowerShell for Codex:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.codex\skills\fmri-process", "$env:USERPROFILE\.codex\skills\fmri-followup" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills\fmri-process", "$env:USERPROFILE\.codex\skills\fmri-followup" | Out-Null
Copy-Item -Recurse -Force .\skills\fmri-process\* "$env:USERPROFILE\.codex\skills\fmri-process\"
Copy-Item -Recurse -Force .\skills\fmri-followup\* "$env:USERPROFILE\.codex\skills\fmri-followup\"
```

Windows PowerShell for Claude or other Claude Code based agents:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\skills\fmri-process", "$env:USERPROFILE\.claude\skills\fmri-followup" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\fmri-process", "$env:USERPROFILE\.claude\skills\fmri-followup" | Out-Null
Copy-Item -Recurse -Force .\skills\fmri-process\* "$env:USERPROFILE\.claude\skills\fmri-process\"
Copy-Item -Recurse -Force .\skills\fmri-followup\* "$env:USERPROFILE\.claude\skills\fmri-followup\"
```

### Runtime Prerequisites

Other runtime prerequisites:

- Container software: Apptainer or Singularity is recommended on Linux, WSL, and servers. Native Windows environments use Docker.
- `datalad` and `git-annex`: used to confirm that TemplateFlow template files have been fully downloaded. You can ask the agent to install them. We recommend installing them in the same conda environment you use for these skills.
- FreeSurfer license: fMRIPrep needs this for FreeSurfer. Register at https://surfer.nmr.mgh.harvard.edu/registration.html. Note: fMRIPrep needs a FreeSurfer license even if you do not run surface reconstruction. See https://github.com/nipreps/fmriprep/issues/1747.

Images and templates can be downloaded by the agent after the runtime audit and after you authorize preparation. XCP-D usually does not require TemplateFlow, but some configuration/container runs may trigger template access, so preflight is recommended. This avoids the container trying to download templates during the run, which is more likely to fail.

### User Prompts

Full version:

```text
$fmri-process help me process /path/to/bids_dataset, use conda_env as the conda environment, the image is in /path/to/images, TemplateFlow is in /path/to/templateflow, and run 10 subjects at the same time
```

Full version (needs preparation):

```text
$fmri-process help me process /path/to/bids_dataset, use conda_env as the conda environment, and help me prepare the environment
```

Simplest use:

```text
$fmri-process help me process /path/to/bids_dataset
```

Select subjects with a wildcard:

```text
$fmri-process help me process sub-00[1-5] in /path/to/bids_dataset
```

Specify output location:

```text
$fmri-process help me process sub-00[1-5] in /path/to/bids_dataset, and put outputs and work files under the matching directory on drive E
```

Run on a remote server:

```text
$fmri-process ssh to remote and process sub-00[1-5] in /path/to/bids_dataset
```

Remote server has no internet access, so prepare locally first and upload later:

```text
$fmri-process ssh to remote and process sub-00[1-5] in /path/to/bids_dataset. The server has no internet, so fetch the needed assets locally first and then upload them
```

Continue from fMRIPrep outputs to XCP-D:

```text
$fmri-process run XCP-D for all subjects under /path/to/derivatives/fmriprep
```

Check a submitted job:

```text
$fmri-followup check progress
```

### Using Config Files

If you already have a set of fixed paths or parameters from an old container command, you can write them into a config file. The agent reads the YAML first, then translates it into explicit CLI arguments. Config files are an agent-side translation aid; the underlying CLI does not accept `--config`. After parsing the file, the agent still proceeds with explicit paths and arguments.

The repository includes two examples:

- [config.fmriprep.example.yaml](config.fmriprep.example.yaml): fMRIPrep inputs, outputs, images, subjects, and resource parameters.
- [config.xcpd.example.yaml](config.xcpd.example.yaml): fMRIPrep derivatives, XCP-D image, subjects, and filtering parameters.

The config file uses three sections: `shared`, `fmriprep`, and `xcpd`. For a specific request, fill only the sections needed for the current target. For example, when running only fMRIPrep, fill `shared` and `fmriprep`; do not also fill a complete `xcpd` section.

You can also paste an old container command and ask the agent to translate it instead of rewriting it yourself. For example:

```text
$fmri-process Translate this old fMRIPrep command into this skill's CLI request, then audit first:

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

The agent should extract host-side paths and options, then build an equivalent explicit request such as:

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

This translation does not skip the harness. The agent still audits the dataset and runtime, reports blockers and warnings, and pauses before execution unless you explicitly authorize running.

Common requests and default behavior:

| User request | Default behavior |
| --- | --- |
| `process this dataset` | Audit the dataset and runtime first, then pause with a report. |
| `audit the dataset` | Check only BIDS files, T1w, BOLD, subject list, and whether files are actually materialized locally. |
| `check the runtime` | Check only container software, image, license, TemplateFlow, disk, write permissions, and server settings. |
| `prepare the runtime` | Prepare images or TemplateFlow after your confirmation. Does not start the run automatically. |
| `run directly if audit passes` | Run only after there are no hard blockers; preparation still pauses first when needed. |
| `continue the previous audit` | Read saved audit records and report whether continuation is possible. Does not treat "continue" as run authorization. |
| `run XCP-D` | Check fMRIPrep outputs and the XCP-D runtime first, then pause with a report. |
| `check progress` | Route to `$fmri-followup` for read-only progress, log, and crash-file inspection. |

## What It Will Not Do Automatically

The following actions are not performed by default unless you explicitly request or confirm them:

- Does not automatically apply for a FreeSurfer license.
- Does not materialize DataLad/git-annex data content by default unless you explicitly request it.
- Does not delete old derivatives / work directories by default.
- Does not automatically continue from fMRIPrep to XCP-D; you must explicitly request XCP-D.
- Does not treat warnings as blockers, but reports them before execution.
- Does not guarantee that high-motion data will survive XCP-D scrubbing; for example, `No runs survived high-motion outlier scrubbing` is a data/parameter-level issue.

## Storage Layout

If no output location is specified, the default output structure is:

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

After specifying `--output-root`, downloaded images and templates are placed by default under `_downloads/` in the parent directory of `<output-root>`. The final paths in the agent report are authoritative.

The `_artifacts/` directory contains audit records and run records. `harness-trace.md` is a natural-language progress record. Each dataset reuses one `harness-trace.md`. In a new conversation or after context compaction, the agent reads this file first to recover prior progress. When the file exceeds 200 KiB, the agent automatically compacts it.

## Runtime Environments

The skills include checks and compatibility handling for several runtime environments:

- Linux
- WSL
- Native Windows with Docker
- Remote Linux or HPC
- Slurm, recommended for batch runs on servers

Native Windows can also connect to a remote server over SSH, but it is not the preferred setup. Mixing PowerShell, Git Bash/MSYS, and remote Linux paths may trigger path rewriting or quoting issues. The agent can usually handle them, but WSL is recommended: https://learn.microsoft.com/en-us/windows/wsl/install.

Container software is selected automatically: Apptainer is preferred when available, Singularity is used next, and native Windows environments use Docker.

## Defaults

Default fMRIPrep image version:

```text
docker://nipreps/fmriprep:25.2.5
```

Default XCP-D image version:

```text
docker://pennlinc/xcp_d:26.1.0
```

Default fMRIPrep output spaces:

```text
MNI152NLin2009cAsym:res-2
MNI152NLin6Asym:res-2
```

Default CIFTI:

```text
--cifti-output 91k
```

FreeSurfer runs by default. Only explicit `--fs-no-reconall` skips FreeSurfer reconstruction.

The number of threads per subject is decided by the runtime audit and is usually conservative:

- Slurm mode runs up to 4 subjects at the same time by default, with `--nthreads 4 --omp-nthreads 4` per subject. You can set the subject concurrency yourself.
- A local worker pool on Linux or a remote server runs up to `min(4, CPU / threads_per_subject)` subjects at the same time by default. Here, worker pool means multiple subject jobs started on the same machine.
- Local machine runs default to 1 subject at a time.
- You can tune resources with `--max-jobs`, `--nthreads-per-job`, `--omp-nthreads`, and `--slurm-mem-gb`, or ask the agent in natural language.

## Warning / Blocker Checks

After auditing the dataset and runtime, the agent reports issues to the user. The report contains warnings, which may affect preprocessing but are not immediately fatal, and blockers, which prevent preprocessing from running. For example, low disk margin is a warning, while a missing FreeSurfer license is a blocker.

This README lists common risks only. The complete issue catalog is available in English: [issue-codes.en.md](docs/issue-codes.en.md).

| Risk | What the agent reports |
| --- | --- |
| Subject is missing T1w or BOLD | Marks the subject or session as unrunnable. If other subjects can run, the whole dataset is not rejected. |
| DataLad or git-annex files are not downloaded | Tells you to materialize the real files first. The agent does not run `datalad get` over the whole dataset by default. |
| FreeSurfer license is missing | This blocks fMRIPrep. You must provide a `license.txt` readable by the runtime machine. |
| fMRIPrep or XCP-D image is missing | Reports which image is missing. After your confirmation, the agent can prepare it. |
| TemplateFlow is missing or incomplete | Reports which templates are missing. After your confirmation, the agent can download and prepare TemplateFlow, then recheck. |
| Output, work, or log directory is not writable | Requires changing directories or fixing permissions before the run can continue. |
| Disk space is close to the limit | Estimates output and temporary file size. The estimate is not exact, especially with FreeSurfer and different output spaces. |
| Image is pulled to the C drive | One image plus cache may approach 10 GB, and TemplateFlow adds more files, which can fill the C drive. |
| Output directory is on exFAT | FreeSurfer often needs symlinks, which may fail on exFAT. Prefer NTFS or a Linux filesystem. See the related Neurostars case: https://neurostars.org/t/symlink-permission-and-fmriprep-wsl/26202. |
| Remote Docker plus Slurm | Not recommended. Compute nodes in a scheduler may not be able to access the Docker service on the login node. Apptainer or Singularity is preferred for remote batch runs. |
| Remote local run | Warns that the task will run on the current SSH node, not inside the queueing system. Continue only if that node is allowed to run compute jobs. |

## TemplateFlow and FreeSurfer

fMRIPrep needs TemplateFlow. XCP-D usually does not require it, but some configuration/container runs may trigger template access, so preflight is recommended. Downloading templates from inside the container during a run is fragile. These skills let the agent download and prepare TemplateFlow in the target environment after your confirmation, then pass it to the container. This catches missing files, permission issues, slow networks, or missing tools earlier.

The config field `templateflow-tool-bins` and the CLI option `--templateflow-tool-bin <bin-dir>` are legacy names. The actual meaning is "the directory containing the commands needed for TemplateFlow checks". On Linux, WSL, and remote Linux, this is usually a conda environment's `bin` directory. Native Windows environments usually do not have this `bin` directory, so do not append `bin` just to match the option name. On Windows, pass the parent directory that contains `datalad.cmd`, `git.cmd`, and `git-annex.cmd`, such as a conda `Scripts` directory or Git's `cmd` directory.

When fMRIPrep runs multiple subjects concurrently, several jobs may initialize the FreeSurfer `fsaverage` directory at the same time. Similar issues have appeared in fMRIPrep: https://github.com/nipreps/fmriprep/issues/3492.

Before submitting subject jobs, this project performs a short FreeSurfer warm-up that places the needed `fsaverage` resources under `fmriprep/sourcedata/freesurfer`. This warm-up only protects the shared initialization stage; it does not serialize every long-running subject job. It is skipped when `--fs-no-reconall` is used or when there are no fMRIPrep subject jobs. XCP-D does not have this FreeSurfer concurrency issue.

## Progress Monitoring

`$fmri-followup` is only for post-run inspection. It does not submit jobs, rerun jobs, or prepare the runtime. It prefers saved run records, then checks Slurm jobs, processes, stdout/stderr, crash files, and output files.

It answers questions such as:

- Whether the job is queued, running, finished, or no longer visible.
- Where the job ID, process ID, and log paths are.
- Whether stdout or stderr tails contain obvious errors.
- Whether new crash files appeared.
- Whether fMRIPrep or XCP-D outputs have reached a checkable state.
- Whether the next step should be waiting, reading a specific log, rechecking, or continuing to XCP-D after fMRIPrep outputs are valid.

Each audit, preparation, run, and monitoring step appends to the same `<output-root>/_artifacts/harness-trace.md`. In the next conversation, the agent reads this file first instead of guessing what happened earlier.

## Skill Layout

<details>
<summary>Expand skills, references, and Python files</summary>

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

Top-level files:

- `README.md`: user-facing English instructions.
- `README.zh.md`: user-facing Chinese instructions.
- `LICENSE`: MIT license.
- `pyproject.toml`: Python package configuration. The package name is `fmri-proc-tools`.
- `MANIFEST.in`: includes resource files in the package.
- `config.fmriprep.example.yaml`: fMRIPrep config example.
- `config.xcpd.example.yaml`: XCP-D config example.

`docs/`:

- `issue-codes.md`: issue-code language index.
- `issue-codes.zh.md`: Chinese issue-code documentation.
- `issue-codes.en.md`: English issue-code documentation.

`skills/fmri-process/`:

- `SKILL.md`: main entry point. Decides whether the user wants to audit, prepare, run fMRIPrep, or continue from fMRIPrep outputs to XCP-D.

`skills/fmri-process/references/common/`:

- `append-harness-trace.py`: small script for appending to `harness-trace.md`.
- `arguments.md`: shared fMRIPrep and XCP-D argument reference.
- `audit-report.md`: audit report format and user confirmation boundary.
- `cli.md`: local Python CLI invocation contract.
- `config.md`: config-file reading and field boundaries.
- `config.fmriprep.example.yaml`: copy of the fMRIPrep config example.
- `config.xcpd.example.yaml`: copy of the XCP-D config example.
- `execution-report.md`: report format after submitting a run.
- `harness-trace.md`: per-dataset progress trace path, writing, and compaction rules.
- `path-preflight.md`: path preflight rules.
- `path-preflight-unresolved.md`: pause rules when paths or required inputs cannot be resolved.
- `prepare-image.md`: container image preparation rules.
- `prepare-runtime.md`: runtime preparation entry point.
- `prepare-templateflow.md`: TemplateFlow preparation rules.
- `saved-execution.md`: rules for continuing from saved audit or run records.

`skills/fmri-process/references/fmriprep/`:

- `route.md`: fMRIPrep main flow.
- `dataset-audit.md`: BIDS data and subject runnability audit.
- `runtime-audit.md`: fMRIPrep runtime audit.
- `workflow-gates.md`: pause and confirmation rules between audit, preparation, and run.
- `fmriprep-args.md`: fMRIPrep argument reference.
- `custom-args.md`: allowed scope and risks for extra fMRIPrep arguments.
- `saved-continuation.md`: rules for continuing old fMRIPrep audit records.
- `saved-exec.md`: rules for resubmitting from saved fMRIPrep run records.

`skills/fmri-process/references/xcpd/`:

- `route.md`: XCP-D main flow.
- `xcpd-audit.md`: XCP-D preflight, including fMRIPrep output completeness.
- `run-xcpd.md`: XCP-D run submission rules.
- `xcpd-args.md`: XCP-D argument reference.
- `custom-args.md`: allowed scope and risks for extra XCP-D arguments.
- `artifacts.md`: saved XCP-D audit and run artifact format.

`skills/fmri-followup/`:

- `SKILL.md`: post-run inspection entry point. Read-only inspection of progress, logs, crash files, and outputs.
- `references/run-inspection.md`: choose which run record to inspect.
- `references/run-inspection-fmriprep.md`: fMRIPrep post-run inspection rules.
- `references/run-inspection-xcpd.md`: XCP-D post-run inspection rules.

`src/fmri_process/`:

- `__init__.py`: package initialization.
- `cli.py`: public command entry point, including `process`, `dataset-audit`, `runtime-audit`, `xcpd-audit`, `run-fmriprep`, `run-xcpd`, `run-status`, and `path-probe`.
- `request_config.py`: normalizes command-line and config-file values into one request object.
- `execution_flow.py`: saves audit records, continues old records, and submits runs.
- `xcpd_context.py`: extracts reusable XCP-D context from fMRIPrep audit results.

`src/fmri_core/`:

- `__init__.py`: package initialization.
- `audit.py`: combines dataset audit and runtime audit.
- `dataset_audit.py`: checks BIDS data, subjects, T1w, BOLD, DataLad/git-annex content, and fMRIPrep outputs.
- `disk.py`: disk capacity and filesystem checks.
- `image_audit.py`: container image checks.
- `image_metadata.py`: image metadata reading.
- `issue_codes.py`: issue-code loading and formatting.
- `models.py`: shared data structures.
- `monitor.py`: post-run status, log, crash-file, and output inspection.
- `path_probe.py`: path preflight.
- `pipelines.py`: builds fMRIPrep and XCP-D container commands and the FreeSurfer warm-up step.
- `run.py`: submits Slurm, local, and remote-local runs.
- `runtime_audit.py`: checks container software, image, license, TemplateFlow, write permissions, resources, and storage risks.
- `runtime_probe.py`: probes local or remote runtime conditions.
- `runtime_proofs.py`: saves runtime-preparation evidence.
- `shell.py`: local and remote shell command wrapper.
- `storage_check.py`: estimates output and temporary file size.
- `templateflow_audit.py`: checks whether TemplateFlow files are ready.
- `resources/issue_catalog.json`: issue catalog used by audits.
- `resources/storage_check_inventory.json`: file inventory used for storage estimation.

</details>

## License

MIT
