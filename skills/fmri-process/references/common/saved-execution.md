# Common Saved Execution

Read this from saved execution route files after selecting `run-fmriprep` or
`run-xcpd`.

This file is the shared retry, failure handoff, artifact-stability, and
`run-id` reference. Pipeline files keep their saved selector and artifact names.

## Failure Handoff

After `run-fmriprep` or `run-xcpd` returns `failed`, report the failure payload
first. Do bounded stderr triage from payload-provided stderr/log paths. If
stderr suggests dataset/runtime drift, read-only TemplateFlow, no network, or
missing runtime assets, say re-audit should happen before recovery or rerun.
Route deeper log, crash, output, or status investigation to `$fmri-followup`
first.

Do not start re-audit, prepare, rerun, manual probes, or a manual container
command from `$fmri-process` unless this file's retry policy allows the action
and the user explicitly approves it.

## Retry Policy

If saved execution fails after launch or submission attempt, retry only the same
saved artifact set in the same user request. Use a maximum of two retries after
the initial failure. Total attempts are capped at three.

Each retry must reuse the same saved selector or saved artifact set. Do not
change dataset artifacts, runtime artifacts, prepare inputs, resource overrides,
or current-turn runtime values. `--run-id` may change only to separate logs.
`--run-id` must not participate in artifact selection.

After the second failed retry, stop and report the final failure payload plus a
short summary of the first two attempts. First use `$fmri-followup` evidence and
payload-provided stderr/log paths to summarize the likely cause. If bounded
stderr suggests dataset/runtime drift, read-only TemplateFlow, no network, or
missing runtime assets, recommend re-audit before recovery or rerun.

Only after the initial attempt plus two failed retries may you ask whether the
user wants to bypass the `run-fmriprep` / `run-xcpd` interface and manually run
the exact single-subject container command returned by the execution payload.
This manual container fallback requires explicit user approval and must not be
used before both retries have failed. Do not start the manual container command
automatically.

## Artifact Stability

Saved execution is artifact-only. It consumes archived audit artifacts and must
not run fresh audits, run prepare, or clear blockers with current-turn runtime
overrides.

Config must not locate the saved snapshot or supply fresh runtime, resource,
output-selection, recon-mode, selector, or custom-argument overrides to saved
execution. Saved execution uses only explicit locator arguments and values from
the saved request.

If the current request changes dataset/output locator, remote host, selected
subjects/sessions, runtime paths, image, backend, output selection, or resource
choices, do not patch those values into saved execution. Rerun the matching
fresh audit route. For fMRIPrep runtime-only changes, use
`process --reuse-dataset-from <audit_id|audit_dir|dataset-audit.json> --reaudit-runtime`
only when the saved dataset and storage signature still match.

Within one troubleshooting thread, keep using the selected audit snapshot as the
dataset selector when the dataset/output selector is unchanged. Prefer copying
the saved dataset audit through `--reuse-dataset-from ... --reaudit-runtime`
instead of reopening a fresh dataset audit. After the CLI writes a new audit id,
use that new id for later execution or inspection in the same thread.

`next_action` is advisory route evidence, not execution approval. Saved
execution and artifact validation failures are not audit results; report the
error code, missing or invalid artifact path when present, and required recovery
route. Do not route those failures through the audit report.

Saved execution requires current-turn execution approval from the matching route
contract. Saved continuation, audit results, and saved `next_action` values
only identify candidates; they do not authorize prepare, run, or runtime
override changes.
