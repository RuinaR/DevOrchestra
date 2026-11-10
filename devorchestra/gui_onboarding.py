"""Owner review for the existing-Git onboarding workflow."""
from concurrent.futures import Future
from pathlib import Path
from threading import Thread
import tkinter as tk
from tkinter import filedialog, ttk

from . import project_onboarding
from .ledger import digest
from .runtime import Runner


def show_panel(center, initial):
    index = center.project.current()
    if index < 0:
        return
    project = center.project_ids[index]
    runner = Runner(center.reader.ledger)
    panel = center.panel()
    panel.title("기존 Git 프로젝트 문맥")
    panel.geometry("760x590")
    ttk.Label(panel, text=f"{project} · 원본 읽기 전용 / 개발 권한 별도 승인").pack(anchor="w")
    destination = ttk.Entry(panel)
    destination.insert(0, initial)
    destination.pack(fill="x", pady=(7, 4))

    def choose():
        parent = filedialog.askdirectory(parent=panel, mustexist=True)
        if parent:
            destination.delete(0, "end")
            destination.insert(0, str(Path(parent) / "development-copy"))

    ttk.Button(panel, text="새 복제본의 상위 폴더 선택", command=choose).pack(anchor="w")
    ttk.Label(panel, text="추가 로컬 예제 경로 · 승인된 .example.json 또는 .sample.json · 한 줄에 하나").pack(anchor="w")
    examples = center.editor_box(panel, height=3)
    actions = ttk.Frame(panel)
    actions.pack(fill="x")
    report = center.editor_box(panel, height=15, state="disabled")
    busy, approved = False, None

    def display(text):
        report.configure(state="normal")
        report.delete("1.0", "end")
        report.insert("1.0", text)
        report.configure(state="disabled")

    def review_text(scope):
        baseline = scope["baseline"]
        context = scope["context"]
        lines = ["CONTEXT_READY · 원본 및 작업 복제본 승인 검토", f'원본 HEAD {baseline["head"]}',
                 f'브랜치 {baseline["branch"] or "DETACHED"}',
                 "원격 " + (", ".join(remote["host"] or "호스트 비공개" for remote in baseline["remotes"]) or "없음"),
                 f'선택된 파일 {len(baseline["files"])}개 · 보호 자료 {len(baseline["protected"])}개',
                 f'개인 지침 {context["private_instructions"]["status"]}',
                 f'탐지된 환경 {", ".join(context["detected_environment"]) or "없음"} · 빌드 NOT_VALIDATED']
        lines.append("빌드 설정 표식 · " + (", ".join(context["build_inputs"][:20]) or "없음"))
        lines.append(f'테스트 경로 {len(context["test_sources"])}개 · 실행 NOT_VALIDATED')
        for title, key in (("문맥 문서", "documents"), ("스테이징", "staged"),
                           ("인덱스와 다른 원시 바이트", "raw_worktree_changed"), ("삭제", "deleted"),
                           ("미추적", "untracked"), ("Git 제외", "ignored"),
                           ("보호 자료", "protected"), ("추가 승인 예제", "local_examples")):
            values = list(baseline[key])
            lines.append(f"{title} ({len(values)}) · " + ", ".join(values[:80]))
        return "\n".join(lines)

    def close():
        if not busy:
            panel.destroy()

    def run(mode):
        nonlocal busy, approved
        path = destination.get().strip()
        include = [line.strip() for line in examples.get("1.0", "end-1c").splitlines() if line.strip()]
        if mode != "status" and not path:
            display("새 복제본 경로를 지정하세요.")
            return
        if mode == "create":
            if not approved or approved[:2] != (path, include):
                display("복제본 경로와 예제를 다시 검토하세요.")
                return
            if not center.confirm("기존 Git 프로젝트 복제", "검토된 파일만 새 경로에 복사합니다. 원본 Git 이력은 그대로 보존됩니다.",
                                  parent=panel):
                return
        busy = True
        for button in buttons:
            button.configure(state="disabled")
        display("소유 Git 작업 실행 중 · 원본 변경과 네트워크 연결은 허용하지 않습니다.")
        future = Future()
        approval = approved[2] if approved else None

        def work():
            try:
                if mode == "status":
                    value = project_onboarding.status(runner.ledger, project)
                elif mode == "review":
                    value = project_onboarding.review(runner, project, path, include)
                else:
                    value = project_onboarding.create(runner, project, path, include, approval)
            except Exception as error:
                from .lease import Blocked

                future.set_result((None, str(error) if isinstance(error, (Blocked, ValueError)) else "로컬 온보딩 확인 실패"))
            else:
                future.set_result((value, None))

        def poll():
            nonlocal busy, approved
            if not future.done():
                panel.after(100, poll)
                return
            busy = False
            for button in buttons:
                button.configure(state="normal")
            value, failure = future.result()
            if failure:
                approved = None
                display("BLOCKED · " + failure)
            elif mode == "review":
                approved = (path, include, digest(value))
                display(review_text(value))
            else:
                display("\n".join(f"{key} · {value[key]}" for key in (
                    "status", "replica", "original_head", "replica_head", "environment", "development", "reason",
                ) if key in value))
                if mode == "create":
                    approved = None
                    center.refresh()

        Thread(target=work, daemon=True).start()
        panel.after(100, poll)

    buttons = []
    for name, mode in (("준비 상태", "status"), ("문맥 및 복제 범위 검토", "review"), ("승인하고 복제본 생성", "create")):
        button = ttk.Button(actions, text=name, command=lambda mode=mode: run(mode))
        button.pack(side="left", padx=(0, 6))
        buttons.append(button)
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: close())
    run("review" if initial else "status")
