#!/usr/bin/env python3
"""Reconcile labeled GitHub issues for validated pending bank source paths."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence
from urllib.parse import unquote_to_bytes


ROOT = Path(__file__).resolve().parent.parent
LABEL = "pending-bank-identity"
ISSUE_TITLE_PREFIX = "Verify pending bank ID: "
ISSUE_TITLE_LIMIT = 256
MARKER_PREFIX = "<!-- pending-bank-identity:path="
RESERVED_MARKER_PREFIX = "<!-- pending-bank-identity:"
MARKER_PATTERN = re.compile(
    r"<!-- pending-bank-identity:path=([A-Za-z0-9._/%-]+) -->"
)
FENCE_START_PATTERN = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,})[^\r\n]*$")
FENCE_END_PATTERN = re.compile(r"^[ \t]{0,3}(?P<fence>`+)[ \t]*$")
SAFE_PATH_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
)


class TrackerError(Exception):
    """An actionable failure while validating or reconciling pending paths."""


@dataclass(frozen=True)
class ManagedIssue:
    """An open, label-scoped issue with one canonical pending-path marker."""

    number: int
    path: str


def encode_path(path: str) -> str:
    """Encode a path for an unambiguous, canonical hidden issue marker."""
    return "".join(
        chr(byte) if byte in SAFE_PATH_BYTES else f"%{byte:02X}"
        for byte in path.encode("utf-8")
    )


def decode_path(encoded_path: str) -> str | None:
    """Decode only a canonical encoded path, rejecting alternate spellings."""
    if not isinstance(encoded_path, str) or not encoded_path:
        return None
    try:
        path = unquote_to_bytes(encoded_path).decode("utf-8")
    except UnicodeDecodeError:
        return None
    return path if encode_path(path) == encoded_path else None


def marker_for_path(path: str) -> str:
    """Return the exact marker used as a pending path's issue identity."""
    return f"{MARKER_PREFIX}{encode_path(path)} -->"


def _without_fenced_code_blocks(body: str) -> str:
    """Remove fenced code contents before looking for semantic HTML markers."""
    visible_lines: list[str] = []
    fence_length: int | None = None
    for line in body.splitlines(keepends=True):
        stripped_line = line.rstrip("\r\n")
        if fence_length is None:
            opening = FENCE_START_PATTERN.match(stripped_line)
            if opening is not None:
                fence_length = len(opening.group("fence"))
            else:
                visible_lines.append(line)
            continue
        closing = FENCE_END_PATTERN.match(stripped_line)
        if closing is not None and len(closing.group("fence")) >= fence_length:
            fence_length = None
    return "".join(visible_lines)


def path_from_body(body: Any) -> str | None:
    """Return a path only when a body has exactly one canonical marker."""
    if not isinstance(body, str):
        return None
    visible_body = _without_fenced_code_blocks(body)
    if visible_body.count(RESERVED_MARKER_PREFIX) != 1:
        return None
    match = MARKER_PATTERN.search(visible_body)
    if match is None:
        return None
    return decode_path(match.group(1))


def markdown_path(path: str) -> str:
    """Render a raw path in a fenced code block that its backticks cannot close."""
    longest_backtick_run = max(
        (len(match.group()) for match in re.finditer(r"`+", path)), default=0
    )
    fence = "`" * max(3, longest_backtick_run + 1)
    separator = "" if path.endswith("\n") else "\n"
    return f"{fence}\n{path}{separator}{fence}"


def issue_title(path: str) -> str:
    """Return a readable tracking title within GitHub's length limit."""
    available = ISSUE_TITLE_LIMIT - len(ISSUE_TITLE_PREFIX)
    if len(path) <= available:
        return f"{ISSUE_TITLE_PREFIX}{path}"
    return f"{ISSUE_TITLE_PREFIX}{path[: available - 3]}..."


def issue_body(path: str, owner: str) -> str:
    """Return the deterministic body for a managed pending-bank issue."""
    return (
        f"{marker_for_path(path)}\n\n"
        f"@{owner}, please verify the bank ID and rename this bank's "
        "source directory to `<label>-<cc>_<bank-id>`.\n\n"
        "Pending source path:\n\n"
        f"{markdown_path(path)}\n"
    )


def _run_command(
    arguments: Sequence[str], *, input_text: str | None = None
) -> str:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=ROOT,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
            shell=False,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
        raise TrackerError(detail or "command failed") from exc
    except OSError as exc:
        raise TrackerError(str(exc)) from exc
    if result.returncode:
        raise TrackerError("command failed")
    if not isinstance(result.stdout, str):
        raise TrackerError("command returned non-text output")
    return result.stdout


def _json_output(output: str, description: str) -> Any:
    try:
        return json.loads(output)
    except (json.JSONDecodeError, ValueError) as exc:
        raise TrackerError(f"{description} returned invalid JSON") from exc


def pending_paths() -> list[str]:
    """Run the sole repository source enumerator and validate its complete output."""
    output = _run_command(
        [sys.executable, "scripts/cashbacks.py", "pending", "--json"]
    )
    decoded = _json_output(output, "pending-path enumeration")
    if not isinstance(decoded, list):
        raise TrackerError("pending-path enumeration must return a JSON array")

    paths: list[str] = []
    seen: set[str] = set()
    for path in decoded:
        if not isinstance(path, str) or not path:
            raise TrackerError(
                "pending-path enumeration must contain only non-empty strings"
            )
        try:
            path.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise TrackerError(
                "pending-path enumeration must contain only UTF-8 strings"
            ) from exc
        if path in seen:
            raise TrackerError("pending-path enumeration returned duplicate paths")
        seen.add(path)
        paths.append(path)
    return paths


def _github_api(
    method: str, endpoint: str, payload: dict[str, Any] | None = None
) -> str:
    arguments = ["gh", "api", "--method", method, endpoint]
    input_text = None
    if payload is not None:
        arguments.extend(("--input", "-"))
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return _run_command(arguments, input_text=input_text)


def _require_environment() -> tuple[str, str]:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not owner:
        raise TrackerError("GITHUB_REPOSITORY_OWNER must be non-empty")
    if not repository:
        raise TrackerError("GITHUB_REPOSITORY must be non-empty")
    return owner, repository


def _require_label(repository: str) -> None:
    output = _github_api("GET", f"repos/{repository}/labels/{LABEL}")
    label = _json_output(output, "label preflight")
    if not isinstance(label, dict) or label.get("name") != LABEL:
        raise TrackerError(f"required label {LABEL!r} is unavailable")


def _list_labeled_issues(repository: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    page_number = 1
    while True:
        output = _github_api(
            "GET",
            f"repos/{repository}/issues?state=open&labels={LABEL}"
            f"&per_page=100&page={page_number}",
        )
        page = _json_output(output, "labeled issue listing")
        if not isinstance(page, list) or not all(
            isinstance(issue, dict) for issue in page
        ):
            raise TrackerError("labeled issue listing must return JSON arrays of objects")
        issues.extend(page)
        if len(page) < 100:
            return issues
        page_number += 1


def _has_label(issue: dict[str, Any]) -> bool:
    labels = issue.get("labels")
    return isinstance(labels, list) and any(
        isinstance(label, dict) and label.get("name") == LABEL for label in labels
    )


def _managed_issue(issue: dict[str, Any]) -> ManagedIssue | None:
    if "pull_request" in issue or not _has_label(issue):
        return None
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return None
    path = path_from_body(issue.get("body"))
    if path is None:
        return None
    return ManagedIssue(number=number, path=path)


def _managed_issues_by_path(issues: list[dict[str, Any]]) -> dict[str, list[ManagedIssue]]:
    matches: dict[str, list[ManagedIssue]] = {}
    for issue in issues:
        managed = _managed_issue(issue)
        if managed is not None:
            matches.setdefault(managed.path, []).append(managed)
    for path_matches in matches.values():
        path_matches.sort(key=lambda issue: issue.number)
    return matches


def _create_issue(repository: str, path: str, owner: str) -> None:
    _github_api(
        "POST",
        f"repos/{repository}/issues",
        {
            "title": issue_title(path),
            "body": issue_body(path, owner),
            "labels": [LABEL],
        },
    )


def _update_issue(repository: str, number: int, path: str, owner: str) -> None:
    _github_api(
        "PATCH",
        f"repos/{repository}/issues/{number}",
        {
            "title": issue_title(path),
            "body": issue_body(path, owner),
        },
    )


def _close_issue(repository: str, number: int) -> None:
    _github_api(
        "PATCH", f"repos/{repository}/issues/{number}", {"state": "closed"}
    )


def reconcile() -> None:
    """Reconcile the validated pending-path set with labeled open GitHub issues."""
    current_paths = sorted(pending_paths())
    owner, repository = _require_environment()
    _require_label(repository)
    matches_by_path = _managed_issues_by_path(_list_labeled_issues(repository))
    current_path_set = set(current_paths)

    for path in current_paths:
        matches = matches_by_path.get(path)
        if matches:
            _update_issue(repository, matches[0].number, path, owner)
        else:
            _create_issue(repository, path, owner)

    for path in sorted(set(matches_by_path) - current_path_set):
        for issue in matches_by_path[path]:
            _close_issue(repository, issue.number)


def main() -> int:
    try:
        reconcile()
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
