# fMRIPrep Arguments

Read this after [route.md](route.md), [dataset-audit.md](dataset-audit.md), or
[runtime-audit.md](runtime-audit.md) selects a fMRIPrep CLI action.

For shared locator, remote, path, runtime proof, resource, and WSL
storage arguments, read [../common/arguments.md](../common/arguments.md).
This file covers fMRIPrep-specific parameter semantics only.

## Parameter Details

| Argument | Accepted by | Meaning |
| --- | --- | --- |
| `--fs-no-reconall` | `process`, `dataset-audit`, `runtime-audit` | Run fMRIPrep with `--fs-no-reconall`. This mode is volume-only, still requires `--fs-license`, and skips FreeSurfer subjects-dir/prewarm. |
| `--skip-bids-validation` | `process`, `runtime-audit` | Skip external BIDS validation at execution time only when the user or dataset contract requires it. |
| `--task-id <task>` | `process`, `dataset-audit`, `runtime-audit` | Official fMRIPrep task selector. It filters BOLD inputs before readiness and is stored in request/storage signatures. |
| `--echo-idx <index>` | `process`, `dataset-audit`, `runtime-audit` | Official fMRIPrep echo selector. It filters BOLD inputs before readiness and is stored in request/storage signatures. |
| `--anat-only` | `process`, `dataset-audit`, `runtime-audit` | Official fMRIPrep anatomical-only mode. It does not require BOLD inputs and changes storage signature identity. |
| `--output-spaces <space> [<space> ...]` | `process`, `dataset-audit`, `runtime-audit` | fMRIPrep output spaces used for storage estimates, TemplateFlow requirements, and command construction. |
| `--cifti-output 91k` | `process`, `dataset-audit`, `runtime-audit` | CIFTI output request. It may add TemplateFlow requirements and changes the storage/output-selection signature. |
| `--fmriprep-image <fmriprep-image>` | `process`, `runtime-audit` | Registry reference or target-visible fMRIPrep image path. Remote POSIX paths are remote paths; this flag does not upload or sync an image. |

Read [custom-args.md](custom-args.md) before adding any custom fMRIPrep flag.
Known custom arguments use typed rendering. For user-requested custom flags
outside the local list, warn first; if the user still confirms execution,
continue through the existing workflow path and report any CLI failure payload.
Do not pass arbitrary shell strings into the container command, and do not open,
read, or edit package source files by default to make a flag work. The agent
must not read or modify Python implementation code by default.

## Output Selection

Output selection affects storage estimates, TemplateFlow readiness/preparation,
and fMRIPrep command construction. It does not change route selection, dataset
readiness, executor selection, or image materialization behavior.
It may change TemplateFlow readiness because required TemplateFlow templates are
derived from the effective output selection.

Current agent-facing inputs are `--output-spaces <space> [<space> ...]`,
`--cifti-output 91k`, and `--fs-no-reconall`.
If the user supplies YAML config, read [../common/config.md](../common/config.md)
before `path-probe`, translate supported output-selection values into these
explicit flags, then continue without passing config to the CLI.

Defaults: `MNI152NLin2009cAsym:res-2`, `MNI152NLin6Asym:res-2`, and
`--cifti-output 91k`.

- output selection may be supplied to fresh audit/process/runtime-audit routes
- a translated config value can keep a fresh request no-CIFTI when explicitly
  needed.
- saved execution consumes output selection already recorded in artifacts.
- current-turn output-selection overrides do not bypass saved runtime findings.
- `--fs-no-reconall` defaults to volume-only output spaces and rejects CIFTI or
  surface output requests.
- `--fs-no-reconall` still needs FreeSurfer license proof and `--fs-license`.
- no-reconall runtime audits still require `license.freesurfer` proof.
- Changing from no CIFTI to `--cifti-output 91k` changes the
  storage/output-selection signature. Use a fresh `process`, not runtime-only
  reuse from a no-CIFTI dataset audit.
