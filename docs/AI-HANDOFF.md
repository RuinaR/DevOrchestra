# 개발 인수인계

P2.94에서 구독 모델 네 턴과 고정 Python Verifier의 실제 순차 사이클을 검증했다. `cycle-role`은 변경 매니페스트가 있는 Developer 결과가 필요하며, 최종 완료에는 동일 작업의 결정적 PASS가 필요하다. 다음 단계는 소유 GUI 영수증을 이 계약에 연결하고 재작업 범위를 별도 검증하는 것이다.

P2.93은 실제 Manager 계획에 바인딩된 구독 Developer의 단일 파일 수정을 실측했다. \`developer-edit\`는 리뷰 해시와 별도 승인으로만 실행한다. \`developer-diff\`는 보호된 검토 자료를 로컬에서 읽는다. 다음 단계는 검토 계약에 diff를 연결하고 SDK 파일·프로세스 집행과 결정적 테스트를 별도 검증하는 것이다.

다음 자동 개발 검증은 [공식 Codex 백엔드 비교](ARCHITECTURE-ROADMAP.md#공식-codex-백엔드)에서 시작한다. Windows SDK의 설치 CLI 시작은 확인했고, 실제 구독 Manager 턴은 여전히 차단 상태다. 기존 App Server 검토 명령과 감독형 경로를 유지한다.

개발 노트북에서는 [공개 지침](../defaults/global/AGENTS.md)과 감독형 요청 바인딩부터 확인한다. 이전 요청의 계약 해시가 다르면 결과를 가져오기 전에 지시를 다시 발행한다. 직접 GUI 검증에는 운영 원장과 분리된 테스트 데이터를 사용한다. 자동 역할 실행의 다음 관문은 [GUI 도구 입장 조건](ARCHITECTURE-ROADMAP.md#개발-노트북-gui-도구-입장-조건)이다.

P2.32 기본 실행 후보는 Windows Native Codex다. VirtualBox 및 명시적 MXC 진단은 선택 후보이며 자동 폴백은 없다. 실운영 연결은 차단된 상태다. [네이티브 계약과 다음 승인 범위](ARCHITECTURE-ROADMAP.md#p232-windows-native-codex)

P2.8 기준 `ac306f4`. 명시적 MXC 시작 진단은 별도 승인 하에 PASS했다. [합성 파일 시험](P2-MXC-FILE-TEST.md)의 호스트 양성 대조 및 MXC 1회는 새 승인이 필요하다. 기존 안전 계약은 유지하며 실제 Codex 및 P3는 BLOCKED다.

P2.7 기준 `71abfd9`. [MXC 시험 계약](P2-MXC-VALIDATION.md)을 우선 적용한다. VirtualBox 다운로드·설치는 보류다. 명시적 MXC 시작 진단은 소유자 별도 승인 후 합성 작업 공간에서만 수행한다. 진단 PASS는 파일·네트워크·데스크톱·Job 및 원장 보호를 증명하지 않는다. 레거시 폴백은 입장 금지다.

P2.6 기준 `9c748eb`. 소유자가 현재 PC 도입을 선택했다. AMD-V와 Microsoft 하이퍼바이저는 관측했으며 WHP 및 실제 부팅은 미검증이다. [최소 설치와 승인 순서](P2-VM-VALIDATION.md#p26-현재-pc-도입-사전-점검)를 우선 적용한다. 다음 동작은 패키지 다운로드 승인 이후이며, 설치·드라이버·WHP/보안 설정·재부팅·게스트 준비·VM 실행은 별도다.

P2.5 기준 `1519b78`. [인수 계획](P2-VM-VALIDATION.md)에 따라 승인된 전용 VirtualBox 환경의 UUID·이미지·자원·합성 매체를 먼저 지정한다. VM 제어 및 반출기는 미구현이다. 기존 Job 증거는 OS 접근 거부나 게스트 반출에 사용하지 않는다. A/B/C와 별도 송신 승인 및 App Server 버전 정산 확인 전 P3는 BLOCKED다.

P2.4 기준 `07f6ff7`. `EventJournal`은 trusted 실행 슬롯 아래 `stream_event`를 개별 커밋한다. `restore`는 검증된 접두사 조회와 중복 확인 전용이다. `event_audit`는 제한된 공식 이벤트 모양과 별도로 표시한 합성 사용량을 처리한다. 다음은 P2 A/B/C 실측 및 고정 App Server 버전의 스키마·정산 계약 검증이다.

P2.3 기준 `9228088`. `Runner.development`가 기존 원장의 `session_*` 이벤트와 `development_id`로 작업을 연결한다. `session-next` 뒤에는 기존 작업별 승인 흐름을 사용한다. `event_audit.py`는 로컬 봉투 기반 오프라인 subset이며 제공자 완료를 인수인계 근거로 쓰지 않는다. 다음 단계는 P2 A/B/C 실측과 버전 고정 App Server 스키마 계약 검증이다.

P2.2 기준 `9551db4`. `Catalog`는 런타임 옆의 별도 SQLite 원장이다. GUI/CLI는 revision 검증을 거쳐 설정만 저장한다. 등록된 프로젝트는 경로·권한을 어댑터에 전달하지 않고 기존 Mock 승인·실행을 사용한다. 실행 중 설정 갱신은 이미 동결된 계약에 영향을 주지 않는다. 전역 예산은 작업별 상한이며 실제 비용·토큰 계측과 OS 격리는 후속 단계다.

P2.1 기준 `24fc3e7`. `job_probe._cleanup`은 종료 확인 실패 시 소유 Job을 직접 종료 시도하고 각 핸들을 정리한다. `quarantine`은 합성 승인 목록과 바이트 검증만 제공한다. 게스트 종료 확인과 전송 계층에는 연결되어 있지 않다. A의 소유자 장애 실측과 B/C 게스트 부정 시험은 승인된 전용 VM에서 이어간다. [A/B/C와 승인 범위](ARCHITECTURE-ROADMAP.md#p21-게스트-시험과-산출물-회수)

P2 기준은 `a2f650e56251c6145091d1e4686679af587b63eb`다. `job_probe.py`는 고정 합성 Win32 Job 시험만 수행한다. `_probe_job`은 CLI에 연결되지 않으며 Job 종료 기록이 없으면 `recover`도 UNKNOWN을 보존한다. `RoleAdapter`는 미연결 타입 계약이다. 다음 단계는 승인된 WSB/VM 환경에서 실제 OS 접근 거부, 소유자 장애의 종료 증거 및 안전한 산출물 회수를 검증하는 것이다.

P1.5 기준은 `d256f791f4b1c088a105a7297c5ef960ca64a2f0`이다. `python -B -m devorchestra.gui`가 Tk 콘솔을 연다. `projection.py`는 `Ledger.snapshot()` 기본 읽기 모드에서만 데이터를 받고, 역행이나 계보 변경 시 현재 화면을 지운다. `tests/test_projection.py`는 창을 만들지 않는 합성 검증이다. `sandbox_profile.py`는 Windows Sandbox 제한 XML만 만들며 실행 코드가 없다. 실제 창과 P2 집행 검증은 별도다.

- [현재 상태](CURRENT-STATUS.md)에서 판정과 차단을 확인한다. 기준 SHA는 `becd2f7abaca1b8af521d6536259a6faaadbeddc`다.
- `Runner.command`가 Windows 전역 뮤텍스를 소유하고 이벤트 상태 전이를 수행한다. 원장의 이벤트 순번과 끝 해시가 쓰기 펜스이며 실행 세대와 소유자는 각 이벤트에 남는다.
- 역할 시작과 종료 사이에 중단되면 결과는 UNKNOWN이다. `recover RUN_ID`로 복구 이벤트를 남기고 `cancel RUN_ID`로 종결한다. PAUSED에서만 명시적 재개가 가능하다.
- 기본 원장은 사용자 프로필 아래의 SQLite 파일이다. 실제 프로젝트 파일·명령·외부 서비스에는 접근 경로가 없다. 테스트는 저장소 안 임시 디렉터리를 사용한다.
- 다음 단계는 승인된 격리 환경의 파일·네트워크·자식 프로세스 차단 증거를 확보하는 P2다. [아키텍처](ARCHITECTURE-ROADMAP.md)
# 감독형 개발 운영실과 동시 실행 경계

다음 작업은 목표·완료 기준과 차수 요구사항을 구분하고, 감독형 개발의 지시 생성 → 사용자 직접 전달 → 결과 가져오기를 유지한다. 일반 개발 대상은 프로젝트와 승인된 작업 폴더다. 메이플스토리 등 다른 프로그램의 창·입력·프로세스·메모리·화면은 조사하거나 조작하지 않는다. 컴퓨터 사용, 전역 입력, 화면 캡처, 후킹·주입은 금지한다. OS 비간섭은 `NOT VERIFIED`이고 자동 모델 실행은 `BLOCKED`다.
