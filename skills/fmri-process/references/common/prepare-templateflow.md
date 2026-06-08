# Prepare TemplateFlow

Read this only for `prepare_requirements` entries with `kind: templateflow`.

Use the TemplateFlow paths and tool bins already selected by path preflight and
saved in the current runtime audit. Do not infer command availability from
unstated shell state or site notes.

## Command Proof

The commands used to prepare TemplateFlow are `datalad`, `git`, and
`git-annex`. Command availability is proven before `path-probe` by the
agent-side path-preflight check.
Manual prepare can run bounded version commands before materialization as a
human-readable sanity check, but those checks do not update audit state. Use a
60-second cap for `datalad --version` and `git annex version`, especially on
remote hosts, because the first invocation may pay conda/env cold-start cost.

If the user names a conda environment or other env, find its target `bin`
directory during path preflight. Pass only that concrete bin directory to
workflow CLI commands as `--templateflow-tool-bin <bin-dir>`.
If default target `$PATH` lacks working DataLad or git-annex commands,
path preflight may use a bounded candidate-bin search over user-named envs,
`conda env list` entries, and site-known env roots. It must not search `/`, broad `$HOME`, datasets, or remote filesystems recursively.
If the bounded search cannot find one concrete `bin` directory that runs
`datalad`, `git`, and `git-annex`, pause and ask the user for a working env or
`bin` directory before audit, prepare-probe, or execution.

There is no separate `--git-annex-bin` or `--datalad-bin` contract.
Do not invent separate `--datalad-bin` or `--git-annex-bin` flags.

Do not infer site-private conda paths or module names unless the user names
the environment. Keep proxy variables in the explicit shell setup, not in the
default command path.

If TemplateFlow home or download storage is on exFAT, warn the user before
materialization. TemplateFlow pulls can be very slow on exFAT because DataLad
and git-annex create many small files and links. In that warning, prefer native
Linux storage or another filesystem with reliable symbolic links.

## Default POSIX Shell Commands

Use these only on POSIX shells when the latest check shows the default shell
commands are healthy. Do not copy these bash forms directly into native Windows
PowerShell or CMD.

```bash
timeout 60 datalad --version
timeout 10 git --version
timeout 60 git annex version
datalad install -s ///templateflow "${templateflow_home}"
cd "${templateflow_home}" && datalad get -J8 "tpl-${template_name}"
```

Use `runtime-audit.required_templateflow_templates` as the selected template
list. Repeat the `datalad get` command for every selected required template. If
DataLad created the clone but content is missing, run
`git annex get -J8 "tpl-${template_name}"` from `"${templateflow_home}"`.
Retry the same DataLad or git-annex materialization command for the same
template at most twice. If it still fails after two retries, stop and report
stderr or log evidence. Do not replace the failed command with broad `find`,
broad `rg`, or manual directory sweeps.

## Explicit Bin Directory POSIX Commands

Use these on POSIX shells when the latest check reports
`toolchain.source == "explicit_tool_bin"` or the user supplies a TemplateFlow
bin directory. Do not copy `PATH="${bin_dir}:$PATH"` or
`cd "${templateflow_home}" && ...` into native Windows PowerShell or CMD.

```bash
PATH="${bin_dir}:$PATH" timeout 60 datalad --version
PATH="${bin_dir}:$PATH" timeout 10 git --version
PATH="${bin_dir}:$PATH" timeout 60 git annex version
PATH="${bin_dir}:$PATH" datalad install -s ///templateflow "${templateflow_home}"
cd "${templateflow_home}" && PATH="${bin_dir}:$PATH" datalad get -J8 "tpl-${template_name}"
cd "${templateflow_home}" && PATH="${bin_dir}:$PATH" git annex get -J8 "tpl-${template_name}"
```

Do not forward `--templateflow-tool-bin` to `prepare-probe`; that command uses
the saved runtime audit values. Use the same selected bin directory for the
matching follow-up runtime audit. Do not mix default-shell audit evidence with
explicit-bin manual commands unless the follow-up audit uses the same explicit
bin.

## Native Windows Command Shape

Use native Windows command syntax only when the selected target is native
Windows and the latest check proves the Windows commands are healthy.

PowerShell examples:

```powershell
$env:PATH = "${bin_dir};$env:PATH"
Start-Job { datalad --version } | Wait-Job -Timeout 60 | Receive-Job
Start-Job { git --version } | Wait-Job -Timeout 10 | Receive-Job
Start-Job { git annex version } | Wait-Job -Timeout 60 | Receive-Job
datalad install -s ///templateflow "${templateflow_home}"
Push-Location "${templateflow_home}"; datalad get -J8 "tpl-${template_name}"; Pop-Location
Push-Location "${templateflow_home}"; git annex get -J8 "tpl-${template_name}"; Pop-Location
```

CMD examples:

```cmd
set "PATH=${bin_dir};%PATH%"
datalad --version
git --version
git annex version
datalad install -s ///templateflow "${templateflow_home}"
pushd "${templateflow_home}" && datalad get -J8 "tpl-${template_name}" & popd
pushd "${templateflow_home}" && git annex get -J8 "tpl-${template_name}" & popd
```

Native Windows `cmd.exe` lacks a built-in timeout wrapper equivalent to POSIX
`timeout 60 <command>` for foreground commands. If bounded execution is needed,
prefer PowerShell `Start-Job ... Wait-Job -Timeout ...` or run the CLI from WSL
with POSIX paths.

## Unverified Warning

If TemplateFlow content exists but command proof is unavailable or inconclusive,
the audit may report warning code `templateflow_unverified`. This is not a
prepare requirement. Do not run manual directory sweeps to clear it.

Prefer a working `--templateflow-tool-bin <bin-dir>` selected during path
preflight, then rerun the matching runtime audit against the same saved inputs.
If no
DataLad/git-annex proof can be produced, report the warning plainly:
fMRIPrep/XCP-D may later fail if TemplateFlow files are absent, unreadable, or
try to download in a read-only or no-network container.

There is no alternate TemplateFlow proof mode.

## Python Client Recovery

Python TemplateFlow client is not a standard route. Treat direct S3 recovery as
a last resort only when DataLad and git-annex remain unavailable under the
verified command PATH and the user accepts environment-specific recovery.

Before Python client recovery, handle unresolved or failed DataLad/git-annex
preflight first, then rerun the matching runtime audit.
If direct S3 recovery fails because site networking, S3 access, or partial
cache state is unreliable, prefer DataLad/archive recovery and show:
https://www.templateflow.org/usage/archive/.

If Python TemplateFlow recovery fails twice for the same template/source in the
current request, stop. Do not keep retrying S3 downloads.

Deterministic config, path, or artifact errors are not retryable. Stop and
report the concrete invalid input or artifact path.
