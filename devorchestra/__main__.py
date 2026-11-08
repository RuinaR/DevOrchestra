"""Local owner console. No server, model, shell or external service adapter."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from time import sleep
import sys

from .catalog import Catalog, new_project
from .dashboard import project_view, render
from .lease import Blocked
from .ledger import Ledger, canonical
from .runtime import Runner, Scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("init")
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
    args = parser.parse_args()
    try:
        if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
            raise Blocked("Windows LOCALAPPDATA required")
        ledger = Ledger(Path(os.environ["LOCALAPPDATA"]) / "DevOrchestra" / "p1.sqlite3")
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
        parser.exit(2, f"BLOCKED: {error}\n")


if __name__ == "__main__":
    main()
