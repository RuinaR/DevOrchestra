# 현재 상태

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
