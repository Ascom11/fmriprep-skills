# Audit Report

## First Principle

User-facing output readability comes before completeness. Use no internal terms
in headings or advice. Translate route tokens, payload fields, bucket names,
status values, and flags into ordinary user language before giving details.

## Purpose

Use this reference after `fmri-process` has produced dataset/runtime audit
artifacts and the reply needs to be readable by a non-programmer. It is
mandatory for user-facing audit reports, including paused process reports that
contain audit findings.

This reference is reporting guidance only. It must not change route selection,
hard-gate predicates, `--auto-approve` rules, or saved-artifact execution
rules, and it does not write artifacts. It does not report prepare execution
failures or artifact validation failures.

## Artifact Inputs

Prefer the audit facts returned by the current CLI payload. If the user asks to
review a saved audit, locate the matching snapshot through the CLI route or the
explicit archive path, then read only the compact audit JSON pair needed for the
report.

fMRIPrep compact pair:

- `dataset-audit.json`: dataset summary, compact warning findings, and
  excluded subjects/sessions with reason codes
- `runtime-audit.json`: runtime status, runtime context, proof references,
  readiness, resource summary, TemplateFlow requirements, blockers,
  prepare-required findings, and warnings

XCP-D compact pair:

- `xcpd-dataset-audit.json`: XCP-D dataset summary, compact warning findings,
  and excluded subjects/sessions with reason codes
- `xcpd-runtime-audit.json`: XCP-D runtime status, runtime context, proof
  references, readiness, resource summary, TemplateFlow requirements, blockers,
  prepare-required findings, and warnings

Agents must not read `dataset-audit-debug.json` or
`xcpd-dataset-audit-debug.json`. These are CLI-internal / agent-do-not-read
artifacts. They may contain one record per selected subject/session and may be
too large for agent context. `run-xcpd` may let the CLI internally validate the
XCP-D debug artifact; agents still do not open it.

For saved local or remote audit reports, read exactly the selected compact pair
through one batched read operation. For remote saved reports, batch those two
paths through one read boundary instead of issuing separate SSH reads. Keep
per-path path, existence, readability, text, and parse results so missing,
unreadable, empty, and invalid JSON artifacts remain distinguishable.

If a shell read is needed for artifact inspection, bound unknown output with
the default first-pass cap `COMMAND 2>&1 | head -c 4000`. Prefer a short JSON
field extraction over printing a whole artifact. Do not parse byte-truncated
JSON, and do not paste full debug artifacts into agent context.

If either compact artifact is missing, unreadable, empty, or invalid JSON, stop
using this checklist and report the CLI artifact-validation error plainly.

Do not infer a runnable subject list from debug artifacts. Report every
`subject_exclusions` entry under Excluded data.

Do not invent missing files. If an expected audit artifact is missing or
invalid, stop using this checklist and report the CLI artifact-validation error
that surfaced the problem.

## Reply Checklist

Write the user reply as a short natural-language report, not as raw saved-state
fields or key-value assignments. This is a required reporting contract for
reportable audit outputs. First principle: put plain language first. Do not
use internal bucket names, status values, route tokens, raw JSON field names,
or CLI flags as headings or advice.

Use the user's language for headings and finding explanations. For Chinese
requests, write the report in plain Chinese while keeping paths, subject IDs,
and trace codes unchanged.

Do not replace this shape with a generic summary or a free-form narrative. If a
reportable audit output exists, the reply must follow the ordered shape below
unless a section is truly inapplicable because the corresponding artifact fact
is absent.

### Required Report Shape

Required report shape:

1. Current conclusion: say whether preprocessing can start now or remains paused.
2. Data readiness: summarize selected data, runnable data, and excluded subjects or sessions.
3. Runtime readiness: name only the runtime, execution method, concurrency from
   the resource summary, image, and TemplateFlow facts that affect the current
   decision. For local or remote-local `worker_pool`, write the resource
   comparison in plain words: how many subjects can run at the same time, how many CPU threads each subject uses, how many CPUs the current environment has, and that 1-2 CPUs should remain free for the system and Docker or the run may be unsafe. Do not use "local worker pool is detected/selected" as the main user-facing sentence.
   Do not expose proof ids as section headings.
4. Important paths: list only paths that affect the current decision, such as output, work, image, license, TemplateFlow, or logs.
5. Storage check: Use `storage_check.comparison_text` when it is present for either local audits or successful remote storage probes; it already merges work and derivatives on shared volumes and separates them on split volumes. For WSL native output/work roots, this comparison uses the Windows host drive that stores the WSL VHDX when that drive can be resolved. When `comparison_text` is absent, read the stored estimate values for final derivatives, work minimum, work peak, and total peak increment, then label them plainly as estimated derivatives/work/total storage. State that free-space comparison is unavailable for this audit. Do not print the JSON field paths in the user report.
6. Must fix before starting: list hard stops in plain language with concrete repair advice.
7. Tool can prepare: list runtime items the agent can prepare, but explain
   the action in ordinary words before naming any internal route. Do not list
   FreeSurfer license findings here; license cannot be prepared. Report missing
   or unreadable license under Must fix before starting and ask the user for a
   readable license path. For image
   preparation, say that the container cache may need about 10 GB of additional
   disk space, ask the user to confirm the cache disk has enough space, and say
   to name both possible default cache roots: `$HOME/.apptainer/cache` for
   Apptainer and `$HOME/.singularity/cache` for Singularity. If the user
   chooses a custom cachedir outside home, warn that existing cache entries may
   not be reused and image pull or build time may increase. Also warn that
   container cache must not be on exFAT because image preparation may need
   symbolic links. TemplateFlow on exFAT can be very slow.
8. Needs your confirmation: list checks that require user approval before the agent may run them.
9. Warnings: list warnings that may still make execution unsafe, including
   unverified TemplateFlow proof.
10. Excluded data: list every excluded subject/session without dumping JSON.
    If an exclusion is caused by missing materialized payloads, report the
    affected subject/session and advice from the audit finding. Do not run
    `datalad get` for those entries by default.
11. Next step: give one concrete user-facing next step.

Do not describe a saved next-step snapshot as permission to execute. It is only
the tool's recorded decision at the time the audit was written.

Do not synthesize a storage comparison when `storage_check.comparison_text` is
absent. For failed remote storage probes, report the unavailable-comparison
message already present in the CLI payload or saved audit. Do not invent shared
or split volume status, free-space values, logs estimates, image cache estimates,
runtime `disk_risk`, or TemplateFlow estimates.

Do not omit non-empty finding groups. Empty groups may be omitted, but actual
hard stops, agent-preparable items, user-confirmation items, warnings, and
subject/session exclusions must appear under the plain-language headings above.

Write compact finding rows from the structured findings already present in the
CLI payload or saved audit artifacts. Every finding must visibly retain:

- severity rendered as five stars
- plain-language meaning
- concrete advice

Do not make issue codes part of the main user-facing sentence. If a technical
trace is useful, add it at the end of the row as `code: <issue_code>`. Do not
show raw field names such as `status`, `next_action`, `prepare_required`,
`templateflow_cache_status`, or `file_probe_status` in headings or advice.

Do not use Python to load the full issue catalog or reconstruct finding metadata
for routine reports. The audit payload already contains structured metadata for
observed codes only.

## Structured Findings

Use structured findings already present in the CLI payload or saved audit
artifacts:

- `findings.blockers`
- `findings.prepare_required`
- `findings.warnings`
- `subject_exclusions[].findings`

Each finding contains `code`, `category`, `severity`, `meaning`, and `advice`.
Render every user-facing severity as five stars, for example numeric severity 3
is shown as `★★★☆☆`. Do not print raw `severity=N` in a user-facing finding.
If structured findings are missing from a current audit payload, report that
the audit was produced by an older tool version and rerun the audit instead of
reconstructing metadata by hand.

## Checklist Example

This example shows one concise reporting shape. Plain language comes first;
trace codes, when useful, stay at the end of a row.

```text
Current conclusion: Review complete; preprocessing remains paused.

Data readiness: 8 selected; 7 runnable; `sub-003` excluded.

Runtime readiness: Remote Slurm + Apptainer selected. fMRIPrep image needs preparation. TemplateFlow present but unverified.

Runtime readiness for local run: Docker is selected. This run is configured for 5 subjects at the same time, with 4 CPU threads per subject. The current environment has 20 detected CPUs; keep at least 1-2 CPUs free for the system and Docker, otherwise the run may slow down or fail.
Local CPU check: concurrent subjects * CPU threads per subject = 5 * 4 = 20; available CPUs in this environment = 20.

Important paths:
out: `/path/to/bids/derivatives/fmriprep`
work: `/path/to/work/work_fmriprep/<run_id>`
image: `/path/to/_downloads/images/fmriprep.sif`
license: `/path/to/license.txt`

Storage check: derivatives (3.96 GB) + work (8.37 GB ~ 11.16 GB) < target drive free space.

Remote storage check: Remote free-space comparison unavailable: ssh unavailable.

Must fix before starting:
FreeSurfer license missing | ★★☆☆☆ | <meaning from structured finding> | Advice: <advice from structured finding> | code: `missing_fs_license`

Tool can prepare:
fMRIPrep image needs preparation | ★★★☆☆ | <meaning from structured finding> | Advice: <advice from structured finding> | code: `prepare_runtime_required_fmriprep_image`

Warnings:
TemplateFlow unverified | ★★★☆☆ | <meaning from structured finding> | Advice: <advice from structured finding> | code: `templateflow_unverified`

Next step: Provide a readable FreeSurfer license path, then run readiness review again.
```


## Review Issue Boundaries

- Map audit artifact findings from their original internal groups to the
  plain-language report headings above.
- Warnings are reporting-only and must not be promoted to hard stops.
- Agent-preparable image and TemplateFlow findings are reported under Tool can
  prepare or Needs your confirmation; warnings stay under Warnings. License
  findings are not agent-preparable. They are not rewritten as hard stops unless
  the audit already reports them as hard stops.
- Do not report prepare execution failures as audit findings. Report the
  prepare-stage payload or error outside this audit checklist after audit
  reporting has ended.
- Do not report saved execution or artifact validation failures as audit findings.
  Report the CLI payload error outside this audit checklist.

## Rules

- Do not invent an extra approval step outside structured audit findings.
- Do not write any audit report artifact.
- Do not copy CLI command templates into the report.
- Do not promote subject-level exclusions to request-level blockers unless the
  CLI already returned that request-level blocker.
- Do not turn subject/session exclusions into automatic dataset
  materialization work. Ask for explicit user direction before repairing
  dataset payloads.
