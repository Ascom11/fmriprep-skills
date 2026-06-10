#!/usr/bin/env python
"""Append one structured entry to a harness trace."""

from __future__ import annotations

import argparse
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SIZE_LIMIT_BYTES = 50 * 1024
TRACE_HEADER = "# Harness Trace\n\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Append one harness trace entry.")
    parser.add_argument("--trace-path", required=True)
    parser.add_argument("--remote-host")
    parser.add_argument("--remote-python", default="python")
    parser.add_argument("--entry-kind", choices=("init", "run-status", "user-correction"), required=True)
    parser.add_argument("--timestamp")
    parser.add_argument("--raw")
    parser.add_argument("--goal")
    parser.add_argument("--constraints")
    parser.add_argument("--dataset")
    parser.add_argument("--output-root")
    parser.add_argument("--remote")
    parser.add_argument("--next-command")
    parser.add_argument("--target")
    parser.add_argument("--audit-id")
    parser.add_argument("--submission-id")
    parser.add_argument("--status")
    parser.add_argument("--evidence")
    parser.add_argument("--next")
    parser.add_argument("--applies-to")
    parser.add_argument("--action")
    args = parser.parse_args()

    text = build_entry(args)
    if args.remote_host:
        append_remote(args.remote_host, args.remote_python, args.trace_path, args.entry_kind, text)
    else:
        append_local(Path(args.trace_path), args.entry_kind, text)
    return 0


def build_entry(args: argparse.Namespace) -> str:
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.entry_kind == "init":
        return "\n".join(
            [
                "## User Request",
                fenced_item("Raw", args.raw),
                fenced_item("Goal", args.goal),
                fenced_item("Constraints", args.constraints),
                "",
                "## Scope",
                inline_item("Dataset", args.dataset),
                inline_item("Output root", args.output_root),
                inline_item("Remote", args.remote or "local"),
                "",
                "## Status Log",
                "",
                f"### {timestamp} init",
                "- State: trace created.",
                inline_item("Next", args.next_command, prefix="run "),
                "",
            ]
        )
    if args.entry_kind == "run-status":
        return "\n".join(
            [
                "",
                f"### {timestamp} run-status",
                inline_item("Target", args.target),
                selector_item(args.audit_id, args.submission_id),
                inline_item("Status", args.status),
                fenced_item("Evidence", args.evidence),
                fenced_item("Next", args.next),
                "",
            ]
        )
    return "\n".join(
        [
            "",
            f"### {timestamp} user-correction",
            fenced_item("Raw", args.raw),
            inline_item("Applies to", args.applies_to),
            fenced_item("Action", args.action),
            fenced_item("Next", args.next),
            "",
        ]
    )


def append_local(trace: Path, entry_kind: str, text: str) -> None:
    trace.parent.mkdir(parents=True, exist_ok=True)
    if entry_kind == "run-status":
        append_run_status_local(trace, text)
        return
    append_text_local(trace, text)


def append_text_local(trace: Path, text: str) -> None:
    size = trace.stat().st_size if trace.exists() else 0
    if size > SIZE_LIMIT_BYTES:
        raise SystemExit("spawn subagent to compact trace before appending")
    needs_header = size == 0
    needs_newline = size > 0 and not file_ends_with_newline(trace)
    with trace.open("a", encoding="utf-8", newline="\n") as handle:
        if needs_header:
            handle.write(TRACE_HEADER)
            text = text.lstrip("\n")
        elif needs_newline:
            handle.write("\n")
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def append_run_status_local(trace: Path, text: str) -> None:
    existing = trace.read_text(encoding="utf-8") if trace.exists() else TRACE_HEADER
    if not existing:
        existing = TRACE_HEADER
    if not existing.endswith("\n"):
        existing += "\n"
    compacted = trim_run_status_entries(existing + text)
    tmp = trace.with_suffix(trace.suffix + ".tmp")
    tmp.write_text(compacted, encoding="utf-8", newline="\n")
    tmp.replace(trace)
    if _entry_status(text) == "completed" or len(compacted.encode("utf-8")) > SIZE_LIMIT_BYTES:
        raise SystemExit("spawn subagent to compact trace")


def trim_run_status_entries(text: str) -> str:
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("### ")]
    if not starts:
        return text
    starts.append(len(lines))
    prefix = lines[: starts[0]]
    blocks = [lines[start:end] for start, end in zip(starts, starts[1:])]
    run_status_by_target: dict[str, list[int]] = {}
    for index, block in enumerate(blocks):
        if _block_kind(block) == "run-status":
            run_status_by_target.setdefault(_block_target(block), []).append(index)
    keep_run_status: set[int] = set()
    for indexes in run_status_by_target.values():
        keep_run_status.add(indexes[0])
        keep_run_status.update(indexes[-2:])
    kept_lines = list(prefix)
    for index, block in enumerate(blocks):
        if _block_kind(block) == "run-status" and index not in keep_run_status:
            continue
        kept_lines.extend(block)
    return "".join(kept_lines)


def _block_kind(block: list[str]) -> str | None:
    if not block:
        return None
    parts = block[0].strip().split()
    return parts[-1] if len(parts) >= 3 else None


def _block_target(block: list[str]) -> str:
    for line in block:
        if line.startswith("- Target:"):
            value = line.split(":", 1)[1].strip()
            return value.strip("`") or "unknown"
    return "unknown"


def _entry_status(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("- Status:"):
            return line.split(":", 1)[1].strip().strip("`")
    return None


def file_ends_with_newline(trace: Path) -> bool:
    with trace.open("rb") as handle:
        handle.seek(-1, 2)
        return handle.read(1) == b"\n"


def append_remote(remote_host: str, remote_python: str, trace_path: str, entry_kind: str, text: str) -> None:
    remote_code = """from pathlib import Path
import sys

p = Path(sys.argv[1])
entry_kind = sys.argv[2]
text = sys.stdin.read()
p.parent.mkdir(parents=True, exist_ok=True)
SIZE_LIMIT_BYTES = 50 * 1024
TRACE_HEADER = "# Harness Trace\\n\\n"

def block_kind(block):
    if not block:
        return None
    parts = block[0].strip().split()
    return parts[-1] if len(parts) >= 3 else None

def block_target(block):
    for line in block:
        if line.startswith("- Target:"):
            value = line.split(":", 1)[1].strip()
            return value.strip("`") or "unknown"
    return "unknown"

def entry_status(content):
    for line in content.splitlines():
        if line.startswith("- Status:"):
            return line.split(":", 1)[1].strip().strip("`")
    return None

def trim_run_status_entries(content):
    lines = content.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("### ")]
    if not starts:
        return content
    starts.append(len(lines))
    prefix = lines[:starts[0]]
    blocks = [lines[start:end] for start, end in zip(starts, starts[1:])]
    by_target = {}
    for index, block in enumerate(blocks):
        if block_kind(block) == "run-status":
            by_target.setdefault(block_target(block), []).append(index)
    keep = set()
    for indexes in by_target.values():
        keep.add(indexes[0])
        keep.update(indexes[-2:])
    kept = list(prefix)
    for index, block in enumerate(blocks):
        if block_kind(block) == "run-status" and index not in keep:
            continue
        kept.extend(block)
    return "".join(kept)

if entry_kind == "run-status":
    existing = p.read_text(encoding="utf-8") if p.exists() else TRACE_HEADER
    if not existing:
        existing = TRACE_HEADER
    if not existing.endswith("\\n"):
        existing += "\\n"
    compacted = trim_run_status_entries(existing + text)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(compacted, encoding="utf-8", newline="\\n")
    tmp.replace(p)
    if entry_status(text) == "completed" or len(compacted.encode("utf-8")) > SIZE_LIMIT_BYTES:
        raise SystemExit("spawn subagent to compact trace")
    raise SystemExit(0)

size = p.stat().st_size if p.exists() else 0
if size > SIZE_LIMIT_BYTES:
    raise SystemExit("spawn subagent to compact trace before appending")
needs_header = size == 0
needs_newline = False
if size > 0:
    with p.open("rb") as reader:
        reader.seek(-1, 2)
        needs_newline = reader.read(1) != b"\\n"
with p.open("a", encoding="utf-8", newline="\\n") as handle:
    if needs_header:
        handle.write("# Harness Trace\\n\\n")
        text = text.lstrip("\\n")
    elif needs_newline:
        handle.write("\\n")
    handle.write(text)
    if not text.endswith("\\n"):
        handle.write("\\n")
"""
    remote_cmd = (
        f"{shlex.quote(remote_python)} -c {shlex.quote(remote_code)} "
        f"{shlex.quote(trace_path)} {shlex.quote(entry_kind)}"
    )
    subprocess.run(
        ["ssh", remote_host, remote_cmd],
        input=text,
        text=True,
        check=True,
    )


def inline_item(label: str, value: str | None, *, prefix: str = "") -> str:
    return f"- {label}: {prefix}`{escape_inline(value or 'none')}`"


def selector_item(audit_id: str | None, submission_id: str | None) -> str:
    return (
        "- Selector: "
        f"`audit_id={escape_inline(audit_id or 'none')}`, "
        f"`submission_id={escape_inline(submission_id or 'none')}`"
    )


def fenced_item(label: str, value: str | None) -> str:
    return f"- {label}:\n{fence(value or 'none')}"


def fence(value: str) -> str:
    longest = 0
    current = 0
    for char in value:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    marker = "`" * max(3, longest + 1)
    return f"{marker}\n{value}\n{marker}"


def escape_inline(value: str) -> str:
    return value.replace("`", "\\`")


if __name__ == "__main__":
    raise SystemExit(main())
