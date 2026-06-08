# `xcpd-audit` Action Reference

Read this file before running an XCP-D audit.

## Purpose

`xcpd-audit` checks XCP-D-specific dataset readiness and runtime readiness. It
writes `xcpd-runtime-audit.json`, `xcpd-dataset-audit.json`, and
`xcpd-dataset-audit-debug.json`, then stops.

By default, report the audit result and pause. A ready result is a run
candidate, not execution approval. Same-turn `run-xcpd` requires explicit
current-turn approval to run after the audit passes.

## Parameter Details

Use [../common/arguments.md](../common/arguments.md) for shared locator,
selector, config, remote, runtime proof, resource, and storage fields.

Use [xcpd-args.md](xcpd-args.md) for XCP-D-specific fields:

- `--xcpd-image <xcpd-image>`
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
- `--reuse-context-from <audit>`
- `--scheduler-partition <partition>`
- `--run-id <run-id>`

When `--xcpd-min-time` is omitted, the wrapper uses `240` for `abcd` and `0`
for `nichart`.

XCP-D custom args in config must be translated into repeatable
`--xcpd-custom-arg key=value` CLI entries. Read
[custom-args.md](custom-args.md) before accepting a key.

## Command Template

```bash
python -m fmri_process.cli xcpd-audit \
  --fmriprep-derivatives <fmriprep-derivatives> \
  --output-root <output-root> \
  --reuse-context-from <fmriprep-audit-id> \
  --subject <selector> \
  --xcpd-mode abcd \
  --xcpd-custom-arg smoothing=4 \
  --xcpd-custom-arg low_mem=true
```

Translate config custom args into the same CLI form:

```yaml
xcpd:
  custom-args:
    smoothing: 4
    low-mem: true
```

Do not pass the YAML file to the CLI. Do not invent wrapper flags such as
`--xcpd-smoothing` or `--xcpd-low-mem`, and do not append raw trailing XCP-D
args. After the fresh audit is ready, `run-xcpd` consumes the saved
`xcpd_custom_args` signature; it does not add or replace custom args during
saved execution.

Raw `--bids-root` is optional XCP-D context when
`--fmriprep-derivatives` is provided. Add `--fs-license`,
`--templateflow-home`, `--templateflow-tool-bin`, or `--xcpd-image` only when
the user supplied those runtime assets or wants the audit to validate a saved
signature against them.

Read [xcpd-args.md](xcpd-args.md#gate-categories) before explaining blockers.
Do not call TemplateFlow or omitted FreeSurfer license an XCP-D input blocker.

If the audit reports wrapper execution gates such as image or runtime
`needs_prepare`, report the listed `prepare_requirements` and request
current-turn prepare approval. Do not implicitly continue from audit to prepare
or run. After approved preparation, rerun `xcpd-audit` before any `run-xcpd`.
