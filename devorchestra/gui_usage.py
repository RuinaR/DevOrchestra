"""Command Center subscription and budget panel."""
from concurrent.futures import Future
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from threading import Thread
import tkinter as tk
from tkinter import filedialog, ttk
from webbrowser import open as open_browser

from . import ai_usage
from .chatgpt_subscription import AccountStore
from .lease import Blocked
from .ledger import check_path
from .runtime import Runner


def _display(value):
    return "UNKNOWN" if value is None else str(value)


def _local_time(epoch=None):
    try:
        moment = datetime.now() if epoch is None else datetime.fromtimestamp(epoch)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except (OSError, OverflowError, ValueError):
        return "UNKNOWN"


def show_panel(center):
    from .gui import BG, MUTED, label

    ledger = center.reader.ledger
    panel = center.panel()
    panel.title("AI 사용량과 개발 예산")
    panel.geometry("790x590")
    outer = tk.Frame(panel, bg=BG, padx=16, pady=12)
    outer.pack(fill="both", expand=True)
    label(outer, "AI 사용량과 개발 예산", size=16, bg=BG, bold=True).pack(anchor="w")
    notice = label(outer, bg=BG, fg=MUTED)
    notice.pack(anchor="w")
    accounts = ttk.Combobox(outer, state="readonly")
    accounts.pack(fill="x", pady=5)
    toolbar = tk.Frame(outer, bg=BG)
    toolbar.pack(fill="x")
    display = center.editor_box(outer, height=22, state="disabled")
    checks, imported, lookup = {}, None, None
    last_text = None
    states = {"CONNECTED": "공식 모델 목록 연결 확인", "AUTH_REQUIRED": "인증 필요 또는 만료",
              "NO_PERMISSION": "구독 사용 권한 없음", "QUERY_FAILED": "연결 조회 실패",
              "BUSY": "Controller 실행 슬롯 대기", "LIMIT_REACHED": "제공자 한도 도달 · 새 요청 일시 중지"}

    def refresh(_event=None):
        nonlocal last_text
        try:
            project = center.project_ids[center.project.current()] if center.project.current() >= 0 else None
            notice.configure(text=f"프로젝트 · {project or '미선택'}")
            events = ledger.read() if ledger.path.exists() else []
            stats = ai_usage.project_usage(events, project)
            rows = {row["account"]: row for row in AccountStore(ledger).accounts()}
            accounts.configure(values=list(rows))
            if accounts.get() not in rows:
                accounts.set(stats["account"] if stats["account"] in rows else next(iter(rows), ""))
            selected = accounts.get()
            row = rows.get(selected)
            status = ("AUTH_REQUIRED" if not row or not row["signed_in"] else
                      "NO_PERMISSION" if not row["subscription_granted"] else None)
            if status is None and selected in checks:
                status = checks[selected][0]
            paused = ai_usage.pause_event(events, selected) is not None
            if paused:
                status = "LIMIT_REACHED"
            lines = ["ChatGPT 직접 OAuth · 구독 연결",
                     states.get(status, "로컬 연결 저장됨 · 실시간 확인 전"),
                     "계정 한도와 초기화 · 직접 OAuth 조회 미지원",
                     "출처 · 로컬 등록 및 공식 /v1/models 연결 확인"]
            if selected in checks:
                lines.append(f"연결 확인 · {checks[selected][1]}")
            lines.extend(("", "Codex 계정 할당량 · 직접 OAuth와 별도 표시"))
            if imported:
                snapshot, imported_at = imported
                lines.append(f"출처 · 가져온 Codex 응답 · 가져온 시각 {imported_at}")
                for item in snapshot["reached"]:
                    lines.append(f'제공자 한도 상태 · {item["bucket"]} / {item["state"]}')
                for window in snapshot["windows"]:
                    reset = _local_time(window["resets_at"]) if window["resets_at"] is not None else "UNKNOWN"
                    duration = f'{window["minutes"]}분' if window["minutes"] is not None else "UNKNOWN"
                    used, remaining = (_display(window[key]) + ("%" if window[key] is not None else "")
                                       for key in ("used", "remaining"))
                    lines.append(f'{window["bucket"]} / {window["window"]} · {duration} · '
                                 f'사용 {used} · 잔여 {remaining} · 초기화 {reset}')
                lines.append(f'Codex 계정 누적 보고 토큰 · {_display(snapshot["lifetime_tokens"])}')
            else:
                lines.append("실시간 조회 미연결 · 공식 결과를 가져오거나 ChatGPT 설정에서 확인")
            lines.extend(("", "DevOrchestra 개발 세션 · 내부 토큰 예산",
                          f'세션의 OAuth 등록 · {_display(stats["account"])}',
                          f'한도 {_display(stats["budget"])} · 보고 사용 {stats["known"]} · 잔여 {_display(stats["remaining"])}',
                          f'프로젝트 보고 누적 {stats["project_reported"]} · 미확정 응답 {stats["project_unknown"]}',
                          "역할 · 모델 · 입력 / 출력 / 캐시 / 합계"))
            if stats["models"]:
                models = " · ".join(f"{role} {model}" for role, model in stats["models"].items())
                lines.insert(-1, f"승인 모델 · {models}")
            for item in stats["roles"]:
                counts = " / ".join(_display(item[k]) for k in ("input", "output", "cached", "total"))
                lines.append(f'{item["phase"]} · {_display(item["model"])} · {counts}')
            resume_button.configure(state="normal" if paused else "disabled")
            text = "\n".join(lines)
        except (Blocked, OSError, ValueError, sqlite3.Error):
            resume_button.configure(state="disabled")
            text = "로컬 사용량 원장을 읽지 못했습니다. 기존 개발 상태는 유지됩니다."
        if text == last_text:
            return
        position = display.yview()[0]
        display.configure(state="normal")
        display.delete("1.0", "end")
        display.insert("1.0", text)
        display.configure(state="disabled")
        display.yview_moveto(position)
        last_text = text

    def verify_connection():
        nonlocal lookup
        selected = accounts.get()
        if not selected or lookup:
            return
        future = Future()
        lookup = (selected, future)

        def work():
            try:
                future.set_result(ai_usage.check_connection(ledger, selected))
            except Exception:
                future.set_result("QUERY_FAILED")

        Thread(target=work, daemon=True).start()

    def import_codex():
        nonlocal imported
        selected = filedialog.askopenfilename(parent=panel, filetypes=[("Codex JSON", "*.json")])
        if not selected:
            return
        try:
            path = Path(selected)
            check_path(path)
            with path.open("rb") as stream:
                raw = stream.read(262145)
            value = json.loads(raw) if len(raw) <= 262144 else None
            if type(value) is not dict:
                raise ValueError
            imported = (ai_usage.codex_snapshot(value.get("limits", value), value.get("activity")), _local_time())
        except (OSError, ValueError, Blocked, RecursionError):
            notice.configure(text="가져오기 실패 · 공식 Codex 결과 JSON을 선택하세요")
            return
        refresh()

    def resume_usage():
        if not center.confirm("새 모델 요청 재개", "ChatGPT 사용량 설정을 확인했습니다. 이 계정의 새 요청을 재개합니다.",
                              parent=panel):
            return
        try:
            ai_usage.resume(Runner(ledger), accounts.get())
        except (Blocked, OSError, sqlite3.Error):
            notice.configure(text="현재 요청 재개를 기록할 수 없습니다.")
            return
        refresh()

    for title, action in (("새로 고침", refresh), ("OAuth 연결 확인", verify_connection),
                          ("Codex 결과 가져오기", import_codex),
                          ("ChatGPT 사용량 설정", lambda: open_browser(ai_usage.USAGE_URL))):
        ttk.Button(toolbar, text=title, command=action).pack(side="left", padx=(0, 5))
    resume_button = ttk.Button(toolbar, text="설정 확인 후 재개", command=resume_usage)
    resume_button.pack(side="left")
    accounts.bind("<<ComboboxSelected>>", refresh)
    timer = None

    def tick():
        nonlocal lookup, timer
        if lookup and lookup[1].done():
            selected, future = lookup
            checks[selected] = (future.result(), _local_time())
            lookup = None
        refresh()
        timer = panel.after(1500, tick)

    def destroy(event):
        if event.widget is panel and timer is not None:
            panel.after_cancel(timer)

    panel.bind("<Destroy>", destroy, add="+")
    tick()
