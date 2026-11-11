# 개발 인수인계

두 번째 등록 프로젝트 온라인 시험은 Manager 계획과 Developer 변경을 확인했다. Reviewer가 승인 이유를 덧붙여 기존 파서가 중단했고 복제본과 스테이징을 보존했다. 같은 복제본의 별도 MXC 검사는 `hello` 통과, `goodbye` 미충족이었다. 판정 파서는 명시적인 판정 토큰과 한 줄 근거를 받아들이며, 최종 Manager와 Controller 판정의 일치 요구는 유지한다. 다음 온라인 시험은 남은 복구 자료를 보존하고 새 온보딩 복제본의 승인을 받아야 한다.

첫 등록 프로젝트 온라인 시험은 GUI 승인과 모델 호출을 확인했지만 계획 Manager가 셸을 세 번 사용해 역할 한도에서 종료됐다. 원본과 복제본은 온보딩 기준에 맞는다. 이제 등록 세션의 계획 및 최종 Manager는 Controller가 제공하는 승인 파일, 프로젝트 파일 목록, 기준과 검증 결과로 판정한다. 셸 도구는 Developer와 Reviewer에만 연결한다. 기존 합성 CLI 세션의 실행 정책은 유지한다.

Command Center의 개발 시작 → 등록 프로젝트 Codex 자동 개발로 온보딩 복제본을 선택한다. 등록 목표와 기준은 프로젝트 설정에서 수정하며 온보딩 기준이 바뀌면 다시 검토한다. 검증 명세는 설치 EXE의 절대 경로, 기준별 고정 인자와 기대 출력, 변경 대상에서 제외한 검증 입력 목록을 요구한다. 승인과 결과는 로컬 원장에 남고 완료된 동일 복제본은 별도 원본 반영 검토에서 diff를 표시한다. 모델을 대체한 Windows GUI 통합 시험은 통과했다. 실제 온라인 시험은 독립 텍스트 출력 프로젝트만 별도 승인 대상으로 올린다.

역할별 한도를 조정한 온라인 재시험은 `COMPLETED`로 종료됐다. 승인 한 번으로 A를 검증하고 B로 자동 전환했다. 두 번째 독립 Reviewer 승인, MXC Verifier의 두 기준 통과, 최종 Manager와 Controller의 완료 합의를 확인했다. 모델 호출 8회의 보고 합계는 144,279토큰이었고 Job과 스테이징을 회수했다. 다음 제품 작업은 이 검증된 순차 실행을 등록 프로젝트와 Command Center에 연결하는 것이다.

조정된 역할 한도의 첫 재시험은 Developer 응답 완료 뒤 CLI 종료 지연에서 멈췄다. 복구 스테이징은 보존됐다. 새 경로는 회수된 쓰기 역할에서 완료 이벤트와 사용량·테스트 증거를 확인하고, 승인 파일의 실제 변경 및 원본·Git 기준을 대조한 뒤 Reviewer 입장을 허용한다. 온라인 재시험에는 새 세션 승인이 필요하다.

변경 범위를 바로잡은 온라인 시험에서 두 번째 Reviewer가 `ACCEPT`를 반환했다. 역할별 토큰 한도 초과로 후속 Verifier와 최종 Manager는 미실행이다. 다음 시험은 Reviewer와 Developer의 예약량을 각각 55,000 및 60,000토큰으로 검토한다. 세션 예산과 사이클·시간·호출 한도는 동일하다. 이번 시험의 스테이징은 정리가 미확인 상태이며 소유 Job은 회수됐다.

승인 한 번으로 실제 자동 두 사이클과 두 기능의 독립 검사를 완료했다. 최종 완료를 막은 원인은 두 번째 Reviewer에 전달한 누적 diff였다. Reviewer에는 해당 사이클의 시작 상태와 종료 상태를 비교한 변경을 전달하고, 원장에는 그 변경과 전체 Git diff의 해시를 각각 남긴다. 온라인 재시험의 완료 조건에는 두 번째 독립 Reviewer 승인과 최종 Manager의 `COMPLETED`가 포함된다.

온라인 재시험의 최종 Manager는 `CONTINUE`를 반환했다. 완료 이벤트 이후 CLI 종료 지연으로 원래 세션은 멈췄고 해당 스테이징을 보존했다. 읽기 전용 역할에 한해 정상 종료와 강제 회수를 구분해 기록하고, Job 회수·완료 이벤트·사용량·복제본 불변을 모두 확인하면 다음 역할을 허용한다. 오프라인에서 확인했으며 실제 두 사이클은 새 승인 후 검증한다.

첫 온라인 통합 시험에서는 다섯 역할이 한 사이클을 수행했고 A가 검증됐다. 최종 Manager가 아직 착수하지 않은 B를 보고 `REWORK`를 반환해 Controller가 자동 전환을 중단했다. 현재 기능의 독립 검사 결과, 다음 사이클의 미착수 기준, 회귀 여부를 최종 Manager에게 명시적으로 전달한다. 결과의 불일치 차단은 유지한다. 기존 시험 승인과 복제본은 종료됐으며 온라인 재시험에는 새 승인이 필요하다.

`native-cycle --archive <공식 ZIP> --request-id <32자리 hex> --expires <UTC epoch> --seconds 960 --tokens 270000 --cycles 2`는 실제 두 사이클 시험의 승인 범위를 검토한다. 사용자 동의 후 동일 인자와 `--approve <해시>`를 사용한다. 최대 Codex 호출은 8회다. Manager는 각 30,000토큰, Reviewer는 55,000토큰, Developer는 60,000토큰을 입장 전에 남은 예산과 비교한다. Verifier는 모델을 호출하지 않는다. 각 호출의 실제 입력+출력은 사후 판정한다. Developer는 MXC 복제본 내 셸과 쓰기 권한을 사용하며 파일별 변경은 Controller가 사후 대조한다. Reviewer와 두 Manager 단계는 MXC 읽기 전용이다. 미확정 요청은 자동 재송신하지 않고 증거를 보존한다. 프로세스 회수가 미확정이면 원장 의도를 열린 채 두어 신규 입장을 막는다.

공식 Codex 0.167.1 로컬 Code Mode로 첫 온라인 Developer 시험이 `REAL_PASS`였다. `gpt-6.1-sol`이 합성 Git 복제본의 두 파일을 직접 수정했고 모델 명령과 Controller 독립 시험에서 모두 `11`을 확인했다. 사용량 32,588토큰, diff와 소유 Job 회수 결과를 로컬 원장에 기록했다. 이전 `CODE_MODE_HOST_DISABLED` 결과와 로컬 합성 시험은 상태 문서에서 별도로 추적한다.

`native-developer-trial --archive <공식 ZIP> --request-id <32자리 hex> --expires <UTC epoch> --role-tokens <N> --session-tokens <N> [--model <ID>]`는 로컬 명령 검증 영수증과 함께 일회 승인 범위와 해시를 검토한다. 전용 `.ai-local/state/codex-developer-home`의 공식 ChatGPT 로그인을 사용한다. 명시 동의 후 같은 인자와 `--approve <해시>`로 한 턴을 실행한다. 실행은 최대 120초다. 두 토큰 기준은 제공자가 보고한 입력+출력에 적용하는 사후 판정 기준이다. 캐시·추론은 각각 입력·출력에 포함되며 중복 합산하지 않는다. 제공자 쪽 강제 제한이 아니므로 초과가 가능하다. 복제본 전체가 MXC 쓰기 범위이고, 지정한 두 파일 외의 변경은 Controller가 결과에서 거부한다. 합성 `subtotal.cmd`와 `total.cmd`만 바뀌고 모델 명령과 독립 시험이 모두 11을 관측해야 성공한다. UNKNOWN 요청은 자동 재실행하지 않는다.

현 제품은 일반 개발만 제공한다. 공식 Codex가 인증·모델 통신을 맡는다. `native-exec-diagnostic --code-mode --archive <공식 0.167.1 Windows x64 ZIP>`은 자격증명 없는 합성 Git 복제본에서 공식 CLI를 동시 프로세스 16개로 제한한 소유 Job으로 시작해 로컬 도구 실행을 확인한다. 실제 구독 두 사이클 검증 뒤 등록 프로젝트 및 Command Center 연결 범위를 확정한다. [Codex 권한 범위](https://learn.chatgpt.com/docs/permissions#what-the-network-proxy-does-not-control)

이하 전용 계정·WFP·프록시·별도 게스트 시험과 복구 지침은 역사 자료다. 제품 실행 승인이나 다음 개발 작업에는 적용하지 않는다.

P2.119의 새 MSVC x64 빌드는 비승격 `pilot.exe --job-preflight`로 생성 시 Job 편입과 실패 경로를 확인할 수 있다. 기존 `native-egress-trial`의 고정 해시와 복구 계약은 비활성 P2.117 빌드에만 적용된다. 다음 통합은 원장 의도를 먼저 기록하고, Job 핸들을 보유하지 않는 독립 관찰자가 실행 중인 고정 작업자·자식의 대기 핸들, 생성 시각, SID 및 Job 소속을 결속해야 한다. 소유 Controller 종료 후 관찰자가 두 종료를 증명하고 시험 계정의 신규 실행을 막은 상태에서만 별도 UAC 복구로 넘어간다. 관찰 증거가 없으면 `CLEANUP_BLOCKED`를 유지한다. 실제 교차 계정 생성에는 선택된 실행 토큰에 따라 Windows 호출 권한이 추가로 필요할 수 있다. [CreateProcessAsUserW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessasuserw)

P2.118에서 일반 사용자 권한의 Windows 시험으로 Job 이탈 요청 거부, 시험 Controller 강제 종료 후 작업자·자식 핸들의 종료, 미확정 원장의 재입장 차단을 확인했다. 전용 계정에서 실행할 다음 시험과 복구의 승인 범위는 [P2.118 시험 계획](ARCHITECTURE-ROADMAP.md#p2118-활성-작업자와-로컬-프록시-시험-계획)에 있다.

P2.117 관리자 시험에서 비활성 계정의 설치기 강제 종료 후 필터 16개의 영속성을 관측했고, 별도 복구 후 Controller가 시험 개체의 부재를 확인했다. 원장 상태는 `CLEANUP_VERIFIED`다. 다음에는 기존 네이티브 실행기·Job·원장을 재사용해 활성 작업자 종료 후 회수와 지정 프록시 주소·포트의 송신 경계를 검증한다. 같은 실행 신원에서 허용 연결과 직접 IPv4·IPv6, DNS 및 자식 송신 거부를 비교하고, 작업자가 남아 있거나 회수가 미확정이면 정책 제거와 새 실행을 차단한다. 해당 경계를 확인하고 별도 승인을 받은 뒤 독립 Git 복제본에서 공식 Codex Developer의 직접 수정·테스트를 검증한다.

P2.117 `native-egress-trial --binary <절대 경로> --expires <UNIX_SECONDS>`는 `.ai-local/temp/wfp-trial-*` 아래의 고정 해시 `pilot.exe`와 변경 범위를 검토한다. `--approve <SHA256>`는 명시적 관리자 시험 승인 이후에만 사용한다. 설치기는 영속 WFP 필터를 읽어 확인한 뒤 **계정을 활성화하기 전에** 자체 프로세스를 종료한다. 결과의 `request_id`로 `--recover <ID>` 검토를 만든 다음 별도 복구 승인 해시를 사용한다. 복구기는 시험 ID·비활성 계정·WFP 소유권을 확인하고 지정 개체를 회수한다. Controller는 별도 BFE 조회, 계정 및 디렉터리 부재를 확인한 뒤에만 원장 펜스를 닫는다. 작업자가 실행된 상태의 강제 종료 복구는 이 시험 범위 밖이며 미확정 상태의 정책을 자동 제거하지 않는다. [WFP 개체 수명](https://learn.microsoft.com/en-us/windows/win32/fwp/object-management)

P2.116의 관리자 승인 루프백 시험기는 `tools/wfp-loopback/pilot.cpp`다. MSVC x64 개발 환경에서 `cl /std:c++17 /EHsc /W4 /WX /O2 pilot.cpp /Fe:pilot.exe`로 빌드한다. 새 관리자 승인과 검증된 바이너리 해시가 필요한 일회용 시험 도구다. 표준 계정의 고정 PowerShell 소켓 검사만 실행하며 결과 파일은 실행 파일 옆에 새로 생성한다. 이번 시험의 정상·실패 후 정리는 확인했다. 강제 중단 후 자동 복구는 구현하지 않았다. 잔존 상태가 보이면 재실행이 차단되며, 소유 작업 종료를 확인한 뒤 원래 공급자·하위 계층과 필터 GUID 범위만 관리자 검토로 회수한다. 공급자 `54b49f93-eb64-4c60-874b-87dc274cc601`, 하위 계층 끝 바이트 `02`, 필터 끝 바이트 `10`~`1f`다. 이 결과로 온라인 입장을 승인하지 않는다.

P2.116에서 `native-egress-preflight`로 UAC 토큰을 조회했다. 기존 P2.115의 파일·루프백 진단을 재실행할 필요는 없다. [네이티브 시험 변경 범위와 복구 계획](ARCHITECTURE-ROADMAP.md#p2116-네이티브-시험-정책과-복구-범위)에 대해 별도 승인을 받은 뒤 정책 설치기와 독립 검증을 진행한다. 외부 제공자 연결과 모델 요청은 그 시험에 포함되지 않는다.

`native-command-diagnostic --archive <공식 0.167.1 Windows x64 CLI ZIP의 절대 경로> --powershell-sha256 <SHA256>`은 새 합성 홈과 Controller 소유 Job에서 고정 명령의 파일·루프백 경계를 검증한다. 배포 ZIP과 CLI를 각각 확인하며 모델·계정·스레드 RPC는 허용하지 않는다. 다음 네이티브 입장 검증은 호스트의 제공자 전용 송신을 독립적으로 집행한 후, 별도 승인으로 모델의 쓰기·빌드와 Verifier 영수증을 연결하는 것이다. 이 진단으로 온라인 입장 권한을 부여할 수 없다.

P2.115부터 같은 진단에서 `direct_host_controls`는 소유 PowerShell의 IPv4·IPv6 양성 대조이며 `loopback`과 `loopback_ipv6`는 MXC 명령의 거부 결과다. `proxy_contacted=false`는 고정 TCP 대조가 프록시를 경유하지 않았다는 뜻이다. 실제 Codex 호스트의 HTTP 경로와 외부 DNS는 미검증이다. 다음 시험은 전용 송신 정책을 갖춘 실행 환경에서만 준비하고, 허용된 연결과 거부된 연결을 같은 실행 신원으로 비교한다.

`manager-mxc-offline`의 `--loopback-only --managed-loopback`은 합성 리스너로 관리 네트워크의 직접 루프백을 측정한다. `--command-boundary`는 같은 파일 권한 프로필을 직접 MXC와 오프라인 MXC 안의 App Server `command/exec`에서 비교한다. 시험용 저장소의 서명·해시 고정 후보를 사용한다. 결과 원장은 온라인 실행 승인의 근거로 사용하지 않는다.

개발 시작의 **완료 기준** 탭에서 각 기준 ID를 확인하고 검증 명세의 관측과 연결한다. CLI는 `subscription-session --criterion-checks '{"C1-<해시>":[{"input":1}]}'`을 받는다. 배치 검증에서는 `file`과 `input`으로 모호한 관측을 구분한다. 연결하지 않았거나 유일하게 식별할 수 없는 기준은 미검증이다. SDK 콘솔 실행의 이름별 `PASS` 출력은 실행 코드와 채널을 공유하므로 독립 완료 증거로 사용하지 않는다. `--max-stagnant`의 기본값은 2이며 새 기준 충족과 기존 기준 퇴행은 별도로 표시한다. 재계획도 승인된 재작업 한도를 사용한다.

구독 개발 시작 화면에서 권한과 시간 또는 토큰 한도를 설정한다. 생략한 시간·토큰 항목에는 등록 예산과 백엔드 상한을 적용하고 승인 화면에 실제 한도를 표시한다. 사이클 상한은 선택 사항이다. CLI의 `subscription-session --permission ID --cycles N`은 반복 가능한 권한 선택과 선택적 사이클 한도를 받는다. 권한 옵션을 생략한 기존 CLI 호출은 승인 해시에 해당 실행에 필요한 권한을 표시한다. 실행 중 철회는 새 역할을 막고 현재 작업을 회수하며, 새 실행에는 새 검토 해시가 필요하다. 동적 권한 확대와 기존 세션 재개는 미연결이다.

WinForms 최종 판정이 증거 부족으로 `REWORK_REQUIRED`인 경우 `subscription-reverify-winforms`로 같은 변경을 추가 검증할 수 있다. 새 시나리오와 승인 해시를 사용하며 이전 Developer·Reviewer 영수증, 원본 기준, 프로젝트 구성과 도구 신원을 고정한다. 새 Verifier와 최종 Manager가 완료되면 기존 `original-apply` 별도 승인으로 시험 원본에 반영할 수 있다.

P2.109의 `mxc-dotnet-winforms-project-v1`은 패키지 없는 WinForms SDK 프로젝트를 MXC 안에서 빌드·실행한다. 기존 SDK 경로의 `runtime.tests` 대신 `runtime.scenario`에 소유 컨트롤 매핑과 2~3개의 기대 장면을 지정한다. 앱의 동결된 시작 코드에서 `DEVORCHESTRA_GUI_MANIFEST`에 현재 PID와 `root` 및 컨트롤 HWND를 JSON으로 게시해야 한다. Controller는 현재 Job 소속, 프로세스 핸들, 창 계층을 확인한 뒤 승인된 메시지만 보낸다. 같은 복제본·시나리오·툴체인의 사전 빌드 영수증이 있으면 구독 승인에 변경 전 캡처를 연결한다. WinForms SDK 모드는 검증 센터에서 검토할 수 있으며 결과 이미지는 GUI 검증 보고서에서 확인한다.

검증 범위는 클릭·입력·리스트 스크롤·선택 확인·크기 변경·상태와 창 제목 확인이다. SDK 프로젝트에는 제한된 로컬 참조와 명시적 텍스트 내장 리소스를 허용한다. WPF, 임의 창 자동 탐색, 체크박스·모달·포커스 시나리오, 외부 패키지 및 모델의 프로젝트 구성·리소스 편집은 지원 대상에 포함되지 않는다.

P2.108의 오프라인 SDK 어댑터는 **검증 센터 → 복제본 빌드·테스트 → SDK 프로젝트**에서 검토한다. 테스트 진입점에 `tests/Checks.csproj`, 기대 이름에 `["case-name"]` 형식의 배열을 입력한다. 해당 프로젝트는 종료 코드 0과 이름별 `PASS case-name` 출력이 필요한 콘솔 테스트 실행 파일이다. CLI는 `build-verify --kind mxc-dotnet-sdk-project-v1 --source tests/Checks.csproj --vectors '["case-name"]'`와 기존 프로젝트·실행기·예산·승인 옵션을 사용한다. 승인된 소스만 격리 스테이징에 복사하고 실제 SDK 복원·Release 빌드·테스트를 순서대로 실행한다. 패키지 소스는 비어 있으며 사용자 설정과 별도의 임시 프로필을 사용한다.

구독 세션의 배치 검증 명세에서 `runtime`은 `{"kind":"mxc-dotnet-sdk-project-v1","launcher":"<절대 Codex 경로>","project":"tests/Checks.csproj","tests":["case-name"]}` 형식이다. `files`는 승인된 라이브러리 C# 경로별로 기존 완료 기준 배열을 지정하고 `create`에는 별도 승인된 새 파일만 지정한다. 테스트와 프로젝트 구성은 모델 수정 범위 밖에 고정한다. P2.107의 격리 Verifier 영수증과 원본 승인 절차를 재사용한다. 패키지 의존성, 사용자 타깃·import, 기존 테스트 프레임워크 어댑터, 새 디렉터리, 파일 삭제·이동은 별도 작업이다.

P2.107은 승인된 하위 경로의 Python/C# 배치와 검증 센터의 **원본 반영 검토**를 연결한다. CLI는 `original-apply --project ID --expires UNIX_SECONDS`로 diff와 승인 해시를 검토하고, 동일 인자에 `--approve HASH`를 추가해 한 번 반영한다. 원본 Git 및 작업 트리, 온보딩 기준, 동일 세션의 리뷰·격리 런타임·최종 판정, 변경 바이트가 일치해야 한다. 중단된 intent와 로컬 DPAPI 복구 자료를 보존하며 자동 재실행하지 않는다. 삭제·이동, 새 디렉터리, 임의 MSBuild와 범용 Python 패키지 실행은 지원 범위 밖이다.

P2.105 Command Center의 일곱 작업 공간은 기존 Controller 메서드를 호출한다. `ui_design.py`가 토큰과 버튼 스킨을 제공하고, 정적 폰트·아이콘과 고지는 EXE 패키지에 포함된다. 프레임워크 교체·모든 모달 리디자인·OS 수준 접근성 인증은 별도 작업이다.

P2.104는 기존 Git 문맥 패널의 **환경·빌드 검토**에서 복제본 C# 파일 두 개와 기대 벡터를 승인한다. 같은 기능의 CLI는 \`build-verify --kind mxc-dotnet-onboarded-v1\`이다. MSBuild·패키지 복원·프로젝트 스크립트는 이 좁은 실행 범위 밖이다. \`replica-changes --project <ID>\`는 원본 파일과 독립 이력 복제본을 읽기 전용으로 비교한다. 승인 전 준비 상태와 실측 결과를 구분하고 기존 미확정 스테이징은 보존한다.

P2.103은 등록부의 원본을 기준으로 `onboard-git --project <ID> --destination <NEW_PATH>`를 검토하고 동일 인자에 `--approve <SHA256>`를 추가해 새 복제본을 만든다. 필요한 비식별 로컬 예제는 정확한 `--include` 경로로 선택한다. 등록·문맥·복제본 상태와 환경·개발 입장을 구분한다. 기존 개인 지침 승인 중 다른 복제본에 묶인 것은 자동 승계하지 않는다. 중단된 온보딩의 원장과 소유 스테이징을 보존하고 자동 재시도하지 않는다.

P2.102의 `sdk-install`은 루트 `global.json`의 정확한 안정 버전과 `rollForward: disable`을 요구한다. Microsoft HTTPS 메타데이터 및 ZIP만 읽고 설치 스크립트 없이 풀며 MXC에서 `dotnet --info`를 확인한다. 성공 영수증에 있는 프로젝트 SDK만 이후 검토에서 재사용한다. 구독 C# 세션은 `mxc-dotnet-arithmetic-v1`과 두 기존 산술 파일에 한정하며, 변경된 소스와 DLL 해시를 같은 Verifier 영수증에 기록한다.

P2.101의 `environment-review`는 설치나 실행을 시작하지 않는다. .NET의 경우 로컬 SDK 선택 근거와 해시를 확인한 뒤 기존 `build-verify`를 검토·승인한다. `global.json`의 `disable`, `patch`, `latestPatch`, `latestFeature`, `latestMinor`, `latestMajor`를 지원한다. 자동 설치를 연결하려면 승인된 공식 다운로드 목적지, 아카이브 무결성, 프로젝트 전용 설치 경계, 설치 취소·회수 검증이 필요하다. 모델 변경 소스 실행의 경계는 기존 검토된 두 파일 경로와 별개다.

빌드 Verifier의 미확정 실행 의도는 소유 폴더를 보호하며 재시도 시 자동 삭제하지 않는다. `artifact-review --retain 2`는 로컬 릴리스 및 기존 임시 자료를 검토용으로 분류한다. 최종 배포본·원장·복구·공유 도구의 삭제는 이 경로에 포함되지 않는다.

P2.100은 `build-verify --kind mxc-dotnet-console-v1`로 복제본의 검토된 C# 소스 두 개와 정수 벡터를 승인한다. 재현용 소스 바이트는 `mxc_dotnet.py`의 `PROGRAM`과 `CALCULATION`이며, 기대 벡터는 `[[-7,-14],[9,18]]`이다. 승인 검토와 실행은 같은 인자 및 만료 시각을 사용하고 실행에만 `--approve` 해시를 전달한다. .NET 7은 지원이 종료된 이번 PC의 실측 대상이다. 다음에는 지원 중인 .NET 버전과 변경 소스에 필요한 경계를 검증한 뒤 구독 역할 인계와 C# GUI를 연결한다.

P2.99는 `subscription-session`의 벡터 명세에서 `mxc-python-modules-v1`을 선택한다. 실제 구독 세션과 MXC에서 승인된 두 파일의 import·직접 호출을 확인했다. 기존 런타임 승인 해시는 고정 하네스 변경을 반영해 다시 검토한다. 실제 FAIL의 관측값과 영수증을 재작업에 전달하며, 실구독 성공 세션에는 재작업이 없었다. 프로젝트 표준 라이브러리 import, 패키지 초기화, 플러그인과 외부 빌드 도구는 도구별 격리·동작 실측 전까지 차단한다.

P2.98은 고정 매니페스트의 MXC CPython으로 실제 다중 파일 빌드·함수 관측을 동일 세션에 연결했다. 프로젝트 빌드 스크립트, 임의 생성 코드/EXE 실행 및 범용 GUI 통합은 이 증거에서 승격하지 않는다. 다음에는 도구별 파일·네트워크·프로세스 경계를 별도로 검증한다.

P2.97은 기존 Python 파일 수정과 명시된 새 파일 생성을 한 승인 배치로 처리한다. 복구 저널이 남고 실행이 미확정이면 파일과 영수증을 먼저 대조한다. 데이터 전용 GUI의 실제 실패 관측은 동일한 작업의 재작업에 사용한다. 일반 생성 코드 실행에는 별도 파일·프로세스·네트워크 경계가 필요하다.

P2.96은 변경한 폼의 데이터를 고정 호스트의 EXE에 내장하고 통합 세션 Verifier에서 소유 창으로 검증한다. 같은 작업의 EXE·변경·시나리오 해시와 로컬 이미지를 연결한다. 일반 생성 코드 빌드와 실행은 이 데이터 전용 계약 밖이다.

P2.95는 단일 승인 후 실제 Reviewer 반려와 Developer 재작업을 거쳐 통합 세션을 완료했다. 구독 모델의 사용량은 제공자 보고를 합산하며, 미리보기의 제공자 측 강제 토큰 상한은 제공되지 않는다. 다음 단계는 GUI 영수증을 동일 승인·작업에 안전하게 결속하는 것이다.

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
