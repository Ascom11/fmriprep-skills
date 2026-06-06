---
name: fmri-followup
description: Use when inspecting fMRIPrep or XCP-D logs, scheduler state, crash files, outputs, run status, or progress/status monitoring requests such as "检查进度", "查看进度", "监测", "monitor", "check progress", or "check status"; prefer this skill when a run failed or when debugging a failed or confusing run. Do not use for XCP-D execution requests.
---

# fMRI Follow-Up

## Use This Stage When

Use this stage after `run-fmriprep` or `run-xcpd` has launched or completed and
the question is now about inspection: outputs, logs, scheduler state, crash
files, or run status.

Use this stage when the user asks to check progress or monitor a launched run,
including requests phrased as "检查进度", "查看进度", "监测", "monitor",
"check progress", or "check status".

Use this stage first when a run failed or the user asks to debug a failed or
confusing run.

## Don't Use This Stage When

Don't use this stage when the request is to run preprocessing or
postprocessing. Use `$fmri-process` route `xcpd` for explicit XCP-D execution
requests when valid fMRIPrep derivatives already exist.

## Inputs

Inspect the saved run context only:

- the chosen `output_root`
- the chosen `log_root`, when one was used
- the previous execution status and subject-level run records

## Context Budget

Protect agent context when reading logs, artifacts, or directory listings.
Any command with unknown or potentially large output must be bounded. Use this
as the default first-pass byte cap:

```bash
COMMAND 2>&1 | head -c 4000
```

Prefer `run-status` bounded JSON fields, compact artifact fields, and short
tails over raw file dumps. Keep evidence readable, but do not paste full logs
or full debug artifacts unless the user explicitly asks for that exact content.
If bounded evidence is truncated or does not contain enough diagnostic context,
do a targeted second read with a larger cap, more tail lines, or a keyword/line
range query instead of dumping the whole file.

## Harness Trace

For a new same-dataset thread/session, including after context compaction,
recover or create the single dataset harness trace before `run-status` or
manual probes. Use the fMRI-process
[harness trace guide](../fmri-process/references/common/harness-trace.md) for
path, template, append, compaction, correction, and boundary rules.

## Stage Boundary

This stage handles inspection and status reporting only.

It does not own a public execution subcommand.

Entry rule:

- `$fmri-process` default chain does not auto-enter this stage after successful `run-fmriprep`
- enter this stage only when the user explicitly asks for post-run inspection
- if this stage reports valid fMRIPrep outputs and the user asks to continue
  with XCP-D, return to `$fmri-process` route `xcpd`

## What To Inspect

For run progress monitoring requests, first read the shared
[references/run-inspection.md](references/run-inspection.md) guide. It tells
you how to choose the execution target and how to use the read-only
`run-status` CLI before manual probes.

After the target is known, read exactly one target reference:

- fMRIPrep target: [references/run-inspection-fmriprep.md](references/run-inspection-fmriprep.md)
- XCP-D target: [references/run-inspection-xcpd.md](references/run-inspection-xcpd.md)

Inspect only the selected target's saved context, logs, scheduler/process
evidence, output tree, and crash evidence.

## Do Not Do

- Do not invent an extra inspect/finalize lifecycle layer.
- Do not run `run-fmriprep` from this stage.
- Do not run `run-xcpd` from this stage.
- Do not handle XCP-D execution requests from this stage; route explicit XCP-D
  execution intent to `$fmri-process` route `xcpd`.
- Do not treat XCP-D as an automatic continuation; require explicit user intent.
- Do not assume this stage is the default handoff after `run-fmriprep`.
- Do not hide scheduler state; report concrete job IDs, paths, and reasons.
