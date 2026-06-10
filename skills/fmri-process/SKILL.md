---
name: fmri-process
description: Use when user needs to audit, prepare, or launch pre-fMRIPrep preprocessing or explicit XCP-D postprocessing. This is the only execution router; do not use it for post-run inspection only.
---

# fMRI Process

## First Principle
User-facing output optimizes for readability first. Assume the user knows nothing about this skill's internal implementation. Explain the real-world consequence first, then add paths, commands, code blocks, or optional trace codes only as evidence. Internal route names, issue buckets, artifact keys, status tokens, schema fields, and helper document names are for agent reasoning only.

## Role

Use this skill as the parent execution router: classify the request, load the selected fMRIPrep or XCP-D route contract, run the local CLI boundary, and report plainly.
If the user only asks for logs, scheduler state, outputs, crashes, or run status, use `$fmri-followup`.

## Quick Start

At the start of a real audit, prepare, continuation, or execution request, do a preliminary route classification. For a new same-dataset thread/session, including after context compaction, locate the output root and read or create the single dataset trace before install checks or workflow commands; use [references/common/harness-trace.md](references/common/harness-trace.md) for trace rules. Then select the local Python/environment and confirm install:

```bash
python -m pip show fmri-proc-tools
```

If this fails, stop and ask the user to repair the active Python. Do not install packages automatically. For remote-host requests, this is still the selected local Python. Do not run `python -m fmri_process.cli` on the remote host. Then load the selected route contract.

## User Request Patterns

| Pattern | User wording | English equivalent | Route | Completion rule |
| --- | --- | --- | --- | --- |
| `generic request` | "处理", "预处理", "处理这个数据集", "审查一下", "先审一下", "看一下", "把风险告诉我" | "process", "preprocess", "run this dataset", "review this dataset", "take a look", "tell me the risks" | `fmriprep/process` | Run dataset + runtime audits, report findings, then pause by default. |
| `fresh-dataset-audit-request` | "审查数据集" / "检查数据集" | "audit/check the dataset" | `fmriprep/dataset-audit` | Stop after dataset findings. |
| `fresh-runtime-audit-request` | "审查环境" / "检查运行环境" | "audit/check the runtime environment" | `fmriprep/runtime-audit` | Stop after runtime findings; prepare only with current-turn prepare approval. |
| `explicit-prepare-request` | "装一下环境", "拉镜像", "下载模板文件" | "prepare the environment", "pull the image", "download template files" | `prepare-runtime` route from saved/current audit target | Prepare or verify only what that audit requires after current-turn approval; never imply execution approval. |
| `direct-run-after-clean` | "审查完直接跑", "直接跑", "通过就提交", "auto-approve" | "run directly after review" | selected route with execution approval | same-turn execution may proceed only through the matching route contract and CLI result. |
| `resume-saved-request` | "继续", "继续之前的", "从之前的 audit 继续" | "continue", "continue previous audit/run" | saved artifact inspection | Read compact saved artifacts and report the candidate route; do not treat continuation as prepare or execution approval. |
| `xcpd request` | "XCP-D", "后处理", "run-xcpd", "对 fMRIPrep derivatives 继续" | "postprocess", "run XCP-D", "continue from fMRIPrep derivatives" | `xcpd` | Run XCP-D audit first, report, and pause unless the current turn explicitly approves execution after audit passes. |

Classify as `direct-run-after-clean` only when the user explicitly authorizes same-turn execution. Complete paths, runtime values, and remote host facts are audit contract, not execution approval.
Audit results, saved continuation, and saved `next_action` values are route advice, not authorization. Direct-run approval is not prepare approval.

## Reference Loading By Request

Start with the route contract that matches the classified request. Do not pre-load detail references from another route.

If a user instruction is wrong or a parameter is unclear, read the matching
route and argument reference docs first before answering, broad searching, or
debugging. Use the table below plus the selected route contract to choose the
relevant files under `references/`; do not fall back to source inspection,
ad hoc command guessing, or command-output discovery.

| When | Route contract | Use for |
| --- | --- | --- |
| fMRIPrep process or direct-run | [references/fmriprep/route.md](references/fmriprep/route.md) | Fresh dataset + runtime review, explicit same-turn fMRIPrep execution intent, and report handoff. |
| fMRIPrep dataset audit only | [references/fmriprep/dataset-audit.md](references/fmriprep/dataset-audit.md) | Dataset-only readiness review and report handoff. |
| fMRIPrep runtime audit only | [references/fmriprep/runtime-audit.md](references/fmriprep/runtime-audit.md) | Runtime-only readiness review, prepare routing, and report handoff. |
| fMRIPrep saved continuation | [references/fmriprep/saved-continuation.md](references/fmriprep/saved-continuation.md) | Continue from compact fMRIPrep artifacts. |
| XCP-D audit, prepare, run, or continuation | [references/xcpd/route.md](references/xcpd/route.md) | Existing fMRIPrep derivatives, saved XCP-D artifacts, XCP-D prepare, and `run-xcpd`. |
| explicit environment preparation only | [references/common/prepare-runtime.md](references/common/prepare-runtime.md) | Load only after current-turn prepare approval and a current or saved audit shows image, TemplateFlow, license, or transfer preparation is needed. Do not load during dataset/runtime audit, path preflight, or audit reporting. |

## Path Preflight Before CLI

After loading the selected route contract, read [references/common/path-preflight.md](references/common/path-preflight.md) before the first workflow CLI command when the request contains path-like inputs or omitted runtime inputs that the tool can probe.

Use path preflight only to normalize path inputs. It does not decide readiness or approval, and it does not collect TemplateFlow command proof. If required user input is missing or a path/command check cannot be resolved from the request, pause and ask for that input. Do not guess, auto-fill site-private paths, or continue into audit/prepare/execution with missing required inputs.

If `preflight_decision=ready`, later route references use only the corrected `normalized_args` plus the original request context that still applies. Do not reload fallback search or unresolved-question rules inside route references unless a new path-like value is added.

For fresh fMRIPrep `dataset-audit`, run dataset-only preflight for paths only; do not ask for TemplateFlow command bins. Skip path preflight when the turn reuses an already validated saved artifact path and adds no new path-like input.

## Audit Report Before Reply

Load [references/common/audit-report.md](references/common/audit-report.md) only after audit artifacts exist and the next action is user-facing audit output. Use it for the plain-language report shape, confirmation items, and pause/next-step wording. Do not answer from the route contract, CLI payload, or raw audit JSON alone when reporting audit results.

## Operating Boundary

- The selected route contract and CLI payload are the execution boundary.
- Use the selected route contract, CLI payloads, and compact audit artifacts;
  do not hand-build replacements for path checking, audit, preparation proof,
  saved execution, or execution.
- Manual prepare requires explicit current-turn prepare approval; direct-run approval does not authorize manual prepare.
- Same-turn execution requires explicit current-turn execution approval such as "run after review passes".
- Audit route advice, saved continuation, and saved `next_action` values never authorize prepare or execution by themselves.
- fMRIPrep and XCP-D readiness stay separate. fMRIPrep artifacts may seed XCP-D context, but fresh XCP-D artifacts are still required before `run-xcpd`.
- fMRIPrep never auto-routes to XCP-D after `run-fmriprep`; require explicit user intent.
- Before any user-facing report or reply, confirm the same-dataset harness trace
  has been created and the current action, status, or user correction has been
  appended when applicable. If it is missing or stale, update it with the
  harness trace guide before replying.
- If the selected route contract or CLI payload is ambiguous, stop and explain the ambiguity. If the CLI reports a hard stop, do not bypass it.
- Default source boundary: do not open, read, inspect, or edit package source
  files by default. The agent must not read or modify Python implementation
  code by default. Only inspect or change source files when the user explicitly
  asks for source-level debugging or code changes.
- Default failure boundary: when a route command, prepare command, execution, or saved-continuation step errors, report the CLI payload and allowed next route. Do not start manual debugging, broad file searches, or opening implementation files unless the user explicitly asks for debugging/recovery.

## Reporting Contract

First principle: user-facing output readability comes before completeness. Use plain language and no internal terms. Keep internal field names out of headings and advice. Do not use raw JSON field names, route tokens, bucket names, raw status headings, or CLI flags as section headings or advice.
Write as if the user has never seen this skill or its implementation. Translate every internal term into the user-visible consequence before replying. If an exact command, path, or log line is required, introduce it with a natural language sentence and keep it as supporting evidence.
Do not send the report until the harness trace has been checked and updated for
the current dataset session when applicable.

For audit artifacts, explain the current conclusion, what is not ready, what the tool can prepare, what needs user confirmation, and the next step. Path-preflight pauses are not audit artifact reports; handle them through the path-preflight reference loaded earlier in the route.

Every successful execution reply must include the exact container command from the payload. Use [execution-report.md](references/common/execution-report.md) for the required shape.
