# Harness Trace

Use this guide for agent handoff only. Harness trace is a short Markdown log
that helps a new thread/session recover the dataset context. It is not a
pipeline feature.

## Path

Use exactly one trace file per dataset output root:

```text
<dataset-output-root>/_artifacts/harness-trace.md
```

Do not create target-specific variants:

```text
<dataset-output-root>/_artifacts/harness-trace-fmriprep.md
<dataset-output-root>/_artifacts/harness-trace-xcpd.md
```

Use the same trace for fMRIPrep and XCP-D work on the dataset so XCP-D can
reuse prior selectors, status evidence, paths, and findings.

## Read And Write Rules

- At the start of a new thread/session, including after context compaction,
  locate this trace before running the first workflow or status command for
  the same dataset.
- If the trace exists, read the whole trace once to recover context.
- If the trace does not exist, create it before the first workflow or status
  command. Record the user's raw request, goal, constraints, scope, and the
  next command.
- Preserve the user's preferred language for trace notes. If the user writes
  Chinese, keep the trace entry in Chinese unless exact command output must
  stay English.
- Wrap Markdown-sensitive tokens in backticks, including skill names such as
  `$fmri-process`, `$fmri-followup`, route names, CLI flags, command names,
  schema keys, status tokens, and paths.
- After every `run-status`, write one short `Status Log` entry summarizing the
  JSON result. Keep only the first and latest two `run-status` entries per
  target so fMRIPrep context remains available when XCP-D monitoring starts.
  Move important intermediate transitions into `Findings`.
- When the user corrects path, scope, selector, status interpretation, or
  workflow constraints, append a `user-correction` entry before acting on the
  corrected instruction.
- Non-status updates must not read the whole trace first. Open the file for
  append/write only.
- Read the whole trace only for context recovery.
- Do not maintain a mutable top-level `Current State`. The latest state for a
  target is the latest retained `Status Log` entry for that target.

## Compression

Trace compression threshold: 50 KiB.

- After recording a `run-status`, spawn a subagent to summarize and compact
  when either condition is true: successful completion was recorded, or the
  retained trace is larger than 50 KiB. Failed or still-running status checks
  do not trigger compression unless the 50 KiB fallback applies.
- The main agent must not read the full trace for compression.
- Only the compression subagent may read and rewrite the full trace for
  whole-trace summarization.
- The compacted trace must preserve raw user request, scope, all user
  corrections, findings, open items, and each target's retained `run-status`
  entries. Preserve latest selectors through each target's latest retained
  status entry.
- The triggering `run-status` is already recorded before compression. The
  compacted trace must retain it, not append a duplicate afterward.

## Write Examples

Use the fixed helper script in this directory. Pass values as arguments; do not
copy inline Python append snippets into normal workflow notes. The script writes
UTF-8/LF, creates parent directories, creates `# Harness Trace` only when the
target file is absent or empty, and enforces the retained `run-status` window.
When an existing trace does not end with a
newline, the script writes one newline before the new entry so entries do not
run together.

Free-text fields such as `--raw`, `--goal`, `--constraints`, `--evidence`,
`--action`, and `--next` are written as fenced Markdown blocks. This protects
user wording that contains backticks, pipes, brackets, angle brackets, shell
redirection, or other Markdown-sensitive characters.

The helper is stdlib-only; no `PYTHONPATH` is required. Use an explicit script
path because cwd may not be the fmri-process skill directory.

Local or native Windows runs:

```bash
python <fmri-process-skill-dir>/references/common/append-harness-trace.py \
  --trace-path "<dataset-output-root>/_artifacts/harness-trace.md" \
  --entry-kind run-status \
  --target "<fmriprep|xcpd>" \
  --audit-id "<id>" \
  --submission-id "<id|none>" \
  --status "<running|queued|launched-but-not-visible|failed|completed|unknown|target-ambiguous|submission-ambiguous>" \
  --evidence "<summary>" \
  --next "<one action>"
```

For remote runs, write the trace on the remote filesystem under the
target-visible dataset output root. Do not create a local mirror trace for a
remote output root. A local agent may use SSH only for this small file append;
the fMRI CLI still runs locally.

```bash
python <fmri-process-skill-dir>/references/common/append-harness-trace.py \
  --remote-host "<remote-host>" \
  --trace-path "<dataset-output-root>/_artifacts/harness-trace.md" \
  --entry-kind run-status \
  --target "<fmriprep|xcpd>" \
  --audit-id "<id>" \
  --submission-id "<id|none>" \
  --status "<running|queued|launched-but-not-visible|failed|completed|unknown|target-ambiguous|submission-ambiguous>" \
  --evidence "<summary>" \
  --next "<one action>"
```

Initial trace creation:

```bash
python <fmri-process-skill-dir>/references/common/append-harness-trace.py \
  --trace-path "<dataset-output-root>/_artifacts/harness-trace.md" \
  --entry-kind init \
  --raw "<user raw request>" \
  --goal "<goal>" \
  --constraints "<paths/remote/prohibitions>" \
  --dataset "<dataset>" \
  --output-root "<output-root>" \
  --remote "<host|local>" \
  --next-command "<command>"
```

User correction:

```bash
python <fmri-process-skill-dir>/references/common/append-harness-trace.py \
  --trace-path "<dataset-output-root>/_artifacts/harness-trace.md" \
  --entry-kind user-correction \
  --raw "<user correction>" \
  --applies-to "<path|scope|selector|status|constraint|other>" \
  --action "<what changed in agent behavior>" \
  --next "<one action>"
```

## Template

```md
# Harness Trace

## User Request
- Raw:
```
<user raw request>
```
- Goal:
```
<goal>
```
- Constraints:
```
<paths/remote/prohibitions>
```

## Scope
- Dataset: `<dataset>`
- Output root: `<output-root>`
- Remote: `<host|local>`

## Status Log

### <timestamp> init
- State: trace created.
- Next: run `<command>`.

### <timestamp> run-status
- Target: `<fmriprep|xcpd>`
- Selector: `audit_id=<id>`, `submission_id=<id|none>`
- Status: `<running|queued|launched-but-not-visible|failed|completed|unknown|target-ambiguous|submission-ambiguous>`
- Evidence:
```
<scheduler/log/crash summary>
```
- Next:
```
<one action>
```

### <timestamp> user-correction
- Raw:
```
<user correction>
```
- Applies to: `<path|scope|selector|status|constraint|other>`
- Action:
```
<what changed in agent behavior>
```
- Next:
```
<one action>
```

## Findings
- <short fact>

## Open Items
- <item to confirm>
```

## Boundary

Harness trace never participates in:

- fMRIPrep or XCP-D readiness gates
- artifact validation
- saved-artifact replay
- runtime proofs
- `latest.json`
- `run-status` status semantics or JSON output

The CLI still owns workflow state. The agent/harness may summarize CLI JSON
into trace Markdown, but must not feed trace Markdown back into gate,
readiness, replay, or execution decisions.
