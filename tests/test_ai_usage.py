from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from devorchestra import ai_usage
from devorchestra.chatgpt_subscription import SubscriptionError
from devorchestra.lease import Blocked
from devorchestra.ledger import digest


class UsageTests(unittest.TestCase):
    def test_account_buckets_only_show_provider_fields(self):
        result = ai_usage.codex_snapshot({"rateLimitsByLimitId": {"codex": {
            "primary": {"usedPercent": 29, "windowDurationMins": 10080, "resetsAt": 1794384416},
            "secondary": {"usedPercent": None},
        }}, "accountId": "private"}, {"summary": {"lifetimeTokens": 452}})
        first, second = result["windows"]
        self.assertEqual((29, 71, 10080, 1794384416),
                         tuple(first[k] for k in ("used", "remaining", "minutes", "resets_at")))
        self.assertIsNone(second["remaining"])
        self.assertIsNone(second["resets_at"])
        self.assertNotIn("private", str(result))
        self.assertEqual(452, result["lifetime_tokens"])

    def test_confirmed_pause_is_per_registration_and_requires_explicit_resume(self):
        status = ai_usage.provider_status(SubscriptionError(429, ai_usage.LIMIT_CODE))
        event = {"kind": "native_manager_result", "usage_account": digest("a"), "provider_usage_status": status}
        ledger = Mock()
        ledger.read.return_value = [event]
        with self.assertRaisesRegex(Blocked, "paused"):
            ai_usage.check_account(ledger, "a")
        ai_usage.check_account(ledger, "b")
        ledger.read.return_value.append({"kind": "subscription_usage_resumed", "usage_account": digest("a")})
        ai_usage.check_account(ledger, "a")
        self.assertEqual("QUERY_FAILED", ai_usage.provider_status(SubscriptionError(429, "UNRECOGNIZED_ERROR")))

    def test_budget_is_distinct_and_unknown_when_inference_is_pending(self):
        binding = {"project_id": "p", "development_session_id": "session"}
        start = {**binding, "kind": "subscription_session_started", "scope": {"tokens": 30000, "account": "a"}}
        role = {**binding, "kind": "native_manager_result", "manager_backend": "chatgpt-subscription-text",
                "usage": 1300, "run_id": "plan", "usage_details": ai_usage.response_usage(
                    {"usage": {"input_tokens": 1000, "output_tokens": 300, "total_tokens": 1300}})}
        pending = {**binding, "kind": "subscription_developer_intent", "run_id": "developer"}
        stats = ai_usage.project_usage([start, role], "p")
        self.assertEqual((1300, 28700), (stats["known"], stats["remaining"]))
        self.assertEqual(1000, stats["roles"][0]["input"])
        self.assertIsNone(stats["roles"][0]["cached"])
        self.assertIsNone(ai_usage.project_usage([start, role, pending], "p")["remaining"])

    def test_connection_statuses_use_selected_registration(self):
        rows = [{"account": "signed", "signed_in": True, "subscription_granted": True},
                {"account": "denied", "signed_in": True, "subscription_granted": False},
                {"account": "expired", "signed_in": False, "subscription_granted": False}]
        store = Mock()
        store.accounts.return_value = rows
        with patch.object(ai_usage, "execution_slot", return_value=nullcontext(False)), \
                patch("devorchestra.chatgpt_subscription.AccountStore", return_value=store), \
                patch("devorchestra.chatgpt_subscription.list_models") as list_models:
            self.assertEqual("AUTH_REQUIRED", ai_usage.check_connection(SimpleNamespace(), "expired"))
            self.assertEqual("NO_PERMISSION", ai_usage.check_connection(SimpleNamespace(), "denied"))
            self.assertEqual("CONNECTED", ai_usage.check_connection(SimpleNamespace(), "signed"))
            store.access.assert_called_once_with("signed")
            list_models.side_effect = Blocked("ChatGPT subscription connection interrupted")
            self.assertEqual("QUERY_FAILED", ai_usage.check_connection(SimpleNamespace(), "signed"))
