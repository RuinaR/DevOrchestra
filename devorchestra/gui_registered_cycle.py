"""Command Center entry point for an owner-approved registered Codex session."""
from concurrent.futures import Future
from base64 import b64encode
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread
from time import time
import tkinter as tk
from tkinter import filedialog, ttk

from . import codex_setup, cycle_progress, game_pilot, registered_cycle, replica_readiness, visual_review as visual
from .catalog import Catalog
from .ledger import digest


LABELS = {
    "replica-read": "작업 복제본 읽기", "replica-edit": "지정 파일 생성·수정",
    "offline-shell": "복제본에서 오프라인 명령 실행", "pinned-verifier": "지정된 독립 검증 명령 실행",
}
ROLE_LABELS = {"manager-planning": "계획 Manager", "developer": "Developer",
               "reviewer": "Reviewer", "manager-final": "최종 Manager"}
ROLE_TOKEN_FIELDS = {"manager-planning": "manager_tokens", "developer": "developer_tokens",
                     "reviewer": "reviewer_tokens", "manager-final": "final_manager_tokens"}


def _permission_checkboxes(panel, draft):
    options = ttk.LabelFrame(panel, text="자동 개발 사전 권한", padding=6)
    options.pack(fill="x", pady=(4, 6))
    ttk.Label(options, text="새 세션에 필요한 권한을 모두 체크하고 최종 승인 화면에서 확인하세요.",
              wraplength=580).grid(row=0, column=0, columnspan=2, sticky="w")
    grants = {}
    for index, key in enumerate(registered_cycle.PERMISSIONS):
        grants[key] = tk.BooleanVar(panel, value=draft.get(key, key == "replica-read"))
        ttk.Checkbutton(options, text=LABELS[key], variable=grants[key],
                        command=lambda key=key: draft.update({key: grants[key].get()})).grid(
            row=1 + index // 2, column=index % 2, sticky="w")
    return grants


def _checked_permissions(grants, state):
    selected = [key for key, variable in grants.items() if variable.get()]
    missing = [LABELS[key] for key in grants if key not in selected]
    if missing:
        state.set("자동 개발 사전 권한에서 다음 항목을 확인하고 체크하세요.\n" + " · ".join(missing))
        return None
    return selected


def _remaining_signoff(checkpoint):
    if checkpoint.get("comparison") is not None:
        return "최종 Manager"
    if checkpoint.get("review") is not None:
        return "독립 Verifier → 최종 Manager"
    return "Reviewer → 독립 Verifier → 최종 Manager"


def _open_visual_report(center, runner, project):
    panel = center.panel()
    panel.title("소유 창 캡처와 AI 시각 검토")
    panel.geometry("740x600")
    display = center.editor_box(panel, height=20)
    try:
        report = next((row for row in reversed(registered_cycle.history(runner.ledger, project))
                       if row["kind"] == "native_inspection_visual_review"), None)
        if report is None:
            display.insert("end", "시각 검토 응답이 아직 없습니다.")
        else:
            display.insert("end", f"{report['status']} · {report['verdict']} · 발견 {len(report['findings'])}건\n")
            for observation in report.get("observations", ()):
                display.insert("end", f"{observation['frame']} · {observation['visible']}\n")
            for unverified in report.get("not_verified", ()):
                display.insert("end", f"NOT_VERIFIED · {unverified}\n")
            if code := report.get("code_finding"):
                display.insert("end", f"코드 결함 · {code['file']} · {code['observed']}\n")
            for item in report["findings"]:
                display.insert("end", f"{item['id']} · {item['severity']} · {item['frame']} · {item['observed']}\n"
                                      f"기대 {item['expected']}\n재현 {item['reproduce']}\n수정 {item['fix']}\n"
                                      f"근거 {item['basis']} · 불확실성 {item['uncertainty']}\n")
            panel.visual_images = []
            for frame, data in zip(report["frames"], visual.read_frames(runner.ledger, report["frames"]), strict=True):
                picture = tk.PhotoImage(master=panel, data=b64encode(data))
                factor = max(1, (picture.width() + 639) // 640)
                picture = picture.subsample(factor) if factor > 1 else picture
                panel.visual_images.append(picture)
                display.insert("end", f"\n{frame['id']} · {frame['step']} · {frame['width']}×{frame['height']}\n")
                display.image_create("end", image=picture)
                display.insert("end", "\n")
    except (OSError, RuntimeError, ValueError, sqlite3.Error, tk.TclError) as error:
        display.insert("end", f"캡처 기록을 열 수 없습니다. {error}")
    display.configure(state="disabled")


def _session_layout(panel, state):
    width = min(740, panel.winfo_screenwidth() - 60)
    height = min(780, panel.winfo_screenheight() - 120)
    panel.geometry(f"{width}x{height}")
    panel.minsize(min(540, width), min(380, height))
    footer = ttk.Frame(panel)
    footer.pack(side="bottom", fill="x", pady=(8, 0))
    status = ttk.Label(footer, textvariable=state, wraplength=width - 40)
    status.pack(fill="x", pady=(0, 5))
    footer.bind("<Configure>", lambda event: status.configure(wraplength=max(250, event.width)))
    viewport = ttk.Frame(panel)
    viewport.pack(fill="both", expand=True)
    canvas = tk.Canvas(viewport, bg=panel.cget("bg"), highlightthickness=0, takefocus=True,
                       yscrollincrement=20)
    scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    body = ttk.Frame(canvas)
    embedded = canvas.create_window(0, 0, window=body, anchor="nw")
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(embedded, width=event.width))
    body.bind("<Configure>", lambda event: canvas.configure(scrollregion=(0, 0, event.width, event.height)))

    def wheel(event):
        widget = event.widget
        if widget is not canvas and not str(widget).startswith(f"{body}.") and widget is not body:
            return
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Combobox)):
            return
        if event.delta:
            direction = -1 if event.delta > 0 else 1
            canvas.yview_scroll(direction * max(1, abs(event.delta) // 120), "units")
            return "break"

    panel.bind("<MouseWheel>", wheel, add="+")
    canvas.bind("<Button-1>", lambda _event: canvas.focus_set())
    canvas.bind("<Prior>", lambda _event: canvas.yview_scroll(-1, "pages"))
    canvas.bind("<Next>", lambda _event: canvas.yview_scroll(1, "pages"))
    canvas.bind("<Home>", lambda _event: canvas.yview_moveto(0))
    canvas.bind("<End>", lambda _event: canvas.yview_moveto(1))
    return body, footer


def show(center, project, *, resume_only=False):
    runner = center.controller
    panel = center.panel()
    panel.title("등록 프로젝트 · Codex 개발 세션")
    state = tk.StringVar(panel, value="세션 검토를 누른 뒤 승인하면 자동 개발이 시작됩니다.")
    body, footer = _session_layout(panel, state)
    try:
        data = Catalog(runner.ledger).read()["projects"][project]["data"]
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError):
        state.set("프로젝트 등록 정보를 읽지 못했습니다. 프로젝트 목록을 새로고침하세요.")
        return
    criteria = cycle_progress.freeze(data["acceptance"], None)
    ttk.Label(body, text=f"{data['name']} · 완료 기준 {len(criteria)}개").pack(anchor="w")
    ttk.Label(body, text="등록 목표와 완료 기준으로 계획을 세웁니다. 요구사항 문서는 선택 사항입니다.",
              wraplength=580).pack(anchor="w")

    def show_criteria():
        viewer = center.panel()
        viewer.title("개발 목표와 완료 기준")
        content = center.editor_box(viewer, height=18)
        content.insert("1.0", data["goal"] + "\n\n" + "\n\n".join(
            f"{key} · {item['text']}" for key, item in criteria.items()))
        content.configure(state="disabled")

    ttk.Button(body, text="목표와 완료 기준 보기", command=show_criteria).pack(anchor="w", pady=(0, 4))
    baseline = None
    try:
        original, replica, baseline, _ = replica_readiness.baseline(runner.ledger, project)
        original_head = baseline["head"][:12] if baseline["head"] else "첫 커밋 전"
        ttk.Label(body, text=f"원본 {original}\n복제본 {replica}\n"
                  f"원본 Git {original_head} · 스테이징 {len(baseline['staged'])} · "
                  f"작업 트리 변경 {len(baseline['raw_worktree_changed'])}", wraplength=580).pack(fill="x")
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        state.set("작업 복제본이 없습니다. Git 온보딩에서 복제본을 먼저 준비하세요."
                  if str(error) == "An established Git onboarding baseline is required" else
                  f"작업 복제본을 확인할 수 없습니다. Git 온보딩에서 상태를 확인하세요.\n{error}")
    is_godot = baseline is not None and any(name.casefold() == "project.godot" for name in baseline["files"])
    draft = center.development_budget_drafts.setdefault(project, {})
    grants = _permission_checkboxes(body, draft)
    use_images = tk.BooleanVar(panel, value=False)
    ttk.Button(body, text="시각 검토 결과와 캡처 보기",
               command=lambda: _open_visual_report(center, runner, project)).pack(anchor="w")
    if is_godot and project == game_pilot.PROJECT and not resume_only:
        ttk.Checkbutton(body, text="AI 시각 검토 · 소유 테스트 창 캡처를 Codex에 전달",
                        variable=use_images).pack(anchor="w", pady=(0, 6))
        state.set("게임 개발 · 실행 범위 검토 또는 오프라인 검증")
        _game_preflight(center, panel, body, footer, runner, state, grants, use_images)
        return
    ttk.Button(body, text="등록 목표·기준 편집",
               command=lambda: (panel.destroy(), center.register_project(edit=True))).pack(anchor="w")
    inputs = ttk.Frame(body)
    inputs.pack(fill="x")
    entries = {}
    budget_keys = ("seconds", "tokens", "cycles", *ROLE_TOKEN_FIELDS.values())
    defaults = registered_cycle.recommended_limits()
    for index, (key, label, default) in enumerate((
            ("archive", "공식 Codex ZIP", ""), ("files", "수정 파일 경로 (쉼표 구분)", ""),
            ("seconds", "전체 시간 기준 (초)", str(defaults["seconds"])),
            ("tokens", "전체 토큰 기준", str(defaults["tokens"])), ("cycles", "사이클 상한 (선택)", ""),
            *((field, f"{ROLE_LABELS[role]} 호출당 토큰", str(defaults["role_tokens"][role]))
              for role, field in ROLE_TOKEN_FIELDS.items()))):
        ttk.Label(inputs, text=label).grid(row=index, column=0, sticky="w")
        entry = entries[key] = ttk.Entry(inputs)
        entry.insert(0, draft.get(key, default))
        entry.grid(row=index, column=1, sticky="ew")
    inputs.columnconfigure(1, weight=1)
    if baseline is not None and baseline["head"] is None and not baseline["files"]:
        entries["files"].insert(0, "docs/PROJECT-PLAN.md")
    ttk.Label(body, text="시간과 토큰은 새 작업 시작을 멈추는 기준입니다. 세션 승인 전에 적용값을 확인할 수 있습니다.",
              wraplength=580).pack(anchor="w", pady=4)
    ttk.Label(body, text="빈 역할 입력은 전체 토큰 기준 내에서 해당 역할의 기본값을 사용합니다. "
                         "독립 Verifier는 모델을 호출하지 않습니다.",
              wraplength=580).pack(anchor="w", pady=(0, 5))

    def save_budget():
        draft.update((key, entries[key].get()) for key in budget_keys)

    if baseline is None:
        def close_preflight():
            save_budget()
            panel.destroy()

        def reopen():
            close_preflight()
            show(center, project)

        ttk.Button(footer, text="Git 온보딩 · 작업 복제본 준비",
                   command=lambda: center.onboarding_panel(project=project)).pack(anchor="w")
        ttk.Button(footer, text="복제본 준비 상태 다시 확인", command=reopen).pack(anchor="w")
        ttk.Button(footer, text="닫기", command=close_preflight).pack(anchor="e")
        panel.protocol("WM_DELETE_WINDOW", close_preflight)
        panel.bind("<Escape>", lambda _event: (close_preflight(), "break")[1])
        return
    saved_archive = codex_setup.archive_path(runner.ledger)
    if saved_archive.is_file():
        entries["archive"].insert(0, str(saved_archive))

    def archive():
        path = filedialog.askopenfilename(parent=panel, filetypes=[("Codex ZIP", "*.zip")])
        if path:
            entries["archive"].delete(0, "end")
            entries["archive"].insert(0, path)

    ttk.Button(inputs, text="찾기", command=archive).grid(row=0, column=2)
    ttk.Label(body, text="독립 검증 설정").pack(anchor="w")
    ttk.Label(body, text="기본 검증은 원본 Git, 복제본과 변경 범위를 확인합니다. 입력·동작 검증은 실행 검사를 설정하세요.",
              wraplength=580).pack(anchor="w")
    ttk.Label(body, text="실행 검사에 screen_changed를 설정하면 소유 창의 입력 전후 화면을 비교합니다.",
              wraplength=580).pack(anchor="w")
    ttk.Checkbutton(body, text="AI 시각 검토 · 소유 테스트 창 캡처를 Codex에 전달",
                    variable=use_images).pack(anchor="w", pady=(0, 6))
    visual_hint = ttk.Label(body, wraplength=580)
    visual_hint.pack(fill="x")
    verifier_actions = ttk.Frame(body)
    verifier_actions.pack(fill="x")
    spec = center.editor_box(body, height=6)
    template = {"executable": "", "checks": {key: {"args": [], "stdout": "", "inputs": []} for key in criteria}}
    default_verifier = {"mode": registered_cycle.BASELINE_VERIFIER}
    godot_verifier = {"mode": game_pilot.REGISTERED_GUI}

    def write_spec(value):
        spec.delete("1.0", "end")
        spec.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2))

    def read_spec():
        try:
            value = json.loads(spec.get("1.0", "end"))
        except json.JSONDecodeError as error:
            raise ValueError(f"독립 검증 JSON의 {error.lineno}행 {error.colno}열을 확인하세요.") from error
        if not isinstance(value, dict):
            raise ValueError("독립 검증 설정은 JSON 객체로 입력하세요.")
        return value

    def update_visual_hint(_event=None):
        if not spec.edit_modified():
            return
        spec.edit_modified(False)
        try:
            configured = registered_cycle.visual_capture_configured(read_spec())
        except ValueError:
            visual_hint.configure(text="검증 설정 JSON을 확인하세요.")
            return
        visual_hint.configure(text="설정된 소유 창 검사의 화면을 Reviewer에게 전달합니다." if configured else
                              "화면 없이도 시작합니다. Developer가 실행 가능한 Windows 앱을 빌드하면 "
                              "소유 창의 시작 화면을 Reviewer가 확인합니다.")

    spec.bind("<<Modified>>", update_visual_hint, add="+")

    def choose_verifier():
        try:
            value = read_spec()
        except ValueError as error:
            state.set(str(error))
            return
        path = filedialog.askopenfilename(parent=panel, filetypes=[("검증 실행 파일", "*.exe")])
        if path:
            if value == default_verifier or registered_cycle.godot_verifier_spec(value):
                value = template.copy()
            value["executable"] = str(Path(path))
            write_spec(value)
            state.set("검증 도구를 선택했습니다. 기준별 실행 인자와 기대 출력을 확인하세요.")

    write_spec(godot_verifier if is_godot else default_verifier)

    def select_godot():
        directory = filedialog.askdirectory(parent=panel, mustexist=True, title="검증된 Godot 엔진과 템플릿의 상위 tools 폴더")
        if not directory:
            return
        write_spec({**godot_verifier, "tools_root": str(Path(directory))})
        if not is_godot and entries["files"].get().strip() == "docs/PROJECT-PLAN.md":
            entries["files"].delete(0, "end")
            entries["files"].insert(0, "project.godot, scenes/main.tscn, scripts/main.gd, docs/PROJECT-PLAN.md")
        state.set("Godot Windows 내보내기, 소유 창 클릭·키 입력, 입력 전후 캡처를 검토합니다.")

    def windows_text_check():
        from .mxc_launcher import _known_folder

        write_spec({"executable": str(_known_folder(0x24) / "System32" / "cmd.exe"),
                    "checks": {key: {"args": ["/d", "/q", "/c", "type", "message.txt"],
                                     "stdout": "Hello", "inputs": []} for key in criteria}})
        state.set("텍스트 읽기 예제를 불러왔습니다. 프로젝트에 맞는 검증 도구, 인자와 기대 출력으로 바꾸세요.")

    ttk.Button(verifier_actions, text="검증 도구 선택", command=choose_verifier).pack(side="left")
    ttk.Button(verifier_actions, text="기본 검증 사용",
               command=lambda: write_spec(godot_verifier if is_godot else default_verifier)).pack(side="left")
    ttk.Button(verifier_actions, text="Godot GUI 검증", command=select_godot).pack(side="left")
    ttk.Button(verifier_actions, text="Windows 텍스트 검사 예제", command=windows_text_check).pack(side="left", padx=4)
    detail = center.editor_box(body, height=8, state="disabled")
    cancelled = Event()
    future = None
    active = False

    def render():
        rows = registered_cycle.history(runner.ledger, project)
        roles = [row for row in rows if row["kind"] in {"native_inspection_role_intent", "native_inspection_role_result"}]
        end = next((row for row in reversed(rows) if row["kind"] == "native_inspection_result"), None)
        stop = next((row for row in rows if row["kind"] == "native_inspection_stop_requested"), None)
        lines = []
        for row in roles:
            usage = row.get("provider_usage")
            text = f"{row['cycle']} · {row['phase']} · {row.get('verdict', row.get('outcome', '실행 중'))}"
            if isinstance(usage, dict):
                text += f" · 입력 {usage['input_tokens']} / 출력 {usage['output_tokens']} / 캐시 {usage.get('cached_input_tokens', '?')}"
            if row.get("command_events"):
                text += f" · 도구 명령 {len(row['command_events'])}회"
            lines.append(text)
        if stop:
            lines.append(f"종료 예약 · {stop['reason']} · 현재 작업 마무리")
        for row in rows:
            if row["kind"] == "native_inspection_visual_review":
                if row["status"] == "NOT_VERIFIED" and not row["frames"]:
                    lines.append(f"{row['cycle']} · 시각 검토 미확인 · 캡처한 실행 화면 없음")
                    continue
                lines.append(f"{row['cycle']} · {row['status']} · {row['verdict']} · 발견 {len(row['findings'])}건")
                lines.extend(f"{item['severity']} · {item['frame']} · {item['observed']}" for item in row["findings"])
            elif row["kind"] == "native_inspection_visual_integrity":
                lines.append(f"{row['cycle']} · 캡처 무결성 · {row['result']}")
        if end:
            lines.extend((f"{end['outcome']} · {end['cycle_count']}회 · {end['known_reported_tokens']} 토큰",
                          f"변경: {', '.join(end['changed']) or '없음'}",
                          "기준: " + ", ".join(f"{key} {value}" for key, value in end["criteria_status"].items())))
        elif rows and not active:
            lines.append("미확정 실행 · 원장 및 복구 자료 확인 필요")
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", "\n".join(lines))
        detail.configure(state="disabled")

    def close():
        if active:
            cancelled.set()
            state.set("중단 요청됨 · 소유 자원 회수 대기")
        else:
            save_budget()
            panel.destroy()

    def submit(args, *, resume=False, soft_limits=None, graceful=False, visual_review=False):
        nonlocal future, active
        future = Future()
        active = True
        button.configure(state="disabled")
        resume_button.configure(state="disabled")
        state.set("승인 범위 확인 중")
        options = ({"graceful": graceful} if resume else
                   {"soft_limits": soft_limits, "visual_review": visual_review})
        review_fn = registered_cycle.review_resume if resume else registered_cycle.review
        run_fn = registered_cycle.run_resume if resume else registered_cycle.run

        def work():
            try:
                if not resume:
                    archive = codex_setup.archive_path(runner.ledger)
                    if archive.is_file() and Path(args[1]).absolute() == archive:
                        account = codex_setup.run(runner, "status", cancelled=cancelled.is_set)
                        if account["status"] != "CONNECTED":
                            raise RuntimeError("설정의 Codex 환경 및 로그인에서 ChatGPT 연결을 완료하세요.")
                if resume and not graceful:
                    registered_cycle.verify_resume_candidate(runner, project, cancelled=cancelled.is_set)
                future.set_result(review_fn(runner, *args, **options))
            except Exception as error:
                future.set_exception(error)

        def launch(scope):
            def run(activity, pause):
                return run_fn(runner, *args, approval=digest(scope), cancelled=cancelled.is_set,
                              activity=activity, pause=pause, **options)

            if center.start_registered_run(scope, run, cancelled):
                save_budget()
                panel.destroy()
            else:
                state.set("이미 자동 개발이 실행 중입니다. 개발 운영실에서 확인하세요.")

        def poll():
            nonlocal active
            try:
                render()
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                pass
            if not future.done():
                panel.after(400, poll)
                return
            active = False
            button.configure(state="normal")
            resume_button.configure(state="normal")
            error = future.exception()
            if error:
                state.set(str(error))
                return
            result = future.result()
            if cancelled.is_set():
                state.set("검토 취소됨")
                return
            if resume:
                checkpoint = result["resume"]
                if checkpoint.get("mode") == "next-cycle":
                    summary = (f"{project}\n복제본 {result['replica']}\n"
                               f"다음 사이클 {checkpoint['cycle'] + 1}부터 이어갑니다.\n"
                               f"남은 승인 예산 {result['seconds']:,}초 · {result['tokens']:,}토큰 · "
                               f"모델 최대 {result['model_calls_max']}회\n"
                               "기존 완료 기준, 파일 범위와 실행 권한을 사용합니다.")
                    if center.confirm("체크포인트에서 이어서 개발", summary, parent=panel):
                        launch(result)
                    else:
                        state.set("재개 승인 취소됨")
                    return
                summary = (f"{project}\n복제본 {result['replica']}\n"
                           f"재검토 대상: {', '.join(checkpoint['changed'])}\n"
                           f"기존 사용량 {checkpoint['parent_reported_tokens']:,}토큰\n"
                           f"추가 한도 {result['tokens']:,}토큰 / {result['seconds']}초\n"
                           f"Reviewer {result['role_tokens']['reviewer']:,} / "
                           f"최종 Manager {result['role_tokens']['manager-final']:,}토큰\n"
                           f"{_remaining_signoff(checkpoint)} · 모델 최대 {result['model_calls_max']}회. "
                           "Developer 재호출과 원본 반영은 없습니다.")
                if result.get("visual_review") and not checkpoint.get("review"):
                    summary += "\n소유 테스트 창 PNG 최대 4장을 Codex Reviewer에 전송합니다."
                if center.confirm("추가 예산 및 중단 작업 재개 승인", summary, parent=panel):
                    launch(result)
                else:
                    state.set("재개 승인 취소됨")
                return
            verifier = result["verifier"]
            checks = "\n".join(f"{key} · {row['args']} → {row['stdout']}"
                               for key, row in verifier.get("checks", {}).items())
            if verifier.get("mode") == registered_cycle.BASELINE_VERIFIER:
                verification = "기본 독립 검증 · 원본 Git과 복제본 변경 범위 확인. 기능 기준은 사용자 검증 필요.\n"
            elif verifier.get("mode") == game_pilot.REGISTERED_GUI:
                verification = (f"Godot 엔진 {verifier['executable']}\n"
                                "Windows EXE를 내보내 소유 창에 키 입력과 클릭을 보내고 입력 전후 화면을 캡처합니다.\n")
            else:
                verification = f"검증 EXE {verifier['executable']}\n{checks}\n"
            role_budget = " · ".join(f"{ROLE_LABELS[role]} {result['role_tokens'][role]:,}토큰"
                                     for role in ROLE_LABELS)
            summary = (f"{project}\n{result['goal']}\n복제본 {result['replica']}\n"
                       f"파일 {', '.join(result['files'])}\n"
                       "사전 권한 · " + " · ".join(LABELS[key] for key in result["permissions"]) + "\n"
                       f"{result['seconds']}초 / {result['tokens']} 토큰 / 사이클 {result['cycles'] or '예산까지'}\n"
                       f"{result['model']}\n{role_budget}\n"
                       f"{verification}"
                       "Developer는 MXC 안에서 복제본 쓰기와 셸 권한을 사용합니다. "
                       "Controller가 역할 종료 후 지정 파일 밖의 변경을 차단합니다. "
                       "승인된 프로젝트 문맥과 변경 근거가 모델에 전달됩니다. 명령 네트워크는 꺼져 있습니다.")
            if result.get("graceful_stop"):
                summary += (f"\n역할 초 {result['role_deadlines']}. 시간과 토큰은 소프트 기준입니다. "
                            "토큰은 호출 전 잔여량과 공급자 응답 후 사용량을 확인합니다. "
                            "마무리 최대 1,800초·300,000토큰. 역할 유예 최대 900초. "
                            "무응답 600초, 도구 실행 중 무응답 1,200초에서 소유 Job을 회수합니다.")
            if result.get("visual_review"):
                summary += ("\n캡처에 성공하면 소유 테스트 창의 PNG 최대 4장을 Codex Reviewer에 전송합니다. "
                            "검토는 Reviewer 토큰 기준에 포함됩니다.")
                if result.get("visual_preview"):
                    summary += ("\nDeveloper가 세션 임시 폴더에 빌드한 Windows 앱을 오프라인 샌드박스에서 실행해 "
                                "시작 화면을 캡처합니다. 화면이 준비되기 전에는 코드 검토를 계속합니다.")
            if center.confirm("등록 프로젝트 Codex 세션 승인", summary, parent=panel):
                launch(result)
            else:
                state.set("실행 승인 취소됨")

        Thread(target=work, daemon=False).start()
        panel.after(150, poll)

    def start():
        if active:
            return
        permissions = _checked_permissions(grants, state)
        if permissions is None:
            return
        try:
            verifier = read_spec()
            if verifier != default_verifier and not registered_cycle.godot_verifier_spec(verifier):
                if not isinstance(verifier.get("executable"), str) or not verifier["executable"].strip():
                    raise ValueError("검증 도구를 선택하거나 ‘기본 검증 사용’을 누르세요.")
                checks = verifier.get("checks")
                if not isinstance(checks, dict) or checks.keys() != criteria.keys():
                    raise ValueError("독립 검증 설정에 등록된 완료 기준별 검사 항목을 입력하세요.")
                missing = [key for key, check in checks.items()
                           if not isinstance(check, dict) or not isinstance(check.get("stdout"), str)
                           or not check["stdout"]]
                if missing:
                    remaining = f" 외 {len(missing) - 3}개" if len(missing) > 3 else ""
                    raise ValueError("독립 검증의 기대 출력을 입력하세요. " + ", ".join(missing[:3]) + remaining)
            values = {key: entry.get().strip() for key, entry in entries.items()}
            seconds, tokens = int(values["seconds"]), int(values["tokens"])
            cycles = int(values["cycles"]) if values["cycles"] else None
            role_tokens = {role: min(cap, tokens) for role, cap in defaults["role_tokens"].items()}
            for role, field in ROLE_TOKEN_FIELDS.items():
                if not values[field]:
                    continue
                try:
                    role_tokens[role] = int(values[field])
                except ValueError as error:
                    raise ValueError(f"{ROLE_LABELS[role]} 호출당 토큰은 정수로 입력하세요.") from error
            limits = {"seconds": seconds, "tokens": tokens, "cycles": cycles,
                      "role_seconds": {role: min(cap, seconds) for role, cap in defaults["role_seconds"].items()},
                      "role_tokens": role_tokens}
            registered_cycle.validate_limits(limits)
            args = (project, values["archive"], int(time()) + seconds + 2940, seconds, tokens, cycles,
                    [name.strip() for name in values["files"].split(",") if name.strip()],
                    permissions, verifier,
                    role_tokens["manager-planning"])
        except (RuntimeError, ValueError) as error:
            state.set(str(error))
            return
        cancelled.clear()
        submit(args, soft_limits=limits, visual_review=use_images.get())

    def resume():
        try:
            rows = registered_cycle.history(runner.ledger, project)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            state.set("저장된 개발 기록을 읽지 못했습니다. 기록·복구 화면에서 상태를 확인하세요.")
            return
        if rows and rows[-1].get("outcome") == registered_cycle.MANUAL_CHECKPOINT:
            seconds, tokens, _ = registered_cycle.manual_remaining(rows[0]["scope"], rows[-1])
            cancelled.clear()
            submit((project, int(time()) + seconds + 2940, seconds, tokens), resume=True, graceful=True)
            return
        if not rows or rows[-1].get("outcome") not in {"SIGNOFF_PENDING", "TOKEN_LIMIT"}:
            state.set("재개할 검토 단계가 없습니다. 중단 원인은 기록·복구 화면에서 확인하세요.")
            return
        graceful = rows[-1]["outcome"] == "SIGNOFF_PENDING"
        max_tokens = registered_cycle.MAX_SIGNOFF_TOKENS if graceful else 400000
        prior_limits = rows[0]["scope"]["role_tokens"]
        reserve = prior_limits["reviewer"] + prior_limits["manager-final"]
        dialog = tk.Toplevel(panel, padx=12, pady=12)
        dialog.title("재개 예산")
        dialog.transient(panel)
        values = {}
        for key, title, default in (("seconds", "추가 시간(초)", 300), ("tokens", "추가 토큰", reserve)):
            ttk.Label(dialog, text=title).pack(anchor="w")
            entry = values[key] = ttk.Entry(dialog)
            entry.insert(0, str(default))
            entry.pack(fill="x", pady=(0, 5))
        hint = ttk.Label(dialog, wraplength=350)
        hint.pack(anchor="w")

        def review():
            try:
                seconds, tokens = (int(values[key].get()) for key in ("seconds", "tokens"))
                if not 120 <= seconds <= (1800 if graceful else 1500) or not 1 <= tokens <= max_tokens:
                    raise ValueError
            except ValueError:
                hint.configure(text=f"추가 시간은 120초부터, 토큰은 1~{max_tokens:,} 사이로 입력하세요.")
                return
            args = (project, int(time()) + (4740 if graceful else 1700), seconds, tokens)
            dialog.destroy()
            cancelled.clear()
            submit(args, resume=True, graceful=graceful)

        ttk.Button(dialog, text="재개 범위 검토", command=review).pack(anchor="e")
        dialog.bind("<Return>", lambda _event: review())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        values["seconds"].focus_set()

    buttons = ttk.Frame(footer)
    buttons.pack(fill="x")
    button = ttk.Button(buttons, text="세션 검토 및 시작", command=start, style="Primary.TButton")
    resume_button = ttk.Button(buttons, text="중단 작업 재개 검토", command=resume)
    close_button = ttk.Button(buttons, text="닫기 / 중단", command=close)
    apply_button = ttk.Button(buttons, text="원본 반영 검토", command=lambda: center.original_apply_panel(project))
    for index, control in enumerate((button, resume_button, apply_button, close_button)):
        control.grid(row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2)
    for column in range(2):
        buttons.columnconfigure(column, weight=1, uniform="actions")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])
    render()
    if resume_only:
        panel.after_idle(resume)


def _game_preflight(center, panel, body, footer, runner, state, grants, use_images):
    cancelled = Event()
    future = None

    def close():
        if future is not None and not future.done():
            cancelled.set()
            state.set("중단 요청됨 · 소유 Job 회수 대기")
        else:
            panel.destroy()

    def start(export_windows=False, *, online=False, resume=False):
        nonlocal future
        visual_review = online and not resume and use_images.get()
        limits = None
        if online and not resume:
            if _checked_permissions(grants, state) is None:
                return
            try:
                limits = {"seconds": int(session_entries["seconds"].get()),
                          "tokens": int(session_entries["tokens"].get()),
                          "cycles": int(session_entries["cycles"].get()) if session_entries["cycles"].get().strip() else None,
                          **{name: {role: int(entry.get()) for role, entry in entries.items()}
                             for name, entries in role_entries.items()}}
                game_pilot.validate_limits(limits)
            except (RuntimeError, ValueError) as error:
                state.set(f"실행 한도 확인 · {error}")
                return
        archive = None
        if not resume:
            saved_archive = codex_setup.archive_path(runner.ledger)
            archive = (str(saved_archive) if saved_archive.is_file() else
                       filedialog.askopenfilename(parent=panel, filetypes=[("Codex ZIP", "*.zip")]))
        if not resume and not archive:
            return
        cancelled.clear()
        expires = int(time()) + (limits["seconds"] + 2940 if limits else 1750)
        resume_args = (game_pilot.PROJECT, expires, 360, 120000)
        graceful = False
        if resume:
            rows = registered_cycle.history(runner.ledger, game_pilot.PROJECT)
            if rows and rows[-1].get("outcome") == registered_cycle.MANUAL_CHECKPOINT:
                panel.destroy()
                show(center, game_pilot.PROJECT, resume_only=True)
                return
            if rows and rows[-1].get("reason") == "Manager plan exceeds the approved criteria":
                resume_args = (game_pilot.PROJECT, expires, *registered_cycle.game_plan_remaining(rows))
            elif rows and rows[0]["scope"].get("role_deadlines"):
                graceful = True
                resume_args = (game_pilot.PROJECT, int(time()) + 4740, 1800, 300000)

        def submit():
            nonlocal future
            future = Future()
            for control in (button, export_button, online_button, resume_button):
                control.configure(state="disabled")
            state.set("온라인 실행 범위 검토 중" if online else "오프라인 MXC 검사 실행 중")

            def worker():
                try:
                    if not online:
                        result = game_pilot.run(runner, archive, cancelled=cancelled.is_set, capture=True,
                                                export_windows=export_windows)
                    elif resume:
                        result = registered_cycle.review_resume(runner, *resume_args, graceful=graceful)
                    else:
                        result = registered_cycle.review_game(runner, archive, expires, limits,
                                                             visual_review=visual_review)
                    future.set_result(result)
                except Exception as error:
                    future.set_exception(error)

            def poll():
                if not future.done():
                    panel.after(150, poll)
                    return
                for control in (button, export_button, online_button, resume_button):
                    control.configure(state="normal")
                try:
                    result = future.result()
                    if online:
                        if cancelled.is_set():
                            state.set("검토 취소됨")
                            return
                        summary = (f"{result['project_id']}\n복제본 {result['replica']}\n"
                                   f"{result['seconds']:,}초 · {result['tokens']:,}토큰 · "
                                   f"사이클 {result['cycles'] if result['cycles'] is not None else '별도 상한 없음'}\n"
                                   f"{result['model']} · {result['role_tokens']}\n"
                                   f"호출당 초 · {result.get('role_deadlines') or {'developer': result.get('developer_seconds', 120), '기타': 120}}\n"
                                   f"편집 파일 {', '.join(result['files'])}\n"
                                   "Godot 및 템플릿 읽기, Developer 임시 폴더, 오프라인 셸, "
                                   "Windows 내보내기 및 소유 창 Verifier를 사용합니다. "
                                   "기존 에셋은 읽기 전용이며 명령 네트워크와 원본 반영은 꺼져 있습니다. "
                                   "플레이 동작과 시각 품질은 사용자 검증이 필요합니다. "
                                   "시간·토큰은 새 작업 시작을 멈추는 기준입니다. "
                                   "토큰은 호출 전 잔여량과 공급자 응답 후 사용량을 확인합니다.")
                        title = "게임 온라인 개발 1회 승인"
                        if result.get("candidate_base"):
                            title = "검증된 게임 후보에서 새 작업 승인"
                            base = result["candidate_base"]
                            summary += (f"\n후보 {base['source_sha256'][:12]} · 기존 변경 {len(base['changed'])}개. "
                                        f"이전 세션 결과 {base['parent_outcome']}를 보존합니다. "
                                        "검증된 파일을 기준으로 새 Manager 계획부터 시작합니다.")
                            if base.get("player_report"):
                                summary += "\n사용자 평가와 완주 여부를 Manager 계획에 전달합니다. 메모는 로컬에 저장합니다."
                        if not resume:
                            summary += "\n사전 권한 · " + " · ".join(LABELS[key] for key in result["permissions"])
                        if resume:
                            title = "게임 후보 검토 재개 승인"
                            if result["resume"].get("mode") == "after-manager-planning":
                                title = "게임 계획 이후 재개 승인"
                                summary += (f"\n저장된 계획을 재사용합니다. 첫 Developer부터 시작하며 "
                                            f"추가 모델 호출은 최대 {result['model_calls_max']}회입니다.")
                            else:
                                summary = (f"{result['replica']}\n추가 {result['seconds']:,}초 · {result['tokens']:,}토큰 · "
                                           f"모델 최대 {result['model_calls_max']}회\n"
                                           f"이전 후보의 {_remaining_signoff(result['resume'])}만 실행합니다. "
                                           f"Reviewer {result['role_tokens']['reviewer']:,} · "
                                           f"최종 Manager {result['role_tokens']['manager-final']:,}토큰. 복제본 편집은 꺼져 있습니다.")
                        if result.get("graceful_stop"):
                            policy = result["graceful_stop"]
                            summary += (f"\n시간·토큰 소프트 기준. 역할 초 {result['role_deadlines']}. "
                                        f"역할 유예 {policy['role_grace_seconds']:,}초, "
                                        f"무응답 {policy['idle_seconds']:,}초, 도구 실행 중 무응답 "
                                        f"{policy['tool_idle_seconds']:,}초, 마무리 {policy['signoff_seconds']:,}초. "
                                        f"마무리 토큰 {policy['signoff_tokens']:,}은 "
                                        f"{'세션 기준 잔여량 내' if policy['signoff_within_session_tokens'] else '별도 예산'}에서 사용합니다.")
                        if result.get("visual_review") and (not resume or not result["resume"].get("review")):
                            summary += "\n소유 테스트 창의 PNG 최대 4장을 Codex Reviewer에 전송합니다."
                        if center.confirm(title, summary, parent=panel):
                            def run(activity, pause):
                                if resume:
                                    return registered_cycle.run_resume(
                                        runner, *resume_args, approval=digest(result),
                                        cancelled=cancelled.is_set, graceful=graceful, activity=activity, pause=pause)
                                return registered_cycle.run_game(
                                    runner, archive, expires, limits, approval=digest(result),
                                    cancelled=cancelled.is_set, visual_review=visual_review, activity=activity, pause=pause)

                            if center.start_registered_run(result, run, cancelled):
                                panel.destroy()
                            else:
                                state.set("이미 자동 개발이 실행 중입니다. 개발 운영실에서 확인하세요.")
                        else:
                            state.set("온라인 실행 승인 취소됨")
                    elif export_windows:
                        detail = result.get("reason") or result.get("artifact_directory", result["export"])
                        state.set(f"Windows EXE 오프라인 검증 {result['outcome']} · {detail}")
                    else:
                        detail = result.get("reason") or (f"PNG {result.get('imported_textures', 0)}개 · "
                                                          f"PCK {'PASS' if result.get('resource_pack') else '미검증'} · "
                                                          f"{result['gui']}")
                        state.set(f"오프라인 사전 검증 {result['outcome']} · {detail}")
                except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
                    state.set(str(error))

            Thread(target=worker, daemon=False).start()
            panel.after(150, poll)

        submit()

    ttk.Label(body, text="등록 기준의 PNG 가져오기, 리소스 팩 실행, 소유 테스트 창 캡처를 검사합니다.",
              wraplength=580).pack(anchor="w")
    budget = ttk.Frame(body)
    budget.pack(fill="x", pady=4)
    defaults = game_pilot.recommended_limits()
    session_entries = {}
    role_entries = {key: {} for key in ("role_seconds", "role_tokens")}
    for row, (key, label) in enumerate((("seconds", "세션 초"), ("tokens", "세션 토큰"), ("cycles", "사이클 상한 (선택)"))):
        ttk.Label(budget, text=label).grid(row=row, column=0, sticky="w")
        entry = session_entries[key] = ttk.Entry(budget, width=12)
        entry.grid(row=row, column=1, sticky="w")
        if defaults[key] is not None:
            entry.insert(0, str(defaults[key]))
    ttk.Label(budget, text="역할 초").grid(row=3, column=1, sticky="w")
    ttk.Label(budget, text="역할 토큰").grid(row=3, column=2, sticky="w")
    for row, role in enumerate(defaults["role_seconds"], 4):
        ttk.Label(budget, text=ROLE_LABELS[role]).grid(row=row, column=0, sticky="w")
        for column, key in enumerate(role_entries, 1):
            entry = role_entries[key][role] = ttk.Entry(budget, width=12)
            entry.insert(0, str(defaults[key][role]))
            entry.grid(row=row, column=column, sticky="w")
    button = ttk.Button(body, text="게임 오프라인 사전 검증", command=start)
    button.pack(anchor="w")
    export_button = ttk.Button(body, text="Windows EXE 내보내기 및 오프라인 검증", command=lambda: start(True))
    export_button.pack(anchor="w")
    online_button = ttk.Button(footer, text="게임 온라인 개발 검토", command=lambda: start(online=True),
                               style="Primary.TButton")
    online_button.pack(side="left")
    resume_button = ttk.Button(footer, text="게임 중단 후보 재개 검토",
                               command=lambda: start(online=True, resume=True))
    resume_button.pack(side="left")
    ttk.Button(body, text="검증된 EXE · 사용자 플레이 기록",
               command=lambda: _player_panel(center, runner)).pack(anchor="w")
    ttk.Button(footer, text="닫기 / 중단", command=close).pack(side="right")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])


def _player_panel(center, runner):
    panel = center.panel()
    panel.title("Lantern Ruins · 사용자 플레이")
    state = tk.StringVar(value="")
    ttk.Label(panel, textvariable=state, wraplength=580).pack(anchor="w")
    try:
        events = runner.ledger.read()
        candidate = game_pilot.player_candidate(runner.ledger, events)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        state.set(str(error))
        return
    ttk.Label(panel, text=f"{candidate['executable']}\nSHA-256 {candidate['identity']['sha256']}",
              wraplength=580).pack(anchor="w")
    ttk.Label(panel, text="Enter 시작 · WASD/방향키 이동 · Space 대시 · R 재시작").pack(anchor="w")
    ratings = {}
    for key, title in zip(game_pilot.PLAYER_FEEDBACK_FIELDS, ("조작 이해도", "난이도", "화면 품질", "진행 흐름")):
        row = ttk.Frame(panel)
        row.pack(fill="x")
        ttk.Label(row, text=title, width=14).pack(side="left")
        rating = ratings[key] = tk.StringVar(value="미확인")
        ttk.Combobox(row, textvariable=rating, values=("미확인", "양호", "문제"), state="readonly").pack(side="left")
    finished = tk.BooleanVar()
    ttk.Checkbutton(panel, text="배포 EXE에서 승리까지 직접 플레이함", variable=finished).pack(anchor="w")
    ttk.Label(panel, text="직접 겪은 문제와 재현 방법 · 비공개 기록").pack(anchor="w")
    notes = center.editor_box(panel, height=4)
    labels = {"미확인": "NOT_TESTED", "양호": "OK", "문제": "ISSUE"}
    saved = game_pilot.latest_player_feedback(events, candidate["source_sha256"], candidate["run_id"])
    if saved:
        for label, value in labels.items():
            for key, previous in saved["ratings"].items():
                if previous == value:
                    ratings[key].set(label)
        finished.set(saved["finished"])
        notes.insert("1.0", saved["notes"])
    cancelled = Event()
    active = None

    def launch():
        nonlocal active
        active = Future()
        cancelled.clear()
        launch_button.configure(state="disabled")
        save_button.configure(state="disabled")
        state.set("플레이 중 · 최대 20분")

        def work():
            try:
                active.set_result(game_pilot.run_player_candidate(runner, candidate, cancelled=cancelled.is_set))
            except Exception as error:
                active.set_exception(error)

        def poll():
            if not active.done():
                panel.after(250, poll)
                return
            if cancelled.is_set():
                panel.destroy()
                return
            state.set(str(active.exception() or active.result()))
            launch_button.configure(state="normal")
            save_button.configure(state="normal")

        Thread(target=work, daemon=False).start()
        panel.after(250, poll)

    def save():
        try:
            game_pilot.save_player_feedback(runner, candidate, {k: labels[v.get()] for k, v in ratings.items()},
                                            notes.get("1.0", "end-1c").strip(), finished.get())
            state.set("사용자 관찰을 비공개 원장에 저장했습니다.")
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            state.set(str(error))

    def close():
        if active is not None and not active.done():
            cancelled.set()
            state.set("게임 종료 대기 중")
        else:
            panel.destroy()

    launch_button = ttk.Button(panel, text="검증된 EXE 실행", command=launch)
    launch_button.pack(side="left")
    save_button = ttk.Button(panel, text="사용자 플레이 기록 저장", command=save)
    save_button.pack(side="left")
    ttk.Button(panel, text="닫기", command=close).pack(side="left")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])
    panel.bind("<Destroy>", lambda event: cancelled.set() if event.widget is panel else None, add="+")
