# DevOrchestra — Architecture & Milestone Gates (2026-11-08)

## P2.21 실연동 전 수명주기 계약

실제 어댑터는 P2.20과 같은 원장 트랜잭션에서 역할·도구·자식 각각의 입장 의도를 승인해야 한다. 종료 예약보다 먼저 승인된 의도만 기존 소유 작업으로 다루고 이후 생성은 차단한다. 도구가 자동으로 만드는 자식까지 이 경계를 강제하고 감사할 수 없다면 실연동을 차단한다. 향후 취소·회수는 직접 생성 시 기록한 소유권·세대·Job 영수증으로만 처리한다. 소유 CLI 종료나 표식만으로 자식 트리 회수 또는 정상 체크포인트를 확정하지 않는다. 실제 어댑터·취소는 미연결이다. [MXC 실패 분석](P2-MXC-VALIDATION.md#p221-네이티브-자기-점검-실패-분석)

## P2.20 종료 접수와 배포 준비

예약 접수만 전역 실행 슬롯 밖에서 처리한다. 같은 SQLite 원장의 짧은 쓰기 트랜잭션으로 요청 ID·현재 실행 세대·기한을 기록하며, 역할·작업·소유 자식의 입장 의도와 순서를 원자적으로 결정한다. 접수보다 먼저 기록된 역할 의도는 이미 입장한 작업으로 다룬다. 해당 역할의 감사 기록은 검증된 이전 체인과 예약 이벤트만 사이에 추가됐을 때 이어 붙인다. 취소·최종 체크포인트·재개는 계속 전역 슬롯을 보유한다. 오래된 세대나 이미 끝난 요청의 ID는 재접수하지 않는다. 장애 후 같은 원장을 다시 열어 예약을 복구한다. 새 DB 형식이나 자동 데이터 마이그레이션은 없다.

GUI는 예약 접수·체크포인트 명령을 백그라운드에서 처리하고, 창 닫기도 같은 명령으로 연결한다. 슬롯 대기가 상한을 넘거나 원장 확인이 실패하면 창을 닫지 않는다. 실제 외부 요청, 파일 쓰기, 테스트, Git 작업은 원장 트랜잭션과 원자적으로 커밋할 수 없다. 향후 각 부작용 전에 세대·멱등성 키·대상 해시가 있는 의도를 남기고, 재시작 뒤 독립 결과·검역 해시·소유 자식 회수를 확인할 수 없으면 UNKNOWN으로 격리해야 한다. 현재 이어하기는 기존 동결 합성 세대와 감사·승인·예산·설정 확인으로 한정한다.

배포 기본 후보는 Windows x64에서 고정한 CPython/Tk/SQLite를 포함하는 PyInstaller 폴더형 ZIP이다. EXE와 DLL·Tcl/Tk 파일 목록을 검사하기 쉬워 첫 후보로 선택했다. 현재 PyInstaller·Nuitka·cx_Freeze가 확인되지 않아 설치하거나 번들을 만들지 않았다. 기존 소스 실행은 유지한다. 별도 승인된 빌드에서는 깨끗한 Windows x64 사용자 프로필에서 관리자 권한 없이 압축 해제·첫 실행·설정 보존·버전 교체·기존 스키마 거부 동작을 검증한다. 실행 파일 서명과 파일별 해시가 든 서명된 매니페스트를 검증한 뒤 프로그램 파일만 버전별 폴더에 교체하고, 사용자 LOCALAPPDATA에는 쓰지 않는다. Codex 미설치 안내와 서명·버전·MXC 유효성 확인은 별개다. 포터블 배포·업데이트 구현과 다른 PC 실측은 BLOCKED다. [PyInstaller 폴더형 배포](https://pyinstaller.org/en/stable/operating-mode.html#bundling-to-one-folder)

## P2.19 종료 예약과 배포 경계

Trusted Controller는 같은 전역 슬롯 아래 예약·취소·체크포인트·재개를 처리한다. 합성 역할 간에 슬롯을 반환하고, 예약이 원장에 커밋된 뒤 새 작업·역할·세션·합성 자식 입장을 막는다. 실행 중 클릭은 현재 합성 호출이 슬롯을 반환할 때까지 UI가 제한 시간 내 대기한다. 제한을 넘기면 UI를 유지하고 재확인을 요구한다. 현재 호출은 동기 합성 함수이며 일반 자식 취소 경로는 미연결이다. 소유권이 불명확하면 UNKNOWN으로 남긴다. Windows 종료 API는 사용하지 않는다.

체크포인트는 파일 교체 없이 같은 SQLite 감사 트랜잭션의 한 이벤트로 기록한다. 요청 ID, 체크포인트 직전 순번·체인 해시, 실행 및 세션 참조, 상태, 마지막 완료 역할, 승인 해시·만료, 사용량·예산, 변경 경로와 합성 diff 해시를 보존한다. 설정과 정책의 변경, 끊긴 역할 또는 손상된 원장은 재개를 차단한다. 원본 Git과 실제 복제본은 열지 않으며 Mock 식별자와 등록부 해시만 사용한다. 재개는 기존 세대의 입장을 다시 열며 역할을 자동 시작하지 않는다. 다음 합성 작업 진행은 기존 CLI에서 명시적으로 요청한다. 복구 증거가 필요한 상태는 자동 해제하지 않는다.

Windows x64 소스 배포를 기본으로 한다. Tk 마법사는 환경, 경로, 프로젝트 등록을 분리해 로컬 등록부에 저장한다. 기본 상태 경로는 사용자별 LOCALAPPDATA 아래이며 기존 등록 덮어쓰기를 거부한다. 경로 충돌은 문자열 수준으로 검사한다. 실제 경로의 재분석 지점과 복제본 신원 검사는 실연동 선행 조건이다. CLI 존재 확인과 서명·버전·MXC 유효성은 별도다. 후속 배포는 서명된 사용자별 포터블 패키지를 목표로 하며 번들 런타임, 서명·해시·업데이트 검사와 깨끗한 PC 검증은 미구현이다.

P2.18에는 정제 오류 범주만 남아 과거 stderr에서 실패 위치를 복원할 수 없다. 추후 별도 승인된 실행에는 원문을 메모리에서만 처리하고, 알려진 런처·프로필·프로세스 생성 실패 문구와 숫자 코드만 허용 목록으로 분류한다. 알 수 없는 문자열은 UNKNOWN으로 남기고 경로·환경·원문을 저장하지 않는다. 이번에는 재실행하지 않았다.

## P2.12 제공자 감사와 로컬 테스트 영수증

App Server 이벤트 저널은 제공자의 턴·도구 관측을 보존한다. `turn/completed`, 도구 종료 보고 및 모델 PASS 문자열은 Controller 테스트 영수증으로 승격하지 않는다. App Server의 Windows sandbox 준비 상태 역시 MXC 구현을 단독으로 식별하지 못한다. [OpenAI Codex App Server](https://learn.chatgpt.com/docs/app-server)

향후 신뢰 Controller만 같은 전역 슬롯과 작업 세대에서 승인된 복제본의 전체 전후 파일 집합과 해시를 독립 수집하고, 검증된 OS 경계에서 승인된 정확한 테스트 인자를 실행한다. `ControllerTestReceipt` 계약은 동결 승인, Verifier 실행, 복제본, 전후 인벤토리, 실제 종료 코드, 소유 프로세스 회수 증거와 감사 순번·해시를 연결한다. 발행 전에 CLI/App Server 및 자식 소유권을 증명하고, 원장 추가 이후에만 영수증을 인정한다. 범위·귀속·해시·회수·원장 중 하나라도 불명확하면 차단한다. 스테이징 전에 전체 인벤토리를 재확인하고 검사 이후 변경도 막아야 한다.

현재 실제 영수증 발행과 검증된 출처 조회는 미연결이다. 새 합성 후보는 `SYNTHETIC` 및 `live_commit=BLOCKED`로 기록한다. 이전 후보도 합성 재확인만 허용한다. 형식상 맞는 Controller 영수증을 직접 제출해도 실사용 게이트는 BLOCKED다.

## P2.11 오프라인 커밋 후보

신뢰 Controller가 선택한 전용 합성 원장, 현재 승인, 시각 및 변경 전후 번들을 사용한다. 동결 승인 해시는 프로젝트·세션·작업·실행 세대·역할 실행 ID·목표·테스트 인자·허용 파일의 변경 전 해시·예산·유효기간·커밋 허가를 묶는다. 후보 입력은 승인 해시, 감사 체인 끝 해시 및 변경 후 목록을 제출한다.

감사 체인은 계획 → 개발 → 검토 → 검증 → 최종 Manager 순이다. Developer와 Reviewer는 같은 변경 후 목록에 귀속되고, Verifier는 Reviewer 기록 해시와 승인된 테스트의 종료 코드 0을 제시해야 한다. 최종 Manager는 그 Verifier 기록을 참조해 완료 판정을 내려야 한다. Controller가 공급한 합성 기록만 사용하며, 자체 제출한 결과나 해시는 테스트 증거로 채택하지 않는다.

기존 검역의 정규 경로와 UTF-8 `.txt` 제한을 적용한다. 승인된 파일의 수정만 다루며, 전후 목록이 정확히 일치해야 한다. 승인되지 않은 파일, 추가·삭제, 실행 파일, 링크·reparse 메타데이터, 손상 및 재전송을 거부한다. 이는 합성 번들 검증이며 실제 복제본의 경로 안전성이나 변경 목록 완전성을 입증하지 않는다.

후보는 기존 전역 슬롯과 감사 원장의 비교 후 추가를 사용해 한 번 기록한다. 재확인은 현재 승인과 새 전체 합성 변경 번들을 다시 검사한다. 실제 적용 전에는 Controller가 복제본 전체 변경 목록을 독립 수집하고, OS 경로 검증과 검사·스테이징 사이의 변경 방지를 입증해야 한다. 현재 후보 API는 파일을 적용하거나 Git을 호출하지 않는다. 푸시는 별도 권한이다.

## 실제 자동 개발까지의 우선순위

1. **P0 · 안전 경계.** 별도 승인된 P2.10 1회로 MXC 시작 조건을 확인한다. 이후 합성 자원만으로 B의 파일·자격증명·네트워크와 외부 프로세스·데스크톱 경계를 증명한다. 실제 게임과 타 사용자 앱은 시험 대상에서 제외한다. 공유 데스크톱에서 안전하게 시험할 수 없는 경계는 차단 상태를 유지한다. MXC 실패 시 자동 폴백은 없다.
2. **P0 · 소유권과 호스트 보호.** A는 MXC와 외부 Job 결합, 시간·자원 제한, 취소·재시작 후 소유 트리 회수를 입증한다. C는 Controller·원장·원본 저장소 보호와 회수 후 검역·적용을 입증한다. 시작 성공만으로 A/B/C를 승격하지 않는다.
3. **P1 · 실행 없이 구현 가능한 제어 흐름.** 구조화된 요구 기능을 확인하고 금지 또는 미분류 요구를 보고한다. 다음 구현 우선순위는 승인·작업 계획·검증 증거를 결합하는 오프라인 커밋 후보 검증과 자원 종료·예산·감사 증거 누락의 합성 회귀다.
4. **P2 · 승인된 실연동.** A/B/C, 비간섭 및 사용자 동의가 확인된 구성에서만 P3 모델·도구 호출 경로를 연다. 단일 프로젝트·작업·역할·슬롯과 승인·시간·토큰·비용 상한을 유지한다. 빌드·테스트는 생성 코드와 동일한 OS 경계에 포함하고 커밋은 Verifier 증거와 별도 Git 승인 이후 Controller만 수행한다.

현재 `live-preflight`는 선언된 기능을 정적으로 분류한다. 미분류, 금지, 격리 미검증 모두 BLOCKED이며 선언 누락이나 무해해 보이는 선언으로 우회할 수 없다. 이 검사는 코드 분석이나 OS 집행이 아니므로 실제 실행은 제공하지 않는다. [MXC 시험 계약](P2-MXC-VALIDATION.md)

## P2.9 MXC 부트스트랩 분리

상위 trusted launcher의 부트스트랩 경로, 내부 명령의 환경 상속, 유효 MXC 파일·네트워크 정책을 별도 검증한다. 앞선 두 실패는 파일 접근 거부 증거로 사용하지 않는다. [런처 계약](P2-MXC-VALIDATION.md#p29-런처와-내부-명령의-경계)에 따라 별도 승인된 시작 진단 이후에만 파일 시험 승인을 검토한다. Win32 Job과 기존 전역 단일 실행은 유지한다. 폴백과 P3는 차단한다.

## P2.7 네이티브 MXC 우선 검토

현재 PC에서 명시적 MXC를 우선 검토한다. 전역 단일 슬롯과 Win32 Job을 유지하며, 외부 Job과 MXC의 결합을 별도로 검증한다. 유효 파일·네트워크 프로필 외에도 데스크톱 비간섭, Controller·원장 보호, 소유 자식 회수 및 검역이 입장 조건이다. 폴백은 차단한다. VirtualBox는 보류한 후보이며 이전 P2.5/P2.6 계획은 이력이다. [호환성·시험·P3 조건](P2-MXC-VALIDATION.md)

## P2.5 환경 선정과 P3 입장

[P2.5 격리 실행 환경과 인수 시험](P2-VM-VALIDATION.md)이 후보 비교, 자원 한도, 승인 범위, A/B/C 양성 대조와 실패 조건, 반출·검역, 독립 GUI 시험을 정의한다. 우선 환경은 전용 x64 테스트 PC의 VirtualBox 7.2 계열과 Windows 11 테스트 게스트다. 이전 Sandbox 우선 검토는 지원 에디션에서의 B 초기 시험 후보로 한정한다.

전역 입장은 한 신뢰 실행기가 관리한다. 검증 모드에서는 모든 게스트 NIC를 제거한다. 미래 Codex 모드에서는 별도 승인·구성과 애플리케이션 브로커의 제한 경계를 요구한다. A/B/C 실측과 버전별 정산 계약 및 송신 승인 전에는 P3를 열지 않는다. P2.5는 설계 변경이며 실행·반출·GUI 기능을 추가하지 않았다.

## P2.4 증분 감사와 복구

- 전역 실행 슬롯 안에서 역할 시작 의도를 먼저 저장한다. `EventJournal`은 바인딩과 순번·이벤트 ID를 검사하고 정규화 영수증을 기존 SQLite 해시 체인에 하나씩 커밋한 뒤에만 확인한다. 이벤트 원문은 저장하지 않는다. 상충 탐지용 지문과 제한된 필드만 남긴다. 정확히 같은 영수증의 재전달은 추가 기록·과금 없이 확인한다.
- 쓰기 실패나 연결 중단 후 새 이벤트는 차단된다. 커밋된 접두사를 재구성해 동일 이벤트 재전달만 확인할 수 있다. 미커밋 이벤트는 증거로 취급하지 않는다. 순번은 로컬 수집기 봉투이므로 제공자에서 수집기에 도달하지 않은 이벤트를 모두 탐지한다는 보장은 없다. 기존 실행 세대·다른 프로젝트 또는 턴에 속한 결과는 거부한다.
- 승인 요청과 `serverRequest/resolved`는 요청 ID로 연결한다. resolved는 처리되거나 정리됐다는 알림이다. 실제 승인 여부는 trusted 권한 기록이 필요하다. 항목 시작·완료는 ID와 유형으로 짝을 맞추며, 미완료 항목이나 승인 요청이 남으면 합성 역할을 정산하지 않는다. 메시지·계획·diff 알림은 요약 영수증만 기록한다. [OpenAI Docs · Codex App Server](https://learn.chatgpt.com/docs/app-server)
- 공개 가이드에서 `thread/tokenUsage/updated`는 확인했다. 이 가이드는 버전별 카운터와 최종 정산 규칙을 고정하지 않으므로 제공자 값은 UNKNOWN으로 유지한다. 합성 전용 누적 스냅샷은 역할별 최고치를 유지해 중복이나 하향 정정으로 예산이 줄지 않는다. 최종 표식과 종료 증거가 필요하며, 중단된 역할의 이미 커밋된 사용량은 합계에 남아도 세션 상태는 UNKNOWN이다. 실제 카운터 매핑은 배포할 Codex 버전의 생성 스키마와 증거 검증이 필요하다.
- 다음 작업은 이전 승인 유효기간, 역할별 영속 정산, 자원 해제, 검토 결과, Verifier 및 동결 목표 증거, 세션 예산과 재시도 상한을 모두 확인한다. 제공자 완료만으로는 다음 역할이나 작업을 열지 않는다.

## P2.3 세션과 이벤트 감사

- 세션 시작·작업·종료는 기존 검증 원장에 함께 저장한다. 전역 한 세션이 작업 입장권을 보유하며, 동일한 전역 뮤텍스 안에서만 전이한다. 설정 revision, 목표·완료 조건, 지침 참조와 정책의 해시를 동결한다. 실제 프로젝트 문서 본문은 읽지 않는다. 재시작 시 완료 기록과 누적 사용량을 다시 계산한다. UNKNOWN이 있으면 다음 주기를 차단한다.
- `session-next`는 소유자가 호출하는 스케줄러 틱이다. 승인된 `run`이 한 작업의 역할을 순서대로 처리한다. 새 작업마다 별도 승인한다. 작업 상한은 전역·프로젝트 기본값, 선택적 개별 작업 한도 및 세션 잔여량의 최솟값이다. 역할에는 역할 상한·잔여량·유효 기한을 적용한다. 세션 벽시계 기한은 대기와 재시작을 포함한다. 합성 토큰·비용과 측정된 로컬 역할 시간은 제공자 사용량과 별개다.
- 최초 Manager는 작업 목표·검증 조건·선정 근거를 감사 기록에 남긴다. 마지막 Manager의 NEXT/REWORK/APPROVAL/BLOCK/GOAL_COMPLETE/NO_PROGRESS 제안을 동결 목표와 Verifier 증거로 대조한다. REWORK는 세션의 별도 수정 주기로 집계한다. 검토 FAIL, 만료, 취소, 증거 불명에는 자동 재시도가 없다. 관리자 APPROVAL 제안은 세션을 승인 대기 결과로 닫는다. 재개 UI·백그라운드 스케줄러는 미구현이다.
- App Server 공개 문서에서 `turn/started`, `turn/completed`의 `turn.id/status`, `item/started`와 `item/completed`의 항목 ID와 유형, 명령의 `exitCode`, 도구 승인 요청, 메시지·계획·diff 알림 및 `thread/tokenUsage/updated`를 확인했다. 지원 subset은 `event_audit.py`에 있다. JSON-RPC 알림에는 공통 ID가 없으므로 프로젝트·세션·실행 세대·역할·문맥 및 로컬 이벤트 ID/순번을 수집기 봉투로 분리한다. 이미 수집된 봉투 재생 시 역전·중복·누락·범위 혼입을 거부한다. 연결 단절 이전에 수집되지 않은 이벤트의 완전성이나 제공자 재전송 중복 제거는 검증하지 못했다. [OpenAI Docs · Codex App Server](https://learn.chatgpt.com/docs/app-server)
- 합성 App Server 모양의 정규화 결과만 감사에 저장한다. 명령 원문과 모델 메시지는 저장하지 않는다. 버전별 생성 스키마로 사용량 필드를 고정하기 전까지 제공자 토큰·비용은 UNKNOWN이며 합산하지 않는다. `turn/completed`는 보고된 상태이며, 인수인계에는 별도의 Job 종료·권한 해제·결정적 검증 근거가 필요하다. 실제 연결, 재접속 프로토콜, 권한 응답 전송은 미연결이다.

## P2.2 등록부와 역할 계약

- 별도 해시 체인 SQLite 등록부에 프로젝트 설정과 전역 예산을 저장한다. 로컬 소유자 Controller가 전역 뮤텍스와 revision 비교로 동시 변경·덮어쓰기를 막는다. GitHub URL·로컬 경로·문서 경로는 메타데이터다. 등록 시 열거나 검증 목적으로 접근하지 않는다. 실제 경로 승인 및 핸들 경계는 P2 OS 시험 이후다.
- 작업이 시작되면 설정 스냅샷과 적용 예산을 동결한다. 전역 예산은 작업별 공용 상한이며 여러 작업의 누적 쿼터는 미구현이다. 프로젝트 상한과 작은 값을 작업 한도로 쓰고 역할 한도 및 잔여 토큰을 추가 적용한다. Mock의 비용은 0이며 실제 비용·추정 토큰은 미계측이다. 단위는 미래 가격 연동을 위한 micro 단위다. 역할 시간은 동기 Mock 반환 뒤 검사한다. 실제 비동기 중단 및 제공자 하드 토큰 상한은 미검증이다.
- Manager → Developer → Reviewer → Verifier → Manager는 기존 영속 전이를 재사용한다. 실행별 독립 문맥 ID, 역할 예산, 승인 ID, 계약·인수인계 해시 및 합성 도구 거절 기록을 남긴다. 등록 모델은 요청 선호값이며 Mock 실제 역할은 `mock-v1`과 결정적 검증기만 사용한다. 완료 수신과 scope 해제·자식 없음 확인 뒤에만 종료 영수증과 다음 상태를 함께 커밋한다. 이전 버전의 Mock 원장도 계속 읽는다. 불완전 이벤트는 열린 실행과 UNKNOWN으로 남고 자동 재시도는 없다.
- 상세 감사는 설정과 별도인 검증된 실행 원장에 저장한다. 합성 테스트·diff·결과·오류·복구를 프로젝트와 역할별로 조회한다. 실제 SDK 원문 프롬프트, 명령 출력, 비밀정보의 수집은 연결하지 않았다. 향후 원문은 별도 로컬 옵트인·최소 접근·삭제 정책으로만 설계한다.
- P3 후보는 Codex App Server의 버전 고정 stdio JSON-RPC다. `model/list`의 페이지와 `supportedReasoningEfforts`로 실제 제공 모델·추론 조합을 확인한다. 등록부 편집기의 검증은 저장 형식에 한정된다. `item/*`는 도구와 결과 증거에 매핑하고 `turn/completed`의 status 및 `thread/tokenUsage/updated`의 상관관계를 검증한다. 누락된 사용량은 UNKNOWN이다. `turn/completed` 이후에도 소유 프로세스·권한 해제 증거가 필요하다. 제공자 가격과 실제 하드 한도는 별도 검증 대상이다. 이번 차수에서는 인증·호출·계정 기능을 연결하지 않았다. [OpenAI Docs · Codex App Server](https://learn.chatgpt.com/docs/app-server)

상태: **P1/P1.5 구현 및 P2 소유 Job 제어 검증**. 실제 프로젝트 실행과 OS 격리 집행은 차단 상태다.

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
| P1.5 | 원장 기반 Windows Command Center | 상태 일치·프로젝트/역할 분리·손상 차단의 합성 검사 PASS, 실제 창 검증 별도 |
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

[GAME-UI-DESIGN](GAME-UI-DESIGN.md)의 P1.5 콘솔은 읽기 전용 SQLite 스냅샷을 검증하고 버전 1 Projection으로 표시한다. UI에는 잠금·실행·승인 권한이 없다. 실제 창 시각·접근성은 후속 격리 검사 항목이다.

## P2 격리 검증 계획

Win32 Job 제어는 고정 합성 시험에서 집행한다. 전역 뮤텍스 안에서 실행 세대와 Job 의도를 원장에 저장한다. `PROC_THREAD_ATTRIBUTE_JOB_LIST`로 자식을 생성과 동시에 Job에 편입하고 중지 상태에서 생성 영수증을 기록한다. 이어서 실행을 재개한다. Job의 동시 활성 상한은 2, 커밋 메모리 상한은 96 MiB이며 커널 API로 설정값을 읽어 확인한다. 사용자 핸들과 환경 변수는 상속하지 않고 Windows 부트스트랩용 `SystemRoot`만 넘긴다. 루트 종료와 활성 0을 확인하고 소유 핸들을 닫은 후에만 종료 영수증을 쓴다. 다음 작업은 이 조건을 통과해야 한다. [프로세스 생성 특성](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute) · [Job 메모리 제한](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)

Job 소유자가 비정상 종료되면 커널의 kill-on-close를 사용하도록 설정되어 있다. 현재 익명 Job의 종료를 재시작 뒤 독립적으로 증명하는 복구 수단은 없다. 원장에 시작 기록만 남으면 복구는 같은 UNKNOWN 영수증을 반복 반환하며 원장 입장 차단을 유지한다. 실제 OS 전원 상실, 소유자 강제 종료 후 kill-on-close, 메모리 한도 초과는 미검증이다. 영속 입장 범위는 기존 단일 프로필 원장 계약을 따른다. P3의 `RoleAdapter`는 계약만 정의하며 등록·실행 경로가 없다.

`sandbox_profile.restricted_profile()`은 네트워크·vGPU·클립보드·오디오/비디오·프린터 리디렉션을 끄고 Protected Client를 켜는 고정 XML을 메모리에서 만든다. 매핑 경로, 자동 로그인 명령, 저장 및 실행 API는 없다. 출력 계약만 합성 검증했다. [Windows Sandbox 설정](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)

| 경계 | 다음 구현·부정 테스트 |
| --- | --- |
| 소유 자식과 단일 슬롯 | 고정 시험에서 정상·비정상 루트 종료, 남은 자식, 시간 한도·취소 시 트리 회수와 재진입 거부를 확인했다. 이후 호스트 소유자 강제 종료의 독립 종료 증거와 게스트/VM 종료 관측을 검증한다. |
| 파일·재분석 지점 | 최초 검증은 호스트 공유 폴더 없이 시작한다. 후속 전달기는 합성 파일의 해시와 상대 경로를 고정하고 허용 루트 및 열린 핸들의 최종 경로/볼륨을 검증한다. junction, 심볼릭 링크, 하드 링크, ADS, 경로 바꿔치기 및 쓰기 제한 우회가 거부되는지 시험한다. |
| 네트워크·자격증명 | 공유 클립보드·계정·환경·홈 디렉터리 없이 시작한다. 외부·호스트·사설 주소와 DNS, 호스트 환경·자격증명 표식 조회를 합성 표식으로 거부 시험한다. P3에서 네트워크가 필요하면 별도 게이트와 전용 제한 자격증명이 필요하다. |
| Sandbox / VM | 파일·네트워크 부정 시험의 첫 후보로 공유 없는 Windows Sandbox를 선택했다. 별도 VM은 자동화된 수명주기와 게스트 출력 회수에 유리하나 이미지·가상 네트워크 관리가 추가된다. AppContainer는 호스트에서 권한을 축소하는 후보로, 게스트 OS 경계와 동일한 보장을 전제하지 않는다. 승인된 환경에서 WSB의 Protected Client 지원과 종료를 검증하고 증거 회수에 한계가 있으면 전용 VM을 평가한다. |
| 복구 | 재시작 후 남은 소유 프로세스 또는 격리 세션을 확인할 수 없으면 BLOCKED를 유지한다. 종료 증거를 받은 뒤에만 명시 복구하고 이전 역할을 자동 재실행하지 않는다. |

Job Object 편입과 종료 집계는 프로세스·자원 제어이며 파일·네트워크에는 독립 OS 경계가 필요하다. VM/Sandbox 실행 및 증거 회수는 BLOCKED다. 회수 경로는 게스트 종료 증거 이후 허용 목록·크기·해시·파일 형식을 검사한 합성 산출물만 격리 검역소에서 읽어야 한다. 게스트가 호스트 감사 원장에 직접 쓸 수 없어야 한다. [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

## P2.1 게스트 시험과 산출물 회수

현재 호스트의 Windows 에디션 값은 `Core`이며 표준 경로에 Sandbox 실행 파일이 없고 `Get-VM`도 확인되지 않았다. Windows Sandbox는 지원 에디션에서 일회용 게스트와 구성 파일을 제공하지만 종료 시 데이터가 폐기되므로 공유를 차단한 상태의 회수에 한계가 있다. 종료 후 검역 회수까지 검증하려면 이미 승인·구성된 전용 VM과 분리된 출력 매체가 적합하다. Windows Sandbox는 지원 호스트에서 B의 초기 부정 시험 후보로 남긴다. [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/) · [Hyper-V](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/overview)

1. 호스트가 승인 목록과 크기·해시가 일치하는 합성 입력만 `package_inputs`로 패키징한다. 테스트용 게스트는 호스트 폴더·클립보드·네트워크를 연결하지 않는다. 입력은 사전에 준비한 읽기 전용 매체를 사용한다.
2. 허용된 게스트 작업 경로에서 합성 파일을 읽고 쓴다. 금지된 호스트 위치와 별도 게스트 보호 경로에는 실제 자료 대신 전용 표식을 배치한다. 직접 열기, 심볼릭 링크·junction, 검사와 열기 사이 경로 교체, 경로 밖 쓰기를 각각 시도한다. 링크 생성 실패만으로 우회 거부를 판정하지 않는다.
3. 통제된 가상망의 합성 DNS 및 사설 주소, 호스트 합성 수신기만 대상으로 차단을 측정한다. 외부 요청은 보내지 않는다. 가짜 환경 변수, 자격증명, 사용자 프로필·클립보드 표식 및 가짜 감사 원장으로 비상속과 접근 거부를 확인한다. 양성 대조로 표식과 시험 수신기가 정상인지 확인한다.
4. 호스트는 같은 실행 세대의 게스트 종료와 소유 프로세스 회수를 독립적으로 확인한다. 결과는 호스트 프로젝트와 분리된 검역으로만 전달한다. 전용 VM에서는 종료 이후 분리한 출력 매체에서 크기가 제한된 번들을 읽기 전용으로 받는 방식을 검토한다. 불명확한 종료나 반출은 차단한다.
5. `inspect_bundle`은 승인된 ASCII 상대 경로와 정확한 파일 목록, 최대 16개·파일당 64 KiB, SHA-256, UTF-8 및 일반 파일 형식을 검사한다. 압축·링크·재분석 속성·경로 이탈은 거부한다. 파일을 추출하지 않고 검토용 바이트만 반환한다. 게스트 전송, 승인 발급, 프로젝트 적용은 미구현이다. 향후 적용은 별도 승인 후 현재 Git 기준과 대상 핸들 경계를 재검증한다.

| 게이트 | 현재 판정 |
| --- | --- |
| A · 소유 자원 종료 | 제한된 실제 Win32 정상·취소·시간 한도는 PASS. 조회 장애·정리 시간 초과·핸들 닫기 실패·종료 영수증 유실은 Mock으로 검사. 소유자 강제 종료, 전원 상실, 메모리 초과는 NOT VERIFIED. |
| B · OS 접근 거부 | BLOCKED. 전용 게스트 실측이 필요하다. |
| C · 호스트 보호 및 회수 | 번들 검역 합성 PASS. 게스트의 호스트 저장소·원장 직접 수정 차단 및 종료 후 반출 실측은 BLOCKED. |

필요한 별도 승인 범위는 기존 테스트 전용 VM 또는 지원 Sandbox 호스트의 시작·종료, 합성 입력 매체 연결, 가상 네트워크 또는 합성 수신기, 별도 검역으로의 읽기 전용 결과 반출이다. 현 PC에서 Windows 에디션 변경, Hyper-V 또는 Sandbox 기능 활성화·설치, 관리자 권한 및 재부팅이 필요하다면 각각 별도 승인을 받는다. P3 진입은 A/B/C 완료 이후다.
