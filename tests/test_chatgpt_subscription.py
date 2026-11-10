from contextlib import contextmanager
from email.message import Message
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import Mock, patch

from devorchestra.chatgpt_subscription import AccountStore, PLAN_SCOPE, complete
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger


class SubscriptionTests(unittest.TestCase):
    def test_protected_account_roundtrip_and_separation(self):
        with TemporaryDirectory() as directory:
            ledger = Ledger.for_project(Path(directory))
            ledger.initialize()
            store = AccountStore(ledger)
            account = {"access_token": "fixture-token", "scopes": [PLAN_SCOPE]}
            state = {"host": "test-host", "accounts": {"first": account, "second": {}}}
            store.save(state)
            self.assertNotIn(b"fixture-token", store.path.read_bytes())
            self.assertEqual(state, store.read())
            self.assertEqual([True, False], [item["subscription_granted"] for item in store.accounts()])

    def test_stream_requires_completed_and_classifies_failures(self):
        body = {"model": "fixture", "store": False, "stream": True, "tools": [],
                "instructions": "Plan", "input": [{"role": "user", "content": "Fixture"}]}
        for media, payload, terminal in (
            ("text/event-stream", b'data: {"type":"response.completed","response":{"status":"completed"}}\r\n\r\n', None),
            ("text/plain", b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n', None),
            ("text/plain", b'Plain response\n', "not framed as SSE"),
            ("text/event-stream", b'data: {"type":"response.output_text.delta","delta":"fixture"}\n\n', "ended before"),
            ("text/event-stream", b'data: {"type":"response.incomplete","response":{}}\n\n', "response.incomplete"),
        ):
            with self.subTest(terminal=terminal):
                @contextmanager
                def request(*args, **kwargs):
                    response = BytesIO(payload)
                    response.headers = Message()
                    response.headers["Content-Type"] = media
                    yield response

                sent = Mock()
                with patch("devorchestra.chatgpt_subscription._request", request):
                    if terminal:
                        with self.assertRaisesRegex(Blocked, terminal):
                            complete("fixture-token", body, lambda: None, sent, monotonic() + 2)
                    else:
                        self.assertEqual("completed", complete(
                            "fixture-token", body, lambda: None, sent, monotonic() + 2,
                        )["status"])
                sent.assert_called_once_with()

    def test_refresh_rotates_protected_credentials_and_logout_clears_local_tokens(self):
        with TemporaryDirectory() as directory:
            ledger = Ledger.for_project(Path(directory))
            ledger.initialize()
            store = AccountStore(ledger)
            store.save({"host": "test-host", "accounts": {"first": {
                "client_id": "oaiapp_fixture", "subject": "fixture", "access_token": "old-token",
                "refresh_token": "old-refresh", "scopes": [PLAN_SCOPE], "expires": 0,
            }}})
            with patch("devorchestra.chatgpt_subscription._json", return_value={
                "access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600,
                "token_type": "Bearer",
            }):
                self.assertEqual("new-token", store.access("first"))
            self.assertEqual("new-refresh", store.read()["accounts"]["first"]["refresh_token"])
            with patch("devorchestra.chatgpt_subscription._json", side_effect=Blocked("offline")):
                self.assertEqual({"signed_out": True, "remote_revocation_confirmed": False}, store.logout("first"))
            self.assertEqual({"client_id": "oaiapp_fixture", "subject": "fixture"},
                             store.read()["accounts"]["first"])
