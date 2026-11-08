"""DevOrchestra Command Center. Read-only desktop viewer for Windows."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import ttk

from .lease import Blocked
from .ledger import Ledger
from .projection import ProjectionReader
from .runtime import PROJECTS

BG, PANEL, TILE, EDGE = "#09121f", "#111f30", "#172a40", "#395575"
INK, MUTED, CYAN = "#f1f6ff", "#b9cde4", "#82d8ff"
TEAL, AMBER, RED = "#78efca", "#ffd082", "#ff9da9"
ROLE_MARKS = {"Manager": "01 / CMD", "Developer": "02 / DEV",
              "Reviewer": "03 / REV", "Verifier": "04 / VFY"}
POLL_MS = 1500

# Display only these structured receipt fields in the local evidence inspector.
EVIDENCE_FIELDS = (
    "sequence", "timestamp", "source", "kind", "state", "project_id", "run_id",
    "task_id", "generation", "role", "agent_run_id", "phase", "model", "reasoning",
    "policy_hash", "instruction", "goal", "git_before", "git_after", "reviewed_files",
    "decision", "result", "reason", "request", "approval_id", "issued", "expires",
    "safe_args_hash", "diff", "test", "commands", "exit_code", "elapsed_seconds",
    "synthetic_tokens", "measured_tokens", "cost", "resource_peaks", "retries",
    "handoff", "resume_phase", "owned_children", "probe_id", "fixture", "active_processes",
    "active_before_stop", "total_processes", "root_exited_before_stop", "job_memory_limit", "job_process_limit",
)


def color(state):
    if state in ("FAILED", "FAIL", "BLOCKED", "DENY"):
        return RED
    if state in ("NEED_APPROVAL", "RECOVERING", "PAUSED", "UNKNOWN"):
        return AMBER
    if state in ("COMPLETED", "PASS", "OK", "RECORDED OK"):
        return TEAL
    return CYAN


def audit_rows(view, run_id=None, role=None):
    return [event for event in reversed(view["events"])
            if (run_id is None or event["run_id"] == run_id)
            and (role is None or event.get("role", event.get("request", {}).get("role")) == role)]


def evidence_text(event):
    fields = {key: event[key] for key in EVIDENCE_FIELDS if key in event}
    return json.dumps(fields, ensure_ascii=False, indent=2)


def label(parent, text="", size=10, fg=INK, bg=PANEL, bold=False):
    return tk.Label(parent, text=text, bg=bg, fg=fg, anchor="w", justify="left",
                    font=("Segoe UI", size, "bold" if bold else "normal"))


def section(parent, name):
    frame = tk.Frame(parent, bg=PANEL, padx=13, pady=9,
                     highlightbackground=EDGE, highlightthickness=1)
    label(frame, name, fg=CYAN, bold=True).pack(fill="x")
    return frame


class CommandCenter:
    def __init__(self, root, reader):
        self.root = root
        self.reader = reader
        self.view = None
        self.event_lookup = {}
        self.project_ids = list(PROJECTS)
        self.run_options = {}
        root.title("DevOrchestra | Command Center")
        root.configure(bg=BG)
        root.geometry("1220x830")
        root.minsize(900, 660)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)
        self.theme()

        header = tk.Frame(root, bg=BG, padx=16, pady=10)
        header.grid(row=0, column=0, sticky="ew")
        label(header, "DEVORCHESTRA    /    COMMAND CENTER", 20, bg=BG, bold=True).pack(side="left")
        self.global_badge = label(header, fg=CYAN, bg=BG, bold=True)
        self.global_badge.pack(side="right")
        self.observation = label(root, bg=BG, fg=MUTED)
        self.observation.grid(row=4, column=0, sticky="ew", padx=16, pady=6)

        command = tk.Frame(root, bg=BG)
        command.grid(row=1, column=0, sticky="ew", padx=16)
        command.columnconfigure(1, weight=2)
        command.columnconfigure(2, weight=1)
        projects = section(command, "01  /  PROJECTS")
        projects.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.project = ttk.Combobox(projects, state="readonly", width=16,
                                    values=[PROJECTS[key].name for key in self.project_ids])
        self.project.pack(fill="x", pady=(7, 3))
        self.project.current(0)
        self.project.bind("<<ComboboxSelected>>", self.select_project)
        self.project_states = label(projects, fg=MUTED)
        self.project_states.pack(fill="x")

        mission = section(command, "02  /  MISSION & GATES")
        mission.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.mission = label(mission, size=13, bold=True)
        self.mission.pack(fill="x", pady=(4, 0))
        self.goal = label(mission, fg=MUTED)
        self.goal.pack(fill="x")
        self.gates = label(mission)
        self.gates.pack(fill="x", pady=(3, 0))

        safety = section(command, "03  /  SAFETY & BUDGET")
        safety.grid(row=0, column=2, sticky="nsew")
        self.safety = label(safety, fg=MUTED)
        self.safety.pack(fill="x", pady=(5, 0))
        self.resources = label(safety)
        self.resources.pack(fill="x")

        operations = tk.Frame(root, bg=BG)
        operations.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        timeline = section(operations, "04  /  QUEST TIMELINE")
        timeline.pack(fill="x")
        lane = tk.Frame(timeline, bg=PANEL)
        lane.pack(fill="x", pady=(5, 0))
        self.phases = []
        for index, name in enumerate(("PLAN", "DEVELOP", "REVIEW", "VERIFY", "DECIDE")):
            lane.columnconfigure(index, weight=1, uniform="phase")
            badge = label(lane, bg=TILE, bold=True)
            badge.grid(row=0, column=index, sticky="ew", padx=(0, 4), ipady=4, ipadx=4)
            self.phases.append((name, badge))

        operators = section(operations, "05  /  OPERATOR ROSTER")
        operators.pack(fill="x", pady=(8, 0))
        roster = tk.Frame(operators, bg=PANEL)
        roster.pack(fill="x", pady=(5, 0))
        self.cards = {}
        for column, (role, mark) in enumerate(ROLE_MARKS.items()):
            roster.columnconfigure(column, weight=1, uniform="role")
            card = tk.Frame(roster, bg=TILE, padx=8, pady=4)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 5))
            button = ttk.Button(card, text=f"{mark}   {role}", command=lambda r=role: self.show_role(r))
            button.pack(fill="x")
            status = label(card, bg=TILE, bold=True)
            status.pack(fill="x", pady=(4, 0))
            detail = label(card, size=9, fg=MUTED, bg=TILE)
            detail.pack(fill="x")
            self.cards[role] = (status, detail)

        self.build_audit(root)
        root.bind("<F5>", lambda _: self.refresh())
        self.refresh()
        root.after(POLL_MS, self.poll)

    def theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=TILE, foreground=INK, fieldbackground=TILE,
                        bordercolor=EDGE, font=("Segoe UI", 10))
        style.configure("TButton", padding=(5, 3))
        style.map("TButton", background=[("pressed", EDGE), ("active", "#254665")],
                  bordercolor=[("focus", CYAN)])
        style.map("TCombobox", fieldbackground=[("readonly", TILE)],
                  selectbackground=[("readonly", EDGE)], selectforeground=[("readonly", INK)])
        style.configure("Treeview", rowheight=24)
        style.map("Treeview", background=[("selected", "#315879")],
                  foreground=[("selected", INK)])

    def build_audit(self, root):
        audit = section(root, "06  /  EVENT STREAM & EVIDENCE")
        audit.grid(row=3, column=0, sticky="nsew", padx=16)
        controls = tk.Frame(audit, bg=PANEL)
        controls.pack(fill="x", pady=4)
        label(controls, "Run").pack(side="left", padx=(0, 4))
        self.run_filter = ttk.Combobox(controls, state="readonly", width=27)
        self.run_filter.pack(side="left", padx=(0, 12))
        label(controls, "Role").pack(side="left", padx=(0, 4))
        self.role_filter = ttk.Combobox(controls, state="readonly", width=15,
                                        values=("All roles", *ROLE_MARKS))
        self.role_filter.current(0)
        self.role_filter.pack(side="left")
        for box in (self.run_filter, self.role_filter):
            box.bind("<<ComboboxSelected>>", lambda _: self.render_audit())
        self.audit_count = label(controls, fg=MUTED)
        self.audit_count.pack(side="right")

        split = ttk.Panedwindow(audit, orient="horizontal")
        split.pack(fill="both", expand=True)
        left, right = tk.Frame(split, bg=PANEL), tk.Frame(split, bg=PANEL)
        split.add(left, weight=3)
        split.add(right, weight=2)
        columns = ("seq", "role", "event", "state")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=4)
        for key, title, width in zip(columns, ("SEQ", "ACTOR", "EVENT", "STATE"), (52, 95, 152, 115)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=44, stretch=(key == "event"))
        self.tree.bind("<<TreeviewSelect>>", self.select_event)
        self.scrollable(left, self.tree)
        self.evidence = tk.Text(right, height=4, width=36, wrap="word", bg=BG, fg=INK,
                                relief="flat", font=("Consolas", 10), padx=8, pady=5,
                                highlightthickness=1, highlightbackground=EDGE,
                                highlightcolor=CYAN, state="disabled")
        self.scrollable(right, self.evidence)

    @staticmethod
    def scrollable(parent, widget):
        scrollbar = ttk.Scrollbar(parent, command=widget.yview)
        widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        widget.pack(side="left", fill="both", expand=True)

    def select_project(self, _event):
        self.run_filter.set("")
        self.role_filter.current(0)
        self.view = None
        self.refresh()

    def poll(self):
        self.refresh()
        self.root.after(POLL_MS, self.poll)

    def refresh(self):
        try:
            project_id = self.project_ids[self.project.current()]
            view = self.reader.read(project_id)
        except (Blocked, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            self.clear(f"{type(error).__name__}: {error}")
            return
        if (self.view is None or view["snapshot_version"] != self.view["snapshot_version"]
                or view["project_id"] != self.view["project_id"]):
            self.view = view
            self.render()
        seq, tail = view["snapshot_version"]
        self.observation.configure(text=f"READ ONLY  /  MOCK LEDGER  /  Seq {seq} · "
                                   f"{tail[:12] or 'empty'}  /  Verified {view['as_of_utc']}")

    def clear(self, reason):
        self.view = None
        self.global_badge.configure(text="GLOBAL / BLOCKED", fg=RED)
        self.observation.configure(text="READ ONLY / Ledger unavailable · no current evidence")
        self.mission.configure(text="Ledger unavailable", fg=RED)
        self.goal.configure(text=reason, wraplength=350)
        self.project_states.configure(text="UNKNOWN")
        for widget in (self.gates, self.safety, self.resources):
            widget.configure(text="UNKNOWN")
        for name, badge in self.phases:
            badge.configure(text=f"{name}\nUNKNOWN", fg=MUTED)
        for status, detail in self.cards.values():
            status.configure(text="UNKNOWN", fg=MUTED)
            detail.configure(text="")
        self.run_filter.configure(values=())
        self.run_filter.set("")
        self.render_audit()

    def render(self):
        view = self.view
        run, current = view["run"], view["global_run"]
        state = view["global_state"]
        global_project = current["project"]["name"] if current else "No run"
        self.global_badge.configure(text=f"GLOBAL / {state} · {global_project}", fg=color(state))
        self.project_states.configure(text="\n".join(
            f"{PROJECTS[key].name} · {status}" for key, status in view["project_states"].items()))
        self.mission.configure(text=f"{view['project_name']}  /  {view['project_state']}",
                               fg=color(view["project_state"]))
        self.goal.configure(text=(f"{run['goal']}\nGen {run['generation']} · Run {run['run_id'][:10]}"
                                  if run else "No recorded mission"), wraplength=350)
        self.gates.configure(text="\n".join(
            f"{gate['name']}   {gate['state']}" +
            (f" · {gate['git_sha'][:7]} ({gate['scope']})" if gate.get("git_sha") else "")
            for gate in view["milestone_gates"]))
        self.render_safety(view)
        for (name, badge), phase in zip(self.phases, view["pipeline_phases"]):
            status = phase["state"]
            badge.configure(text=f"{name}\n{status}", fg=color(status))
        for role, (status, detail) in self.cards.items():
            record = view["role_cards"][role]
            start, finish, latest = record["start"], record["finish"], record["latest"]
            if start is None:
                status.configure(text="NO EXECUTION", fg=MUTED)
                detail.configure(text="Awaiting ledger evidence")
                continue
            outcome = latest.get("result", "UNKNOWN")
            phase = start["phase"]
            if finish and finish["state"] == "BLOCKED":
                outcome = "BLOCKED"
            status.configure(text=f"{outcome} · {phase}", fg=color(outcome))
            duration = f"{finish['elapsed_seconds']:.3f}s" if finish else "duration unknown"
            detail.configure(text=f"{start['model']} · {duration}\n"
                                  f"Receipt #{latest['sequence']} · {start['run_id'][:8]}")

        self.run_options = {"All runs": None}
        for event in reversed(view["events"]):
            if event["kind"] == "run_started":
                run_id = event["run_id"]
                self.run_options[f"Gen {event['generation']} · {run_id[:10]}"] = run_id
        self.run_filter.configure(values=tuple(self.run_options))
        if self.run_filter.get() not in self.run_options:
            self.run_filter.current(0)
        self.render_audit()

    def render_safety(self, view):
        resources = view["resources"]
        latest = view["safety"]["latest"]
        approval = view["safety"]["approval"]
        pending = view["unconfirmed_role"]
        safety = view["safety"]
        baseline = safety["security_baseline"]
        live = f"Live role UNKNOWN · {pending} intent" if pending else "No recorded open role"
        gate = approval.get("decision", approval["kind"]) if approval else "NO RECORD"
        reason = latest.get("reason")
        job, recovery = safety["job"], safety["recovery"]
        if safety["job_pending"]:
            job_status = f"Owned Job #{job['sequence']} · drain UNKNOWN"
        elif job:
            job_status = f"Last synthetic Job #{job['sequence']} · drained ({job['active_processes']})"
        else:
            job_status = "Owned Job · no receipt"
        recovery_status = (f"Recovery/stop #{recovery['sequence']} · {recovery.get('result', recovery['state'])}"
                           if recovery else "")
        self.safety.configure(text="\n".join(filter(None, (
            live, f"Slot · {safety['lease']}", job_status, recovery_status,
            f"Real execution BLOCKED · {safety['block_reason']}",
            safety["isolation_mode"],
            f"Files {safety['files']} / Net {safety['network']} / Credentials {safety['credentials']}",
            f"Recorded baseline · {baseline['scope']} @ {baseline['git_sha'][:7]}",
            f"Approval {gate}" + (f" · {reason}" if reason else ""),
        ))))
        if resources["elapsed"] is None:
            self.resources.configure(text="Time, tokens, cost · UNKNOWN")
            return
        self.resources.configure(text=f"Role time {resources['elapsed']:.3f}s / "
                                 f"{resources['time_budget']}s run budget\n"
                                 f"Deadline {datetime.fromtimestamp(resources['deadline'], timezone.utc):%H:%M:%S} UTC\n"
                                 f"Synthetic tokens {resources['synthetic_tokens']} / {resources['token_limit']}\n"
                                 f"Model tokens · {resources['measured_tokens']}\n"
                                 f"Cost · {resources['cost']} / cap {resources['cost_limit']}")

    def show_role(self, role):
        self.role_filter.set(role)
        self.run_filter.set("All runs")
        self.render_audit()
        self.tree.focus_set()

    def render_audit(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.event_lookup = {}
        rows = []
        if self.view:
            role = self.role_filter.get()
            rows = audit_rows(self.view, self.run_options.get(self.run_filter.get()),
                              None if role == "All roles" else role)
        self.audit_count.configure(text=f"{len(rows)} verified receipts")
        for event in rows:
            key = str(event["sequence"])
            self.event_lookup[key] = event
            actor = event.get("role", event.get("request", {}).get("role", "Runner"))
            self.tree.insert("", "end", iid=key,
                             values=(key, actor, event["kind"], event["state"]))
        key = selected[0] if selected and selected[0] in self.event_lookup else next(iter(self.event_lookup), None)
        if key:
            self.tree.selection_set(key)
            self.tree.focus(key)
            self.tree.see(key)
        self.select_event()

    def select_event(self, _event=None):
        selected = self.tree.selection()
        event = self.event_lookup.get(selected[0]) if selected else None
        self.evidence.configure(state="normal")
        self.evidence.delete("1.0", "end")
        self.evidence.insert("1.0", evidence_text(event) if event else "No matching evidence.")
        self.evidence.configure(state="disabled")


def main():
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        raise SystemExit("Windows LOCALAPPDATA required")
    path = Path(os.environ["LOCALAPPDATA"]) / "DevOrchestra" / "p1.sqlite3"
    root = tk.Tk()
    CommandCenter(root, ProjectionReader(Ledger(path)))
    root.mainloop()


if __name__ == "__main__":
    main()
