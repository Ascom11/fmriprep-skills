# Path Preflight Unresolved

Read this only when `path-preflight.md` reports a non-ready preflight result.
Use it for pause wording, bounded fallback limits, and TemplateFlow proof
choice.

## Status Surfaces

Read all status surfaces before asking the user:

- top-level `status`: `ok`, `needs_user_input`, `missing`, or `error`
- path entries: `exact`, `unique_correction`, `ambiguous`, `missing`, or
  `skipped`
- TemplateFlow command proof: missing, failed, or timed out in agent-side
  preflight

If the CLI returns `status: error`, pause and report the probe failure. Do not
replace it with manual discovery.

## Pause Policy

Ask once from the bundled `unresolved_questions`, but translate them before
replying. Do not dump payload field names, raw status values, or CLI flags as
the user's choices. Do not create a separate blocking list split by path,
runtime, command, required, or optional classes.

Pause when the CLI reports missing or ambiguous information, missing command
evidence, or failed probe evidence. Ask from `unresolved_questions`; do not
enumerate path categories here.

`skipped` means a category does not apply. Required-but-unresolved runtime
inputs are `missing`, not `skipped`.

## User Pause Reply

The path preflight only checks whether the inputs are understandable before the
real workflow command starts. It is not the dataset audit or runtime audit.

Write the pause in ordinary language:

1. Say what was already understood, such as dataset path, license, image, or
   TemplateFlow folder.
2. Say what still needs a value or proof, using names like dataset path,
   FreeSurfer license, fMRIPrep image, TemplateFlow folder, and target
   environment bin directory with `datalad`, `git`, and `git-annex`.
3. Say: You can provide the missing paths or continue audit-only.
4. Say: The later audit may still report these unresolved items as `missing` or
   `needs_prepare`.
5. Say: continuing audit-only does not prepare assets, download files, repair
   TemplateFlow, or start execution.

Do not ask for or imply `prepare-runtime` approval from a path-preflight pause.
Do not describe missing image, TemplateFlow, or license values as things the
agent can prepare at this stage. Do not use raw payload fields as user-facing
headings or advice.

Example:

```text
Current pause: I have only checked whether the paths and target commands are
clear enough to start the review. This is not the dataset or runtime review yet.

Already understood:
- BIDS dataset: `/path/to/bids`
- FreeSurfer license: `/path/to/license.txt`

Still unresolved:
- TemplateFlow folder
- fMRIPrep image

Next step: provide those paths, or tell me to continue audit-only. If we
continue audit-only, later review may still report these unresolved items as
missing or needing preparation. I will not prepare assets, download files,
repair TemplateFlow, or start execution from this pause.
```

## Bounded Discovery Rules

The CLI may use exact probes and bounded candidate roots derived from supplied
values or saved artifacts. It may correct:

- BIDS dataset roots
- XCP-D fMRIPrep derivatives roots
- TemplateFlow roots and TemplateFlow home paths
- FreeSurfer license files
- fMRIPrep and XCP-D SIF/SIMG images or registry references
- typo-like sibling output paths

Do not search broad home directories, filesystem roots, runtime cache trees,
Docker daemon storage, or remote home directories. Do not run wide `find`,
wide `rg`, or manual remote scans before or after `path-probe`.

## TemplateFlow Proof Choice

Fresh fMRIPrep `dataset-audit` does not need TemplateFlow proof. Use
path preflight only for path normalization and do not ask for TemplateFlow
command bins.

Runtime-capable routes use the pre-`path-probe` TemplateFlow tool precheck in
`path-preflight.md`. The workflow CLI does not test `datalad` or `git-annex`
availability.

When command proof is missing, failed, or timed out, choose one agent action
after translating it into the user pause reply above:

- a concrete `--templateflow-tool-bin <bin-dir>` for the later workflow CLI,
  with working `datalad`, `git`, and `git-annex`
- continue audit-only and carry TemplateFlow proof forward as unresolved risk

Do not infer a bin directory from unstated site notes or discovered
environments. If the user names a conda environment, find its target `bin`
directory before invoking the workflow CLI. Prefer
`--templateflow-tool-bin <bin-dir>` with working `datalad`, `git`, and
`git-annex`.

Use this plain-language prompt when command proof is unresolved:

```text
I have not proven that `datalad`, `git`, and `git-annex` are available for checking TemplateFlow.
```

The precheck proves command startup only. Final runtime readiness still comes
from `runtime-audit`; `prepare-probe` may reuse that saved proof for
TemplateFlow cache preparation.
