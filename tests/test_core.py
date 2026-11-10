"""Unit tests for the parts that need no running Hermes. Run with:

    python -m unittest discover -s tests      (from the repo root, with the Python that runs Hermes)
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktop_inbox import core, settings, webhook  # noqa: E402


def with_settings(**values):
    """Patch the user settings the plugin reads from config.yaml."""
    return mock.patch.object(settings, "_user_settings", return_value=values)


class SettingsTests(unittest.TestCase):
    def test_defaults_come_from_the_manifest(self):
        with with_settings():
            s = settings.load()
        self.assertEqual(s["done_days"], 7)
        self.assertEqual(s["empty_title"], "Inbox zero")
        self.assertNotIn("webhook_token", s)  # secrets never come from config

    def test_user_name_falls_back(self):
        with with_settings(user_name=""):
            self.assertEqual(settings.user_name(), "the user")
        with with_settings(user_name="Ada"):
            self.assertEqual(settings.user_name(), "Ada")

    def test_types_extend_and_override(self):
        extra = {"pay": {"emoji": "💳", "label": "Pay", "icon": "credit-card", "how": "{user} pays it."},
                 "read": {"icon": "eye"}, "bad": "not a dict"}
        with with_settings(types=extra):
            types = settings.types()
        self.assertEqual(list(types)[:4], ["read", "answer", "decide", "do"])
        self.assertEqual(types["pay"]["label"], "Pay")
        self.assertEqual(types["read"]["icon"], "eye")
        self.assertEqual(types["read"]["emoji"], "📖")  # untouched fields stay
        self.assertNotIn("bad", types)

    def test_type_without_fields_gets_a_default(self):
        with with_settings(types={"call": {}}):
            spec = settings.types()["call"]
        self.assertEqual(spec["label"], "Call")
        self.assertTrue(spec["emoji"])

    def test_watched_jobs_empty_means_all(self):
        with with_settings(watched_jobs=[]):
            self.assertIsNone(settings.watched_jobs())
        with with_settings(watched_jobs=["a", " ", "b"]):
            self.assertEqual(settings.watched_jobs(), ["a", "b"])


class ParseWhenTests(unittest.TestCase):
    NOW = datetime(2026, 11, 10, 15, 30)  # a Tuesday

    def at(self, s):
        with with_settings():
            return datetime.fromtimestamp(core.parse_when(s, self.NOW))

    def test_relative(self):
        self.assertEqual(self.at("30m"), datetime(2026, 11, 10, 16, 0))
        self.assertEqual(self.at("2h"), datetime(2026, 11, 10, 17, 30))
        self.assertEqual(self.at("1d"), datetime(2026, 11, 11, 15, 30))

    def test_named(self):
        self.assertEqual(self.at("tomorrow"), datetime(2026, 11, 11, 9, 0))
        self.assertEqual(self.at("evening"), datetime(2026, 11, 10, 20, 0))
        self.assertEqual(self.at("next-week"), datetime(2026, 11, 16, 9, 0))

    def test_weekday_is_strictly_next(self):
        self.assertEqual(self.at("fri"), datetime(2026, 11, 13, 9, 0))
        self.assertEqual(self.at("tue"), datetime(2026, 11, 17, 9, 0))
        self.assertEqual(self.at("Monday"), datetime(2026, 11, 16, 9, 0))

    def test_hours_follow_settings(self):
        with with_settings(morning_hour=7):
            got = datetime.fromtimestamp(core.parse_when("tomorrow", self.NOW))
        self.assertEqual(got, datetime(2026, 11, 11, 7, 0))

    def test_iso_and_errors(self):
        self.assertEqual(self.at("2026-12-01T08:15"), datetime(2026, 12, 1, 8, 15))
        with self.assertRaises(SystemExit):
            self.at("someday")
        with self.assertRaises(SystemExit):
            self.at("mo")  # too short to be a weekday


class BriefTests(unittest.TestCase):
    def test_source_and_ask(self):
        text = "_📥 From `morning-brief` · 📖 Read_\n\nBody here.\n\n---\n**Read the brief, then say \"read\".**"
        b = core.parse_brief(text)
        self.assertEqual(b["source"], "morning-brief")
        self.assertEqual(b["ask"], "Read the brief, then say \"read\".")

    def test_closing_sentence_is_trimmed(self):
        text = "x\n---\n**Three PRs wait for your review. Say \"done\" when you have looked.**"
        self.assertEqual(core.parse_brief(text)["ask"], "Three PRs wait for your review.")

    def test_no_ask(self):
        self.assertEqual(core.parse_brief("plain text"), {"source": "", "ask": ""})

    def test_parse_type_uses_custom_types(self):
        with with_settings(types={"pay": {"emoji": "💳"}}):
            self.assertEqual(core.parse_type("💳 Rent · Nov 1"), "pay")
            self.assertEqual(core.parse_type("✅ Taxes · Nov 1"), "do")
            self.assertEqual(core.parse_type("Untitled"), "read")


class SeedTests(unittest.TestCase):
    def test_seed_uses_name_style_and_notes(self):
        with with_settings(user_name="Ada", reply_style="short, plain words"):
            seed = core.seed_text("do", "✅ Taxes · Nov 10", "tax-job", "File the form.", "- keep it brief\nask first")
        self.assertIn("Ada's Hermes Inbox", seed)
        self.assertIn("Ada does the task", seed)
        self.assertIn("- Style: short, plain words", seed)
        self.assertIn("\n- keep it brief", seed)
        self.assertIn("\n- ask first", seed)
        self.assertIn('hermes desktop-inbox done "✅ Taxes · Nov 10"', seed)

    def test_seed_without_style(self):
        with with_settings():
            seed = core.seed_text("read", "t", "s", "b", "")
        self.assertNotIn("Style:", seed)
        self.assertIn("the user's Hermes Inbox", seed)


class FailedJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "cron").mkdir()
        jobs = [
            {"id": "a1", "name": "brief", "last_status": "error", "last_error": "boom", "last_run_at": 1},
            {"id": "b2", "name": "digest", "last_status": "delivery_queued"},
            {"id": "c3", "name": "off", "enabled": False, "last_status": "error"},
            {"id": "d4", "name": "silent", "last_status": "ok"},
        ]
        (self.home / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))
        out = self.home / "cron" / "output" / "d4"
        out.mkdir(parents=True)
        (out / "2026-11-10.md").write_text("# run\n## Response\nINBOX FAILED: hermes not found")
        self.patch = mock.patch.object(settings, "hermes_home", return_value=self.home)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_all_enabled_jobs_by_default(self):
        with with_settings():
            got = {j["name"]: j["status"] for j in core.failed_jobs()}
        self.assertEqual(got, {"brief": "error", "silent": "inbox_failed"})

    def test_watched_list_filters(self):
        with with_settings(watched_jobs=["silent"]):
            got = [j["name"] for j in core.failed_jobs()]
        self.assertEqual(got, ["silent"])


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(settings, "inbox_dir", return_value=Path(self.tmp.name)),
            mock.patch.object(core, "list_items", return_value=[]),
            mock.patch.object(core, "create_item", side_effect=lambda kind, name, body, source="", reply_notes="":
                              {"id": "s1", "title": f"{kind}:{name}", "notes": reply_notes}),
        ]
        for p in self.patches:
            p.start()
        webhook._login_cache = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def review_event(self, reviewer="ada"):
        return json.dumps({
            "action": "review_requested", "requested_reviewer": {"login": reviewer},
            "repository": {"full_name": "acme/app"}, "sender": {"login": "bob"},
            "pull_request": {"number": 7, "title": "Fix it", "html_url": "https://x/7",
                             "base": {"ref": "main"}, "head": {"ref": "fix"}},
        }).encode()

    def test_review_request_files_a_do_item(self):
        with with_settings(github_login="ada", user_name="Ada"), mock.patch.object(settings, "secret", return_value=""):
            code, data = webhook.handle_github(self.review_event(), "", "pull_request")
        self.assertEqual(code, 200)
        self.assertEqual(data["title"], "do:Review acme/app #7")
        core.create_item.assert_called_once()
        self.assertIn("Ada was asked to review", core.create_item.call_args.kwargs["reply_notes"])

    def test_review_for_someone_else_is_ignored(self):
        with with_settings(github_login="ada"), mock.patch.object(settings, "secret", return_value=""):
            code, data = webhook.handle_github(self.review_event("carol"), "", "pull_request")
        self.assertEqual(code, 200)
        self.assertIn("ignored", data)
        core.create_item.assert_not_called()

    def test_signature_is_checked(self):
        body = self.review_event()
        good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        with with_settings(github_login="ada"), mock.patch.object(settings, "secret", return_value="s3cret"):
            self.assertEqual(webhook.handle_github(body, "sha256=bad", "pull_request")[0], 403)
            self.assertEqual(webhook.handle_github(body, good, "pull_request")[0], 200)

    def test_ping(self):
        with with_settings(), mock.patch.object(settings, "secret", return_value=""):
            self.assertEqual(webhook.handle_github(b'{"zen": "hi"}', "", "ping"), (200, {"ok": True, "ignored": "ping"}))

    def test_generic_item_token_type_and_dedup(self):
        with with_settings(), mock.patch.object(settings, "secret", return_value="tok"):
            self.assertEqual(webhook.handle_item({"title": "x"}, b"hello", "nope")[0], 403)
            self.assertEqual(webhook.handle_item({"title": "x", "type": "zzz"}, b"hello", "tok")[0], 400)
            self.assertEqual(webhook.handle_item({}, b"hello", "tok")[0], 400)
            self.assertEqual(webhook.handle_item({"title": "x"}, b"   ", "tok")[0], 400)
            code, first = webhook.handle_item({"title": "x"}, b"hello", "tok")
            self.assertEqual((code, first["title"]), (200, "do:x"))
            self.assertEqual(webhook.handle_item({"title": "x"}, b"hello", "tok")[1], {"ok": True, "duplicate": True})

    def test_disabled_webhook_does_not_start(self):
        with with_settings(webhook_enabled=False):
            self.assertEqual(webhook.status(), {"ok": True, "enabled": False, "running": False})


if __name__ == "__main__":
    unittest.main()
