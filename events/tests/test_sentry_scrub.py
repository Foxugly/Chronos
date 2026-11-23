"""Tests of the self-contained Sentry scrubber (replacement of sentry-sdk 2's EventScrubber)."""

import json
from unittest import TestCase

from config.sentry_scrub import FILTERED, scrub_event, scrub_span, scrub_text, scrub_url, scrub_value

UUID = "3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b"


def leaked(data, *secrets):
    text = json.dumps(data, default=str)
    return [s for s in secrets if s in text]


class ScrubEventTests(TestCase):
    def test_request_extra_user_breadcrumbs_and_local_vars(self):
        event = scrub_event({
            "request": {"data": {"password": "hunter2", "title": "ok"},
                        "headers": {"Authorization": "Bearer xyz", "Accept": "*/*"},
                        "cookies": {"sessionid": "s3ss"},
                        "url": "https://bob:pa55@h/x/?token=abc123"},
            "extra": {"token": "abc123", "page": 3, "nested": {"api_key": "k3y"}},
            "user": {"id": "7", "ip_address": "203.0.113.9"},
            "breadcrumbs": {"values": [{"message": "GET /x?token=abc123",
                                        "data": {"csrftoken": "c5rf", "n": 1}}]},
            "exception": {"values": [{"stacktrace": {"frames": [
                {"vars": {"password": "hunter2", "secret": "s3cr", "i": 2}}]}}]},
            "threads": {"values": [{"stacktrace": {"frames": [{"vars": {"token": "abc123"}}]}}]},
        })
        self.assertEqual(leaked(event, "hunter2", "Bearer xyz", "s3ss", "pa55", "abc123",
                                "k3y", "203.0.113.9", "c5rf", "s3cr"), [])
        self.assertEqual(event["request"]["data"]["title"], "ok")
        self.assertEqual(event["request"]["headers"]["Accept"], "*/*")
        self.assertEqual(event["extra"]["page"], 3)
        self.assertEqual(event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["i"], 2)

    def test_contexts_are_scrubbed(self):
        event = scrub_event({"contexts": {
            "response": {"status_code": 200, "headers": {
                "Set-Cookie": "sessionid=s3ss", "Authorization": "Bearer xyz",
                "Content-Type": "text/html"}},
            "trace": {"trace_id": "t", "data": {"token": "abc123",
                                                "url": "https://h/reset/MQ/c4v2xx-0123456789abcdef0123/"}},
        }})
        self.assertEqual(leaked(event, "s3ss", "Bearer xyz", "abc123", "c4v2xx"), [])
        self.assertEqual(event["contexts"]["response"]["headers"]["Content-Type"], "text/html")
        self.assertEqual(event["contexts"]["response"]["status_code"], 200)

    def test_event_spans_are_scrubbed(self):
        event = scrub_event({"spans": [{
            "name": "GET /x?token=abc123", "description": "POST https://bob:pa55@h/",
            "data": {"password": "hunter2", "db.system": "postgresql",
                     "http.request.header.authorization": ["Bearer xyz"]},
            "attributes": {"user.email": "a@b.c"}}]})
        self.assertEqual(leaked(event, "abc123", "pa55", "hunter2", "Bearer xyz", "a@b.c"), [])
        self.assertEqual(event["spans"][0]["data"]["db.system"], "postgresql")


class ScrubTextTests(TestCase):
    def test_name_value_pairs_and_bearer(self):
        self.assertEqual(scrub_text("GET /x?token=abc123&page=2"), f"GET /x?token={FILTERED}&page=2")
        self.assertNotIn("xyz", scrub_text("Authorization: Bearer xyz"))

    def test_url_userinfo_is_dropped(self):
        self.assertEqual(scrub_text("postgres://bob:pa55@db:5432/app"), "postgres://db:5432/app")
        self.assertEqual(scrub_text("see https://tok3n@h/x/"), "see https://h/x/")

    def test_secret_routes_are_masked(self):
        for url in ("https://h/accounts/password/reset/key/1-abc/",
                    "https://h/accounts/confirm-email/Mzo1:abc/",
                    "https://h/invitation/abc/", "https://h/magic-link/abc/",
                    "https://h/activate/abc/", "https://h/partage/abc/events/"):
            with self.subTest(url=url):
                self.assertNotIn("abc", scrub_text(url))
                self.assertIn(FILTERED, scrub_text(url))
        self.assertEqual(scrub_text("https://h/accounts/password/reset/key/done/"),
                         "https://h/accounts/password/reset/key/done/")

    def test_long_opaque_segment_is_masked_but_uuid_kept(self):
        self.assertEqual(scrub_text("/x/c4v2xx-0123456789abcdef0123/y/"), f"/x/{FILTERED}/y/")
        self.assertEqual(scrub_text(f"/parties/{UUID}/"), f"/parties/{UUID}/")
        self.assertEqual(scrub_text("/static/css/site.css"), "/static/css/site.css")


class ScrubSpanTests(TestCase):
    def test_name_and_attributes(self):
        span = scrub_span({"name": "GET https://bob:pa55@h/invitation/abc/?token=t0k", "attributes": {
            "http.request.header.cookie": ["sessionid=s3ss"], "client.address": "203.0.113.9",
            "url.full": "https://h/x/?api_key=k3y", "sentry.op": "http.client", "n": 2}})
        self.assertEqual(leaked(span, "pa55", "abc", "t0k", "s3ss", "203.0.113.9", "k3y"), [])
        self.assertEqual(span["attributes"]["sentry.op"], "http.client")
        self.assertEqual(span["attributes"]["n"], 2)

    def test_never_returns_the_raw_span(self):
        class Trap(dict):
            def items(self):
                raise RuntimeError("boom")

        self.assertEqual(scrub_span({"name": "x", "attributes": Trap(password="hunter2")}),
                         {"name": FILTERED, "attributes": {}})


class SecondReviewTests(TestCase):
    """Second security review: exception messages, query/fragment, userinfo, fail-closed."""

    def test_exception_and_thread_messages_are_scrubbed(self):
        event = scrub_event({
            "exception": {"values": [{"type": "HTTPError",
                                      "value": "401 for https://bob:pa55@api/x?api_key=k3y"}]},
            "threads": {"values": [{"value": "GET /reset/MQ/c4v2xx-0123456789abcdef0123/"}]},
            "logentry": {"message": "%s", "params": ["token=abc123"], "formatted": "token=abc123"},
        })
        self.assertEqual(leaked(event, "pa55", "k3y", "c4v2xx", "abc123"), [])
        self.assertIn("HTTPError", json.dumps(event))

    def test_query_values_sensitive_or_opaque_and_fragment(self):
        out = scrub_text("https://h/cb?code=0aB1&state=ok&next=Zx9QwErTyUiOpAsDf&page=2#access_token=t0k")
        self.assertEqual(leaked(out, "0aB1", "Zx9QwErTyUiOpAsDf", "t0k", "access_token"), [])
        self.assertIn("state=ok", out)
        self.assertIn("page=2", out)
        self.assertNotIn("#", out)
        # Mixed case is enough: no digit required.
        self.assertIn(FILTERED, scrub_text("/x?ref=AbCdEfGhIjKlMnOpQr"))
        self.assertEqual(scrub_text(f"/x?id={UUID}"), f"/x?id={UUID}")

    def test_userinfo_is_stripped_robustly(self):
        self.assertEqual(scrub_url("https://a:b@c@host:8443/p"), "https://host:8443/p")
        self.assertEqual(scrub_url("https://tok3n@host"), "https://host")
        self.assertEqual(scrub_url("https://user:s3cr3t/p"), f"https://{FILTERED}/p")
        self.assertEqual(scrub_url("https://host:8443/p"), "https://host:8443/p")
        self.assertEqual(scrub_url("http://[::1]:8000/p"), "http://[::1]:8000/p")

    def test_fail_closed_tuples_sets_unknown_types_and_depth(self):
        event = scrub_event({"extra": {
            "t": ("token=abc123", {"password": "hunter2"}),
            "s": {"Bearer xyz"},
            "obj": object(),
            "n": 3,
        }})
        self.assertEqual(leaked(event, "abc123", "hunter2", "xyz"), [])
        self.assertEqual(event["extra"]["obj"], FILTERED)
        self.assertEqual(event["extra"]["n"], 3)
        deep = current = {}
        for _ in range(50):
            current["d"] = {}
            current = current["d"]
        current["leaf"] = "raw-secret"
        out = scrub_value(deep)
        self.assertEqual(leaked(out, "raw-secret"), [])
        self.assertIn(FILTERED, json.dumps(out))
