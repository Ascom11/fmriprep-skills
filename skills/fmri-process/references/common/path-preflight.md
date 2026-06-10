# Path Preflight

Read this file after the install check and selected route contract, before the
first workflow CLI command, when the request contains path-like inputs or
omitted runtime inputs that the tool can probe.

This is a front-loaded input-normalization step. It does not decide dataset
readiness, runtime readiness, prepare approval, or execution approval.

## Fast Path

Agent preflight still probes and corrects path-like inputs before invoking the
workflow command. If the user supplied YAML config, translate it into explicit
path flags before this step. Run `path-probe` once with only explicit values.

Fresh fMRIPrep `dataset-audit` does not require `datalad` or `git-annex`
during path preflight.

For fMRIPrep runtime-capable routes, do the TemplateFlow tool precheck before
`path-probe`:

1. If the user names a conda environment, find that environment's target
   `bin` directory first. Use target-shell facts such as active conda metadata,
   `conda env list`, or site-known env roots. Do not pass the env name itself
   to workflow CLI commands.
2. Check that the selected target shell can run `datalad` and `git-annex`.
   Use bounded checks such as `timeout 60 datalad --version` and
   `timeout 60 sh -c 'git annex version | head -n 1'`; on remote hosts, run them in the remote
   shell where later TemplateFlow materialization will run. Do not use a 10-second cap for the first remote DataLad/git-annex version probe because conda/env cold start can be slow.
3. If default target `$PATH` cannot run both commands, try only a bounded candidate-bin search before pausing.
   Allowed roots are user-named conda/env roots, `conda env list` entries, and site-known env roots for the selected
   target shell. Search only immediate likely `bin` directories such as
   `<env>/bin`. Do not search `/`, broad `$HOME`, project trees, datasets, or remote filesystems recursively.
   Bound the search with a short timeout and a small candidate cap.
4. A fallback candidate is valid only when the same concrete `bin` directory
   can run `timeout 60 datalad --version`, `timeout 10 git --version`, and
   `timeout 60 sh -c 'git annex version | head -n 1'` in the target shell. Use that directory as
   `--templateflow-tool-bin <bin-dir>`.
5. If no default or bounded-search candidate works, pause and ask for a working conda env or concrete `bin` directory.
   Do not continue into audit, prepare-probe, or execution.
6. After the check passes, resolve the command location to a concrete target
   `bin` directory. If default `$PATH` passed, still resolve the working
   commands with target-shell facts such as `command -v datalad` and
   `command -v git-annex`, then derive the concrete `bin` directory.
7. Do not pass that `bin` directory to `path-probe`. Pass it only to the later
   workflow CLI command as `--templateflow-tool-bin <bin-dir>`.

## Policy

Use `--target fmriprep` for fMRIPrep routes and `--target xcpd` for XCP-D
routes. Fresh fMRIPrep `dataset-audit` uses path preflight only for path
normalization; it does not require TemplateFlow command checks.
Pass only the values the user actually provided.
Skip `path-probe` when the current turn reuses an already validated saved artifact path and introduces no new path-like input.
It is not a mandatory repeated cost when current saved artifacts already prove
the exact paths.

## Skeleton Template

Use this short skeleton, then add only values from the argument table that the
user actually supplied:

```bash
python -m fmri_process.cli path-probe \
  --target <fmriprep|xcpd> \
  --bids-root <bids-root> \
  --output-root <output-root>
```

Do not restore a long template with runtime asset, image, TemplateFlow, or
`--require-path` flags.

## Parameter Details

Use the table to assemble one `path-probe` command. Do not paste every row into
one command. Omit unavailable flags and values that do not apply to the selected
route.

| Argument | Use when | Meaning |
| --- | --- | --- |
| `--target fmriprep` | fMRIPrep routes | Select fMRIPrep path categories. |
| `--target xcpd` | XCP-D routes | Select XCP-D path categories. |
| `--bids-root <bids_root>` | user supplied BIDS root | Target-visible BIDS root. In remote mode this is a remote POSIX path. |
| `--user-dataset-path <path>` | user gave a dataset-like path that may need correction | Candidate dataset root, parent directory, or XCP-D fMRIPrep derivatives hint. |
| `--output-root <output_root>` | user supplied output root | Target-visible output root. |
| `--templateflow-home <path>` | user supplied TemplateFlow path or parent | Candidate TemplateFlow directory. |
| `--fs-license <fs_license>` | user supplied FreeSurfer license | Target-visible license path. |
| `--fmriprep-image <image>` | fMRIPrep route with supplied image | fMRIPrep image path or registry reference. |
| `--xcpd-image <image>` | XCP-D route with supplied image | XCP-D image path or registry reference. |
| `--remote-host <remote_host>` | remote request | Probe target-visible remote paths while running the CLI locally. |
| `--require-path <category>` | route cannot continue unless that category resolves | Require only categories valid for the selected target. |

Valid `--require-path` categories depend on `--target`:

| Target | Valid required categories |
| --- | --- |
| `fmriprep` | `dataset`, `output_root`, `templateflow_home`, `fs_license`, `fmriprep_image` |
| `xcpd` | `fmriprep_derivatives`, `output_root` |

For fMRIPrep, require the BIDS dataset with `--require-path dataset`; do not use
`--require-path bids_root`. For XCP-D, treat `bids_root` as optional context
when `fmriprep_derivatives` resolves.

After preflight, the workflow CLI performs the native Windows last-mile
conversion of strict `/mnt/<single-letter-drive>/...` values to
`<DRIVE>:\...` only for local native Windows requests. Remote `--remote-host`
requests keep `/mnt/<drive>/...` as POSIX paths. fMRIPrep image references
remain strings.

For runtime-capable fMRIPrep routes, require applicable runtime assets:
`fs_license`, `fmriprep_image`, and `templateflow_home`. For XCP-D, pass
`--target xcpd` and pass fMRIPrep derivatives as `--user-dataset-path` when the
user gives `<bids_root>/derivatives/fmriprep`. The probe can derive the BIDS
root and `output_root` from that derivatives boundary; raw BIDS context is
optional. The probe still reports `xcpd_image` when supplied, but do not require
it in path preflight. Context seeded from fMRIPrep artifacts is only a locator/runtime hint.
It does not prove XCP-D dataset or runtime readiness.

`path-probe` does not accept or report `--templateflow-tool-bin` and does not
test TemplateFlow command availability. fMRIPrep runtime-capable workflow
commands still require the concrete `--templateflow-tool-bin <bin-dir>` resolved
by the precheck above. For XCP-D, check `--templateflow-tool-bin` command
availability only when the user supplied TemplateFlow proof inputs, a saved
signature requires them, or the route explicitly asks to validate TemplateFlow
proof. Do not pause a fresh `xcpd-audit` solely because omitted TemplateFlow
tool bins are unavailable.

## Normalized Args Mapping

`path-probe` reports path categories in `normalized_args`. Before invoking the
next workflow CLI command, translate those keys to the selected route's explicit
CLI flags:

| Target | `normalized_args` key | Next workflow CLI flag |
| --- | --- | --- |
| `fmriprep` | `dataset` | `--bids-root` |
| `fmriprep` | `output_root` | `--output-root` |
| `fmriprep` | `templateflow_home` | `--templateflow-home` |
| `fmriprep` | `fs_license` | `--fs-license` |
| `fmriprep` | `fmriprep_image` | `--fmriprep-image` |
| `xcpd` | `fmriprep_derivatives` | `--fmriprep-derivatives` |
| `xcpd` | `output_root` | `--output-root` |

`dataset` is a path category, not a workflow CLI flag. Do not invent
`--dataset`; apply a corrected fMRIPrep `dataset` value as `--bids-root`.

## Ready Handling

If the result reports `preflight_decision=ready`:

1. Apply only deterministic `normalized_args` corrections.
2. Preserve user scope and saved-audit values that still apply.
3. Assemble the selected route's workflow CLI command.
4. Do not reload unresolved fallback rules inside route references.

`path-probe` is the only normal discovery surface. Do not replace it with broad
filesystem search, PowerShell discovery, or manual remote scans. Manual `ssh
test -d` or `ssh test -f` checks are implementation-debug only after CLI output
fails or contradicts other evidence.

## Non-Ready Handling

If the result reports `preflight_decision=pause_required`, `status: error`,
`ambiguous`, `missing`, `warning`, `failed`, `timed out`, or unanswered
TemplateFlow proof choices, read
[path-preflight-unresolved.md](path-preflight-unresolved.md).

Explicit audit-only continuation can continue to `runtime-audit` only. It is
not permission to execute, prepare assets, or bypass later runtime findings.
The explicit audit-only continuation path never approves execution.

## After Path Correction

After a deterministic correction, rebuild the next CLI command from the full
request context, not only from the corrected value. Preserve path-like
arguments, selectors, runtime options, resource flags, output-selection flags,
and any other explicit flags that still apply.

This preflight must not change route selection, saved execution, prepare
approval, or execution approval.
