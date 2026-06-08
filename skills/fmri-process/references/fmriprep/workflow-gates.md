# Workflow Gates

## Purpose

Use this reference after the skill entry selects a real pre-fMRIPrep route. It
gives the stage chain, stop point, and gate meaning for each route. It is a
routing guide, not a replacement for CLI validation.

## Action Namespace And Entry Boundary

Use one action namespace everywhere in pre-fMRIPrep routing:

- process state field: `next_action`
- `next_action` allowed values: `dataset-audit`, `runtime-audit`,
  `prepare-runtime`, `run-fmriprep`, `process`
- CLI surface: `python -m fmri_process.cli <command>`
- public skill entrypoints: `$fmri-process` for execution routing,
  `$fmri-followup` for inspection, deprecated `$xcpd` stub for forwarding only

Use only the current action names in artifacts and technical handoff mapping.
Translate them before writing ordinary user-facing replies.

`$fmri-process` is the only parent execution router for fMRIPrep and XCP-D
requests.

- public router actions: `process`, `dataset-audit`, `runtime-audit`
- read-only prepare proof command: `prepare-probe`
- internal execution command: `run-fmriprep`
- route token without a CLI command: `prepare-runtime`
- explicit prepare or execution user intent still enters through
  `$fmri-process`
- after successful `run-fmriprep`, control returns to `$fmri-process`
- `$fmri-process` does not auto-enter `$fmri-followup`
- explicit XCP-D intent enters `$fmri-process` route `xcpd`
- `process` never routes directly to `run-xcpd`

## Stage Chains

| Scenario | Chain |
| --- | --- |
| generic request | `process -> dataset-audit -> runtime-audit -> paused` |
| direct-run-after-clean | `process --auto-approve -> dataset-audit -> runtime-audit -> run-fmriprep? -> paused \| blocked \| submitted \| launched` |
| resume saved request | `read compact saved audit artifacts -> prepare-runtime \| run-fmriprep --resume-from <snapshot> \| report-and-stop` |
| explicit prepare request | `prepare-runtime route -> documented manual image/TemplateFlow prepare -> prepare-probe readiness proof -> fresh process snapshot or process --reuse-dataset-from ... --reaudit-runtime before run-fmriprep` |
| single-audit request | `dataset-audit` or `runtime-audit` |

`prepare-runtime` is a route token, not a CLI command. `run-fmriprep` remains
the internal execution command.

Generic requests are audit-and-pause routes. They do not enter `run-fmriprep`
in the same turn unless the request is explicitly classified as
`direct-run-after-clean` and the explicit execution gate passes.

After successful `run-fmriprep`, the workflow stops in `$fmri-process`.
Post-run inspection and XCP-D require an explicit follow-up route.

Inside internal `run-fmriprep`, the CLI-managed per-subject
`fmriprep-container-probe` runs inside each local subject process, remote-local
process, or Slurm array task before true fMRIPrep starts. It is not a shared
`pre_step`; failure is an execution failure, not a runtime-audit blocker.

## Execution Failure Output

Before true fMRIPrep starts, the CLI-managed per-subject
`fmriprep-container-probe` runs inside each local subject process, remote-local
process, or Slurm array task. It is not a shared `pre_step`. If the probe
fails, treat it as an execution failure or pre-start subject failure reported
with the subject execution summaries, not as a runtime-audit blocker.

When execution reaches internal `run-fmriprep` and fails, stdout still contains
one valid JSON document and the process may exit nonzero. Do not parse stderr
as JSON; use stdout for machine-readable results and payload-provided log paths
for detail.

Failure summaries are intentionally bounded:

- failed pre-step summaries appear under `summary.execution.failed_pre_steps`
- failed subject launch summaries appear under
  `summary.execution.failed_subjects`
- summaries keep `returncode`; do not expect or emit `return_code`
- summaries include short `error` text and payload-provided operation log paths
  or manifest paths
- Do not hard-code launcher log filenames, PID manifest filenames, or PID
  manifest columns in the skill contract

For `run-fmriprep` failure handoff, retry, and manual container fallback, use
[../common/saved-execution.md](../common/saved-execution.md). Do not duplicate
those shared rules here.

## Gate Semantics

- Default `process` pauses after audits and reports the next action.
- Explicit fresh same-turn execution through `process --auto-approve` requires
  ready dataset/runtime artifacts and an execution-clean CLI gate result. Do
  not decide execution safety from bucket names alone. Execution-clean may
  include warning-only findings; report those warnings, but do not treat them
  as a direct-run blocker. Hard blockers or runtime preparation requirements
  still pause the route.
- Later natural-language continuation is artifact-first. The agent reads only
  compact `dataset-audit.json` and `runtime-audit.json` facts for route
  selection. It does not read `dataset-audit-debug.json`; saved execution calls
  `run-fmriprep --resume-from <snapshot>` and the CLI validates the archived
  debug artifact internally.
- Missing materialized subject or session data is a dataset finding. Report the
  affected entries under Excluded data and continue route selection for any
  runnable data. Do not start DataLad materialization for those entries unless
  the user explicitly asks for dataset repair.
- Clean saved-artifact candidates route to `run-fmriprep --resume-from
  <snapshot>`. Runtime artifacts with `status == "needs_prepare"`,
  `prepare_required` non-empty, and no hard blocker route to the
  `prepare-runtime` reference. Blockers, missing artifacts, invalid artifacts,
  or request mismatches are reported without execution.
- Saved snapshot selection, blocked snapshot recovery, and artifact validation
  require the saved execution reference before acting on a continuation
  request.
- Fresh explicit prepare requests use [prepare-runtime.md](../common/prepare-runtime.md) to run documented
  manual image or TemplateFlow materialization, then run CLI `prepare-probe`.
- `prepare-probe` is readiness evidence only. It is not an execution snapshot;
  execution still needs a fresh ready `process` snapshot, a new
  `process --reuse-dataset-from <snapshot> --reaudit-runtime` snapshot, or an
  already ready saved snapshot.
- Standalone `runtime-audit` and standalone `prepare-probe` do not replace a
  ready `process` snapshot. They are route evidence, not archived execution
  state.
- After manual prepare, run `prepare-probe` against the selected runtime audit.
  A successful `prepare-probe` is readiness evidence only. It does not replace
  an execution-capable process snapshot. To proceed toward execution, create a
  new ready snapshot with either a fresh `process` request or
  `process --reuse-dataset-from <snapshot> --reaudit-runtime`, preserving the
  same dataset-owning inputs and TemplateFlow tool-bin choices.
- A TemplateFlow-ready `prepare-probe --kind templateflow` or `--kind all`
  result followed by the matching dataset-owning inputs and TemplateFlow
  tool-bin choices still reporting a prepare requirement is a CLI/artifact
  contradiction. Stop and report it; do not route back to `prepare-runtime` or
  continue toward `run-fmriprep`.
- Runtime re-audit after a runtime change must use
  `process --reuse-dataset-from <snapshot> --reaudit-runtime` and must not
  reuse the old `audit_id`. `--run-id` groups logs only and does not decide
  audit reuse.
- `run-fmriprep --resume-from <snapshot>` performs execution-readiness checks
  against archived dataset, debug, and runtime facts before submission,
  including archived runtime resource proof. It does not accept
  current-turn resource overrides.
- `--resume-from` is the only saved snapshot selector.
- After `run-fmriprep --resume-from <snapshot>` or auto-approved `process`
  submits to Slurm, the CLI may wait silently for up to 90 seconds while it
  performs internal startup checks. Wait for the CLI to return before choosing
  the next route; do not start `run-status` during that startup window.
- `next_action` is advisory route evidence, not execution approval.
- This map intentionally does not redefine prepare eligibility or direct prepare behavior.

## User-Visible Outcome Taxonomy

Use plain-language report headings for users:

- Must fix before starting: true request-level hard stops.
- Tool can prepare: runtime items the agent can prepare or verify before a new
  readiness decision.
- Needs your confirmation: checks that require explicit user approval before
  the agent may run them.
- Warnings: reporting-only findings that do not change route selection or
  execution gating.
- Excluded data: per-subject or per-session skip reasons; they must not be
  promoted to request-level hard stops.

Keep internal group names only for agent route mapping and testable artifacts.

## Execution Status Semantics

- `submitted`: launcher accepted the submission, but no grace verification has confirmed the run is alive yet
- `launched`: grace verification confirmed the job/process is still visible after submission
- `completed`: only for truly synchronous success
- `success`: legacy/internal alias for `completed`; agents should report it as
  `completed`
- `failed`: execution attempted and failed
- `nothing_to_do`: execution found no runnable work for this saved context
- `blocked`: submission or saved execution stopped before a clean launch

`submitted`, `launched`, and `completed` exit 0. `success` is a
legacy/internal alias for `completed` and exits 0 after normalization.
`failed` and `nothing_to_do` exit 1 while preserving the payload status.

These workflow execution payload statuses describe submission or result
outcomes, not live monitoring state. Do not translate `submitted` or
`launched` into `running` unless later `run-status`, scheduler/process
evidence, or bounded log/crash evidence supports that monitoring conclusion.
