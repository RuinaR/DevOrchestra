"""Command Center entry point for an owner-approved registered Codex session."""
from concurrent.futures import Future
import json
import sqlite3
from threading import Event, Thread
from time import time
import tkinter as tk
from tkinter import filedialog, ttk

from . import cycle_progress, registered_cycle, replica_readiness
from .catalog import Catalog
from .ledger import digest


LABELS = {
    "replica-read": "복제본 읽기", "replica-edit": "지정 파일 생성·수정",
    "offline-shell": "복제본의 오프라인 셸", "pinned-verifier": "지정 검증 명령",
}


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
    ttk.Button(panel, text="등록 목표·기준 편집",
               command=lambda: (panel.destroy(), center.register_project(edit=True))).pack(anchor="w")
    inputs = ttk.Frame(panel)
    inputs.pack(fill="x")
    entries = {}
    for index, (key, label, default) in enumerate((
            ("archive", "공식 Codex ZIP", ""), ("files", "수정 파일 경로 (쉼표 구분)", ""),
            ("seconds", "초", "960"), ("tokens", "토큰", "270000"), ("cycles", "사이클 상한", "2"))):
        ttk.Label(inputs, text=label).grid(row=index, column=0, sticky="w")
        entry = entries[key] = ttk.Entry(inputs)
        entry.insert(0, default)
        entry.grid(row=index, column=1, sticky="ew")
    inputs.columnconfigure(1, weight=1)

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
        lines = []
        for row in roles:
            usage = row.get("provider_usage")
            text = f"{row['cycle']} · {row['phase']} · {row.get('verdict', row.get('outcome', '실행 중'))}"
            if isinstance(usage, dict):
                text += f" · 입력 {usage['input_tokens']} / 출력 {usage['output_tokens']} / 캐시 {usage.get('cached_input_tokens', '?')}"
            if row.get("command_events"):
                text += f" · 도구 명령 {len(row['command_events'])}회"
            lines.append(text)
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

    def submit(args, approval=None):
        nonlocal future, active
        future = Future()
        active = True
        button.configure(state="disabled")
        state.set("승인 범위 확인 중" if approval is None else "Codex 개발 실행 중")

        def work():
            try:
                result = (registered_cycle.review(runner, *args) if approval is None
                          else registered_cycle.run(runner, *args, approval=approval, cancelled=cancelled.is_set))
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
            if center.confirm("등록 프로젝트 Codex 세션 승인", summary, parent=panel):
                submit(args, digest(result))
            else:
                state.set("실행 승인 취소됨")

        Thread(target=work, daemon=False).start()
        panel.after(150, poll)

    def start():
        try:
            values = {key: entry.get().strip() for key, entry in entries.items()}
            args = (project, values["archive"], int(time()) + 1700, int(values["seconds"]), int(values["tokens"]),
                    int(values["cycles"]) if values["cycles"] else None,
                    [name.strip() for name in values["files"].split(",") if name.strip()],
                    [key for key, variable in grants.items() if variable.get()], json.loads(spec.get("1.0", "end")))
        except ValueError as error:
            state.set(str(error))
            return
        cancelled.clear()
        submit(args)

    buttons = ttk.Frame(panel)
    buttons.pack(fill="x")
    button = ttk.Button(buttons, text="세션 검토 및 시작", command=start)
    button.pack(side="left")
    ttk.Button(buttons, text="닫기 / 중단", command=close).pack(side="right")
    ttk.Button(buttons, text="원본 반영 검토", command=lambda: center.original_apply_panel(project)).pack(side="left")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])
    render()
