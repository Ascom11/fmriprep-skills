# XCP-D Custom Args

Read this before attempting custom XCP-D arguments.

## Hard Rule

Use typed allowlist only. Do not use raw shell strings, free-form argument
suffixes, unknown config keys, or manual command edits.

Path-binding, input-scope, mode, file-format, output-layout, resource, cleanup,
debug, help, and version parameters are not custom args.

## Request-Only Parameters

Use first-class request fields for:

- subject/session scope from [../common/arguments.md](../common/arguments.md)
- `xcpd_task_ids` / `--xcpd-task-id <task>`
- `xcpd_bids_filter_file` / `--xcpd-bids-filter-file <json>`
- `xcpd_datasets` / `--xcpd-dataset <alias=path>`
- `xcpd_mem_mb` / `--xcpd-mem-mb <mb>`
- `xcpd_mode` / `--xcpd-mode <abcd|nichart>`
- future `input_type`, `file_format`, `output_layout`, `config_file`,
  `use_plugin`, and `bids_database_dir`

Extra datasets are audited and bound into the container. Rendered XCP-D commands
use `/xcpd_datasets/<alias>`, not host paths.

## Config Allowlist

Canonical config key: `xcpd.custom-args`.
The downstream workflow CLI does not accept config files directly. Translate
values in this allowlist into repeatable `--xcpd-custom-arg key=value` entries
on the fresh `xcpd-audit` command. `run-xcpd` inherits the saved
`xcpd_custom_args` signature; repeat `--xcpd-custom-arg` there only when you
need to assert that the current request still matches the saved audit.

Example:

```bash
--xcpd-custom-arg smoothing=4 --xcpd-custom-arg low_mem=true
```

Config may contain these typed keys:

| Config key | Type | XCP-D flag |
| --- | --- | --- |
| `dummy_scans` | integer or string | `--dummy-scans` |
| `smoothing` | number or string | `--smoothing` |
| `combine_runs` | string | `--combine-runs` |
| `skip` | list of strings | `--skip` |
| `head_radius` | number or string | `--head-radius` |
| `fd_thresh` | number or string | `--fd-thresh` |
| `output_type` | string | `--output-type` |
| `disable_bandpass_filter` | boolean | `--disable-bandpass-filter` |
| `lower_bpf` | number | `--lower-bpf` |
| `upper_bpf` | number | `--upper-bpf` |
| `bpf_order` | integer | `--bpf-order` |
| `min_coverage` | number or string | `--min-coverage` |
| `output_run_wise_correlations` | string | `--output-run-wise-correlations` |
| `atlases` | list of atlas names | `--atlases` |
| `nuisance_regressors` | built-in strategy token | `--nuisance-regressors` |
| `create_matrices` | list of strings | `--create-matrices` |
| `random_seed` | integer | `--random-seed` |
| `linc_qc` | string | `--linc-qc` |
| `abcc_qc` | string | `--abcc-qc` |
| `report_output_level` | string | `--report-output-level` |
| `aggregate_session_reports` | string | `--aggregate-session-reports` |
| `low_mem` | boolean | `--low-mem` |
| `md_only_boilerplate` | boolean | `--md-only-boilerplate` |
| `resource_monitor` | boolean | `--resource-monitor` |
| `stop_on_first_crash` | boolean | `--stop-on-first-crash` |
| `verbose` | non-negative integer | repeated `-v` |

External atlas paths still go through `xcpd_datasets`. YAML nuisance config
paths need a future first-class request field; do not put them in
`nuisance_regressors`.

## Rejected Or Deferred

Rejected: raw shell strings, `clean_workdir`, `debug`, `help`, `version`, and
deprecated `skip_parcellation`.

Deferred to first-class fields: `input_type`, `file_format`, `output_layout`,
`config_file`, `use_plugin`, `bids_database_dir`, `reports_only`,
`boilerplate_only`, `write_graph`, and `warp_surfaces_native2std`.
