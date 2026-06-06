# Dataset Audit Route Contract

Read this route contract after `$fmri-process` classifies the user request as a
dataset-only readiness review.

## Route Scope

`dataset-audit` checks BIDS dataset scope, subject/session availability,
storage estimates, and fMRIPrep dataset readiness. It does not inspect runtime
assets and does not submit preprocessing.
Fresh dataset-only preflight normalizes dataset and output paths only. It must
not pause because TemplateFlow command proof is missing.

## What To Read

Before the first workflow command, read:

- Use `normalized_args` from the parent path preflight when it ran. For this
  route, the parent preflight uses dataset-only mode and skips TemplateFlow
  command checks.
- [../common/arguments.md](../common/arguments.md) for shared locator,
  selector, config, and remote rules.
- [fmriprep-args.md](fmriprep-args.md) for fMRIPrep dataset selectors and output
  selection.

## Dataset Selectors

Dataset audit checks subject/session/task/echo/anat-only scope:

- `--subject <selector>` and `--subject-file <subject-file>` choose subjects.
- `--session <session>` narrows sessions only when the user requested it.
- `--task-id <task>` filters BOLD inputs before readiness and storage
  signatures.
- `--echo-idx <index>` filters multiecho BOLD inputs before readiness and
  storage signatures.
- `--anat-only` permits anatomical-only readiness and does not require BOLD
  inputs.

Output selection is part of the dataset snapshot. `--output-spaces`,
`--cifti-output 91k`, and `--fs-no-reconall` change storage estimates and
storage/output-selection signatures. If any of these change, run a fresh
`process`/`dataset-audit`; do not patch saved artifacts.

When audit artifacts or payload findings need a user-facing report, read
[audit-report.md](../common/audit-report.md).

## Completion

Stop after dataset findings. Missing materialized subject/session payloads are
dataset findings; do not repair dataset content unless the user explicitly asks
for dataset repair.
