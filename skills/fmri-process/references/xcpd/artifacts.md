# XCP-D CLI Reference

Read this file after `$fmri-process` selects route `xcpd`. It defines the
XCP-D command-family boundary and archived artifact contract.

Shared invocation, config, remote, path, TemplateFlow proof, resource, and saved
execution rules live in [../common/arguments.md](../common/arguments.md) and
[../common/saved-execution.md](../common/saved-execution.md).
Action-specific parameter details live in [xcpd-args.md](xcpd-args.md),
[xcpd-audit.md](xcpd-audit.md), and [run-xcpd.md](run-xcpd.md).

## Public Commands

- `xcpd-audit`: check valid fMRIPrep derivatives, XCP-D min-time warnings, and
  XCP-D runtime/image readiness. It writes XCP-D artifacts and stops.
- `run-xcpd`: submit or launch XCP-D from saved XCP-D artifacts only.

## Core Inputs

`xcpd-audit` checks XCP-D dataset scope: subjects, sessions, XCP-D mode,
min-time threshold, fMRIPrep derivatives boundary, and XCP-D runtime signature
fields.

`run-xcpd` performs saved-artifact execution. The CLI validates execution
subject scope from saved XCP-D artifacts. Agents do not open the XCP-D dataset
debug artifact, and current command selectors do not change saved scope.

## Archived Artifact Contract

This section is the canonical source for XCP-D artifact names, reuse keys, and
execution subject source.

`xcpd-audit` creates all XCP-D artifacts under one `audit_id`:

- `xcpd-runtime-audit.json`
- `xcpd-dataset-audit.json`
- `xcpd-dataset-audit-debug.json`

`xcpd-audit --reuse-context-from <audit>` may read a saved fMRIPrep audit to
seed missing locator/runtime defaults, take the fMRIPrep runnable subject scope,
and reuse ready component proofs for shared runtime facts. Fresh XCP-D artifacts
are still required and may let the fresh XCP-D artifacts reference ready shared
fMRIPrep proofs.

The XCP-D dataset artifact checks only the needed fMRIPrep derivatives under
the resolved `--fmriprep-derivatives` root. The XCP-D image and the XCP-D
image's TemplateFlow container-import proof are never inherited from fMRIPrep.

Agents read the compact XCP-D audit artifacts above. Do not treat
`runtime-proofs.json` proof ids or older proof shapes as direct readiness
evidence.
Runtime image, image-root, and TemplateFlow cache details live in
`runtime-proofs.json`; agents use compact XCP-D artifacts for route decisions.

`prepare-probe --target xcpd --from-runtime-audit <audit>` reads archived
`xcpd-runtime-audit.json` only. It can prove manually prepared XCP-D image
assets after a `needs_prepare` audit and can inspect optional license or
TemplateFlow evidence, but XCP-D TemplateFlow proof is advisory by default. It
does not create or update the audit artifact.
If that advisory TemplateFlow container-import proof is not ready, `run-xcpd`
does not bind the saved `templateflow_home`; it records
`xcpd_templateflow_bind_skipped` in the execution plan instead. Treat that as a
warning that XCP-D may fall back to its container-default TemplateFlow cache.

`run-xcpd` reads archived compact artifacts selected by
`--resume-from <audit>` and lets the CLI internally validate
`xcpd-dataset-audit-debug.json`. Execution requires matching saved XCP-D
artifacts and does not accept current-turn subject or session scope.

Do not consume shared `runtime-audit.json`, `dataset-audit.json`, or
`dataset-audit-debug.json` for XCP-D execution.

Agents must not derive XCP-D execution subjects from
`xcpd-dataset-audit-debug.json`. The CLI validates saved subject scope
internally. `runnable_subjects` is the fMRIPrep execution path field, not an
agent-facing `run-xcpd` consumption field.

Dataset readiness is mode-specific. `abcd` requires fsLR/CIFTI fMRIPrep
derivatives plus confounds TSV/JSON. `nichart` readiness checks one coherent
NIfTI derivative set; mismatch is advisory in audit output, not a saved
execution subject blocker.

If the source fMRIPrep audit used `--fs-no-reconall`, treat it as volume-only:
do not assume fsLR/CIFTI derivatives exist. `nichart` may reuse the source
FreeSurfer license proof; `abcd` remains blocked because the source lacks
surface/CIFTI derivatives.

`run-xcpd` does not read or write fMRIPrep execution replay artifacts.

XCP-D saved artifact reuse uses `fmriprep_derivatives`, `bids_root`,
`output_root`, `remote_host`, `xcpd_mode`, `xcpd_min_time`,
`xcpd_min_time_explicit`, `xcpd_motion_filter_type`, `xcpd_band_stop_min`,
`xcpd_band_stop_max`, `xcpd_motion_filter_order`, `xcpd_despike`,
`xcpd_task_ids`, `xcpd_bids_filter_file`, `xcpd_datasets`, `xcpd_mem_mb`,
`xcpd_custom_args`, and runtime signature fields.
The CLI validates explicit current-turn fields against the selected saved
signature, then reconstructs execution from saved artifact signatures.
The CLI validates the saved execution subject scope from archived artifacts.

## Notes

- `xcpd-audit` subject scope supports exact IDs and wildcard patterns.
- non-sessionized subjects must fail clearly when `xcpd-audit --session` is
  provided.
- If `--xcpd-image` is omitted, the CLI uses
  `docker://pennlinc/xcp_d:26.1.0` as the tested default source.
- If `--xcpd-min-time` is omitted, the wrapper default is mode-specific:
  `abcd=240` and `nichart=0`.
- If motion-filter arguments are omitted, the wrapper default for `abcd` is
  `lp`, `band-stop-min=12`, `motion-filter-order=4`, and `despike=y`.
  If `abcd` uses an explicit non-`none` motion filter, omitted
  `motion-filter-order` and `despike` still default to `4` and `y`.
  `nichart` omits motion-filter and despike flags unless explicit.
- For `nichart`, the default TemplateFlow requirement is
  `MNI152NLin2009cAsym` only.
- If `xcpd-audit` reports prepare-required wrapper execution gates, report only
  listed `prepare_requirements` and wait for current-turn prepare approval.
  After approved preparation, rerun `xcpd-audit`. Report ready saved artifacts;
  run `run-xcpd` only with current-turn execution approval.
