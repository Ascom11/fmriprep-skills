# Prepare Runtime Route

## Route Scope

Use this reference only after the current user turn explicitly approves manual
preparation. A CLI payload that reports `next_action: prepare-runtime` is a
recommendation to report, not approval to prepare.

`prepare-runtime` is a route token, not a `fmri_process.cli` command. The agent
does only the current audit's required preparation or verification work, then
returns to CLI `prepare-probe --target <fmriprep|xcpd>`.

Do not run `prepare-probe` at the start of this route. `prepare-probe` is the
final read-only proof after the documented image, TemplateFlow, or license
action is complete.

This route prepares runtime assets only. It does not repair or materialize
subject/session dataset payloads.

## Inputs

Use the current `runtime-audit` payload as the preparation source. For fMRIPrep
this is `runtime-audit.json`; for XCP-D this is `xcpd-runtime-audit.json`.

Start from `runtime-audit.prepare_requirements`. The `prepare_requirements`
array is the structured prepare contract. Do not reconstruct a plan from
warnings, prose, defaults, or directory names. Do not start explicit prepare
without a current or saved `runtime-audit` payload. Do not invent image,
TemplateFlow, license, download, source, or target paths.

Always carry the saved-audit values that still apply:

- BIDS root, output root, and remote host
- FreeSurfer license, image, TemplateFlow, download, work, and log paths
- container runtime, executor policy, resource fields, and output selection

subject selectors and sessions only when rerunning `process` or
`dataset-audit`, not when running `prepare-probe`.

Use [cli.md](cli.md) for shared local CLI, remote-host, environment, and saved
path boundaries.

Remote preparation may run documented image, TemplateFlow, or image-upload
recovery commands in the remote shell when those commands materialize
target-visible assets. It does not permit remote Python CLI execution. `process`,
`runtime-audit`, `prepare-probe`, and `run-fmriprep --resume-from` remain local
CLI boundaries.

## Dispatcher

Use this table before running commands:

| prepare_requirements.kind/code | Detail reference | Final proof command after prepare | Next route |
| --- | --- | --- | --- |
| `kind: image` / `prepare_runtime_required_<pipeline>_image` | [prepare-image.md](prepare-image.md) | `prepare-probe --target <target> --kind image` | rerun the matching audit |
| `kind: templateflow` / `prepare_runtime_required_templateflow_cache` | [prepare-templateflow.md](prepare-templateflow.md) | `prepare-probe --target <target> --kind templateflow` | rerun the matching audit |
| `kind: templateflow` / `prepare_runtime_required_templateflow_container_import` | [prepare-templateflow.md](prepare-templateflow.md) | `prepare-probe --target <target> --kind templateflow` | rerun the matching audit |

For `kind: image`, read [prepare-image.md](prepare-image.md). For
`kind: templateflow`, read
[prepare-templateflow.md](prepare-templateflow.md). Do not read both detail
files unless both kinds are present.

When several prepare requirements are present, satisfy them in this order:

1. Prepare all image requirements.
2. Run TemplateFlow container-import checks.
3. Materialize or repair every required TemplateFlow target.
4. Run `prepare-probe --kind all` by default. Use `--kind all` after multiple
   item changes.

Use `prepare-probe --kind image` or `--kind templateflow` when exactly one
atomic prepare target changed. Use `--kind license` only for read-only license
verification after the user supplies or corrects a license path. Never run
these `prepare-probe` variants before the corresponding prepare or correction
step. A partial successful probe closes only the item it proves.

## Command Assembly Safety

Do not use `eval`. Prefer local argv-array execution for local commands. When
a shell is unavoidable, quote every path, image reference, target, bin
directory, and remote host. Reject or pause before using values that contain
newlines or shell control characters.

Do not directly interpolate user values into nested `ssh` commands. Build the
remote command from validated pieces, quote target-side values, and pass only
the final command to SSH.

## Hard Stops

- `runtime-audit` is read-only and never downloads images or TemplateFlow.
- SSH/probe transport failure is a hard stop.
- Unknown image or TemplateFlow targets are hard stops.
- Do not use broad `find`, broad `rg`, or manual directory sweeps.
- Do not use delete semantics in image-upload recovery commands.
- Manual prepare does not grant execution approval.
- Direct-run approval or `--auto-approve` does not grant manual preparation
  approval.
- Audit results, saved continuation, and saved `next_action` values do not
  grant manual preparation approval.

## Parameter Details

`prepare-probe` accepts only these parameters:

- `--target fmriprep`: select fMRIPrep saved runtime audits.
- `--target xcpd`: select XCP-D saved runtime audits.
- `--bids-root <bids-root>`: target-visible BIDS root used to locate saved artifacts.
- `--output-root <output-root>`: output root used to locate saved artifacts.
- `--remote-host <remote-host>`: remote target for saved-artifact lookup and read-only probes.
- `--from-runtime-audit <snapshot>`: audit id, audit directory, or runtime
  audit JSON path.
- `--kind image|templateflow|license|all`: proof scope. Use `all` by default
  after multiple prepare items. `license` is verification-only, not preparation.

`prepare-probe` does not accept config. `prepare-probe` does not accept
`--templateflow-tool-bin`. It also does not accept `--templateflow-home`,
image, license, resource, or runtime override arguments. It reads saved runtime audit values only.

If paths, TemplateFlow tool bins, images, license, runtime, or resources need
to change, rerun path preflight and the matching audit. Do not repair tool
discovery or current-turn runtime inputs inside `prepare-probe`.

For XCP-D, use `--target xcpd` and an `xcpd-runtime-audit.json` selector.

## Required Verification

After manual preparation, run `prepare-probe` against the saved runtime audit.

```bash
python -m fmri_process.cli prepare-probe \
  --target fmriprep \
  --bids-root "${bids_root}" \
  --output-root "${output_root}" \
  --from-runtime-audit "${runtime_audit_ref}" \
  --kind all
```

Use `--kind image` or `--kind templateflow` when only one prepared target
changed. Use `--kind license` only after the user supplies or corrects a license
path. The probe is read-only. It does not download, mutate, or approve
execution. `--kind image` does not prove TemplateFlow readiness.

Continue only when `prepare-probe` reports `status: ready`. If it reports
blockers, report the failing atomic check and stop.

`prepare-probe` is readiness evidence only; it is not an execution snapshot and
does not update the runtime audit artifact.
Before manual prepare, `prepare-probe` is not a discovery shortcut and not a
replacement for following the detail reference. After manual prepare, the only
allowed next steps are `prepare-probe` and then the matching audit route. Do
not run fMRIPrep or XCP-D directly from this route. To execute later, run a
fresh `process` request with the same BIDS/output roots, dataset scope, paths,
runtime options, and TemplateFlow check choices, or run
`process --reuse-dataset-from <audit_id|audit_dir|dataset-audit.json> --reaudit-runtime`
when dataset reuse is still valid.

Do not patch runtime findings into the old audit directory. Do not reuse an old `audit_id` for a changed runtime.
`--run-id` remains only a log grouping key.
