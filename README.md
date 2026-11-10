# DevOrchestra

DevOrchestra는 Windows에서 공식 Codex 코딩 에이전트의 순차 개발을 관리하는 Controller다. 역할·승인·예산·원장·복구는 Controller가 맡고, 결정적 GUI 검증은 작업 소유 창 브로커가 맡는다. 자동 실행 후보는 [공식 Codex 백엔드 비교](docs/ARCHITECTURE-ROADMAP.md#공식-codex-백엔드)에서 확인한다.

**개발 운영실**에서 목표·완료 기준, 최근 요구사항과 다음 행동을 확인합니다. 목표와 완료 기준은 프로젝트 전체의 방향과 판정 기준입니다. 요구사항 문서는 이번 차수에 수행할 기능·버그·설계·개선 작업을 설명합니다. **감독형 개발 시작**에서 지시를 만든 뒤 기존 Codex에 직접 전달하고 결과를 가져옵니다. 자동 모델 실행은 실행 전 권한 경계가 확인될 때까지 차단됩니다.

Controller는 고정 테스트 앱과 등록 프로젝트의 선언형 WinForms 시나리오를 실행할 수 있습니다. 조작과 캡처는 작업 소유 창에 한정하며 실행 후 소유 프로세스를 회수합니다.

프로젝트와 요구사항을 등록하면 Controller가 계획 → 구현 → 검토 → 검증 → 최종 판정을 한 작업씩 조율하는 Windows 프로그램입니다. 검증된 결과를 사용자가 직접 테스트하고 개선 문서를 등록하면 다음 개발 주기로 이어집니다. Command Center에서 다음 단계와 합성 결과, 실제 실행 기록을 구분해 확인할 수 있습니다. 현재 실제 자동 개발과 사용자 테스트 인계는 차단되어 있습니다.

기존 ChatGPT/Codex 구독 인증만 사용합니다. DevOrchestra 전용 Sign in with ChatGPT 연결은 공식 구독 텍스트 엔드포인트를 사용합니다. API 키, 별도 과금 계정 및 자동 폴백은 비활성입니다. [구독 Manager 실행](docs/ARCHITECTURE-ROADMAP.md#구독-manager-실행)

### 감독형 Codex로 시작하기

등록 프로젝트의 감독형 지시에는 [공개 전역 지침](defaults/global/AGENTS.md), [프로젝트 공통 지침](defaults/project/AGENTS.template.md)과 해당 역할 기본값이 자동으로 포함됩니다. 승인한 프로젝트 재정의와 개인 기본값의 우선순위를 먼저 적용합니다. **작업 지침**에서 출처·버전·해시를 확인할 수 있습니다. 프로젝트 파일에 지침을 자동 복사하지 않습니다.

### GUI 검증과 이미지 보고서

**GUI 검증 보고서**에서 고정 Windows 테스트 앱의 Verifier 실행 범위를 검토하고 승인합니다. Controller가 단일 슬롯에서 소유 창을 실행·조작하고 종료와 Job 회수를 확인한 뒤 영수증을 기록합니다. 썸네일에서 가공 이미지, 원본, 전후 비교를 열 수 있습니다. 검증 파일은 로컬 전용 `.ai-local/logs/`에 저장합니다.

CLI에서는 `python -B -m devorchestra gui-verify --project fixture-a`로 범위와 해시를 확인합니다. 동일한 명령에 `--approve <SCOPE_SHA256>`를 추가하면 고정 테스트를 실행합니다. Windows 대화형 세션에서 `DEVORCHESTRA_GUI_E2E=1`을 설정하면 테스트 스위트가 실제 GUI 경로를 검증합니다.

등록 프로젝트는 별도 작업 복제본의 루트에 [선언형 예제](examples/native-form/gui-test.json)를 참고해 `gui-test.json`을 둡니다. 프로젝트가 고른 컨트롤 이름, 클릭·텍스트·스크롤·리사이즈 단계와 세 장면의 기대 상태를 지정할 수 있습니다. Controller가 서명·해시를 검증한 시스템 PowerShell에서 신뢰된 WinForms 호스트를 띄우고, 승인한 합성 데이터 사본으로 검증합니다. CLI에서는 `--project <ID> --registered --role Verifier`로 범위를 확인합니다. Developer와 Reviewer도 소유자 승인으로 독립 검증을 실행할 수 있으며 Manager는 `--receipt`로 영수증을 읽습니다. 모델 호출, 임의 EXE, 빌드 명령과 다른 UI 프레임워크의 실행은 현재 지원 범위 밖입니다.

독립 x64 테스트 EXE를 검증하려면 `python -B -m devorchestra gui-build-exe --project <ID> --exe InventoryQa.exe`로 빌드 범위를 확인하고, 동일 명령에 `--approve <SCOPE_SHA256>`를 추가합니다. Controller가 검토된 C# 소스를 고정 빌드하고 복제본에 새 EXE와 원장 출처를 기록합니다. **GUI 검증 보고서**의 EXE 입력란에 파일명을 지정하거나 `gui-verify --project <ID> --exe InventoryQa.exe`로 새 실행 승인을 확인합니다. EXE 시나리오는 `{"op":"menu","path":["Actions","Browse stock"]}`처럼 소유 창의 네이티브 메뉴를 이름으로 지정할 수 있습니다. 출처를 확인할 수 없는 EXE는 별도의 파일·네트워크 격리 검증이 필요합니다.

1. Command Center에서 프로젝트와 요구사항 문서를 등록합니다.
2. **감독형 Codex 작업**에서 Manager가 제안할 수 있는 프로젝트 상대 파일을 적고 **Manager 시작**을 누릅니다.
3. 지시를 검토한 뒤 **지시 복사**로 기존 Codex에 직접 전달합니다. JSON 봉투를 붙여넣거나 로컬 JSON 파일로 가져옵니다.
4. 질문은 Command Center에서 답한 뒤 새 Manager 작업을 시작합니다. 유효한 계획은 해당 JSON을 유지한 상태에서 **Developer 지시 준비**로 인계합니다. 승인할 별도 복제본을 선택하면 DevOrchestra가 변경 전 기준을 기록합니다. 작업은 사용자가 별도로 승인하고 기존 Codex에 전달합니다.
5. Developer JSON을 가져오면 복제본의 파일 해시를 대조합니다. 변경이 일치하면 표시된 Reviewer 지시를 복사하고, 검토 JSON을 같은 창에 가져옵니다. Reviewer가 다음 단계에 동의하면 **Verifier 빌드 계획**에서 명령·환경·출력 폴더·산출물을 지정합니다. 승인한 외부 실행 결과와 산출물 해시를 가져올 수 있습니다. DevOrchestra는 제출된 종료 코드와 자체 확인한 산출물을 구분합니다.

감독형 모드는 사용자가 역할 지시와 결과를 직접 전달하는 실사용 경로입니다. 가져온 계획은 외부 실행 증거와 별도로 표시됩니다. 최종 목표인 자동 Codex 실행은 기존 입장 조건을 유지합니다.

### 포터블 Windows x64 빌드

Windows x64, x64 Python, Tk와 이미 설치된 PyInstaller를 준비한 깨끗한 소스 체크아웃에서 `python -B tools/build_windows_release.py 0.2.66`을 실행합니다. 스크립트는 로컬 전용 `.ai-local/release/` 아래 새 폴더에 GUI EXE를 빌드하고 버전·Git SHA·PE 형식·내장 Tk 리소스·해시를 검사합니다. EXE는 배치할 폴더의 `.ai-local/`을 사용합니다. 업데이트할 때 같은 위치의 EXE만 교체하고 데이터 폴더를 보존합니다. 빌드 도구가 없으면 사전검사에서 중단됩니다. 패키지 GUI 실행은 별도 격리 검증 대상입니다.

Command Center는 구현과 검증의 진척을 따로 표시합니다. 동일 기준에서 구현 진척이 두 주기 연속 없으면 재계획하고, 세 주기면 `NO_PROGRESS`로 중단합니다. 차단과 재작업 한도는 먼저 적용될 수 있습니다.

## 다른 Windows PC에서 시작하기

포터블 EXE는 첫 실행에서 **초기 설정 → 저장소 준비**를 표시합니다. EXE 옆 `.ai-local/`을 준비한 뒤 이름·원본 폴더·목표·완료 기준으로 프로젝트를 등록하고 요구사항을 추가하세요. 기존 저장소가 있으면 계속 사용합니다. 저장소 오류가 표시되면 기존 폴더를 보존한 채 권한이나 백업을 확인하세요.

**개발용 작업 폴더**는 원본과 분리된 작업 사본입니다. 이미 준비한 폴더를 선택하거나 등록 화면에서 별도로 승인해 새 폴더로 복사할 수 있습니다. 자동 복사는 Git 메타데이터, 로컬 앱 데이터와 알려진 자격증명 파일을 제외합니다. **작업 지침**에는 기본 지침이 있으며, 개인 AGENTS.md는 선택·백업 후 승인하고 기존 로컬 역할 초안은 미리본 뒤 감독형 작업에 연결할 수 있습니다. 감사·예산 상세는 **고급 정보**에서 확인합니다.

0.2.69에서는 **목표와 완료 기준 편집**에서 여러 줄 목표와 완료 항목을 저장하고, **요구사항 직접 작성**으로 새 Markdown을 등록할 수 있습니다. **개발 시작**은 감독형 Codex 작업을 엽니다. **작업 지침 → 개인 기본 지침 연결**에서 기존 로컬 매니페스트를 검토·연결하면 이후 프로젝트에도 적용됩니다. 같은 폴더의 버전별 EXE는 EXE 옆 저장소를 공유합니다.

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
