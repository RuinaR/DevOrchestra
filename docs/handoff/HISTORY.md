# DevOrchestra 구조와 개발 이력

## 운영 경계

Controller는 등록 정보와 승인 범위를 동결하고, 프로젝트·작업·에이전트·실행 슬롯을 하나씩 관리한다. Manager 계획 → Developer → 코드 및 필요 시 시각 Reviewer → 결정적 Verifier → 최종 Manager 순서로 수행한다. 다음 사이클은 앞선 작업의 역할·Job과 게시가 처리되고 승인된 예산과 중단 조건이 허용할 때 진입한다. 외부 Codex의 Computer Use 관찰과 Controller의 소유 창 캡처·입력은 승인과 증거가 다른 실행 경로다.

Windows Job은 소유 프로세스 종료와 회수에 쓰인다. 파일 및 네트워크 제한은 적용된 샌드박스와 고정된 승인 계약을 따로 확인한다. 등록 개발에서는 실행과 후보의 기준점, 작업 경로, 정책과 리비전, 검증 도구, 캡처·빌드 식별자, 역할 결과를 연결한다. 조작 불명·미확정 응답·실행 설명 부족은 이전 판정을 바꾸지 않고 승인된 복구 절차에 추가한다. Git은 승인된 브랜치와 파일을 재확인하고 후보의 독립 검증과 게시 영수증을 별도로 기록한다. 구독 잔여량·실제 토큰·미확정 사용량을 구분한다.

## 마일스톤

| 날짜 | 기준점 | 기능과 검증 범위 | 후속 |
| --- | --- | --- | --- |
| 2026-11-08 | [0ebe8db](https://github.com/RuinaR/DevOrchestra/commit/0ebe8db) | Windows P1 Mock 및 감사 콘솔. 합성 정책 동작. | 실제 Codex와 네이티브 격리는 이후 단계에서 검증했다. |
| 2026-11-09 | [d2ffa57](https://github.com/RuinaR/DevOrchestra/commit/d2ffa57), [3227cba](https://github.com/RuinaR/DevOrchestra/commit/3227cba) | 읽기 전용 Manager App Server 프로토콜과 공식 Windows 후보. | 지원 조건과 역할·통신 예산을 실제 환경에서 확인한다. |
| 2026-11-10 | [b049fff](https://github.com/RuinaR/DevOrchestra/commit/b049fff) | 오프라인 MXC 호스트 App Server stdio와 파일 격리 측정. | [MXC 검증](../P2-MXC-VALIDATION.md)의 호스트·시나리오 한계를 유지한다. |
| 2026-11-15 | [0b3ac58](https://github.com/RuinaR/DevOrchestra/commit/0b3ac58), [9800515](https://github.com/RuinaR/DevOrchestra/commit/9800515) | Controller 개발 사이클 기록과 승인된 원본 Git 작업 트리 절차. | 후보·SHA·푸시 결과와 원장 연결을 확인한다. |
| 2026-11-16 | [6ef4f08](https://github.com/RuinaR/DevOrchestra/commit/6ef4f08), [39efd9f](https://github.com/RuinaR/DevOrchestra/commit/39efd9f) | 소유 렌더 비교와 사전 빌드 Windows GUI 검증 경로. | 선택형 Godot 관찰과 범용 Controller 경계를 유지한다. |
| 2026-11-17 | [a557655](https://github.com/RuinaR/DevOrchestra/commit/a557655), [cd78d85](https://github.com/RuinaR/DevOrchestra/commit/cd78d85) | 대시보드 간소화, 구독 표시 직렬화, 종료 책임 지침. | 머신별 실행 파일과 현재 승인 상태는 재확인한다. |

[공개 커밋 이력](https://github.com/RuinaR/DevOrchestra/commits/main/) · [기존 작업 기록](../WORKLOG.md) · [제품 정의](../PROJECT-DEFINITION.md)

## 지침 및 문서 갱신

공개 번들은 버전 있는 기본 지침으로 등록 승인에 연결된다. 공개 기준점의 `public-v6` 이후 로컬에는 개인화된 역할 지침과 운영 규칙이 추가됐다. `public-v12`의 체크아웃에서 화면 크기·언어 적응, 명시적 상태 소유자, UI와 도메인 분리, 실제 독립 검증 원칙을 확인했다. 중간 단계의 완전한 배포 목록은 공개 Git만으로 확인되지 않는다. 운영 지침은 새 승인 전 동결된 번들의 실제 해시를 확인한다.

현재 직접 Git 방식에서 Controller가 `docs/development/CURRENT.md`, `CYCLE-LOG.md`, 개별 사이클 기록을 갱신한다. 공개 인수인계는 이 원본을 링크한다. 기능, 구조, UI, 주요 BUG·차단, 판정 또는 게시 기준점이 달라진 사이클은 인수인계의 정적 설명도 검토한다. 게시된 코드와 문서가 어긋나면 해당 기록의 기준점을 밝힌다. 로컬 원장·승인·로그 원본은 공개 기록에 포함하지 않는다.
