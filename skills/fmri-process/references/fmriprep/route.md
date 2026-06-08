# Process Route Contract

Read this route contract after `$fmri-process` classifies the user request as
fresh `process` or direct-run-after-clean.

## Route Scope

`process` runs the full pre-fMRIPrep readiness review for the effective
request. It runs dataset and runtime checks, writes a new audit snapshot, and
pauses by default. Direct-run-after-clean may continue in the same turn only
when the user explicitly asked for same-turn execution and the CLI result
permits it. Direct-run approval is not prepare approval.

Within the same troubleshooting thread, if a current audit snapshot still
matches the intended dataset and only runtime inputs changed, prefer
`process --reuse-dataset-from <snapshot> --reaudit-runtime` over a fresh
`process`. Dataset facts usually change less often than runtime facts. Start a
fresh `process` for a new thread, a changed dataset/output selector, or when
the saved dataset/storage signature no longer describes the request.

## What To Read

Before the first workflow command, read:

- Use `normalized_args` from the parent path preflight when it ran. If the user
  adds new path-like input, return to the parent preflight guard before
  assembling this route's CLI command.
- Read [../common/arguments.md](../common/arguments.md) for shared workflow
  arguments and invocation rules.
- Read [fmriprep-args.md](fmriprep-args.md) for fMRIPrep-specific arguments.

When the CLI result needs gate, stage chain, stop point, and gate semantics,
read [workflow-gates.md](workflow-gates.md).

If CLI results that choose the prepare route appear, switch to
[prepare-runtime.md](../common/prepare-runtime.md).

When audit artifacts or payload findings need a user-facing report, read
[audit-report.md](../common/audit-report.md).

When a successful execution payload returns, read
[execution-report.md](../common/execution-report.md).

## Process-Only Arguments

- `--auto-approve`: only when the user explicitly authorizes
  "review then run". It opens same-turn execution only when the CLI gate is
  execution-clean. Warning-only findings must be reported, but they do not
  block direct-run. Hard blockers or runtime preparation requirements still
  pause the route. Auto-approve is not permission to run manual prepare work.
- `--run-id <run-id>`: log grouping key for this route. It does not select or
  reuse audits.
- `--scheduler-partition <partition>`: Slurm partition override for this route.
  Do not use it to bypass audit-reported scheduler or node constraints.
- `--reuse-dataset-from <snapshot>` with `--reaudit-runtime`: selective
  runtime re-audit path when dataset facts should be reused and runtime facts
  changed. Use runtime re-audit only when the saved dataset and storage
  signature still match. The snapshot may be an audit id, audit directory, or
  `dataset-audit.json`; `latest.json` is not accepted for
  `--reuse-dataset-from`.
- `--reaudit-runtime`: valid only with `--reuse-dataset-from <snapshot>`.
  Do not add `--reaudit-runtime` to a fresh `process`; fresh `process` already
  runs both audits.
  Be very conservative about `--reaudit-runtime`: use it only for narrow
  runtime-only changes after confirming the saved dataset and output-selection
  facts still describe the intended request.

There is no `--reaudit-dataset` flag. Re-auditing dataset facts means running
a fresh `process` request.

Treat dataset/output locator, subject/session, and output-selection changes as
fresh `process` inputs. Changing from no CIFTI to `--cifti-output 91k` changes
the storage/output-selection signature, so it needs a fresh `process`, not
runtime-only reuse. Do not use `--reuse-dataset-from ... --reaudit-runtime` as
a broad compatibility patch.

## Command Template

Generic process:

```bash
python -m fmri_process.cli process \
  --bids-root <bids-root> \
  --output-root <output-root> \
  --templateflow-tool-bin <bin-dir> \
  --subject <selector>
```

Remote process:

```bash
python -m fmri_process.cli process \
  --bids-root <bids-root> \
  --output-root <output-root> \
  --templateflow-tool-bin <bin-dir> \
  --subject <selector> \
  --remote-host <remote-host>
```

Process with supported fMRIPrep custom args:

```bash
python -m fmri_process.cli process \
  --bids-root <bids-root> \
  --output-root <output-root> \
  --templateflow-tool-bin <bin-dir> \
  --subject <selector> \
  --fmriprep-custom-arg dummy_scans=4 \
  --fmriprep-custom-arg random_seed=123
```

Use `--fmriprep-custom-arg key=value` for allowlisted custom args. Do not
append raw fMRIPrep shell args or invent wrapper flags. The generic custom-arg
form is the canonical route for config-translated values.

## Completion

After `process` produces dataset/runtime audit artifacts, report the current
conclusion in plain language. If preprocessing is not ready, explain what is
missing, what the tool can prepare, what needs user confirmation, and the next
step.

A parent path-preflight pause is earlier than this completion point. Use the
path-preflight pause wording instead of asking for prepare approval or
describing tool-preparable audit findings.
