import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "track_pending_bank_company_ids.py"
)
SPEC = importlib.util.spec_from_file_location("pending_bank_tracker", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
tracker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tracker
SPEC.loader.exec_module(tracker)


class PendingBankTrackerTest(unittest.TestCase):
    repository = "example/cashbacks"
    owner = "bank-maintainers"

    def completed(self, value):
        return subprocess.CompletedProcess([], 0, json.dumps(value), "")

    def failed(self, message="GitHub API failure"):
        return subprocess.CalledProcessError(1, ["mock"], output="", stderr=message)

    def issue(
        self,
        number,
        body,
        *,
        labels=None,
        pull_request=False,
        assignees=None,
    ):
        issue = {
            "number": number,
            "body": body,
            "labels": [{"name": tracker.LABEL}] if labels is None else labels,
        }
        if pull_request:
            issue["pull_request"] = {}
        if assignees is not None:
            issue["assignees"] = assignees
        return issue

    def run_tracker(self, responses, *, owner=owner, repository=repository):
        calls = []
        response_iterator = iter(responses)

        def fake_run(arguments, **kwargs):
            calls.append((arguments, kwargs))
            try:
                response = next(response_iterator)
            except StopIteration as exc:
                raise AssertionError(f"unexpected command: {arguments!r}") from exc
            if isinstance(response, BaseException):
                raise response
            return response

        environment = {}
        if owner is not None:
            environment["GITHUB_REPOSITORY_OWNER"] = owner
        if repository is not None:
            environment["GITHUB_REPOSITORY"] = repository
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(tracker.subprocess, "run", side_effect=fake_run),
            contextlib.redirect_stderr(stderr),
        ):
            status = tracker.main()
        return status, stderr.getvalue(), calls

    def assert_pending_command(self, call):
        arguments, kwargs = call
        self.assertEqual(
            [sys.executable, "scripts/cashbacks.py", "pending", "--json"],
            arguments,
        )
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        self.assertIsNone(kwargs["input"])

    def assert_endpoint(self, call, method, endpoint):
        arguments, kwargs = call
        self.assertEqual(["gh", "api", "--method", method, endpoint], arguments[:5])
        self.assertFalse(kwargs["shell"])
        self.assertNotIn(self.owner, arguments)

    def payload(self, call):
        arguments, kwargs = call
        self.assertEqual(["--input", "-"], arguments[-2:])
        return json.loads(kwargs["input"])

    def label_response(self):
        return self.completed({"name": tracker.LABEL})

    def list_response(self, issues):
        return self.completed(issues)

    def test_pending_enumeration_failures_stop_before_github(self):
        invalid_outputs = (
            (self.failed("source data is invalid"), "command failure"),
            (subprocess.CompletedProcess([], 0, "not JSON", ""), "invalid JSON"),
            (self.completed({"path": "src/bank"}), "non-array"),
            (self.completed(["src/bank", ""]), "empty path"),
            (self.completed(["src/bank", "src/bank"]), "duplicate path"),
            (self.completed(["src/bank", 7]), "non-string path"),
            (self.completed(["src/\ud800"]), "non-UTF-8 path"),
        )

        for response, case in invalid_outputs:
            with self.subTest(case=case):
                status, _, calls = self.run_tracker([response])

                self.assertEqual(1, status)
                self.assertEqual(1, len(calls))
                self.assert_pending_command(calls[0])

    def test_missing_or_empty_repository_identity_stops_before_github(self):
        for owner, repository, case in (
            (None, self.repository, "missing owner"),
            ("", self.repository, "empty owner"),
            (self.owner, None, "missing repository"),
            (self.owner, "", "empty repository"),
        ):
            with self.subTest(case=case):
                status, stderr, calls = self.run_tracker(
                    [self.completed(["src/Pending-ru_/categories"])],
                    owner=owner,
                    repository=repository,
                )

                self.assertEqual(1, status)
                self.assertIn("GITHUB_REPOSITORY", stderr)
                self.assertEqual(1, len(calls))
                self.assert_pending_command(calls[0])

    def test_codec_marker_and_markdown_display_are_canonical_and_safe(self):
        path = 'src/Банк-->%`~ "$(echo unsafe);|&/categories'
        encoded = tracker.encode_path(path)
        marker = tracker.marker_for_path(path)

        self.assertEqual(path, tracker.decode_path(encoded))
        self.assertIsNone(tracker.decode_path(""))
        self.assertIsNone(
            tracker.path_from_body("<!-- pending-bank-identity:path= -->")
        )
        self.assertIn("%D0%91", encoded)
        self.assertIn("%3E", encoded)
        self.assertIn("%25", encoded)
        self.assertIn("%60", encoded)
        self.assertIn("%7E", encoded)
        self.assertNotIn("~", encoded)
        self.assertEqual(path, tracker.path_from_body(f"before\n{marker}\nafter"))
        self.assertIsNone(
            tracker.path_from_body("<!-- pending-bank-identity:path=src/~ -->")
        )
        self.assertIsNone(
            tracker.path_from_body("<!-- pending-bank-identity:path=src/%zz -->")
        )
        self.assertIsNone(tracker.path_from_body(marker + "\n" + marker))
        self.assertIsNone(
            tracker.path_from_body("<!-- pending-bank-identity:path=src/path")
        )
        self.assertIsNone(
            tracker.path_from_body(
                marker + "\n<!-- pending-bank-identity:malformed -->"
            )
        )
        marker_like_path = (
            "src/<!-- pending-bank-identity:path=not-an-identity -->/categories"
        )
        self.assertEqual(
            marker_like_path,
            tracker.path_from_body(tracker.issue_body(marker_like_path, self.owner)),
        )

        display = tracker.markdown_path("src/contains ```` backticks/categories")
        self.assertTrue(display.startswith("`````\n"))
        self.assertTrue(display.endswith("\n`````"))
        body = tracker.issue_body(path, self.owner)
        self.assertTrue(body.startswith(marker + "\n\n"))
        self.assertIn(f"@{self.owner}", body)
        self.assertIn(path, body)
        self.assertIn("verify the bank ID", body)
        self.assertIn("<label>-<cc>_<bank-id>", body)
        self.assertNotIn("positive", body)

    def test_issue_title_preserves_unicode_and_bounds_long_paths(self):
        self.assertEqual("Verify pending bank ID: ", tracker.ISSUE_TITLE_PREFIX)
        unicode_path = "src/Пират Банк-ru_/categories"
        unicode_title = tracker.issue_title(unicode_path)
        self.assertEqual(
            f"{tracker.ISSUE_TITLE_PREFIX}{unicode_path}",
            unicode_title,
        )
        self.assertNotIn("%D0", unicode_title)

        long_path = f"src/{'a' * 240}-ru_/categories"
        long_title = tracker.issue_title(long_path)
        self.assertLessEqual(len(long_title), tracker.ISSUE_TITLE_LIMIT)
        self.assertTrue(long_title.startswith(tracker.ISSUE_TITLE_PREFIX))
        self.assertTrue(long_title.endswith("..."))
        self.assertEqual(
            long_path,
            tracker.path_from_body(tracker.issue_body(long_path, self.owner)),
        )

    def test_missing_or_wrong_label_stops_before_listing_and_mutation(self):
        label_failures = (
            (self.failed("not found"), "missing"),
            (self.completed({"name": "renamed-label"}), "wrong label"),
        )
        for label_response, case in label_failures:
            with self.subTest(case=case):
                status, _, calls = self.run_tracker(
                    [
                        self.completed(["src/Pending-ru_/categories"]),
                        label_response,
                    ]
                )

                self.assertEqual(1, status)
                self.assertEqual(2, len(calls))
                self.assert_pending_command(calls[0])
                self.assert_endpoint(
                    calls[1],
                    "GET",
                    f"repos/{self.repository}/labels/{tracker.LABEL}",
                )

    def test_listing_failure_stops_before_every_mutation(self):
        status, _, calls = self.run_tracker(
            [
                self.completed(["src/Pending-ru_/categories"]),
                self.label_response(),
                self.failed("listing unavailable"),
            ]
        )

        self.assertEqual(1, status)
        self.assertEqual(3, len(calls))
        self.assert_endpoint(
            calls[2],
            "GET",
            f"repos/{self.repository}/issues?state=open&labels={tracker.LABEL}"
            "&per_page=100&page=1",
        )

    def test_later_label_filtered_page_supplies_primary_issue(self):
        path = "src/Pending-ru_/categories"
        first_page = [self.issue(number, "unmanaged") for number in range(1, 101)]
        later_primary = self.issue(7, tracker.marker_for_path(path))
        status, _, calls = self.run_tracker(
            [
                self.completed([path]),
                self.label_response(),
                self.list_response(first_page),
                self.list_response([later_primary]),
                self.completed({}),
            ]
        )

        self.assertEqual(0, status)
        self.assertEqual(5, len(calls))
        self.assert_endpoint(
            calls[2],
            "GET",
            f"repos/{self.repository}/issues?state=open&labels={tracker.LABEL}"
            "&per_page=100&page=1",
        )
        self.assert_endpoint(
            calls[3],
            "GET",
            f"repos/{self.repository}/issues?state=open&labels={tracker.LABEL}"
            "&per_page=100&page=2",
        )
        self.assert_endpoint(calls[4], "PATCH", f"repos/{self.repository}/issues/7")
        update = self.payload(calls[4])
        self.assertEqual(tracker.issue_title(path), update["title"])
        self.assertEqual(tracker.issue_body(path, self.owner), update["body"])
        self.assertNotIn("labels", update)
        self.assertNotIn("assignee", update)
        self.assertNotIn("assignees", update)

    def test_reconciliation_updates_primary_creates_and_closes_in_order(self):
        current_a = "src/Alpha-ru_/categories"
        current_z = "src/Zulu-ru_/categories"
        resolved = "src/Resolved-ru_/categories"
        unmanaged = "src/Unmanaged-ru_/categories"
        issues = [
            self.issue(20, tracker.marker_for_path(current_a)),
            self.issue(
                3,
                tracker.marker_for_path(current_a),
                assignees=[{"login": "human-maintainer"}],
            ),
            self.issue(1, tracker.marker_for_path(current_a), pull_request=True),
            self.issue(2, tracker.marker_for_path(current_a), labels=[]),
            self.issue(10, tracker.marker_for_path(resolved)),
            self.issue(13, tracker.marker_for_path(resolved)),
            self.issue(30, None),
            self.issue(31, "<!-- pending-bank-identity:path=src/~ -->"),
            self.issue(34, "<!-- pending-bank-identity:path= -->"),
            self.issue(
                32,
                tracker.marker_for_path(unmanaged)
                + "\n"
                + tracker.marker_for_path(unmanaged),
            ),
            self.issue(33, tracker.marker_for_path(unmanaged), labels=[]),
        ]
        status, _, calls = self.run_tracker(
            [
                self.completed([current_z, current_a]),
                self.label_response(),
                self.list_response(issues),
                self.completed({}),
                self.completed({}),
                self.completed({}),
                self.completed({}),
            ]
        )

        self.assertEqual(0, status)
        self.assertEqual(7, len(calls))
        self.assert_endpoint(calls[3], "PATCH", f"repos/{self.repository}/issues/3")
        self.assert_endpoint(calls[4], "POST", f"repos/{self.repository}/issues")
        self.assert_endpoint(calls[5], "PATCH", f"repos/{self.repository}/issues/10")
        self.assert_endpoint(calls[6], "PATCH", f"repos/{self.repository}/issues/13")

        update = self.payload(calls[3])
        self.assertEqual(tracker.issue_body(current_a, self.owner), update["body"])
        self.assertIn(f"@{self.owner}", update["body"])
        self.assertNotIn("labels", update)
        self.assertNotIn("assignee", update)
        self.assertNotIn("assignees", update)

        creation = self.payload(calls[4])
        self.assertEqual([tracker.LABEL], creation["labels"])
        self.assertEqual(tracker.issue_title(current_z), creation["title"])
        self.assertEqual(tracker.issue_body(current_z, self.owner), creation["body"])
        self.assertIn(f"@{self.owner}", creation["body"])
        self.assertNotIn("assignee", creation)
        self.assertNotIn("assignees", creation)

        self.assertEqual({"state": "closed"}, self.payload(calls[5]))
        self.assertEqual({"state": "closed"}, self.payload(calls[6]))
        mutated_numbers = [call[0][4].rsplit("/", 1)[-1] for call in calls[3:]]
        self.assertEqual(["3", "issues", "10", "13"], mutated_numbers)

    def test_path_rename_creates_new_issue_before_closing_old_path(self):
        old_path = "src/Old Bank-ru_/categories"
        new_path = "src/New Bank-ru_/categories"
        status, _, calls = self.run_tracker(
            [
                self.completed([new_path]),
                self.label_response(),
                self.list_response([self.issue(14, tracker.marker_for_path(old_path))]),
                self.completed({}),
                self.completed({}),
            ]
        )

        self.assertEqual(0, status)
        self.assert_endpoint(calls[3], "POST", f"repos/{self.repository}/issues")
        self.assertEqual(new_path, tracker.path_from_body(self.payload(calls[3])["body"]))
        self.assert_endpoint(calls[4], "PATCH", f"repos/{self.repository}/issues/14")
        self.assertEqual({"state": "closed"}, self.payload(calls[4]))

    def test_absent_pending_path_is_closed_when_it_becomes_active(self):
        formerly_pending = "src/Active Bank-ru_/categories"
        status, _, calls = self.run_tracker(
            [
                self.completed([]),
                self.label_response(),
                self.list_response(
                    [self.issue(22, tracker.marker_for_path(formerly_pending))]
                ),
                self.completed({}),
            ]
        )

        self.assertEqual(0, status)
        self.assertEqual(4, len(calls))
        self.assert_endpoint(calls[3], "PATCH", f"repos/{self.repository}/issues/22")
        self.assertEqual({"state": "closed"}, self.payload(calls[3]))

    def test_mutation_failure_stops_later_writes_after_partial_progress(self):
        current_a = "src/Alpha-ru_/categories"
        current_b = "src/Beta-ru_/categories"
        resolved_a = "src/Resolved A-ru_/categories"
        resolved_b = "src/Resolved B-ru_/categories"
        cases = (
            (
                "create",
                [current_a, current_b],
                [],
                [self.completed({}), self.failed("create failed")],
                f"repos/{self.repository}/issues",
            ),
            (
                "update",
                [current_a, current_b],
                [
                    self.issue(8, tracker.marker_for_path(current_a)),
                    self.issue(9, tracker.marker_for_path(current_b)),
                ],
                [self.completed({}), self.failed("update failed")],
                f"repos/{self.repository}/issues/9",
            ),
            (
                "close",
                [],
                [
                    self.issue(4, tracker.marker_for_path(resolved_a)),
                    self.issue(5, tracker.marker_for_path(resolved_b)),
                ],
                [self.completed({}), self.failed("close failed")],
                f"repos/{self.repository}/issues/5",
            ),
        )
        for case, paths, issues, mutations, failed_endpoint in cases:
            with self.subTest(case=case):
                status, _, calls = self.run_tracker(
                    [
                        self.completed(paths),
                        self.label_response(),
                        self.list_response(issues),
                        *mutations,
                    ]
                )

                self.assertEqual(1, status)
                self.assertEqual(5, len(calls))
                self.assertEqual(failed_endpoint, calls[-1][0][4])


if __name__ == "__main__":
    unittest.main()
