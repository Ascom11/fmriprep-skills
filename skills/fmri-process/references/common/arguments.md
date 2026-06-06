# Common Workflow Arguments And Rules

Read this after a route contract selects a CLI action and before assembling
shared command arguments.

This file is the fMRIPrep/XCP-D shared locator, remote, path, runtime proof,
resource, and storage input reference. Pipeline references keep
pipeline-specific flags only.

## Parameter Details

| Argument | Accepted by | Meaning |
| --- | --- | --- |
| `--bids-root <bids-root>` | `process`, `dataset-audit`, `runtime-audit`, `prepare-probe`, `run-fmriprep`, `xcpd-audit`, `run-xcpd`, `run-status` | Target-visible BIDS root. Required for fMRIPrep. Optional context for XCP-D when `--fmriprep-derivatives` is provided. In remote mode this is a remote POSIX path. |
| `--fmriprep-derivatives <path>` | `xcpd-audit`, `run-xcpd` | Target-visible fMRIPrep derivatives root used as XCP-D input. This is the XCP-D dataset input when raw BIDS is not provided. |
| `--output-root <output-root>` | `process`, `dataset-audit`, `runtime-audit`, `prepare-probe`, `run-fmriprep`, `xcpd-audit`, `run-xcpd`, `run-status` | Derivatives and audit archive root. When omitted for fMRIPrep, CLI default is `<bids_root>/derivatives`; for XCP-D with `--fmriprep-derivatives`, it defaults to the derivatives parent. |
| `--remote-host <remote-host>` | `process`, `dataset-audit`, `runtime-audit`, `prepare-probe`, `run-fmriprep`, `xcpd-audit`, `run-xcpd`, `run-status` | Run the CLI locally while probes/execution target the remote filesystem/runtime. |
| `--subject <selector>` | `process`, `dataset-audit`, `xcpd-audit` | Repeatable subject selector. Preserve user scope; do not widen after path correction. |
| `--subject-file <subject-file>` | `process`, `dataset-audit`, `xcpd-audit` | Local CLI-side selector file, even with `--remote-host`. |
| `--session <session>` | `process`, `dataset-audit`, `xcpd-audit` | Session filter. Add only when the user explicitly constrains sessions. |
| `--work-root <work-root>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Work directory for the selected target. Remote mode interprets it on the remote filesystem. |
| `--log-root <log-root>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Log root override. Without it, logs derive under `<output-root>/_artifacts/`. |
| `--download-root <download-root>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Cache root for image and TemplateFlow defaults. |
| `--fs-license <fs-license>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | FreeSurfer license path visible to the selected runtime. |
| `--templateflow-home <templateflow-home>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | target-visible TemplateFlow directory. When omitted, audit derives it from `<download_root>/templateflow`. |
| `--templateflow-tool-bin <bin-dir>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Repeatable target environment `bin` directory for TemplateFlow `datalad`, `git`, and `git-annex` commands. Do not pass this to `path-probe` or `prepare-probe`; `path-probe` only normalizes paths, and `prepare-probe` uses the saved runtime audit values. |
| `--container-runtime <runtime>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Runtime override. Accepted values are `auto`, `apptainer`, `singularity`, and `docker`. |
| `--executor-policy <local\|slurm\|auto>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Execution backend override. Do not use it to bypass audit-reported scheduler or node constraints. |
| `--nthreads-per-job <n>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Per-job thread control recorded in the runtime resource proof. |
| `--omp-nthreads <n>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | OMP thread control recorded in the runtime resource proof. |
| `--slurm-mem-gb <n>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Slurm per-job memory request. Slurm execution renders `#SBATCH --mem=<n>G`. |
| `--max-jobs <n>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Subject concurrency control recorded in the runtime resource proof. |
| `--wsl-vhdx-path <path>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Windows/WSL storage check input. |
| `--windows-host-drive <drive>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Windows/WSL storage check input. |
| `--docker-wsl-storage-path <path>` | `process`, `runtime-audit`, `xcpd-audit`, `run-xcpd` | Windows/WSL storage check input. |

Pipeline image flags are pipeline-specific:

- fMRIPrep uses `--fmriprep-image <image>`.
- XCP-D uses `--xcpd-image <image>`.

Both follow the same rule: registry references are prepared by the selected
prepare route; target-visible image paths are not uploaded or synced by the
flag itself.

## Config Guidance

Config files are not CLI inputs.
If the user gives a YAML config file or asks you to translate an old container
command/script into config, read [config.md](config.md) before `path-probe`;
translate supported values into explicit flags. Do not pass a config file to
any CLI command. Otherwise do not load the config guide.

## Shared Command Rules

Use this file and the selected route reference as the normal source for command
assembly, not command-output discovery. If docs and a CLI payload contradict
each other, stop and report the contradiction.

- Run workflow commands through `python -m fmri_process.cli` with the selected
  local Python.
- Use `normalized_args` from the parent preflight when it ran.
- Keep BIDS root, output root, remote host, runtime paths, and TemplateFlow
  tool-bin choices stable when moving from audit to prepare/probe/execution.
- Carry subjects and sessions only on dataset-owning commands: `process`,
  `dataset-audit`, and `xcpd-audit`.
- No CLI command accepts `--config` or `--remote-config`. Translate config
  values before `path-probe`, then use explicit flags or saved artifacts.
- `prepare-probe` reads saved runtime audit values only.
- Do not run `datalad get` for subject or session data as default audit,
  prepare, direct-run, or saved execution handling.
- Run dataset materialization only when the user explicitly asks for it.

## Remote Host Invocation

`--remote-host <remote-host>` means the CLI still runs locally. The remote host
is the target filesystem/runtime for tool-managed probes, prepare commands, and
execution scripts.

Allowed:

- local `python -m fmri_process.cli ... --remote-host <remote-host>`
- SSH probes performed inside `fmri_core`

Forbidden:

- `ssh <remote-host> 'python -m fmri_process.cli ...'`
- hand-written `ssh` or `find` as normal path discovery
- changing the target environment to recover from local CLI, SSH, sandbox, or
  quoting failures

Remote execution does not require `python3`, `python`, or `fmri_process` on the
remote host. The local Python CLI generates shell scripts for the remote target.
This is not permission to run `python -m fmri_process.cli` remotely.

Do not use hand-written `ssh` or `find` as normal path discovery. Manual remote
probes are allowed only after a CLI probe fails or contradicts other evidence;
debug results must not change route selection, gate decisions, saved execution,
runtime readiness, or direct-run approval.

Documented image, TemplateFlow, or image-upload recovery commands may run in the
remote shell when the selected prepare route chooses remote materialization.
That permission covers target asset preparation only. `process`, `runtime-audit`,
`prepare-probe`, and saved execution remain local CLI boundaries.

## MSYS Path Conversion

On native Windows, prefer PowerShell or CMD. If the Windows CLI runs from
Git Bash/MSYS, prefix each `python -m fmri_process.cli ...` command with
`MSYS_NO_PATHCONV=1` so remote POSIX paths such as `/gpfs/...` are not
rewritten.
Do not add this prefix for PowerShell, CMD, Linux, WSL, or remote POSIX shell
commands. This is the `MSYS_NO_PATHCONV=1` invocation rule.

## Environment Selection Contract

When a local request says "use environment <env>", run install checks and
workflow commands with that environment's Python:

```text
<env-python> -m pip show fmri-proc-tools
<env-python> -m fmri_process.cli <command> ...
```

For remote requests, the CLI still runs with the selected local Python. Do not
translate "use remote environment <env>" into
`ssh <remote-host> 'python -m fmri_process.cli ...'`.

The only current remote environment injection surface is
`--templateflow-tool-bin <bin-dir>` on runtime-capable workflow commands.
Resolve named conda environments to target `bin` directories before invoking
the workflow CLI; later stages use the saved bin value. It affects only
TemplateFlow commands and does not change local CLI Python, `path-probe`, or
container execution. It does not change local CLI Python or container execution.

## Path Defaults

Use CLI payloads for exact resolved defaults. Agent-facing defaults:

- default output root: `<bids_root>/derivatives`
- default XCP-D product directory: `<output-root>/xcp_d`
- default fMRIPrep work root: `<bids_root>.parent/work/work_fmriprep`
- default XCP-D work root: `<output-root>.parent/work/work_xcpd` when `--output-root`
  or `--fmriprep-derivatives` is available
- default fMRIPrep logs: `<output-root>/_artifacts/fmriprep_logs`
- default XCP-D logs: `<output-root>/_artifacts/xcpd_logs`
- default download root: if default output root is `<bids_root>/derivatives`,
  use `<bids_root>.parent/_downloads`; otherwise use
  `<output-root>.parent/_downloads`
- default image root: `<download_root>/images`
- TemplateFlow home: `<download_root>/templateflow`

`--download-root <download-root>` overrides the derived download root.
Saved execution consumes values recorded in selected artifacts. Current-turn
path overrides do not clear saved findings.

## Saved Path Layout

The selected output root contains target-specific audit archives:

```text
<output-root>/
+-- _artifacts/
    +-- fmriprep_audit/
    |   +-- latest.json
    |   +-- runtime-proofs.json
    |   +-- audit_<audit_id>/
    |       +-- dataset-audit.json
    |       +-- runtime-audit.json
    |       +-- dataset-audit-debug.json
    |       +-- submission_<submission_id>/
    |           +-- execution-context.json
    |           +-- submission-result.json
    +-- xcpd_audit/
        +-- latest.json
        +-- runtime-proofs.json
        +-- audit_<audit_id>/
            +-- xcpd-dataset-audit.json
            +-- xcpd-runtime-audit.json
            +-- xcpd-dataset-audit-debug.json
            +-- submission_<submission_id>/
                +-- execution-context.json
                +-- submission-result.json
```

`latest.json` is an index for finding the latest archived audit. It is not an
execution snapshot and must not be passed directly to saved execution.
`runtime-proofs.json` is stored at the selected target bucket root, not inside a
specific `audit_<audit_id>/` directory.

Agents may read compact audit artifacts for report and route selection. Debug
artifacts are CLI-internal / agent-do-not-read:
`dataset-audit-debug.json` and `xcpd-dataset-audit-debug.json`. Saved execution
may validate debug artifacts internally, but agents must not read them directly.

Cache, tmp, per-subject work, and operation log paths are not stable prose
contracts. Read those paths from the CLI payload when needed.

## Remote Execution Modes

`--remote-host` can lead to two execution modes after audit gates pass:

- `remote-slurm`: submit through Slurm on the remote environment.
- `remote-local`: run on the current SSH target node.

Remote-local execution is not a scheduler fallback. Use it only when the SSH
target itself is the node that should run the selected pipeline. Report the
`remote_local_execution_current_node` warning when the payload returns it.
If the user says "run on node <node>", "execute on <node>", or "在 <node>
节点上执行", interpret that as a remote-local request with
`--remote-host <node>` unless they explicitly ask to submit through Slurm from a
login/control host.

Remote Docker with Slurm remains blocked unless the CLI verifies the selected
daemon boundary. Do not infer Slurm compute-node Docker availability from the
SSH target node.

## Subject Concurrency

Runtime audit exposes two execution strategies: `slurm` and `worker_pool`.
`max_jobs=1` is a one-worker `worker_pool`, not a separate serial strategy.

`--max-jobs <n>` limits subject concurrency for local worker-pool,
remote-local worker-pool, and Slurm array execution. Use the effective resource
summary reported by the CLI payload when explaining concurrency.

Explicit resource values are preserved in the resource proof; runtime audit does
not silently clamp them to detected CPU or memory. Local and remote-local
overload risk is reported as warnings. Slurm login-node CPU and memory facts are
not used to reduce requested concurrency. When local or remote-local concurrency
is omitted, the default plan does not create CPU overcommit by itself.
