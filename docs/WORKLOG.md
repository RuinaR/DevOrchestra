# 작업 기록

## P2.6 · 현재 PC 설치 전 점검

- 기준 `9c748eb4b78923e083dfe9340984071d7379c922`. 로컬·원격 main 일치, 미커밋 변경 없음. 변경 SHA는 이 기록의 커밋에서 조회한다.
- OS·CPU·펌웨어·하이퍼바이저·메모리·볼륨·VirtualBox 표준 위치를 읽기 전용으로 점검했다. CIM과 WHP 접근 제한은 미확인 항목으로 남겼다. 공개 문서에 원시 시스템 조회 결과를 포함하지 않았다.
- 최소 MSI 기능과 독립 승인 범위, 초기 단독 오프라인 VM 및 A/B/C 시험 순서를 문서화했다. 실행 코드·설정·설치 변경은 없으며 문서 검사만 수행했다.

## P2.5 · 실행 환경 선정

- 기준 `1519b7874d6e6716aef33746b94d7b79adb24ece`. 로컬·원격 main 일치, 미커밋 변경 없음. 변경 SHA는 이 기록의 커밋에서 조회한다.
- 공식 자료와 읽기 전용 표준 경로 확인을 바탕으로 전용 PC의 VirtualBox를 선정했다. A/B/C 양성 대조, 종료 증거, 반출, 두 가지 네트워크 모드, GUI 인수와 필요한 승인을 `P2-VM-VALIDATION.md`에 기록했다.
- 실행 코드 변경 없이 Windows 기존 44건과 CLI 합성 흐름을 검증했다. 다운로드, 설치, 설정 변경, VM·GUI 실행 및 실제 모델 연결은 하지 않았다.

## P2.4 · 증분 스트림 감사

- 기준 `07f6ff7d86a3f247d9b8eab35ad6b4743d35c100`. 로컬·원격 main 일치, 미커밋 변경 없음. 변경 SHA는 이 기록의 커밋에서 조회한다.
- 기존 실행 원장으로 수신 영수증을 개별 커밋한다. 재시작 후 미확인 접미사를 차단한다. 합성 사용량 최고치, 승인 요청 상관관계, Manager 후속 작업 게이트, Command Center 상세 표시를 연결했다.
- Windows 기존 39건과 신규 5건, 기존 및 세션 CLI PASS. 완료 이벤트 쓰기 실패에서 접두사와 사용량 보존, 다음 역할 차단을 확인했다. 실제 GUI, App Server, 게스트 격리 시험은 수행하지 않았다.

## P2.3 · 세션 및 이벤트 감사

- 기준 `92280880309ac358dd193eb66d7bb997cc4413b0`. 시작 시 로컬과 GitHub main 일치, 미커밋 변경 없음. 변경 SHA는 이 기록의 커밋에서 조회한다.
- 기존 원장·뮤텍스를 재사용해 세션 입장, 동결 설정, 누적 예산과 Manager 판정을 연결했다. 로컬 봉투 기반 오프라인 감사, 세션 CLI, 게임형 읽기 표시를 추가했다.
- Windows 회귀 32건, 신규 7건 및 CLI 두 작업 흐름 PASS. 실제 창·App Server 연결·OS 격리 시험은 수행하지 않았다. 다음 게이트는 승인된 독립 VM의 P2 A/B/C 실측이다.

## P2.2 · 등록부와 합성 역할 계약

- 기준 `9551db4e7288e9db0a9830c1f370f56d0bb73141`. 시작 시 로컬·원격 main 일치, 미커밋 변경 없음. 변경 SHA는 이 기록의 커밋에서 조회한다.
- `catalog.py`에 설정 원장과 revision Controller, `adapter.py`와 `runtime.py`에 역할 문맥·Mock 스트림·승인 만료·예산·종료 감사, CLI와 Command Center에 조회·설정 편집을 추가했다.
- Windows 회귀 26건과 신규 계약 6건, 기존/등록 프로젝트 CLI 흐름 PASS. 합성 임시 저장소만 사용했다. 실제 GUI 창, 제공자 모델·사용량, VM 접근 거부는 미검증이다. 다음 우선순위는 별도 승인된 게스트의 P2 A/B/C 실측이다.

## P2.1 · 장애 정리 및 검역

- 기준 `24fc3e74f8f1ef483b00c4677a97639b02061a6f`. 로컬과 원격 main이 일치했고 변경 사항은 없었다. 변경 SHA는 이 기록의 커밋을 조회한다.
- `job_probe.py`는 비정상 경로에서 조회 전에 소유 Job 종료를 요청하고 핸들마다 닫기를 시도한다. 원장 종료 증거가 없으면 기존 입장 차단을 유지한다. `quarantine.py`에 합성 입력 패키징과 메모리 내 검역을 추가했다. Projection/GUI의 보안 표시를 분리했다.
- `python -B -m unittest discover -s tests -v` 26건 PASS. 실제 Win32는 기존 저부하 합성 Job을 대상으로 했다. 정리 API 및 영수증 쓰기 실패는 Mock, 번들은 합성 데이터다. 기존 CLI 흐름 PASS.
- Windows 읽기 전용 환경 점검 결과와 B/C의 승인·시험 범위를 아키텍처에 기록했다. Sandbox/VM 실행, 시스템 설정 변경, GUI 캡처 및 실제 서비스 호출은 하지 않았다.

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
