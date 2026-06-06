# Common CLI Boundary

Read this only when a selected route needs shared invocation rules.

## Local CLI Rule

Run workflow commands through the selected local Python:

```text
<env-python> -m fmri_process.cli <command> ...
```

For `--remote-host`, the CLI still runs locally. The remote host is the target
filesystem/runtime boundary for tool-managed probes, prepare commands, and
execution scripts. Do not run `python -m fmri_process.cli` on the remote host.

## Shared Inputs

Use [common/arguments.md](arguments.md) and the selected pipeline argument
reference as the default source for command assembly. If the route docs and
CLI payload contradict each other, report the contradiction instead of
silently changing the route or discovering parameters from command output.

Canonical argument files:

- shared: [common/arguments.md](arguments.md)
- fMRIPrep-specific: [../fmriprep/fmriprep-args.md](../fmriprep/fmriprep-args.md)
- XCP-D-specific: [../xcpd/xcpd-args.md](../xcpd/xcpd-args.md)

## Remote Rules

- Dataset, output, work, log, image, license, and TemplateFlow paths are
  target-visible paths.
- `--subject-file` remains a local CLI-side file.
- Config files are not CLI inputs. If the user gives a local or target-local
  YAML config file, read it before `path-probe`, translate supported values
  into explicit flags, and carry only explicit flags forward.
- If the user gives an old container command or script, first translate the
  concrete facts using [common/config.md](config.md). There is no
  `config-from-command` CLI action.
- Do not use hand-written `ssh`, broad `find`, or broad `rg` as normal path
  discovery. Path normalization happens at the parent preflight guard.
- Manual remote probes are debug only after CLI output contradicts available
  evidence. They must not change route selection, readiness, or execution
  approval.
