# XCP-D Arguments

Read this after [route.md](route.md) selects `xcpd-audit` or `run-xcpd`.

For shared locator, remote, path, runtime proof, resource, and WSL
storage arguments, read [../common/arguments.md](../common/arguments.md).
This file covers XCP-D-specific parameter semantics only.

## Parameter Details

| Argument | Accepted by | Meaning |
| --- | --- | --- |
| `--xcpd-image <xcpd-image>` | `xcpd-audit`, `run-xcpd` | Registry reference or target-visible XCP-D image path. Defaults to `docker://pennlinc/xcp_d:26.0.2` when omitted. |
| `--xcpd-mode <abcd\|nichart>` | `xcpd-audit`, `run-xcpd` | Mode-specific derivative contract. `abcd` needs fsLR/CIFTI derivatives; `nichart` needs MNI-space NIfTI derivatives. |
| `--xcpd-min-time <seconds>` | `xcpd-audit`, `run-xcpd` | Minimum run duration warning threshold. When omitted, wrapper uses `240` for `abcd` and `0` for `nichart`. |
| `--xcpd-task-id <task>` | `xcpd-audit`, `run-xcpd` | XCP-D task filter. Repeat for multiple tasks. `run-xcpd` uses the saved audit value only. |
| `--xcpd-bids-filter-file <json>` | `xcpd-audit`, `run-xcpd` | XCP-D BIDS filter JSON. Audit checks that it is valid JSON. The file is bound into the container and rendered as `--bids-filter-file /xcpd_bids_filter.json`. |
| `--xcpd-dataset <alias=path>` | `xcpd-audit`, `run-xcpd` | Extra derivative or atlas dataset for XCP-D `--datasets`. Repeat for multiple aliases. Aliases must be simple tokens. Paths are audited and bound into the container. |
| `--xcpd-mem-mb <mb>` | `xcpd-audit`, `run-xcpd` | XCP-D internal memory limit rendered as `--mem-mb`. This is separate from scheduler memory such as `--slurm-mem-gb`. |
| `--xcpd-motion-filter-type <lp\|notch\|none>` | `xcpd-audit`, `run-xcpd` | Motion-parameter filter. `lp` requires `--xcpd-band-stop-min`; `notch` requires both band-stop bounds; `none` renders no motion-filter flags. |
| `--xcpd-band-stop-min <bpm>` | `xcpd-audit`, `run-xcpd` | Lower motion-filter frequency in breaths per minute. Required for `lp` and `notch`. |
| `--xcpd-band-stop-max <bpm>` | `xcpd-audit`, `run-xcpd` | Upper motion-filter frequency in breaths per minute. Required for `notch`. |
| `--xcpd-motion-filter-order <order>` | `xcpd-audit`, `run-xcpd` | Motion filter order. Wrapper default for `abcd` is `4`. |
| `--xcpd-despike <y\|n>` | `xcpd-audit`, `run-xcpd` | XCP-D despike setting. Wrapper default for `abcd` is `y`; omitted for `nichart` unless explicit. |
| `--reuse-context-from <audit>` | `xcpd-audit` | Seed missing locator/runtime context from a fMRIPrep audit. This never proves XCP-D readiness. |
| `--resume-from <audit>` | `run-xcpd` | Required saved XCP-D audit selector. Accepts audit id, audit directory, or artifact JSON path. |
| `--scheduler-partition <partition>` | `xcpd-audit`, `run-xcpd` | Slurm queue override for XCP-D audit/execution. Do not use it to bypass audit-reported constraints. |
| `--run-id <run-id>` | `xcpd-audit`, `run-xcpd` | Log grouping key only. It does not select artifacts. |

If the user supplies YAML config, read [../common/config.md](../common/config.md)
before `path-probe`, translate supported values into these explicit flags,
then continue without passing config to the CLI.

## Dataset And Artifact Rules

- XCP-D's official positional input is the preprocessed fMRIPrep derivatives
  directory. In this wrapper, provide that as `--fmriprep-derivatives`.
- `--bids-root` is optional context for XCP-D when `--fmriprep-derivatives`
  resolves. Missing raw BIDS should produce a warning, not a blocker.
- `xcpd-audit` accepts current-turn subject/session selectors from
  [../common/arguments.md](../common/arguments.md).
- `run-xcpd` does not accept current-turn subject/session selectors. The CLI
  internally validates saved subject scope from archived XCP-D artifacts.
- `run-xcpd` requires `--resume-from`; do not rely on `latest.json`.
- Do not pass `--reuse-context-from` to `run-xcpd`.
- Runtime and filter fields on `run-xcpd` validate the selected saved
  signature only. They do not override saved execution values.
- Task, BIDS filter, extra dataset, memory, and saved `xcpd_custom_args`
  signature fields are saved request fields. Changing them requires a fresh
  `xcpd-audit`.
- Saved session scope renders as `--session-id ...`; saved task scope renders
  as `--task-id ...`.
- Extra XCP-D datasets render with container paths such as
  `--datasets custom=/xcpd_datasets/custom`, never raw host paths.
- When `--reuse-context-from` points to a fMRIPrep no-reconall audit, `abcd`
  is blocked because the source is volume-only and lacks surface/CIFTI
  derivatives. Use `nichart`, or rerun fMRIPrep with surface/CIFTI-capable
  outputs before a fresh `xcpd-audit`.

## Gate Categories

| Category | Findings |
| --- | --- |
| Official XCP-D input blockers | Missing or unreadable `--fmriprep-derivatives`; mode-specific missing derivatives that leave no runnable subject. |
| Explicit optional request blockers | User-supplied `--xcpd-task-id`, `--xcpd-bids-filter-file`, or `--xcpd-dataset` values that select no valid input or point to invalid files/datasets. |
| Wrapper execution gates | `output_root` write access, container runtime, Docker daemon, Slurm, generated paths, and XCP-D image readiness. |
| Warnings | Missing raw `--bids-root`; omitted or unverified TemplateFlow cache/import proof for XCP-D when the user did not explicitly request a TemplateFlow proof gate. |

Omitted XCP-D `--fs-license` is not an input blocker. Validate a FreeSurfer
license only when the user supplies `--fs-license`.
The workflow CLI does not test `--templateflow-tool-bin` command availability;
agents check `--templateflow-tool-bin` command availability only when the user
supplied TemplateFlow proof inputs, a saved signature requires them, or the
route explicitly asks to validate TemplateFlow proof. Do not pause a fresh
`xcpd-audit` solely because omitted TemplateFlow tool bins are unavailable.

## Custom Args

Read [custom-args.md](custom-args.md) before adding any custom XCP-D flag.
Known custom arguments use typed rendering from `xcpd.custom-args`; downstream
CLI commands do not accept config files directly. For user-requested custom
flags outside the local list, warn first; if the user still confirms execution,
continue through the existing workflow path and report any CLI failure payload.
Do not pass arbitrary shell strings into the container command, and do not open,
read, or edit package source files by default to make a flag work. The agent
must not read or modify Python implementation code by default.
