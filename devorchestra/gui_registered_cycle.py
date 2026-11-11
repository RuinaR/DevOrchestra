"""Command Center entry point for an owner-approved registered Codex session."""
from concurrent.futures import Future
import json
import sqlite3
from threading import Event, Thread
from time import time
import tkinter as tk
from tkinter import filedialog, ttk

from . import codex_setup, cycle_progress, game_pilot, registered_cycle, replica_readiness
from .catalog import Catalog
from .ledger import digest


LABELS = {
    "replica-read": "복제본 읽기", "replica-edit": "지정 파일 생성·수정",
    "offline-shell": "복제본의 오프라인 셸", "pinned-verifier": "지정 검증 명령",
}


def _remaining_signoff(checkpoint):
    if checkpoint.get("comparison") is not None:
        return "최종 Manager"
    if checkpoint.get("review") is not None:
        return "독립 Verifier → 최종 Manager"
    return "Reviewer → 독립 Verifier → 최종 Manager"


def show(center, project):
    runner = center.controller
    panel = center.panel()
    panel.title("등록 프로젝트 · Codex 개발 세션")
    state = tk.StringVar(value="등록된 복제본과 검증 조건을 확인하세요.")
    ttk.Label(panel, textvariable=state, wraplength=580).pack(fill="x", pady=4)
    data = Catalog(runner.ledger).read()["projects"][project]["data"]
    ttk.Label(panel, text=f"{data['name']} · {data['goal']}", wraplength=580).pack(fill="x")
    try:
        original, replica, baseline, _ = replica_readiness.baseline(runner.ledger, project)
        ttk.Label(panel, text=f"원본 {original}\n복제본 {replica}\n"
                  f"원본 Git {baseline['head'][:12]} · 스테이징 {len(baseline['staged'])} · "
                  f"작업 트리 변경 {len(baseline['raw_worktree_changed'])}", wraplength=580).pack(fill="x")
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        state.set(str(error))
        ttk.Button(panel, text="Git 온보딩", command=lambda: center.onboarding_panel(project)).pack(anchor="w")
        return

    criteria = cycle_progress.freeze(data["acceptance"], None)
    for key, item in criteria.items():
        ttk.Label(panel, text=f"{key} · {item['text']}", wraplength=580).pack(anchor="w")
    if any(name.casefold() == "project.godot" for name in baseline["files"]):
        state.set("게임 개발 · 실행 범위 검토 또는 오프라인 검증")
        if project == game_pilot.PROJECT:
            _game_preflight(center, panel, runner, state)
        return
    ttk.Button(panel, text="등록 목표·기준 편집",
               command=lambda: (panel.destroy(), center.register_project(edit=True))).pack(anchor="w")
    inputs = ttk.Frame(panel)
    inputs.pack(fill="x")
    entries = {}
    for index, (key, label, default) in enumerate((
            ("archive", "공식 Codex ZIP", ""), ("files", "수정 파일 경로 (쉼표 구분)", ""),
            ("seconds", "세션 초", str(game_pilot.SESSION_SECONDS)),
            ("tokens", "세션 토큰", str(game_pilot.SESSION_TOKENS)), ("cycles", "사이클 상한 (선택)", ""),
            ("manager_tokens", "Manager 토큰 / 호출", str(game_pilot.ROLE_TOKENS["manager-planning"])))):
        ttk.Label(inputs, text=label).grid(row=index, column=0, sticky="w")
        entry = entries[key] = ttk.Entry(inputs)
        entry.insert(0, default)
        entry.grid(row=index, column=1, sticky="ew")
    inputs.columnconfigure(1, weight=1)
    saved_archive = codex_setup.archive_path(runner.ledger)
    if saved_archive.is_file():
        entries["archive"].insert(0, str(saved_archive))

    def archive():
        path = filedialog.askopenfilename(parent=panel, filetypes=[("Codex ZIP", "*.zip")])
        if path:
            entries["archive"].delete(0, "end")
            entries["archive"].insert(0, path)

    ttk.Button(inputs, text="찾기", command=archive).grid(row=0, column=2)
    ttk.Label(panel, text="독립 검사 · 설치된 EXE와 기준별 인자 및 기대 출력").pack(anchor="w")
    spec = center.editor_box(panel, height=6)
    spec.insert("1.0", json.dumps({"executable": "", "checks": {key: {"args": [], "stdout": "", "inputs": []}
                                                             for key in criteria}}, indent=2))
    options = ttk.Frame(panel)
    options.pack(fill="x")
    grants = {}
    for index, key in enumerate(registered_cycle.PERMISSIONS):
        grants[key] = tk.BooleanVar(panel, value=key == "replica-read")
        ttk.Checkbutton(options, text=LABELS[key], variable=grants[key]).grid(
            row=index // 2, column=index % 2, sticky="w")
    detail = center.editor_box(panel, height=8, state="disabled")
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
            panel.destroy()

    def submit(args, approval=None, *, resume=False, soft_limits=None, graceful=False):
        nonlocal future, active
        future = Future()
        active = True
        button.configure(state="disabled")
        resume_button.configure(state="disabled")
        state.set("승인 범위 확인 중" if approval is None else "Codex 개발 실행 중")

        def work():
            try:
                if resume and approval is None and not graceful:
                    registered_cycle.verify_resume_candidate(runner, project, cancelled=cancelled.is_set)
                review_fn = registered_cycle.review_resume if resume else registered_cycle.review
                run_fn = registered_cycle.run_resume if resume else registered_cycle.run
                options = {"graceful": graceful} if resume else {"soft_limits": soft_limits}
                result = (review_fn(runner, *args, **options) if approval is None
                          else run_fn(runner, *args, approval=approval, cancelled=cancelled.is_set, **options))
                future.set_result(result)
            except Exception as error:
                future.set_exception(error)

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
            if approval is not None:
                state.set(f"{result['outcome']} · {result.get('reason') or '기록 확인'}")
                return
            if cancelled.is_set():
                state.set("검토 취소됨")
                return
            if resume:
                checkpoint = result["resume"]
                summary = (f"{project}\n복제본 {result['replica']}\n"
                           f"재검토 대상: {', '.join(checkpoint['changed'])}\n"
                           f"기존 사용량 {checkpoint['parent_reported_tokens']:,}토큰\n"
                           f"추가 한도 {result['tokens']:,}토큰 / {result['seconds']}초\n"
                           f"Reviewer {result['role_tokens']['reviewer']:,} / "
                           f"최종 Manager {result['role_tokens']['manager-final']:,}토큰\n"
                           f"{_remaining_signoff(checkpoint)} · 모델 최대 {result['model_calls_max']}회. "
                           "Developer 재호출과 원본 반영은 없습니다.")
                if center.confirm("추가 예산 및 중단 작업 재개 승인", summary, parent=panel):
                    submit(args, digest(result), resume=True, graceful=graceful)
                else:
                    state.set("재개 승인 취소됨")
                return
            checks = "\n".join(f"{key} · {row['args']} → {row['stdout']}"
                               for key, row in result["verifier"]["checks"].items())
            summary = (f"{project}\n{result['goal']}\n복제본 {result['replica']}\n"
                       f"파일 {', '.join(result['files'])}\n"
                       f"{result['seconds']}초 / {result['tokens']} 토큰 / 사이클 {result['cycles'] or '예산까지'}\n"
                       f"{result['model']} · {result['role_tokens']}\n"
                       f"검증 EXE {result['verifier']['executable']}\n{checks}\n"
                       "Developer는 MXC 안에서 복제본 쓰기와 셸 권한을 사용합니다. "
                       "Controller가 역할 종료 후 지정 파일 밖의 변경을 차단합니다. "
                       "승인된 프로젝트 문맥과 변경 근거가 모델에 전달됩니다. 명령 네트워크는 꺼져 있습니다.")
            if result.get("graceful_stop"):
                summary += (f"\n역할 초 {result['role_deadlines']}. 시간과 토큰은 소프트 기준입니다. "
                            "토큰은 호출 전 잔여량과 공급자 응답 후 사용량을 확인합니다. "
                            "마무리 최대 1,800초·300,000토큰. 역할 유예 최대 900초. "
                            "무응답 600초, 도구 실행 중 무응답 1,200초에서 소유 Job을 회수합니다.")
            if center.confirm("등록 프로젝트 Codex 세션 승인", summary, parent=panel):
                submit(args, digest(result), soft_limits=soft_limits)
            else:
                state.set("실행 승인 취소됨")

        Thread(target=work, daemon=False).start()
        panel.after(150, poll)

    def start():
        try:
            values = {key: entry.get().strip() for key, entry in entries.items()}
            seconds, tokens = int(values["seconds"]), int(values["tokens"])
            cycles = int(values["cycles"]) if values["cycles"] else None
            manager = int(values["manager_tokens"])
            role_tokens = game_pilot.ROLE_TOKENS if tokens >= 750000 else registered_cycle.ROLE_TOKENS
            limits = {"seconds": seconds, "tokens": tokens, "cycles": cycles,
                      "role_seconds": {role: min(cap, seconds) for role, cap in game_pilot.ROLE_SECONDS.items()},
                      "role_tokens": {role: manager if role.startswith("manager-") else min(cap, tokens)
                                      for role, cap in role_tokens.items()}}
            game_pilot.validate_limits(limits)
            args = (project, values["archive"], int(time()) + seconds + 2940, seconds, tokens, cycles,
                    [name.strip() for name in values["files"].split(",") if name.strip()],
                    [key for key, variable in grants.items() if variable.get()], json.loads(spec.get("1.0", "end")),
                    int(values["manager_tokens"]))
        except (RuntimeError, ValueError) as error:
            state.set(str(error))
            return
        cancelled.clear()
        submit(args, soft_limits=limits)

    def resume():
        try:
            rows = registered_cycle.history(runner.ledger, project)
            graceful = bool(rows and rows[-1].get("outcome") == "SIGNOFF_PENDING")
            args = (project, int(time()) + (4740 if graceful else 1700),
                    int(entries["seconds"].get()), int(entries["tokens"].get()))
        except ValueError:
            state.set("추가 시간과 토큰 한도를 숫자로 입력하세요.")
            return
        cancelled.clear()
        submit(args, resume=True, graceful=graceful)

    buttons = ttk.Frame(panel)
    buttons.pack(fill="x")
    button = ttk.Button(buttons, text="세션 검토 및 시작", command=start)
    button.pack(side="left")
    resume_button = ttk.Button(buttons, text="중단 작업 재개 검토", command=resume)
    resume_button.pack(side="left")
    ttk.Button(buttons, text="닫기 / 중단", command=close).pack(side="right")
    ttk.Button(buttons, text="원본 반영 검토", command=lambda: center.original_apply_panel(project)).pack(side="left")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])
    render()


def _game_preflight(center, panel, runner, state):
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
        limits = None
        if online and not resume:
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
            if rows and rows[-1].get("reason") == "Manager plan exceeds the approved criteria":
                resume_args = (game_pilot.PROJECT, expires, *registered_cycle.game_plan_remaining(rows))
            elif rows and rows[0]["scope"].get("role_deadlines"):
                graceful = True
                resume_args = (game_pilot.PROJECT, int(time()) + 4740, 1800, 300000)

        def submit(approval=None):
            nonlocal future
            future = Future()
            for control in (button, export_button, online_button, resume_button):
                control.configure(state="disabled")
            state.set("온라인 실행 범위 검토 중" if online and approval is None else
                      "온라인 게임 개발 실행 중" if online else "오프라인 MXC 검사 실행 중")

            def worker():
                try:
                    if not online:
                        result = game_pilot.run(runner, archive, cancelled=cancelled.is_set, capture=True,
                                                export_windows=export_windows)
                    elif resume and approval is None:
                        result = registered_cycle.review_resume(runner, *resume_args, graceful=graceful)
                    elif resume:
                        result = registered_cycle.run_resume(runner, *resume_args, approval=approval,
                                                             cancelled=cancelled.is_set, graceful=graceful)
                    elif approval is None:
                        result = registered_cycle.review_game(runner, archive, expires, limits)
                    else:
                        result = registered_cycle.run_game(runner, archive, expires, limits, approval=approval,
                                                           cancelled=cancelled.is_set)
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
                    if online and approval is None:
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
                        if center.confirm(title, summary, parent=panel):
                            submit(digest(result))
                        else:
                            state.set("온라인 실행 승인 취소됨")
                    elif online:
                        statuses = ", ".join(f"{key} {status}" for key, status in result.get("criteria_status", {}).items())
                        state.set(f"{result['outcome']} · {result.get('cycle_count', 0)}사이클 · "
                                  f"{result.get('elapsed_seconds', 0):g}초 · "
                                  f"{result.get('known_reported_tokens', 0):,}토큰 · {statuses}")
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

    ttk.Label(panel, text="등록 기준의 PNG 가져오기, 리소스 팩 실행, 소유 테스트 창 캡처를 검사합니다.",
              wraplength=580).pack(anchor="w")
    budget = ttk.Frame(panel)
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
        ttk.Label(budget, text=role).grid(row=row, column=0, sticky="w")
        for column, key in enumerate(role_entries, 1):
            entry = role_entries[key][role] = ttk.Entry(budget, width=12)
            entry.insert(0, str(defaults[key][role]))
            entry.grid(row=row, column=column, sticky="w")
    button = ttk.Button(panel, text="게임 오프라인 사전 검증", command=start)
    button.pack(anchor="w")
    export_button = ttk.Button(panel, text="Windows EXE 내보내기 및 오프라인 검증", command=lambda: start(True))
    export_button.pack(anchor="w")
    online_button = ttk.Button(panel, text="게임 온라인 개발 검토", command=lambda: start(online=True))
    online_button.pack(anchor="w")
    resume_button = ttk.Button(panel, text="게임 중단 후보 재개 검토",
                               command=lambda: start(online=True, resume=True))
    resume_button.pack(anchor="w")
    ttk.Button(panel, text="검증된 EXE · 사용자 플레이 기록",
               command=lambda: _player_panel(center, runner)).pack(anchor="w")
    ttk.Button(panel, text="닫기 / 중단", command=close).pack(anchor="w")
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
