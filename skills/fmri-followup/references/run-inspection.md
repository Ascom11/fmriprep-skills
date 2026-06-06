# Run Inspection

## Purpose

Use this shared guide when the user asks about a launched or completed
fMRIPrep or XCP-D run. This is read-only post-run inspection. Start with
`run-status`; do not submit work, prepare assets, recover, or rerun from
`$fmri-followup`.

## Inputs

Start from saved run context:

- `latest.json`
- latest `execution-context.json`, when present
- latest `submission-result.json`, when present
- target-specific audit artifacts, when needed for route context
- payload-provided stdout, stderr, launcher, report, and crash-log paths

`latest.json` is only a discovery index. It is not proof that a run launched,
succeeded, failed, or is execution-ready.

## Select Target

Select exactly one execution target before reading target-specific outputs:

- Use the explicit user target when the request says fMRIPrep or XCP-D.
- Otherwise let `run-status` infer target from saved
  `execution-context.json`, `submission-result.json`, or saved
  `request_signature.target`.
- If `run-status` returns `target-ambiguous`, stop and ask for target. Do not
  inspect both targets because both output trees exist.
- If `run-status` returns `submission-ambiguous`, stop and ask for the missing
  selector named in `summary.missing_evidence`.

After target is known, read exactly one target reference:

- fMRIPrep target: [run-inspection-fmriprep.md](run-inspection-fmriprep.md)
- XCP-D target: [run-inspection-xcpd.md](run-inspection-xcpd.md)

## Run Status First

Use the profile matching available selector evidence.

Known exact fMRIPrep submission:

```bash
python -m fmri_process.cli run-status \
  --target fmriprep \
  --output-root <output-root> \
  --audit-id <audit-id> \
  --submission-id <submission-id> \
  --log-lines 20 \
  --max-paths 20
```

Known exact XCP-D submission on a remote host:

```bash
python -m fmri_process.cli run-status \
  --target xcpd \
  --output-root <output-root> \
  --audit-id <audit-id> \
  --submission-id <submission-id> \
  --remote-host <remote-host> \
  --log-lines 20 \
  --max-paths 20
```

Latest saved-context discovery when no target or submission id is known:

```bash
python -m fmri_process.cli run-status \
  --output-root <output-root> \
  --log-lines 20 \
  --max-paths 20
```

For an already submitted or launched run, prefer exact selectors:
`--audit-id` plus `--submission-id`. `--audit-id` alone is only latest
discovery inside that audit; if more than one submission exists, it is not
exact and `run-status` must ask for `--submission-id`.
`--submission-id` alone is exact only when it matches exactly one saved audit
archive; otherwise `run-status` must ask for `--audit-id`.

Omit `--remote-host` only for local runs. Treat the JSON result as primary
monitoring evidence. After `run-status` returns, append a short entry to
`<dataset-output-root>/_artifacts/harness-trace.md` with target, selector,
status, compact evidence, and next action.

## Target Profiles

| Target | Output tree | Extra evidence | Recovery route |
| --- | --- | --- | --- |
| `fmriprep` | `output_root/fmriprep` | exact subject HTML report tails and fMRIPrep crash logs | `$fmri-process` `process` or route `xcpd` after valid derivatives |
| `xcpd` | `output_root/xcp_d` | XCP-D logs and crash logs only | `$fmri-process` route `xcpd` after explicit user request |

Do not inspect upstream or downstream target outputs from the wrong profile.

## Selector Discipline

Within one inspection thread, keep one selected `audit_id` and, when available,
one selected `submission_id`. Every `run-status`, scheduler/process probe,
manual log read, and crash search must use evidence from the same selected
audit/submission and `log_root`. Do not combine a status result from one audit
with manual crash or log probes from another audit directory.

If recovery or re-audit creates a new audit id, switch the selector to that new
id before continuing inspection.

## Parameter Details

- `--bids-root <bids-root>`: BIDS root used to derive default output root.
- `--output-root <output-root>`: output root containing `_artifacts/`.
- `--remote-host <remote-host>`: read scheduler, process, exact log, and exact
  crash evidence on the remote target.
- `--target fmriprep|xcpd`: target to inspect when known.
- `--audit-id <audit-id>`: audit archive selector.
- `--submission-id <submission-id>`: submission archive selector; exact alone
  only when the saved submission id matches one audit archive.
- `--log-lines <n>`: maximum tail lines per log, default 20, clamped to 1..50.
- `--max-paths <n>`: maximum log, report, or crash paths, default 20, clamped
  to 1..50.

If `summary.missing_evidence` includes `stdout_stderr_log_paths`, say the saved
execution payload lacks stdout/stderr path evidence. Do not infer success or
failure from absent logs.

## Current Stage From Log Tail

When the user asks what stage is running now, do not add a separate phase
parser or require a structured phase field. Use the bounded `summary.logs[].tail`
returned by `run-status` as the evidence.

If the tail clearly names the active workflow, node, command, or tool, answer in
plain language with an explicit "likely" qualifier and cite the matching log
text. Examples: `skullstrip_wf`, `antsBrainExtraction`, or `BrainExtraction`
means the run is likely doing skull stripping; `recon-all` means FreeSurfer
surface reconstruction; `bold_*_wf` means BOLD preprocessing; report-generation
messages mean report building.

If the tail only shows scheduler output, stale completed lines, warnings without
an active node, or too little context, say the current stage is unclear from the
available log tail. Do not invent a stage and do not treat this best-effort
inference as scheduler truth.

Use this compact wording:

```text
Current stage: likely <plain-language stage>.
Evidence: <log path> tail mentions <matched workflow/node/tool>.
Confidence: best-effort from bounded stdout/stderr tail, not a structured runtime state.
```

## Manual Fallback

Use manual probes only when `run-status` reports missing evidence or the user
asks for deeper inspection.

- Keep probes bounded: `COMMAND 2>&1 | head -c 4000`.
- Prefer at most 20 log tail lines and at most 20 paths.
- For remote runs, probe the remote filesystem and scheduler; do not run the
  fMRI CLI remotely.
- For native Windows local runs, do not run `squeue`, `ps`, or direct
  `run_shell` probes.
- Prefer payload-provided log paths over discovery searches.
- Do not parse byte-truncated JSON.

Crash evidence must belong to the selected submission. Trust payload-provided
paths or crash files inside the selected run window. If a subject log subtree
contains an old `crash*` file outside the selected submission/run window, report
it as stale evidence and do not classify the current run as failed from that
file alone.

## State Layer

Run-inspection states describe current monitoring evidence. `submitted` or
`launched` from saved execution payloads must not be reported as `running`
unless scheduler/process evidence, bounded log evidence, or current crash
evidence supports it.

| Status | Plain meaning |
| --- | --- |
| `running` | Saved scheduler job or saved process is currently visible. |
| `queued` | Saved Slurm job is visible but waiting or configuring. |
| `launched-but-not-visible` | Saved artifacts say submitted or launched, but current probes do not see the saved job or process. |
| `failed` | Saved execution, current crash evidence, or strong stderr evidence points to failure. |
| `completed` | Saved execution or bounded completion evidence supports completion. |
| `unknown` | Required saved evidence is missing or too incomplete to classify. |
| `target-ambiguous` | No explicit or saved target could be selected. |
| `submission-ambiguous` | The selected audit or submission id matches more than one saved submission context. |

Use this compact template for `launched-but-not-visible`:

```text
Selector: audit_id=<id|unknown>, submission_id=<id|unknown>, target=<target>.
Current visibility: saved job/PID is not visible now.
Checked evidence: scheduler=<summary>, process=<summary>, logs=<summary>, crashes=<summary>.
Conclusion: cannot determine root cause from visibility loss alone; inspect named log or missing evidence next.
```

## Failure Triage

If strong stderr or current crash evidence points to dataset/runtime drift,
read-only TemplateFlow access, no network, attempted TemplateFlow download, or
missing runtime assets, recommend re-audit through `$fmri-process`.

Do not start prepare, recovery, cleanup, or rerun from `$fmri-followup`.

## Stop Rule: Lost After Launch

When no current crash files are visible, fMRIPrep/XCP-D stderr, launcher
stderr, and step stderr have no error clue, and the saved PID or Slurm job is
no longer visible, stop treating the case as a normal pipeline crash.

```text
"No tool-internal crash file or stderr error was found. The saved job or PID is no longer visible, so this evidence cannot distinguish manual kill or scancel, session interruption, node or scheduler cleanup, or a completed process whose final output was not inspected. Treat external interruption as possible but unproven; request recovery or deeper log inspection as a separate explicit action."
```
