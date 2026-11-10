from contextlib import contextmanager, nullcontext
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
from devorchestra.catalog import Catalog, new_project
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.manager_api import ENDPOINT, build_manager_api_draft, parse_manager_api_result
from devorchestra.native_manager import (
    DISABLED_FEATURES, MANAGER_CONFIG, ManagerApproval, ManagerPolicyApproval,
    inspect_manager_thread_only, manager_admission, manager_request_hash,
)
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
    if mode == 'thread-only':
        assert method in ('initialize', 'initialized', 'config/read', 'configRequirements/read', 'thread/start')
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
        assert mode not in ('tools-enabled', 'api-account', 'async-feature-enabled', 'managed-conflict')
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
        'configRequirements/read': {'requirements': {
            'managed-compatible': {'allowedLoginMethods':['chatgpt'], 'network':None},
            'managed-conflict': {'allowedLoginMethods':['api']},
        }.get(mode)},
        'account/read': {'account': {'type':'apiKey' if mode == 'api-account' else 'chatgpt'},
                         'requiresOpenaiAuth': mode != 'wrong-auth-provider'},
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
                'work_kind':'PRODUCT','product_outcome':'Ready fixture',
                'validation_reason':'Check expected fixture','approach':'Update fixture',
                'proposed_paths':['fixture.txt'],'acceptance_checks':['Check fixture'],'stop_conditions':[]}
        if mode == 'invalid':
            plan['scope'] = 'unapproved'
        if mode == 'clarification':
            plan.update(disposition='CLARIFICATION', task_spec='Which fixture behavior?',
                        proposed_paths=[], acceptance_checks=[], work_kind='NONE')
        item = {'type':'agentMessage','id':'item-fixture','text':json.dumps(plan)}
        if mode == 'async-plan':
            item['delivery'] = 'async'
        if mode == 'tool-call':
            send({'id':99, 'method':'item/tool/call', 'params':{'tool':'shell', 'arguments':{}}})
        if mode == 'duplicate-item':
            notice('item/started', turnId='turn-fixture', item=item)
            notice('item/started', turnId='turn-fixture', item=item)
        notice('item/completed', turnId='turn-fixture',
               item=item)
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

    def run_fixture(self, mode="normal", *, cancel=False, registered=False):
        @contextmanager
        def open_fixture(_binary, cwd, _env, timeout, cancelled, *, explicit_mxc, **binary_options):
            self.assertTrue(explicit_mxc)
            self.assertEqual(self.approval.binary_options(), binary_options)
            self.assertEqual("native_manager_intent", self.ledger.read()[-1]["kind"])
            if cancel:
                self.runner.shutdown("request")
            config = self.effective_config()
            if mode == "tools-enabled":
                config["features"]["computer_use"] = True
            if mode == "async-feature-enabled":
                config["features"]["send_message_to_user_async"] = True
            with _owned_stdio(Path(sys.executable), ["-I", "-S", "-B", "-c", PEER, mode, json.dumps(config)], cwd,
                              {"SystemRoot": os.environ["SystemRoot"], "CODEX_HOME": str(self.approval.home)},
                              min(timeout, 0.4) if mode == "hang" else timeout, cancelled) as server:
                yield server

        catalog = {"setups": {"fixture-a": {"replica": str(self.replica)}}}
        catalog_source = (nullcontext() if registered else
                          patch("devorchestra.runtime.Catalog.read", return_value=catalog))
        with patch("devorchestra.native_manager.require_live_manager_admission"), \
                catalog_source, \
                patch("devorchestra.native_manager.open_app_server", side_effect=open_fixture):
            return self.runner.plan_native_manager(self.approval, self.request)

    def test_read_only_thread_boundary_can_stop_before_any_account_or_turn_rpc(self):
        config = self.effective_config()
        home = self.approval.home
        argv = ["-I", "-S", "-B", "-c", PEER, "thread-only", json.dumps(config)]
        with _owned_stdio(Path(sys.executable), argv, self.replica,
                          {"SystemRoot": os.environ["SystemRoot"], "CODEX_HOME": str(home)},
                          10, lambda: False) as server:
            sources = (home / "AGENTS.md", self.replica / "AGENTS.md")
            receipt = inspect_manager_thread_only(server, self.request, self.replica, sources, lambda: None)
            self.assertEqual({"thread_policy": "CONFIRMED", "instruction_sources": "CONFIRMED",
                              "account_rpc_sent": False, "turn_rpc_sent": False}, receipt)
        self.assertEqual("DRAINED_NORMALLY", server.cleanup_receipt["job"])
        self.assertEqual("CLOSED", server.cleanup_receipt["pipes"])

    def test_live_path_is_blocked_before_any_intent(self):
        with self.assertRaisesRegex(
            Blocked, "GENERAL_ADMISSION_UNVERIFIED: MANAGER_PRE_EXECUTION_BOUNDARY_UNVERIFIED",
        ):
            self.runner.plan_native_manager(self.approval, self.request)
        self.assertEqual([], self.ledger.read())

    def test_manager_admission_requires_both_trusted_boundaries_then_fresh_approval_and_cli(self):
        tools = "SIDE_EFFECTING_TOOLS_UNVERIFIED"
        egress = "PROVIDER_EGRESS_UNVERIFIED"
        self.assertEqual(tools, manager_admission(self.approval)["reason"])
        self.assertEqual([tools, egress], [item["code"] for item in
                                         manager_admission(self.approval)["missing_boundaries"]])
        with patch("devorchestra.native_manager._verified_manager_boundaries", return_value={tools}):
            self.assertEqual(egress, manager_admission(self.approval)["reason"])
        with patch("devorchestra.native_manager._verified_manager_boundaries", return_value={tools, egress}), \
                patch("devorchestra.native_manager.verify_app_server_binary") as verify:
            self.assertEqual("NEEDS_OWNER_APPROVAL", manager_admission()["state"])
            self.assertEqual("NEEDS_OWNER_APPROVAL",
                             manager_admission(replace(self.approval, expires_epoch=0))["state"])
            verify.assert_not_called()
            self.assertEqual("READY", manager_admission(self.approval)["state"])
            verify.assert_called_once_with(self.approval.binary)
            verify.side_effect = Blocked("fixture invalid CLI")
            self.assertEqual("BINARY_IDENTITY_INVALID", manager_admission(self.approval)["reason"])

    def test_explicit_candidate_uses_its_own_pin_and_never_falls_back(self):
        pin = "d" * 64
        self.approval = replace(self.approval, candidate_cli_sha256=pin,
                                request_hash=manager_request_hash(self.request, self.layers, pin))
        with patch("devorchestra.native_manager._verified_manager_boundaries", return_value=set()) as evidence:
            self.assertEqual("BLOCKED", manager_admission(self.approval)["state"])
            evidence.assert_called_once_with("APP_SERVER_EXPLICIT_CANDIDATE", pin)
        result, _ = self.run_fixture()
        self.assertEqual("APP_SERVER_EXPLICIT_CANDIDATE", result["manager_backend"])
        self.assertEqual(pin, result["cli_sha256"])

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
                "work_kind": "NONE", "product_outcome": "Clarify expected fixture",
                "validation_reason": "Await answer", "approach": "Ask the user",
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
        pre_thread_failures = ("tools-enabled", "api-account", "async-feature-enabled", "wrong-auth-provider")
        for mode in (*pre_thread_failures, "unsafe-thread", "network-thread", "wrong-instructions"):
            with self.subTest(mode=mode):
                self.ledger = Ledger(self.ledger.path.with_name(f"{mode}.sqlite3"),
                                     local_root=self.ledger.local_root)
                self.runner = Runner(self.ledger)
                self.ledger.initialize()
                with self.assertRaisesRegex(Blocked, "UNKNOWN"):
                    self.run_fixture(mode)
                expected = ["native_manager_intent"]
                if mode not in pre_thread_failures:
                    expected.append("native_manager_thread_dispatch")
                self.assertEqual(expected, [event["kind"] for event in self.ledger.read()])

    def test_async_message_with_valid_plan_json_is_not_a_manager_result(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("async-plan")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])
        with self.assertRaisesRegex(Blocked, "ownership requires recovery"):
            self.run_fixture()

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

    def test_public_common_and_role_fallbacks_are_hash_bound(self):
        from devorchestra.default_instructions import public_instruction
        from devorchestra.native_manager import prepare_manager

        (self.role_file.parent.parent / "common/orchestration-rules.md").unlink()
        self.role_file.unlink()
        layers = list(self.layers)
        for index, section in ((1, "project"), (3, "manager-planning")):
            item = public_instruction(section)
            layers[index] = {**layers[index], "version": item["version"], "sha256": item["sha256"]}
        approval = replace(self.approval, layers=tuple(layers),
                           request_hash=bind_instructions(self.request, layers))
        messages, _ = prepare_manager(self.ledger, approval, self.request)
        self.assertEqual(public_instruction("project")["text"], messages[0]["text"].strip())
        self.assertEqual(public_instruction("manager-planning")["text"], messages[1]["text"].strip())

    def test_registered_project_plans_without_a_mock_session_and_grants_no_write(self):
        source = self.replica.parent / "requirements"
        source.mkdir()
        (source / "goal.md").write_text("Synthetic planning requirement", encoding="utf-8")
        catalog = Catalog(self.ledger)
        limits = {"seconds": 10, "tokens": 8192, "cost_microunits": 0}
        catalog.save_limits(limits, 0)
        project = new_project("sample", "Sample", self.request.goal)
        project.update(local_path=str(source), acceptance=["Check fixture"], budget=limits)
        project["roles"]["Manager"] = {"model": self.request.model, "reasoning": self.request.reasoning,
                                        "budget": limits}
        revision = catalog.save_setup({"project": project, "replica": str(self.replica), "wait_seconds": 10})
        project["requested_scope"]["files"] = ["fixture.txt"]
        catalog.save_project(project, revision)
        catalog.register_requirement("sample", "goal.md", "GOAL")
        context = self.runner.native_manager_context("sample")
        preview = self.runner.inspect_manager_admission("sample")
        self.assertEqual("BLOCKED", preview["state"])
        self.assertEqual(digest(context), preview["summary"]["input_sha256"])
        self.request = replace(self.request, project_id="sample", verified_input_refs=(digest(context),))
        self.approval = replace(self.approval, request_hash=bind_instructions(self.request, self.layers))
        self.assertEqual([], self.ledger.read())
        result, _ = self.run_fixture(registered=True)
        self.assertEqual("OWNER_SCOPE_REQUIRED", result["task_candidate_status"])
        self.assertEqual("BLOCKED", result["next_role_admission"])
        self.assertEqual(context["development_id"], result["question_development_id"])
        self.assertFalse((self.replica / "fixture.txt").exists())

    def test_valid_turn_drains_commits_and_does_not_admit_developer(self):
        result, proposal = self.run_fixture()
        self.assertEqual("PROPOSED", proposal["disposition"])
        self.assertEqual("DRAINED", result["owned_tree"])
        self.assertEqual("BLOCKED", result["next_role_admission"])
        self.assertEqual("UNKNOWN", result["usage"])
        self.assertEqual("CHATGPT_ACCOUNT_CONFIRMED", result["authentication"])
        self.assertEqual({"job": "DRAINED_NORMALLY", "pipes": "CLOSED"}, result["turn_receipt"]["cleanup"])
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

    def test_duplicate_item_blocks_role_completion(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("duplicate-item")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])

    def test_compatible_managed_requirements_preserve_manager_profile(self):
        result, _ = self.run_fixture("managed-compatible")
        self.assertTrue(result["turn_receipt"]["configuration"]["managed_requirements_present"])
        self.assertEqual("BLOCKED", result["next_role_admission"])

    def test_conflicting_managed_requirements_block_before_thread_dispatch(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN.*MANAGER_MANAGED_REQUIREMENTS_CONFLICT:allowedLoginMethods"):
            self.run_fixture("managed-conflict")
        self.assertEqual(["native_manager_intent"], [event["kind"] for event in self.ledger.read()])

    def test_unrecognized_managed_requirements_fail_closed_without_disclosing_values(self):
        from devorchestra.native_manager import _verify_manager_requirements

        with self.assertRaisesRegex(Blocked, "^MANAGER_MANAGED_REQUIREMENTS_UNVERIFIED$"):
            _verify_manager_requirements({"network": {"private fixture": True}})

    def test_tool_request_never_dispatches_a_command(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("tool-call")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])

    def test_rpc_error_remains_unknown_without_raw_error(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN"):
            self.run_fixture("error")
        self.assertEqual("native_manager_dispatch", self.ledger.read()[-1]["kind"])
        self.assertNotIn("fixture private error", str(self.ledger.read()))

    def test_timeout_remains_unknown(self):
        with self.assertRaisesRegex(Blocked, "UNKNOWN.*TIMEOUT"):
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
