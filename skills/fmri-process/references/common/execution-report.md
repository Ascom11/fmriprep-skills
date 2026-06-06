# Execution Report

## Purpose

Use this reference after a successful fMRIPrep or XCP-D execution payload
returns from the CLI. It covers final user-facing reporting only.

## Successful Execution Report

For a successful fMRIPrep or XCP-D execution payload, `submitted`, `launched`,
and `completed` exit 0. `success` is a legacy/internal alias for `completed`;
treat it as completed if it appears in old artifacts or internal logs.

Inspect `summary.execution.single_subject_command` and report it as the
single-subject container command; preserve every token, value, quoting, and
order from that field. This command is the actual container invocation for one
runnable subject; it is not a Slurm wrapper, not a launcher script, not a
command template, and not a mandatory manual rerun step.

Display it over multiple physical lines with shell backslash continuations so
long container commands do not require horizontal scrolling. Split only between
shell words. Do not omit arguments, add ellipses, infer extra flags, or replace
the command with a wrapper path.

If that field is absent from a successful execution payload, report a
CLI/artifact contradiction instead of substituting `sbatch`, wrapper scripts,
launcher scripts, templates, or inferred commands.

Use this report shape:

````text
Status: <payload status>
Audit: <audit_id>
Submission: <submission_id>

Single-subject container command:
```bash
<summary.execution.single_subject_command \
  split across shell words with backslash continuations>
```
````

Keep the rest of the reply short: say what was submitted, where the submission
artifact is, and which follow-up route to use for logs or status if the user
asks.
