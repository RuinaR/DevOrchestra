"""Local owner console for scoped Controller operations."""
import argparse
import json
import os
import sqlite3
from time import sleep
import sys

from .adapter import ModelBackend
from .catalog import Catalog, new_project
from .dashboard import project_view, render
from .development import session_view
from .lease import Blocked
from .ledger import Ledger, canonical, digest
from .policy import DEFAULT_BACKEND, DevelopmentMode, ExecutionBackend, live_preflight
from .runtime import Runner, Scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("init")
    commands.add_parser("local-status", help="Inspect project-local storage without creating it")
    manager_check = commands.add_parser("manager-preflight", help="Inspect Manager backend admission without starting Codex")
    manager_check.add_argument("--project", required=True)
    manager_turn = commands.add_parser("manager-turn", help="Review or approve one registered Manager turn")
    manager_turn.add_argument("--project", required=True)
    for option in ("binary", "cli-sha256", "home", "account", "model"):
        manager_turn.add_argument("--" + option)
    manager_turn.add_argument("--expires", type=int, required=True, help="Review expiry as Unix seconds")
    manager_turn.add_argument("--approve", metavar="REVIEW_SHA256")
    for command in (manager_check, manager_turn):
        command.add_argument("--backend", type=ModelBackend, choices=list(ModelBackend),
                             default=ModelBackend.APP_SERVER, metavar="BACKEND",
                             help=", ".join(item.value for item in ModelBackend))
    commands.add_parser("chatgpt-accounts", help="List local DevOrchestra account labels")
    commands.add_parser("chatgpt-login", help="Open official browser consent").add_argument("--account")
    for action in ("chatgpt-models", "chatgpt-logout"):
        commands.add_parser(action).add_argument("--account", required=True)
    developer = commands.add_parser("developer-edit", help="Review or approve one controlled subscription file edit")
    for option in ("project", "manager-run", "account", "model"):
        developer.add_argument("--" + option, required=True)
    developer.add_argument("--expires", type=int, required=True)
    developer.add_argument("--approve", metavar="REVIEW_SHA256")
    developer_diff = commands.add_parser("developer-diff", help="Read a protected Developer diff locally")
    developer_diff.add_argument("--project", required=True)
    developer_diff.add_argument("--run", required=True)
    cycle = commands.add_parser("cycle-role", help="Review or approve the next Developer evidence role")
    cycle.add_argument("--phase", required=True, choices=("reviewer", "verifier", "manager-final"))
    cycle.add_argument("--project", required=True)
    cycle.add_argument("--developer-run", required=True)
    cycle.add_argument("--account")
    cycle.add_argument("--model")
    cycle.add_argument("--vectors", type=json.loads, help="Owner-approved JSON test vectors for the fixed Python Verifier")
    cycle.add_argument("--expires", type=int, required=True)
    cycle.add_argument("--approve", metavar="REVIEW_SHA256")
    rework = commands.add_parser("cycle-rework", help="Read protected rework evidence locally")
    rework.add_argument("--project", required=True)
    rework.add_argument("--developer-run", required=True)
    unified = commands.add_parser("subscription-session", help="Review or run one approved subscription development session")
    unified.add_argument("--project", required=True)
    unified.add_argument("--account", required=True)
    unified.add_argument("--vectors", type=json.loads, required=True)
    unified.add_argument("--seconds", type=int)
    unified.add_argument("--tokens", type=int)
    unified.add_argument("--reworks", type=int, default=2)
    unified.add_argument("--expires", type=int, required=True)
    unified.add_argument("--approve", metavar="REVIEW_SHA256")
    unified.add_argument("--permission", action="append", dest="permissions")
    unified.add_argument("--cycles", type=int, help="Optional maximum number of development cycles")
    unified.add_argument("--criterion-checks", type=json.loads, help="Approved criterion-to-observation bindings")
    unified.add_argument("--max-stagnant", type=int, default=2)
    reverify = commands.add_parser("subscription-reverify-winforms",
                                   help="Approve supplemental GUI verification for a reviewed change")
    reverify.add_argument("--project", required=True)
    reverify.add_argument("--account", required=True)
    reverify.add_argument("--scenario", type=json.loads, required=True)
    reverify.add_argument("--seconds", type=int, required=True)
    reverify.add_argument("--tokens", type=int, required=True)
    reverify.add_argument("--expires", type=int, required=True)
    reverify.add_argument("--approve", metavar="REVIEW_SHA256")
    unified_recover = commands.add_parser("subscription-session-recover", help="Close an interrupted session without replay")
    unified_recover.add_argument("--project", required=True)
    unified_recover.add_argument("--run", required=True)
    mxc_host = commands.add_parser("manager-mxc-offline", help="Measure MXC files and App Server stdio without login")
    for option in ("launcher", "launcher-sha256", "candidate", "candidate-sha256", "powershell-sha256"):
        mxc_host.add_argument("--" + option, required=True)
    mxc_host.add_argument("--loopback-only", action="store_true",
                          help="Measure direct TCP to an owned loopback listener without starting App Server")
    gui_verify = commands.add_parser("gui-verify", help="Review or run an approved Windows GUI scenario")
    gui_verify.add_argument("--project", required=True)
    gui_verify.add_argument("--registered", action="store_true")
    gui_verify.add_argument("--exe", metavar="FILENAME", help="Controller-built EXE in the registered replica")
    gui_verify.add_argument("--role", choices=("Manager", "Developer", "Reviewer", "Verifier"), default="Verifier")
    gui_verify.add_argument("--receipt", action="store_true")
    gui_verify.add_argument("--approve", metavar="SCOPE_SHA256")
    gui_build = commands.add_parser("gui-build-exe", help="Build the reviewed standalone GUI test app")
    gui_build.add_argument("--project", required=True)
    gui_build.add_argument("--exe", default="DevOrchestraQa.exe")
    gui_build.add_argument("--approve", metavar="SCOPE_SHA256")
    build_verify = commands.add_parser("build-verify", help="Review or run an approved offline build verification")
    build_verify.add_argument("--project", required=True)
    build_verify.add_argument("--kind", required=True)
    build_verify.add_argument("--launcher", required=True)
    build_verify.add_argument("--source", action="append", required=True)
    build_verify.add_argument("--vectors", type=json.loads, required=True)
    build_verify.add_argument("--seconds", type=int, required=True)
    build_verify.add_argument("--expires", type=int, required=True)
    build_verify.add_argument("--approve", metavar="SCOPE_SHA256")
    commands.add_parser("environment-review", help="Inspect tool requirements without running the project").add_argument(
        "--project", required=True,
    )
    commands.add_parser("artifact-review", help="Classify retained local builds and cleanup candidates").add_argument(
        "--retain", type=int, default=2,
    )
    install_sdk = commands.add_parser("sdk-install", help="Review or install an approved project-local .NET ZIP")
    for name in ("project", "launcher"):
        install_sdk.add_argument("--" + name, required=True)
    install_sdk.add_argument("--expires", required=True, type=int)
    install_sdk.add_argument("--approve")
    onboarding = commands.add_parser("onboard-git", help="Review or create an isolated existing Git baseline")
    onboarding.add_argument("--project", required=True)
    onboarding.add_argument("--destination")
    onboarding.add_argument("--include", action="append", default=[])
    onboarding.add_argument("--approve")
    commands.add_parser("replica-changes", help="Review frozen original versus replica bytes without applying").add_argument(
        "--project", required=True,
    )
    original_apply = commands.add_parser("original-apply", help="Review or apply verified replica changes")
    original_apply.add_argument("--project", required=True)
    original_apply.add_argument("--expires", required=True, type=int)
    original_apply.add_argument("--approve")
    shutdown = commands.add_parser("shutdown")
    shutdown.add_argument("transition", choices=("request", "cancel", "finalize", "resume"))
    shutdown.add_argument("--request-id")
    shutdown.add_argument("--generation", type=int)
    preflight = commands.add_parser("live-preflight", help="Check declared capabilities without running anything")
    preflight.add_argument("--requires", action="append", default=[], metavar="CAPABILITY")
    preflight.add_argument("--backend", choices=[backend.value for backend in ExecutionBackend],
                           default=DEFAULT_BACKEND.value)
    preflight.add_argument("--mode", choices=[mode.value for mode in DevelopmentMode],
                           default=DevelopmentMode.GENERAL.value)
    start = commands.add_parser("start")
    start.add_argument("--project", default="fixture-a", help="Run a synthetic workflow only")
    start.add_argument("--scenario", type=Scenario, choices=list(Scenario), default=Scenario.NORMAL)
    for action in ("step", "run", "approve", "pause", "resume", "cancel", "recover"):
        commands.add_parser(action).add_argument("run_id")
    for action in ("dashboard", "audit"):
        command = commands.add_parser(action)
        command.add_argument("--project")
        if action == "audit":
            command.add_argument("--role", choices=("Manager", "Developer", "Reviewer", "Verifier"))
            command.add_argument("--run")
        else:
            command.add_argument("--watch", action="store_true", help="Print changed snapshots")
    template = commands.add_parser("project-template")
    template.add_argument("project_id")
    template.add_argument("name")
    template.add_argument("goal")
    commands.add_parser("project-list")
    commands.add_parser("project-save").add_argument("--revision", type=int, default=0)
    commands.add_parser("limits-save").add_argument("--revision", type=int, required=True)
    development = commands.add_parser("session-start")
    development.add_argument("--project", default="fixture-a")
    development.add_argument("--seconds", type=int, default=600)
    development.add_argument("--tokens", type=int, default=20)
    development.add_argument("--cost-microunits", type=int, default=0)
    development.add_argument("--cycles", type=int, default=2)
    development.add_argument("--retries", type=int, default=0)
    development.add_argument("--task-limits", type=json.loads)
    for action in ("session-next", "session-stop"):
        commands.add_parser(action).add_argument("development_id")
    commands.add_parser("session-status").add_argument("--project")
    args = parser.parse_args()
    if args.action == "live-preflight":
        print(canonical(live_preflight(args.requires, backend=args.backend, mode=args.mode)))
        return 2
    try:
        ledger = Ledger.for_project()
        if args.action == "original-apply":
            from . import original_apply

            runner = Runner(ledger)
            if args.approve:
                print(canonical(original_apply.run(runner, args.project, args.expires, args.approve)))
            else:
                scope, patch = original_apply.review(runner, args.project, args.expires)
                print(canonical({"review": scope, "review_sha256": digest(scope), "diff": patch}))
            return
        if args.action == "replica-changes":
            from .replica_readiness import changes_review

            print(canonical(changes_review(ledger, args.project)))
            return
        if args.action == "onboard-git":
            from . import project_onboarding

            if args.destination is None:
                print(canonical(project_onboarding.status(ledger, args.project)))
            elif args.approve is None:
                scope = project_onboarding.review(Runner(ledger), args.project, args.destination, args.include)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
            else:
                print(canonical(project_onboarding.create(Runner(ledger), args.project, args.destination,
                                                         args.include, args.approve)))
            return
        if args.action == "sdk-install":
            runner = Runner(ledger)
            fields = (args.project, args.launcher, args.expires)
            if args.approve is None:
                scope = runner.sdk_install_scope(*fields)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
                return
            receipt = runner.sdk_install(*fields, args.approve)
            print(canonical(receipt))
            return 0 if receipt["result"] == "PASS" else 2
        if args.action == "artifact-review":
            from .owned_artifacts import review

            print(canonical(review(ledger, args.retain)))
            return
        if args.action == "environment-review":
            from .development_environment import review

            print(canonical(review(ledger, args.project)))
            return
        if args.action == "build-verify":
            runner = Runner(ledger)
            fields = (args.project, args.kind, args.launcher, args.source, args.vectors, args.seconds, args.expires)
            if args.approve is None:
                scope = runner.build_verifier_scope(*fields)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
                return
            receipt = runner.build_verify(*fields, args.approve)
            print(canonical(receipt))
            return 0 if receipt["result"] == "PASS" else 2
        if args.action == "subscription-session-recover":
            from .subscription_session import recover

            print(canonical(recover(Runner(ledger), args.project, args.run)))
            return
        if args.action == "subscription-reverify-winforms":
            from . import subscription_session

            fields = (Runner(ledger), args.project, args.account, args.expires, args.seconds, args.tokens, args.scenario)
            if args.approve is None:
                scope, _, _ = subscription_session.review_winforms_reverification(*fields)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
                return
            receipt = subscription_session.run_winforms_reverification(*fields, args.approve)
            print(canonical(receipt))
            return 0 if receipt["outcome"] == "COMPLETED" else 2
        if args.action == "subscription-session":
            from . import subscription_session

            fields = (Runner(ledger), args.project, args.account, args.expires, args.seconds, args.tokens,
                      args.reworks, args.vectors)
            if args.approve is None:
                scope = subscription_session.review(*fields, permissions=args.permissions, cycles=args.cycles,
                                                    criterion_checks=args.criterion_checks, max_stagnant=args.max_stagnant)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
                return
            receipt = subscription_session.run(*fields, args.approve, permissions=args.permissions, cycles=args.cycles,
                                                criterion_checks=args.criterion_checks, max_stagnant=args.max_stagnant)
            print(canonical(receipt))
            return 0 if receipt["outcome"] == "COMPLETED" else 2
        if args.action == "cycle-rework":
            from .subscription_cycle import rework_context

            print(canonical(rework_context(ledger, args.project, args.developer_run)))
            return
        if args.action == "cycle-role":
            from . import subscription_cycle

            fields = (Runner(ledger), args.project, args.developer_run, args.phase,
                      args.account, args.model, args.expires, args.vectors)
            if args.approve is None:
                scope = subscription_cycle.review(*fields)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
                return
            receipt = subscription_cycle.run(*fields, args.approve)
            print(canonical(receipt))
            if receipt["outcome"] != "COMPLETED":
                return 2
            if args.phase == "verifier":
                return receipt["exit_code"] if receipt["exit_code"] is not None else 2
            return 0
        if args.action == "developer-diff":
            from .subscription_developer import read_diff

            print(read_diff(ledger, args.project, args.run), end="")
            return
        if args.action == "developer-edit":
            from . import subscription_developer

            fields = (Runner(ledger), args.project, args.manager_run, args.account, args.model, args.expires)
            if args.approve is None:
                scope, _, _ = subscription_developer.review(*fields)
                print(canonical({"review": scope, "review_sha256": digest(scope)}))
                return
            receipt = subscription_developer.run(*fields, args.approve)
            print(canonical(receipt))
            return 0 if receipt["outcome"] == "COMPLETED" else 2
        if args.action.startswith("chatgpt-"):
            from .chatgpt_subscription import AccountStore, SubscriptionError, list_models
            from .lease import execution_slot

            store = AccountStore(ledger)
            if args.action == "chatgpt-accounts":
                print(canonical(store.accounts()))
                return
            with execution_slot() as abandoned:
                Runner(ledger)._idle_diagnostic(abandoned)
                if args.action == "chatgpt-login":
                    result = store.login(os.startfile, args.account)
                elif args.action == "chatgpt-logout":
                    result = store.logout(args.account)
                else:
                    try:
                        result = list_models(store.access(args.account))
                    except SubscriptionError as error:
                        if error.status == 401:
                            store.invalidate(args.account)
                        raise
                print(canonical(result))
            return
        if args.action == "manager-mxc-offline":
            from .mxc_host_probe import run_host_probe

            receipt = run_host_probe(ledger, args.launcher, args.launcher_sha256, args.candidate,
                                     args.candidate_sha256, args.powershell_sha256, loopback_only=args.loopback_only)
            print(canonical(receipt))
            if args.loopback_only:
                return 2 if receipt["loopback"]["status"] == "INCONCLUSIVE" else 0
            return 0 if receipt["file_probe"]["status"] == "PASS" else 2
        if args.action == "manager-preflight":
            decision = Runner(ledger).inspect_manager_admission(args.project, args.backend)
            print(canonical(decision))
            return 2 if decision["state"] == "BLOCKED" else 0
        if args.action == "manager-turn":
            runner = Runner(ledger)
            if args.backend is ModelBackend.CHATGPT_TEXT:
                from . import subscription_manager

                if not args.account or not args.model or any((args.binary, args.cli_sha256, args.home)):
                    raise Blocked("Subscription Manager requires --account and --model")
                fields = (runner, args.project, args.account, args.model, args.expires)
                if args.approve is None:
                    review, _, _ = subscription_manager.review(*fields)
                    print(canonical({"review": review, "review_sha256": digest(review)}))
                    return
                receipt, _ = subscription_manager.run(*fields, args.approve)
                print(canonical(receipt))
                return 0 if receipt["outcome"] == "COMPLETED" else 2
            if not all((args.binary, args.cli_sha256, args.home)) or args.account or args.model:
                raise Blocked("App Server Manager requires --binary, --cli-sha256 and --home")
            fields = (args.project, args.binary, args.cli_sha256, args.home, args.expires)
            if args.approve is None:
                review = runner.review_native_manager(*fields, backend=args.backend)
                print(canonical({"review": review, "review_sha256": digest(review)}))
                return
            receipt, _proposal = runner.run_reviewed_native_manager(*fields, args.approve, backend=args.backend)
            print(canonical(receipt))
            return
        if args.action == "gui-build-exe":
            runner = Runner(ledger)
            if args.approve is None:
                scope = runner.gui_exe_build_scope(args.project, args.exe)
                print(canonical({"scope": scope, "scope_sha256": digest(scope)}))
                return
            result = runner.build_gui_exe(args.project, args.exe, args.approve)
            print(canonical(result))
            return 0 if result["result"] == "PASS" else 2
        if args.action == "gui-verify":
            runner = Runner(ledger)
            options = {"registered": args.registered or args.exe is not None, "role": args.role, "exe_name": args.exe}
            if args.receipt:
                if args.approve is not None:
                    raise Blocked("Receipt lookup cannot approve an execution")
                print(canonical(runner.gui_verification_receipt(args.project, role=args.role)))
                return
            if args.approve is None:
                scope = runner.gui_verification_scope(args.project, **options)
                print(canonical({"scope": scope, "scope_sha256": digest(scope)}))
                return
            receipt = runner.verify_gui_fixture(args.project, args.approve, **options)
            print(canonical(receipt))
            return 0 if receipt["result"] == "PASS" else 2
        if args.action == "local-status":
            status = ledger.storage_status()
            print(canonical(status))
            return 2 if status["status"] == "BLOCKED" else None
        if args.action == "shutdown":
            print(canonical(Runner(ledger).shutdown(args.transition, args.request_id,
                                                    expected_generation=args.generation)))
            return
        if args.action == "session-start":
            print(canonical(Runner(ledger).development(
                "start", project=args.project, cycles=args.cycles, retries=args.retries,
                task_limits=args.task_limits,
                limits={"seconds": args.seconds, "tokens": args.tokens, "cost_microunits": args.cost_microunits},
            )))
            return
        if args.action in ("session-next", "session-stop"):
            print(canonical(Runner(ledger).development(args.action.removeprefix("session-"),
                                                        development_id=args.development_id)))
            return
        if args.action == "session-status":
            print(canonical(session_view(ledger.read(), args.project)))
            return
        if args.action == "project-template":
            print(json.dumps(new_project(args.project_id, args.name, args.goal), indent=2, ensure_ascii=True))
            return
        if args.action == "project-list":
            print(canonical(Catalog(ledger).read()))
            return
        if args.action in ("project-save", "limits-save"):
            raw = sys.stdin.read(131_073)
            if len(raw) > 131_072:
                raise Blocked("Configuration input too large")
            catalog = Catalog(ledger)
            save = catalog.save_project if args.action == "project-save" else catalog.save_limits
            print(canonical({"revision": save(json.loads(raw), args.revision)}))
            return
        if args.action == "dashboard":
            previous = None
            while True:
                current = render(project_view(ledger.read(), args.project))
                if current != previous:
                    print(current, end="\n\n" if args.watch else "\n", flush=True)
                    previous = current
                if not args.watch:
                    break
                sleep(1)
        elif args.action == "audit":
            for seq, event in enumerate(ledger.read(), 1):
                if ((args.project is None or event["project_id"] == args.project)
                        and (args.role is None or event.get("role", event.get("request", {}).get("role")) == args.role)
                        and (args.run is None or event["run_id"] == args.run)):
                    print(canonical({"sequence": seq, **event}))
        else:
            event = Runner(ledger).command(args.action, getattr(args, "project", "fixture-a"),
                                           getattr(args, "scenario", Scenario.NORMAL),
                                           getattr(args, "run_id", None))
            print(canonical(event) if event else "Initialized P1 Mock ledger")
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted. Check the ledger before continuing.\n")
    except (Blocked, OSError, sqlite3.Error, ValueError, KeyError) as error:
        if args.action == "local-status":
            print(canonical({"storage": ".ai-local", "status": "BLOCKED",
                             "legacy_import": "NOT_ATTEMPTED"}))
            return 2
        parser.exit(2, f"BLOCKED: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
