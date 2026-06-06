# fMRIPrep Run Inspection

Use this reference only after [run-inspection.md](run-inspection.md) selects
the fMRIPrep target.

## Target Delta

- Target: `fmriprep`
- Output tree: `output_root/fmriprep`
- Primary command evidence: `run-status --target fmriprep`
- Target-only evidence: exact subject HTML report tails at
  `output_root/fmriprep/<subject-key>.html`
- Crash evidence: fMRIPrep `crash*` files under selected saved crash-log
  directories

Do not inspect downstream XCP-D outputs from this reference. If the user asks
about XCP-D launch or outputs, return to the shared guide and select the XCP-D
target.

## fMRIPrep Evidence

Use `summary.logs[]`, `summary.reports[]`, `summary.subject_statuses[]`, and
`summary.crashes[]` from the shared `run-status` result.

`summary.reports[]` is only bounded tail evidence around `No errors to report!`.
It is not a full HTML read and not required-derivatives validation.

For a saved subject, the combination of:

- stdout/stderr tail containing `finished successfully`
- exact subject report tail containing `No errors to report!`

is enough to report that subject as `likely_completed`.

## Crash Boundary

Prefer crash paths reported by `run-status --target fmriprep`. If deeper manual
inspection is needed, use only saved `subject_key` values from the selected
submission. Do not enumerate `sub-*`.

If a found crash file is outside the selected submission/run window, name it as
stale evidence. Do not classify the current fMRIPrep run as failed from that
old crash alone.

## Reply Checklist

Return a compact fMRIPrep status report:

- run state: running, queued, launched-but-not-visible, failed, completed,
  unknown, target-ambiguous, or submission-ambiguous
- selector: target, audit id, submission id
- evidence: job IDs, PIDs, log paths, recent log lines, bounded report marker
  evidence, output tree, and current fMRIPrep crash paths
- affected subjects or sessions when identifiable
- next action: wait, inspect a named log, rerun through `$fmri-process`, or
  request `$fmri-process` route `xcpd` after valid fMRIPrep derivatives exist

Use `unknown` when evidence is incomplete. Apply the shared lost-after-launch
stop rule when fMRIPrep has no current crash files, stderr is quiet, and saved
scheduler/process visibility is gone.

## Boundaries

- Do not treat partial fMRIPrep outputs as success unless logs and
  scheduler/process evidence support that conclusion.
- Do not search outside `output_root/fmriprep`, saved `log_root`, and saved
  payload paths.
- Do not full-read subject HTML reports during routine monitoring.
