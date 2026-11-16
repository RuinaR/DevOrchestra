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

from . import codex_setup, cycle_progress, development_profiles, game_pilot, registered_cycle, replica_readiness
from . import visual_review as visual
from .ai_usage import SUBSCRIPTION_MODE
from .catalog import Catalog
from .ledger import digest
from .local_folder import MODE as LOCAL_MODE


LABELS = {
    "replica-read": "작업 복제본 읽기", "replica-edit": "복제본 파일 작업",
    "offline-shell": "오프라인 빌드·테스트·명령 실행", "pinned-verifier": "독립 검증 실행",
    "web-research": "Codex 인터넷 자료 조사", "portable-tools": "필요한 도구 자동 설치",
}
ROLE_LABELS = {"manager-planning": "계획 Manager", "developer": "Developer",
               "reviewer": "Reviewer", "manager-final": "최종 Manager"}
ROLE_TOKEN_FIELDS = {"manager-planning": "manager_tokens", "developer": "developer_tokens",
                     "reviewer": "reviewer_tokens", "manager-final": "final_manager_tokens"}
BUDGET_FIELDS = ("seconds", "tokens", "cycles", *ROLE_TOKEN_FIELDS.values())
PLAY_FIELDS = {"play_inputs": "input_steps", "play_seconds": "seconds"}


def _scope_preferences(scope):
    verifier = scope["verifier"]
    if verifier.get("mode") in (registered_cycle.BASELINE_VERIFIER, game_pilot.REGISTERED_GUI):
        spec = {key: verifier[key] for key in ("mode", "tools_root") if key in verifier}
    elif "package" in verifier:
        package = verifier["package"]
        spec = {"package": {"archive": package["archive"], "entry": package["entry"],
                            "sha256": package["identity"]["sha256"]}, "checks": verifier["checks"]}
    elif {"executable", "checks"} <= verifier.keys():
        spec = {key: verifier[key] for key in ("executable", "checks")}
    else:
        spec = None
    values = {**{key: key in scope["permissions"] for key in LABELS},
              "whole_replica": bool(scope.get("project_edit")), "files": ", ".join(scope["files"]),
              "token_mode": scope.get("token_mode", "fixed"), "use_images": bool(scope.get("visual_review")),
              "use_play": bool(scope.get("adaptive_play")), "verifier": spec}
    for key in ("seconds", "tokens", "cycles"):
        if scope.get(key) is not None or key == "cycles":
            values[key] = str(scope[key]) if scope.get(key) is not None else ""
    for role, field in ROLE_TOKEN_FIELDS.items():
        if (amount := scope.get("role_tokens", {}).get(role)) is not None:
            values[field] = str(amount)
    if policy := scope.get("adaptive_play"):
        values.update((field, str(policy[key])) for field, key in PLAY_FIELDS.items() if key in policy)
        values["play_foreground"] = policy.get("foreground") is True
        if "capture_fps" in policy:
            values["capture_fps"] = str(policy["capture_fps"])
        if "evidence_mib" in policy:
            values["evidence_mib"] = str(policy["evidence_mib"])
        if "desktop_hold_ms" in policy:
            values["desktop_preference"] = True
            values["desktop_hold"] = str(policy["desktop_hold_ms"])
    values["approved_criterion"] = scope.get("approved_criterion")
    values["allow_documentation"] = scope.get("allow_documentation") is True
    if git := scope.get("direct_git"):
        values["direct_branch"] = git["ref"].removeprefix("refs/heads/")
    return values


def _permission_checkboxes(panel, draft, *, local=False):
    options = ttk.LabelFrame(panel, text="자동 개발 사전 권한", padding=6)
    options.pack(fill="x", pady=(4, 6))
    ttk.Label(options, text="일반 개발 권한은 기본 선택됩니다. 세션 시작 전 승인 화면에서 확인하세요.",
              wraplength=580).grid(row=0, column=0, columnspan=2, sticky="w")
    grants = {}
    for index, key in enumerate((*registered_cycle.PERMISSIONS, *registered_cycle.OPTIONAL_PERMISSIONS)):
        if local and key == "portable-tools":
            continue
        grants[key] = tk.BooleanVar(panel, value=draft.get(key, True) is True)
        ttk.Checkbutton(options, text=LABELS[key], variable=grants[key]).grid(
            row=1 + index // 2, column=index % 2, sticky="w")
    return grants


def _checked_permissions(grants, state):
    selected = [key for key, variable in grants.items() if variable.get()]
    missing = [LABELS[key] for key in registered_cycle.PERMISSIONS if key not in selected]
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
        rows = registered_cycle.history(runner.ledger, project, current_replica=True)
        last_play = next((row for row in reversed(rows) if row["kind"] == "native_inspection_play_started"), None)
        if last_play:
            play = [row for row in rows if row.get("cycle") == last_play["cycle"]]
            display.insert("end", f"AI 플레이 · {last_play['cycle']}사이클 · 기준 {last_play['target']}\n")
            for row in play:
                if row["kind"] == "native_inspection_play_action_result":
                    display.insert("end", f"{row['step']} · {row['action']} · {row['result']}\n")
                elif row["kind"] == "native_inspection_play_finished":
                    display.insert("end", f"플레이 종료 · {row['outcome']} · {row['elapsed_seconds']}초\n")
            frames = [row["frame"] for row in play if row["kind"] == "native_inspection_play_observation"]
            if frames:
                frame = frames[-1]
                data, = visual.read_frames(runner.ledger, [frame])
                picture = tk.PhotoImage(master=panel, data=b64encode(data))
                factor = max(1, (picture.width() + 639) // 640)
                panel.play_image = picture.subsample(factor) if factor > 1 else picture
                display.image_create("end", image=panel.play_image)
                display.insert("end", "\n")
        report = next((row for row in reversed(rows)
                       if row["kind"] == "native_inspection_visual_review"), None)
        if report is None:
            display.insert("end", "시각 검토 응답이 아직 없습니다.")
        else:
            display.insert("end", f"{report['status']} · {report['verdict']} · 발견 {len(report['findings'])}건\n")
            if report.get("review_backend") == "CODEX_CLI_IMAGE":
                display.insert("end", "검토 방식 · 소유 창 캡처를 Codex에 첨부\n")
            if report.get("input_source") == "CONTROLLER_SCENARIO":
                display.insert("end", "입력 출처 · Controller에 설정한 검사 시나리오\n")
            if report.get("autonomous_play") == "NOT_VERIFIED":
                display.insert("end", "자율 플레이 · 검증되지 않음\n")
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


def _play_approval(scope):
    if play := scope.get("adaptive_play"):
        inputs = (f"실제 입력 최대 {play['input_steps']}회. 대기·재관찰은 모델 호출에만 포함합니다. "
                  if "input_steps" in play else "")
        extra = "창 내부 스크롤과 제한된 크기 변경을 허용합니다. " if "scroll" in play["actions"] else ""
        if "text" in play["actions"]:
            extra += "표준 Windows 편집 컨트롤에 시험 문자열 입력을 허용합니다. "
        if "capture_fps" in play:
            extra += (f"Windows Graphics Capture 최대 {play['capture_fps']}FPS로 관찰마다 1초 동안 수집하고 "
                      "마지막 소유 창 프레임 하나만 사용합니다. ")
        if "evidence_mib" in play:
            extra += f"플레이 증거 상한은 사이클당 {play['evidence_mib']} MiB입니다. "
        if "desktop_hold_ms" in play:
            return (f"\n실제 Windows 입력 · 현재 실행의 소유 프리뷰 게임 창. 최대 {play['seconds']}초. {inputs}{extra}"
                    f"WASD·방향키·Space 중 최대 두 키를 {play['desktop_hold_ms']}ms 유지하고 왼쪽 클릭을 보냅니다. "
                    "전용 시험 환경을 권장합니다. 공유 데스크톱에서는 포커스가 바뀌는 순간 입력과 긴급 해제가 "
                    "다른 창으로 전달될 수 있으며 이미 전달된 입력은 취소할 수 없습니다. "
                    "포커스 상실·다른 입력 감지 또는 운영실의 ‘중단’ 요청 시 추가 입력을 멈추고 해제를 시도합니다. "
                    "Controller 자체가 강제 종료되면 해제를 보장하지 않습니다. 관찰 이미지는 Codex로 전송합니다.")
        foreground = ("전경 QA · 소유 HWND에 직접 클릭과 키 메시지를 보냅니다. "
                      "창이 전경에서 벗어나면 입력을 중단합니다. "
                      if play.get("foreground") else "")
        mouse = "창 내부 클릭" if foreground else "창 내부 클릭·이동"
        return (f"\nAI 플레이 · 사이클당 추가 모델 최대 {play['steps']}회, 소유 창 실행 최대 {play['seconds']}초. "
                f"{inputs}"
                f"{foreground}{mouse}과 600ms 이내 최대 3키 동시 입력을 허용합니다. "
                f"{extra}허용 키: {', '.join(play['keys'])}. 각 화면을 Codex에 전송합니다.")
    return ""


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


AUTOMATIC_CRITERION = "자동 선택"


def _criterion_approval(scope):
    key = scope.get("approved_criterion")
    if key is None:
        return ""
    return f"\n\n고정 완료 기준 · {key}\n{scope['criteria'][key]['text']}\n다른 기준이면 Developer 호출 전에 중단합니다.\n"


def show(center, project, *, resume_only=False, continue_progress=False):
    runner = center.controller
    panel = center.panel()
    panel.title("등록 프로젝트 · Codex 개발 세션")
    state = tk.StringVar(panel, value="세션 검토를 누른 뒤 승인하면 자동 개발이 시작됩니다.")
    if continue_progress:
        state.set("기존 복제본을 유지합니다. 새 개발 권한 또는 기존 승인으로 이어서 개발할 수 있습니다.")
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
    approved_criterion = tk.StringVar(panel, value=AUTOMATIC_CRITERION)
    if not resume_only:
        ttk.Label(body, text="완료 기준 고정 (선택)").pack(anchor="w")
        ttk.Combobox(body, textvariable=approved_criterion, state="readonly",
                     values=(AUTOMATIC_CRITERION, *criteria)).pack(fill="x")
    baseline = None
    try:
        original, replica, baseline, _ = replica_readiness.baseline(runner.ledger, project)
        if baseline.get("mode") == LOCAL_MODE:
            description = f"일반 폴더 · 원본 {original}\n개발 결과 저장 위치 {replica}\n원본 반영은 별도 승인"
        else:
            original_head = baseline["head"][:12] if baseline["head"] else "첫 커밋 전"
            description = (f"원본 {original}\n복제본 {replica}\n원본 Git {original_head} · "
                           f"스테이징 {len(baseline['staged'])} · 작업 트리 변경 {len(baseline['raw_worktree_changed'])}")
        ttk.Label(body, text=description, wraplength=580).pack(fill="x")
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        state.set("작업 복제본이 없습니다. Git 온보딩에서 복제본을 먼저 준비하세요."
                  if str(error) == "An established Git onboarding baseline is required" else
                  f"작업 복제본을 확인할 수 없습니다. Git 온보딩에서 상태를 확인하세요.\n{error}")

    def restart():
        panel.destroy()
        center.restart_registered_development(project)

    restart_button = ttk.Button(body, text="처음부터 다시 시작 · 새 복제본", command=restart)
    if baseline is not None:
        restart_button.pack(anchor="w", pady=(0, 4))
    local = bool(baseline and baseline.get("mode") == LOCAL_MODE)
    is_godot = baseline is not None and any(name.casefold() == "project.godot" for name in baseline["files"])
    progress = (center.view or {}).get("registered_progress") if (center.view or {}).get("project_id") == project else None
    prior = _scope_preferences(progress["scope"]) if progress else {}
    try:
        restored = development_profiles.read(runner.ledger, project)
    except (OSError, ValueError, RuntimeError, RecursionError):
        restored = None
        state.set("저장한 개발 설정을 읽지 못했습니다. 화면의 권한과 검증 설정을 다시 확인하세요.")
    draft = restored if restored is not None else prior.copy()
    saved_criterion = draft.get("approved_criterion")
    if isinstance(saved_criterion, str) and saved_criterion in criteria:
        approved_criterion.set(saved_criterion)
    if local:
        draft = {"web-research": False, "portable-tools": False, "cycles": "1", **draft}
    if restored is not None or prior:
        ttk.Label(body, text="저장한 설정을 불러왔습니다. 이미 승인한 실행의 권한은 그대로 유지됩니다.",
                  wraplength=580).pack(anchor="w")
    grants = _permission_checkboxes(body, draft, local=local)
    if not local:
        ttk.Label(body, text="자동 설치를 허용하면 필요한 공개 GitHub 도구나 소스를 작업 중 찾아 복제본에 준비합니다.",
                  wraplength=580).pack(anchor="w")
    use_images = tk.BooleanVar(panel, value=draft.get("use_images") is True)
    use_play = tk.BooleanVar(panel, value=draft.get("use_play") is True)
    play_foreground = tk.BooleanVar(panel, value=draft.get("play_foreground") is True)
    desktop_input = tk.BooleanVar(panel)
    desktop_preference = tk.BooleanVar(panel, value=draft.get("desktop_preference") is True)
    desktop_hold = tk.StringVar(panel, value=draft.get("desktop_hold", "1000"))
    capture_fps = tk.StringVar(panel, value=draft.get("capture_fps", ""))
    evidence_mib = tk.StringVar(panel, value=draft.get("evidence_mib", ""))
    direct_branch = tk.StringVar(panel, value="" if local else draft.get("direct_branch", ""))
    reboot_request = tk.StringVar(panel)
    auto_apply = tk.BooleanVar(panel)
    allow_documentation = tk.BooleanVar(panel, value=draft.get("allow_documentation") is True)
    if not resume_only:
        ttk.Checkbutton(body, text="문서 전용 개발 허용 선호 (실행 승인에서 확인)",
                        variable=allow_documentation).pack(anchor="w")
    spec = None
    play_entries = {}

    def play_option():
        ttk.Checkbutton(body, text="AI 플레이 · 모델이 소유 게임 창의 다음 행동을 선택",
                        variable=use_play,
                        command=lambda: use_images.set(True) if use_play.get() else None).pack(anchor="w")
        if is_godot and project == game_pilot.PROJECT and not resume_only:
            return
        ttk.Checkbutton(body, text="전경 QA · 승인된 소유 창에 클릭·키 메시지 직접 전달",
                        variable=play_foreground).pack(anchor="w")
        ttk.Checkbutton(body, text="실제 키보드·마우스 입력 선호 저장 (실행별 동의 필요)",
                        variable=desktop_preference).pack(anchor="w")
        ttk.Checkbutton(body, text="이번 실행에 실제 키보드·마우스 입력 허용 (공유 화면의 오입력 위험에 동의)",
                        variable=desktop_input,
                        command=lambda: play_foreground.set(True) if desktop_input.get() else None).pack(anchor="w")
        limits = ttk.Frame(body)
        limits.pack(fill="x")
        for field, title in (("play_inputs", "사이클당 AI 입력 상한 (1~64회)"),
                             ("play_seconds", "플레이 창 실행 시간 (60~1,800초)")):
            row = len(play_entries)
            ttk.Label(limits, text=title).grid(row=row, column=0, sticky="w", padx=(0, 8))
            entry = play_entries[field] = ttk.Entry(limits, width=12)
            entry.insert(0, draft.get(field, ""))
            entry.grid(row=row, column=1, sticky="w")
        ttk.Label(limits, text="빈 값은 기본값을 사용합니다. 저장된 세션은 기존 승인 한도를 유지합니다.").grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(limits, text="소유 창 고속 캡처 상한 (FPS)").grid(row=3, column=0, sticky="w")
        ttk.Combobox(limits, textvariable=capture_fps, values=("", "30", "60"),
                     state="readonly", width=12).grid(row=3, column=1, sticky="w")
        ttk.Label(limits, text="사이클당 플레이 증거 상한 (4~64 MiB)").grid(row=4, column=0, sticky="w")
        ttk.Entry(limits, textvariable=evidence_mib, width=12).grid(row=4, column=1, sticky="w")
        ttk.Label(limits, text="실제 키 최대 유지 시간 (30~1,000ms)").grid(row=5, column=0, sticky="w")
        ttk.Entry(limits, textvariable=desktop_hold, width=12).grid(row=5, column=1, sticky="w")
    ttk.Button(body, text="시각 검토 결과와 캡처 보기",
               command=lambda: _open_visual_report(center, runner, project)).pack(anchor="w")
    if is_godot and project == game_pilot.PROJECT and not resume_only:
        ttk.Checkbutton(body, text="AI 시각 검토 · 소유 테스트 창 캡처를 Codex에 전달",
                        variable=use_images).pack(anchor="w", pady=(0, 6))
        state.set("게임 개발 · 실행 범위 검토 또는 오프라인 검증")
        play_option()
        _game_preflight(center, panel, body, footer, runner, state, grants, use_images, use_play, restart_button,
                        draft, approved_criterion)
        return
    ttk.Button(body, text="등록 목표·기준 편집",
               command=lambda: save_budget() and (panel.destroy(), center.register_project(edit=True))).pack(anchor="w")
    if not local:
        from .gui_workspace import inspect_folder

        try:
            _, _, detected_branch, _ = inspect_folder(data["local_path"])
        except (OSError, ValueError, RuntimeError):
            detected_branch = ""
        ttk.Label(body, text="Git 자동 게시 · 비워 두면 작업 사본에 개발").pack(anchor="w")
        ttk.Entry(body, textvariable=direct_branch).pack(fill="x")
        if detected_branch:
            ttk.Button(body, text=f"현재 GitHub 브랜치 사용 · {detected_branch}",
                       command=lambda: direct_branch.set(detected_branch)).pack(anchor="w")
        elif not direct_branch.get():
            ttk.Label(body, text="지원되는 GitHub 원격과 브랜치를 자동으로 찾지 못했습니다. 작업 사본을 사용할 수 있습니다.",
                      wraplength=580).pack(fill="x")
    recovery = ttk.LabelFrame(body, text="실행 복구", padding=8)

    def toggle_section(frame, button, title):
        if frame.winfo_manager():
            frame.pack_forget()
            button.configure(text=f"{title} 보기")
        else:
            frame.pack(fill="x", after=button, pady=(0, 6))
            button.configure(text=f"{title} 닫기")

    recovery_button = ttk.Button(body, text="실행 복구 보기",
                                 command=lambda: toggle_section(recovery, recovery_button, "실행 복구"))
    if not local:
        recovery_button.pack(anchor="w", pady=(4, 6))
    ttk.Label(recovery, text="전체 재부팅 후 새 1사이클 · 미종결 계획 요청 ID").pack(anchor="w")
    ttk.Entry(recovery, textvariable=reboot_request).pack(fill="x", pady=(0, 4))

    def retry_git():
        from . import direct_git

        if center.confirm("Git 푸시 재시도", "원장에 기록된 로컬 커밋을 승인된 origin 브랜치로 다시 확인합니다.",
                          parent=panel):
            try:
                commit = direct_git.retry(runner, project)
            except (RuntimeError, OSError, ValueError) as error:
                state.set(str(error))
            else:
                state.set(f"원격 HEAD 확인 · {commit}")

    ttk.Button(recovery, text="미완료 Git 푸시 재시도", command=retry_git).pack(anchor="w")
    inputs = ttk.Frame(body)
    inputs.pack(fill="x")
    entries = {}
    whole = tk.BooleanVar(panel, value=draft.get("whole_replica", True) is True)
    subscription = tk.BooleanVar(panel, value=local or draft.get("token_mode") == SUBSCRIPTION_MODE)
    defaults = registered_cycle.recommended_limits()
    for index, (key, label, default) in enumerate((
            ("archive", "공식 Codex ZIP", ""), ("files", "지정 경로만 수정 (선택)", ""),
            ("seconds", "전체 시간 기준 (초)", str(defaults["seconds"])),
            ("tokens", "전체 토큰 기준", str(defaults["tokens"])), ("cycles", "사이클 상한 (선택)", ""),
            *((field, f"{ROLE_LABELS[role]} 호출당 토큰", str(defaults["role_tokens"][role]))
              for role, field in ROLE_TOKEN_FIELDS.items()))):
        ttk.Label(inputs, text=label).grid(row=index, column=0, sticky="w")
        entry = entries[key] = ttk.Entry(inputs)
        value = draft.get(key)
        entry.insert(0, value if isinstance(value, str) and key != "archive" else default)
        entry.grid(row=index, column=1, sticky="ew")
    inputs.columnconfigure(1, weight=1)

    def edit_mode():
        entries["files"].configure(state="disabled" if whole.get() else "normal")

    whole_label = ("개발 사본 전체 · 보호 경로를 제외한 파일 추가 및 수정" if local else
                   "복제본 전체 개발 · 소스·설정·에셋 생성, 수정 및 삭제")
    ttk.Checkbutton(inputs, text=whole_label,
                    variable=whole, command=edit_mode).grid(row=len(entries), column=0, columnspan=2, sticky="w")
    edit_mode()
    ttk.Label(inputs, text="복제본 전체 개발을 끄면 쉼표로 지정한 파일만 수정합니다. "
              "등록 요구사항과 독립 검증 입력, 원본 프로젝트는 보호됩니다.",
              wraplength=570).grid(row=len(entries) + 1, column=0, columnspan=2, sticky="w")
    if local:
        ttk.Checkbutton(inputs, text="독립 검증 완료 후 지정한 파일을 원본에 자동 반영",
                        variable=auto_apply).grid(row=len(entries) + 2, column=0, columnspan=2, sticky="w")
    def token_mode():
        for key in ("tokens", *ROLE_TOKEN_FIELDS.values()):
            entries[key].configure(state="disabled" if subscription.get() else "normal")

    modes = ttk.Frame(body)
    modes.pack(fill="x")
    if local:
        ttk.Label(modes, text="공식 Codex 구독 · 잔여량 10% 초과에서만 신규 작업 시작").pack(side="left")
    else:
        ttk.Radiobutton(modes, text="토큰 한도 지정", variable=subscription, value=False,
                        command=token_mode).pack(side="left")
        ttk.Radiobutton(modes, text="구독 잔여량 기반 개발", variable=subscription, value=True,
                        command=token_mode).pack(side="left", padx=8)
    token_mode()
    ttk.Label(body, text="시간과 토큰은 새 작업 시작을 멈추는 기준입니다. 세션 승인 전에 적용값을 확인할 수 있습니다.",
              wraplength=580).pack(anchor="w", pady=4)
    ttk.Label(body, text="빈 역할 입력은 전체 토큰 기준 내에서 해당 역할의 기본값을 사용합니다. "
                         "독립 Verifier는 모델을 호출하지 않습니다.",
              wraplength=580).pack(anchor="w", pady=(0, 5))

    def save_budget():
        preferences = {key: variable.get() for key, variable in grants.items()}
        preferences.update((key, entries[key].get()) for key in (*BUDGET_FIELDS, "files"))
        preferences.update(whole_replica=whole.get(), token_mode=SUBSCRIPTION_MODE if subscription.get() else "fixed",
                           use_images=use_images.get(), use_play=use_play.get(),
                           play_foreground=play_foreground.get(), direct_branch=direct_branch.get().strip(),
                           capture_fps=capture_fps.get(), evidence_mib=evidence_mib.get(),
                           desktop_preference=desktop_preference.get(), desktop_hold=desktop_hold.get().strip(),
                           approved_criterion=approved_criterion.get(),
                           allow_documentation=allow_documentation.get())
        preferences.update((field, entry.get().strip()) for field, entry in play_entries.items())
        if spec is not None:
            try:
                preferences["verifier"] = read_spec()
            except (ValueError, RecursionError):
                state.set("검증 설정이 올바르지 않아 저장하지 않았습니다.")
                return False
        elif isinstance(draft.get("verifier"), dict):
            preferences["verifier"] = draft["verifier"]
        try:
            development_profiles.save(runner.ledger, project, preferences)
        except (OSError, ValueError, RuntimeError, RecursionError):
            state.set("개발 설정을 저장하지 못했습니다. 사용자 저장소 쓰기 권한을 확인하세요.")
            return False
        return True

    restart_button.configure(command=lambda: save_budget() and restart())
    ttk.Button(body, text="프로젝트 설정 저장",
               command=lambda: save_budget() and state.set("현재 설정을 저장했습니다. 실행 승인에서 적용 범위를 확인하세요."))\
        .pack(anchor="w", pady=(0, 4))

    if baseline is None:
        def close_preflight():
            if save_budget():
                panel.destroy()

        def reopen():
            if save_budget():
                panel.destroy()
                show(center, project)

        ttk.Button(footer, text="처음부터 다시 시작", command=lambda: save_budget() and restart(),
                   style="Primary.TButton").pack(anchor="w")
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
    ttk.Label(body, text="기본 검증은 작업 사본과 변경 범위를 확인합니다. 기능 동작은 검증 계약에서 설정할 수 있습니다.",
              wraplength=580).pack(anchor="w", pady=(4, 0))
    ttk.Checkbutton(body, text="AI 시각 검토 · 소유 테스트 창 캡처를 Codex에 전달",
                    variable=use_images).pack(anchor="w", pady=(0, 6))
    play_option()
    advanced = ttk.LabelFrame(body, text="검증 계약", padding=8)
    advanced_button = ttk.Button(body, text="검증 계약과 실행 도구 보기",
                                 command=lambda: toggle_section(advanced, advanced_button, "검증 계약과 실행 도구"))
    advanced_button.pack(anchor="w", pady=(4, 6))
    visual_hint = ttk.Label(advanced, wraplength=570)
    visual_hint.pack(fill="x")
    verifier_actions = ttk.Frame(advanced)
    verifier_actions.pack(fill="x")
    spec = center.editor_box(advanced, height=6)
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

    saved_spec = draft.get("verifier")
    if isinstance(saved_spec, dict):
        write_spec(saved_spec)
        if ((registered_cycle.godot_verifier_spec(saved_spec) and not is_godot)
                or ("checks" in saved_spec and (not isinstance(saved_spec["checks"], dict)
                                               or not saved_spec["checks"].keys() <= criteria.keys()))):
            state.set("이전 검증 설정을 복원했습니다. 현재 프로젝트 구조와 완료 기준에 맞게 확인하세요.")
    else:
        write_spec(default_verifier)

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

    center.action_grid(verifier_actions, (("검증 도구 선택", choose_verifier),
                                         ("기본 검증 사용", lambda: write_spec(
                                             godot_verifier if is_godot else default_verifier)),
                                         ("Godot GUI 검증", select_godot),
                                         ("Windows 텍스트 검사 예제", windows_text_check)))
    detail = center.editor_box(body, height=8, state="disabled")
    cancelled = Event()
    future = None
    active = False

    def render():
        rows = registered_cycle.history(runner.ledger, project, current_replica=True)
        roles = [row for row in rows if row["kind"] in {"native_inspection_role_intent", "native_inspection_role_result"}]
        end = next((row for row in reversed(rows) if row["kind"] == "native_inspection_result"), None)
        stop = next((row for row in rows if row["kind"] == "native_inspection_stop_requested"), None)
        lines = []
        for row in roles:
            usage = row.get("provider_usage")
            result = ("모델 응답 완료" if row.get("outcome") == "COMPLETED" and row["phase"] != "verifier"
                      else row.get("verdict", row.get("outcome", "실행 중")))
            text = f"{row['cycle']} · {row['phase']} · {result}"
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
        elif save_budget():
            panel.destroy()

    def submit(args, *, resume=False, soft_limits=None, graceful=False, visual_review=False, play=False,
               direct_branch=None, reboot=None, criterion=None, apply_original=False):
        nonlocal future, active
        future = Future()
        active = True
        button.configure(state="disabled")
        resume_button.configure(state="disabled")
        restart_button.configure(state="disabled")
        state.set("승인 범위 확인 중")
        options = ({"graceful": graceful} if resume else
                   {"soft_limits": soft_limits, "visual_review": visual_review, "play": play,
                    "direct_branch": direct_branch, "reboot": reboot, "approved_criterion": criterion,
                    "auto_apply": apply_original, "allow_documentation": allow_documentation.get()})
        review_fn = registered_cycle.review_resume if resume else registered_cycle.review
        run_fn = registered_cycle.run_resume if resume else registered_cycle.run

        def work():
            try:
                if not resume and not reboot:
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

            if not save_budget():
                return
            if center.start_registered_run(scope, run, cancelled):
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
            restart_button.configure(state="normal")
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
                quota_mode = result.get("token_mode") == SUBSCRIPTION_MODE
                updated_roles = checkpoint.get("updated_role_instructions", ())
                instruction_note = ("\n새 승인부터 최신 개발 지침을 적용합니다 · "
                                    + ", ".join({"global": "전역", "verifier": "독립 Verifier"}.get(
                                        role, ROLE_LABELS.get(role, role)) for role in updated_roles)
                                    if updated_roles else "")
                instruction_note += _criterion_approval(result) + _play_approval(result)
                if "allow_documentation" in result:
                    instruction_note += ("\n문서 전용 개발: "
                                         + ("허용" if result["allow_documentation"] else "차단"))
                if result.get("development_mode") == LOCAL_MODE:
                    instruction_note += "\n재개 결과는 개발 사본에 저장합니다. 원본 반영은 다시 승인하세요."
                if checkpoint.get("mode") == "partial-candidate":
                    git = result["direct_git"]
                    evidence = checkpoint["staging_evidence"]
                    reconstruction = ""
                    if isinstance(evidence, dict):
                        edits = ", ".join(f"{edit['item']} · {edit['path']}" for edit in evidence["edits"])
                        if evidence.get("added"):
                            edits = ", ".join(f"{item['item']} · 신규 {path}" for path, item in evidence["added"].items())
                        reconstruction = (f"보존 파일 목록 SHA-256 {evidence['retained_files_sha256']}\n"
                                          f"성공 작업 {edits}\n"
                                          "출처 불일치 없음 · 전체 변경 바이트 일치\n")
                    memory = result.get("developer_memory")
                    memory_note = (f"후속 Developer Job 한도 {memory // 1024**2:,} MiB · 커밋 여유 확인 후 실행\n"
                                   if memory else "")
                    summary = (f"{project}\n{checkpoint['cycle']}사이클 미확정 부분 후보\n"
                               f"원본 {result['original']}\n{git['url']} · {git['ref']} · {git['head']}\n"
                               f"요청 {checkpoint['request_id']} · 호출 {checkpoint['role_call']}\n"
                               f"후보 SHA-256 {result['replica_sha256']}\n"
                               f"변경 파일: {', '.join(checkpoint['changed'])}\n"
                               f"{reconstruction}{memory_note}"
                               f"요구사항 {len(result['requirements'])}건 · 남은 시간 {result['seconds']:,}초\n"
                               "기존 Developer 응답·사용량은 미확정으로 보존합니다. "
                               "새 코드 Reviewer로 시작하고 독립 검증·최종 판정 뒤 Controller가 게시합니다. "
                               "검증이 남으면 그 상태를 기록하고, 기존 권한과 구독 기준으로 다음 개발을 진행합니다.")
                    if center.confirm("미확정 부분 후보 인수 승인", summary + instruction_note, parent=panel):
                        launch(result)
                    else:
                        state.set("부분 후보 인수 취소됨")
                    return
                if checkpoint.get("mode") == "postprocess":
                    role = ROLE_LABELS[checkpoint["saved_response"]["phase"]]
                    action = (f"저장된 {role} 응답의 판정을 확정할 수 없어 같은 후보로 {role}만 다시 호출합니다. "
                              if checkpoint.get("rerun_phase") else f"저장된 {role} 응답과 현재 후보를 사용합니다. ")
                    if checkpoint.get("unavailable_visual_response"):
                        action = ("저장된 시각 검토는 미검증으로 보존합니다. 같은 이미지를 다시 전송하지 않고 "
                                  "Reviewer가 코드를 별도로 검토합니다. ")
                    summary = (f"{project}\n복제본 {result['replica']}\n"
                               f"{action}"
                               f"{checkpoint['cycle']}사이클의 남은 판정부터 이어갑니다.\n"
                               f"이전 확정 사용량 {checkpoint['parent_reported_tokens']:,}토큰\n"
                               f"{'새 승인 예산' if checkpoint.get('renewed_budget') else '남은 예산'} "
                               f"{result['seconds']:,}초 · "
                               + ("구독 잔여량 기준\n" if quota_mode else
                                  f"{result['tokens']:,}토큰 · 모델 최대 {result['model_calls_max']}회\n") +
                               "승인 범위 내에서 판정을 마치고 필요하면 다음 사이클에서 재작업합니다.")
                    if checkpoint.get("renewed_budget"):
                        summary += f"\n이번 추가 승인은 {result['cycles']}사이클까지 유효합니다."
                    if center.confirm("보존된 모델 응답에서 이어서 개발", summary + instruction_note, parent=panel):
                        launch(result)
                    else:
                        state.set("재개 승인 취소됨")
                    return
                if checkpoint.get("mode") in {"next-cycle", "after-registered-planning"}:
                    summary = (f"{project}\n복제본 {result['replica']}\n"
                               f"다음 사이클 {checkpoint['cycle'] + 1}부터 이어갑니다.\n"
                               f"남은 승인 예산 {result['seconds']:,}초 · "
                               + ("구독 잔여량 기준\n" if quota_mode else
                                  f"{result['tokens']:,}토큰 · 모델 최대 {result['model_calls_max']}회\n") +
                               "기존 완료 기준, 파일 범위와 실행 권한을 사용합니다.")
                    if checkpoint["mode"] == "after-registered-planning":
                        summary += "\n저장된 Manager 계획을 사용해 Developer부터 실행합니다."
                    if center.confirm("체크포인트에서 이어서 개발", summary + instruction_note, parent=panel):
                        launch(result)
                    else:
                        state.set("재개 승인 취소됨")
                    return
                summary = (f"{project}\n복제본 {result['replica']}\n"
                           f"재검토 대상: {', '.join(checkpoint['changed'])}\n"
                           f"기존 사용량 {checkpoint['parent_reported_tokens']:,}토큰\n"
                           + (f"구독 잔여량 기준 / {result['seconds']}초\n" if quota_mode else
                              f"추가 한도 {result['tokens']:,}토큰 / {result['seconds']}초\n"
                              f"Reviewer {result['role_tokens']['reviewer']:,} / "
                              f"최종 Manager {result['role_tokens']['manager-final']:,}토큰\n") +
                           f"{_remaining_signoff(checkpoint)} · 모델 최대 {result['model_calls_max']}회. "
                           "Developer 재호출과 원본 반영은 없습니다.")
                if checkpoint.get("mode") == "review-size-stopped-candidate":
                    summary += ("\n이전 실행은 변경 후 파일 해시를 확정하기 전에 멈췄습니다. "
                                "지금 복제본의 파일을 새 후보로 승인하고 최초 등록 기준과 비교해 검토합니다.")
                if checkpoint.get("interrupted_preview"):
                    summary += ("\n미리보기 오류 전에 확정된 후보를 독립 검토합니다. 이전 임시 빌드와 자동 플레이는 "
                                "이 재개에 포함되지 않습니다. 새 실행 증거가 없으면 GUI는 미검증입니다.")
                if checkpoint.get("prior_play_usage_unknown"):
                    summary += "\n중단된 플레이 호출의 사용량은 미확정으로 남고, 그 호출은 재전송하지 않습니다."
                if result.get("visual_review") and not checkpoint.get("review"):
                    summary += "\n소유 테스트 창 PNG 최대 4장을 Codex Reviewer에 전송합니다."
                if center.confirm("추가 예산 및 중단 작업 재개 승인", summary + instruction_note, parent=panel):
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
            elif "package" in verifier:
                package = verifier["package"]
                verification = (f"검증 ZIP {package['archive']}\nSHA-256 {package['identity']['sha256']}\n"
                                f"실행 파일 {package['entry']}\n"
                                f"{checks}\n"
                                + "\n".join(f"{key} · {row['level']} / {row['coverage']} · "
                                            f"입력 {row['inputs']}" for key, row in verifier["checks"].items()) + "\n")
            else:
                verification = f"검증 EXE {verifier['executable']}\n{checks}\n"
            quota_mode = result.get("token_mode") == SUBSCRIPTION_MODE
            role_budget = ("구독 잔여량 기반 개발 · 10% 이하에서 종료 예약" if quota_mode else
                           " · ".join(f"{ROLE_LABELS[role]} {result['role_tokens'][role]:,}토큰"
                                      for role in ROLE_LABELS))
            token_label = "구독 잔여량 기준" if quota_mode else f"{result['tokens']:,} 토큰"
            edit_summary = ("복제본 전체에서 소스·설정·에셋을 생성·수정·삭제합니다. 보호된 파일은 제외합니다."
                            if result.get("project_edit") else "파일 " + ", ".join(result["files"]))
            summary = (f"{project}\n{result['goal']}\n복제본 {result['replica']}\n"
                       f"{edit_summary}\n"
                       "사전 권한 · " + " · ".join(LABELS[key] for key in result["permissions"]) + "\n"
                       f"{result['seconds']}초 / {token_label} / 사이클 {result['cycles'] or '예산까지'}\n"
                       f"{result['model']}\n{role_budget}\n"
                       f"{verification}"
                       "Developer는 MXC 안에서 복제본 쓰기와 셸 권한을 사용합니다. "
                       "Controller가 역할 종료 후 승인 범위와 보호 파일의 무결성을 확인합니다. "
                       "승인된 프로젝트 문맥과 변경 근거가 모델에 전달됩니다. 명령 네트워크는 꺼져 있습니다.")
            if result.get("research"):
                summary += ("\n계획 Manager와 Developer가 공식 Codex 검색으로 공개 자료를 조사합니다. "
                            "검색어는 Codex 제공자에게 전달됩니다.")
            if git := result.get("direct_git"):
                summary += (f"\n원본 직접 개발 · {result['original']}\n"
                            f"{git['url']} · {git['ref']} · 현재 HEAD {git['head'] or '첫 커밋 전'}\n"
                            "사이클마다 Controller가 확정 변경을 커밋·푸시합니다. "
                            "미검증 판정도 기록하며 원격 동기화 실패 시 다음 작업을 보류합니다.")
            if result.get("development_mode") == LOCAL_MODE:
                summary += "\n일반 폴더 자동 개발 · 결과와 체크포인트를 로컬에 보관합니다. 파일 삭제는 금지합니다."
                if result.get("auto_apply_original"):
                    summary += ("\n원본 쓰기 승인 포함 · 모든 기준의 독립 검증 완료와 정확한 원본 기준선이 "
                                "확인된 경우에만 지정 경로를 자동 반영합니다.")
                else:
                    summary += "\n원본 반영은 별도 승인합니다."
            if "developer_memory" in result:
                summary += f"\nDeveloper Windows Job 커밋 한도 {result['developer_memory'] // 1024**2:,} MiB"
            if result.get("portable_source_policy"):
                summary += ("\n필요할 때 공개 GitHub 릴리스 ZIP 또는 커밋이 고정된 소스 ZIP을 "
                            "복제본에 설치합니다. 명령 네트워크와 시스템 전체 설치는 꺼져 있습니다.")
            if result.get("tool_packages"):
                summary += "\n자동 설치 가능 도구 · 계획에서 선택한 항목만 복제본에 설치\n" + "\n".join(
                    f"{tool['name']} · {tool['url']}\nSHA-256 {tool['sha256']}" for tool in result["tool_packages"])
            if result.get("graceful_stop"):
                summary += (f"\n역할 초 {result['role_deadlines']}. "
                            + ("공식 구독 허용 여부와 잔여량을 확인합니다. 크레딧 사용 가능성이 있으면 호출을 보류합니다. "
                               "마무리 최대 1,800초. " if quota_mode else
                               "시간과 토큰은 소프트 기준입니다. "
                            "토큰은 호출 전 잔여량과 공급자 응답 후 사용량을 확인합니다. "
                            "마무리 최대 1,800초·300,000토큰. ") +
                            "역할 유예 최대 900초. "
                            "무응답 600초, 도구 실행 중 무응답 1,200초에서 소유 Job을 회수합니다.")
            if result.get("visual_review"):
                summary += ("\n캡처에 성공하면 소유 테스트 창의 PNG 최대 4장을 Codex Reviewer에 전송합니다. "
                            "검토는 Reviewer 토큰 기준에 포함됩니다.")
                if result.get("visual_preview"):
                    summary += ("\nDeveloper가 세션 임시 폴더에 빌드한 Windows 앱을 오프라인 샌드박스에서 실행해 "
                                "시작 화면을 캡처합니다. 화면이 준비되기 전에는 코드 검토를 계속합니다.")
            if candidate := result.get("prior_replica_candidate"):
                summary += "\n보존할 기존 복제본 변경 · " + ", ".join(candidate["changed"])
                if candidate.get("unsettled_candidate"):
                    summary += ("\n이전 Developer 응답 및 사용량은 미확정입니다. 현재 파일 해시를 새 개발 기준으로 "
                                "승인합니다. 기존 요청은 재전송하지 않으며 이전 기능 검증 결과를 승계하지 않습니다.")
                    if "candidate_plan" in result:
                        summary += "\n저장된 Manager 계획으로 새 Developer 작업을 시작합니다."
                elif "candidate_plan" in result and candidate.get("tool_plan"):
                    summary += "\n저장된 Manager 계획의 도구를 다시 준비한 뒤 Developer부터 시작합니다."
            if prior:
                current = _scope_preferences(result)
                names = {**LABELS, "whole_replica": "복제본 전체 개발", "files": "수정 경로",
                         "token_mode": "예산 모드", "use_images": "시각 검토", "use_play": "AI 플레이",
                         "verifier": "독립 검증", "seconds": "시간", "tokens": "세션 토큰", "cycles": "사이클 상한",
                         "play_inputs": "AI 입력 상한", "play_seconds": "플레이 시간",
                         **{field: f"{ROLE_LABELS[role]} 토큰" for role, field in ROLE_TOKEN_FIELDS.items()}}
                changes = [label for key, label in names.items() if prior.get(key) != current.get(key)]
                if changes:
                    summary += "\n이전 승인과 달라진 설정 · " + " · ".join(changes)
            if boundary := result.get("reboot_boundary"):
                boot = boundary["boot"]
                summary += (f"\n\n전체 재부팅 이후 새 실행 · 부팅 {boot['started']} (Unix 초)"
                            f" · Windows 기록 {boot['start_record']}/{boot['type_record']}"
                            f"\n기존 요청 {boundary['old_request_id']} · 응답·사용량 UNKNOWN 유지"
                            f"\n새 실행 {digest(boundary)[:32]} · 원장 Seq {boundary['ledger_sequence']}"
                            f"\n원장 SHA-256 {boundary['ledger_head']}"
                            f"\n체크아웃 {boundary['checkout']} · {boundary['direct_git']['ref']}"
                            f"\nGit 시작 HEAD {boundary['direct_git']['head']}"
                            f"\n승인 만료 {boundary['expires']} (Unix 초)")
            if center.confirm("등록 프로젝트 Codex 세션 승인",
                              _criterion_approval(result) + summary + _play_approval(result), parent=panel):
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
                if ("package" not in verifier and (not isinstance(verifier.get("executable"), str)
                                                   or not verifier["executable"].strip())):
                    raise ValueError("검증 도구를 선택하거나 ‘기본 검증 사용’을 누르세요.")
                checks = verifier.get("checks")
                if not isinstance(checks, dict) or not checks or not checks.keys() <= criteria.keys():
                    raise ValueError("독립 검증 설정에 등록된 완료 기준의 검사 항목을 하나 이상 입력하세요.")
                missing = [key for key, check in checks.items()
                           if not isinstance(check, dict) or not isinstance(check.get("stdout"), str)
                           or not check["stdout"]]
                if missing:
                    remaining = f" 외 {len(missing) - 3}개" if len(missing) > 3 else ""
                    raise ValueError("독립 검증의 기대 출력을 입력하세요. " + ", ".join(missing[:3]) + remaining)
            values = {key: entry.get().strip() for key, entry in entries.items()}
            if local and not values["archive"]:
                raise ValueError("설정에서 공식 Codex를 연결하거나 검증된 Codex ZIP을 선택하세요.")
            seconds = int(values["seconds"])
            tokens = None if subscription.get() else int(values["tokens"])
            cycles = int(values["cycles"]) if values["cycles"] else None
            role_tokens = (dict.fromkeys(ROLE_LABELS) if subscription.get() else
                           {role: min(cap, tokens) for role, cap in defaults["role_tokens"].items()})
            for role, field in (() if subscription.get() else ROLE_TOKEN_FIELDS.items()):
                if not values[field]:
                    continue
                try:
                    role_tokens[role] = int(values[field])
                except ValueError as error:
                    raise ValueError(f"{ROLE_LABELS[role]} 호출당 토큰은 정수로 입력하세요.") from error
            limits = {"seconds": seconds, "tokens": tokens, "cycles": cycles,
                      "role_seconds": {role: min(cap, seconds) for role, cap in defaults["role_seconds"].items()},
                      "role_tokens": role_tokens}
            if subscription.get():
                limits["token_mode"] = SUBSCRIPTION_MODE
            registered_cycle.validate_limits(limits)
            files = [] if whole.get() else [name.strip() for name in values["files"].split(",") if name.strip()]
            if not whole.get() and not files:
                raise ValueError("수정할 파일을 입력하거나 ‘복제본 전체 개발’을 선택하세요.")
            if auto_apply.get() and not files:
                raise ValueError("원본에 자동 반영하려면 수정할 파일의 경로를 지정하세요.")
            args = (project, values["archive"], int(time()) + seconds + 2940, seconds, tokens, cycles,
                    files, permissions, verifier, role_tokens["manager-planning"])
            play = use_play.get()
            if desktop_input.get() and not play:
                raise ValueError("실제 입력을 허용하려면 AI 플레이를 선택하세요.")
            if play and (play_foreground.get() or desktop_input.get() or capture_fps.get() or evidence_mib.get().strip()
                         or any(entry.get().strip() for entry in play_entries.values())):
                from . import adaptive_play
                default = adaptive_play.approval(godot=registered_cycle.godot_verifier_spec(verifier))
                try:
                    play = {key: int(play_entries[field].get().strip() or default[key])
                            for field, key in PLAY_FIELDS.items()}
                except ValueError as error:
                    raise ValueError("AI 플레이 입력 횟수와 실행 시간은 정수로 입력하세요.") from error
                rate = int(capture_fps.get()) if capture_fps.get() else None
                try:
                    cap = int(evidence_mib.get().strip()) if evidence_mib.get().strip() else None
                    desktop_max = int(desktop_hold.get().strip()) if desktop_input.get() else None
                except ValueError as error:
                    raise ValueError("플레이 증거 상한과 키 유지 시간은 정수로 입력하세요.") from error
                adaptive_play.approval(limits=play, foreground=play_foreground.get(), capture_fps=rate,
                                       evidence_mib=cap, desktop_hold_ms=desktop_max)
                if rate is not None:
                    play["capture_fps"] = rate
                if cap is not None:
                    play["evidence_mib"] = cap
                if desktop_max is not None:
                    play["desktop_hold_ms"] = desktop_max
                if play_foreground.get():
                    play["foreground"] = True
        except (RuntimeError, ValueError) as error:
            state.set(str(error))
            return
        cancelled.clear()
        reboot = reboot_request.get().strip()
        criterion = approved_criterion.get()
        submit(args, soft_limits=limits, visual_review=use_images.get(), play=play,
               direct_branch=direct_branch.get().strip() or None, reboot=(reboot, int(time()) + 900) if reboot else None,
               criterion=None if criterion == AUTOMATIC_CRITERION else criterion, apply_original=auto_apply.get())

    def resume():
        try:
            rows = registered_cycle.history(runner.ledger, project, current_replica=True)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            state.set("저장된 개발 기록을 읽지 못했습니다. 기록·복구 화면에서 상태를 확인하세요.")
            return
        if rows and rows[0]["scope"].get("token_mode") == SUBSCRIPTION_MODE:
            end = rows[-1]
            seconds = 1800 if (end.get("outcome") == "SIGNOFF_PENDING"
                               or registered_cycle._interrupted_preview(rows)
                               or registered_cycle._interrupted_play(rows)) else registered_cycle.manual_remaining(
                rows[0]["scope"], end)[0]
            if seconds < 60:
                state.set("남은 시간이 부족합니다. 보존된 후보에서 새 세션을 검토하세요.")
                return
            cancelled.clear()
            submit((project, int(time()) + seconds + 2940, seconds, None), resume=True, graceful=True)
            return
        if rows and (registered_cycle.can_review_postprocess(rows[-1]) or registered_cycle.can_review_size_stop(rows[-1])):
            prior, result = rows[0]["scope"], rows[-1]
            size_recovery = registered_cycle.can_review_size_stop(result)
            seconds, tokens, calls = registered_cycle.manual_remaining(prior, result)
            dialog = tk.Toplevel(panel, padx=12, pady=12)
            dialog.title("현재 후보 검토 재개 예산" if size_recovery else "보존된 모델 응답 재개 예산")
            dialog.transient(panel)
            ttk.Label(dialog, text=f"기존 잔여 {max(0, seconds):,}초 · {max(0, tokens):,}토큰 · "
                      f"모델 최대 {max(0, calls)}회").pack(anchor="w", pady=(0, 8))
            limits = {}
            suggested_tokens = (prior["role_tokens"]["reviewer"] + prior["role_tokens"]["manager-final"]
                                if size_recovery else sum(prior["role_tokens"].values()) + prior["role_tokens"]["manager-final"])
            minimum_tokens = suggested_tokens if size_recovery else max(prior["role_tokens"].values())
            for field, title, default in (
                    ("seconds", "새 시간 예산(초)", 1800),
                    ("tokens", "새 토큰 예산", suggested_tokens)):
                ttk.Label(dialog, text=title).pack(anchor="w")
                entry = limits[field] = ttk.Entry(dialog)
                entry.insert(0, str(min(default, registered_cycle.MAX_SESSION_TOKENS)))
                entry.pack(fill="x")
            hint = ttk.Label(dialog, wraplength=460)
            hint.pack(fill="x")

            def review(renew=False):
                try:
                    use_seconds, use_tokens = ((int(limits[key].get()) for key in ("seconds", "tokens"))
                                               if renew else (seconds, tokens))
                    if renew and not (60 <= use_seconds <= registered_cycle.MAX_SESSION_SECONDS
                                      and minimum_tokens <= use_tokens
                                      <= registered_cycle.MAX_SESSION_TOKENS):
                        raise ValueError
                except ValueError:
                    hint.configure(text=f"시간과 토큰을 확인하세요. 최소 {minimum_tokens:,}토큰이 필요합니다.")
                    return
                args = (project, int(time()) + use_seconds + 2940, use_seconds, use_tokens, renew)
                dialog.destroy()
                cancelled.clear()
                submit(args, resume=True, graceful=True)

            ttk.Button(dialog, text="남은 예산으로 재개 검토", command=review,
                       state="normal" if min(seconds, tokens, calls) >= 0 else "disabled").pack(fill="x", pady=(6, 0))
            ttk.Button(dialog, text="새 예산으로 재개 검토", command=lambda: review(True)).pack(fill="x")
            dialog.bind("<Escape>", lambda _event: dialog.destroy())
            dialog.grab_set()
            return
        if rows and (rows[-1].get("outcome") == registered_cycle.MANUAL_CHECKPOINT
                     or registered_cycle.can_review_registered_plan(rows[-1])
                     or registered_cycle._recoverable_pre_developer_stop(rows)):
            try:
                seconds, tokens, _ = (registered_cycle.registered_plan_remaining(runner.ledger, rows)
                                      if registered_cycle.can_review_registered_plan(rows[-1]) else
                                      registered_cycle.manual_remaining(rows[0]["scope"], rows[-1]))
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                state.set(str(error))
                return
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
    button = ttk.Button(buttons, text="새 권한으로 이어서 개발" if continue_progress else "세션 검토 및 시작",
                        command=start, style="Primary.TButton")
    resume_button = ttk.Button(buttons, text="기존 권한으로 재개" if continue_progress else "중단 작업 재개 검토",
                               command=resume)
    close_button = ttk.Button(buttons, text="닫기 / 중단", command=close)
    apply_button = ttk.Button(buttons, text="원본 반영 검토", command=lambda: center.original_apply_panel(project))
    if not resume_only:
        def independent_play():
            from .gui_preview_play import show as show_preview_play

            show_preview_play(center, project, {
                "capture_fps": capture_fps.get(), "evidence_mib": evidence_mib.get(),
                "play_seconds": play_entries["play_seconds"].get() if play_entries else "",
                "play_inputs": play_entries["play_inputs"].get() if play_entries else "",
                "desktop_hold": desktop_hold.get(), "desktop_preference": desktop_preference.get(),
            })

        ttk.Button(footer, text="독립 GUI 입력 검증 · 모델 호출 없음",
                   command=independent_play).pack(fill="x", pady=(4, 0))
    for index, control in enumerate((button, resume_button, apply_button, close_button)):
        control.grid(row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2)
    for column in range(2):
        buttons.columnconfigure(column, weight=1, uniform="actions")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])
    render()
    if resume_only:
        panel.after_idle(resume)


def _game_preflight(center, panel, body, footer, runner, state, grants, use_images, use_play, restart_button, draft,
                    approved_criterion):
    cancelled = Event()
    future = None

    def save():
        values = {key: draft[key] for key in ("whole_replica", "files", "token_mode", "verifier") if key in draft}
        values.update((key, variable.get()) for key, variable in grants.items())
        values.update((key, entry.get()) for key, entry in session_entries.items())
        values.update((field, role_entries["role_tokens"][role].get()) for role, field in ROLE_TOKEN_FIELDS.items())
        values.update(use_images=use_images.get(), use_play=use_play.get(),
                      game_role_seconds={role: entry.get() for role, entry in role_entries["role_seconds"].items()})
        try:
            development_profiles.save(runner.ledger, game_pilot.PROJECT, values)
        except (OSError, ValueError, RuntimeError, RecursionError):
            state.set("개발 설정을 저장하지 못했습니다. 사용자 저장소 쓰기 권한을 확인하세요.")
            return False
        return True

    def close():
        if future is not None and not future.done():
            cancelled.set()
            state.set("중단 요청됨 · 소유 Job 회수 대기")
        elif save():
            panel.destroy()

    def start(export_windows=False, *, online=False, resume=False):
        nonlocal future
        selected = approved_criterion.get()
        criterion = None if selected == AUTOMATIC_CRITERION else selected
        visual_review = online and not resume and use_images.get()
        play = online and not resume and use_play.get()
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
            rows = registered_cycle.history(runner.ledger, game_pilot.PROJECT, current_replica=True)
            if rows and (rows[-1].get("outcome") == registered_cycle.MANUAL_CHECKPOINT
                         or registered_cycle.can_review_postprocess(rows[-1])):
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
            for control in (button, export_button, online_button, resume_button, restart_button):
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
                                                             visual_review=visual_review, play=play,
                                                             approved_criterion=criterion)
                    future.set_result(result)
                except Exception as error:
                    future.set_exception(error)

            def poll():
                if not future.done():
                    panel.after(150, poll)
                    return
                for control in (button, export_button, online_button, resume_button, restart_button):
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
                        if resume and (updated_roles := result["resume"].get("updated_role_instructions")):
                            summary += "\n새 승인부터 최신 개발 지침을 적용합니다 · " + ", ".join(
                                "전역" if role == "global" else ROLE_LABELS[role] for role in updated_roles)
                        if result.get("visual_review") and (not resume or not result["resume"].get("review")):
                            summary += "\n소유 테스트 창의 PNG 최대 4장을 Codex Reviewer에 전송합니다."
                        if center.confirm(title, _criterion_approval(result) + summary + _play_approval(result),
                                          parent=panel):
                            if not save():
                                return
                            def run(activity, pause):
                                if resume:
                                    return registered_cycle.run_resume(
                                        runner, *resume_args, approval=digest(result),
                                        cancelled=cancelled.is_set, graceful=graceful, activity=activity, pause=pause)
                                return registered_cycle.run_game(
                                    runner, archive, expires, limits, approval=digest(result),
                                    cancelled=cancelled.is_set, visual_review=visual_review, activity=activity,
                                    pause=pause, play=play, approved_criterion=criterion)

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
        value = draft.get(key, defaults[key])
        if value is not None:
            entry.insert(0, str(value))
    ttk.Label(budget, text="역할 초").grid(row=3, column=1, sticky="w")
    ttk.Label(budget, text="역할 토큰").grid(row=3, column=2, sticky="w")
    for row, role in enumerate(defaults["role_seconds"], 4):
        ttk.Label(budget, text=ROLE_LABELS[role]).grid(row=row, column=0, sticky="w")
        for column, key in enumerate(role_entries, 1):
            entry = role_entries[key][role] = ttk.Entry(budget, width=12)
            if key == "role_seconds":
                saved_seconds = draft.get("game_role_seconds")
                saved = saved_seconds.get(role) if isinstance(saved_seconds, dict) else None
            else:
                saved = draft.get(ROLE_TOKEN_FIELDS[role])
            entry.insert(0, saved if isinstance(saved, str) and saved else str(defaults[key][role]))
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
