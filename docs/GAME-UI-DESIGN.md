# DevOrchestra — Game-like Operations Console UI/UX Specification v1.0

## 현재 배포 화면

현재 기본 진입점은 기존 Controller와 결합한 Tk Command Center다. 고정 왼쪽 탐색과 스크롤 가능한 작업 영역을 사용한다. 대시보드·프로젝트·개발 운영실·검증·승인·기록과 복구·설정의 작업 목적을 분리하고, 모델·빌드·승인 동작은 기존 Controller를 호출한다. 상태에는 색상과 텍스트를 함께 쓴다. 승인 대기는 권한을 생성하지 않는다.

정기 원장 조회는 백그라운드에서 실행하고 프로젝트 전환 이전의 응답은 화면에 적용하지 않는다. 기록은 60개 단위로 표시한다. 창 너비와 Tk 배율에 따라 동작 버튼, 실행 단계 및 역할 카드의 열 수를 줄인다.

구독 개발 시작은 프로젝트에 등록한 목표와 완료 기준을 사용한다. 시간·토큰 예산, 선택적 사이클 상한, 역할별 권한을 한 승인 화면에서 확인한다. 실행 상태에는 현재 사이클·역할·남은 토큰·승인 시간을 표시한다. 철회와 중단은 같은 실행을 회수하며 미확정 요청은 복구 판단 대상으로 남긴다.

P2.106에서는 배경·탐색·카드·입력을 검정과 중성 회색으로 구성한다. 파랑은 주요 동작과 포커스에, 청록·호박색·빨강은 의미가 있는 상태에만 사용한다. 프로젝트 검색에서 아래 화살표로 결과 목록에 진입할 수 있다.

| 기술 | 렌더링·입력 | 배포·전환 |
| --- | --- | --- |
| PySide6 Qt Quick | 장면 그래프, 레이아웃·애니메이션, QML의 접근성 속성 | QML 모듈·플러그인 수집과 Python 연결 계층이 필요 |
| PySide6 Qt Widgets | 맞춤 페인팅, 네이티브 키보드·포커스, Qt DPI | Windows EXE 구성 및 LGPL/GPL 또는 상용 배포 의무 확인 필요 |
| CustomTkinter | 둥근 컨트롤과 스케일링을 제공 | 기존 ttk 테이블·입력·검증 화면을 이식해야 하며 테마 리소스 배포가 추가됨 |
| Tk Canvas와 ttk 스킨 | Canvas 헤더·이미지 뷰어, 상태별 이미지 버튼, 기존 위젯 입력 계약 보존 | 기존 Windows 패키징과 화면 경로 유지. 복잡한 애니메이션·OS 접근성은 별도 검증 |

Qt Widgets·QML은 별도 PySide6 Essentials 6.10.2 시험 화면에서 실제 렌더링을 확인했다. Windows 시험 패키지는 195,350,581바이트였고 패키징 후 실행 검증은 실패했다. 기능 이전과 배포 검증이 끝날 때까지 Tk를 유지한다. 폰트·아이콘 출처는 [리소스 고지](UI-ASSETS.md)를 따른다.

## 초기 단계 이력

P2.4는 마지막으로 커밋된 합성 스트림 이벤트의 ID·원장 순번, 복구 대기, 역할별 도구 시작·승인 요청·해결·결과를 표시한다. 미커밋 수신은 현재 증거로 표시하지 않는다. 사용량은 합성 정산과 제공자 UNKNOWN을 구분한다. 승인된 독립 GUI 시험 환경을 확인하지 못해 실제 창·DPI·접근성은 NOT VERIFIED다.

P2.3은 선택 프로젝트의 세션 ID, 주기·검증 완료 수·수정 횟수, 중단 사유, 기록 시점 벽시간·합성 토큰·비용 게이지와 선정 근거를 원장에서 표시한다. 역할별 상세 감사에서 구조화된 제안과 오프라인 이벤트를 조회한다. 세션 제어는 CLI에 있으며 GUI는 이를 표시한다. 실제 창·DPI 검증은 미수행이다.

P2.2 콘솔은 검증된 등록부의 프로젝트 선택, 등록·설정·전역 예산 JSON 편집을 제공한다. 저장은 revision을 검사하는 Controller를 통한다. 요청 모델·추론, 선언된 목표·Git SHA는 실제 Mock 실행과 분리해 표시한다. 프로젝트별 실행·역할 필터에는 문맥, 계약, 도구 거절, 사용량 출처와 인수인계 증거가 포함된다. 실제 창·DPI 검증은 별도다.

작성: 2026-11-08 KST. 기준: `main@0ebe8db5ca7a05715862383dff09cd9492cef4c5`.
범위: **설계 및 P1.5 구현 기준**. Tk 기반 읽기 전용 Windows Command Center, 버전 1 Projection과 역할·실행별 감사 조회를 구현했다. 실제 창의 시각·DPI 검증은 NOT VERIFIED이며 P2 OS 격리는 BLOCKED다.

## 1. 제품 경험 목표

한눈에 여러 프로젝트 중 선택된 프로젝트의 상태, **전역 단일 실행**, 4가지 역할의 현재 실행/대기/완료 상태, 작업 이력과 진행도를 볼 수 있도록 **게임 퀘스트/전략 운영실 스타일의 Windows 데스크톱 콘솔**을 만든다. 에이전트 캐릭터는 운영 역할을 시각화하는 UI 장치이지 인간형 의식/감정 주장이나 실제 자율 실행의 증거가 아니다.

핵심 사용자는 소유자 한 명이다. 다른 앱을 사용하는 동안에도 쓸 수 있도록 포커스 강탈·전체 화면 캡처·다른 프로세스 조작 없이 오케스트레이터 소유 앱 안에서만 그린다. 실제 상태가 없으면 반드시 빈 상태 또는 NOT_VERIFIED로 보여 준다.

## 2. 가장 중요한 5대 UI 원칙

1. **Ledger-first:** `devorchestra/ledger.py`의 검증된 이벤트 및 `dashboard.project_view`의 읽기 모델만 시각화 데이터 원천으로 사용. 에이전트 텍스트는 생성 제안·서술로 분리하고 시스템 상태로 승격하지 않는다.
2. **One at a time:** 전역 단일 실행 슬롯 하나. UI는 전역 작업 큐를 표시할 수 있지만 다른 프로젝트/역할이 동시에 '작업 중' 상태가 되는 화면을 표시하거나 실행시키지 않는다. UI 갱신 스레드는 에이전트 실행 슬롯을 소유하거나 소유권을 우회하지 않는다.
3. **Truthful progress:** 실제 milestone 완료 기준과 verified evidence가 없으면 전체 프로젝트 완성률 %를 추정하지 않는다. `N개 중 K개 확인` 또는 `자료 없음`을 표시. 실행 단계와 제품 개발 단계는 별개의 진행 막대.
4. **Drill-down:** 상태 배지·사건·역할 카드 클릭 시 run ID, agent_run_id, source/seq, Git SHA, 실제 test/approval/permission 영수증, 시간, 자원, token 사용량의 출처와 결측 이유를 조회.
5. **Safe controls:** 시작/재개/일시정지/취소/승인은 UI 입력이 아니라 trusted Runner의 명시된 상태 전이·정확한 run ID·generation 및 권한 재검사를 호출한다. P1 CLI가 이미 제공하지 않는 기능은 작동하는 것처럼 보이는 버튼으로 만들지 않는다.

## 3. 레이아웃 정보 구조

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ DevOrchestra    [GLOBAL IDLE/RUNNING/BLOCKED]  [현재 Run]    [리소스]       │
├──────────────────┬───────────────────────────────────┬───────────────────┤
│ 프로젝트          │ 선택 프로젝트 개요                │ 현재 에이전트     │
│ - 목표 문서       │ [Milestone progression / gates]   │ 실행 단계        │
│ - 등록된 레포     │ [작업 단계: PLAN→DEV→REV→VER→DEC] │ 승인/차단 근거   │
│ - 진행상태        │ [4 role/operator cards]           │ 예산/시간        │
│                  │ [활성 작업 카드 / 작업 목표]       │ 안전 상태        │
├──────────────────┴───────────────────────────────────┴───────────────────┤
│ 이벤트 타임라인 / 프로젝트별·작업별·역할별 상세 감사                     │
└──────────────────────────────────────────────────────────────────────────┘
```

권장 화면: `Command Center`(대시보드), `Project Operations`(목표/프로젝트 상세), `Agent Records`(에이전트별 실행·검토), `Evidence & Tests`(diff/test/권한), `Settings & Approvals`(범위/모델/승인), `Recovery`(중단·복구). 최초 버전은 단일 창의 대시보드 + 역할별 감사 상세만 구현해도 좋다. UI 복잡도를 줄이기 위해 단계적으로 확장한다.

## 4. 스타일과 게임형 연출 계약

- 색조: dark navy/charcoal 바탕, 푸른색 정보, 청록 성공, 호박색 승인 필요·주의, 붉은색 실패/차단, 회색 미실행. 색깔만으로 상태를 구분하지 않고 텍스트·아이콘·툴팁을 병행한다.
- 에이전트는 별개의 역할 카드/심벌/아바타(Manager: 지휘, Developer: 코드, Reviewer: 조사, Verifier: 방패), 명칭과 상태를 표시한다. **4개 카드가 존재해도 active count ≤ 1**.
- 작업 흐름은 '퀘스트'처럼 보여주되 실제 `role_finished`와 성공 영수증이 있을 때만 완료 표시. 검토 실패→수정 요청 루프는 화살표로 명시하되 미래 단계의 완료를 예측하지 않는다.
- 진행 연출은 작은 펄스, 부드러운 값 보간, 최근 상태 변경 강조 정도만 허용. 30/60fps 무의미한 애니메이션 루프, GPU 고부하, 지속적인 알림음, 화면 흔들림/과도한 반짝임을 피한다. 사용자 Reduce Motion 옵션.
- UI/UX: 100%·125%·150%·200% DPI, 최소 창, 스크롤, 긴 로그/파일명, 키보드 포커스, 확대/고대비/스크린리더 접근성을 고려. 운영 상태 색상은 테마와 무관하게 판독 가능해야 한다.
- 실제 기록과 데모 시각화는 명시적인 `DEMO / SYNTHETIC` 표시로 구분하며 기본 화면에서는 데모를 운영 상태로 혼용하지 않는다.

## 5. 상태 데이터 계약

`projection.dashboard_projection_v1`을 순수 읽기 투영으로 구현했다. P1 `project_view(events, project)`와 검증된 원장 이벤트가 근거다. 읽기 오류, 순번 역행과 다른 원장 계보를 감지하면 화면을 BLOCKED로 바꾸고 현재 결과를 지운다. 워터마크는 창 수명 동안 유지되며 재시작을 넘는 외부 앵커는 없다.

권장 항목:
- `schema_version, as_of_utc, observation_kind, project_id, run_id, task_id, generation, global_state, active_role, active_agent_run_id?`
- `milestone_gates[]`: goal_id, acceptance_criteria, evidence_refs[], status ∈ NOT_STARTED/ACTIVE/PASS/FAIL/BLOCKED/UNKNOWN; 퍼센트는 분모·모집단이 명시됐을 때만.
- `pipeline_phases[]`: phase, role, state, role_started_seq?, role_finished_seq?, completed_with_evidence. Manager 계획/최종 판정은 별도 단계지만 같은 역할 카드.
- `role_cards[]`: role, last_run_id, last_agent_run_id, state, model_id?, reasoning?, started_at?, ended_at?, duration?, result?, evidence_seq?, measured_vs_synthetic.
- `resources`: elapsed, time_budget, actual_input_tokens?, output_tokens?, cached_tokens?, cost?, synthetic_tokens?, measurement_source. 0과 UNKNOWN/NOT_APPLICABLE을 절대 혼동하지 않는다.
- `recent_events[]`: seq, timestamp, actor, kind, evidence, status, redacted_summary, evidence_ref.
- `safety`: lease_state, owner_generation?, approval_state, current_grant_scope?, sandbox_status, dirty_worktree?, next_action_allowed?.
- `snapshot_version`: 감사 이벤트의 max sequence/hash. UI는 읽기 전에 원장 검증, 상태 역행/오래된 요청은 폐기.

보안: 원본 Discord/Steam/KIS 정보·토큰·모델 원문/전체 shell 인자 등 민감 자료를 기본 패널에 표시하지 않는다. 화면에서 공개 가능 요약과 개인 로컬 감사 뷰를 분리한다. UI 오류 시 마지막 정상 상태를 '현재'로 표시하지 않는다. 필요 시 `STALE / LAST VERIFIED at...`로 표시.

## 6. UI 컨트롤 동작

P1.5 화면에는 프로젝트 선택, 감사 필터, 역할 상세, 새로고침만 제공한다. 아래 실행 제어 계약은 향후 쓰기 경로 설계이며, 이번 버전의 실행 제어는 기존 CLI에서 수행한다.

- `Run`: 실행 전 현재 run/generation, 잠금·승인·허용 명령·프로젝트 확인. 현재 P1 합성 읽기도 기존 소유자 승인 게이트를 적용한다.
- `Pause`: 소유 Runner가 단계 경계에서 상태를 보존했을 때 완료 표시. 진행 중 실제 child에 대한 중단은 P2 집행이 검증될 때까지 비활성.
- `Cancel`: 정확한 대상 ID + 영향·유실 경고; 현재 지원하는 모의 상태만.
- `Approve`: 권한 요청/대상/만료·사용량 한도를 표시하고 사용자 명시 클릭 후 Runner가 승인을 기록한 다음에만 적용. 다른 주체 승격 안 됨.
- `Recover`: 먼저 미완료/UNKNOWN/원장·프로세스 소유권을 보여주는 읽기 전용 점검. 자동 재실행 금지.
- `Project picker`: 현재 P1 합성 프로젝트만 표시하고 실제 외부 프로젝트 선택은 P2/P3 권한 격리 후.
- 화면에서 복잡한 원장 파일 경로/내부 토큰을 바로 노출하지 않고 copy sanitized run/agent ID·증거 표시는 지원 가능.

## 7. P1 실제 코드 통합 지점

- `ledger.py`: SQLite 검증 읽기는 변경하지 않고 그대로 사용. 새로운 화면 때문에 쓰기 원장 형식을 임의 변경하거나 해시 체인을 깨지 않는다.
- `dashboard.py`: 기존 순수 `project_view`와 터미널 `render`는 그대로 유지한다. `projection.py`가 GUI용 버전 1 읽기 계약을 제공한다.
- `runtime.py`: 상태·PHASES 및 role_started/finished, owner gate, milestone·초기 설정을 사실 원천으로 재사용. UI가 `mock_role` 또는 상태 전이를 직접 호출하는 구조 금지.
- `__main__.py`: 기존 CLI/Mock smoke regression 유지. GUI 진입점을 별도 모듈로 구성하고 UI 미설치 환경에서도 핵심 실행 테스트 가능해야 한다.
- GUI 기술 선택: Tcl/Tk 8.6과 Python `tkinter`를 사용한다. 기존 Python Projection을 직접 재사용하고 별도 패키지, IPC, 게임 엔진 없이 구현할 수 있다. `ttk` 사용자 테마와 자체 색·글꼴·레이아웃 토큰을 사용한다. 별도 이미지·아이콘·폰트 파일을 배포하지 않는다. [Python tkinter](https://docs.python.org/3/library/tkinter.html)
- WPF는 XAML 템플릿, 해상도 독립 렌더링 및 Windows 통합이 강점이다. 현 Python 원장 검증과 결합하려면 추가 .NET 빌드 및 IPC 계약이 필요하다. PySide6은 동일 프로세스에서 Python을 재사용하고 테마 및 접근성 도구가 풍부하나 Qt 런타임 패키징과 라이선스 검토가 추가된다. [Microsoft WPF](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/overview/) · [Qt for Python 라이선스](https://doc.qt.io/qtforpython-6/licenses.html)
- P1.5는 정적 장식과 1.5초 폴링을 사용한다. 색 외 텍스트 배지, 키보드 탐색 및 스크롤을 제공한다. 스크린리더 연동, 다중 DPI 레이아웃과 장시간 원장 폴링 비용은 미검증이다. 모델의 실시간 생존 여부는 관측하지 않으며 열려 있는 역할 의도 하나만 UNKNOWN으로 구분한다.

## 8. 품질 게이트 / 시각화 테스트

1. 이벤트 없이 빈 상태: 프로젝트/역할 진행도 0 또는 UNKNOWN; 가짜 active 표시 없음.
2. 각 역할 실행: manager plan → developer → reviewer → verifier → manager decision 순서를 렌더, active 카드 동시 2개 이상 불가.
3. role_started 뒤 비정상 종료: 이전 실행을 여전히 RUNNING으로 표시하지 않고 UNKNOWN/RECOVERING/BLOCKED 구별.
4. 취소/실패/검토 거절/승인 대기/한도 초과: 퀘스트 완료와 구별하고 적절한 근거 표시.
5. 합성 사용량/토큰 표시: 실제 토큰으로 승격 금지. 미측정 비용=UNKNOWN 또는 NOT_APPLICABLE.
6. 원장 손상/해시 불일치: 깨진 기록으로 그럴듯한 대시보드를 만들지 않고 BLOCKED와 원인 표시.
7. 두 프로젝트의 이력: 프로젝트/Run/Agent 필터 간 누출 없음. 새 실행 또는 선택 전환 후 stale UI 값 섞이지 않음.
8. UI 재시작, 빠른 버튼 연타, 예산/승인 만료, 검증기 FAIL 및 삭제/불완전 데이터까지 포함.
9. UI는 별도 Windows 격리 환경의 **자체 창에서만** 검사. 사용자 현재 데스크톱 캡처·포커스 강탈·외부 앱 조회 금지.
10. 현 P1 테스트 8건과 CLI smoke를 최종 HEAD에서 재검증. 변경 범위에 따른 UI 테스트는 별도 수치로 보고.

## 9. 구현 로드맵과 안전 우선순위

- **P1.5 — 문서/읽기 모델/Mock UI:** 실제 원장 기반 버전된 Projection, 역할별 카드/단계 타임라인 및 읽기 전용 대시보드 스켈레톤. OS 격리가 불완전해도 **실제 Codex/프로젝트/네트워크 호출 없이** 합성 데이터에서 검증 가능.
- **P2 — OS 경계 검증(최우선 보안 게이트):** 파일/네트워크/자격증명/프로세스 거부 시험과 실제 자식 소유권·중단 복구; 미완성 시 실제 실행 차단.
- **P2.5 — 게임형 Windows 시각화 다듬기:** 접근성·DPI·상태 변환·에이전트별 기록/필터·화면 성능/CPU 절약. 우선 구현한 UI를 무리하게 대형 SPA로 재작성하지 않는다.
- **P3 — Codex 어댑터:** 별도 승인으로 실제 한 에이전트 연결 → 역할 전체 순차화, 토큰 실계측/정식 감사/코드 권한.
- P4~: 개발 자동화, 실제 프로젝트 등록, 사용자 관리 설정, 장기 QA.

UI는 중요한 요구지만, 게임형 연출이 P2 보안/실제 Codex 연결보다 우선해 기능 개발을 지연시키면 안 된다.

## 10. 문서 연계

[PROJECT-DEFINITION](PROJECT-DEFINITION.md), [ARCHITECTURE-ROADMAP](ARCHITECTURE-ROADMAP.md), [GLOBAL-SAFETY-POLICY](GLOBAL-SAFETY-POLICY.md), [CURRENT-STATUS](CURRENT-STATUS.md)를 함께 유지. 현재 구현되지 않은 기능을 완료했다고 표시하지 않는다.
