# fMRIPrep Custom Args

Read this only after [fmriprep-args.md](fmriprep-args.md) says typed fMRIPrep custom
arguments are needed.

## Hard Rule

Custom fMRIPrep args are not a raw shell string. Prefer typed, known values
from config translation or explicit `--fmriprep-custom-arg key=value` CLI
entries.

Custom args get typed rendering, saved command capture, and a runtime signature.
They do not get full dataset/runtime semantic review. Prefer first-class route
flags when available. If custom args change scientific meaning, output shape, or
resource behavior, the custom path carries that risk.

Unknown, cleanup, path-binding, debug, interactive, or output-layout-changing
flags require an explicit warning. If the user understands the warning and
still wants to execute, continue through the existing execution path instead of
blocking at the agent layer. Do not open, read, or edit package source files by
default just to accept the requested flag in the current task. The agent must
not read or modify Python implementation code by default for custom flag
support.

## Config Allowlist

Canonical config key: `fmriprep.custom-args`.
The downstream workflow CLI does not accept config files directly. Translate
values in this allowlist into repeatable `--fmriprep-custom-arg key=value`
entries.

Allowed config keys map to official fMRIPrep flags:

- `ignore` -> `--ignore`
- `force` -> `--force`
- `bold2anat_init` -> `--bold2anat-init`
- `bold2anat_dof` -> `--bold2anat-dof`
- `slice_time_ref` -> `--slice-time-ref`
- `dummy_scans` -> `--dummy-scans`
- `fallback_total_readout_time` -> `--fallback-total-readout-time`
- `mem` -> `--mem`
- `mem_mb` / `mem-mb` -> `--mem-mb`
- `random_seed` -> `--random-seed`
- `me_t2s_fit_method` -> `--me-t2s-fit-method`
- `skull_strip_template` -> `--skull-strip-template`
- `me_output_echos` -> `--me-output-echos`
- `low_mem` -> `--low-mem`
- `return_all_components` -> `--return-all-components`
- `fd_spike_threshold` -> `--fd-spike-threshold`
- `dvars_spike_threshold` -> `--dvars-spike-threshold`
- `aggregate_session_reports` -> `--aggregate-session-reports`
- `medial_surface_nan` -> `--medial-surface-nan`
- `md_only_boilerplate` -> `--md-only-boilerplate`
- `msm: true` -> `--msm`; `msm: false` -> `--no-msm`
- `project_goodvoxels` -> `--project-goodvoxels`
- `skull_strip_fixed_seed` -> `--skull-strip-fixed-seed`
- `skull_strip_t1w` -> `--skull-strip-t1w`
- `fmap_bspline` -> `--fmap-bspline`
- `fmap_no_demean` -> `--fmap-no-demean`
- `use_syn_sdc` -> `--use-syn-sdc`
- `verbose` -> `--verbose`
- `resource_monitor` -> `--resource-monitor`
- `stop_on_first_crash` -> `--stop-on-first-crash`

## CLI Usage

Canonical generic form:

```bash
--fmriprep-custom-arg dummy_scans=4 --fmriprep-custom-arg low_mem=true
```

Do not invent fMRIPrep-prefixed wrapper aliases, and do not append raw trailing
fMRIPrep arguments. Use the generic custom-arg flag for all allowlisted custom
keys.

## Version-Specific Args

If a user requests a fMRIPrep custom argument outside this allowlist, or says
their fMRIPrep version differs from the version assumed here, search the
official usage page before advising about it:
`https://fmriprep.org/en/<version>/usage.html`.

Replace `<version>` with the user's explicit fMRIPrep version. If no version is
known, first use the version reported by the configured fMRIPrep image or ask
for the version; do not guess from memory.

After checking the official page, warn plainly about the risk: the wrapper does
not semantically review this custom value, path/bind behavior may be wrong, and
the downstream CLI may still reject unsupported keys. If the user still
confirms execution, proceed with the existing workflow path and report any CLI
failure payload. Do not stop solely because the argument is outside this local
allowlist, and do not open, read, or edit package source files by default to
make it work. The agent must not read or modify Python implementation code by
default to make a custom flag work.

## Warning-Required Args

- `bids_filter_file` / `--bids-filter-file`: deferred; session filtering is
  handled by the wrapper.
- `derivatives` / `-d` / `--derivatives`: deferred; needs path, bind, and proof
  design.
- `anat_derivatives` / `--anat-derivatives`: not a stable public surface here.
- `bids_database_dir`, `use_plugin`, `config_file`, `fs_subjects_dir`: deferred
  for path, bind, and security design.
- `clean_workdir`: rejected because it has cleanup semantics.
- `notrack` / `--notrack`: rejected as a custom arg because the wrapper already
  renders `--notrack` by default.
- `subject_anatomical_reference` / `--subject-anatomical-reference`:
  deferred; needs an explicit anatomical-reference policy.
- `track_sessions` / `--track-sessions` and `no_track_sessions` / `--no-track-sessions`:
  deferred; session policy belongs with wrapper session selection.
- `level`, `reports_only`, `boilerplate_only`, `output_layout`,
  `submm_recon`, `no_submm_recon`, `fs_no_resume`: deferred
  because they change workflow scope, output layout, storage, or reuse
  semantics.
- `sloppy`, `debug`, `write_graph`, `track_carbon`, `country_code`, `version`,
  `help`: rejected or deferred as debug, interactive, or environment-side-effect
  surfaces.
