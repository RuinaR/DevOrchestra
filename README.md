# DevOrchestra

## 다른 Windows PC에서 시작하기

GitHub에서 소스를 내려받고 Windows x64에 Python 3.10 이상과 Tk를 준비한다. 저장소에서 `python -m devorchestra.gui`를 실행하면 새 로컬 원장에서는 초기 설정 마법사가 열린다. 기존 로컬 설정과 원장은 저장소와 별도로 유지한다. 마법사는 경로와 프로젝트 정책을 등록하며 인증정보를 받지 않는다. 설정 완료 후에도 실제 실행은 BLOCKED다. Python이 포함된 포터블 배포본은 아직 제공하지 않는다.

Command Center의 종료 예약은 합성 작업의 다음 역할 입장을 막고, 안전한 체크포인트를 기록하면 Command Center를 닫는다. 재시작한 뒤 이어서 개발을 확인하면 같은 합성 세대의 입장이 다시 열리며, 역할 진행에는 기존 CLI의 명시적 명령이 필요하다. [안전 계약](docs/ARCHITECTURE-ROADMAP.md#p219-종료-예약과-배포-경계)

**한 번에 한 프로젝트 · 한 작업 · 한 에이전트만 실행하는 로컬 AI 개발 오케스트레이터.**

> Windows Native Codex가 기본입니다. 일반 개발은 공식 샌드박스·승인 정책을 사용하며, 강화 격리는 별도 A/B/C 실측을 요구합니다. 현재 실제 모델·프로젝트 실행은 일반 모드의 입장 증거가 확보될 때까지 BLOCKED입니다. [실행 모드와 전송기](docs/ARCHITECTURE-ROADMAP.md#p234-일반-개발과-강화-격리)

Command Center의 역할 감사에서 커밋된 합성 스트림의 이벤트 ID와 원장 순번을 조회합니다. 중단된 스트림은 확인된 기록만 보존하며, 새 이벤트로 이어받거나 자동 재실행하지 않습니다.

```powershell
$session = (python -B -m devorchestra session-start --project fixture-a --cycles 2 --tokens 10 | ConvertFrom-Json).development_id
$run = (python -B -m devorchestra session-next $session | ConvertFrom-Json).run_id
python -B -m devorchestra step $run
python -B -m devorchestra approve $run
python -B -m devorchestra run $run
python -B -m devorchestra session-status --project fixture-a
```

두 번째 작업은 같은 `session-next`와 작업별 승인 흐름을 사용합니다. 종료 전 `session-next`로 마지막 판정과 제한을 반영하거나, 작업을 먼저 종료한 뒤 `session-stop`으로 세션을 닫습니다. 재시작은 누적 기록을 보존하고 실행을 자동 재개하지 않습니다. [세션 및 감사 계약](docs/ARCHITECTURE-ROADMAP.md#p23-세션과-이벤트-감사)

Command Center의 `Register`, `Project settings`, `Global limits`에서 JSON 설정을 편집할 수 있습니다. Trusted Controller가 형식과 수정 버전을 검증한 뒤 로컬 등록부에 저장합니다. 등록한 프로젝트에서도 실행은 합성 워크플로와 명시적 승인으로 제한됩니다. 입력 설정에는 비밀이나 원문 로그를 붙여넣지 마세요.

```powershell
python -B -m devorchestra project-template demo 'Demo' 'Synthetic workflow' | python -B -m devorchestra project-save
python -B -m devorchestra project-list
python -B -m devorchestra start --project demo
```

기존 설정을 갱신할 때는 `project-save --revision N`, 공용 예산은 `limits-save --revision N`에 JSON을 표준 입력으로 전달합니다. 프로젝트 수정에는 해당 프로젝트 revision, 공용 예산에는 등록부 version을 사용합니다. [실행·감사 계약](docs/ARCHITECTURE-ROADMAP.md#p22-등록부와-역할-계약)

P2 시험은 Windows Job에서 고정 합성 프로세스만 실행합니다. 루트와 자식 트리 종료를 검증하며 CLI에는 실제 작업 실행 경로를 추가하지 않았습니다. Command Center의 Job 기록은 관측 당시의 합성 증거입니다. 슬롯의 실시간 점유와 파일·네트워크·자격증명 격리는 별도입니다. [P2 검증 범위](docs/ARCHITECTURE-ROADMAP.md#p2-격리-검증-계획)

P2.1 검역은 호스트 승인 목록에 있는 합성 UTF-8 파일만 메모리에서 검사합니다. 게스트 전송·아카이브 추출·Git 반영 경로는 없습니다. [A/B/C 게이트와 필요한 승인](docs/ARCHITECTURE-ROADMAP.md#p21-게스트-시험과-산출물-회수)

## Windows Command Center

Windows의 Python 3.10 이상과 Tk가 필요합니다. P1 원장을 초기화한 뒤 다음 명령으로 읽기 전용 콘솔을 엽니다.

```powershell
python -B -m devorchestra.gui
```

프로젝트를 선택하면 미션·마일스톤·퀘스트 단계·역할 카드·예산·승인/차단 근거를 볼 수 있습니다. 역할 카드를 선택하거나 실행·역할 필터를 바꾸면 해당 감사 이벤트가 표시됩니다. 이벤트를 선택하면 순번, ID, 결정 및 검증 영수증이 열립니다. `Tab`과 방향키로 탐색하고 `F5`로 갱신할 수 있습니다. 콘솔은 원장 상태를 1.5초마다 확인합니다.

프로젝트·역할 실행 제어와 승인은 기존 CLI에서 수행합니다. 시작 이벤트만 있고 실시간 생존 증거가 없는 역할은 `UNKNOWN`입니다. 화면의 Mock 지표는 실제 모델 사용량과 별개입니다. GUI 시각·DPI 검증은 NOT VERIFIED입니다.

## P1 실행 방법

Windows와 Python 3.10 이상이 필요합니다. 추가 패키지는 필요 없습니다. `init`은 DevOrchestra 소스 체크아웃의 `.ai-local/state/p1.sqlite3`를 최초 한 번 생성합니다. 기존 원장은 초기화하지 않습니다. 지침 백업은 `.ai-local/instructions`, 임시 실행은 `.ai-local/temp`에 둡니다. `.ai-local/logs`와 `.ai-local/recovery`는 각각 비공개 로그와 별도 복구 자료용입니다. 현재 체크포인트와 복구 이벤트는 실행 SQLite에 보존합니다.

`python -B -m devorchestra local-status`는 파일을 만들지 않고 `NOT_INITIALIZED`, `READY`, `BLOCKED`를 표시합니다. 명시적 `init` 또는 Command Center의 최초 설정 저장으로 초기화합니다. 부분 생성이나 손상된 저장소는 보존하고 초기화를 차단합니다. 이전 사용자별 저장소는 자동 검색·복사하지 않습니다. 공개 커밋 전 `python -B tools/check_public_index.py`를 실행하고 스테이징된 내용도 검토하세요.

Codex 셸에서 합성 확인 마커를 생성했다면, 사용자 PowerShell에서 프로젝트 루트를 연 뒤 `Test-Path -LiteralPath '.\.ai-local' -PathType Container`와 `Get-FileHash -LiteralPath '.\.ai-local\host-check.txt' -Algorithm SHA256`으로 직접 대조할 수 있습니다. 마커가 없거나 해시가 다르면 사용자 세션에서 동일 저장소가 확인되지 않은 상태입니다.

```powershell
python -B -m devorchestra init
$run = (python -B -m devorchestra start | ConvertFrom-Json).run_id
python -B -m devorchestra step $run     # NEED_APPROVAL 및 합성 읽기 요청 출력
python -B -m devorchestra approve $run  # 위 요청을 확인한 소유자가 실행
python -B -m devorchestra run $run      # 승인된 합성 파이프라인 실행
python -B -m devorchestra dashboard
python -B -m devorchestra audit --run $run --role Verifier
```

`run` 대신 `step`을 쓰면 단계 사이에 `pause`, `resume`, `cancel`을 같은 실행 ID로 요청할 수 있습니다. 강제 종료로 시작만 기록된 역할은 `recover`로 UNKNOWN 상태를 기록한 뒤 `cancel`로 닫습니다. 재시작은 작업을 자동 실행하지 않습니다.

`dashboard --watch`는 변경된 원장 스냅샷을 출력합니다. 단계 배지와 역할 패널은 기록된 전이에서 읽고, 합성 역할의 설명과 사용량은 Mock으로 구분합니다.

```powershell
python -B -m unittest discover -s tests -v
```

## 목표

사용자가 프로젝트별 목표·지침 문서와 모델, 작업 시간, 토큰/비용 예산, 허용 경로·도구·네트워크 범위 및 위험 작업 승인 정책을 설정하면, **관리자 → 개발자 → 검토자 → 결정적 검증기**를 **완전히 순차적으로** 실행하는 Windows 우선 로컬 개발 관리 앱을 만듭니다.

각 프로젝트의 목적과 원본 Git 작업 이력을 존중하며, 사용자는 중요한 승인·중단·복구를 제어합니다. Codex는 적합한 실행 후보지만 특정 클라이언트/SDK/인증 경로가 실제 지원되는지 구현 전에 공식 문서와 실측으로 확인합니다.

## 변경 불가 원칙

- **GLOBAL=1, PROJECT=1, TASK=1, AGENT=1**: 전역에서 동시에 실행되는 개발 작업과 에이전트는 각각 **최대 하나**. 백그라운드에 남은 자식 작업·GPU 추론·검증 실행까지 포함. 병렬 실행 기능 자체를 설계하지 않습니다.
- **기본 차단**: 첫 실행은 read-only/dry-run이며, 개발·테스트·네트워크·Git 쓰기 권한은 별개로 명시적으로 허용해야 합니다.
- **프로젝트 경계**: 허용한 작업 복제본만 접근; 다른 프로젝트, 사용자 데스크톱, 게임·타 프로세스, 인증 정보에 접근하지 않습니다.
- **프롬프트는 권한이 아닙니다**: AI의 지시가 실제 파일시스템/프로세스/네트워크 제한을 바꾸지 못합니다. 시스템 정책과 사용자 승인 검사는 모델 바깥에서 수행합니다.
- **실사용·실금전 차단**: AITraderGuard 등 계좌/증권/장터, Discord 실서비스, 메일 발송/게시·배포는 프로젝트별 별도 실행 승인이 없으면 합성/오프라인 테스트까지만 진행합니다.
- **감사 가능성**: 역할별 개별 실행·작업·모델·권한·도구 호출·수정·테스트·비용·시간·승인·중단 결과를 영속적으로 기록합니다(비밀·개인정보 마스킹).
- **정확한 상태**: PASS / FAIL / BLOCKED / NOT VERIFIED / NEED_APPROVAL / NO_PROGRESS를 구분하고 실측되지 않은 작업을 성공으로 말하지 않습니다.

전체 상위 계약은 [전역 안전 정책](docs/GLOBAL-SAFETY-POLICY.md), Codex 작업 규칙은 [AGENTS.md](AGENTS.md)를 참고하세요.

## 계획된 순차 파이프라인

```text
프로젝트 등록/목표·권한 설정
  -> 관리자 Codex: 최신 Git·문서 확인, 단일 작업 선정/완료 기준 정의
  -> 개발자 Codex: 격리 작업 공간에서 구현·자체 시험
  -> 검토자 Codex: 별도 문맥의 읽기 전용 변경 감사
  -> 결정적 검증기: 허용된 테스트·해시·권한·상태 확인
  -> 관리자 판정: PASS / FIX / BLOCK / APPROVAL / STOP
  -> 승인된 커밋/푸시 또는 안전 중단·체크포인트
  -> 다음 작업 (전 단계와 프로세스 완전 종료 후만)
```

이 흐름의 각 단계는 독립된 역할 기록을 만들며, 검토자에게 개발자의 자기평가를 사실로 주입하지 않습니다. 테스트 실패, 미검증, 승인 대기 또는 한도 초과 시 안전하게 멈춥니다.

## 계획된 관리 기능

- **프로젝트 카탈로그**: 등록 저장소/로컬 경로, 목표·지침·인수인계, 구현 상태, 승인 범위, 테스트 계획
- **에이전트 설정**: 역할별 모델·추론 수준·허용 도구·시간·토큰 사용량/비용·재시도 한도
- **전역 단일 실행 잠금**: 작업과 자식 프로세스까지 세대 ID·소유권·복구 검증, 중복 실행 차단
- **작업 콘솔**: 현재 단계·작업 근거·Git 변경·테스트·성능·자원·승인 요청을 실측값으로만 표시
- **에이전트별 상세 기록**: 관리자/개발자/검토자/검증기의 개별 타임라인·출력·실패·재시작·버전·비용
- **권한 관리**: 디렉터리/네트워크 allowlist, 별도 Git 쓰기/푸시 승격, 실서비스/금전 실행 차단
- **중단/복구**: 중단·재시작 이후 잠금·고아 자식·미커밋 변경·중복 도구 실행 확인, 무단 자동 재실행 없음

실제 작업 폴더·실행·샌드박스 지원은 OS/도구의 검증된 기능에 따라 결정합니다. Windows Job Object는 종료 및 자원 제어 수단이지 **파일/네트워크 보안 샌드박스가 아닙니다**.

## 참고 프로젝트

- [EchoMate](https://github.com/RuinaR/EchoMate): 동의 기반 기억·출처·운영 Discord 분리·인수인계, 실제 LIVE 미승인
- [AITrader](https://github.com/RuinaR/AITrader): 합성 회계·WAL·실시장/실계좌 분리, **실금전 준비 BLOCKED**
- [MarketCrate](https://github.com/RuinaR/MarketCrate): 계정·거래 승인/미확정 결과 보존, 게임 등 타 프로세스 비간섭

위 프로젝트는 **안전 설계의 참고 대상**일 뿐 자동 등록/읽기/쓰기/실행 허가 대상이 아닙니다.

## 구현 시작 순서

1. 위험 모델과 프로젝트 경계·권한·승인 상태 머신·위협 모델 및 테스트를 먼저 정의
2. read-only 프로젝트 검사와 전역 단일 실행 락, 취소·재시작 복구 구현
3. 에이전트 1개를 mock으로 실행하고 기록의 무결성·비밀 마스킹 시험
4. 역할별 순차 Codex 연결 및 비용·시간/토큰 사용량 계측
5. 격리된 개발 작업 → 독립 리뷰/검증 → 명시 승인에 따른 Git 반영
6. Windows GUI 및 다중 프로젝트 등록(실행은 언제나 전역 직렬)

## 문서

- [전역 안전 정책](docs/GLOBAL-SAFETY-POLICY.md) — 모든 연결 프로젝트에 적용할 최소 허용 경계
- [아키텍처·개발 로드맵](docs/ARCHITECTURE-ROADMAP.md) — 실행 구조와 검증 게이트
- [근거 및 출처](docs/REFERENCES.md) — 참조한 공식 보안 문서와 프로젝트 사례
- [제품 정의](docs/PROJECT-DEFINITION.md) · [현재 상태](docs/CURRENT-STATUS.md) · [작업 기록](docs/WORKLOG.md) · [개발 인수인계](docs/AI-HANDOFF.md) · [ChatGPT 전달용](docs/CHATGPT-HANDOFF.md)
- [AGENTS.md](AGENTS.md) — 본 저장소에서 작업하는 Codex 지침

**주의:** 저장소의 `AGENTS.md`는 DevOrchestra 작업에 적용되는 저장소 지침입니다. 다른 저장소의 Codex에 전역 지침을 자동 설치하지 않습니다. 실제 DevOrchestra가 다른 프로젝트를 실행할 때는 등록된 프로젝트 지침과 이 정책을 실행기에서 함께 강제해야 합니다.

## 라이선스·보안

라이선스와 배포 정책은 아직 확정하지 않았습니다. 토큰/운영 DB/프로젝트 원문/개인 대화/실행 로그는 이 공개 저장소에 올리지 않습니다. `.gitignore`는 보조 방어선이며 실제 푸시 전 내용 검사와 GitHub push protection을 별도로 적용해야 합니다.

### 게임형 Windows 운영 콘솔

[GAME-UI-DESIGN](docs/GAME-UI-DESIGN.md)에 원장 기반 프로젝트 진행도, 에이전트 역할 카드, 퀘스트형 파이프라인, 상세 감사와 남은 시각 검증 범위를 기록했습니다.
