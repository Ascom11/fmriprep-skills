# Saved Continuation Route Contract

Read this route contract after the user asks to continue, resume from a saved
audit, or use an explicit audit archive.

## Route Scope

Natural-language "continue" is not a CLI flag. Treat it as saved artifact
inspection, not execution approval. Classify the saved state as execution
candidate, prepare route, or report-and-stop before running any follow-up
command.

## What To Read

Use `normalized_args` from the parent path preflight when the continuation
request adds path-like inputs. If the turn only reuses an already validated
saved artifact path, no new preflight is needed.

Read [saved-exec.md](saved-exec.md) only when compact artifacts are execution
candidates and the current turn explicitly approves execution.

Read [prepare-runtime.md](../common/prepare-runtime.md) only if compact saved
artifacts choose the prepare route and the current turn explicitly approves
manual preparation.

Read [audit-report.md](../common/audit-report.md) when compact saved audit artifacts need
a plain-language report.

Read [workflow-gates.md](workflow-gates.md) when the saved state needs stage
chain, stop point, and gate semantics.

## Route Procedure

1. Select the saved snapshot from the explicit archive, audit id, audit
   directory, or the route's saved context. `latest.json` may help find an
   audit id or `audit_<audit_id>/` directory, but it is an index, not an
   execution snapshot.
2. Read only the compact artifacts needed for route selection and reporting:
   `dataset-audit.json` and `runtime-audit.json`.
3. Do not read `dataset-audit-debug.json`. Saved execution validates it inside
   the CLI.
4. Choose exactly one route:
   - compact saved facts are execution-clean candidates and current-turn
     execution approval exists ->
     [saved-exec.md](saved-exec.md)
   - runtime facts have prepare-required findings, no hard blocker, and
     current-turn prepare approval exists ->
     [prepare-runtime.md](../common/prepare-runtime.md)
   - blockers, missing artifacts, invalid artifacts, request mismatch, or
     stale saved facts -> report-and-stop

By itself, "继续" only continues saved-state route selection. It does not make
unverified TemplateFlow ready, and it does not authorize prepare or execution.

If a saved or fresh `runtime-audit` later reports missing commands used to
check TemplateFlow and the user then provides a bin directory, rerun
`runtime-audit --templateflow-tool-bin <bin-dir>` with the same saved inputs.
Earlier `path-probe` command evidence is advisory only; it does not replace the
new runtime audit proof.
