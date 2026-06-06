# XCP-D Run Inspection

Use this reference only after [run-inspection.md](run-inspection.md) selects
the XCP-D target.

## Target Delta

- Target: `xcpd`
- Output tree: `output_root/xcp_d`
- Primary command evidence: `run-status --target xcpd`
- Target-only evidence: XCP-D stdout/stderr and crash evidence
- Recovery route: `$fmri-process` route `xcpd`

Do not inspect upstream fMRIPrep outputs from this reference. If the user asks
whether fMRIPrep derivatives are valid enough to start XCP-D, route that
execution intent to `$fmri-process` route `xcpd` for audit/readiness checks.

## XCP-D Evidence

Use `summary.logs[]`, `summary.crashes[]`, scheduler evidence, process
evidence, and `summary.primary_error` from the shared `run-status` result.

`summary.outputs` defaults to
`{"checked": false, "skipped": "default_log_only"}`. Do not treat that as a
failed output validation.

## Crash Boundary

Prefer crash paths reported by `run-status --target xcpd`. If deeper manual
inspection is needed, use only saved `subject_key` values from the selected
submission. Do not enumerate `sub-*`.

If a found crash file is outside the selected submission/run window, name it as
stale evidence. Do not classify the current XCP-D run as failed from that old
crash alone.

## Reply Checklist

Return a compact XCP-D status report:

- run state: running, queued, launched-but-not-visible, failed, completed,
  unknown, target-ambiguous, or submission-ambiguous
- selector: target, audit id, submission id
- evidence: job IDs, PIDs, log paths, recent log lines, output tree, and
  current XCP-D crash paths
- affected subjects or sessions when identifiable
- next action: wait, inspect a named log, or rerun through `$fmri-process`
  route `xcpd` after an explicit user request

Use `unknown` when evidence is incomplete. Apply the shared lost-after-launch
stop rule when XCP-D has no current crash files, stderr is quiet, and saved
scheduler/process visibility is gone.

## Boundaries

- Do not treat partial XCP-D outputs as success unless logs and
  scheduler/process evidence support that conclusion.
- Do not search outside `output_root/xcp_d`, saved `log_root`, and saved
  payload paths.
