# 작업 기록

## P2 · Windows 소유 Job 시험

- 기준 `a2f650e56251c6145091d1e4686679af587b63eb`. 시작 시 로컬·원격 일치 및 깨끗한 작업 트리를 확인했다. 변경 SHA는 이 기록을 포함하는 커밋에서 조회한다.
- `job_probe.py`의 고정 시험, `runtime.py`의 세대·종료 영수증 펜스, `lease.py`의 재진입 차단을 추가했다. Projection/GUI에 증거 범위와 과거 검증 SHA를 연결하고 `adapter.py`에 미연결 P3 타입 계약을 정의했다.
- 최초 Win32 시험에서 빈 환경으로 Python 부트스트랩이 실패해 `SystemRoot`만 전달하도록 수정했다. 종료 집계와 루트 핸들의 종료 신호가 모두 확인돼야 영수증을 쓰도록 했다.
- 최종 Windows 단위·합성·Job 시험 18건 및 CLI 흐름 PASS. 실제 Sandbox·VM, OS 파일·네트워크·자격증명 격리, kill-on-close 재시작 증명, GUI 시각 검증은 미수행이다.

## 2026-11-08 KST · P1.5 / P2 준비

- 기준 `d256f791f4b1c088a105a7297c5ef960ca64a2f0`. 시작 시 로컬과 GitHub main이 일치했고 변경 파일은 없었다. 변경 SHA는 이 기록을 포함하는 커밋에서 조회한다.
- `gui.py` 및 `projection.py`에 Tk Command Center, 읽기 전용 Projection V1, 감사 필터, 워터마크와 오류 차단을 추가했다. `sandbox_profile.py`는 호스트 통합을 제한하는 고정 XML만 생성한다. P1 원장·잠금·권한 코드는 그대로다.
- `python -B -m unittest discover -s tests -v`로 기존 P1 8건과 신규 Projection/GUI 연결 5건 및 XML 계약 1건을 실행했다. 임시 원장 CLI 흐름도 검증했다.
- GUI 창, DPI/접근성, OS 격리 및 실제 Codex는 미검증이다. 다음 우선순위는 승인된 격리 환경에서 P2 부정 테스트다.

## 2026-11-08 KST · P1

- 기준 `becd2f7abaca1b8af521d6536259a6faaadbeddc`. 변경 Git SHA는 이 기록을 포함하는 커밋으로 조회한다.
- `devorchestra/lease.py`, `ledger.py`, `runtime.py`, `policy.py`에서 Win32 단일 슬롯, 원장 트랜잭션·펜스, 상태 머신, 합성 승인 계약을 구현했다.
- `dashboard.py`와 `__main__.py`에 원장 기반 읽기 전용 콘솔과 소유자 명령을 추가했다. 상세 기록은 프로젝트·실행·역할 필터를 제공한다.
- `python -B -m unittest discover -s tests -v`의 Windows 격리 테스트 8건 PASS. 임시 원장의 CLI 초기화 → 승인 대기 → 승인 → 완료 → 대시보드·검증기 감사 조회도 PASS. `git diff --check` PASS.
- 검증 환경은 Windows NT 10.0.26200.0, Python 3.10.11, SQLite 3.40.1이다. .NET SDK 9.0.315와 10.0.301이 설치되어 있었으며 P1에서 사용하지 않았다.
- 원격 기준 확인은 GitHub 비교 API를 사용했다. 로컬 Git의 초기 `ls-remote`는 이름 해석 단계에서 실패했다. 실제 운영·GUI·샌드박스는 미검증이다.
