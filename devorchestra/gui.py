"""DevOrchestra Command Center. Read-only desktop viewer for Windows."""
from datetime import datetime, timezone
from concurrent.futures import Future
import json
import os
from pathlib import Path
import sqlite3
from threading import Thread
from time import monotonic, time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from .catalog import Catalog, new_project
from .lease import Blocked
from .instructions import InstructionStore, selected_source, source_ref
from .ledger import Ledger
from .projection import ProjectionReader
from .runtime import PROJECTS, Runner
from .setup import environment_status, validate_setup

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
    "context_id", "requested_model", "requested_reasoning", "role_budget", "input_contract_hash", "input_refs",
    "usage_kind", "estimated_tokens", "synthetic_cost_microunits", "tool_audit", "diff_ref", "handoff_ref",
    "scope_released", "budget", "global_budget", "project_budget",
    "development_id", "cycle", "selection", "selection_ref", "goal_hash", "guidance_hash",
    "selected_task", "manager_proposal", "provider_audit_fixture", "limits", "max_cycles", "max_retries",
    "cycles", "completed", "acceptance", "task_limits",
    "stream_role", "stream_receipt", "stream_settlement",
)


def color(state):
    if state in ("FAILED", "FAIL", "BLOCKED", "DENY"):
        return RED
    if state in ("NEED_APPROVAL", "RECOVERING", "PAUSED", "UNKNOWN"):
        return AMBER
    if state in ("COMPLETED", "PASS", "OK", "RECORDED OK"):
        return TEAL
    return CYAN


def audit_rows(view, run_id=None, role=None, *, include_stream=False):
    return [event for event in reversed(view["events"])
            if (run_id is None or event["run_id"] == run_id)
            and (role is None or event.get("role", event.get("request", {}).get("role")) == role
                 or (include_stream and event.get("stream_role") == role))]


def evidence_text(event):
    fields = {key: event[key] for key in EVIDENCE_FIELDS if key in event}
    return json.dumps(fields, ensure_ascii=False, indent=2)


def meter(used, limit):
    filled = min(8, int(8 * used / limit)) if limit else 0
    return "■" * filled + "□" * (8 - filled)


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
        self.controller = Runner(reader.ledger)
        self.shutdown_future = None
        self.shutdown_notice = None
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
        ttk.Button(projects, text="Register", command=lambda: self.edit_settings("new")).pack(fill="x")
        ttk.Button(projects, text="Project settings", command=lambda: self.edit_settings("edit")).pack(fill="x")
        ttk.Button(projects, text="Global limits", command=lambda: self.edit_settings("limits")).pack(fill="x")
        ttk.Button(projects, text="토큰 예비량", command=lambda: self.edit_settings("reserve")).pack(fill="x")
        ttk.Button(projects, text="초기 설정", command=self.setup_wizard).pack(fill="x")
        ttk.Button(projects, text="작업 지침", command=self.instruction_settings).pack(fill="x")
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
        controls = tk.Frame(safety, bg=PANEL)
        controls.pack(fill="x")
        self.shutdown_label = label(safety, fg=AMBER)
        self.shutdown_label.pack(fill="x")
        self.shutdown_buttons = {}
        for action, title in (("request", "종료 예약"), ("cancel", "예약 취소"),
                              ("finalize", "체크포인트 저장"), ("resume", "이어서 개발")):
            button = ttk.Button(controls, text=title, state="disabled",
                                command=lambda action=action: self.shutdown_command(action))
            button.pack(side="left")
            self.shutdown_buttons[action] = button

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
        root.protocol("WM_DELETE_WINDOW", self.close_requested)
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
        previous = self.view
        try:
            project_id = self.project_ids[self.project.current()]
            view = self.reader.read(project_id)
        except (Blocked, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            self.clear(f"{type(error).__name__}: {error}")
            return
        if (self.view is None or view["snapshot_version"] != self.view["snapshot_version"]
                or view["project_id"] != self.view["project_id"]
                or view["catalog_version"] != self.view["catalog_version"]):
            self.view = view
            self.project_ids = list(view["project_names"])
            self.project.configure(values=[f"{name} [{key}]" for key, name in view["project_names"].items()])
            self.project.current(self.project_ids.index(project_id))
            self.render()
        else:
            self.view = view
        seq, tail = view["snapshot_version"]
        if hasattr(self, "shutdown_buttons"):
            self.render_shutdown()
            shutdown = view["shutdown"]
            if (previous and shutdown["state"] == "SCHEDULED"
                    and shutdown.get("reason") in ("TOKEN_RESERVE_LOW", "TIME_LIMIT", "COST_LIMIT")
                    and previous["shutdown"].get("request_id") != shutdown["request_id"]):
                key = shutdown["request_id"]
                self.root.after(900, lambda: self.shutdown_command("finalize", expected=key))
        self.observation.configure(text=f"MOCK LEDGER  /  Seq {seq} · "
                                   f"{tail[:12] or 'empty'}  /  Verified {view['as_of_utc']}")

    def clear(self, reason):
        self.view = None
        if hasattr(self, "shutdown_buttons"):
            self.render_shutdown()
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
        development = view["global_development"]
        if development and not development["end"]:
            global_project = view["project_names"][development["start"]["project_id"]]
            if not development["current"] or state == "COMPLETED":
                state = "SESSION READY"
        self.global_badge.configure(text=f"GLOBAL / {state} · {global_project}", fg=color(state))
        self.project_states.configure(text="\n".join(
            f"{view['project_names'][key]} · {status}" for key, status in
            view["project_states"].items() if key == view["project_id"]))
        self.mission.configure(text=f"{view['project_name']}  /  {view['project_state']}",
                               fg=color(view["project_state"]))
        registration = view["registration"]
        configured = registration["data"] if registration else None
        goal = (f"{run['goal']}\nGen {run['generation']} · Run {run['run_id'][:10]}"
                if run else "No recorded mission")
        if configured:
            goal += (f"\nConfigured · {configured['stage']} · {configured['goal']}"
                     f"\nAcceptance · {configured['acceptance'][0]}"
                     f"\nDeclared Git SHA · {configured['git_sha'] or 'UNKNOWN'}"
                     f"\nDeclared block · {configured['block_reason'] or 'none'}")
        session = view["development"]
        if session:
            start, end = session["start"], session["end"]
            current_work = session["current"]
            reason = (end or (current_work[-1] if current_work else {})).get("reason", "")
            session_state = end["state"] if end else current_work[-1]["state"] if current_work else "READY"
            limits = start["limits"]
            deadline = datetime.fromtimestamp(start["deadline"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            selected = current_work[0]["selection"] if current_work else "Awaiting first task"
            goal += (f"\nSession {start['development_id'][:8]} · {session_state} {reason}"
                     f"\nCycle {session['cycles']}/{start['max_cycles']} · {session['completed']} verified · "
                     f"Rework {session['retries']}/{start['max_retries']}"
                     f"\n{meter(session['tokens'], limits['tokens'])} {session['usage_kind']} · "
                     f"{session['tokens']}/{limits['tokens']} tokens"
                     f"\n{meter(session['cost'], limits['cost_microunits'])} "
                     f"{session['cost']}/{limits['cost_microunits']} synthetic µcost"
                     f"\n{meter(session['recorded_wall_seconds'], limits['seconds'])} recorded wall "
                     f"{session['recorded_wall_seconds']:.1f}/{limits['seconds']}s · deadline {deadline}"
                     f"\nSelection · {selected} · Session control via CLI")
        self.goal.configure(text=goal, wraplength=350)
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
            preference = configured["roles"][role] if configured else None
            planned = f"\nRequested · {preference['model']} / {preference['reasoning']}" if preference else ""
            if start is None:
                status.configure(text="NO EXECUTION", fg=MUTED)
                detail.configure(text="Awaiting ledger evidence" + planned, wraplength=185)
                continue
            outcome = latest.get("result", "UNKNOWN")
            phase = start["phase"]
            if finish and finish["state"] == "BLOCKED":
                outcome = "BLOCKED"
            status.configure(text=f"{outcome} · {phase}", fg=color(outcome))
            duration = f"{finish['elapsed_seconds']:.3f}s" if finish else "duration unknown"
            detail.configure(text=f"{start['model']} · {duration}\n"
                                  f"Receipt #{latest['sequence']} · {start['run_id'][:8]}" + planned, wraplength=185)

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
        stream = view["latest_stream"]
        stream_status = ""
        if stream:
            receipt = stream["stream_receipt"]
            item = receipt.get("record")
            if item:
                stream_status = (f"Mock committed #{stream['sequence']} · {item['method']}\n"
                                 f"Event {item['event_id'][:10]} · provider usage UNKNOWN")
            else:
                stream_status = f"Mock stream #{stream['sequence']} · recovery pending"
        self.safety.configure(text="\n".join(filter(None, (
            live, f"Slot · {safety['lease']}", job_status, recovery_status, stream_status,
            f"Real execution {safety['real_execution']} · {safety['block_reason']}",
            f"Noninterference · {safety['noninterference']}",
            safety["isolation_mode"],
            f"Files {safety['files']} / Net {safety['network']} / Credentials {safety['credentials']}",
            f"Recorded baseline · {baseline['scope']} @ {baseline['git_sha'][:7]}",
            f"Approval {gate}" + (f" · {reason}" if reason else ""),
        ))))
        def budget_text(title, budget):
            return f"{title} · {budget['seconds']}s / {budget['tokens']} tokens / {budget['cost_microunits']} µcost"

        configured = budget_text("Global config", view["configured_global_budget"])
        tokens = view.get("token_budget")
        if tokens:
            available = tokens["available"] if tokens["available"] is not None else "UNKNOWN"
            configured += (f"\n{tokens['usage']} · 예비 {tokens['reserve']} · 예약 {tokens['active_reservation']}"
                           f" · 입장 가능 {available}\n계정 한도 UNKNOWN")
        if view["registration"]:
            configured += "\n" + budget_text("Project config", view["registration"]["data"]["budget"])
        if resources["elapsed"] is None:
            self.resources.configure(text=configured + "\nTime, tokens, cost · UNKNOWN")
            return
        self.resources.configure(text=configured + f"\nRole time {resources['elapsed']:.3f}s / "
                                 f"{resources['time_budget']}s run budget\n"
                                 f"Deadline {datetime.fromtimestamp(resources['deadline'], timezone.utc):%H:%M:%S} UTC\n"
                                 f"Synthetic tokens {resources['synthetic_tokens']} / {resources['token_limit']}\n"
                                 f"Model tokens · {resources['measured_tokens']}\n"
                                 f"Cost · {resources['cost']} / cap {resources['cost_limit']}")

    def render_shutdown(self):
        state = self.view["shutdown"]["state"] if self.view else "UNAVAILABLE"
        titles = {"RUNNING": "실행 입장 가능 · 합성 전용", "SCHEDULED": "종료 예약됨 · 신규 입장 차단",
                  "READY": "종료 가능 · 체크포인트 저장됨", "RECOVERY_REQUIRED": "복구 확인 필요",
                  "CANCELLED": "종료 예약 취소됨", "RESUMED": "재개 확인됨 · 자동 실행 없음",
                  "UNAVAILABLE": "종료 제어 BLOCKED"}
        problem = self.view["shutdown"].get("resume_problem") if self.view else None
        notice = self.shutdown_notice
        if self.view and state in ("SCHEDULED", "READY", "RECOVERY_REQUIRED"):
            record = self.view["shutdown"]
            reason = {"TOKEN_RESERVE_LOW": "토큰 잔여량 부족", "TIME_LIMIT": "작업 시간 부족",
                      "COST_LIMIT": "비용 예산 부족", "OWNERSHIP_UNKNOWN": "소유권·사용량 확인 필요",
                      "WAIT_LIMIT": "대기 상한 초과"}.get(record.get("reason"), "사용자 요청")
            titles[state] += " · " + reason
            if state == "READY":
                saved = record["checkpoint"]
                titles[state] += (f"\n원장 #{saved['sequence']} · {saved['current_phase'] or 'IDLE'}"
                                  f" · 확인 역할 {saved['last_completed_role'] or '없음'}"
                                  f" · 남은 토큰 {saved['tokens_remaining']}")
        if self.view and state in ("RUNNING", "RESUMED") and self.view.get("recovery_state") == "UNKNOWN":
            notice = "복구 확인 필요 · USAGE_UNVERIFIED"
        if state == "SCHEDULED" and time() >= self.view["shutdown"]["deadline"]:
            notice = "대기 상한 초과 · 복구 확인 필요"
        self.shutdown_label.configure(text=("복구 확인 필요 · " + problem) if problem else notice or titles[state])
        allowed = {"RUNNING": ("request",), "RESUMED": ("request",), "CANCELLED": ("request",),
                   "SCHEDULED": ("cancel", "finalize"), "READY": () if problem else ("resume",)}
        for action, button in self.shutdown_buttons.items():
            enabled = self.shutdown_future is None and action in allowed.get(state, ())
            button.configure(state="normal" if enabled else "disabled")

    def close_requested(self):
        if self.shutdown_future is not None or self.view is None:
            return
        state = self.view["shutdown"]["state"]
        self.shutdown_command("finalize" if state in ("SCHEDULED", "READY") else "request")

    def shutdown_command(self, action, *, expected=None, until=None):
        if self.view is None or self.shutdown_future is not None:
            return
        state = self.view["shutdown"]
        if expected is not None and (state.get("request_id") != expected or state["state"] != "SCHEDULED"):
            return
        active = self.view["global_run"]
        configured = self.view.get("setup") if active and active["project_id"] == self.view["project_id"] else None
        wait_seconds = (configured or {}).get("wait_seconds", 10)
        if until is None:
            until = monotonic() + max(0, state.get("deadline", time() + wait_seconds) - time())
        key = uuid4().hex if action == "request" else state.get("request_id")
        generation = active["generation"] if active else 0
        future = self.shutdown_future = Future()
        self.shutdown_notice = {"request": "예약 접수 중", "finalize": "체크포인트 저장 중",
                                "cancel": "예약 취소 처리 중", "resume": "체크포인트 검증 중"}[action]
        self.render_shutdown()

        def work():
            try:
                future.set_result(self.controller.shutdown(action, key, wait_seconds=wait_seconds,
                                                           expected_generation=generation))
            except Exception as error:
                future.set_exception(error)

        Thread(target=work, daemon=True).start()
        self.root.after(25, lambda: self.finish_shutdown(future, action, key, until))

    def finish_shutdown(self, future, action, key, until):
        if not future.done():
            self.root.after(25, lambda: self.finish_shutdown(future, action, key, until))
            return
        self.shutdown_future = None
        self.shutdown_notice = None
        try:
            event = future.result()
        except (Blocked, OSError, sqlite3.Error, ValueError) as error:
            if (action == "finalize" and isinstance(error, Blocked)
                    and "Execution slot busy" in str(error) and monotonic() < until):
                self.shutdown_notice = "예약됨 · 현재 역할 완료 대기"
                self.render_shutdown()
                self.root.after(200, lambda: self.shutdown_command(action, expected=key, until=until))
                return
            self.shutdown_notice = f"복구 또는 재확인 필요 · {type(error).__name__}"
            self.refresh()
            return
        self.refresh()
        if action == "request" and event["state"] == "SCHEDULED":
            self.root.after(900, lambda: self.shutdown_command("finalize", expected=event["request_id"]))
        elif (action == "finalize" and event["state"] == "READY" and self.view is not None
              and self.view["shutdown"]["state"] == "READY"
              and self.view["shutdown"]["request_id"] == event["request_id"]):
            self.root.destroy()

    def setup_wizard(self):
        wizard = tk.Toplevel(self.root)
        wizard.title("DevOrchestra · 초기 설정")
        wizard.configure(bg=PANEL, padx=12, pady=12)
        steps = ttk.Notebook(wizard)
        steps.pack(fill="both", expand=True)
        environment, paths, project = (tk.Frame(steps, bg=PANEL, padx=12, pady=12) for _ in range(3))
        for tab, title in zip((environment, paths, project), ("1. 환경", "2. 복제본", "3. 프로젝트")):
            steps.add(tab, text=title)
        facts = environment_status()
        label(environment, f"Windows x64 · {facts['windows_x64']}\n"
              f"기본 실행 방식 · {facts['backend_label']}\n"
              "일반 개발 · 공식 샌드박스와 승인 정책 · 완전 격리 보증 없음\n"
              "강화 격리 · 별도 A/B/C 실측 필요 · 실행 차단\n"
              f"Codex on PATH · {facts['codex_found']}\nCLI 서명·버전 / 격리 · NOT VERIFIED\n"
              f"실제 실행 {facts['live_execution']} · 인증정보를 입력하지 마세요.").pack(fill="x")
        label(paths, "승인할 복제본 경로 (등록만, 복사·실행 없음)").pack(fill="x")
        replica = ttk.Entry(paths, width=60)
        replica.pack(fill="x")
        label(paths, "종료 대기 상한(초, 1–60)").pack(fill="x")
        wait = ttk.Entry(paths)
        wait.insert(0, "10")
        wait.pack(fill="x")
        label(paths, f"원장 및 체크포인트 · {self.reader.ledger.path}", wraplength=520).pack(fill="x")
        label(project, "local_path에 원본 경로를 입력하세요. 도구·네트워크·Git은 차단으로 저장합니다.").pack(fill="x")
        editor = tk.Text(project, width=76, height=21, wrap="none")
        self.scrollable(project, editor)
        editor.insert("1.0", json.dumps(new_project("new-project", "New project", "Project goal"),
                                        indent=2, ensure_ascii=False))
        status = label(wizard, fg=AMBER)
        status.pack(fill="x")

        def save():
            try:
                raw = editor.get("1.0", "end")
                if len(raw) > 131_072:
                    raise ValueError("Configuration input too large")
                data = {"project": json.loads(raw), "replica": replica.get(), "wait_seconds": int(wait.get())}
                validate_setup(data, self.reader.ledger.path.parent)
                if not self.reader.ledger.path.exists():
                    self.controller.command("init")
                Catalog(self.reader.ledger).save_setup(data)
            except (Blocked, OSError, sqlite3.Error, ValueError) as error:
                status.configure(text=str(error))
                return
            wizard.destroy()
            self.refresh()

        ttk.Button(wizard, text="설정 저장 · 실제 실행 차단 유지", command=save,
                   state="normal" if facts["windows_x64"] else "disabled").pack(anchor="e")

    def instruction_settings(self):
        panel = tk.Toplevel(self.root)
        panel.title("DevOrchestra · 작업 지침")
        project_id = self.project_ids[self.project.current()]
        status = tk.StringVar(value="원본과 개선본은 이 PC에만 보관됩니다.")
        ttk.Label(panel, textvariable=status, wraplength=640).pack(fill="x")
        selected = ttk.Combobox(panel, state="readonly", width=42)
        selected.pack(fill="x")
        preview = tk.Text(panel, width=82, height=22, wrap="word", state="disabled")
        preview.pack(fill="both", expand=True)
        reviewed = None
        project_ref = None
        reviewed_replica = None

        def show(text):
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", text)
            preview.configure(state="disabled")

        def refresh(_event=None):
            nonlocal reviewed, project_ref, reviewed_replica
            reviewed = None
            project_ref = None
            reviewed_replica = None
            approve_button.configure(state="disabled")
            try:
                store = InstructionStore(self.reader.ledger)
                versions, _ = store.versions()
                active = store.active(project_id)
                setup = Catalog(self.reader.ledger).read()["setups"].get(project_id)
                selected.configure(values=versions)
                state = "등록된 복제본" if setup else "복제본 미등록"
                if active:
                    state += f"\n활성 {active['version']} · {active['sha256']}"
                    try:
                        if setup:
                            store.verify(active, Path(setup["replica"]))
                    except (Blocked, OSError):
                        state += "\n활성 지침 검증 실패 또는 승인 만료"
                if not versions:
                    status.set(state + "\n원본 미선택 · 백업 없음")
                    return
                version = selected.get()
                if version not in versions:
                    version = active["version"] if active else versions[-1]
                    selected.set(version)
                reviewed = store.compare(version)
                status.set(state + f"\n선택 후보 SHA-256 {reviewed['sha256']}"
                           + f"\n원본 백업 SHA-256 {reviewed['source_sha256']}"
                           + (" · 원본 변경 또는 확인 불가" if reviewed["source_changed"] else " · 원본 일치"))
                show("\n".join(reviewed["changes"]) + "\n\n" + reviewed["diff"])
                if setup:
                    try:
                        path, data = selected_source(Path(setup["replica"]))
                    except (Blocked, OSError):
                        status.set(status.get() + "\n프로젝트 지침 확인 실패")
                    else:
                        project_ref = source_ref(path, data)
                        reviewed_replica = Path(setup["replica"])
                        show(preview.get("1.0", "end-1c") + "\n\n프로젝트 지침\n"
                             + data.decode("utf-8-sig"))
                        approve_button.configure(state="normal")
            except (Blocked, OSError, ValueError, sqlite3.Error):
                status.set("작업 지침 상태를 검증할 수 없습니다.")
                show("")

        def backup():
            path = filedialog.askopenfilename(parent=panel, title="설정에서 확인한 전역 AGENTS 파일 선택")
            if not path or not messagebox.askyesno(
                "원본 로컬 백업", "선택한 전역 지침의 원본 바이트를 이 PC에 백업하시겠습니까?", parent=panel,
            ):
                return
            try:
                selected.set(InstructionStore(self.reader.ledger).backup(Path(path)))
                refresh()
            except (Blocked, OSError, ValueError, sqlite3.Error):
                status.set("원본 백업 또는 무결성 확인에 실패했습니다.")

        def approve():
            if not reviewed or not messagebox.askyesno(
                "기준 지침 승인", "표시된 개선본과 프로젝트 지침을 검토했으며 충돌이 없습니까?\n"
                "선택 버전을 다음 작업에 24시간 동안 승인합니다.", parent=panel,
            ):
                return
            try:
                setup = Catalog(self.reader.ledger).read()["setups"][project_id]
                if Path(setup["replica"]) != reviewed_replica:
                    raise Blocked("Reviewed replica changed")
                InstructionStore(self.reader.ledger).approve(
                    reviewed["version"], reviewed["sha256"], project_id, reviewed_replica,
                    project_ref, time() + 24 * 60 * 60,
                )
                refresh()
            except (Blocked, KeyError, OSError, ValueError, sqlite3.Error):
                status.set("기준 지침을 승인하지 못했습니다.")

        def full_preview():
            if reviewed:
                try:
                    _backup, _original, improved = InstructionStore(self.reader.ledger).preview(reviewed["version"])
                    show(improved.decode("utf-8-sig"))
                except (Blocked, OSError, ValueError, sqlite3.Error):
                    status.set("개선본 미리보기를 검증할 수 없습니다.")

        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        ttk.Button(controls, text="원본 선택 및 백업", command=backup).pack(side="left")
        ttk.Button(controls, text="상태 다시 확인", command=refresh).pack(side="left")
        ttk.Button(controls, text="개선본 전체 보기", command=full_preview).pack(side="left")
        approve_button = ttk.Button(controls, text="선택 버전 승인 / 복원", command=approve)
        approve_button.pack(side="left")
        selected.bind("<<ComboboxSelected>>", refresh)
        refresh()

    def edit_settings(self, mode):
        if self.view is None:
            return
        project_id = self.view["project_id"]
        registration = self.view["registration"]
        if mode == "edit" and registration is None:
            self.observation.configure(text="Built-in fixture or missing registration. Use Register for a new project.")
            return
        if mode == "reserve":
            value, revision = self.view["token_policy"], self.view["catalog_version"]
        elif mode == "limits":
            value, revision = self.view["configured_global_budget"], self.view["catalog_version"]
        elif mode == "edit":
            value, revision = registration["data"], registration["revision"]
        else:
            value, revision = new_project("new-project", "New project", "Synthetic workflow goal"), 0
        editor = tk.Toplevel(self.root)
        editor.title("Owner configuration · registration grants no execution permission")
        editor.configure(bg=PANEL, padx=12, pady=12)
        body = tk.Frame(editor, bg=PANEL)
        body.pack(fill="both", expand=True)
        document = tk.Text(body, height=25, width=72, bg=BG, fg=INK, insertbackground=INK,
                           font=("Consolas", 10), wrap="none")
        self.scrollable(body, document)
        document.insert("1.0", json.dumps(value, indent=2, ensure_ascii=False))
        status = label(editor, fg=AMBER)
        status.pack(fill="x")

        def save():
            try:
                raw = document.get("1.0", "end")
                if len(raw) > 131_072:
                    raise ValueError("Configuration input too large")
                data = json.loads(raw)
                catalog = Catalog(self.reader.ledger)
                if mode == "reserve":
                    catalog.save_token_reserve(data, revision)
                elif mode == "limits":
                    catalog.save_limits(data, revision)
                else:
                    if mode == "edit" and (not isinstance(data, dict) or data.get("project_id") != project_id):
                        raise ValueError("Project ID cannot be changed")
                    catalog.save_project(data, revision)
            except (Blocked, ValueError, OSError, sqlite3.Error) as error:
                status.configure(text=str(error))
                return
            editor.destroy()
            self.refresh()

        ttk.Button(editor, text="Save configuration", command=save).pack(anchor="e")

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
                              None if role == "All roles" else role, include_stream=True)
        self.audit_count.configure(text=f"{len(rows)} verified receipts")
        for event in rows:
            key = str(event["sequence"])
            self.event_lookup[key] = event
            actor = event.get("role", event.get("stream_role", event.get("request", {}).get("role", "Runner")))
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
    center = CommandCenter(root, ProjectionReader(Ledger(path)))
    if not path.exists():
        root.after_idle(center.setup_wizard)
    root.mainloop()


if __name__ == "__main__":
    main()
