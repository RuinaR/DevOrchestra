"""DevOrchestra Command Center. Read-only desktop viewer for Windows."""
from datetime import datetime, timezone
from base64 import b64encode
from concurrent.futures import Future
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread
from time import monotonic, time
import tkinter as tk
from tkinter import filedialog, font, ttk
from uuid import UUID, uuid4

from .catalog import Catalog, REQUIREMENT_ROLES, new_project, read_frozen_requirements
from .build_info import build_label
from .default_instructions import defaults_text, public_instruction
from .lease import Blocked
from .instructions import InstructionStore, selected_source, source_ref
from .ledger import Ledger, check_path, digest
from .projection import ProjectionReader
from .questions import AUTHORIZATION_TYPES
from .runtime import PROJECTS, Runner
from .setup import create_work_copy, validate_setup
from .supervised import MAX_HANDOFF_BYTES, latest_handoff

BG, PANEL, TILE, EDGE = "#0c111d", "#141e2e", "#202c42", "#455778"
INK, MUTED, CYAN = "#f1f6ff", "#b9cde4", "#96baff"
TEAL, AMBER, RED = "#78efca", "#ffd082", "#ff9da9"
ROLE_MARKS = {"Manager": "01 / CMD", "Developer": "02 / DEV",
              "Reviewer": "03 / REV", "Verifier": "04 / VFY"}
POLL_MS = 1500
REQUIREMENT_NAMES = dict(zip(REQUIREMENT_ROLES, ("개발 목표", "기능 추가", "설계 변경", "버그 수정", "개선 요청")))
REQUIREMENT_HELP = {
    "GOAL": "차수의 방향을 기록합니다. 예: 이번 차수에는 신규 사용자 등록을 완성합니다.",
    "FEATURE": "새 기능 구현 요청. 예: 프로젝트 목록에 이름 검색을 추가해 주세요.",
    "BUG": "잘못된 동작 수정 요청. 예: 빈 이름으로 저장하면 오류가 납니다. 입력 안내를 표시해 주세요.",
    "DESIGN": "구조나 설계 변경 요청. 예: 문서 등록과 개발 시작을 분리해 주세요.",
    "IMPROVEMENT": "테스트 후 개선점 요청. 예: 긴 문서 이름을 쉽게 읽을 수 있게 해 주세요.",
}
SAFETY_NOTE = ("다른 앱과 비간섭을 목표로 합니다. 프로젝트 및 승인 작업 폴더만 개발 대상으로 삼으며, "
               "메이플스토리 등 다른 앱의 입력·창·프로세스·메모리·화면 접근을 금지합니다. "
               "OS 수준 비간섭은 아직 실측되지 않았습니다.")


def handoff_stage(item):
    if item and item["kind"] == "supervised_imported" and item.get("disposition") == "CLARIFICATION":
        return 0, "질문 확인 필요", "대시보드에서 답변한 뒤 새 계획 지시를 만드세요."
    return {
        "supervised_requested": (0, "계획 결과 대기", "지시를 기존 Codex에 직접 붙여넣고 계획 JSON을 가져오세요."),
        "supervised_imported": (0, "계획 가져오기 완료", "계획을 확인한 뒤 구현 지시를 준비하세요."),
        "supervised_developer": (1, "구현 결과 대기", "승인한 작업 폴더에서 얻은 구현 결과를 가져오세요."),
        "supervised_reviewer": (2, "검토 결과 대기", "변경 증거를 검토한 결과를 가져오세요."),
        "supervised_reviewed": (3, "검증 준비", "승인할 빌드·테스트 계획을 준비하세요."),
        "supervised_verifier": (3, "검증 결과 대기", "승인된 환경에서 얻은 결과를 가져오세요."),
        "supervised_evidence": (3, "산출물 확인", "산출물을 검토하세요. 사용자 테스트 준비는 아직 확인되지 않았습니다."),
        "supervised_review_blocked": (2, "재작업 또는 차단", "검토 의견을 확인하고 다음 계획을 결정하세요."),
        "supervised_cancelled": (0, "취소됨", "필요하면 새 계획 지시를 만드세요."),
    }.get(item["kind"] if item else None, (0, "대기 중", "요구사항을 등록한 뒤 감독형 개발을 시작하세요."))


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
                    font=(getattr(parent.winfo_toplevel(), "ui_font", "Segoe UI"),
                          size, "bold" if bold else "normal"), **options)


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
        self.gui_verification_cancel = None
        self.shutdown_notice = None
        self.question_window = None
        root.title(f"DevOrchestra | Command Center · {build_label()}")
        root.configure(bg=BG)
        root.geometry("1220x830")
        root.minsize(760, 580)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)
        self.theme()

        header = tk.Frame(root, bg=BG, padx=16, pady=10)
        header.grid(row=0, column=0, sticky="ew")
        label(header, "DevOrchestra  /  개발 운영실", 18, bg=BG, bold=True).pack(side="left")
        self.global_badge = label(header, fg=CYAN, bg=BG, bold=True)
        self.global_badge.pack(side="right")
        body = self.scroll_workspace(root)
        self.observation = label(body, bg=BG, fg=MUTED)
        self.observation.grid(row=4, column=0, sticky="ew", padx=16, pady=6)

        command = tk.Frame(body, bg=BG)
        command.grid(row=1, column=0, sticky="ew", padx=16)
        command.columnconfigure(1, weight=2)
        command.columnconfigure(2, weight=1)
        projects = section(command, "프로젝트와 요구사항")
        projects.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.project = ttk.Combobox(projects, state="readonly", width=16,
                                    values=[PROJECTS[key].name for key in self.project_ids])
        self.project.pack(fill="x", pady=(7, 3))
        self.project.current(0)
        self.project.bind("<<ComboboxSelected>>", self.select_project)
        ttk.Button(projects, text="프로젝트 등록", command=self.register_project).pack(fill="x")
        ttk.Button(projects, text="목표와 완료 기준 편집", command=lambda: self.register_project(edit=True)).pack(fill="x")
        ttk.Button(projects, text="요구사항 문서", command=self.requirement_settings).pack(fill="x")
        ttk.Button(projects, text="초기 설정", command=self.setup_wizard).pack(fill="x")
        ttk.Button(projects, text="작업 지침", command=self.instruction_settings).pack(fill="x")
        ttk.Button(projects, text="GUI 검증 보고서", command=self.gui_verification_report).pack(fill="x")
        advanced = tk.Frame(projects, bg=PANEL)
        for mode, title in (("edit", "프로젝트 설정"), ("limits", "공용 예산"), ("reserve", "토큰 예비량")):
            ttk.Button(advanced, text=title, command=lambda mode=mode: self.edit_settings(mode)).pack(fill="x")
        self.project_states = label(projects, fg=MUTED)
        self.project_states.pack(fill="x")
        label(projects, "목표·완료 기준은 프로젝트 전체의 기준입니다.\n요구사항 문서는 이번 개발 차수의 작업 지시입니다.",
              fg=MUTED, wraplength=245).pack(fill="x", pady=(8, 0))

        mission = section(command, "작업 현황")
        mission.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.mission = label(mission, size=13, bold=True)
        self.mission.pack(fill="x", pady=(4, 0))
        self.goal = label(mission, fg=MUTED)
        self.goal.pack(fill="x")
        self.development_button = ttk.Button(mission, text="개발 시작", command=self.development_start,
                                             style="Primary.TButton", state="disabled")
        self.development_button.pack(fill="x", pady=(8, 2))
        label(mission, "자동 실행은 권한 경계 확인 전까지 차단됩니다. 감독형은 지시와 결과를 직접 전달합니다.",
              fg=MUTED, wraplength=455).pack(fill="x")
        self.gates = label(mission)
        self.gates.pack(fill="x", pady=(3, 0))
        self.question_button = ttk.Button(mission, text="사용자 응답 대기 없음", state="disabled",
                                          command=self.answer_question)
        self.question_button.pack(fill="x", pady=(4, 0))

        briefing = section(body, "다음 행동과 안전 경계")
        briefing.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 10))
        self.next_brief = label(briefing, size=12, bold=True, fg=CYAN, wraplength=690)
        self.next_brief.pack(fill="x")
        label(briefing, SAFETY_NOTE, fg=MUTED, wraplength=690).pack(fill="x", pady=(5, 0))

        safety = section(body, "안전과 예산")
        safety.grid(row=6, column=0, sticky="ew", padx=16, pady=8)
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

        operations = tk.Frame(body, bg=BG)
        operations.grid(row=7, column=0, sticky="ew", padx=16, pady=8)
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

        audit = self.build_audit(body)
        details_visible = False

        def toggle_details():
            nonlocal details_visible
            details_visible = not details_visible
            self.details_visible = details_visible
            for frame in (safety, operations, audit):
                (frame.grid if details_visible else frame.grid_remove)()
            if details_visible:
                self.gates.pack(fill="x", before=self.question_button)
                advanced.pack(fill="x", before=self.project_states)
            else:
                self.gates.pack_forget()
                advanced.pack_forget()
            if self.view:
                self.render()

        details_visible = True
        toggle_details()
        ttk.Button(header, text="고급 정보", command=toggle_details).pack(side="right", padx=8)
        root.bind("<F5>", lambda _: self.refresh())
        root.protocol("WM_DELETE_WINDOW", self.close_requested)
        self.refresh()
        root.after(POLL_MS, self.poll)

    def scroll_workspace(self, root):
        root.grid_rowconfigure(3, weight=0)
        root.grid_rowconfigure(1, weight=1)
        viewport = ttk.Frame(root)
        viewport.grid(row=1, column=0, sticky="nsew")
        canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0, takefocus=True, yscrollincrement=20)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(canvas, bg=BG)
        content.grid_columnconfigure(0, weight=1)
        embedded = canvas.create_window(0, 0, window=content, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(embedded, width=event.width))
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))

        def wheel(event):
            widget = event.widget
            # Keep the table, text editors and pop-up controls independently scrollable.
            if widget is not canvas and not str(widget).startswith(str(content) + ".") and widget is not content:
                return
            if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
                return
            if event.delta:
                canvas.yview_scroll((-1 if event.delta > 0 else 1) * max(1, abs(event.delta) // 120), "units")
                return "break"

        root.bind("<MouseWheel>", wheel, add="+")
        canvas.bind("<Button-1>", lambda _event: canvas.focus_set())
        for key, step in (("Up", -1), ("Down", 1), ("Prior", -1), ("Next", 1)):
            unit = "pages" if key in ("Prior", "Next") else "units"
            canvas.bind(f"<{key}>", lambda _event, step=step, unit=unit: canvas.yview_scroll(step, unit))
        canvas.bind("<Home>", lambda _event: canvas.yview_moveto(0))
        canvas.bind("<End>", lambda _event: canvas.yview_moveto(1))
        return content

    def theme(self):
        available = {family.casefold(): family for family in font.families(self.root)}
        family = next((available[name] for name in ("pretendard", "suit", "맑은 고딕", "malgun gothic")
                       if name in available), "Segoe UI")
        self.root.ui_font = family
        for control, options in {
            "Toplevel": {"background": BG},
            "Text": {"background": TILE, "foreground": INK, "insertBackground": INK,
                     "selectBackground": EDGE, "selectForeground": INK,
                     "highlightBackground": EDGE, "highlightColor": CYAN, "highlightThickness": 1,
                     "font": f"{{{family}}} 11", "padX": 8, "padY": 6, "relief": "flat"},
            "Listbox": {"background": TILE, "foreground": INK, "selectBackground": EDGE,
                        "selectForeground": INK, "highlightBackground": EDGE, "highlightColor": CYAN,
                        "highlightThickness": 1, "font": f"{{{family}}} 11"},
        }.items():
            for key, value in options.items():
                self.root.option_add(f"*{control}.{key}", value)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=TILE, foreground=INK, fieldbackground=TILE,
                        bordercolor=EDGE, font=(family, 10))
        style.configure("TButton", padding=(8, 5))
        style.map("TButton", background=[("disabled", PANEL), ("pressed", EDGE), ("active", "#254665")],
                  foreground=[("disabled", MUTED)],
                  bordercolor=[("focus", CYAN)])
        style.configure("Primary.TButton", background="#385ba0", foreground=INK, padding=(12, 8))
        style.map("Primary.TButton", background=[("disabled", PANEL), ("pressed", "#2d4778"),
                                                  ("active", "#4c70b2")], bordercolor=[("focus", CYAN)])
        for control in ("TEntry", "TCombobox", "TCheckbutton", "TNotebook.Tab"):
            style.map(control, background=[("disabled", PANEL), ("selected", EDGE), ("active", "#254665")],
                      bordercolor=[("invalid", RED), ("focus", CYAN)])
        style.map("TCombobox", fieldbackground=[("readonly", TILE)],
                  selectbackground=[("readonly", EDGE)], selectforeground=[("readonly", INK)])
        style.configure("Treeview", rowheight=24)
        style.map("Treeview", background=[("selected", "#315879")],
                  foreground=[("selected", INK)])
        style.configure("Treeview.Heading", background="#1a2231", foreground=INK)
        style.map("Treeview.Heading", background=[("pressed", "#0d121b"), ("active", "#263245")],
                  foreground=[("pressed", INK), ("active", INK)])

    def confirm(self, title, text, *, parent):
        dialog = tk.Toplevel(parent, bg=BG, padx=18, pady=14)
        dialog.title(title)
        dialog.transient(parent)
        dialog.resizable(False, False)
        label(dialog, text, size=11, bg=BG, wraplength=460).pack(fill="x", pady=(0, 12))
        result = False

        def finish(value=False):
            nonlocal result
            result = value
            dialog.destroy()

        controls = ttk.Frame(dialog)
        controls.pack(anchor="e")
        cancel = ttk.Button(controls, text="취소", command=finish)
        cancel.pack(side="left", padx=6)
        ttk.Button(controls, text="확인", command=lambda: finish(True)).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", finish)
        dialog.bind("<Escape>", lambda _event: finish())
        dialog.grab_set()
        cancel.focus_set()
        parent.wait_window(dialog)
        return result

    def panel(self):
        window = tk.Toplevel(self.root, bg=BG, padx=12, pady=10)
        window.bind("<Escape>", lambda _event: window.destroy())
        return window

    def editor_box(self, parent, *, height=4, state="normal"):
        row = ttk.Frame(parent)
        row.pack(fill="both", expand=True, pady=(2, 6))
        editor = tk.Text(row, height=height, width=58, wrap="word", state=state)
        self.scrollable(row, editor)
        editor.bind("<Tab>", lambda event: (event.widget.tk_focusNext().focus_set(), "break")[1])
        editor.bind("<Shift-Tab>", lambda event: (event.widget.tk_focusPrev().focus_set(), "break")[1])
        return editor

    def build_audit(self, root):
        audit = section(root, "06  /  EVENT STREAM & EVIDENCE")
        audit.grid(row=8, column=0, sticky="ew", padx=16, pady=(0, 10))
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
        self.development_button.configure(text="개발 시작", state="disabled")
        if hasattr(self, "next_brief"):
            self.next_brief.configure(text=reason)
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
        readable_state = {"IDLE": "대기 중", "WAITING_FOR_USER": "답변 필요", "SESSION READY": "다음 작업 준비",
                          "COMPLETED": "완료", "BLOCKED": "차단됨", "PAUSED": "일시정지",
                          "FAILED": "실패", "CANCELLED": "취소됨"}.get(state, "진행 중")
        self.global_badge.configure(text=f"{readable_state} · {global_project}" if current else readable_state,
                                    fg=color(state))
        workflow = view["workflow"]
        handoff = view.get("supervised_handoff")
        self.development_button.configure(text="개발 작업 열기" if handoff else "개발 시작",
                                          state="normal" if view["registration"] and workflow["requirements_count"]
                                          else "disabled")
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
        latest = max(view["requirements"], key=lambda item: item["registered_at"], default=None)
        document_summary = ("최근 등록 · " + latest["path"][:65] + f" · 개정 {latest['version']}"
                            if latest else "첫 요구사항을 작성하거나 문서로 등록하세요.")
        self.project_states.configure(text=f"등록 요구사항 {workflow['requirements_count']}개\n{document_summary}",
                                      wraplength=245)
        _, stage_title, stage_next = handoff_stage(handoff)
        if hasattr(self, "next_brief"):
            waiting = view.get("user_question")
            self.next_brief.configure(text=("사용자 질문에 답변하세요." if waiting else stage_next if handoff
                                           else next_action))
        self.mission.configure(text=f"{view['project_name']}  /  {view['project_state']}",
                               fg=color(view["project_state"]))
        registration = view["registration"]
        configured = registration["data"] if registration else None
        goal = "프로젝트를 등록하고 첫 요구사항을 추가하세요."
        if configured:
            text = configured["goal"]
            goal = "개발 목표\n" + text[:380] + ("…" if len(text) > 380 else "")
            criteria = configured["acceptance"]
            goal += "\n완료 기준\n" + "\n".join("• " + line[:125] + ("…" if len(line) > 125 else "")
                                                   for line in criteria[:3])
            if len(criteria) > 3:
                goal += f"\n외 {len(criteria) - 3}개 · 목표와 완료 기준 편집에서 확인"
        if handoff:
            goal += "\n감독형 작업 · " + stage_title
            if handoff.get("artifacts"):
                goal += "\n결과물 · " + ", ".join(handoff["artifacts"])[:250]
        if getattr(self, "details_visible", False):
            goal += f"\n자동 Manager · {manager_status}{cycle_label}"
        session = view["development"]
        if session and getattr(self, "details_visible", False):
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
        window = self.question_window = self.panel()
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
        active = bool(self.view and (self.view.get("recovery_state") == "UNKNOWN"
                      or self.view.get("global_run") and self.view.get("global_state") in
                      ("PLANNING", "DEVELOPING", "REVIEWING", "VERIFYING", "CHECKPOINTING", "PAUSED")))
        for action, button in self.shutdown_buttons.items():
            enabled = self.shutdown_future is None and action in allowed.get(state, ()) and (action != "request" or active)
            button.configure(state="normal" if enabled else "disabled")

    def close_requested(self):
        if self.gui_verification_cancel is not None:
            self.gui_verification_cancel.set()
            return
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
        wizard = self.panel()
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

    def register_project(self, *, edit=False):
        if self.view is None:
            self.setup_wizard()
            return
        panel = self.panel()
        existing = self.view["registration"] if edit else None
        if edit and not existing:
            panel.destroy()
            self.observation.configure(text="편집할 프로젝트를 선택하세요.")
            return
        panel.title("프로젝트 편집" if edit else "프로젝트 등록")
        panel.configure(padx=16, pady=12)
        fields = {}
        for title, key in (("프로젝트 이름", "name"), ("원본 프로젝트 폴더", "original")):
            ttk.Label(panel, text=title).pack(anchor="w")
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=(2, 8))
            value = fields[key] = ttk.Entry(row, width=55)
            value.pack(side="left", fill="x", expand=True)
            if key == "original":
                def choose(field=value):
                    folder = filedialog.askdirectory(parent=panel, mustexist=True)
                    if folder:
                        field.delete(0, "end")
                        field.insert(0, str(Path(folder)))
                ttk.Button(row, text="폴더 선택", command=choose).pack(side="right", padx=(6, 0))
        ttk.Label(panel, text="목표와 상세 설명 · 최대 8,000자").pack(anchor="w")
        goal = self.editor_box(panel, height=5)
        ttk.Label(panel, text="완료 기준 · 한 줄에 한 항목, 최대 32개 (항목당 2,000자)").pack(anchor="w")
        criteria = self.editor_box(panel, height=4)
        options = ttk.Frame(panel)
        ttk.Button(panel, text="개발용 작업 폴더와 추가 설정",
                   command=lambda: options.pack_forget() if options.winfo_manager() else
                   options.pack(fill="x", before=status)).pack(anchor="w", pady=4)
        ttk.Label(options, text="원본을 보호하기 위한 별도 작업 사본입니다.").pack(anchor="w")
        modes = ("나중에 지정", "기존 사본 선택", "새 사본 생성")
        mode = ttk.Combobox(options, values=modes, state="readonly")
        mode.current(0)
        mode.pack(fill="x")
        replica = fields["replica"] = ttk.Entry(options)
        replica.pack(fill="x")

        def choose_replica():
            folder = filedialog.askdirectory(parent=panel, mustexist=True)
            if folder:
                replica.delete(0, "end")
                replica.insert(0, str(Path(folder) / "development-copy") if mode.current() == 2 else folder)
                if mode.current() == 0:
                    mode.current(1)

        ttk.Button(options, text="작업 폴더 또는 새 사본의 상위 폴더 선택", command=choose_replica).pack(anchor="w")
        ttk.Label(options, text="프로젝트별 추가 지침 (원본 기준 Markdown 상대경로)").pack(anchor="w")
        agents = ttk.Entry(options)
        agents.pack(fill="x")
        ttk.Label(options, text="기존 GitHub 저장소 주소 (선택 · 등록만 수행)").pack(anchor="w")
        repository = ttk.Entry(options)
        repository.pack(fill="x")
        if existing:
            data = existing["data"]
            fields["name"].insert(0, data["name"])
            fields["original"].insert(0, data["local_path"])
            goal.insert("1.0", data["goal"])
            criteria.insert("1.0", "\n".join(data["acceptance"]))
            agents.insert(0, data["documents"].get("agents", ""))
            repository.insert(0, data["repository"])
            if self.view["setup"]:
                replica.insert(0, self.view["setup"]["replica"])
                mode.current(1)
        ttk.Label(panel, text="개발 모드 · 감독형 Codex  |  자동 실행은 차단됨").pack(anchor="w", pady=6)
        add_docs = tk.BooleanVar(panel, value=not edit)
        ttk.Checkbutton(panel, text="등록 후 요구사항 문서 추가", variable=add_docs).pack(anchor="w")
        status = ttk.Label(panel, wraplength=530)
        status.pack(fill="x")

        def save():
            values = {key: widget.get().strip() for key, widget in fields.items()}
            values["goal"] = goal.get("1.0", "end-1c").strip()
            acceptance = [line.strip() for line in criteria.get("1.0", "end-1c").splitlines() if line.strip()]
            copying = mode.current() == 2
            if mode.current() == 0:
                values["replica"] = ""
            try:
                if not acceptance or not all(values[key] for key in ("name", "goal", "original")):
                    raise ValueError("이름, 원본 폴더, 목표, 완료 기준을 입력하세요.")
                for key in ("original", "replica"):
                    if values[key]:
                        path = Path(values[key])
                        check_path(path)
                        if not path.is_dir() and not (key == "replica" and copying):
                            raise ValueError("존재하는 폴더를 선택하세요.")
                identity = existing["data"]["project_id"] if existing else "project-" + uuid4().hex[:12]
                data = dict(existing["data"]) if existing else new_project(identity, values["name"], values["goal"])
                documents = {key: value for key, value in data["documents"].items() if key != "agents"}
                if agents.get().strip():
                    documents["agents"] = agents.get().strip()
                data.update(name=values["name"], goal=values["goal"], documents=documents,
                            local_path=str(Path(values["original"])), acceptance=acceptance,
                            repository=repository.get().strip())
                revision = existing["revision"] if existing else 0
                catalog = Catalog(self.reader.ledger)
                if values["replica"]:
                    setup = {"project": data, "replica": str(Path(values["replica"])), "wait_seconds": 10}
                    validate_setup(setup, self.reader.ledger.state_boundary, allow_file_proposals=bool(revision))
                    if copying:
                        if not self.confirm("개발용 작업 사본 만들기",
                                                   "표시된 원본 파일을 지정한 새 폴더에 복사하시겠습니까? "
                                                   "복사 전에 프로젝트에 비공개 파일이 포함됐는지 확인하세요.",
                                                   parent=panel):
                            return
                        create_work_copy(Path(values["original"]), Path(values["replica"]))
                    catalog.save_setup(setup, revision)
                else:
                    catalog.save_project(data, revision)
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

        ttk.Button(panel, text="저장" if edit else "프로젝트 등록", command=save).pack(anchor="e")

    def instruction_settings(self):
        panel = self.panel()
        panel.title("DevOrchestra · 작업 지침")
        panel.geometry("840x650")
        project_id = self.project_ids[self.project.current()]
        status = tk.StringVar(value="원본과 개선본은 이 PC에만 보관됩니다.")
        ttk.Label(panel, textvariable=status, wraplength=640).pack(fill="x")
        ttk.Label(panel, text="감독형 적용 순서 · 프로젝트 승인 재정의 → 개인 기본 → 공개 역할 → 프로젝트 공통 → 공개 전역",
                  wraplength=740).pack(fill="x")
        selected = ttk.Combobox(panel, state="readonly", width=42)
        selected.pack(fill="x")
        sections = {"global": "전역 개발지침", "manager-planning": "계획 지침", "developer": "구현 지침",
                    "reviewer": "검토 지침", "verifier": "검증 지침", "manager-final": "최종 판정 지침",
                    "project": "프로젝트 추가 지침",
                    "original": "전역 원본 (보존용)"}
        category = ttk.Combobox(panel, values=tuple(sections.values()), state="readonly")
        category.current(0)
        category.pack(fill="x", pady=6)
        preview = self.editor_box(panel, height=22, state="disabled")
        reviewed = None
        project_ref = None
        reviewed_replica = None

        def show(text):
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", text)
            preview.configure(state="disabled")

        def show_applied(_event=None):
            try:
                store = InstructionStore(self.reader.ledger)
                pin = store.default_pin()
                defaults = store.active_defaults()
                role = tuple(sections)[category.current()]
                catalog = Catalog(self.reader.ledger).read()
                project = catalog["projects"].get(project_id, {}).get("data")
                setup = catalog["setups"].get(project_id)
                if role == "project":
                    from .instructions import instruction_bytes
                    relative = project["documents"].get("agents") if project else None
                    if relative:
                        source = Path(project["local_path"]) / relative
                        data = instruction_bytes(source)
                        from hashlib import sha256
                        status.set(f"적용 중 · {relative}\nSHA-256 {sha256(data).hexdigest()}")
                        show(data.decode("utf-8-sig"))
                        return
                active = store.active(project_id)
                if role == "global" and active:
                    if not setup:
                        raise Blocked("프로젝트 지침의 작업 폴더를 확인하세요.")
                    show(store.verify(active, Path(setup["replica"])).decode("utf-8-sig"))
                    status.set(f"프로젝트별 개인 기준 적용 중 · {active['version']}\nSHA-256 {active['sha256']}")
                    return
                drafts = store.active_role_drafts(project_id)
                if role in drafts:
                    from hashlib import sha256
                    name = "verifier-contract" if role == "verifier" else role
                    source = store.ledger.path.parent / "roles" / f"{name}.md"
                    show(drafts[role])
                    status.set(f"프로젝트 승인 역할 초안 · 내용 기준 SHA-256 {sha256(drafts[role].encode()).hexdigest()}"
                               f"\n출처 · {source}")
                elif role in defaults:
                    item = defaults[role]
                    status.set(f"{'보존용' if role == 'original' else '개인 기본 지침 적용 중'} · {item['version']}"
                               f"\n출처 · {Path(pin['manifest']).parent / item['path']}\nSHA-256 {item['sha256']}")
                    show(item["text"])
                elif role != "original":
                    item = public_instruction(role)
                    state = "정책 참조" if role == "manager-final" else "공개 기본 지침"
                    status.set(f"{state} · {item['version']}\n출처 · {item['path']}\nSHA-256 {item['sha256']}")
                    show(item["text"])
                else:
                    status.set("보존용 원본 미연결")
                    show("")
            except (Blocked, OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                status.set("변경됨 또는 확인 필요 · 지침을 다시 확인하고 연결하세요.")
                show("")

        def connect_defaults():
            choice = filedialog.askopenfilename(parent=panel, title="기존 개인 지침 MANIFEST.json 선택",
                                                filetypes=[("JSON", "*.json")])
            if not choice:
                return
            try:
                store = InstructionStore(self.reader.ledger)
                manifest = Path(choice)
                verified = store.inspect_defaults(manifest)
                show("\n\n".join(f"{sections[role]} · {item['version']} · {item['sha256']}\n{item['text']}"
                                 for role, item in verified.items()))
                panel.update_idletasks()
                if self.confirm("개인 기본 지침 연결", "표시된 기존 지침을 이 저장소의 감독형 개발 기본으로 연결할까요?",
                                parent=panel):
                    store.approve_defaults(manifest, verified)
                    show_applied()
            except (Blocked, OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                status.set("연결 실패 · 누락된 지침 또는 해시가 다른 지침을 확인하세요.")

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
            if not path or not self.confirm(
                "원본 로컬 백업", "선택한 전역 지침의 원본 바이트를 이 PC에 백업하시겠습니까?", parent=panel,
            ):
                return
            try:
                selected.set(InstructionStore(self.reader.ledger).backup(Path(path)))
                refresh()
            except (Blocked, OSError, ValueError, sqlite3.Error):
                status.set("원본 백업 또는 무결성 확인에 실패했습니다.")

        def approve():
            if not reviewed or not self.confirm(
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
                if self.confirm("역할 초안 연결", "검토한 로컬 역할 초안을 이 프로젝트의 감독형 지시에 포함할까요?",
                                       parent=panel):
                    store.approve_role_drafts(project_id, drafts)
                    status.set("역할 초안 연결됨 · 감독형 지시에만 적용")
            except (Blocked, OSError, ValueError, sqlite3.Error):
                status.set("역할 초안을 연결할 수 없습니다.")

        controls = ttk.Frame(panel)
        controls.pack(fill="x", before=preview.master)
        version_controls = ttk.Frame(panel)
        version_controls.pack(fill="x", before=preview.master)
        ttk.Button(controls, text="적용 지침 보기", command=show_applied).pack(side="left")
        ttk.Button(controls, text="개인 기본 지침 연결", command=connect_defaults).pack(side="left")
        ttk.Button(controls, text="로컬 역할 초안 연결", command=local_roles).pack(side="left")
        ttk.Button(controls, text="개인 지침 선택 및 백업", command=backup).pack(side="left")
        ttk.Button(version_controls, text="상태 다시 확인", command=refresh).pack(side="left")
        ttk.Button(version_controls, text="개선본 전체 보기", command=full_preview).pack(side="left")
        approve_button = ttk.Button(version_controls, text="선택 버전 승인 / 복원", command=approve)
        approve_button.pack(side="left")
        selected.bind("<<ComboboxSelected>>", refresh)
        category.bind("<<ComboboxSelected>>", show_applied)
        refresh()
        show_applied()

    def gui_verification_report(self):
        from .gui_verification import verified_image

        project_id = self.project_ids[self.project.current()]
        panel = self.panel()
        panel.title("DevOrchestra · GUI 검증 보고서")
        panel.geometry("860x445")
        panel.minsize(775, 415)
        registered = project_id not in PROJECTS
        status = tk.StringVar(value="등록 프로젝트 GUI 시나리오" if registered else "고정 Windows 테스트 앱 · Verifier")
        ttk.Label(panel, textvariable=status, wraplength=810).pack(fill="x", pady=(0, 8))
        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        role = tk.StringVar(value="Verifier")
        exe_name = tk.StringVar()
        if registered:
            ttk.Combobox(controls, textvariable=role, values=("Verifier", "Developer", "Reviewer"),
                         state="readonly", width=12).pack(side="left", padx=(0, 5))
            ttk.Label(controls, text="검증 EXE (선택)").pack(side="left", padx=(0, 4))
            ttk.Entry(controls, textvariable=exe_name, width=23).pack(side="left", padx=(0, 5))
        gallery = ttk.Frame(panel)
        gallery.pack(fill="both", expand=True)
        thumbnails = []

        def load(receipt, image):
            if receipt["artifact_dir"] != f"gui-{UUID(receipt['run_id']).hex}":
                raise Blocked("GUI artifact directory invalid")
            if image["file"] not in {"comparison.png", *(
                    f"{prefix}-{index}.png" for prefix in ("scene", "report") for index in range(1, 4)
            )}:
                raise Blocked("GUI artifact filename invalid")
            path = self.reader.ledger.local_directory("logs") / receipt["artifact_dir"] / image["file"]
            data, _ = verified_image(path, image["sha256"])
            return tk.PhotoImage(master=panel, data=b64encode(data))

        def open_image(receipt, title, image):
            try:
                photo = load(receipt, image)
                viewer = self.panel()
                viewer.title(title)
                shown = photo if photo.width() <= viewer.winfo_screenwidth() - 80 else photo.subsample(2)
                picture = ttk.Label(viewer, image=shown)
                picture.image = shown
                picture.pack()
                ttk.Label(viewer, text=f"{image['file']} · SHA-256 {image['sha256']}").pack(fill="x")
            except (Blocked, OSError, ValueError, KeyError, tk.TclError):
                status.set("이미지 무결성 또는 표시를 확인할 수 없습니다.")

        def render(receipt):
            for child in gallery.winfo_children():
                child.destroy()
            thumbnails.clear()
            if receipt is None:
                status.set("이 프로젝트의 GUI 검증 영수증이 없습니다.")
                return
            result = receipt.get("result", "NOT_VERIFIED")
            status.set(f"{result} · {receipt['role']} · 영수증 {receipt['run_id']}\n"
                       f"종료 코드 {receipt.get('exit_code')} · Job 활성 {receipt.get('job_active')}"
                       + (f"\n{receipt['reason']}" if receipt.get("reason") else ""))
            try:
                for column, scene in enumerate(receipt.get("scenes", ())):
                    card = ttk.Frame(gallery, padding=5)
                    card.grid(row=0, column=column, sticky="n")
                    thumbnail = load(receipt, scene["processed"]).subsample(4)
                    thumbnails.append(thumbnail)
                    ttk.Button(card, image=thumbnail, command=lambda scene=scene: open_image(
                        receipt, scene["title"], scene["processed"],
                    )).pack()
                    ttk.Label(card, text=f"{scene['title']} · {scene['observed']}\n{scene['input']}"
                                         f"\n{scene['captured_at'][:19]} UTC", wraplength=240).pack()
                    ttk.Button(card, text="원본 보기", command=lambda scene=scene: open_image(
                        receipt, scene["title"], scene["original"],
                    )).pack()
                if receipt.get("comparison"):
                    ttk.Button(gallery, text="전후 비교 보기", command=lambda: open_image(
                        receipt, "전후 비교", receipt["comparison"],
                    )).grid(row=1, column=0, columnspan=3, pady=6)
            except (Blocked, OSError, ValueError, KeyError, tk.TclError):
                status.set(status.get() + "\n일부 이미지의 무결성 또는 표시를 확인할 수 없습니다.")

        def close():
            if self.gui_verification_cancel is not None and future is not None and not future.done():
                self.gui_verification_cancel.set()
                status.set("취소 요청됨 · 소유 자원 회수 대기")
            else:
                panel.destroy()

        def start():
            nonlocal future
            if self.gui_verification_cancel is not None:
                status.set("다른 GUI 검증이 진행 중입니다.")
                return
            try:
                selected_role = role.get()
                filename = exe_name.get().strip() or None
                scope = self.controller.gui_verification_scope(project_id, registered=registered,
                                                               role=selected_role, exe_name=filename)
            except Blocked as error:
                status.set(str(error))
                return
            except (OSError, sqlite3.Error, ValueError):
                status.set("검증 범위를 확인할 수 없습니다.")
                return
            descriptor = (f"\n복제본: {scope['replica']}\n"
                          f"{scope['descriptor']} · SHA-256 {scope['descriptor_sha256']}\n"
                          if registered else "")
            if not self.confirm("GUI 검증 승인",
                                f"{project_id} · {selected_role} · {scope['scenario']}\n"
                                f"대상: {scope['exe']}\nSHA-256 {scope['exe_sha256']}\n"
                                f"호스트 스크립트 SHA-256 {scope['fixture_sha256']}\n"
                                f"{descriptor}"
                                f"소유 창 조작 및 비공개 캡처 · 최대 {scope['timeout_seconds']}초 실행",
                                parent=panel):
                return
            cancellation = Event()
            self.gui_verification_cancel = cancellation
            future = Future()
            run.configure(state="disabled")
            status.set("검증 중 · 완료 또는 취소 후 소유 자원 회수 확인")

            def work():
                try:
                    if filename:
                        from .gui_exe import tool_request

                        result = self.controller.dispatch_gui_tool(tool_request(scope), digest(scope), cancellation.is_set)
                    else:
                        result = self.controller.verify_gui_fixture(
                            project_id, digest(scope), cancellation.is_set, registered=registered, role=selected_role,
                        )
                    future.set_result(result)
                except Exception as error:
                    future.set_exception(error)

            def poll():
                if not future.done():
                    panel.after(80, poll)
                    return
                self.gui_verification_cancel = None
                run.configure(state="normal")
                if future.exception() is not None:
                    status.set("검증 차단 · 원장에서 승인 및 회수 상태를 확인하세요.")
                else:
                    render(future.result())

            Thread(target=work, daemon=False).start()
            panel.after(80, poll)

        future = None
        run = ttk.Button(controls, text="GUI 검증 실행", command=start)
        run.pack(side="left")
        ttk.Button(controls, text="닫기 / 실행 취소", command=close).pack(side="right")
        panel.protocol("WM_DELETE_WINDOW", close)
        panel.bind("<Escape>", lambda _event: close())
        try:
            render(self.controller.gui_verification_receipt(project_id))
        except (Blocked, OSError, sqlite3.Error, ValueError):
            status.set("원장이 준비되지 않았습니다.")

    def development_start(self):
        if not self.view or not self.view["registration"]:
            return
        project_id = self.view["project_id"]
        try:
            decision = self.controller.inspect_manager_admission(project_id)
        except (Blocked, OSError, sqlite3.Error, ValueError):
            decision = {"state": "BLOCKED", "message": "로컬 저장소를 확인할 수 없습니다.", "summary": None}
        panel = self.panel()
        panel.title("개발 시작 · Manager 준비")
        state = {"BLOCKED": "자동 Manager 준비 차단", "NEEDS_OWNER_APPROVAL": "사용자 승인 필요",
                 "READY": "실행 전 검사 준비"}[decision["state"]]
        label(panel, state, size=13, bold=True, bg=BG).pack(fill="x")
        label(panel, decision["message"], bg=BG, wraplength=520).pack(fill="x", pady=8)
        label(panel, "지금 사용 가능 · 기존 Codex에 지시를 직접 전달하는 감독형 개발\n"
              "자동 개발 후보 · 공식 App Server (실행 경계 확인 전 차단)",
              bg=BG, fg=MUTED, wraplength=520).pack(fill="x", pady=(0, 8))
        if summary := decision["summary"]:
            budget = summary["budget_ceiling"]
            model = ("등록된 실제 모델 없음" if summary["model"] == "mock-v1" else summary["model"])
            label(panel, f"모델 · {model} / {summary['reasoning']}\n"
                  f"입력 · 등록 요구사항 {summary['requirements']}개\n"
                  f"권한 · 읽기 전용 계획, 승인 요청 및 작업 도구 사용 없음\n"
                  f"등록 상한 · {budget['seconds']}초 / {budget['tokens']} 토큰",
                  bg=BG, wraplength=520).pack(fill="x", pady=(0, 8))

        def supervised():
            panel.destroy()
            self.supervised_work()

        ttk.Button(panel, text="감독형 Codex 작업으로 계속", command=supervised).pack(anchor="e")
        ttk.Button(panel, text="GUI 검증 및 보고서", command=self.gui_verification_report).pack(anchor="e")

    def supervised_work(self):
        if not self.view or not self.view["registration"]:
            self.observation.configure(text="프로젝트를 먼저 등록하세요.")
            return
        project_id = self.view["project_id"]
        panel = self.panel()
        panel.title(f"감독형 Codex · {project_id}")
        panel.geometry("780x680")
        ttk.Label(panel, text="직접 전달 방식 · 이 앱은 Codex를 실행하거나 다른 창에 입력하지 않습니다.",
                  wraplength=660).pack(fill="x")
        tracker = ttk.Frame(panel)
        tracker.pack(fill="x", pady=8)
        steps = []
        for index, title in enumerate(("계획", "구현", "검토", "검증")):
            tracker.columnconfigure(index, weight=1, uniform="handoff")
            badge = label(tracker, bg=TILE, bold=True)
            badge.grid(row=0, column=index, sticky="ew", padx=3, ipady=7)
            steps.append((title, badge))
        guide = label(panel, bg=BG, fg=CYAN, bold=True, wraplength=680)
        guide.pack(fill="x")
        status = ttk.Label(panel, wraplength=660)
        status.pack(fill="x")
        ttk.Label(panel, text="지시 만들기 → 복사 → 기존 Codex에 직접 붙여넣기").pack(fill="x")
        prompt = self.editor_box(panel, height=9, state="disabled")
        ttk.Label(panel, text="Codex가 반환한 JSON을 붙여넣거나 파일로 가져오세요.").pack(fill="x")
        response = self.editor_box(panel, height=7)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        shown = self.view["supervised_handoff"]
        ttk.Label(panel, text="계획에서 제안할 파일 · 프로젝트 기준 상대경로를 쉼표로 구분 (예: src/app.py)").pack(
            fill="x", before=buttons)
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
                if action in ("start", "prompt", "developer", "developer_submit", "verifier", "reopen_role"):
                    show(result)
                else:
                    show(None)
                status.configure(text=describe())
                update_steps()
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

        def reopen():
            if shown and shown["kind"] == "supervised_requested":
                apply("prompt")
            elif shown and shown["kind"] == "supervised_developer":
                if not pasted().strip():
                    status.configure(text="승인했던 Manager 결과 JSON을 아래 칸에 붙여넣고 다시 열기를 누르세요.")
                    return
                apply("reopen_role", pasted())
            else:
                apply("reopen_role")

        controls = {}
        for key, title, command in (
            ("start", "계획 지시 만들기", lambda: apply("start", paths=[path.strip() for path in path_entry.get().split(",")
                                                                   if path.strip()])),
            ("prompt", "현재 지시 다시 열기", reopen),
            ("copy", "지시 복사", copy), ("cancel", "대기 취소", lambda: apply("cancel")),
        ):
            controls[key] = ttk.Button(buttons, text=title, command=command)
            controls[key].pack(side="left", padx=3)
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        for key, title, command in (
            ("submit", "결과 확인", lambda: import_result(pasted())), ("file", "JSON 파일 가져오기", import_file),
            ("developer", "구현 지시 준비", developer), ("verifier", "검증 계획 준비", verifier),
        ):
            controls[key] = ttk.Button(actions, text=title, command=command)
            controls[key].pack(side="left", padx=3)

        def update_steps():
            kind = shown["kind"] if shown else None
            index, title, next_step = handoff_stage(shown)
            live = bool(shown and time() < shown.get("expires", 0))
            accepts = live and kind in ("supervised_requested", "supervised_developer",
                                        "supervised_reviewer", "supervised_verifier")
            waiting = accepts or (live and kind == "supervised_reviewed")
            planned = kind == "supervised_imported" and shown.get("disposition") == "PROPOSED"
            if kind == "supervised_imported" and not planned:
                next_step = "대시보드의 질문에 답한 뒤 새 계획 지시를 만드세요."
            if shown and "expires" in shown and not live and kind not in ("supervised_evidence", "supervised_review_blocked"):
                next_step = "기존 요청이 만료되었습니다. 새 계획 지시를 만드세요."
            for position, (name, badge) in enumerate(steps):
                current = position == index
                badge.configure(text=f"{position + 1:02d}  {name}", bg="#385ba0" if current else TILE,
                                fg=INK if current else MUTED)
            guide.configure(text=f"현재 · {title}\n다음 · {next_step}")
            enabled = {"start": not waiting, "prompt": accepts,
                       "copy": bool(prompt.get("1.0", "end-1c")), "cancel": waiting,
                       "submit": accepts, "file": accepts, "developer": live and planned,
                       "verifier": live and kind == "supervised_reviewed"}
            for key, button in controls.items():
                button.configure(state="normal" if enabled[key] else "disabled")

        status.configure(text=describe())
        update_steps()

    def requirement_settings(self):
        if self.view is None or not self.view["registration"]:
            self.observation.configure(text="프로젝트를 먼저 등록하세요.")
            return
        project_id = self.view["project_id"]
        panel = self.panel()
        panel.title("DevOrchestra · 요구사항 문서")
        label(panel, "목표와 완료 기준은 프로젝트 전체를 안내합니다. 요구사항 문서는 이번 차수에 수행할 구체적인 작업을 설명합니다.",
              bg=BG, wraplength=690).pack(fill="x", pady=(0, 8))
        catalog = Catalog(self.reader.ledger)
        panel.geometry("780x690")
        listing = ttk.Frame(panel)
        listing.pack(fill="both", expand=True)
        table = tk.Listbox(listing, width=62, height=7, exportselection=False)
        self.scrollable(listing, table)
        preview = self.editor_box(panel, height=7, state="disabled")
        role = ttk.Combobox(panel, values=tuple(REQUIREMENT_NAMES.values()), state="readonly")
        role.current(1)
        role.pack(fill="x")
        role_help = label(panel, REQUIREMENT_HELP["FEATURE"], bg=BG, fg=CYAN, wraplength=690)
        role_help.pack(fill="x", pady=(3, 7))
        role.bind("<<ComboboxSelected>>", lambda _event: role_help.configure(
            text=REQUIREMENT_HELP[REQUIREMENT_ROLES[role.current()]]))
        status = ttk.Label(panel, text="관련 항목을 선택한 뒤 문서를 추가하면 두 기록을 연결합니다.")
        status.pack(fill="x")
        history = []

        def selected_item():
            selected = table.curselection()
            return history[-1 - selected[0]] if selected else None

        def show_document(_event=None):
            item = selected_item()
            text = "등록된 문서가 없습니다. 첫 문서를 작성하거나 프로젝트 안의 TXT/Markdown 파일을 선택하세요."
            if item:
                try:
                    documents = read_frozen_requirements(catalog.read(), project_id, [item],
                                                         self.reader.ledger.state_boundary)
                    text = documents[0]["text"]
                except (Blocked, OSError, ValueError, sqlite3.Error):
                    text = "이 개정의 파일 내용이 현재 경로와 일치하지 않습니다. 등록 이력은 보존됩니다."
            elif history:
                text = "문서를 선택하면 현재 파일이 등록한 개정과 일치하는지 확인하고 내용을 표시합니다."
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", text)
            preview.configure(state="disabled")

        def refresh():
            history[:] = catalog.read().get("requirements", {}).get(project_id, ())
            table.delete(0, "end")
            for item in reversed(history):
                relation = f" · 관련 {item['related'][:8]}" if item["related"] else ""
                table.insert("end", f"{item['path']} · {REQUIREMENT_NAMES[item['role']]} · v{item['version']}{relation}")
            show_document()

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
                related = selected_item()
                item = catalog.register_requirement(project_id, relative, REQUIREMENT_ROLES[role.current()],
                                                    related=related["id"] if related else None)
                refresh()
                status.configure(text=f"등록 확인 · v{item['version']} · {item['sha256']}")
                self.refresh()
            except (Blocked, ValueError, OSError, sqlite3.Error, KeyError):
                status.configure(text="등록 실패 · 경로, 파일 형식 또는 원장 상태를 확인하세요.")

        def write_document():
            related = selected_item()
            editor = self.panel()
            editor.title("요구사항 작성")
            ttk.Label(editor, text="원본 프로젝트 안에 새 Markdown 파일을 만듭니다. 기존 파일은 유지됩니다.").pack(anchor="w")
            name = ttk.Entry(editor, width=58)
            name.insert(0, "requirements-" + uuid4().hex[:8] + ".md")
            name.pack(fill="x", pady=6)
            body = self.editor_box(editor, height=14)
            notice = ttk.Label(editor, wraplength=480)
            notice.pack(fill="x")

            def save_document():
                try:
                    item = catalog.write_requirement(
                        project_id, name.get().strip(), body.get("1.0", "end-1c"),
                        REQUIREMENT_ROLES[role.current()], related=related["id"] if related else None,
                    )
                except FileExistsError:
                    notice.configure(text="같은 이름의 문서가 있습니다. 새 파일명을 입력하세요.")
                    return
                except (Blocked, OSError, ValueError, sqlite3.Error, KeyError):
                    notice.configure(text="저장 또는 등록 실패. 폴더·권한을 확인하고, 이미 저장된 파일은 가져오기를 사용하세요.")
                    return
                editor.destroy()
                refresh()
                status.configure(text=f"등록 완료 · {item['path']} · 다음 개발 기준본에 반영됩니다.")
                self.refresh()

            ttk.Button(editor, text="새 파일로 저장 및 등록", command=save_document).pack(anchor="e")

        table.bind("<<ListboxSelect>>", show_document)
        controls = ttk.Frame(panel)
        controls.pack(fill="x", pady=6)
        ttk.Button(controls, text="요구사항 직접 작성", command=write_document).pack(side="left", padx=3)
        ttk.Button(controls, text="TXT / Markdown 가져오기", command=register).pack(side="left", padx=3)
        ttk.Button(controls, text="관련 문서 선택 해제",
                   command=lambda: (table.selection_clear(0, "end"), show_document())).pack(side="left", padx=3)
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
        editor = self.panel()
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
