# DevOrchestra — Architecture & Milestone Gates (2026-11-08)

상태: **P1 Mock 실행 기반 구현**. 아래 장기 구조 중 실제 프로젝트 실행과 OS 격리는 설계 단계다.

## P1 구현 및 기술 선택

- Python 표준 라이브러리 `sqlite3`와 `ctypes`를 사용한다. 설치된 Windows 환경에서 추가 종속성 없이 결정적 역할, 트랜잭션 및 Win32 호출을 검증할 수 있다. .NET은 Windows API 형식 안전성 및 GUI 통합의 대안이고, Node/TypeScript는 Codex SDK 연동의 대안이다. 실제 어댑터 선택은 P2 격리 검증 이후 별도로 한다. [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- Win32 `Global\` 이름의 뮤텍스를 잡은 동기 호출만 역할을 실행하거나 원장을 변경한다. 커널 소유권 확인 후 작업한다. 진행 중 실행의 세대와 ID, 이벤트 끝 해시를 원장에 보존해 중복 입장과 오래된 쓰기를 거부한다. P1이 지원하는 영속 입장 범위는 단일 Windows 사용자 프로필과 그 고정 원장이다. 별도 프로필 간 공용 영속 상태는 후속 단계다. [Microsoft Mutex Objects](https://learn.microsoft.com/en-us/windows/win32/sync/mutex-objects) · [Kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)
- SQLite의 원자적 커밋으로 상태 이벤트와 끝 앵커를 함께 저장한다. 순번·SHA-256 체인·앵커·SQLite 상태를 검증한다. JSON 파일은 상태와 감사 이벤트의 원자적 쓰기 및 복구 처리를 별도로 구현해야 한다. 해시 체인은 우발 손상 및 단순 삭제를 검출한다. DB 전체를 다시 쓰거나 과거 사본으로 교체할 권한을 가진 주체에 대한 서명·외부 앵커는 미구현이다. 검증은 전체 원장 크기에 비례하며 장기 원장 성능은 미측정이다. [SQLite Atomic Commit](https://www.sqlite.org/atomiccommit.html)
- P1은 실행기 자체의 고정 함수만 호출하며 자식 프로세스를 생성하지 않는다. 역할 시작 이후 종료 이벤트가 없으면 다음 역할은 차단된다. 재시작으로 자동 재실행하지 않으며 복구 후 취소한다. 실행 중인 실 작업에 대한 강제 종료·자식 회수는 P2 게이트다.
- P2 후보는 검증된 Windows Sandbox/VM과 파일 전달 정책이다. 네트워크·클립보드·공유 경로는 검증 대상이다. Job Object는 소유 프로세스 트리와 자원 제어에 사용 가능하며, 별도 파일·네트워크 격리가 필요하다. [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/) · [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- 대시보드의 현재 상태·단계는 검증된 이벤트, 역할 설명은 별도의 Mock 결과, 배지는 장식이다. 상세 감사 JSON에서 원본 근거를 조회한다. 합성 토큰 외 실제 사용량·비용·Git은 미계측으로 표시한다.

## 1. 컴포넌트

```text
Windows UI (작업 등록/모델 선택/승인/일시정지/감사 보기)
    |
Trusted Controller (권한·전역 단일 실행·모델 설정·예산·비밀 관리)
    |
Durable Scheduler (only ONE project + task + role + process tree)
    |
[Manager] -> [Developer] -> [Reviewer] -> [Verifier] -> [Manager]
    |            |              |             |
 read-only    isolated       read-only    deterministic
 plan         worktree       audit        build/test
    |
Versioned Project Contracts / Durable Journal / Checkpoints / Git Evidence
```

정책 및 증거 이벤트는 모델이 직접 변경할 수 없는 별도 trusted controller 소유다. 모델은 단 하나씩 실행하고 모든 역할 전환 전에 기존 자식 프로세스와 비동기 작업이 종료되어야 한다. 에이전트 간 내용 전달은 버전과 출처가 있는 사실·검증 로그·최소한의 맥락만 포함한다.

## 2. 저장 계층 제안

- `GlobalSettings`: 사용자별 모델/추론 설정, 기본 PC 자원 한도 및 안전 잠금 설정. 비밀값은 별도 암호화 저장.
- `ProjectRegistry`: 명시 등록된 레포 ID/허용 로컬 root·목표·AGENTS·테스트·권한·branch·현재 작업 SHA. 다른 프로젝트 자동 발견 금지.
- `RunLedger`: 영속 run/task/role/status/lease/seq/epoch/event hash 및 작업 상태머신. 원장 손상이면 안전 정지.
- `AgentAudit`: 역할별 계획·실제 실행 tool receipt·모델 ID·사용량·오류·검증·중단. 원문은 opt-in 로컬 보호.
- `ApprovalStore`: 사람의 승인을 작업/도구/경로/도메인/기간 및 예산에 바인딩. 재시작/모델 재시도에 자동 확장 금지.
- `ArtifactManifest`: Git SHA, diff, 변경 파일·빌드/테스트 결과의 안전한 식별자. 서명·hash만으로 작업의 의미적 적합성을 증명하지 못한다.

로컬 DB를 사용하더라도 기본 권한, 트랜잭션/백업/무결성/마이그레이션, 개인 데이터 분리, 로그 삭제/만료 규칙이 필요하다. 운영 DB를 공개 저장소에 보관하지 않는다.

## 3. 모델과 Codex 실행 어댑터

- 특정 제품 계정/API/모델이 지원되지 않으면 불가능한 기능으로 보고한다. 지원한다고 가정해 내부 API/웹 UI 스크래핑·세션 탈취를 사용하지 않는다.
- 관리자, 개발자, 검토자 모델/추론 설정을 **독립** 저장하되 전역 활성 에이전트는 최대 하나.
- 실 사용량은 SDK/API가 제공한 토큰(입력/출력/캐시)과 추정값을 나눠 기록. 사용량 미제공 시 UNKNOWN 및 별도 단위 사용량 제한을 검토.
- ChatGPT 구독 플랜·Codex 앱의 잔여 할당량과 API 사용 요금은 동일하지 않다. 사용자 계정 실제 제한을 임의 추정하거나 무제한이라고 주장하지 않는다.

## 4. 실행과 복구 의사코드

```text
on_start:
    verify_db_policy_git_status_and_global_lock()
    fail_closed_on_ambiguous_previous_run()
    no_auto_resume_of_live_or_unbounded_operations()

run_one(project, task):
    validate_explicit_grants_and_resource_budget()
    acquire_exclusive_global_lease()
    snapshot_trusted_repo_head_and_worktree()
    for role in [MANAGER, DEVELOPER, REVIEWER, VERIFIER, MANAGER]:
        ensure_previous_agent_and_owned_children_exited()
        create_agent_run_id_and_fenced_permission_scope(role)
        execute_one_role_with_strict_time_token_resource_limits()
        append_signed_or_hashed_audit_receipts()
        if failure_or_approval_required: stop_and_checkpoint()
    validate_effective_permissions_and_current_git_commit()
    if approved: publish_only_allowlisted_artifacts_and_commit()
    persist_final_state_then_release_global_lease()
```

이 코드는 설계 의사코드이며 현재 기능 아님. 임계 구간과 종료 단계 중 프로세스 비정상 종료까지 포함한 실제 원자성·멱등성 검증 필요.

## 5. 구현 단계별 완료 조건

| 단계 | 구현 목표 | 통과 게이트 |
|---|---|---|
| P0 | README/안전/위협 모델·역할 정의 | 문서 링크·일관성·공개 diff 검사 |
| P1 | 단일 스케줄러·mock 에이전트·상태 원장·잠금 | 중복 실행 0, 중단·재시작 누락·중복·고아 작업 없는 격리 시험 |
| P2 | 실제 OS 권한 격리·path/net allowlist·자원 제한 | 허용 외 경로/네트워크/프로세스 접근 부정 시험, 재시작·reparse 거부 |
| P3 | Codex CLI/SDK 연동과 역할별 문맥/로그/사용량 | 관리자→개발자→검토자 각 1회 순차·지표·승인 검증 |
| P4 | 독립 리뷰·검증·Git 확정·보고서 | 최신 커밋·PASS/FAIL 명확, 원본 미커밋 자료 보존 |
| P5 | Windows UI·프로젝트 카탈로그·감사 검색 | 실제 저장/중단/재개, 에이전트별 내역 및 예산 표시 |
| P6 | 1개 실제 예제 프로젝트의 안전한 작업 자동화 | 지정된 비운영 복제본 대상 독립 테스트·수동 점검 후 확대 |

## 6. 위험/차단 게이트

- P1 전의 모델 실제 실행은 금지. P2 OS 격리가 실패하면 mock으로 제한.
- 실거래/실Discord/타프로세스·게임 파일 접근·대량 백업 복원·머신 설정 변경은 기본 자동 루프 바깥.
- 테스트 상태 유실·전역 락 불명·출처 불명·환각된 성공/명령·비밀 유출 의심 -> 즉시 BLOCKED.
- 사용자 PC 작업을 위해 Windows 권한이 필요한 기능은 VM/별도 컨테이너 등 검증된 환경 선택; Job Object만으로 충분하다고 판단하지 않는다.
- P6까지는 어떤 프로젝트도 무인 운영/배포/실거래를 정식 지원한다고 표시하지 않는다.

## 7. Windows UI 초안

좌측 프로젝트·작업 목록 / 중앙 단계 타임라인 / 우측 현재 agent·명령·승인·자원 / 탭별 관리자·개발자·리뷰어·검증기 로그. 기본 화면은 최신 실측 상태와 PASS/BLOCKED만 요약하고 상세 감사에서는 근거 SHA·전후 diff·요청/승인 근거·중단 사유를 추적한다.

시간·토큰·비용은 다른 개념이므로 개별 상한과 사용량을 표시한다. 사용자 PAUSE/STOP은 UI가 사라져도 trusted runner가 수행해야 한다. 복구 시 사용자 승인 범위가 확인되지 않으면 자동 실행하지 않는다.

## 8. 문서 규칙

최신 작업 단계가 착수되면 `docs/CURRENT-STATUS.md`, `docs/WORKLOG.md`, `docs/AI-HANDOFF.md`를 생성·갱신하고, 검증된 구현/합성/실행 환경/실서비스를 구분한다. 원시 에이전트 로그, 실계정 정보, 로컬 디렉터리 구조, 비밀 프롬프트는 Git 추적 대상이 아니다. 자세한 규칙은 [GLOBAL-SAFETY-POLICY](GLOBAL-SAFETY-POLICY.md)와 [AGENTS](../AGENTS.md).

## 게임형 UI 확정 설계 (2026-11-08)

[GAME-UI-DESIGN](GAME-UI-DESIGN.md)은 P1 원장 기반 게임형 대시보드와 역할 카드, 정직한 진행도, 상태 드릴다운, 가벼운 효과와 GUI 안전 게이트를 규정한다. P1.5에서 순수 읽기 모델/Mock 시각화, P2에서 OS 격리 검증을 우선, P2.5에서 네이티브 GUI 완성도를 향상한다. UI가 잠금·상태·권한의 소유자가 되어서는 안 된다. GUI 자체가 아직 구현되었다고 주장하지 않는다.
