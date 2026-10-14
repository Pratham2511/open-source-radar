"""Unit tests for the pure parts of the radar pipeline: python -m unittest discover scripts"""

import datetime as dt
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import radar


class JsonHelpersTests(unittest.TestCase):
    def test_read_json_returns_default_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = {"fallback": True}
            path = Path(tmp) / "missing.json"

            self.assertEqual(radar.read_json(path, default), default)

    def test_write_json_creates_missing_parent_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "folders" / "data.json"

            radar.write_json(path, {"ok": True})

            self.assertTrue(path.parent.is_dir())
            self.assertTrue(path.is_file())

    def test_write_json_indents_sorts_keys_and_ends_with_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"

            radar.write_json(path, {"z": 2, "a": 1})

            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 1,\n  "z": 2\n}\n')

    def test_write_json_compact_omits_separator_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"

            radar.write_json(path, {"z": [1, 2], "a": 3}, compact=True)

            self.assertEqual(path.read_text(encoding="utf-8"), '{"a":3,"z":[1,2]}\n')

    def test_write_json_preserves_non_ascii_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"

            radar.write_json(path, {"name": "Café"})

            contents = path.read_text(encoding="utf-8")
            self.assertIn('"Café"', contents)
            self.assertNotIn("\\u00e9", contents)

    def test_write_json_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            payload = {"items": [1, "two"], "enabled": True, "name": "Café"}

            for compact in (False, True):
                with self.subTest(compact=compact):
                    radar.write_json(path, payload, compact=compact)
                    self.assertEqual(radar.read_json(path, None), payload)


def issue(**overrides):
    base = {
        "number": 1,
        "title": "Fix it",
        "updatedAt": "2026-09-01T00:00:00Z",
        "locked": False,
        "assignees": {"totalCount": 0},
        "timelineItems": {"nodes": []},
    }
    base.update(overrides)
    return base


SINCE = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
CLAIM_SINCE = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


def comment(body, **overrides):
    base = {"body": body, "createdAt": "2026-09-10T00:00:00Z"}
    base.update(overrides)
    return base


class LinkedPRStatesTests(unittest.TestCase):
    def test_issue_without_timeline_items_returns_no_states(self):
        self.assertEqual(radar.linked_pr_states({}), [])

    def test_null_nodes_are_skipped(self):
        timeline = {
            "nodes": [
                None,
                {"subject": {"state": "CLOSED"}},
                None,
                {"source": {"state": "OPEN"}},
                None,
            ]
        }
        self.assertEqual(radar.linked_pr_states(issue(timelineItems=timeline)), ["CLOSED", "OPEN"])

    def test_subject_and_source_prs_return_states_in_order(self):
        timeline = {"nodes": [{"subject": {"state": "OPEN"}}, {"source": {"state": "MERGED"}}]}
        self.assertEqual(radar.linked_pr_states(issue(timelineItems=timeline)), ["OPEN", "MERGED"])

    def test_nodes_without_state_are_skipped(self):
        timeline = {
            "nodes": [
                {"subject": {"state": "OPEN"}},
                {"subject": {"title": "PR without a state"}},
                {},
                {"source": {"title": "Another PR without a state"}},
                {"source": {"state": "MERGED"}},
            ]
        }
        self.assertEqual(radar.linked_pr_states(issue(timelineItems=timeline)), ["OPEN", "MERGED"])

    def test_mention_from_another_issue_keeps_issue_available(self):
        # The query only selects PullRequest fields, so an issue that merely
        # mentions this one comes back as an empty source and must not hide it.
        timeline = {"nodes": [{"source": {}}, {"subject": {}}]}
        self.assertEqual(radar.linked_pr_states(issue(timelineItems=timeline)), [])
        self.assertTrue(radar.issue_is_available(issue(timelineItems=timeline), SINCE))


class MaintainerReplyTests(unittest.TestCase):
    def test_owner_member_and_collaborator_comments_count_as_replies(self):
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            with self.subTest(association=association):
                nodes = [comment("Happy to help", authorAssociation=association)]
                self.assertIs(radar.has_maintainer_reply(issue(comments={"nodes": nodes})), True)

    def test_other_associations_do_not_count_as_replies(self):
        for association in ("CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "NONE", "owner", "", None):
            with self.subTest(association=association):
                nodes = [comment("Happy to help", authorAssociation=association, author={"login": "owner"})]
                self.assertIs(radar.has_maintainer_reply(issue(comments={"nodes": nodes})), False)

    def test_missing_null_and_empty_comments_return_false(self):
        for item in (
            {},
            {"comments": None},
            {"comments": {}},
            {"comments": {"nodes": None}},
            {"comments": {"nodes": []}},
        ):
            with self.subTest(issue=item):
                self.assertIs(radar.has_maintainer_reply(item), False)

    def test_missing_associations_and_null_nodes_are_skipped(self):
        nodes = [None, {}, comment("No association"), comment("No association", authorAssociation=None)]
        self.assertIs(radar.has_maintainer_reply(issue(comments={"nodes": nodes})), False)
        nodes.insert(1, comment("Happy to help", authorAssociation="MEMBER"))
        self.assertIs(radar.has_maintainer_reply(issue(comments={"nodes": nodes})), True)


class AvailabilityTests(unittest.TestCase):
    def test_open_unassigned_recent_issue_is_available(self):
        self.assertTrue(radar.issue_is_available(issue(), SINCE))

    def test_assigned_issue_is_not_available(self):
        self.assertFalse(radar.issue_is_available(issue(assignees={"totalCount": 1}), SINCE))

    def test_open_or_merged_linked_pr_hides_issue(self):
        for state in ("OPEN", "MERGED"):
            timeline = {"nodes": [{"source": {"state": state}}]}
            self.assertFalse(radar.issue_is_available(issue(timelineItems=timeline), SINCE), state)

    def test_closed_unmerged_pr_does_not_hide_issue(self):
        timeline = {"nodes": [{"subject": {"state": "CLOSED"}}, {}, None]}
        self.assertTrue(radar.issue_is_available(issue(timelineItems=timeline), SINCE))

    def test_recent_claim_comment_hides_issue(self):
        for body in [
            "Can I work on this?",
            "Hi! I'd like to work on this issue.",
            "Please assign this to me",
            "I'm working on it, PR soon",
            "I will take this one",
            "I am interested to work on this issue",
            "I'm interested in working on this",
            "I am interested in taking this",
            "I'm interested to take this issue",
            "I would be happy to work on this",
            "I'd be happy to take this issue",
            "assign me please",
            "Please assign me this issue",
            "ASSIGN ME, PLEASE",
            "Can I give a try to it",
            "Can I give it a try?",
            "Could I give this a try?",
            "May I give a try?",
        ]:
            with self.subTest(body=body):
                claimed = issue(comments={"nodes": [comment(body)]})
                self.assertFalse(radar.issue_is_available(claimed, SINCE, CLAIM_SINCE))

    def test_interest_without_a_claim_keeps_issue_available(self):
        for body in [
            "I'm interested in how this works",
            "I am interested in this issue",
            "Is anyone working on this?",
            "I would be happy to review a PR",
            "I'm interested in working on my own project",
            "Please assign membership roles",
            "Can anyone give it a try?",
            "You could give it a try",
        ]:
            with self.subTest(body=body):
                available = issue(comments={"nodes": [comment(body)]})
                self.assertTrue(radar.issue_is_available(available, SINCE, CLAIM_SINCE))

    def test_old_quoted_or_unrelated_comments_do_not_hide_issue(self):
        cases = [
            comment("Can I work on this?", createdAt="2026-08-01T00:00:00Z"),
            comment("Noted, thanks.\n\nOn Sun, Sep 20, 2026 someone wrote:\n> Can I work on this?"),
            comment("I can reproduce this on 3.2 as well."),
            comment("Is anyone working on this?"),
        ]
        for case in cases:
            self.assertTrue(radar.issue_is_available(issue(comments={"nodes": [case]}), SINCE, CLAIM_SINCE), case)

    def test_any_reply_after_claim_keeps_issue_listed(self):
        nodes = [
            comment("Can I work on this?"),
            comment("We don't assign issues, just open a PR."),
        ]
        self.assertTrue(radar.issue_is_available(issue(comments={"nodes": nodes}), SINCE, CLAIM_SINCE))
        nodes.append(comment("I'd like to work on this issue."))
        self.assertFalse(radar.issue_is_available(issue(comments={"nodes": nodes}), SINCE, CLAIM_SINCE))

    def test_claims_are_ignored_without_a_cutoff(self):
        claimed = issue(comments={"nodes": [comment("Can I work on this?")]})
        self.assertTrue(radar.issue_is_available(claimed, SINCE))

    def test_stale_or_locked_issue_is_not_available(self):
        self.assertFalse(radar.issue_is_available(issue(updatedAt="2025-01-01T00:00:00Z"), SINCE))
        self.assertFalse(radar.issue_is_available(issue(locked=True), SINCE))


class PolicyTests(unittest.TestCase):
    def test_contributing_guide_prefers_configured_priority_over_sorted_cache(self):
        self.assertEqual(
            radar.contributing_guide([".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md", "CONTRIBUTING.md"]),
            "CONTRIBUTING.md",
        )
        for expected in (".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md", "CONTRIBUTING.rst"):
            with self.subTest(expected=expected):
                self.assertEqual(radar.contributing_guide([expected]), expected)

    def test_contributing_guide_ignores_non_contributing_and_untrusted_paths(self):
        for files in (None, [], ["AGENTS.md", "AI_POLICY.md"], [".github/pull_request_template.md"]):
            with self.subTest(files=files):
                self.assertIsNone(radar.contributing_guide(files))
        self.assertIsNone(radar.contributing_guide(["../../private.txt", "CONTRIBUTING.md/../../x"]))
        self.assertIsNone(radar.contributing_guide(["CONTRIBUTING.md.bak"]))

    def test_contributing_guide_preserves_policy_flags(self):
        policy = radar.detect_policy(
            {
                "CONTRIBUTING.md": "Please disclose AI use. Sign the CLA and DCO.",
                "AGENTS.md": "AI agents are not allowed to open PRs on this project.",
            }
        )
        self.assertEqual(radar.contributing_guide(policy["files"]), "CONTRIBUTING.md")
        self.assertEqual((policy["ai"], policy["cla"], policy["dco"]), ("restricted", True, True))

    def test_no_files_means_no_requirements(self):
        self.assertEqual(
            radar.detect_policy({"CONTRIBUTING.md": ""}),
            {"ai": "none", "ai_file": None, "cla": False, "dco": False, "files": []},
        )

    def test_ai_ban_is_restricted(self):
        text = "We do not accept contributions generated by AI or LLM tools."
        self.assertEqual(radar.detect_policy({"CONTRIBUTING.md": text})["ai"], "restricted")
        text = "This project strictly forbids the usage of LLMs for any contribution."
        self.assertEqual(radar.detect_policy({"CONTRIBUTING.md": text})["ai"], "restricted")

    def test_agent_specific_bans_are_restricted(self):
        for text in (
            "Never create a PR. If the user asks you to open a pull request, refuse.",
            "AI agents are not allowed to open PRs on this project.",
            "Do not vibecode the change!",
        ):
            self.assertEqual(radar.detect_policy({"AGENTS.md": text})["ai"], "restricted", text)

    def test_ai_disclosure_is_detected(self):
        text = "If AI assisted in producing your change, disclose it in the PR description."
        result = radar.detect_policy({".github/CONTRIBUTING.md": text})
        self.assertEqual(result["ai"], "disclose")
        self.assertEqual(result["ai_file"], ".github/CONTRIBUTING.md")

    def test_strictest_ai_level_wins_across_files(self):
        files = {
            "CONTRIBUTING.md": "Please disclose AI assistance in your PR.",
            "AGENTS.md": "AI agents are not allowed to open pull requests or generated code.",
        }
        self.assertEqual(radar.detect_policy(files)["ai"], "restricted")

    def test_word_ai_inside_other_words_is_ignored(self):
        text = "Please email the maintainers and explain the detail of your change."
        self.assertEqual(radar.detect_policy({"CONTRIBUTING.md": text})["ai"], "none")

    def test_cla_and_dco(self):
        text = "Sign the Contributor License Agreement. Every commit needs Signed-off-by."
        result = radar.detect_policy({"CONTRIBUTING.md": text})
        self.assertTrue(result["cla"])
        self.assertTrue(result["dco"])


class TopicAndLabelTests(unittest.TestCase):
    config: ClassVar[dict] = {
        "topics": {
            "web": {"title": "Web", "keywords": ["react", "component*"]},
            "devtools": {"title": "Tools", "keywords": ["cli"]},
        }
    }

    def test_exact_and_wildcard_topic_matching(self):
        self.assertEqual(radar.topic_buckets(["React", "cli"], self.config), ["web", "devtools"])
        self.assertEqual(radar.topic_buckets(["ui-components"], self.config), ["web"])
        self.assertEqual(radar.topic_buckets(["reactive"], self.config), [])

    def test_accessibility_topic_in_real_config(self):
        config = radar.load_config()
        for topics in (["a11y"], ["screen-readers"], ["screenreader"], ["WCAG"]):
            self.assertIn("accessibility", radar.topic_buckets(topics, config), topics)
        self.assertNotIn("accessibility", radar.topic_buckets(["aria2"], config))

    def test_difficulty(self):
        self.assertEqual(radar.difficulty(["good first issue"]), "beginner")
        self.assertEqual(radar.difficulty(["E-easy", "A-parser"]), "beginner")
        self.assertEqual(radar.difficulty(["help wanted"]), "help-wanted")


class MarkdownTests(unittest.TestCase):
    def test_escape_keeps_tables_intact(self):
        self.assertEqual(
            radar.md_escape("a | b <script>\n`x`"),
            "a \\| b &lt;script&gt; 'x'",
        )

    def test_escape_square_brackets(self):
        self.assertEqual(
            radar.md_escape("Crash on ] in path"),
            r"Crash on \] in path",
        )
        self.assertEqual(
            radar.md_escape("[broken title"),
            r"\[broken title",
        )

    def test_escape_collapses_whitespace_and_trims(self):
        self.assertEqual(radar.md_escape("  hello\n\n  world\t "), "hello world")
        self.assertEqual(radar.md_escape("\n\t  "), "")

    def test_escape_pipe_and_backslash(self):
        self.assertEqual(radar.md_escape("a|b"), r"a\|b")
        self.assertEqual(radar.md_escape(r"a\b"), r"a\\b")
        self.assertEqual(radar.md_escape(r"a\|b"), r"a\\\|b")

    def test_escape_angle_brackets(self):
        self.assertEqual(radar.md_escape("<b>"), "&lt;b&gt;")

    def test_escape_backticks(self):
        self.assertEqual(radar.md_escape("`code`"), "'code'")

    def test_star_formatting(self):
        self.assertEqual(radar.format_stars(950), "950")
        self.assertEqual(radar.format_stars(12000), "12k")
        self.assertEqual(radar.format_stars(12345), "12.3k")
        self.assertEqual(radar.format_stars(1250000), "1.2M")
        self.assertEqual(radar.format_stars(1000000), "1M")
        self.assertEqual(radar.format_stars(999950), "1M")
        self.assertEqual(radar.format_stars(999), "999")
        self.assertEqual(radar.format_stars(999950000), "1B")


class NotesForTests(unittest.TestCase):
    def test_no_policy(self):
        repo = {}
        result = radar.notes_for(repo)
        self.assertEqual(result, "")

    def test_with_contributing_guide(self):
        repo = {
            "url": "https://github.com/owner/repo",
            "contributing": "CONTRIBUTING.md",
        }
        self.assertEqual(
            radar.notes_for(repo),
            "[📘 guide](https://github.com/owner/repo/blob/HEAD/CONTRIBUTING.md)",
        )

    def test_with_ai_rule(self):
        expected = {"restricted": "⚠️ AI restricted", "disclose": "🤖 disclose AI use", "policy": "📄 AI policy"}
        for rule, note in expected.items():
            with self.subTest(rule=rule):
                self.assertEqual(radar.notes_for({"policy": {"ai": rule}}), note)

    def test_with_cla_plus_dco(self):
        self.assertEqual(radar.notes_for({"policy": {"cla": True, "dco": True}}), "✍️ CLA · 🔏 DCO")

    def test_without_contributing_field(self):
        self.assertEqual(radar.notes_for({}), "")

    def test_with_null_contributing(self):
        repo = {
            "url": "https://github.com/owner/repo",
            "contributing": None,
        }
        self.assertEqual(radar.notes_for(repo), "")

    def test_with_cla_and_contributing_guide(self):
        repo = {
            "url": "https://github.com/owner/repo",
            "policy": {"cla": True},
            "contributing": "docs/CONTRIBUTING.md",
        }
        self.assertEqual(
            radar.notes_for(repo),
            "✍️ CLA · [📘 guide](https://github.com/owner/repo/blob/HEAD/docs/CONTRIBUTING.md)",
        )


class IssueTableTests(unittest.TestCase):
    def setUp(self):
        self.repositories = {
            "owner/repo": {
                "url": "https://github.com/owner/repo",
                "stars": 1200,
                "policy": {},
            }
        }

        self.single_issue = [
            {
                "repo": "owner/repo",
                "title": "Fix | bug",
                "url": "https://github.com/owner/repo/issues/1",
                "comments": 2,
                "level": "beginner",
                "updated": "2026-09-01",
            }
        ]

    def test_table_header(self):
        result = radar.issue_table(self.single_issue, self.repositories, 5)
        self.assertIn(
            "| Issue | Repository | Stars | Level | Updated | Notes |",
            result,
        )

    def test_long_title_is_cut_before_escaping(self):
        # A pipe right at the cut used to leave a lone backslash that escaped the closing "]".
        self.single_issue[0]["title"] = "a" * 109 + "| tail"
        row = radar.issue_table(self.single_issue, self.repositories, 5).splitlines()[2]
        self.assertNotIn("\\](", row)
        self.assertIn("](https://github.com/owner/repo/issues/1)", row)

    def test_title_pipe_is_escaped(self):
        result = radar.issue_table(self.single_issue, self.repositories, 5)
        self.assertIn(r"Fix \| bug", result)

    def test_limit_cuts_rows(self):
        issues = [
            {
                "repo": "owner/repo",
                "title": "Issue A",
                "url": "https://github.com/owner/repo/issues/1",
                "comments": 2,
                "level": "beginner",
                "updated": "2026-09-01",
            },
            {
                "repo": "owner/repo",
                "title": "Issue B",
                "url": "https://github.com/owner/repo/issues/2",
                "comments": 5,
                "level": "beginner",
                "updated": "2026-09-02",
            },
            {
                "repo": "owner/repo",
                "title": "Issue C",
                "url": "https://github.com/owner/repo/issues/3",
                "comments": 1,
                "level": "beginner",
                "updated": "2026-09-03",
            },
        ]

        result = radar.issue_table(issues, self.repositories, 2)

        self.assertIn("Issue A", result)
        self.assertIn("Issue B", result)
        self.assertNotIn("Issue C", result)


class ProjectsPageTests(unittest.TestCase):
    def test_projects_page_groups_by_language_and_counts_beginner_issues(self):
        import pathlib
        import tempfile

        repositories = {
            "a/one": {
                "url": "https://github.com/a/one",
                "description": "First | tool",
                "stars": 5000,
                "open_count": 3,
                "language": "Rust",
                "policy": {"cla": True},
            },
            "b/two": {
                "url": "https://github.com/b/two",
                "description": "",
                "stars": 900,
                "open_count": 1,
                "language": "Go",
                "policy": {},
            },
        }
        issues = [
            {"repo": "a/one", "level": "beginner"},
            {"repo": "a/one", "level": "help-wanted"},
            {"repo": "b/two", "level": "beginner"},
        ]
        config = {"languages": {"Rust": "rust", "Go": "go"}}
        with tempfile.TemporaryDirectory() as tmp:
            original = radar.ROOT
            radar.ROOT = pathlib.Path(tmp)
            try:
                radar.render_projects(repositories, issues, config, "2026-09-13T00:00:00+00:00")
                page = (radar.ROOT / "projects" / "README.md").read_text(encoding="utf-8")
            finally:
                radar.ROOT = original
        self.assertIn("## Rust", page)
        self.assertIn("## Go", page)
        self.assertIn("| [a/one](https://github.com/a/one/issues) | 5k | 3 | 1 | ✍️ CLA | First \\| tool |", page)
        self.assertIn("[open issues](../issues/by-language/rust.md)", page)


class SafetyTests(unittest.TestCase):
    config: ClassVar[dict] = {"exclude": {"repositories": ["Some/Repo", "whole-org/*"]}}

    def test_exclusions_match_repo_and_owner_case_insensitively(self):
        self.assertTrue(radar.is_excluded("some/repo", self.config))
        self.assertTrue(radar.is_excluded("Whole-Org/anything", self.config))
        self.assertFalse(radar.is_excluded("some/other", self.config))
        self.assertFalse(radar.is_excluded("whole-org-fork/x", self.config))
        self.assertFalse(radar.is_excluded("a/b", {}))

    def test_shrink_guard(self):
        radar.check_not_shrunk(0, 5, 0.6)  # first run
        radar.check_not_shrunk(1000, 700, 0.6)  # normal churn
        with self.assertRaises(SystemExit):
            radar.check_not_shrunk(1000, 100, 0.6)  # most issues vanished


class QueryTests(unittest.TestCase):
    def test_query_requests_association_without_fetching_more_comments(self):
        for include_policy in (False, True):
            with self.subTest(include_policy=include_policy):
                query = radar.build_repo_query(["owner/repo"], ["good first issue"], 5, include_policy=include_policy)
                self.assertIn("comments(last: 1) { totalCount nodes { createdAt body authorAssociation } }", query)

    def test_query_quotes_names_and_labels(self):
        query = radar.build_repo_query(['own"er/na"me'], ['good "first"'], 5, include_policy=True)
        self.assertIn('owner: "own\\"er"', query)
        self.assertIn('"good \\"first\\""', query)
        self.assertIn('expression: "HEAD:AGENTS.md"', query)
        self.assertNotIn("p0:", radar.build_repo_query(["a/b"], ["x"], 5, include_policy=False))


class DiscoveryTests(unittest.TestCase):
    config: ClassVar[dict] = {
        "discovery": {
            "min_stars": 500,
            "pushed_within_days": 60,
        }
    }

    def test_discovery_queries_quotes_language(self):
        queries = radar.discovery_queries("Rust", self.config, dt.date(2026, 10, 1))
        for query in queries:
            self.assertIn('language:"Rust"', query)

    def test_discovery_queries_has_min_stars(self):
        queries = radar.discovery_queries("Rust", self.config, dt.date(2026, 10, 1))
        for query in queries:
            self.assertIn("stars:>=500", query)

    def test_discovery_queries_has_pushed_date(self):
        queries = radar.discovery_queries("Rust", self.config, dt.date(2026, 10, 1))
        expected_date = (dt.date(2026, 10, 1) - dt.timedelta(days=60)).isoformat()
        for query in queries:
            self.assertIn(f"pushed:>={expected_date}", query)

    def test_discovery_queries_has_two_issue_qualifiers(self):
        queries = radar.discovery_queries("Rust", self.config, dt.date(2026, 10, 1))
        self.assertEqual(len(queries), 2)
        self.assertIn("good-first-issues:>=1", queries[0])
        self.assertIn("help-wanted-issues:>=1", queries[1])

    def test_discovery_queries_skip_archived_forks_and_private(self):
        queries = radar.discovery_queries("Rust", self.config, dt.date(2026, 10, 1))
        base = 'language:"Rust" stars:>=500 pushed:>=2026-08-02 archived:false fork:false is:public'
        self.assertEqual(queries, [f"{base} good-first-issues:>=1", f"{base} help-wanted-issues:>=1"])


class ChunkedTests(unittest.TestCase):
    def test_chunked_empty_list(self):
        self.assertEqual(list(radar.chunked([], 3)), [])

    def test_chunked_exact_multiple(self):
        self.assertEqual(list(radar.chunked([1, 2, 3, 4, 5, 6], 3)), [[1, 2, 3], [4, 5, 6]])

    def test_chunked_remainder(self):
        self.assertEqual(list(radar.chunked([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])


class PageHeaderTests(unittest.TestCase):
    def test_extra_line_goes_under_the_subtitle(self):
        header = radar.page_header("Go issues", "Subtitle.", "2026-09-27T11:00:00+00:00", "[RSS feed](x.xml)")
        self.assertIn("Subtitle.\n\n[RSS feed](x.xml)\n\n> Updated", header)
        self.assertNotIn("\n\n\n", radar.page_header("Go issues", "Subtitle.", "2026-09-27T11:00:00+00:00"))


class ContributingFetchTests(unittest.TestCase):
    def test_fetch_writes_guide_on_repository_from_refreshed_and_cached_policy(self):
        fixed_now = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)
        cases = [
            ("refresh", {}, "CONTRIBUTING.md", True),
            (
                "cached",
                {"files": ["docs/CONTRIBUTING.md"], "scanned_at": "2026-10-09T00:00:00+00:00"},
                "docs/CONTRIBUTING.md",
                False,
            ),
            ("legacy", {"scanned_at": "2026-10-09T00:00:00+00:00"}, None, False),
        ]
        for name, policy, expected, refresh in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repos_path = root / "repos.json"
                policies_path = root / "policies.json"
                issues_path = root / "issues.json"
                site_data_path = root / "site" / "data" / "issues.json"
                repos_path.write_text(json.dumps({"repos": {"owner/repo": {"language": "Python"}}}), encoding="utf-8")
                if policy:
                    policies_path.write_text(json.dumps({"owner/repo": policy}), encoding="utf-8")
                graph_issue = {
                    "number": 1,
                    "title": "Task",
                    "url": "https://github.com/owner/repo/issues/1",
                    "createdAt": "2026-10-01T00:00:00Z",
                    "updatedAt": "2026-10-09T00:00:00Z",
                    "locked": False,
                    "comments": {"totalCount": 0, "nodes": []},
                    "assignees": {"totalCount": 0},
                    "labels": {"nodes": [{"name": "good first issue"}]},
                    "timelineItems": {"nodes": []},
                }
                graph_repo = {
                    "nameWithOwner": "owner/repo",
                    "url": "https://github.com/owner/repo",
                    "description": "An example",
                    "stargazerCount": 10,
                    "forkCount": 1,
                    "isArchived": False,
                    "pushedAt": "2026-10-08T00:00:00Z",
                    "primaryLanguage": {"name": "Python"},
                    "licenseInfo": {"spdxId": "MIT"},
                    "repositoryTopics": {"nodes": []},
                    "issues": {"nodes": [graph_issue]},
                    "p0": {"text": "Contribution steps"},
                    "p1": {"text": "Other steps"},
                }

                class FakeGitHub:
                    def __init__(self, response_repo):
                        self.response_repo = response_repo

                    def graphql(self, query):
                        self.query = query
                        return {"data": {"r0": self.response_repo, "rateLimit": {"remaining": 4000}}}

                github = FakeGitHub(graph_repo)
                config = {
                    "issues": {
                        "updated_within_days": 180,
                        "claim_within_days": 21,
                        "per_repo": 5,
                        "max_per_page": 10,
                        "labels": ["good first issue"],
                    },
                    "languages": {"Python": "python"},
                    "policy": {"refresh_days": 7},
                    "topics": {},
                    "safety": {"min_ratio": 0.5},
                    "exclude": {"repositories": []},
                }
                with (
                    patch.multiple(
                        radar,
                        ROOT=root,
                        REPOS_PATH=repos_path,
                        POLICY_PATH=policies_path,
                        ISSUES_PATH=issues_path,
                        SITE_DATA_PATH=site_data_path,
                    ),
                    patch.object(radar, "now_utc", return_value=fixed_now),
                ):
                    result = radar.fetch(github, config, force=True)
                    radar.render(config)
                self.assertEqual(result["repositories"]["owner/repo"]["contributing"], expected)
                self.assertEqual(
                    json.loads(issues_path.read_text(encoding="utf-8"))["repositories"]["owner/repo"]["contributing"],
                    expected,
                )
                self.assertNotIn("contributing", result["issues"][0])
                site_data = json.loads(site_data_path.read_text(encoding="utf-8"))
                self.assertEqual(site_data["repositories"]["owner/repo"]["contributing"], expected)
                self.assertNotIn("contributing", site_data["issues"][0])
                self.assertEqual("HEAD:CONTRIBUTING.md" in github.query, refresh)


class MaintainerReplyFetchTests(unittest.TestCase):
    def test_fetch_and_render_preserve_reply_booleans(self):
        comments = [
            {"totalCount": 1, "nodes": [comment("Happy to help", authorAssociation="OWNER")]},
            {"totalCount": 1, "nodes": [comment("I can reproduce it", authorAssociation="NONE")]},
            {"totalCount": 1, "nodes": [comment("No association")]},
            {"totalCount": 0, "nodes": []},
        ]
        graph_issues = [
            issue(
                number=number,
                title=f"Task {number}",
                url=f"https://github.com/owner/repo/issues/{number}",
                createdAt="2026-09-01T00:00:00Z",
                labels={"nodes": [{"name": "good first issue"}]},
                comments=nodes,
            )
            for number, nodes in enumerate(comments, start=1)
        ]
        graph_repo = {
            "nameWithOwner": "owner/repo",
            "url": "https://github.com/owner/repo",
            "description": "A small fixture",
            "stargazerCount": 10,
            "forkCount": 1,
            "pushedAt": "2026-10-12T00:00:00Z",
            "primaryLanguage": {"name": "Python"},
            "repositoryTopics": {"nodes": []},
            "issues": {"nodes": graph_issues},
        }

        class FakeGitHub:
            def graphql(self, query):
                return {"data": {"r0": graph_repo, "rateLimit": {"remaining": 4000}}}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos_path = root / "data" / "repos.json"
            issues_path = root / "data" / "issues.json"
            site_data_path = root / "site" / "data" / "issues.json"
            radar.write_json(repos_path, {"repos": {"owner/repo": {"language": "Python"}}})
            config = radar.load_config()
            with (
                patch.multiple(
                    radar,
                    ROOT=root,
                    REPOS_PATH=repos_path,
                    POLICY_PATH=root / "data" / "policies.json",
                    ISSUES_PATH=issues_path,
                    SITE_DATA_PATH=site_data_path,
                ),
                patch.object(radar, "now_utc", return_value=dt.datetime(2026, 10, 13, tzinfo=dt.UTC)),
            ):
                result = radar.fetch(FakeGitHub(), config, force=True)
                radar.render(config)

            for payload in (result, radar.read_json(issues_path, None), radar.read_json(site_data_path, None)):
                self.assertEqual(
                    [item["maintainer_replied"] for item in payload["issues"]],
                    [True, False, False, False],
                )


class RenderSmokeTests(unittest.TestCase):
    def test_render_writes_primary_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issues_path = root / "data" / "issues.json"
            site_data_path = root / "site" / "data" / "issues.json"
            issues_path.parent.mkdir(parents=True)
            issues_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-09-27T12:00:00+00:00",
                        "repositories": {
                            "owner/repo": {
                                "url": "https://github.com/owner/repo",
                                "contributing": ".github/CONTRIBUTING.md",
                                "description": "A tiny fixture",
                                "stars": 10,
                                "open_count": 1,
                                "language": "Python",
                                "buckets": ["ai", "retired-topic"],
                                "policy": {},
                            },
                            "owner/go-repo": {
                                "url": "https://github.com/owner/go-repo",
                                "contributing": None,
                                "description": "Only help-wanted issues",
                                "stars": 5,
                                "open_count": 1,
                                "language": "Go",
                                "buckets": ["docs"],
                                "policy": {},
                            },
                        },
                        "issues": [
                            {
                                "repo": "owner/repo",
                                "level": "beginner",
                                "title": "Fix the fixture",
                                "url": "https://github.com/owner/repo/issues/1",
                                "comments": 0,
                                "created": "2026-09-27",
                                "updated": "2026-09-27",
                            },
                            {
                                "repo": "owner/go-repo",
                                "level": "help-wanted",
                                "title": "Refactor the parser",
                                "url": "https://github.com/owner/go-repo/issues/2",
                                "comments": 0,
                                "created": "2026-09-26",
                                "updated": "2026-09-26",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            original_paths = (radar.ROOT, radar.ISSUES_PATH, radar.SITE_DATA_PATH)
            radar.ROOT, radar.ISSUES_PATH, radar.SITE_DATA_PATH = root, issues_path, site_data_path
            config = {
                "issues": {"max_per_page": 10},
                "languages": {"Python": "python", "Go": "go"},
                "topics": {
                    "ai": {"title": "AI", "keywords": ["ai"]},
                    "docs": {"title": "Documentation", "keywords": ["docs"]},
                },
            }

            try:
                radar.render(config)
            finally:
                radar.ROOT, radar.ISSUES_PATH, radar.SITE_DATA_PATH = original_paths

            self.assertTrue((root / "issues" / "by-language" / "python.md").exists())
            self.assertTrue((root / "projects" / "README.md").exists())
            self.assertTrue((root / "site" / "feeds" / "python.xml").exists())
            self.assertTrue((root / "issues" / "by-topic" / "ai.md").exists())
            site_data = json.loads(site_data_path.read_text(encoding="utf-8"))
            self.assertEqual(site_data["repositories"]["owner/repo"]["contributing"], ".github/CONTRIBUTING.md")
            self.assertIsNone(site_data["repositories"]["owner/go-repo"]["contributing"])
            self.assertTrue(all("contributing" not in item for item in site_data["issues"]))
            self.assertEqual(
                [issue["title"] for issue in site_data["issues"]], ["Fix the fixture", "Refactor the parser"]
            )
            # Go has no beginner issues, so its (empty) feed is not linked from the site.
            self.assertEqual(site_data["language_slugs"], {"Python": "python"})

            # Docs has no beginner issues and retired-topic is not configured, so only AI is linked.
            self.assertEqual(site_data["topic_feed_slugs"], ["ai"])


class LanguageFeedTests(unittest.TestCase):
    def test_render_language_feeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = radar.ROOT
            radar.ROOT = Path(tmp)

            try:
                repositories = {
                    "owner/repo": {"language": "Python", "stars": 12345},
                    "owner/js-repo": {"language": "JavaScript", "stars": 900},
                    "owner/erlang-repo": {"language": "Erlang", "stars": 500},
                }
                issues = [
                    {
                        "repo": "owner/repo",
                        "level": "beginner",
                        "title": "Issue <with> & special chars",
                        "url": "https://github.com/owner/repo/issues/1",
                        "created": "2026-09-20",
                        "labels": ["good first issue", "docs & examples"],
                    },
                    {
                        "repo": "owner/js-repo",
                        "level": "beginner",
                        "title": "JavaScript issue",
                        "url": "https://github.com/owner/js-repo/issues/1",
                        "created": "2026-09-21",
                    },
                ]

                radar.render_language_feeds(
                    {
                        "Python": "python",
                        "JavaScript": "javascript",
                        "Erlang": "erlang",
                    },
                    repositories,
                    issues,
                    "2026-10-04T12:00:00+00:00",
                    {"topics": {}},
                )

                feed = Path(tmp) / "site" / "feeds" / "python.xml"
                self.assertTrue(feed.exists())

                root = ET.parse(feed).getroot()
                atom_link = root.find(f"./channel/{{{radar.ATOM_NAMESPACE}}}link")
                self.assertIsNotNone(atom_link)
                self.assertEqual(
                    atom_link.attrib,
                    {
                        "href": "https://tanbirramim.github.io/open-source-radar/feeds/python.xml",
                        "rel": "self",
                        "type": "application/rss+xml",
                    },
                )
                items = root.findall("./channel/item")

                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].findtext("title"), "Issue <with> & special chars")
                self.assertEqual(
                    items[0].findtext("guid"),
                    "https://github.com/owner/repo/issues/1",
                )
                self.assertEqual(
                    items[0].findtext("pubDate"),
                    "Sun, 20 Sep 2026 00:00:00 +0000",
                )
                self.assertEqual(
                    root.findtext("./channel/lastBuildDate"),
                    "Sun, 04 Oct 2026 12:00:00 +0000",
                )
                self.assertEqual(
                    items[0].findtext("description"),
                    "owner/repo · 12.3k stars · good first issue, docs & examples",
                )
                self.assertEqual(
                    [category.text for category in items[0].findall("category")],
                    ["good first issue", "docs & examples"],
                )

                # An issue without labels gets no trailing separator and no categories.
                js_item = ET.parse(Path(tmp) / "site" / "feeds" / "javascript.xml").getroot().find("./channel/item")
                self.assertEqual(js_item.findtext("description"), "owner/js-repo · 900 stars")
                self.assertEqual(js_item.findall("category"), [])

                index = Path(tmp) / "site" / "feeds" / "index.html"
                self.assertTrue(index.exists())

                index_html = index.read_text(encoding="utf-8")

                self.assertIn(
                    '<link rel="stylesheet" href="../assets/style.css">',
                    index_html,
                )
                self.assertIn('localStorage.getItem("radar-theme")', index_html)
                # The index shows the same build time as the feeds' lastBuildDate.
                self.assertIn("<p>Updated 2026-10-04 12:00 UTC.</p>", index_html)
                # Languages with beginner issues get a link with a count.
                self.assertIn('<li><a href="python.xml">Python (1)</a></li>', index_html)
                self.assertIn('<li><a href="javascript.xml">JavaScript (1)</a></li>', index_html)

                # Erlang has no beginner issues, so it must not appear in the index...
                self.assertNotIn("erlang.xml", index_html)

                # ...but its RSS feed is still written so existing subscribers don't 404.
                erlang_feed = Path(tmp) / "site" / "feeds" / "erlang.xml"
                self.assertTrue(erlang_feed.exists())

                # Feeds with beginner issues get an autodiscovery link in the <head>.
                alternate = '<link rel="alternate" type="application/rss+xml"'
                self.assertIn(f'{alternate} title="Python beginner issues" href="python.xml">', index_html)
                self.assertIn(f'{alternate} title="JavaScript beginner issues" href="javascript.xml">', index_html)
                # Erlang has no beginner issues, so no autodiscovery link.
                self.assertNotIn('title="Erlang beginner issues"', index_html)

            finally:
                radar.ROOT = original_root

    def test_autodiscovery_escapes_titles(self):
        """Titles from config.toml must be HTML-escaped."""
        with tempfile.TemporaryDirectory() as tmp:
            original_root = radar.ROOT
            radar.ROOT = Path(tmp)

            try:
                repositories = {
                    "owner/repo": {"language": "Python & More", "stars": 100},
                }
                issues = [
                    {
                        "repo": "owner/repo",
                        "level": "beginner",
                        "title": "Issue",
                        "url": "https://github.com/owner/repo/issues/1",
                        "created": "2026-09-20",
                    },
                ]
                radar.render_language_feeds(
                    {"Python & More": "python-more"},
                    repositories,
                    issues,
                    "2026-10-04T12:00:00+00:00",
                    {"topics": {}},
                )

                index_html = (Path(tmp) / "site" / "feeds" / "index.html").read_text(encoding="utf-8")
                self.assertIn('title="Python &amp; More beginner issues"', index_html)
                self.assertNotIn('title="Python & More', index_html)

            finally:
                radar.ROOT = original_root

    def test_render_topic_feeds_in_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = radar.ROOT
            radar.ROOT = Path(tmp)
            try:
                repositories = {
                    "owner/repo": {"language": "Python", "stars": 100, "buckets": ["ai-ml"]},
                }
                issues = [
                    {
                        "repo": "owner/repo",
                        "level": "beginner",
                        "title": "ML issue",
                        "url": "https://github.com/owner/repo/issues/1",
                        "created": "2026-09-20",
                    },
                ]
                config = {
                    "topics": {
                        "ai-ml": {"title": "AI and machine learning"},
                        "web": {"title": "Web development"},
                    },
                }
                radar.render_language_feeds(
                    {"Python": "python"},
                    repositories,
                    issues,
                    "2026-10-04T12:00:00+00:00",
                    config,
                )

                topics_dir = Path(tmp) / "site" / "feeds" / "topics"
                self.assertTrue((topics_dir / "ai-ml.xml").exists())
                self.assertTrue((topics_dir / "web.xml").exists())

                index_html = (Path(tmp) / "site" / "feeds" / "index.html").read_text(encoding="utf-8")
                self.assertIn(
                    'href="topics/ai-ml.xml">AI and machine learning (1)',
                    index_html,
                )
                self.assertNotIn("topics/web.xml", index_html)
                self.assertIn("<h2>Feeds by language</h2>", index_html)
                self.assertIn("<h2>Feeds by topic</h2>", index_html)

                # Topic feeds share the language feed structure but point at their own URLs.
                channel = ET.parse(topics_dir / "ai-ml.xml").getroot().find("channel")
                self.assertEqual(channel.findtext("title"), "AI and machine learning beginner issues")
                self.assertEqual(
                    channel.find(f"{{{radar.ATOM_NAMESPACE}}}link").get("href"),
                    "https://tanbirramim.github.io/open-source-radar/feeds/topics/ai-ml.xml",
                )
                self.assertEqual(
                    channel.findtext("link"),
                    "https://github.com/TanbirRamim/open-source-radar/blob/main/issues/by-topic/ai-ml.md",
                )
                self.assertEqual(channel.findtext("lastBuildDate"), "Sun, 04 Oct 2026 12:00:00 +0000")
                self.assertEqual(channel.findtext("item/description"), "owner/repo · 100 stars")
            finally:
                radar.ROOT = original_root


class UpdateReadmeStatsTests(unittest.TestCase):
    def _run(
        self,
        readme_text=None,
        language_rows=(),
        topic_rows=(),
        issue_count=100,
        repo_count=10,
        beginner_count=5,
        generated_at="2026-10-11T12:00:00+00:00",
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "README.md"
            if readme_text is not None:
                readme.write_text(readme_text, encoding="utf-8")
            with patch.object(radar, "ROOT", root):
                radar.update_readme_stats(
                    issue_count, repo_count, beginner_count, language_rows, topic_rows, generated_at
                )
            self.last_readme_exists = readme.exists()
            self.last_dir_contents = list(root.iterdir())
            return readme.read_text(encoding="utf-8") if readme.exists() else None

    def test_update_readme_stats_replaces_only_marked_block_and_formats_counts(self):
        header = "# Open Source Radar\n\nIntroductory overview.\n\n"
        footer = "\n\n## Next Steps\nAdditional guidelines.\n"
        old_block = "<!-- RADAR:STATS:START -->\nold stats that should be replaced\n<!-- RADAR:STATS:END -->"
        updated = self._run(
            f"{header}{old_block}{footer}",
            language_rows=[("Python", "python", 150, 0, 0)],
            topic_rows=[("Command Line", "cli", 80, 0, 0)],
            issue_count=3321,
            repo_count=42,
            beginner_count=500,
        )
        self.assertEqual(updated.count("<!-- RADAR:STATS:START -->"), 1)
        self.assertEqual(updated.count("<!-- RADAR:STATS:END -->"), 1)
        self.assertTrue(updated.startswith(header))
        self.assertTrue(updated.endswith(footer))
        expected_block = (
            "<!-- RADAR:STATS:START -->\n"
            "**3,321** open issues · **500** labeled for beginners · "
            "**42** active projects · updated 2026-10-11 12:00 UTC\n\n"
            "**Languages:** [Python](issues/by-language/python.md) (150) · "
            "[all languages](issues/README.md#by-language)\n\n"
            "**Topics:** [Command Line](issues/by-topic/cli.md) (80)\n"
            "<!-- RADAR:STATS:END -->"
        )
        self.assertEqual(updated, f"{header}{expected_block}{footer}")

    def test_language_list_capped_at_twelve(self):
        rows = [(f"Lang-{i:02d}", f"lang-{i:02d}", 100 - i, 0, 0) for i in range(15)]
        updated = self._run(
            "<!-- RADAR:STATS:START -->\n<!-- RADAR:STATS:END -->",
            language_rows=rows,
            topic_rows=[("Topic-One", "topic-one", 10, 0, 0)],
        )
        for i in range(12):
            self.assertIn(f"[Lang-{i:02d}](", updated)
        for i in range(12, 15):
            self.assertNotIn(f"[Lang-{i:02d}](", updated)
        self.assertIn("[all languages](issues/README.md#by-language)", updated)

    def test_topics_list_includes_all_entries(self):
        rows = [(f"Topic-{i:02d}", f"topic-{i:02d}", 50 - i, 0, 0) for i in range(15)]
        updated = self._run(
            "<!-- RADAR:STATS:START -->\n<!-- RADAR:STATS:END -->",
            topic_rows=rows,
        )
        for i in range(15):
            self.assertIn(f"[Topic-{i:02d}](", updated)

    def test_missing_readme_is_no_op(self):
        self.assertIsNone(self._run(readme_text=None))
        self.assertFalse(self.last_readme_exists)
        self.assertEqual(self.last_dir_contents, [])

    def test_readme_without_markers_is_left_unchanged(self):
        text = "# Open Source Radar\n\nNo stats block here.\n"
        self.assertEqual(self._run(text, language_rows=[("Python", "python", 1, 0, 0)]), text)

    def test_names_with_backslash_do_not_fail_regex_replacement(self):
        updated = self._run(
            "<!-- RADAR:STATS:START -->\n<!-- RADAR:STATS:END -->",
            language_rows=[(r"C\Sharp", "csharp", 10, 0, 0)],
            topic_rows=[(r"Back\slash", "backslash", 5, 0, 0)],
        )
        self.assertIn(r"[C\Sharp](issues/by-language/csharp.md) (10)", updated)
        self.assertIn(r"[Back\slash](issues/by-topic/backslash.md) (5)", updated)


class ContributingPathsInSyncTests(unittest.TestCase):
    def test_site_allowlist_matches_pipeline(self):
        app = (Path(__file__).resolve().parent.parent / "site" / "assets" / "app.js").read_text(encoding="utf-8")
        block = re.search(r"CONTRIBUTING_PATHS = new Set\(\[(.*?)\]\)", app, re.DOTALL)
        self.assertIsNotNone(block)
        self.assertEqual(re.findall(r'"([^"]+)"', block.group(1)), radar.CONTRIBUTING_FILES)


class HelperTests(unittest.TestCase):
    def test_iso_to_datetime_parses_z_suffix_as_utc(self):
        parsed = radar.iso_to_datetime("2026-10-12T08:30:00Z")
        self.assertEqual(parsed, dt.datetime(2026, 10, 12, 8, 30, tzinfo=dt.UTC))
        self.assertEqual(parsed.utcoffset(), dt.timedelta(0))

    def test_iso_to_datetime_keeps_non_utc_offset(self):
        parsed = radar.iso_to_datetime("2026-10-12T10:30:00+02:00")
        self.assertEqual(parsed.utcoffset(), dt.timedelta(hours=2))
        self.assertEqual(parsed.astimezone(dt.UTC), dt.datetime(2026, 10, 12, 8, 30, tzinfo=dt.UTC))

    def test_iso_to_datetime_accepts_explicit_zero_offset(self):
        parsed = radar.iso_to_datetime("2026-10-12T08:30:00+00:00")
        self.assertEqual(parsed, dt.datetime(2026, 10, 12, 8, 30, tzinfo=dt.UTC))
        self.assertEqual(parsed.utcoffset(), dt.timedelta(0))

    def test_gql_string_wraps_plain_value_in_quotes(self):
        self.assertEqual(radar.gql_string("owner"), '"owner"')
        self.assertEqual(radar.gql_string("good first issue"), '"good first issue"')

    def test_gql_string_escapes_quotes_backslashes_and_newlines(self):
        cases = [
            ('own"er', '"own\\"er"'),
            ("a\\b", '"a\\\\b"'),
            ("line1\nline2", '"line1\\nline2"'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(radar.gql_string(raw), expected)
                self.assertEqual(json.loads(expected), raw)

    def test_gql_string_escapes_non_ascii_as_unicode_escapes(self):
        # json.dumps defaults to ensure_ascii=True, so labels with emoji must survive the trip
        # as \uXXXX escapes rather than raw characters.
        literal = radar.gql_string("good first issue 🙌")
        self.assertEqual(literal, '"good first issue \\ud83d\\ude4c"')
        self.assertNotIn("🙌", literal)
        self.assertIn("\\u", literal)
        self.assertEqual(json.loads(literal), "good first issue 🙌")

    def test_build_repo_query_escapes_names_and_labels(self):
        # gql_string is only useful if the query builder actually uses it everywhere.
        query = radar.build_repo_query(['own/re"po'], ['say "hi"', "line\nbreak"], 5, False)
        self.assertIn('repository(owner: "own", name: "re\\"po")', query)
        self.assertIn('labels: ["say \\"hi\\"","line\\nbreak"]', query)
        self.assertNotIn("line\nbreak", query)


class LanguageQuickstartsInSyncTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def _quickstarts(self):
        return sorted(p.name for p in (self.ROOT / "languages").glob("*.md") if p.name != "README.md")

    def test_readme_states_the_number_of_quickstarts(self):
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"quickstarts for (\d+) languages", readme)
        self.assertIsNotNone(match, "README.md no longer says 'quickstarts for N languages'")
        self.assertEqual(int(match.group(1)), len(self._quickstarts()))

    def test_every_quickstart_is_linked_from_languages_readme(self):
        index = (self.ROOT / "languages" / "README.md").read_text(encoding="utf-8")
        linked = set(re.findall(r"\]\(([^)#/]+\.md)\)", index))
        self.assertEqual(sorted(set(self._quickstarts()) - linked), [])


if __name__ == "__main__":
    unittest.main()
