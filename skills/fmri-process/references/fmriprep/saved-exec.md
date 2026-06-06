# Pre-fMRIPrep Saved Execution

Read this reference only after [saved-continuation.md](saved-continuation.md)
selects saved execution, or after the user explicitly selects a saved audit
archive for execution.

After a successful execution payload returns, read
[../common/execution-report.md](../common/execution-report.md) and include the
exact single-subject container command in the user reply.
Read [../common/saved-execution.md](../common/saved-execution.md) for shared
retry, failure handoff, artifact stability, and `run-id` rules.

## Parameter Details

Saved execution uses `run-fmriprep`:

- `--bids-root <bids-root>`: BIDS root for resolving default output/archive
  paths.
- `--output-root <output-root>`: output root that contains
  `_artifacts/fmriprep_audit`.
- `--remote-host <remote-host>`: local CLI with remote target paths. Remote
  resume paths are interpreted on the remote filesystem only when this flag is
  present.
- `--resume-from <audit_id|audit_dir|artifact.json>`: required saved snapshot
  selector for execution.
- `--scheduler-partition <partition>`: optional Slurm partition override for
  the launch.
- `--run-id <run-id>`: log grouping key for the new launch only.

## Boundary

`run-fmriprep --resume-from <snapshot>` performs the full archived artifact
validation, including debug artifact schema, request signature, status, and
execution readiness. If the debug artifact is missing, invalid, stale, or not
ready, report the CLI artifact-validation error and stop.

## Saved Artifact Templates

On Windows Git Bash/MSYS, apply the shared `MSYS_NO_PATHCONV=1` invocation rule
to these templates.

Saved execution from an audit id:

```bash
python -m fmri_process.cli run-fmriprep \
  --output-root <output-root> \
  --resume-from <audit_id>
```

Saved execution from an audit directory:

```bash
python -m fmri_process.cli run-fmriprep \
  --resume-from <audit_dir>
```

Saved execution from an explicit artifact file:

```bash
python -m fmri_process.cli run-fmriprep \
  --resume-from <artifact.json>
```

Remote saved execution:

```bash
python -m fmri_process.cli run-fmriprep \
  --remote-host <remote-host> \
  --output-root <remote-output-root> \
  --resume-from <remote-audit-id-or-dir-or-artifact.json>
```

Remote `--resume-from` paths are interpreted on the remote filesystem only when
the same command includes `--remote-host <remote-host>`.

`--resume-from` is the only saved snapshot selector. `--run-id` may label logs
or submission grouping for the new command, but it must not select, reuse, or
invalidate saved audit artifacts.

## Post-Submit Startup Wait

After `run-fmriprep --resume-from <snapshot>` submits a Slurm job, the CLI may
produce no terminal output while it performs internal startup checks. This
silent wait is bounded by the CLI startup window, currently up to 90 seconds.

The same startup wait can occur when an auto-approved `process` reaches Slurm
submission. Wait for the CLI command to return before deciding whether to
report launch, failure, or the next route. Do not start `run-status` during
this 90-second window to race the archive or scheduler state.

## Artifact And Run Boundary

Saved continuation is artifact inspection, not a `process` flag. Do not
preserve hidden compatibility flags or executable recovery aliases.

`run-fmriprep --resume-from <snapshot>` consumes archived audit artifacts only.
It must not run fresh audits, run prepare, or clear blockers with current-turn
runtime overrides. Current-turn arguments may locate the saved snapshot, but
they must not supply fresh fMRIPrep selectors, output selection, recon mode, or
typed custom args.
Recon mode is part of the saved artifact contract. If the saved audit was
created with `--fs-no-reconall`, saved execution replays that mode and does not
accept a current-turn recon-mode override. It still relies on the saved
FreeSurfer license path and `license.freesurfer` proof.

The CLI performs the exact request-signature comparison, but it separates two
identities. Runtime readiness identity excludes selected subjects and sessions;
it is about runtime paths, image, backend, TemplateFlow proof, archived
runtime resource proof, and storage settings. Saved execution snapshot consistency
still validates archived dataset, debug, and runtime artifacts together.
If dataset facts, storage signature, output selection, or recon mode changed,
run a fresh `process` request. If only runtime facts or resource values changed
and the old dataset audit is still the intended dataset snapshot, use
`process --reuse-dataset-from <audit_id|audit_dir|dataset-audit.json>
--reaudit-runtime` to create a new audit id with copied dataset artifacts and a
runtime snapshot assembled from reusable component proofs. The CLI refreshes
only missing, non-ready, stale, schema-incompatible, or signature-changed
runtime components. Be very conservative about `--reaudit-runtime`: use runtime
re-audit only when the saved dataset and storage signature still match. Do not
use `--reuse-dataset-from ... --reaudit-runtime` as a broad compatibility patch.

`_artifacts/fmriprep_audit/` keeps `latest.json` and `audit_<audit_id>/` under
the selected output root. `latest.json` is an index/discovery aid, not a legal
`run-fmriprep --resume-from` target. `latest.json` is not accepted for
`--reuse-dataset-from` either. XCP-D writes its own snapshots under
`_artifacts/xcpd_audit/`.

## Blocked Snapshot Recovery

Blocked snapshots are not saved execution targets. If a blocked snapshot needs
a corrected runtime input such as `--fs-license`, `--fmriprep-image`,
`--templateflow-home`, `--container-runtime`, or `--executor-policy`, reuse the
same dataset snapshot with a fresh runtime audit instead of adding the input to
saved execution:

```bash
python -m fmri_process.cli process \
  --bids-root <bids-root> \
  --output-root <output-root> \
  --remote-host <remote-host> \
  --subject <selector> \
  --fs-license <fs-license> \
  --reuse-dataset-from <audit_id|audit_dir|dataset-audit.json> \
  --reaudit-runtime
```

## Saved Artifact Notes

- keep the same BIDS/output roots and selector inputs when reusing saved
  audits, unless the user intentionally selects a different explicit snapshot
- do not add `--session` unless the user explicitly constrains it
- do not wrap the CLI inside remote SSH Python execution
- `--run-id` groups logs; it does not decide whether an audit snapshot is
  reusable or selected
- saved `needs_prepare` runtime artifacts are not legal execution targets; use
  the saved-continuation route to choose prepare, fresh audit, or report-and-stop
- if the user later supplies an explicit image path or registry reference, that
  override changes the runtime signature and must route through fresh runtime
  re-audit before any saved execution can run
