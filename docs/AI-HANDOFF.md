# 개발 인수인계

P2 기준은 `a2f650e56251c6145091d1e4686679af587b63eb`다. `job_probe.py`는 고정 합성 Win32 Job 시험만 수행한다. `_probe_job`은 CLI에 연결되지 않으며 Job 종료 기록이 없으면 `recover`도 UNKNOWN을 보존한다. `RoleAdapter`는 미연결 타입 계약이다. 다음 단계는 승인된 WSB/VM 환경에서 실제 OS 접근 거부, 소유자 장애의 종료 증거 및 안전한 산출물 회수를 검증하는 것이다.

P1.5 기준은 `d256f791f4b1c088a105a7297c5ef960ca64a2f0`이다. `python -B -m devorchestra.gui`가 Tk 콘솔을 연다. `projection.py`는 `Ledger.snapshot()` 기본 읽기 모드에서만 데이터를 받고, 역행이나 계보 변경 시 현재 화면을 지운다. `tests/test_projection.py`는 창을 만들지 않는 합성 검증이다. `sandbox_profile.py`는 Windows Sandbox 제한 XML만 만들며 실행 코드가 없다. 실제 창과 P2 집행 검증은 별도다.

- [현재 상태](CURRENT-STATUS.md)에서 판정과 차단을 확인한다. 기준 SHA는 `becd2f7abaca1b8af521d6536259a6faaadbeddc`다.
- `Runner.command`가 Windows 전역 뮤텍스를 소유하고 이벤트 상태 전이를 수행한다. 원장의 이벤트 순번과 끝 해시가 쓰기 펜스이며 실행 세대와 소유자는 각 이벤트에 남는다.
- 역할 시작과 종료 사이에 중단되면 결과는 UNKNOWN이다. `recover RUN_ID`로 복구 이벤트를 남기고 `cancel RUN_ID`로 종결한다. PAUSED에서만 명시적 재개가 가능하다.
- 기본 원장은 사용자 프로필 아래의 SQLite 파일이다. 실제 프로젝트 파일·명령·외부 서비스에는 접근 경로가 없다. 테스트는 저장소 안 임시 디렉터리를 사용한다.
- 다음 단계는 승인된 격리 환경의 파일·네트워크·자식 프로세스 차단 증거를 확보하는 P2다. [아키텍처](ARCHITECTURE-ROADMAP.md)
