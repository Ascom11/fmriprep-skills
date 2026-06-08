# `run-xcpd` Action Reference

Read this file only after saved XCP-D dataset and runtime artifacts are ready.
Ready artifacts are run candidates, not execution approval. Run in the current
turn only when the user explicitly approved execution after audit passes.
After a successful execution payload returns, read
[../common/execution-report.md](../common/execution-report.md) and include the
exact single-subject container command in the user reply.
Read [../common/saved-execution.md](../common/saved-execution.md) for shared
retry, failure handoff, artifact stability, and `run-id` rules.

## Purpose

`run-xcpd` submits or launches XCP-D from archived XCP-D artifacts. It does not
reuse fMRIPrep execution artifacts and does not repair runtime assets.
It requires an explicit saved XCP-D audit selector.

## Parameter Details

Use [../common/arguments.md](../common/arguments.md) for shared locator and
remote fields. `--resume-from <audit_id|audit_dir|artifact.json>` is required;
do not rely on `latest.json` for execution.

Use [xcpd-args.md](xcpd-args.md) for XCP-D-specific fields:

- `--xcpd-mode abcd|nichart`
- `--xcpd-min-time <seconds>`
- `--xcpd-task-id <task>`
- `--xcpd-bids-filter-file <json>`
- `--xcpd-dataset <alias=path>`
- `--xcpd-mem-mb <mb>`
- `--xcpd-motion-filter-type lp|notch|none`
- `--xcpd-band-stop-min <bpm>`
- `--xcpd-band-stop-max <bpm>`
- `--xcpd-motion-filter-order <order>`
- `--xcpd-despike y|n`
- `--run-id <run-id>`

When `--xcpd-min-time` is omitted, the wrapper uses `240` for `abcd` and `0`
for `nichart`.

Runtime-looking and filter arguments exposed by the CLI, such as
`--fs-license`, `--templateflow-tool-bin`, `--xcpd-image`, resource flags,
backend flags, `--xcpd-mode`, and motion-filter flags, are
matching/validation-only for the selected saved signature. They must not
repair, override, or update saved XCP-D artifacts.
Omitted saved XCP-D FreeSurfer license and advisory TemplateFlow proofs do not
block saved replay. If the saved request supplied `--fs-license`, that license
proof must still validate.
When the saved XCP-D TemplateFlow container-import proof is not ready,
`run-xcpd` skips the `/templateflow` bind instead of mounting a stale or missing
source path and records warning `xcpd_templateflow_bind_skipped` in the
execution plan. XCP-D may then fall back to its container-default TemplateFlow
cache and can still fail later if that cache lacks required resources.
`--bids-root` is not required for saved XCP-D execution when the selected
audit already records `fmriprep_derivatives`.

The CLI validates execution subject scope from saved XCP-D artifacts, including
its internal debug artifact. Agents must not open
`xcpd-dataset-audit-debug.json`. Do not add current-turn subject or session
flags. Do not pass `--reuse-context-from` to `run-xcpd`.

Current-turn subject, session, reuse-context, and runtime override values must
not change saved execution. If any of those values need to change, rerun
`xcpd-audit`.

XCP-D custom args must be added during the fresh `xcpd-audit` step with
`--xcpd-custom-arg key=value`; read [custom-args.md](custom-args.md) before
accepting a key. `run-xcpd` inherits the saved `xcpd_custom_args` signature.
Do not add new custom args at `run-xcpd`; rerun `xcpd-audit` if the user wants
different custom args. A repeated `--xcpd-custom-arg` on `run-xcpd` is only a
current-turn signature assertion and must match the saved audit.

## Command Templates

Local:

```bash
python -m fmri_process.cli run-xcpd \
  --output-root <output-root> \
  --resume-from <audit_id>
```

Remote:

```bash
python -m fmri_process.cli run-xcpd \
  --output-root <output-root> \
  --resume-from <audit_id> \
  --remote-host <remote-host>
```

Remote path semantics follow [../common/arguments.md](../common/arguments.md).
