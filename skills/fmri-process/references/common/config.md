# Config Guide

Read this only when the user gives a YAML config file, asks you to inspect a
config file, or asks you to translate an old container command or script.

Config is an agent-side translation aid. The CLI does not accept config files.
Read the YAML, extract supported concrete values, then pass those values to
`path-probe` and workflow commands as explicit CLI flags.

## YAML Surface

Config is a user-facing input format, not a runtime input to workflow
commands. It exists to help translate prior user habits into explicit flags
before `path-probe`.

Canonical config uses kebab-case keys in three sections:

| Section | Key examples | Meaning |
| --- | --- | --- |
| `shared` | `remote-host`, `output-root`, `work-root`, `fs-license`, `templateflow-home`, `container-runtime`, `executor-policy`, `nthreads-per-job`, `omp-nthreads` | Shared locator, runtime, and resource fields. |
| `fmriprep` | `bids-root`, `image`, `subjects`, `output-spaces`, `cifti-output`, `skip-bids-validation`, `fs-no-reconall` | fMRIPrep request fields. |
| `xcpd` | `fmriprep-derivatives`, `image`, `subjects`, `mode`, `min-time`, `motion-filter-type`, `band-stop-min`, `band-stop-max`, `motion-filter-order`, `despike` | XCP-D request fields. |

Sections are target-scoped. For a concrete fMRIPrep command, translate only
`shared.*` and `fmriprep.*` keys that have an explicit CLI flag for that route.
For XCP-D, translate only `shared.*` and `xcpd.*` keys that have an explicit
CLI flag for that route. Report keys from the wrong target or outside the
route's argument surface as unsupported for the next command.

There is no config key for `--notrack`. Current fMRIPrep container commands
already render `--notrack`.

Use the target-specific skill-local templates:

- [config.fmriprep.example.yaml](config.fmriprep.example.yaml) for
  fMRIPrep translation.
- [config.xcpd.example.yaml](config.xcpd.example.yaml) for `xcpd-audit` and
  `run-xcpd` translation.

They contain placeholders and are not directly runnable. Copies also exist at
the root of the published `fmriprep-skills` bundle for users who install the
package without this reference tree. Do not use one YAML file containing both
filled `fmriprep` and `xcpd` sections for one concrete command.

## Old Command Migration

When a user gives an old `singularity run`, `apptainer run`, `docker run`, or
script snippet, translate it by judgment. Do not use or invent a CLI parser.
User commands often contain placeholders, shell expressions, comments, missing
line continuations, site variables, and partial commands.

Extract only facts that are concrete enough for config:

| Old command clue | Config destination |
| --- | --- |
| concrete bind mounted at container `/data` | `fmriprep.bids-root` |
| concrete bind mounted at container `/fmriprep` | `xcpd.fmriprep-derivatives` |
| concrete bind mounted at container `/out` | `shared.output-root` |
| concrete bind mounted at container `/work` | `shared.work-root` |
| concrete bind mounted at container `/opt/freesurfer/license.txt` | `shared.fs-license` |
| concrete `.sif`, `.simg`, `.img`, or registry image reference | `fmriprep.image` or `xcpd.image` |
| `--participant-label` / `--participant_label` with concrete subject ids | `fmriprep.subjects` or `xcpd.subjects` |
| fMRIPrep `--output-spaces`, `--cifti-output`, `--skip-bids-validation` | fMRIPrep config keys with the same kebab spelling. |
| XCP-D `--mode`, `--min-time`, `--motion-filter-type`, `--band-stop-min`, `--band-stop-max`, `--motion-filter-order`, `--despike` | XCP-D config keys without the `xcpd-` prefix. |
| `--nthreads`, `--omp-nthreads` | `shared.nthreads-per-job`, `shared.omp-nthreads` |

Do not turn unresolved placeholders such as `${BIDS_ROOT}`, `${OUT_ROOT}`,
`${WORK_DIR}`, `${LICENSE}`, `${SUBJ_ID}`, shell expressions, unknown flags,
cleanup flags, debug flags, interactive metadata flags, or `--notrack` into config.
Report them to the user as unresolved, unsupported, or already fixed by the
wrapper. Ask for concrete values only when required for the next route.
