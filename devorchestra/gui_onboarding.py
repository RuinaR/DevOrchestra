"""Owner review for the existing-Git onboarding workflow."""
from concurrent.futures import Future
import json
from pathlib import Path
from shutil import which
from threading import Thread
from time import time
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
    panel.geometry("810x690")
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
    ttk.Label(panel, text="오프라인 .NET 검증 · C# 상대 경로 두 개를 쉼표로 구분").pack(anchor="w", pady=(6, 0))
    build_paths = ttk.Entry(panel)
    build_paths.pack(fill="x")
    ttk.Label(panel, text="기대 벡터 JSON · 예: [[9, 31]]").pack(anchor="w")
    build_vectors = ttk.Entry(panel)
    build_vectors.pack(fill="x")
    verification = ttk.Frame(panel)
    verification.pack(fill="x")
    report = center.editor_box(panel, height=15, state="disabled")
    busy, approved, build_approved = False, None, None

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
        nonlocal busy, approved, build_approved
        path = destination.get().strip()
        include = [line.strip() for line in examples.get("1.0", "end-1c").splitlines() if line.strip()]
        if mode in ("review", "create") and not path:
            display("새 복제본 경로를 지정하세요.")
            return
        if mode == "create":
            if not approved or approved[:2] != (path, include):
                display("복제본 경로와 예제를 다시 검토하세요.")
                return
            if not center.confirm("기존 Git 프로젝트 복제", "검토된 파일만 새 경로에 복사합니다. 원본 Git 이력은 그대로 보존됩니다.",
                                  parent=panel):
                return
        build_input = (build_paths.get(), build_vectors.get())
        build_args = None
        if mode == "build-review":
            try:
                from .mxc_dotnet import ONBOARDED_KIND

                launcher = which("codex.exe")
                if not launcher:
                    raise ValueError
                names = [name.strip() for name in build_input[0].split(",") if name.strip()]
                build_args = (project, ONBOARDED_KIND, launcher, names, json.loads(build_input[1]), 20, int(time()) + 180)
            except ValueError:
                display("Codex 실행기, 쉼표로 구분한 두 파일 경로와 [[입력, 기대값]] JSON을 확인하세요.")
                return
        if mode == "build":
            if not build_approved or build_approved[0] != build_input:
                display("실행 범위를 다시 검토하세요.")
                return
            if not center.confirm("격리 빌드·테스트 승인", "검토한 파일과 기대 벡터만 오프라인 MXC에서 실행합니다.",
                                  parent=panel):
                return
            build_args = build_approved[1]
        build_digest = build_approved[2] if build_approved else None
        busy = True
        for button in buttons:
            button.configure(state="disabled")
        display("검토 또는 검증 진행 중 · 원본 변경과 네트워크 연결은 허용하지 않습니다.")
        future = Future()
        approval = approved[2] if approved else None

        def work():
            try:
                if mode == "status":
                    value = project_onboarding.status(runner.ledger, project)
                elif mode == "review":
                    value = project_onboarding.review(runner, project, path, include)
                elif mode == "build-review":
                    value = runner.build_verifier_scope(*build_args)
                elif mode == "build":
                    runner.build_verify(*build_args, build_digest)
                    value = project_onboarding.status(runner.ledger, project)
                else:
                    value = project_onboarding.create(runner, project, path, include, approval)
            except Exception as error:
                from .lease import Blocked

                future.set_result((None, str(error) if isinstance(error, (Blocked, ValueError)) else "로컬 온보딩 확인 실패"))
            else:
                future.set_result((value, None))

        def poll():
            nonlocal busy, approved, build_approved
            if not future.done():
                panel.after(100, poll)
                return
            busy = False
            for button in buttons:
                button.configure(state="normal")
            value, failure = future.result()
            if failure:
                approved = None
                build_approved = None
                display("BLOCKED · " + failure)
            elif mode == "review":
                approved = (path, include, digest(value))
                display(review_text(value))
            elif mode == "build-review":
                build_approved = (build_input, build_args, digest(value))
                display("\n".join((
                    "ENVIRONMENT_READY · 이 실행 계약의 도구와 의존성 확인",
                    f'범위 · {value["readiness"]["coverage"]}',
                    f'SDK {value["sdk"]} · 런타임 {value["runtime"]}',
                    f'도구 {value["tool"]}',
                    f'도구 SHA-256 {value["tool_sha256"][value["tool"]]}',
                    "소스 · " + ", ".join(value["sources"]),
                    f'테스트 벡터 · {value["vectors"]}',
                    "네트워크 OFF · 패키지 복원 및 프로젝트 빌드 스크립트 없음",
                    f'시간 {value["limits"]["max_seconds"]}초 · 메모리 {value["limits"]["memory_bytes"]}B',
                )))
            else:
                display("\n".join(f"{key} · {value[key]}" for key in (
                    "status", "replica", "original_head", "replica_head", "environment", "development",
                    "coverage", "sdk", "runtime", "compiler_version", "context_documents",
                    "build_result", "test_results", "cleanup", "build_receipt_sha256", "reason",
                ) if value.get(key) is not None))
                if mode == "build":
                    build_approved = None
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
    for name, mode in (("환경·빌드 검토", "build-review"), ("승인하고 빌드·테스트", "build")):
        button = ttk.Button(verification, text=name, command=lambda mode=mode: run(mode))
        button.pack(side="left", padx=(0, 6))
        buttons.append(button)
    panel.protocol("WM_DELETE_WINDOW", close)
    panel.bind("<Escape>", lambda _event: close())
    run("review" if initial else "status")
