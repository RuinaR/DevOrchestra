"""First-run Codex connection panel for Command Center."""
from concurrent.futures import Future
import sqlite3
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog, ttk
import webbrowser

from . import codex_setup
from .native_developer_trial import DEFAULT_MODEL


STATUS = {
    "CONNECTED": "이 Windows 사용자에 ChatGPT 로그인이 저장되어 있습니다.",
    "SIGNED_OUT": "ChatGPT 로그인 버튼을 눌러 브라우저에서 연결하세요.",
    "OTHER_AUTH": "ChatGPT 구독으로 연결하려면 이 앱의 Codex 연결에서 로그아웃한 뒤 다시 로그인하세요.",
    "STATUS_UNKNOWN": "로그인 상태를 확인하지 못했습니다. Codex ZIP을 다시 확인하세요.",
    "AUTH_FAILED": "계정 연결에 실패했습니다. 브라우저에서 취소 여부와 네트워크를 확인한 뒤 다시 시도하세요.",
    "CANCELLED": "연결 요청을 취소했습니다.",
    "TIMEOUT": "로그인 시간이 초과됐습니다. 브라우저 안내를 확인한 뒤 다시 시작하세요.",
}


def show(center):
    panel = center.panel()
    panel.title("DevOrchestra · Codex 환경 및 로그인")
    panel.minsize(490, 0)
    runner = center.controller
    cancelled = Event()
    pending = None
    state = tk.StringVar(value="공식 Codex 0.167.1 Windows x64 ZIP을 준비하세요.")
    ttk.Label(panel, text="Codex 연결", style="Heading.TLabel").pack(anchor="w")
    ttk.Label(panel, text=f"개인 저장 위치\n{runner.ledger.state_boundary}", wraplength=500).pack(anchor="w")
    ttk.Label(panel, text="공식 ZIP 선택 → ChatGPT 로그인 → 로컬 실행 확인 → 프로젝트 등록",
              wraplength=500).pack(anchor="w", pady=6)
    ttk.Label(panel, textvariable=state, wraplength=500, justify="left").pack(fill="x", pady=8)
    commands = ttk.Frame(panel)
    commands.pack(fill="x")
    buttons = []

    def submit(action):
        nonlocal pending
        if pending and not pending.done():
            return
        if runner.ledger.storage_status()["status"] != "READY":
            state.set("먼저 초기 설정에서 사용자 저장소를 준비하세요.")
            return
        archive = None
        if action == "install":
            archive = filedialog.askopenfilename(parent=panel, filetypes=[("공식 Codex ZIP", "*.zip")])
            if not archive:
                return
        if action == "logout" and not center.confirm("Codex 연결 해제",
                "이 앱 전용 Codex 연결에서 로그아웃합니다. 계정을 바꾸려면 이후 다시 로그인하세요.", parent=panel):
            return
        cancelled.clear()
        pending = Future()
        for button in buttons:
            button.configure(state="disabled")
        state.set("브라우저에서 ChatGPT 로그인을 완료하세요." if action == "login" else "Codex 상태를 확인하는 중입니다.")

        def work():
            try:
                pending.set_result(codex_setup.run(runner, action, archive=archive, cancelled=cancelled.is_set))
            except Exception as error:
                pending.set_exception(error)

        def poll():
            if not pending.done():
                panel.after(150, poll)
                return
            for button in buttons:
                button.configure(state="normal")
            try:
                result = pending.result()
            except (RuntimeError, OSError, ValueError, sqlite3.Error) as error:
                state.set(f"준비를 완료하지 못했습니다. 공식 ZIP, 로컬 Git 및 실행 상태를 확인하세요.\n{error}")
                return
            lines = [STATUS.get(result.get("status"), "로컬 실행 확인을 마쳤습니다.")]
            verified = result.get("verification")
            if verified:
                lines.append("로컬 실행 확인 통과." if verified in (True, "SYNTHETIC_PASS") else
                             "로컬 실행 확인 실패. Git 설치와 Windows 실행 환경을 확인하세요.")
            else:
                lines.append("자동 개발 전에 로컬 실행 확인을 실행하세요.")
            if "provider" in result:
                provider = result["provider"]
                model = {"LISTED": "목록에 있음", "NOT_LISTED": "목록에 없음", "UNKNOWN": "조회 불가"}
                lines.append(f"계정 요금제 {provider['plan']} · {DEFAULT_MODEL} {model[provider['model']]}")
                lines.extend(f"제공자 한도 · {minutes or '?'}분 사용률 {used:g}%" for minutes, used in provider["limits"])
                if not provider["limits"]:
                    lines.append("제공자 한도 조회 불가. 개발 세션의 시간·토큰 예산은 별도입니다.")
            state.set("\n".join(lines))

        Thread(target=work, daemon=False).start()
        panel.after(150, poll)

    for index, (action, title) in enumerate((
            ("install", "공식 ZIP 선택"), ("status", "로그인 상태"), ("login", "ChatGPT 로그인"),
            ("logout", "로그아웃"), ("provider", "계정·모델 조회"), ("verify", "로컬 실행 확인"))):
        button = ttk.Button(commands, text=title, command=lambda key=action: submit(key))
        button.grid(row=index // 3, column=index % 3, sticky="ew", padx=2, pady=2)
        buttons.append(button)
    for column in range(3):
        commands.columnconfigure(column, weight=1)
    links = ttk.Frame(panel)
    links.pack(fill="x", pady=(8, 0))
    for title, url in (("공식 ZIP 다운로드", codex_setup.RELEASE_URL), ("로그인 도움말", codex_setup.AUTH_DOCS),
                       ("Git 설치 안내", "https://git-scm.com/install/windows")):
        ttk.Button(links, text=title, command=lambda target=url: webbrowser.open(target)).pack(side="left")
    bottom = ttk.Frame(panel)
    bottom.pack(fill="x", pady=(8, 0))
    ttk.Button(bottom, text="로그인 취소", command=cancelled.set).pack(side="left")
    ttk.Button(bottom, text="프로젝트 등록", command=lambda: (panel.destroy(), center.register_project())).pack(side="right")
    panel.bind("<Destroy>", lambda event: cancelled.set() if event.widget is panel else None, add="+")
