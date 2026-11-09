"""DevOrchestra Command Center. Read-only desktop viewer for Windows."""
from datetime import datetime, timezone
from concurrent.futures import Future
import json
from pathlib import Path
import sqlite3
from threading import Thread
from time import monotonic, time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from .catalog import Catalog, REQUIREMENT_ROLES, new_project
from .build_info import build_label
from .default_instructions import defaults_text
from .lease import Blocked
from .instructions import InstructionStore, selected_source, source_ref
from .ledger import Ledger, check_path
from .projection import ProjectionReader
from .questions import AUTHORIZATION_TYPES
from .runtime import PROJECTS, Runner
from .setup import create_work_copy, validate_setup
from .supervised import MAX_HANDOFF_BYTES, latest_handoff

BG, PANEL, TILE, EDGE = "#09121f", "#111f30", "#172a40", "#395575"
INK, MUTED, CYAN = "#f1f6ff", "#b9cde4", "#82d8ff"
TEAL, AMBER, RED = "#78efca", "#ffd082", "#ff9da9"
ROLE_MARKS = {"Manager": "01 / CMD", "Developer": "02 / DEV",
              "Reviewer": "03 / REV", "Verifier": "04 / VFY"}
POLL_MS = 1500


def storage_message(storage):
    if storage["status"] == "NOT_INITIALIZED":
        return "처음 실행입니다. ‘초기 설정’에서 로컬 저장소를 준비하세요."
    return {
        "ACCESS_DENIED": "저장 폴더에 접근할 수 없습니다. 이 폴더의 읽기·쓰기 권한을 확인하세요.",
        "CORRUPT": "기존 원장 검증에 실패했습니다. 백업과 원장 상태를 확인하세요.",
        "INCOMPLETE": "기존 저장소의 일부가 누락되었습니다. 남은 데이터와 백업을 확인하세요.",
        "PATH_CONFLICT": "저장 경로가 파일 또는 링크와 충돌합니다. EXE 옆 .ai-local 위치를 확인하세요.",
    }.get(storage.get("reason"), "로컬 저장소를 열 수 없습니다.")

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
    if state in ("NEED_APPROVAL", "WAITING_FOR_USER", "RECOVERING", "PAUSED", "UNKNOWN"):
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


def label(parent, text="", size=10, fg=INK, bg=PANEL, bold=False, **options):
    return tk.Label(parent, text=text, bg=bg, fg=fg, anchor="w", justify="left",
                    font=("Segoe UI", size, "bold" if bold else "normal"), **options)


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
        self.question_window = None
        root.title(f"DevOrchestra | Command Center · {build_label()}")
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
        ttk.Button(projects, text="프로젝트 등록", command=self.register_project).pack(fill="x")
        ttk.Button(projects, text="요구사항 문서", command=self.requirement_settings).pack(fill="x")
        self.development_button = ttk.Button(projects, text="자동 개발 시작 · 확인 중", state="disabled")
        self.development_button.pack(fill="x")
        ttk.Button(projects, text="감독형 Codex 작업", command=self.supervised_work).pack(fill="x")
        ttk.Button(projects, text="초기 설정", command=self.setup_wizard).pack(fill="x")
        ttk.Button(projects, text="작업 지침", command=self.instruction_settings).pack(fill="x")
        advanced = tk.Frame(projects, bg=PANEL)
        for mode, title in (("edit", "프로젝트 설정"), ("limits", "공용 예산"), ("reserve", "토큰 예비량")):
            ttk.Button(advanced, text=title, command=lambda mode=mode: self.edit_settings(mode)).pack(fill="x")
        self.project_states = label(projects, fg=MUTED)
        self.project_states.pack(fill="x")

        mission = section(command, "02  /  진행 상황과 질문")
        mission.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.mission = label(mission, size=13, bold=True)
        self.mission.pack(fill="x", pady=(4, 0))
        self.goal = label(mission, fg=MUTED)
        self.goal.pack(fill="x")
        self.gates = label(mission)
        self.gates.pack(fill="x", pady=(3, 0))
        self.question_button = ttk.Button(mission, text="사용자 응답 대기 없음", state="disabled",
                                          command=self.answer_question)
        self.question_button.pack(fill="x", pady=(4, 0))

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

        audit = self.build_audit(root)
        details_visible = False

        def toggle_details():
            nonlocal details_visible
            details_visible = not details_visible
            for frame in (safety, operations, audit):
                (frame.grid if details_visible else frame.grid_remove)()
            if details_visible:
                self.gates.pack(fill="x", before=self.question_button)
                advanced.pack(fill="x", before=self.project_states)
            else:
                self.gates.pack_forget()
                advanced.pack_forget()
            root.grid_rowconfigure(3, weight=int(details_visible))

        details_visible = True
        toggle_details()
        ttk.Button(header, text="고급 정보", command=toggle_details).pack(side="right", padx=8)
        root.bind("<F5>", lambda _: self.refresh())
        root.protocol("WM_DELETE_WINDOW", self.close_requested)
        self.refresh()
        root.after(POLL_MS, self.poll)

    def theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=TILE, foreground=INK, fieldbackground=TILE,
                        bordercolor=EDGE, font=("Segoe UI", 10))
        style.configure("TButton", padding=(8, 5))
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
        return audit

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
        if previous is None:
            storage = self.reader.ledger.storage_status()
            if storage["status"] != "READY":
                self.clear(storage_message(storage), first_run=storage["status"] == "NOT_INITIALIZED")
                return
        try:
            project_id = self.project_ids[self.project.current()]
            view = self.reader.read(project_id)
        except (Blocked, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            self.clear(storage_message(self.reader.ledger.storage_status()))
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

    def clear(self, reason, *, first_run=False):
        self.view = None
        if hasattr(self, "shutdown_buttons"):
            self.render_shutdown()
        self.global_badge.configure(text="처음 시작" if first_run else "저장소 확인 필요",
                                    fg=CYAN if first_run else RED)
        self.observation.configure(text=reason)
        self.mission.configure(text="첫 실행 안내" if first_run else "저장소 확인 필요",
                               fg=CYAN if first_run else RED)
        self.goal.configure(text=reason, wraplength=350)
        self.project_states.configure(text="UNKNOWN")
        self.development_button.configure(text="자동 개발 시작 · 확인 불가", state="disabled")
        if hasattr(self, "question_button"):
            self.question_button.configure(state="disabled")
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
        if hasattr(self, "question_button"):
            pending = view.get("user_question")
            self.question_button.configure(
                state="normal" if pending else "disabled",
                text=f"사용자 응답 대기 · {pending['question']['type']}" if pending else "사용자 응답 대기 없음",
            )
        run, current = view["run"], view["global_run"]
        state = view["global_state"]
        global_project = current["project"]["name"] if current else "No run"
        development = view["global_development"]
        if development and not development["end"]:
            global_project = view["project_names"][development["start"]["project_id"]]
            if not development["current"] or state == "COMPLETED":
                state = "SESSION READY"
        self.global_badge.configure(text=f"GLOBAL / {state} · {global_project}", fg=color(state))
        workflow = view["workflow"]
        self.development_button.configure(text=f"실제 자동 개발 · {workflow['development_admission']}",
                                          state="disabled")
        next_action = {
            "ANSWER_QUESTION": "사용자 질문에 답변",
            "REGISTER_PROJECT": "프로젝트 폴더 등록",
            "REGISTER_REQUIREMENTS": "요구사항 문서 등록",
            "REVIEW_NEXT_REQUIREMENTS": "다음 주기에 반영할 요구사항 확인",
            "OPEN_SUPERVISED_HANDOFF": "감독형 Codex 작업 열기",
        }[workflow["next_action"]]
        manager_status = {
            "NO_RECORDED_ATTEMPT": "실제 요청 기록 없음",
            "INTENT_UNRESOLVED": "실행 의도 기록 · 결과 미확정",
            "THREAD_UNRESOLVED": "스레드 요청 결과 미확정",
            "TURN_UNRESOLVED": "모델 턴 요청 결과 미확정",
            "RESULT_RECORDED": "Manager 결과 기록됨",
        }[workflow["manager_status"]]
        cycle_status = workflow["latest_cycle_state"]
        cycle_label = (f"\n최근 작업 · {cycle_status} ({workflow['latest_cycle_evidence']})"
                       if cycle_status else "")
        self.project_states.configure(text="\n".join(
            f"{view['project_names'][key]} · {status}" for key, status in
            view["project_states"].items() if key == view["project_id"])
            + f"\n요구사항 {workflow['requirements_count']}개"
              f" · 현재 기준본 {workflow['requirements_baseline_count']}개"
              f" · 기준 이후 등록 {workflow['pending_requirements_count']}개"
              f"{cycle_label}\nManager · {manager_status}"
              f"\n다음 단계 · {next_action}\n사용자 테스트 · {workflow['user_test_status']}"
              + (" · 실제 검증 영수증 필요" if workflow["user_test_status"] == "NOT_AVAILABLE" else ""))
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
            progress = session["progress"]
            latest_progress = progress["latest"]
            current_work = session["current"]
            reason = (end or (current_work[-1] if current_work else {})).get("reason", "")
            session_state = end["state"] if end else current_work[-1]["state"] if current_work else "READY"
            limits = start["limits"]
            deadline = datetime.fromtimestamp(start["deadline"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            selected = current_work[0]["selection"] if current_work else "Awaiting first task"
            progress_text = (f"{latest_progress['before']} → {latest_progress['after']}"
                             f" · 구현 {'신규' if latest_progress['implementation_advanced'] else '동일'}"
                             f" · 검증 {'신규' if latest_progress['verification_advanced'] else '동일'}"
                             if latest_progress else "결과 대기")
            goal += (f"\nSession {start['development_id'][:8]} · {session_state} {reason}"
                     f"\n목표 · {start['goal']}"
                     f"\n합성 진척 · {progress_text}"
                     f"\n연속 무진척 {progress['consecutive_no_progress']}회 · {progress['decision']}"
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

    def answer_question(self):
        pending = self.view.get("user_question") if self.view else None
        if not pending:
            return
        if self.question_window is not None and self.question_window.winfo_exists():
            return
        question = pending["question"]
        permission = question["type"] in AUTHORIZATION_TYPES
        window = self.question_window = tk.Toplevel(self.root)
        window.title("권한 승인 요청" if permission else "사용자 판단 요청")
        window.configure(bg=PANEL, padx=14, pady=12)
        window.transient(self.root)
        label(window, f"{pending['project_id']} · {question['type']}", bold=True).pack(fill="x")
        scope = question["scope"]
        for heading, value in (
            ("질문", question["prompt"]), ("이유", question["reason"]), ("영향 범위", question["impact"]),
            ("권장", question["recommendation"]), ("거부 시", question["decline"]),
            ("유효 기한", datetime.fromtimestamp(question["expires"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")),
            ("권한 범위", f"{scope['capability']} · {scope['target']}" if scope else "권한 변경 없음"),
        ):
            item = label(window, f"{heading} · {value}")
            item.configure(wraplength=620)
            item.pack(fill="x", pady=3)
        answer = ttk.Entry(window, width=65)
        if not permission and question["type"] != "BLOCKED":
            label(window, "답변은 로컬 원장에 남습니다. 인증정보는 입력하지 마세요.", fg=MUTED).pack(fill="x")
            answer.pack(fill="x", pady=5)
        status = label(window, fg=AMBER)
        status.pack(fill="x")
        buttons = []

        def submit(decision):
            value = answer.get() if decision == "ANSWER" else None
            for button in buttons:
                button.configure(state="disabled")
            status.configure(text="응답 기록 중")
            future = Future()

            def work():
                try:
                    future.set_result(self.controller.respond_question(
                        question["id"], pending["run_id"], pending["generation"], decision, value,
                    ))
                except Exception as error:
                    future.set_exception(error)

            def check():
                if not window.winfo_exists():
                    return
                if not future.done():
                    window.after(80, check)
                    return
                self.refresh()
                if future.exception() is not None:
                    status.configure(text="응답 또는 이어가기 차단 · 최신 원장 상태를 확인하세요")
                    for button in buttons:
                        button.configure(state="normal")
                else:
                    window.destroy()

            Thread(target=work, daemon=True).start()
            window.after(80, check)

        controls = tk.Frame(window, bg=PANEL)
        controls.pack(fill="x")
        for text, decision in (
            *((("승인 기록", "APPROVE"),) if permission else
              (("답변 제출", "ANSWER"),) if question["type"] != "BLOCKED" else ()),
            ("거부 / 닫기", "DECLINE"),
        ):
            button = ttk.Button(controls, text=text, command=lambda decision=decision: submit(decision))
            button.pack(side="left", padx=3)
            buttons.append(button)

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
        if self.shutdown_future is not None:
            return
        if self.view is None:
            self.root.destroy()
            return
        state = self.view["shutdown"]["state"]
        if (state != "SCHEDULED" and self.view.get("recovery_state") != "UNKNOWN"
                and (not self.view.get("global_run") or self.view.get("global_state") in
                     ("IDLE", "COMPLETED", "FAILED", "CANCELLED", "BLOCKED", "WAITING_FOR_USER", "SESSION READY"))):
            self.root.destroy()
            return
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
        label(wizard, "저장소 준비 → 프로젝트 등록 → 요구사항 추가 → 감독형 Codex 작업",
              size=12, bold=True, wraplength=520).pack(fill="x", pady=6)
        label(wizard, "기본 작업 지침은 포함되어 있습니다. 개인 지침은 프로젝트 등록 후 연결할 수 있습니다.",
              wraplength=520).pack(fill="x", pady=6)
        storage = self.reader.ledger.storage_status()
        status = label(wizard, "저장소가 준비됐습니다." if storage["status"] == "READY"
                       else storage_message(storage), fg=AMBER, wraplength=520)
        status.pack(fill="x")

        def save():
            try:
                self.controller.command("init")
            except (Blocked, OSError, sqlite3.Error, ValueError) as error:
                current = self.reader.ledger.storage_status()
                if isinstance(error, PermissionError):
                    current = {"status": "BLOCKED", "reason": "ACCESS_DENIED"}
                status.configure(text="저장소 준비 실패 · " + storage_message(current))
                return
            wizard.destroy()
            self.refresh()
            if self.view and not any(key not in PROJECTS for key in self.view["project_names"]):
                self.register_project()

        ttk.Button(wizard, text="저장소 준비", command=save,
                   state="normal" if storage["status"] != "BLOCKED"
                   else "disabled").pack(anchor="e")

    def register_project(self):
        if self.view is None:
            self.setup_wizard()
            return
        panel = tk.Toplevel(self.root)
        panel.title("프로젝트 등록")
        panel.configure(padx=16, pady=12)
        fields = {}
        for title, key in (("프로젝트 이름", "name"), ("원본 프로젝트 폴더", "original"),
                           ("목표와 설명", "goal"), ("완료 기준", "acceptance"),
                           ("개발용 작업 폴더 (선택)", "replica")):
            ttk.Label(panel, text=title).pack(anchor="w")
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=(2, 8))
            value = fields[key] = ttk.Entry(row, width=55)
            value.pack(side="left", fill="x", expand=True)
            if key in ("original", "replica"):
                def choose(field=value):
                    folder = filedialog.askdirectory(parent=panel, mustexist=True)
                    if folder:
                        field.delete(0, "end")
                        field.insert(0, str(Path(folder)))
                ttk.Button(row, text="폴더 선택", command=choose).pack(side="right", padx=(6, 0))
        ttk.Label(panel, text="개발용 작업 폴더는 원본을 보호하기 위한 별도 작업 사본입니다.\n"
                  "이미 준비한 폴더를 선택하거나 Developer 지시를 만들 때 지정하세요.").pack(anchor="w")
        copy_source = tk.BooleanVar(panel, value=False)
        ttk.Checkbutton(panel, text="지정한 새 작업 폴더에 원본 복사 (등록 시 별도 확인)",
                        variable=copy_source).pack(anchor="w")
        ttk.Label(panel, text="새 폴더는 전체 경로를 입력하세요. Git·로컬 앱 데이터와 알려진 비밀 파일은 제외합니다.",
                  wraplength=525).pack(anchor="w")
        ttk.Label(panel, text="개발 모드 · 감독형 Codex  |  자동 실행은 차단됨").pack(anchor="w", pady=6)
        add_docs = tk.BooleanVar(panel, value=True)
        ttk.Checkbutton(panel, text="등록 후 요구사항 문서 추가", variable=add_docs).pack(anchor="w")
        status = ttk.Label(panel, wraplength=530)
        status.pack(fill="x")

        def save():
            values = {key: widget.get().strip() for key, widget in fields.items()}
            try:
                if not all(values[key] for key in ("name", "goal", "original", "acceptance")):
                    raise ValueError("이름, 원본 폴더, 목표, 완료 기준을 입력하세요.")
                for key in ("original", "replica"):
                    if values[key]:
                        path = Path(values[key])
                        check_path(path)
                        if not path.is_dir() and not (key == "replica" and copy_source.get()):
                            raise ValueError("존재하는 폴더를 선택하세요.")
                identity = "project-" + uuid4().hex[:12]
                data = new_project(identity, values["name"], values["goal"])
                data.update(local_path=str(Path(values["original"])), acceptance=[values["acceptance"]])
                catalog = Catalog(self.reader.ledger)
                if values["replica"]:
                    setup = {"project": data, "replica": str(Path(values["replica"])), "wait_seconds": 10}
                    validate_setup(setup, self.reader.ledger.state_boundary)
                    if copy_source.get():
                        if not messagebox.askyesno("개발용 작업 사본 만들기",
                                                   "표시된 원본 파일을 지정한 새 폴더에 복사하시겠습니까? "
                                                   "복사 전에 프로젝트에 비공개 파일이 포함됐는지 확인하세요.",
                                                   parent=panel):
                            return
                        create_work_copy(Path(values["original"]), Path(values["replica"]))
                    catalog.save_setup(setup)
                else:
                    catalog.save_project(data)
            except (Blocked, OSError, ValueError, sqlite3.Error) as error:
                status.configure(text=str(error) if isinstance(error, (Blocked, ValueError)) else
                                 "폴더 권한과 로컬 저장소 상태를 확인하세요.")
                return
            open_docs = add_docs.get()
            panel.destroy()
            self.refresh()
            if identity in self.project_ids:
                self.project.current(self.project_ids.index(identity))
                self.select_project(None)
                if open_docs:
                    self.requirement_settings()

        ttk.Button(panel, text="프로젝트 등록", command=save).pack(anchor="e")

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
                    state += f"\n개인 기준 지침 연결 · {active['version']}"
                    try:
                        if setup:
                            store.verify(active, Path(setup["replica"]))
                    except (Blocked, OSError):
                        state += "\n활성 지침 검증 실패 또는 승인 만료"
                if not versions:
                    status.set("기본 지침 사용 중 · 개인 지침 미연결\n" + state)
                    show(defaults_text())
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

        def local_roles():
            if not self.view or not self.view["registration"]:
                status.set("역할 초안을 연결하려면 프로젝트를 등록하세요.")
                return
            try:
                store = InstructionStore(self.reader.ledger)
                drafts = store.role_drafts()
                if not drafts:
                    status.set("이 저장소에 연결할 로컬 역할 초안이 없습니다.")
                    return
                show("\n\n".join(f"{role}\n{text}" for role, text in drafts.items()))
                panel.update_idletasks()
                if messagebox.askyesno("역할 초안 연결", "검토한 로컬 역할 초안을 이 프로젝트의 감독형 지시에 포함할까요?",
                                       parent=panel):
                    store.approve_role_drafts(project_id, drafts)
                    status.set("역할 초안 연결됨 · 감독형 지시에만 적용")
            except (Blocked, OSError, ValueError, sqlite3.Error):
                status.set("역할 초안을 연결할 수 없습니다.")

        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        ttk.Button(controls, text="기본 지침 보기", command=lambda: show(defaults_text())).pack(side="left")
        ttk.Button(controls, text="로컬 역할 초안 연결", command=local_roles).pack(side="left")
        ttk.Button(controls, text="개인 지침 선택 및 백업", command=backup).pack(side="left")
        ttk.Button(controls, text="상태 다시 확인", command=refresh).pack(side="left")
        ttk.Button(controls, text="개선본 전체 보기", command=full_preview).pack(side="left")
        approve_button = ttk.Button(controls, text="선택 버전 승인 / 복원", command=approve)
        approve_button.pack(side="left")
        selected.bind("<<ComboboxSelected>>", refresh)
        refresh()

    def supervised_work(self):
        if not self.view or not self.view["registration"]:
            self.observation.configure(text="프로젝트를 먼저 등록하세요.")
            return
        project_id = self.view["project_id"]
        panel = tk.Toplevel(self.root)
        panel.title(f"감독형 Codex · {project_id}")
        ttk.Label(panel, text="지시를 직접 전달하고 결과 JSON을 가져오세요. 자동 모델 호출은 차단 상태입니다.",
                  wraplength=660).pack(fill="x")
        status = ttk.Label(panel, wraplength=660)
        status.pack(fill="x")
        prompt = tk.Text(panel, height=11, width=82, wrap="word", state="disabled")
        prompt.pack(fill="both", expand=True)
        ttk.Label(panel, text="결과 JSON · 개인 요구사항과 응답은 이 창에서만 검토하세요.").pack(fill="x")
        response = tk.Text(panel, height=9, width=82, wrap="word")
        response.pack(fill="both", expand=True)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        shown = self.view["supervised_handoff"]
        ttk.Label(panel, text="Manager가 제안할 수 있는 상대 파일 경로 · 쉼표로 구분").pack(fill="x", before=buttons)
        path_entry = ttk.Entry(panel, width=82)
        path_entry.pack(fill="x", before=buttons)
        path_entry.insert(0, ", ".join(self.view["registration"]["data"]["requested_scope"]["files"]))

        def describe():
            if not shown:
                return "새 Manager 작업을 시작할 수 있습니다."
            if shown["kind"] == "supervised_requested":
                return ("만료됨 · 새 작업을 시작하세요." if time() >= shown["expires"]
                        else "Manager 결과 대기 · DevOrchestra가 외부 실행을 관측하지 않습니다.")
            if shown["kind"] == "supervised_imported":
                return ("사용자 등록 계획 · 외부 실행 미검증 · Developer 지시 초안 준비 가능"
                        if shown["disposition"] == "PROPOSED"
                        else "사용자 질문 · Command Center에서 답변한 뒤 새 Manager 작업을 시작하세요.")
            if shown["kind"] == "supervised_evidence":
                artifacts = ", ".join(shown["artifacts"])
                return (f"사용자 제출 종료 코드 {shown['exit_codes']} · 산출물 해시 확인 · 실행 미검증\n"
                        f"{shown['replica']} · {artifacts}\n사용자 테스트 준비 BLOCKED")
            stages = {
                "supervised_developer": "Developer 결과 대기 · 복제본 기준을 기록했습니다.",
                "supervised_reviewer": "변경 파일 해시 확인 · Reviewer 결과 대기",
                "supervised_reviewed": "Reviewer 승인 등록 · Verifier 빌드 계획 입력",
                "supervised_review_blocked": "Reviewer 재작업 또는 차단 · 새 Manager 계획 필요",
                "supervised_verifier": "Verifier 결과 대기 · 명령은 사용자가 승인한 외부 환경에서만 실행",
            }
            if shown["kind"] in stages:
                return stages[shown["kind"]]
            return "작업 취소됨"

        def show(text):
            prompt.configure(state="normal")
            prompt.delete("1.0", "end")
            if text:
                prompt.insert("1.0", text)
            prompt.configure(state="disabled")

        def apply(action, raw=None, **options):
            nonlocal shown
            try:
                result = self.controller.supervised_handoff(
                    action, project_id, raw=raw, expected_request=shown["request_id"] if shown else None, **options,
                )
                shown = latest_handoff(self.reader.ledger.read(), project_id)
                if action in ("start", "prompt", "developer", "developer_submit", "verifier"):
                    show(result)
                elif action == "cancel":
                    show(None)
                status.configure(text=describe())
                self.refresh()
            except (Blocked, OSError, sqlite3.Error, ValueError) as error:
                status.configure(text=str(error) if isinstance(error, Blocked) else "입력 또는 로컬 상태를 확인하세요.")

        def import_file():
            choice = filedialog.askopenfilename(parent=panel, filetypes=[("JSON", "*.json")])
            if not choice:
                return
            try:
                path = Path(choice)
                check_path(path)
                if not path.is_file():
                    raise ValueError
                with path.open("rb") as stream:
                    raw = stream.read(MAX_HANDOFF_BYTES + 1)
                if len(raw) > MAX_HANDOFF_BYTES:
                    raise ValueError
                text = raw.decode("utf-8-sig")
                response.delete("1.0", "end")
                response.insert("1.0", text)
                import_result(raw)
            except (OSError, ValueError, Blocked):
                status.configure(text="읽을 수 있는 로컬 UTF-8 JSON 파일을 선택하세요.")

        def copy():
            text = prompt.get("1.0", "end-1c")
            if text:
                panel.clipboard_clear()
                panel.clipboard_append(text)
                status.configure(text="복사했습니다. 사용자가 대상 Codex에 직접 붙여넣으세요.")

        def pasted():
            return response.get("1.0", "end-1c").encode("utf-8")

        def import_result(raw):
            action = {"supervised_developer": "developer_submit", "supervised_reviewer": "reviewer_submit",
                      "supervised_verifier": "verifier_submit"}.get(shown["kind"] if shown else None, "submit")
            apply(action, raw)

        def verifier():
            template = {"commands": ["<승인할 빌드 또는 테스트 명령>"], "output_folder": "dist",
                        "artifacts": ["dist/app.exe"], "type": "EXE", "environment": "<도구 버전과 환경>"}
            if not pasted().strip() or (shown and shown["kind"] == "supervised_reviewed"
                                        and '"request_id"' in response.get("1.0", "end-1c")):
                response.delete("1.0", "end")
                response.insert("1.0", json.dumps(template, ensure_ascii=False, indent=2))
                status.configure(text="빌드 계획 JSON을 편집하고 같은 버튼을 다시 누르세요. 비밀은 입력하지 마세요.")
                return
            if b"<" not in pasted():
                apply("verifier", pasted())
            else:
                status.configure(text="빌드 명령과 실행 환경 예시를 실제 값으로 바꾸세요.")

        def developer():
            try:
                setup = Catalog(self.reader.ledger).read()["setups"].get(project_id)
            except (Blocked, OSError, sqlite3.Error, ValueError):
                status.configure(text="복제본 설정을 확인할 수 없습니다.")
                return
            replica = None if setup else filedialog.askdirectory(
                parent=panel, title="원본과 별개인 작업 복제본 선택", mustexist=True,
            )
            if setup or replica:
                apply("developer", pasted(), replica_path=replica)

        for title, command in (
            ("Manager 시작", lambda: apply("start", paths=[path.strip() for path in path_entry.get().split(",")
                                                          if path.strip()])),
            ("지시 다시 열기", lambda: apply("prompt")),
            ("지시 복사", copy), ("대기 취소", lambda: apply("cancel")),
        ):
            ttk.Button(buttons, text=title, command=command).pack(side="left")
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        for title, command in (
            ("붙여넣은 결과 확인", lambda: import_result(pasted())), ("JSON 파일 가져오기", import_file),
            ("Developer 지시 준비", developer), ("Verifier 빌드 계획", verifier),
        ):
            ttk.Button(actions, text=title, command=command).pack(side="left")
        status.configure(text=describe())

    def requirement_settings(self):
        if self.view is None or not self.view["registration"]:
            self.observation.configure(text="프로젝트를 먼저 등록하세요.")
            return
        project_id = self.view["project_id"]
        panel = tk.Toplevel(self.root)
        panel.title("DevOrchestra · 요구사항 문서")
        catalog = Catalog(self.reader.ledger)
        table = tk.Listbox(panel, width=84, height=12)
        table.pack(fill="both", expand=True)
        role = ttk.Combobox(panel, values=REQUIREMENT_ROLES, state="readonly")
        role.set("FEATURE")
        role.pack(fill="x")
        status = ttk.Label(panel, text="관련 항목을 선택한 뒤 문서를 추가하면 두 기록을 연결합니다.")
        status.pack(fill="x")
        history = []

        def refresh():
            history[:] = catalog.read().get("requirements", {}).get(project_id, ())
            table.delete(0, "end")
            for item in reversed(history):
                relation = f" · 관련 {item['related'][:8]}" if item["related"] else ""
                table.insert("end", f"{item['path']} · {item['role']} · v{item['version']}"
                                    f" · {item['sha256'][:12]}{relation}")

        def register():
            try:
                record = catalog.read()["projects"][project_id]
                folder = record["data"]["local_path"]
                if not folder:
                    raise Blocked("프로젝트 원본 폴더를 먼저 지정하세요.")
                choice = filedialog.askopenfilename(parent=panel, initialdir=folder,
                    title="프로젝트 안의 요구사항 문서 선택", filetypes=[("TXT / Markdown", "*.txt *.md")])
                if not choice:
                    return
                relative = Path(choice).relative_to(Path(folder)).as_posix()
                selected = table.curselection()
                related = history[-1 - selected[0]]["id"] if selected else None
                item = catalog.register_requirement(project_id, relative, role.get(), related=related)
                refresh()
                status.configure(text=f"등록 확인 · v{item['version']} · {item['sha256']}")
                self.refresh()
            except (Blocked, ValueError, OSError, sqlite3.Error, KeyError):
                status.configure(text="등록 실패 · 경로, 파일 형식 또는 원장 상태를 확인하세요.")

        ttk.Button(panel, text="문서 선택 및 등록", command=register).pack(anchor="e")
        ttk.Button(panel, text="관련 항목 선택 해제", command=lambda: table.selection_clear(0, "end")).pack(anchor="e")
        try:
            refresh()
        except (Blocked, ValueError, OSError, sqlite3.Error):
            status.configure(text="요구사항 이력을 확인할 수 없습니다.")

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

        def choose_folder():
            folder = filedialog.askdirectory(parent=editor, title="기존 프로젝트 폴더 선택", mustexist=True)
            if not folder:
                return
            try:
                updated = json.loads(document.get("1.0", "end"))
                updated["local_path"] = str(Path(folder))
                document.delete("1.0", "end")
                document.insert("1.0", json.dumps(updated, indent=2, ensure_ascii=False))
            except (TypeError, ValueError):
                status.configure(text="프로젝트 등록 필드를 확인하세요.")

        if mode in ("new", "edit"):
            ttk.Button(editor, text="프로젝트 폴더 선택", command=choose_folder).pack(anchor="e")

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
    try:
        ledger = Ledger.for_project()
    except (Blocked, OSError):
        root = tk.Tk()
        root.title("DevOrchestra")
        ttk.Label(root, text="로컬 저장소 · BLOCKED · .ai-local 경로를 확인하세요.").pack(padx=20, pady=20)
        root.mainloop()
        return
    root = tk.Tk()
    center = CommandCenter(root, ProjectionReader(ledger))
    if ledger.storage_status()["status"] == "NOT_INITIALIZED":
        root.after_idle(center.setup_wizard)
    root.mainloop()


if __name__ == "__main__":
    main()
