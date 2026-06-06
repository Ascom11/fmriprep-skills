# Runtime Audit Route Contract

Read this route contract after `$fmri-process` classifies the user request as a
runtime-only readiness review.

## Route Scope

`runtime-audit` checks the fMRIPrep runtime environment and writes a compact
current-request artifact plus reusable component proofs.

The archived `runtime-audit.json` is the agent-facing review snapshot. It keeps
the current runtime context, proof references, readiness, resource summary,
TemplateFlow requirements, warnings, blockers, and prepare requirements.

Reusable evidence such as normalized environment facts, image/license checks,
per-template TemplateFlow checks, container-import checks, and resource-plan
details lives in the runtime proof store referenced by the snapshot. The proof
store is schema-versioned; stale schema roots or old proof ids are not a
compatibility source. Do not treat proof ids as user-facing report headings; use
them only as CLI evidence links.

It is read-only and does not materialize images or TemplateFlow content.

## What To Read

Before the first workflow command, read:

- Use `normalized_args` from the parent path preflight when it ran. If the user
  adds new path-like or omitted runtime input, return to the parent preflight
  guard before assembling this route's CLI command.
- [../common/arguments.md](../common/arguments.md) for shared runtime proof
  inputs, environment selection, path defaults, resource fields, remote rules,
  and WSL storage checks.
- [fmriprep-args.md](fmriprep-args.md) for fMRIPrep-specific runtime inputs.
- [custom-args.md](custom-args.md) when typed fMRIPrep custom args are present.

If CLI results recommend the prepare route, report that recommendation. Switch
to [prepare-runtime.md](../common/prepare-runtime.md) only when the current user
turn explicitly approves manual preparation.

When the CLI result needs stage chain, stop point, and gate semantics, read
[workflow-gates.md](workflow-gates.md).

When audit artifacts or payload findings need a user-facing report, read
[audit-report.md](../common/audit-report.md).

## Completion

Stop after runtime findings. A `prepare-runtime` recommendation is only route
advice; it is not approval to prepare. Runtime preparation is a separate route
and does not approve execution by itself.

## Runtime Proof Inputs

Runtime audit proves selected image, FreeSurfer license, TemplateFlow cache and
container import, container runtime, executor policy, resource settings, WSL
storage checks, and fMRIPrep output-selection requirements.

Typed fMRIPrep custom args are part of the runtime signature and rendered
command. The audit proves typed allowlist rendering and signature stability only;
it does not prove the scientific or output semantics of every custom argument.
