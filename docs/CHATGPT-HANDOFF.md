# ChatGPT 전달용 요약

DevOrchestra는 Windows 우선 로컬 AI 개발 관리 앱이다. P1 구현은 `0ebe8db`, P1.5 기준 설계는 `d256f79`다. 기존 Mock 역할 파이프라인, Win32 단일 슬롯, SQLite 감사, 합성 승인, 복구와 CLI를 유지한다. P1.5는 Tk Command Center와 읽기 전용 Projection V1, 마일스톤·단계·역할 카드, 프로젝트/실행/역할별 영수증 조회를 제공한다.

Windows 임시 원장에서 기존 8건, 신규 6건 및 CLI 흐름을 검증했다. 실제 GUI 창 시각 검증은 NOT VERIFIED다. P2는 Windows Sandbox 제한 XML 계약만 준비했고 Job/VM/Sandbox 집행, 실제 Codex 및 임의 코드는 BLOCKED다. 다음 작업은 승인된 격리 환경에서 자체 자식 트리 종료 및 호스트 파일·네트워크·자격증명 접근 거부를 검증하는 것이다. [현재 상태](CURRENT-STATUS.md) · [실행 방법](../README.md)
