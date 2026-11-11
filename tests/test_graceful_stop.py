import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from devorchestra import game_pilot, native_cycle, registered_cycle
from devorchestra.lease import Blocked


class GracefulStopTests(unittest.TestCase):
    def session(self, *, followup=False):
        session = native_cycle._FixtureSession.__new__(native_cycle._FixtureSession)
        session.scope = {
            "seconds": 1800, "tokens": 300000, "expires": 6000,
            "role_tokens": dict(game_pilot.ROLE_TOKENS),
            "role_deadlines": dict(game_pilot.ROLE_SECONDS),
            "role_seconds": 45,
            "graceful_stop": game_pilot.graceful_policy(followup=followup),
            "model_calls_max": 2, "model": "test",
            "instructions": {role: "test-hash" for role in game_pilot.ROLE_TOKENS},
        }
        session.started = 0
        session.usage = session.calls = session.signoff_tokens = 0
        session.stop_reason = session.signoff_started = None
        session.phases = []
        session.record = Mock()
        session.check = Mock()
        session.check_image = Mock()
        session.source = Mock(return_value="test-source")
        session.work = session.binary = Path("test")
        session.env = {}
        return session

    def test_inflight_role_completes_after_soft_time_and_token_limits(self):
        session = self.session()
        events = [
            {"type": "thread.started", "thread_id": "fixture"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ACCEPT"}},
            {"type": "turn.completed", "usage": {"input_tokens": 160000, "output_tokens": 1}},
        ]
        now = [700]
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder)
            session.logs = Mock(return_value=log)

            def observe(*args, seconds, check, **kwargs):
                self.assertEqual(seconds, 2100)
                output = "\n".join(map(json.dumps, events))
                (log / "probe.stdout").write_text(output, encoding="utf-8")
                now[0] = 1900
                check()
                return {"stdout": output, "exitCode": 0, "job_active": 0}

            with patch.object(native_cycle, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(native_cycle, "time", return_value=1000), \
                    patch.object(native_cycle, "public_instruction", return_value={"text": "", "sha256": "test-hash"}), \
                    patch.object(native_cycle, "_observe", side_effect=observe):
                response, _ = session.dispatch(1, "reviewer", None, "")
                self.assertEqual(response["message"], "ACCEPT")
                self.assertEqual(session.phases[-1]["outcome"], "COMPLETED")
                self.assertEqual(session.phases[-1]["role_token_overage"], 10001)
                self.assertEqual(session.stop_reason, "SESSION_TIME")
                self.assertEqual(session.verification_seconds(), 45)
                self.assertTrue(session.admit_graceful("manager-final", 120000))
                with self.assertRaisesRegex(Blocked, "GRACEFUL_STOP"):
                    session.admit_graceful("manager-planning")

    def test_followup_reserve_stays_within_approved_session(self):
        session = self.session(followup=True)
        session.usage = 310000
        with patch.object(native_cycle, "monotonic", return_value=100):
            with self.assertRaisesRegex(Blocked, "SIGNOFF_PENDING"):
                session.admit_graceful("manager-final", 120000)
        self.assertEqual(session.stop_reason, "SESSION_TOKENS")

    def test_idle_detection_distinguishes_a_running_command(self):
        session = self.session()
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder)
            (log / "probe.stdout").write_text(json.dumps({
                "type": "item.started", "item": {"id": "one", "type": "command_execution"}}) + "\n", encoding="utf-8")
            check = session.progress_check("developer", 0, log, 262144)
            with patch.object(native_cycle, "monotonic", return_value=0):
                check()
            with patch.object(native_cycle, "monotonic", return_value=600):
                check()
            with patch.object(native_cycle, "monotonic", return_value=1200):
                with self.assertRaisesRegex(Blocked, "MODEL_IDLE_TIMEOUT"):
                    check()

    def test_resume_after_verification_only_admits_final_manager(self):
        session = Mock()
        status = {"criterion": "VERIFIED"}
        comparison = {"criteria": status, "regressed": []}
        session.scope = {"files": ["game.gd"], "resume": {
            "cycle": 1, "criterion": "criterion", "criteria_status": status,
            "changed": ["game.gd"], "review": "ACCEPT", "comparison": comparison}}
        with patch.object(registered_cycle, "_decide", return_value="COMPLETED") as decide:
            self.assertEqual(registered_cycle._resume_remaining(session, "diff"), "COMPLETED")
        decide.assert_called_once_with(session, 1, "criterion", "ACCEPT", comparison, "COMPLETED")
        session.call.assert_not_called()
        session.verify_registered.assert_not_called()


if __name__ == "__main__":
    unittest.main()
