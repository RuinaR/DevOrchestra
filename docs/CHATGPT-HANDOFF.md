# ChatGPT 전달용 요약

P2.9 기준 `c86d63b65e9b8baf4302c1f658975689371d440b`. 이전 MXC 시작 성공과 `SystemDrive`·`LOCALAPPDATA` 준비 실패를 정적으로 비교했다. 런처 환경, 내부 명령 상속, OS 권한을 별도로 검증한다. 합성 `LOCALAPPDATA` 지원과 실제 경로의 내부 전파는 미검증이다. 다음은 별도 승인된 파일 없는 명시적 MXC 시작 진단 한 번이다. 이번에는 실행하지 않았다. P2 격리와 P3는 BLOCKED다. [런처 계약](P2-MXC-VALIDATION.md#p29-런처와-내부-명령의-경계)

P2.8 별도 승인 재시험은 `SystemDrive`를 교차 검증해 전달했다. 호스트 양성 대조 일곱 대상 PASS, MXC는 준비 중 `LOCALAPPDATA` 요구로 종료 코드 1이었다. 해시와 junction 대상은 그대로였고 대상별 접근 거부는 미측정이다. 실제 사용자 환경을 추가하거나 재시도하지 않았다. B/C MXC 경계는 NOT VERIFIED, P3는 BLOCKED다. [재시험 기록](P2-MXC-FILE-TEST.md)

P2.8 재시험 준비에서 이전 합성 루트·Codex 홈을 읽기 전용으로 확인하고 보존했다. CLI `0.167.0-alpha.3.2`에서 앞서 관측한 `SystemDrive` 요구 오류를 근거로 Windows 시스템 디렉터리에서 검증한 값을 전달하는 안을 확정했다. 새 GUID 루트에서의 양성 대조와 MXC 1회는 별도 승인 대기이며 B/C MXC 경계는 NOT VERIFIED, P3는 BLOCKED다. [수정안](P2-MXC-FILE-TEST.md#재시험용-최소-환경-수정안)

P2.8 합성 파일 시험은 호스트 양성 대조 7개 대상 PASS, MXC 1회는 `SystemDrive` 누락으로 준비 단계 종료 코드 1이었다. 표식 해시와 junction은 불변이며 접근 거부 실측은 없다. 재시험에는 최소 환경 수정 검토와 새 승인이 필요하다. P3는 BLOCKED다. [시험 결과](P2-MXC-FILE-TEST.md)

P2.8 기준 `ac306f4ef05e0e939b4e9cf5c7f9c5f4794f90f4`. 앞선 명시적 MXC·`:workspace` 시작 진단은 `MXC_OK`/0이었다. 별도 합성 프로필과 파일·junction 양성 대조, 실행 명령, 전후 해시 기준을 준비했다. 파일 시험은 별도 승인 대기이며 시스템 경로 및 MXC/Job 결합은 미검증이다. P3는 BLOCKED다. [시험 계약](P2-MXC-FILE-TEST.md)

P2.7 기준 `71abfd91f6ac80dcd46548e44d13a4b0866eaa76`. 현재 Windows PC의 MXC를 우선 검토하고 VirtualBox 계획은 보류했다. OS와 CLI 수치 기준을 충족했지만 장치·유효 정책·Job 결합은 미검증이다. 다음은 소유자 승인 후 합성 폴더에서 공식 명시적 MXC 시작 진단 한 번이다. 파일·네트워크·데스크톱·소유 Job·원장 보호와 검역이 입증될 때까지 P3와 실제 Codex 실행은 BLOCKED다. [MXC 시험 계약](P2-MXC-VALIDATION.md)

P2.6 기준 `9c748eb4b78923e083dfe9340984071d7379c922`. 현재 PC의 Windows Core x64와 AMD-V 펌웨어 표시, Microsoft 하이퍼바이저 실행을 읽기 전용으로 확인했다. WHP 및 VBS 런타임 조회는 제한됐고 VirtualBox는 표준 위치에서 확인되지 않았다. Oracle 7.2.22 x64의 `VBoxApplication`만 포함하는 설치안과 별도 승인·자원·A/B/C 순서를 기록했다. 다운로드·설치·VM 실행은 미수행이다. 실제 VM 격리·반출과 P3는 BLOCKED다. [사전 점검](P2-VM-VALIDATION.md#p26-현재-pc-도입-사전-점검)

P2.5 기준 `1519b7874d6e6716aef33746b94d7b79adb24ece`. 전용 x64 테스트 PC의 VirtualBox 7.2 계열·Windows 11 게스트를 1순위로 선정했고 A/B/C 양성 대조, 합성 매체, 오프라인 반출, 네트워크 모드 분리와 GUI 인수 계획을 확정했다. 현재 PC에는 즉시 실행할 후보가 확인되지 않았다. A 일부 Job과 C 합성 검역만 PASS이며 게스트 격리·반출과 P3는 BLOCKED, 실제 GUI는 NOT VERIFIED다. 다음은 [전용 환경 승인과 실측](P2-VM-VALIDATION.md)이다.

P2.4 기준 `07f6ff7d86a3f247d9b8eab35ad6b4743d35c100`. 증분 합성 이벤트 영수증을 기존 SQLite 원장에 개별 커밋하고, 커밋된 접두사만 복구하며 새 이벤트 이어받기는 차단한다. 합성 사용량 최고치와 승인·종료·검증 근거를 후속 작업 게이트에 연결했다. 전체 44건 및 CLI PASS. 실제 GUI·제공자 계측·재접속은 미검증이며 OS 격리·반출·Codex는 BLOCKED다.

P2.3 기준 `92280880309ac358dd193eb66d7bb997cc4413b0`. 기존 단일 슬롯과 원장에 재시작 가능한 장기 Mock 세션, 동결 설정, 누적·계층 예산, Manager 제안 검증과 오프라인 App Server 이벤트 감사를 연결했다. 기존 32건과 신규 7건, CLI 흐름 PASS. Command Center는 세션 진행과 상세 기록을 표시한다. 실제 GUI와 제공자 연결은 미검증이며 OS 격리·반출·Codex는 BLOCKED다. 다음은 승인된 독립 VM에서 P2 A/B/C 실측이다.

P2.2 기준 `9551db4e7288e9db0a9830c1f370f56d0bb73141`. 별도 해시 체인 프로젝트 등록부, revision 기반 설정 편집, 역할별 모델 선호값/예산, 합성 스트림 정규화, 승인 만료 및 상세 감사가 추가됐다. Command Center/CLI에서 설정을 관리하며 등록은 권한이 아니다. Windows 회귀 26건과 신규 계약 6건, CLI 흐름 PASS. 실제 GUI와 제공자 계측은 NOT VERIFIED이며 P2 A/B/C 실측 전 Codex·임의 실행·운영 서비스는 BLOCKED다.

P2.1 기준 `24fc3e74f8f1ef483b00c4677a97639b02061a6f`. Job 실패 경로의 종료와 핸들 정리를 보강했고 종료 영수증 유실 시 재실행 차단을 검증했다. 메모리 내 합성 번들 검역은 경로·목록·크기·SHA-256·UTF-8을 검사한다. 26건 및 기존 CLI PASS. A는 제한적 실제 Job PASS, B 및 C의 게스트 검증은 BLOCKED다. 현재 Windows는 `Core`이며 Sandbox 표준 실행 파일과 `Get-VM`을 확인하지 못했다. 다음 단계는 승인된 전용 VM의 거부·회수 시험이다. P3/Codex는 BLOCKED다.

P2 기준은 `a2f650e56251c6145091d1e4686679af587b63eb`다. 실제 Win32 Job으로 고정 합성 루트와 자식 종료를 검사했다. 전역 뮤텍스, 실행 세대, 시작/생성/종료 영수증을 묶었고 트리 활성 0을 확인하기 전 다음 역할을 차단한다. 종료 증거가 없는 재시작은 UNKNOWN으로 멱등 차단한다. 전체 18건과 기존 CLI 흐름 PASS. OS 파일·네트워크·자격증명 격리와 Codex는 BLOCKED다. 실제 전원 상실, 소유자 강제 종료 후 kill-on-close, GUI 시각 검증은 NOT VERIFIED다. 다음 단계는 승인된 WSB/VM 부정 시험이다.

DevOrchestra는 Windows 우선 로컬 AI 개발 관리 앱이다. P1 구현은 `0ebe8db`, P1.5 기준 설계는 `d256f79`다. 기존 Mock 역할 파이프라인, Win32 단일 슬롯, SQLite 감사, 합성 승인, 복구와 CLI를 유지한다. P1.5는 Tk Command Center와 읽기 전용 Projection V1, 마일스톤·단계·역할 카드, 프로젝트/실행/역할별 영수증 조회를 제공한다.

Windows 임시 원장에서 기존 8건, 신규 6건 및 CLI 흐름을 검증했다. 실제 GUI 창 시각 검증은 NOT VERIFIED다. P2는 Windows Sandbox 제한 XML 계약만 준비했고 Job/VM/Sandbox 집행, 실제 Codex 및 임의 코드는 BLOCKED다. 다음 작업은 승인된 격리 환경에서 자체 자식 트리 종료 및 호스트 파일·네트워크·자격증명 접근 거부를 검증하는 것이다. [현재 상태](CURRENT-STATUS.md) · [실행 방법](../README.md)
