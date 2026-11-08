# 개발 인수인계

- [현재 상태](CURRENT-STATUS.md)에서 판정과 차단을 확인한다. 기준 SHA는 `becd2f7abaca1b8af521d6536259a6faaadbeddc`다.
- `Runner.command`가 Windows 전역 뮤텍스를 소유하고 이벤트 상태 전이를 수행한다. 원장의 이벤트 순번과 끝 해시가 쓰기 펜스이며 실행 세대와 소유자는 각 이벤트에 남는다.
- 역할 시작과 종료 사이에 중단되면 결과는 UNKNOWN이다. `recover RUN_ID`로 복구 이벤트를 남기고 `cancel RUN_ID`로 종결한다. PAUSED에서만 명시적 재개가 가능하다.
- 기본 원장은 사용자 프로필 아래의 SQLite 파일이다. 실제 프로젝트 파일·명령·외부 서비스에는 접근 경로가 없다. 테스트는 저장소 안 임시 디렉터리를 사용한다.
- 다음 단계는 승인된 격리 환경의 파일·네트워크·자식 프로세스 차단 증거를 확보하는 P2다. [아키텍처](ARCHITECTURE-ROADMAP.md)
