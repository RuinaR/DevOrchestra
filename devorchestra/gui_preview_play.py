"""Review and approve a model-free preview run from the registered project form."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, ttk

from . import preview_play
from .ledger import digest


def show(center, project, preferences):
    panel = center.panel()
    panel.title("독립 GUI 입력 검증")
    runner = center.controller
    state = tk.StringVar(panel, value="검증할 프리뷰 폴더와 실행할 입력을 지정하세요.")
    label = "저장된 실제 입력 선호는 켜져 있습니다. " if preferences.get("desktop_preference") else ""
    ttk.Label(panel, text=label + "모델을 호출하지 않습니다. 실행할 때마다 대상을 검토하고 승인합니다.",
              wraplength=570).pack(fill="x")
    ttk.Label(panel, text=f"프로젝트 {project} · build.json 옆의 Windows 프리뷰 폴더").pack(anchor="w")
    folder = tk.StringVar(panel)
    directory = ttk.Frame(panel)
    directory.pack(fill="x")
    ttk.Entry(directory, textvariable=folder).pack(side="left", fill="x", expand=True)

    def browse():
        if value := filedialog.askdirectory(parent=panel, mustexist=True):
            folder.set(value)

    ttk.Button(directory, text="폴더 선택", command=browse).pack(side="right")
    fields = ttk.Frame(panel)
    fields.pack(fill="x")
    limits = {}
    for column, (key, preference, title, fallback) in enumerate((
            ("steps", "play_inputs", "최대 입력 수", "24"),
            ("seconds", "play_seconds", "최대 시간(초)", "180"),
            ("hold", "desktop_hold", "최대 키 유지(ms)", "1000"),
            ("fps", "capture_fps", "FPS", "30"),
            ("evidence", "evidence_mib", "증거(MiB)", "64"))):
        ttk.Label(fields, text=title).grid(row=0, column=column, sticky="w")
        limits[key] = tk.StringVar(panel, value=preferences.get(preference) or fallback)
        ttk.Entry(fields, textvariable=limits[key], width=10).grid(row=1, column=column, sticky="w")
    ttk.Label(panel, text="입력 순서 JSON · keys / click / wait · 클릭 좌표는 게임 클라이언트 기준",
              wraplength=570).pack(anchor="w")
    plan = center.editor_box(panel, height=8)
    plan.insert("1.0", '[\n  {"action":"wait","ms":500}\n]')
    ttk.Label(panel, textvariable=state, wraplength=570).pack(fill="x")

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview-play")
    cancelled = Event()
    active = None
    closing = False

    def close():
        nonlocal closing
        closing = True
        cancelled.set()
        if active is None:
            executor.shutdown(wait=False)
            panel.destroy()
        else:
            state.set("중단 요청됨 · 입력과 소유 Job 정리 중")

    def destroyed(event):
        if event.widget is panel:
            cancelled.set()
            executor.shutdown(wait=False)

    def submit(task, done):
        nonlocal active
        review_button.configure(state="disabled")
        active = executor.submit(task)

        def poll():
            nonlocal active
            if not active.done():
                panel.after(150, poll)
                return
            finished, active = active, None
            if closing:
                close()
                return
            review_button.configure(state="normal")
            try:
                done(finished.result())
            except Exception as error:
                state.set(f"검증 중단 · {error}")

        panel.after(150, poll)

    def request_review():
        try:
            options = {key: int(value.get()) for key, value in limits.items()}
        except ValueError:
            state.set("실행 한도는 정수로 입력하세요.")
            return
        if not folder.get().strip():
            state.set("Windows 프리뷰 폴더를 선택하세요.")
            return
        path = Path(folder.get()).absolute()
        raw = plan.get("1.0", "end-1c")
        state.set("등록 정보와 프리뷰 신원을 확인 중")
        submit(lambda: preview_play.review(runner, project, path, raw, **options), approve)

    def approve(scope):
        review = tk.Toplevel(panel, padx=12, pady=12)
        review.title("독립 GUI 실행 승인")
        review.transient(panel)
        review.grab_set()
        approval = digest(scope)
        details = center.editor_box(review, height=16)
        details.insert("1.0", json.dumps({**scope, "approval_sha256": approval}, ensure_ascii=False, indent=2))
        details.configure(state="disabled")
        ttk.Label(review, text="검증을 시작할 때 승인된 게임 창을 자동으로 전경에 배치합니다. "
                  "검증이 시작된 뒤 소유 창을 벗어나거나 다른 입력을 감지하면 후속 입력을 중단합니다. "
                  "전역 이벤트가 다른 창에 전달될 위험과 강제 종료 시 키 해제의 한계가 남습니다.",
                  wraplength=560).pack(fill="x", pady=5)
        consent = tk.BooleanVar(review)
        ttk.Checkbutton(review, text="이 실행의 대상·입력 순서·전역 입력 위험을 확인했습니다.",
                        variable=consent).pack(anchor="w")

        def launch():
            if not consent.get():
                return
            review.destroy()
            state.set("독립 GUI 검증 중 · 모델 호출 없음")
            submit(lambda: preview_play.run(runner, scope, approval, cancelled=cancelled.is_set), report)

        ttk.Button(review, text="승인하고 실행", command=launch).pack(side="left")
        ttk.Button(review, text="취소", command=review.destroy).pack(side="right")

    def report(result):
        state.set(f"{result['outcome']} · {result['reason'] or '화면과 입력 관찰 완료'} · "
                  f"입력·대기 {len(result['actions'])}건 · 이미지 {len(result['frames'])}장 · "
                  f"소유 Job {result['owned_tree']} · 실행 {result['run_id']}")

    footer = ttk.Frame(panel)
    footer.pack(fill="x", pady=4)
    review_button = ttk.Button(footer, text="실행 범위 검토", command=request_review)
    review_button.pack(side="left")
    ttk.Button(footer, text="닫기 / 중단", command=close).pack(side="right")
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: (close(), "break")[1])
    panel.bind("<Destroy>", destroyed, add="+")
