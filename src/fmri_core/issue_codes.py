"""Code-owned issue catalog for audit, runtime, and reporting contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


@dataclass(frozen=True)
class IssueCode:
    code: str
    scope: str
    category: str
    severity: int
    meaning: str
    advice: str
    missing_required: str | None = None
    reportable: bool = True
    legacy: bool = False


def _catalog_payload() -> dict[str, Any]:
    path = resources.files("fmri_core").joinpath("resources/issue_catalog.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise ValueError("issue_catalog.json must contain an issues list")
    return payload


def _load_issues() -> tuple[IssueCode, ...]:
    issues: list[IssueCode] = []
    seen: set[str] = set()
    for item in _catalog_payload()["issues"]:
        if not isinstance(item, dict):
            raise ValueError("issue catalog entries must be objects")
        issue = IssueCode(
            code=str(item["code"]),
            scope=str(item["scope"]),
            category=str(item["category"]),
            severity=int(item["severity"]),
            meaning=str(item["meaning"]),
            advice=str(item["advice"]),
            missing_required=str(item["missing_required"]) if item.get("missing_required") is not None else None,
            reportable=bool(item.get("reportable", True)),
            legacy=bool(item.get("legacy", False)),
        )
        if issue.code in seen:
            raise ValueError(f"Duplicate issue code in catalog: {issue.code}")
        seen.add(issue.code)
        issues.append(issue)
    return tuple(issues)


def _issue_dict(issue: IssueCode) -> dict[str, Any]:
    return {
        "code": issue.code,
        "scope": issue.scope,
        "category": issue.category,
        "severity": issue.severity,
        "meaning": issue.meaning,
        "advice": issue.advice,
    }


def _unknown_issue_dict(code: str, *, category: str | None) -> dict[str, Any]:
    return {
        "code": code,
        "scope": "unknown",
        "category": category or "unknown",
        "severity": 0,
        "meaning": code,
        "advice": "Review the raw audit payload and rerun with the current CLI if this code should be cataloged.",
    }


def _dedupe_codes(codes: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for code in codes:
        value = str(code)
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def issue_findings(codes: list[str], *, category: str | None = None) -> list[dict[str, Any]]:
    """Return structured metadata for observed issue codes only."""

    findings: list[dict[str, Any]] = []
    for code in _dedupe_codes(codes):
        issue = ISSUE_BY_CODE.get(code)
        if issue is None:
            findings.append(_unknown_issue_dict(code, category=category))
        else:
            findings.append(_issue_dict(issue))
    return findings


def issue_bucket_findings(
    *,
    blockers: list[str] | None = None,
    prepare_required: list[str] | None = None,
    warnings: list[str] | None = None,
    reason_codes: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    findings: dict[str, list[dict[str, Any]]] = {}
    if blockers is not None:
        findings["blockers"] = issue_findings(blockers, category="blocker")
    if prepare_required is not None:
        findings["prepare_required"] = issue_findings(prepare_required, category="prepare-required")
    if warnings is not None:
        findings["warnings"] = issue_findings(warnings, category="warning")
    if reason_codes is not None:
        findings["reason_codes"] = issue_findings(reason_codes)
    return findings


ISSUES = _load_issues()
ISSUE_BY_CODE = {issue.code: issue for issue in ISSUES}
ISSUE_DESCRIPTIONS = {issue.code: issue.meaning for issue in ISSUES}
PREPARE_REQUIRED_RUNTIME_CODES = {issue.code for issue in ISSUES if issue.category == "prepare-required"}
REQUIRED_BLOCKER_FIELDS = {issue.code: issue.missing_required for issue in ISSUES if issue.missing_required}
XCPD_ISSUE_CODES = {issue.code for issue in ISSUES if issue.scope == "xcpd"}
LEGACY_ISSUE_CODES = {issue.code for issue in ISSUES if issue.legacy}

AUDIT_CHECKLIST_REVIEW_ISSUES = tuple(
    issue
    for issue in ISSUES
    if issue.reportable
    and issue.scope in {"shared", "fmriprep"}
    and issue.category in {"advice", "blocker", "prepare-required", "subject-exclusion", "warning"}
)
PREPARE_RESULT_REVIEW_ISSUES = ()
REPLAY_ARTIFACT_REVIEW_ISSUES = tuple(
    issue for issue in ISSUES if issue.reportable and issue.scope in {"shared", "fmriprep"} and issue.category == "artifact-replay"
)
_REPORTABLE_REVIEW_CODES = {
    issue.code
    for surface in (
        AUDIT_CHECKLIST_REVIEW_ISSUES,
        PREPARE_RESULT_REVIEW_ISSUES,
        REPLAY_ARTIFACT_REVIEW_ISSUES,
    )
    for issue in surface
}
REPORTABLE_REVIEW_ISSUES = tuple(issue for issue in ISSUES if issue.code in _REPORTABLE_REVIEW_CODES)


__all__ = [
    "AUDIT_CHECKLIST_REVIEW_ISSUES",
    "ISSUE_BY_CODE",
    "ISSUE_DESCRIPTIONS",
    "ISSUES",
    "LEGACY_ISSUE_CODES",
    "PREPARE_RESULT_REVIEW_ISSUES",
    "PREPARE_REQUIRED_RUNTIME_CODES",
    "REPORTABLE_REVIEW_ISSUES",
    "REQUIRED_BLOCKER_FIELDS",
    "REPLAY_ARTIFACT_REVIEW_ISSUES",
    "XCPD_ISSUE_CODES",
    "IssueCode",
    "issue_bucket_findings",
    "issue_findings",
]
