# 현재 상태

P2.15 승인된 MXC Python `-V` 1회는 `0xC0000022`로 종료했고 표준 출력·오류는 없었다. 버전 출력 FAIL, 거부 단계 미확정. 후속 실행 없이 중단했다. P2 전체와 P3는 BLOCKED다. [진단 기록](P2-MXC-FILE-TEST.md#p215-승인된-python-버전-진단-결과)

P2.14 정적 분석에서 작업 공간 밖의 Python 실행 이미지와 앱 로컬 DLL 접근을 우선 가설로 좁혔다. 거부 단계는 미확인이다. 다음은 별도 승인된 MXC 내부 Python `-V` 1회이며, 이번 실행은 0회다. [정적 분석 및 단계별 시험](P2-MXC-FILE-TEST.md#p214-python-시작-경로의-정적-분석)

P2.13 호스트 대조 1회에서 7개 경로 읽기·쓰기 성공. MXC 1회는 `0xC0000022`로 종료됐으며 대상 결과가 없었다. 표식·junction은 불변이지만 B 파일 경계와 C의 MXC 원장 보호는 NOT VERIFIED다. P2 A/B/C 전체와 P3는 BLOCKED다. [시험 결과](P2-MXC-FILE-TEST.md#p213-승인된-파일-시험-결과)

P2.10 승인 진단 1회에서 명시적 MXC와 `mxc-file-test` 조합으로 `MXC_OK` 및 종료 코드 0을 확인했다. 시작만 PASS다. OS 접근 거부·비간섭·외부 Job 결합은 NOT VERIFIED이며 P2 A/B/C 전체 및 P3는 BLOCKED다. [시작 진단 결과](P2-MXC-VALIDATION.md#p210-승인된-시작-진단-결과)

P2.12 기준 `44f731aaca227e8a5a6bb8a81b894da4d6aaba00`. 현재 OpenAI 서명 CLI 버전을 직접 확인했다. P2.10 시작 진단은 별도 승인 대기다. 합성 후보와 실제 Controller 테스트 영수증 계약을 분리했고, 실제 영수증 발행·실행 어댑터·P2 A/B/C·P3는 BLOCKED다. [CLI 점검](P2-MXC-VALIDATION.md#p212-현재-cli-확인)

P2.11 기준 `be6db0e712b0857d52bd4316af2c86630ce1b0f7`. 전용 합성 원장 기반 커밋 후보 등록·재확인을 구현했다. 실제 커밋 실행 및 스테이징은 미연결이며 P2.10은 별도 승인 대기, P2 A/B/C와 P3는 BLOCKED다. [커밋 후보 계약](ARCHITECTURE-ROADMAP.md#p211-오프라인-커밋-후보)

기준 `41043393e954d97e8087acbddeff38a6734a91b3`. 최우선 인수 조건을 사용자 앱 비간섭으로 확정했다. 오프라인 CLI 사전 검사와 Command Center에서 실제 실행 BLOCKED를 표시한다. MXC는 후보이며 비간섭은 NOT VERIFIED다. P2.10은 별도 1회 승인 대기다. [실제 자동 개발 우선순위](ARCHITECTURE-ROADMAP.md#실제-자동-개발까지의-우선순위)

P2.10 기준 `cf6ab48d48fcb53717b239138e35aa7a2a5a97a0`. Known Folder·SystemDrive 사전 점검은 통과했다. 현재 CLI 경로는 이전 관측과 다르고 파일 버전 메타데이터가 없어 정확한 버전 확인이 남았다. 실행은 별도 승인 대기이며 P2 A/B/C 및 P3는 BLOCKED다. [최소 시작 진단](P2-MXC-VALIDATION.md#p210-최소-시작-진단-사전-점검)

P2.9 기준 `c86d63b65e9b8baf4302c1f658975689371d440b`. 앞선 성공한 호스트 시작 진단과 두 최소 환경 실패를 정적으로 비교했다. 신뢰 런처 초기화 입력, 내부 환경 상속, OS 권한을 구분했다. 실제 및 합성 `LOCALAPPDATA` 사용 가능성과 내부 전파 여부는 미검증이다. 다음은 별도 승인된 파일 없는 최소 시작 진단이다. P2 파일·네트워크·프로세스 격리와 P3는 BLOCKED다. [런처 계약](P2-MXC-VALIDATION.md#p29-런처와-내부-명령의-경계)

P2.8 재시험 기준 `d14c0b8ab9b73e0827e18d70e5be599574580ef6`. 새 루트에서 호스트 대조 7개 PASS, `SystemDrive`를 보완한 MXC 1회는 `LOCALAPPDATA` 요구로 준비 단계에서 종료 코드 1이었다. SHA-256과 junction 대상은 동일하다. 파일 경계와 MXC 원장 보호는 NOT VERIFIED이며 P3는 BLOCKED다. [재시험 기록](P2-MXC-FILE-TEST.md#2026-11-09-별도-승인-재시험)

P2.8 재시험 준비 기준 `fe1f1e2dc145833398ee2ffe9ceed11648ca362d`. 이전 합성 루트와 시험용 Codex 홈을 읽기 전용으로 확인하고 보존했다. Windows 시스템 디렉터리에서 확인한 드라이브를 최소 환경에 전달하는 안을 확정했다. 재시험은 새 승인 대기이며 B 파일 경계와 C의 MXC 원장 보호는 NOT VERIFIED, P3는 BLOCKED다. [최소 환경 수정안](P2-MXC-FILE-TEST.md#재시험용-최소-환경-수정안)

P2.8 실행 기준 `96125464afb62848999647e1aaa5fa578c1dfcef`. 호스트 양성 대조는 일곱 대상 모두 PASS였다. MXC 1회는 시험 환경의 `SystemDrive` 누락으로 준비 단계에서 종료 코드 1을 반환했다. 모든 전후 표식 해시와 junction 대상은 동일했으나 MXC 파일 접근 결과는 미측정이다. A 기존 증거와 C 합성 검역만 유지한다. B 파일 경계와 C의 MXC 원장 보호는 NOT VERIFIED이며 P3는 BLOCKED다. [실행 결과](P2-MXC-FILE-TEST.md#2026-11-09-실행-결과)

P2.8 기준 `ac306f4ef05e0e939b4e9cf5c7f9c5f4794f90f4`. 별도 승인으로 명시적 MXC·`:workspace` 시작 진단 1회에서 `MXC_OK` 및 종료 코드 0을 관측했다. 파일 시험의 프로필·양성 대조·명령·판정은 확정했으며 실행은 별도 승인 대기다. A는 기존 제한 Job 및 MXC 시작 증거만 있다. B와 MXC/Job 결합은 미검증, C는 기존 합성 검역만 PASS다. P3는 BLOCKED다. [합성 파일 시험](P2-MXC-FILE-TEST.md)

P2.7 기준 `71abfd91f6ac80dcd46548e44d13a4b0866eaa76`. Windows 11 25H2 빌드 및 설치 CLI 버전이 공식 MXC 시작 진단의 수치 기준을 충족한다. 장치 호환성·유효 정책·Job 결합은 NOT VERIFIED다. VirtualBox 계획은 보류했다. MXC 진단은 별도 승인 대기이며 P2 실측과 P3는 BLOCKED다. [MXC 사전 검토](P2-MXC-VALIDATION.md)

P2.6 기준 `9c748eb4b78923e083dfe9340984071d7379c922`. 현재 Windows Core x64의 AMD-V 펌웨어 표시와 Microsoft 하이퍼바이저 실행을 확인했다. WHP·VBS 런타임과 VirtualBox 실제 부팅은 미확인이다. 기본 패키지의 최소 설치 승인안을 작성했다. 설치, 다운로드, 설정 변경, VM 실행은 수행하지 않았다. 현재 PC의 부팅은 승인·자원 여유·WHP 확인 전 BLOCKED다. 기존 A 일부 Job 실측과 C 합성 검역만 PASS이며 OS 격리·반출·P3는 BLOCKED다. [P2.6 사전 점검](P2-VM-VALIDATION.md#p26-현재-pc-도입-사전-점검)

P2.5 기준 `1519b7874d6e6716aef33746b94d7b79adb24ece`. Windows Core x64에서 VirtualBox·VMware 관리 도구를 PATH와 표준 경로에서 찾지 못했다. 전용 x64 테스트 PC의 VirtualBox 7.2 계열과 Windows 11 게스트를 1순위로 선정했다. 설치·설정 변경 및 VM 실행은 하지 않았다. A는 일부 Win32 Job 실측만 PASS, B는 BLOCKED, C는 합성 검역만 PASS이며 실제 반출은 BLOCKED다. GUI는 NOT VERIFIED, P3는 BLOCKED다. [시험과 승인 범위](P2-VM-VALIDATION.md)

P2.4 기준 `07f6ff7d86a3f247d9b8eab35ad6b4743d35c100`. 역할별 증분 영속 합성 이벤트, 커밋된 접두사 복구, 사용량 최고치 및 세션 다음 작업 게이트를 추가했다. 기존 39건과 신규 5건 및 CLI 흐름 PASS. 실제 제공자 사용량·재접속·GUI는 NOT VERIFIED다. 독립 GUI 시험 환경은 확인되지 않았다. 실제 Codex·임의 프로젝트·OS 격리와 반출은 계속 BLOCKED다.

P2.3 기준 `92280880309ac358dd193eb66d7bb997cc4413b0`. 두 작업 이상을 잇는 합성 세션, 계층형 예산·관리자 제안 검증, 오프라인 App Server 이벤트 감사와 Command Center 세션 표시를 추가했다. 기존 32건과 신규 7건, 기존/세션 CLI 흐름 PASS. 실제 GUI, 제공자 계측·재접속은 NOT VERIFIED. P2 OS 격리·반출, 실제 Codex 및 임의 프로젝트 실행은 BLOCKED다.

P2.2 기준 `9551db4e7288e9db0a9830c1f370f56d0bb73141`. 프로젝트 등록부·역할/전역 예산·동결된 합성 실행 계약·상세 감사 및 설정 UI를 연결했다. 회귀 26건과 신규 계약 6건, 기존 및 등록 프로젝트 CLI 흐름을 검증했다. OS 파일·네트워크·자격증명 격리, 게스트 반출 및 실제 Codex 실행은 계속 BLOCKED다. 실제 창과 제공자 계측은 NOT VERIFIED다.

P2.1 기준 `24fc3e74f8f1ef483b00c4677a97639b02061a6f`. 실제 Win32 합성 Job 회귀와 새 Mock 정리 실패·영수증 유실 및 번들 검역을 포함해 26건 PASS. Command Center는 격리 방식, 파일·네트워크·자격증명 차단과 역사적 Job 기준 SHA를 표시한다. 실제 창은 NOT VERIFIED다.

게이트 A는 제한된 Job 시나리오만 PASS다. B는 BLOCKED이며 C는 합성 검역만 PASS, 실제 게스트 보호·반출은 BLOCKED다. Windows 에디션 `Core`, Sandbox 표준 실행 파일 및 `Get-VM` 미확인으로 현재 호스트의 게스트 실행 시험을 보류했다. 시스템 설정을 변경하지 않았다. P2 전체 및 P3 진입은 BLOCKED다. [별도 승인 범위](ARCHITECTURE-ROADMAP.md#p21-게스트-시험과-산출물-회수)

P1 구현 `0ebe8db5ca7a05715862383dff09cd9492cef4c5`. P1.5 기준 Git SHA `d256f791f4b1c088a105a7297c5ef960ca64a2f0`. 시작 시 로컬 main, origin/main 추적 참조 및 GitHub main이 일치했고 미커밋·무시 파일은 없었다.

P2 기준은 `a2f650e56251c6145091d1e4686679af587b63eb`이다. P2 시험을 포함한 18건과 기존 CLI 흐름을 검증했다. 화면의 제품 마일스톤은 P1/P1.5 검증 커밋에 귀속되며 Mock 실행 성공으로 바뀌지 않는다.

| 판정 | 범위 |
| --- | --- |
| PASS | Windows 합성 실행의 역할 순서, 승인 대기·거부, 예산 차단, 취소·일시정지·재개, 경쟁 프로세스 배제, 소유 프로세스 강제 종료 후 결과 UNKNOWN 및 명시 복구, 감사 장애·손상 차단 |
| PASS | 터미널 대시보드 렌더링 및 원장 일치, 프로젝트·역할 전이와 주요 배지, 기록 없는 상태에서 진행 표시 없음 |
| PASS | P1.5 합성 원장의 빈 상태, 역할·프로젝트·실행 필터, 실패·차단·복구, 원장 손상 시 GUI 렌더 상태 제거, 원장 계보 변경 차단 및 P2 제한 XML 계약 |
| PASS | 실제 Win32 Job의 고정 합성 프로그램 정상·비정상 종료, 루트 종료 후 자식 잔류 관측, 시간 한도·취소 후 활성 수 0, 96 MiB 및 동시 활성 상한 2 설정 읽기, 단계 재진입 차단 |
| PASS (합성 장애) | 종료 증거 없는 원장을 재개방하면 다음 작업을 차단하고 명시 복구를 멱등적으로 UNKNOWN 처리 |
| BLOCKED | 임의 코드·실제 프로젝트·Codex·네트워크·GPU·실서비스. OS 집행 경계가 검증될 때까지 실행 경로 미제공 |
| NOT VERIFIED | 실제 GUI 창, Sandbox/VM 집행, 소유자 강제 종료 시 kill-on-close, 전원 상실, 다중 사용자 영속 입장, 메모리 초과 거부 |

허용 경로 읽기·쓰기, 외부 경로 및 reparse 우회 거부, 네트워크·자격증명 접근 거부, 호스트 공유·클립보드 차단 및 산출물 회수는 OS 검증 환경이 필요해 BLOCKED다.

터미널에서 프로젝트, 단계, 역할별 최근 실행, 시각·시간·합성 토큰 및 이벤트를 조회할 수 있다. 실행 상태와 근거는 SQLite 원장에서, 설명은 합성 역할 결정에서, 단계 배지는 장식에서 가져온다. 감사 원장에 시작만 기록된 역할의 실제 활성 여부는 UNKNOWN이다. 계측되지 않은 비용·사용량과 실제 Git·테스트 결과를 합성 데이터로 대체하지 않는다.

Windows Command Center는 구현됐으며 합성 원장과 헤드리스 렌더 연결을 검증했다. 다음 작업은 [P2 격리 검증 계획](ARCHITECTURE-ROADMAP.md#p2-격리-검증-계획)에 따라 승인된 환경에서 자체 자식 종료 및 파일·네트워크·자격증명 거부 증거를 확보하는 것이다.
