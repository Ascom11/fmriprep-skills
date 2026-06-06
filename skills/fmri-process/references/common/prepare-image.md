# Prepare Image

Read this only for `prepare_requirements` entries with `kind: image`.

Choose image source, target, runtime, and target host from the current
`prepare_requirements` entry. Do not invent a new cache or output path.

## Before Pull

Inspect `network_check.status` before starting a pull. A failed network check
means the normal pull needs explicit risk confirmation. A plain request to pull
the image is not enough.

Verify the target-side write boundary named by the entry. If the target path is
unknown or unwritable, report it and stop.

Container cachedir selection is an environment-level manual choice, not a
stable CLI interface. Do not invent `--cachedir`, `--cache-dir`,
`--singularity-cachedir`, `--apptainer-cachedir`, or similar CLI flags.

Do not put Apptainer or Singularity cache, temp, or build directories on exFAT.
container image preparation may create symbolic links, and exFAT commonly
cannot provide them. Tell the user to choose native Linux storage or another
filesystem with symbolic-link support before starting image preparation.

## Materialization

Use only the command shape that matches the selected runtime and target host:

```bash
docker pull "${image_ref}"
singularity pull "${target_sif}" "${image_ref}"
apptainer pull "${target_sif}" "${image_ref}"
```

- Local Linux or WSL with Apptainer/Singularity: pull a target-visible SIF on
  the local runtime host.
- Native Windows with Docker: pull into the selected local Docker daemon.
- Remote Linux/HPC with Apptainer/Singularity: materialize the SIF on the
  remote target host by default.
- Remote Docker: pull into the selected remote Docker daemon.

For Apptainer/Singularity, the target is a target-visible SIF path. When the
payload reports a `docker://...` source, use that reported source instead of
reconstructing it from the original user value.

State the chosen target SIF path before long pulls.

## Retry And Upload Recovery

If an image pull log shows registry access ending with `EOF` before or during
blob transfer, retry the same image pull twice before changing strategy.

If the same `EOF` failure remains after two retries, report registry, proxy, or
network evidence and switch strategy to one of:

- a ready target-visible image
- SIF upload recovery for Apptainer/Singularity
- Docker daemon preload for Docker
- a registry mirror

Use local pull-then-upload only after the user explicitly chooses it or after a
real remote Apptainer/Singularity pull failed and the user accepts recovery.
This recovery is SIF-only. Build or provide a completed local SIF, transfer it
to `"${target_sif}.partial"`, then run
`mv "${target_sif}.partial" "${target_sif}"` on the remote host in the same
filesystem.

Never write directly to the final SIF, and never use `rsync --delete`.
