"""Owner-selected permissions for the Controller's subscription session."""
from .lease import Blocked

AVAILABLE = {
    "source-read": "프로젝트 소스·문서 읽기",
    "replica-edit": "승인된 복제본 파일 수정·생성",
    "tool-discovery": "컴파일러·SDK·런타임 탐색",
    "build-test": "빌드·컴파일·자동 테스트",
    "owned-app-run": "격리된 개발 프로그램 실행",
    "owned-window-input": "소유 테스트 창 자동 조작",
    "owned-window-capture": "소유 테스트 창 캡처",
    "test-evidence": "테스트 로그·검증 증거 저장",
    "owned-cleanup": "작업 소유 임시 파일 정리",
}
UNAVAILABLE = (
    "복제본 파일 삭제·이동", "새 하위 디렉터리 생성", "프로젝트 설정·리소스 변경",
    "프로젝트 전용 도구 설치", "패키지·라이브러리 설치", "네트워크 다운로드",
    "복제본 Git 커밋 생성",
)
SEPARATE = (
    "검증된 변경의 원본 반영", "Git 원격 푸시", "시스템 전체 설치·관리자 권한",
    "사용자 소유 자료 삭제", "외부 계정·서비스 변경", "실거래·결제·실서비스 배포",
)


def phases(*, runtime, gui):
    verifier = {"source-read", "build-test", "test-evidence", "owned-cleanup"}
    if runtime or gui:
        verifier.update(("tool-discovery", "owned-app-run"))
    if gui:
        verifier.update(("owned-window-input", "owned-window-capture"))
    return {"manager-planning": {"source-read"}, "developer": {"source-read", "replica-edit"},
            "reviewer": {"source-read"}, "verifier": verifier, "manager-final": {"source-read"}}


def contract(selection, *, runtime, gui):
    grants = phases(runtime=runtime, gui=gui)
    required = set().union(*grants.values())
    if selection is None:
        # Existing CLI callers already approve the complete reviewed scope by hash.
        selection = sorted(required)
    if (type(selection) is not list or any(type(item) is not str or item not in AVAILABLE for item in selection)
            or len(set(selection)) != len(selection)):
        raise Blocked("세션에서 집행할 수 없는 권한이 선택됐습니다.")
    missing = required.difference(selection)
    if missing:
        raise Blocked("추가 승인 필요 · " + ", ".join(AVAILABLE[name] for name in AVAILABLE if name in missing))
    return {"granted": sorted(selection), "roles": {role: sorted(items) for role, items in grants.items()},
            "tool_network_destinations": [], "process_scope": "CONTROLLER_OWNED"}
