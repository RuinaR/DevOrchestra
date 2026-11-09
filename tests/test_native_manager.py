from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import time
import unittest
from unittest.mock import patch

from devorchestra.adapter import Budget, RoleRequest, Scope
from devorchestra.app_server import NativeInspectionApproval, _owned_stdio
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.manager_api import ENDPOINT, build_manager_api_draft, parse_manager_api_result
from devorchestra.native_manager import DISABLED_FEATURES, MANAGER_CONFIG, ManagerApproval, ManagerPolicyApproval
from devorchestra.role_contract import bind_instructions
from devorchestra.runtime import Runner


PEER = r"""
import json, os, sys, time
mode = sys.argv[1]
config = json.loads(sys.argv[2])
cwd = None
def send(value):
    print(json.dumps(value), flush=True)
def notice(method, **params):
    send({'method': method, 'params': {'threadId':'thr-fixture', **params}})
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    if mode == 'config-only':
        assert method in ('initialize', 'initialized', 'config/read', 'configRequirements/read')
    if method == 'initialize':
        if mode == 'config-only':
            assert 'capabilities' not in msg['params']
        else:
            assert msg['params']['capabilities'] == {'experimentalApi': True}
    if method == 'initialized':
        continue
    if method == 'account/read':
        assert msg['params'] == {'refreshToken':False}
    if method == 'thread/start':
        assert mode not in ('tools-enabled', 'api-account')
        assert msg['params']['sandbox'] == 'read-only'
        assert not msg['params'].get('dynamicTools')
        assert msg['params']['modelProvider'] == 'openai'
        assert msg['params']['config'] == {'model_reasoning_effort':'low'}
        cwd = msg['params']['cwd']
        notice('thread/started', thread={'id': 'thr-fixture'})
    if method == 'turn/start':
        p = msg['params']
        assert p['approvalPolicy'] == 'never' and p['effort'] == 'low'
        assert 'sandboxPolicy' not in p
        assert p['environments'] == []
        assert mode not in ('unsafe-thread', 'network-thread', 'wrong-instructions')
        assert p['input'][0]['text'] == 'Common fixture'
        assert p['input'][1]['text'] == 'Manager fixture'
        assert 'Global fixture' not in str(p['input']) and 'Project fixture' not in str(p['input'])
        assert not p['outputSchema']['additionalProperties']
        if mode == 'hang':
            time.sleep(4)
        if mode == 'error':
            send({'id':msg['id'], 'error':{'message':'fixture private error'}})
            continue
        notice('turn/started', turn={'id':'turn-fixture','status':'inProgress'})
    result = {
        'initialize': {'platformOs':'windows'},
        'config/read': {'config': config},
        'configRequirements/read': {'requirements':None},
        'account/read': {'account': {'type':'apiKey' if mode == 'api-account' else 'chatgpt'}},
        'thread/start': {'thread': {'id':'thr-fixture', 'ephemeral':True}, 'approvalPolicy':'never',
                         'approvalsReviewer':'user', 'modelProvider':'openai', 'reasoningEffort':'low',
                         'instructionSources': [] if mode == 'wrong-instructions' or not cwd else
                             [os.path.join(os.environ['CODEX_HOME'], 'AGENTS.md'), os.path.join(cwd, 'AGENTS.md')],
                         'sandbox': {'type':'workspaceWrite' if mode == 'unsafe-thread' else 'readOnly',
                                     'networkAccess':mode == 'network-thread'},
                         'cwd':cwd, 'model':'fixture-model'},
        'turn/start': {'turn': {'id':'turn-fixture','status':'inProgress'}},
    }[method]
    send({'id':msg['id'], 'result':result})
    if method == 'turn/start':
        plan = {'disposition':'PROPOSED','priority_reason':'Fixture','task_spec':'Inspect fixture',
                'proposed_paths':['fixture.txt'],'acceptance_checks':['Check fixture'],'stop_conditions':[]}
        if mode == 'invalid':
            plan['scope'] = 'unapproved'
        if mode == 'clarification':
            plan.update(disposition='CLARIFICATION', task_spec='Which fixture behavior?',
                        proposed_paths=[], acceptance_checks=[])
        notice('item/completed', turnId='turn-fixture',
               item={'type':'agentMessage','id':'item-fixture','text':json.dumps(plan)})
        notice('turn/completed', turn={'id':'turn-fixture','status':'completed'})
"""


class NativeManagerTests(unittest.TestCase):
    @staticmethod
    def effective_config():
        return {"sandbox_mode": "read-only", "approval_policy": "never", "web_search": "disabled",
                "cli_auth_credentials_store": "file",
                "forced_login_method": "chatgpt",
                "analytics": {"enabled": False}, "mcp_servers": {},
                "features": {name: False for name in DISABLED_FEATURES}}

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.ledger = Ledger.for_project(root)
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.replica = root / "replica"
        self.replica.mkdir()
        home = self.ledger.local_directory("temp") / "fixture-home"
        home.mkdir()
        (home / "config.toml").write_bytes(MANAGER_CONFIG.encode())
        instructions = self.ledger.local_directory("instructions")
        files = [("global", home / "AGENTS.md", b"Global fixture"),
                 ("common", instructions / "common/orchestration-rules.md", b"Common fixture"),
                 ("project", self.replica / "AGENTS.md", b"Project fixture"),
                 ("role", instructions / "roles/manager-planning.md", b"Manager fixture")]
        self.layers = tuple({"layer": layer, "version": "fixture", "sha256": sha256(data).hexdigest()}
                            for layer, path, data in files)
        self.role_file = files[-1][1]
        for _, path, data in files:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        now = time()
        self.request = RoleRequest("fixture-a", "task", "run", 1, "agent", "Manager",
                                   "fixture-model", "low", "context",
                                   Scope("replica", ("a" * 32,), readable=(str(self.replica),)),
                                   Budget(10, 8192, 0), (), "Fixture goal", now + 30)
        self.approval = ManagerApproval("a" * 32, now + 40, root / "codex.exe", self.replica, home,
                                        self.layers, bind_instructions(self.request, self.layers), 8192, 1024)

    def run_fixture(self, mode="normal", *, cancel=False):
        @contextmanager
        def open_fixture(_binary, cwd, _env, timeout, cancelled, *, explicit_mxc):
            self.assertTrue(explicit_mxc)
            self.assertEqual("native_manager_intent", self.ledger.read()[-1]["kind"])
            if cancel:
                self.runner.shutdown("request")
            config = self.effective_config()
            if mode == "tools-enabled":
                config["features"]["computer_use"] = True
            with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER, mode, json.dumps(config)], cwd,
                              {"SystemRoot": os.environ["SystemRoot"], "CODEX_HOME": str(self.approval.home)},
                              min(timeout, 0.4) if mode == "hang" else timeout, cancelled) as server:
                yield server

        catalog = {"setups": {"fixture-a": {"replica": str(self.replica)}}}
        with patch("devorchestra.native_manager.require_live_manager_admission"), \
                patch("devorchestra.runtime.Catalog.read", return_value=catalog), \
                patch("devorchestra.native_manager.open_app_server", side_effect=open_fixture):
            return self.runner.plan_native_manager(self.approval, self.request)

    def test_live_path_is_blocked_before_any_intent(self):
        with self.assertRaisesRegex(
            Blocked, "GENERAL_ADMISSION_UNVERIFIED: MANAGER_PRE_EXECUTION_BOUNDARY_UNVERIFIED",
        ):
            self.runner.plan_native_manager(self.approval, self.request)
        self.assertEqual([], self.ledger.read())

    def test_responses_draft_is_tool_free_and_rejects_unexpected_response_items(self):
        request = replace(self.request, budget=replace(self.request.budget, cost_microunits=1))
        with self.assertRaisesRegex(Blocked, "RESPONSES_DISABLED_SUBSCRIPTION_ONLY"):
            self.runner.prepare_tool_free_manager(self.approval, request)
        draft = build_manager_api_draft(
            request, ["Global fixture", "Common fixture", "Project fixture", "Manager fixture"],
            "Synthetic goal", context_limit=8192, output_limit=1024,
        )
        body = json.loads(draft.body)
        self.assertEqual(ENDPOINT, draft.endpoint)
        self.assertEqual(sha256(draft.body).hexdigest(), draft.sha256)
        self.assertEqual(([], "none", False, 1024),
                         (body["tools"], body["tool_choice"], body["store"], body["max_output_tokens"]))
        self.assertEqual(["Global fixture", "Common fixture", "Project fixture", "Manager fixture"],
                         [item["content"] for item in body["input"][:-1]])
        self.assertNotIn("Global fixture", repr(draft))
        self.assertEqual([], self.ledger.read())
        plan = {"disposition": "CLARIFICATION", "priority_reason": "Fixture",
                "task_spec": "Which result?", "proposed_paths": [], "acceptance_checks": [],
                "stop_conditions": []}
        response = {"object": "response", "status": "completed", "model": request.model,
                    "tools": [], "tool_choice": "none",
                    "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130},
                    "output": [{"type": "message", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": json.dumps(plan)}]}]}

        def parse(value):
            return parse_manager_api_result(json.dumps(value).encode(), model=request.model,
                                            output_limit=1024, token_limit=request.budget.tokens)

        self.assertEqual((plan, {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                                "cost_microunits": "UNKNOWN"}), parse(response))
        for invalid in ({"tool_choice": "auto"}, {"usage": None}, {"status": "incomplete"},
                        {"output": [*response["output"], {"type": "function_call"}]}):
            with self.subTest(invalid=invalid), self.assertRaises(Blocked):
                parse({**response, **invalid})

    def test_config_only_inspection_drains_without_opening_thread_or_turn(self):
        identity = "c" * 32
        lab = self.ledger.local_directory("temp") / f"app-server-{identity}"
        for name in ("home", "work", "tmp"):
            (lab / name).mkdir(parents=True)
        for name in ("home", "work"):
            (lab / name / "AGENTS.md").write_bytes(b"Synthetic Manager inspection")
        (lab / "home" / "config.toml").write_bytes(MANAGER_CONFIG.encode("ascii"))
        approval = ManagerPolicyApproval(NativeInspectionApproval(identity, time() + 30, self.approval.binary, lab))

        @contextmanager
        def transport(_binary, cwd, _env, timeout, cancelled, *, explicit_mxc):
            self.assertTrue(explicit_mxc)
            with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER, "config-only",
                              json.dumps(self.effective_config())], cwd,
                              {"SystemRoot": os.environ["SystemRoot"]}, timeout, cancelled) as server:
                yield server

        with patch("devorchestra.app_server.open_app_server", side_effect=transport) as launch:
            result = self.runner.inspect_native(approval)
            self.assertEqual("MATCHED", result["config_check"])
            self.assertEqual("NOT_VERIFIED", result["thread_policy"])
            self.assertEqual("NOT_VERIFIED", result["tool_exposure"])
            self.assertEqual("DRAINED", result["owned_tree"])
            self.assertIs(result["model_dispatched"], False)
            with self.assertRaisesRegex(Blocked, "DUPLICATE_REQUEST"):
                self.runner.inspect_native(approval)
            launch.assert_called_once()

    def test_input_limit_and_output_reserve_block_before_intent(self):
        for changes in ({"context_limit_tokens": 1024}, {"output_reserve_tokens": 100}):
            with self.subTest(changes=changes):
                original = self.approval
                self.approval = replace(original, **changes)
                with self.assertRaisesRegex(Blocked, "output reserve"):
                    self.run_fixture()
                self.approval = original
        self.assertEqual([], self.ledger.read())

    def test_effective_tool_or_thread_policy_mismatch_blocks_turn(self):
        for mode in ("tools-enabled", "api-account", "unsafe-thread", "network-thread", "wrong-instructions"):
            with self.subTest(mode=mode):
                self.ledger = Ledger(self.ledger.path.with_name(f"{mode}.sqlite3"),
                                     local_root=self.ledger.local_root)
                self.runner = Runner(self.ledger)
                self.ledger.initialize()
                with self.assertRaisesRegex(Blocked, "UNKNOWN"):
                    self.run_fixture(mode)
                expected = ["native_manager_intent"]
                if mode not in ("tools-enabled", "api-account"):
                    expected.append("native_manager_thread_dispatch")
                self.assertEqual(expected, [event["kind"] for event in self.ledger.read()])

    def test_clarification_is_durable_after_manager_drain_and_requires_fresh_approval(self):
        result, _ = self.run_fixture("clarification")
        self.assertEqual("DRAINED", result["owned_tree"])
        self.assertEqual("CLARIFICATION", result["question"]["type"])
        runner = Runner(self.ledger)
        with self.assertRaisesRegex(Blocked, "question is pending"):
            runner.command("start")
        event = runner.respond_question(result["question"]["id"], result["run_id"], result["generation"],
                                        "ANSWER", "Use the fixture default")
        self.assertEqual("FRESH_MANAGER_APPROVAL_REQUIRED", event["continuation"])
        self.assertEqual(1, sum(e["kind"] == "native_manager_dispatch" for e in self.ledger.read()))

    def test_manager_documents_must_match_the_approved_request_hash_binding(self):
        from devorchestra.native_manager import prepare_manager

        context = {"project_id": self.request.project_id, "goal": self.request.goal,
                   "requirements": [{"ref": {"id": "synthetic"}, "text": "Owner-selected fixture"}]}
        with self.assertRaisesRegex(Blocked, "context is not bound"):
            prepare_manager(self.ledger, self.approval, self.request, project_context=context)
        request = replace(self.request, verified_input_refs=(digest(context),))
        approval = replace(self.approval, request_hash=bind_instructions(request, self.layers))
        messages, _ = prepare_manager(self.ledger, approval, request, project_context=context)
        self.assertIn("Owner-selected fixture", messages[-1]["text"])
        self.assertEqual([], self.ledger.read())

    def test_valid_turn_drains_commits_and_does_not_admit_developer(self):
        result, proposal = self.run_fixture()
        self.assertEqual("PROPOSED", proposal["disposition"])
        self.assertEqual("DRAINED", result["owned_tree"])
        self.assertEqual("BLOCKED", result["next_role_admission"])
        self.assertEqual("UNKNOWN", result["usage"])
        self.assertEqual(["native_manager_intent", "native_manager_thread_dispatch",
                          "native_manager_dispatch", "native_manager_result"],
                         [event["kind"] for event in self.ledger.read()])
        self.assertNotIn("Fixture goal", str(self.ledger.read()))
        with self.assertRaisesRegex(Blocked, "unused admission"):
            self.run_fixture()

    def test_changed_prompt_or_expanded_scope_refuses_before_intent(self):
        original = self.request
        self.request = replace(original, scope=replace(original.scope, tools=("shell",)))
        with self.assertRaisesRegex(Blocked, "scope"):
            self.run_fixture()
        self.request = original
        self.role_file.write_text("Unexpected prompt")
        with self.assertRaisesRegex(Blocked, "hash changed"):
            self.run_fixture()
        self.assertEqual([], self.ledger.read())

    def test_invalid_result_remains_unknown_and_blocks_restart(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("invalid")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])
        self.runner = Runner(Ledger.for_project(self.ledger.state_boundary.parent))
        with self.assertRaisesRegex(Blocked, "ownership requires recovery"):
            self.run_fixture()

    def test_rpc_error_remains_unknown_without_raw_error(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("error")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])
        self.assertNotIn("fixture private error", str(self.ledger.read()))

    def test_timeout_remains_unknown(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("hang")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])

    def test_result_commit_failure_prevents_return_and_replay(self):
        real_update = self.ledger.update
        calls = 0

        def fail_result(choose):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("fixture settlement failure")
            return real_update(choose)

        with patch.object(self.ledger, "update", side_effect=fail_result):
            with self.assertRaisesRegex(Blocked, "result commit unverified"):
                self.run_fixture()
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])
        with self.assertRaisesRegex(Blocked, "ownership requires recovery"):
            self.run_fixture()

    def test_shutdown_before_turn_prevents_dispatch(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture(cancel=True)
        self.assertNotIn("native_manager_dispatch", [event["kind"] for event in self.ledger.read()])
