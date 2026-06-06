# XCP-D Route

Read this after `$fmri-process` classifies the request as explicit XCP-D,
postprocessing, `run-xcpd`, or continuation from existing fMRIPrep
derivatives.

The selected XCP-D route contract and CLI payload are the execution boundary.
Do not hand-build replacements for path checking, audit, preparation proof,
saved artifact selection, or execution.

## Route Chain

1. Use `normalized_args` from the parent path preflight when it ran. If the
   user adds new path-like or omitted runtime input, return to the parent
   preflight guard before assembling XCP-D CLI commands.
2. Read [artifacts.md](artifacts.md) for XCP-D artifact boundaries.
3. Read [../common/arguments.md](../common/arguments.md) and
   [xcpd-args.md](xcpd-args.md) before command assembly.
4. Read [xcpd-audit.md](xcpd-audit.md) before `xcpd-audit`, unless the user
   explicitly asks to reuse saved XCP-D artifacts. By default, run
   `xcpd-audit`, report the result, and pause.
5. If runtime prepare is needed, report the listed XCP-D requirements and ask
   for current-turn prepare approval. Prepare only after that approval, then
   run `prepare-probe --target xcpd`. When `prepare-probe` reports ready,
   rerun `xcpd-audit`.
6. Treat ready saved XCP-D dataset and runtime artifacts as run candidates.
   Run `run-xcpd` in the same turn only when the user explicitly approved
   execution after audit passes.
7. After successful execution, use
   [../common/execution-report.md](../common/execution-report.md) for the final
   user reply.
8. Route post-launch inspection to `$fmri-followup`.

## State Table

| State | Next step |
| --- | --- |
| No current XCP-D artifacts | Read [xcpd-audit.md](xcpd-audit.md). Run `xcpd-audit`, report, and pause. |
| XCP-D runtime needs prepare | Report the listed requirement and request current-turn prepare approval; do not implicitly audit -> prepare -> run. |
| Saved XCP-D artifacts are ready | Report ready; run only with current-turn execution approval. |
| Saved XCP-D artifacts are missing, stale, or mismatched | Rerun `xcpd-audit`. |
| XCP-D run failed | Apply the [run-xcpd.md](run-xcpd.md) retry policy first when retry attempts remain. After retries are exhausted, report the failure payload first, do bounded stderr triage, and if stderr suggests dataset/runtime drift, read-only TemplateFlow, no network, or missing runtime assets, recommend re-audit before recovery or rerun. Route deeper log, crash, output, or status investigation to `$fmri-followup` first. Do not start re-audit, prepare, rerun, or manual probes from `$fmri-process` unless the user explicitly asks for recovery. |
| XCP-D launched or submitted | Report launch result; use `$fmri-followup` only for explicit inspection. |

## Boundaries

- Fresh XCP-D artifacts remain required. Agents read only the compact pair:
  `xcpd-runtime-audit.json` and `xcpd-dataset-audit.json`. The CLI may validate
  `xcpd-dataset-audit-debug.json` internally.
- `xcpd-audit --reuse-context-from <audit>` may seed locator/runtime context
  from fMRIPrep artifacts. It is not XCP-D readiness proof.
- `run-xcpd` consumes saved XCP-D artifacts only; this is the saved-artifact
  execution contract. Runtime-looking current-turn values may validate a saved
  signature only; they must not replace it.
- Do not rerun fMRIPrep from this route.
- Do not use remote SSH Python execution for this CLI.
- Do not bypass TemplateFlow findings with ad hoc environment injection.
- Do not run broad `find`, broad `rg`, or manual remote directory scans to
  replace `path-probe --target xcpd`.

## Detail References

- [../common/arguments.md](../common/arguments.md): shared parameter surface.
- [xcpd-args.md](xcpd-args.md): XCP-D-specific parameter surface.
- [custom-args.md](custom-args.md): typed XCP-D custom-args allowlist and
  rejected/deferred surfaces.
- [artifacts.md](artifacts.md): artifact names, reuse keys, subject source.
- [xcpd-audit.md](xcpd-audit.md): fresh audit command.
- [run-xcpd.md](run-xcpd.md): saved-artifact execution command.
