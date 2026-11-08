# P2.5 격리 실행 환경과 인수 시험

## P2.30 사용자 세션의 설치 파일 접근 확인

기준 `64f786ae8a20390461bff5511f62d13cc54180e2`. 사용자가 직접 연 Windows PowerShell에서는 다운로드 폴더가 발견되지 않았다. Codex의 기본 Windows 셸은 패키지별 `LOCALAPPDATA`를 사용했고, 해당 변수로 구성한 경로에서도 파일이 발견되지 않았다. 같은 셸에 사용자가 알려준 절대 경로를 지정하면 EXE가 보였다. 별도의 승인된 읽기 전용 명령에서도 같은 절대 경로의 EXE와 SHA256SUMS가 보였고, EXE 크기와 SHA-256은 아래 기준에 일치했다.

확인된 차이는 기본 Codex 셸의 환경 변수 값이다. 동일한 절대 경로를 바라본 Codex 도구와 사용자 세션의 관측 차이, 실제 파일 저장 위치 및 P2.29 실행 오류의 원인은 미확정이다. 사용자 세션에서 접근 가능한 파일을 확인할 때까지 P2.28 호스트 다운로드 상태는 `HOST_FILE_NOT_VERIFIED`다. 기존 무결성 결과는 Codex 도구가 읽은 바이트에만 적용된다.

사용자가 직접 연 일반 PowerShell에서 탐색기의 **경로로 복사**로 얻은 절대 EXE 경로를 아래에 입력한다. 존재·크기·SHA-256만 읽고 경로는 출력하지 않는다. 파일이 없다면 그 결과를 그대로 기록한다.

```powershell
$p = (Read-Host 'VirtualBox EXE absolute path').Trim().Trim('"')
if ($p -notmatch '^[A-Za-z]:\\') { throw 'ABSOLUTE_LOCAL_PATH_REQUIRED' }
if (Test-Path -LiteralPath $p -PathType Leaf -ErrorAction Stop) {
    $file = Get-Item -LiteralPath $p -ErrorAction Stop
    $hash = (Get-FileHash -LiteralPath $p -Algorithm SHA256 -ErrorAction Stop).Hash
    [pscustomobject]@{ Exists = $true; Bytes = $file.Length; SHA256 = $hash }
} else {
    [pscustomobject]@{ Exists = $false }
}
```

이번 확인에서 다시 다운로드하거나 설치기를 실행하지 않았다. 수동 설치 및 P2 A/B/C·P3는 BLOCKED다.

## P2.28 승인된 Oracle 패키지 다운로드 및 검증

**정정** 사용자 세션에서의 다운로드 상태는 `HOST_FILE_NOT_VERIFIED`다. 아래는 기존 Codex 도구 세션 기록이다. [P2.30 재확인](#p230-사용자-세션의-설치-파일-접근-확인)

기준 `67a447b8af3662652623ec590f4125b9bf141edc`. 다운로드 직전 Oracle의 안정 채널은 7.2.22를 가리켰으며, 10월 보안 공지에는 7.2.16과 7.2.20이 영향 버전으로 기록돼 있다. 공식 7.2.22 디렉터리에서 별도 승인된 Windows x64 기본 설치 파일과 SHA256SUMS 두 개만 저장소 밖 DevOrchestra-Lab 다운로드 폴더에 저장했다. 기존 파일을 덮어쓰지 않았다. [Oracle 안정 채널](https://download.oracle.com/virtualbox/LATEST-STABLE.TXT) · [배포 인덱스](https://download.oracle.com/virtualbox/7.2.22/) · [Oracle 10월 보안 공지](https://www.oracle.com/security-alerts/cpuoct2026.html)

| 항목 | 검증 결과 |
| --- | --- |
| 설치 파일 | VirtualBox-7.2.22-175245-Win.exe |
| 파일 및 제품 버전 | 7.2.22.175245 |
| 설치 파일 크기 | 178101352 bytes |
| SHA256SUMS 크기 | 2959 bytes |
| SHA-256 | `afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f` |
| Authenticode | 캐시 전용 Windows 신뢰 검증 유효, 서명자 Oracle America, Inc. |

공식 목록과 해시가 일치해 패키지 무결성 PASS다. 캐시 전용 서명 검증은 실시간 인증서 폐기 조회를 포함하지 않는다. 두 파일은 로컬에 보존했다. 설치 호환성, 드라이버 로드와 P2 격리는 별도다.

다음 승인 후보는 지정 유지보수 시간에 `VBoxApplication` 본체와 필수 서명 가상화 드라이버만 설치하는 범위다. 관리자 작업과 드라이버·서비스 변경을 명시한다. 네트워크 변경·재부팅 요구가 나타나면 별도 승인 전 중단한다. 게스트 이미지·라이선스, VM 생성·부팅과 A/B/C 시험도 별도 승인이다. 이번에는 EXE 실행, 관리자 요청, 시스템 변경 또는 VM 생성·실행을 수행하지 않았다. P2 A/B/C와 P3는 BLOCKED다.

## P2.29 승인된 최소 설치 시도

기준 `4ce9d798fb8232930dfa242f4fbed4eeef86ed08`. 실행 직전 P2.28의 SHA-256, 크기 178101352 bytes, 파일 버전 7.2.22.175245 및 유효한 Oracle America, Inc. Authenticode 서명을 재확인했다.

대화형 설치 요청에 `ADDLOCAL=VBoxApplication`, `VBOX_START=0`, `REBOOT=ReallySuppress`, `MSIRESTARTMANAGERCONTROL=Disable`을 전달하고 바탕 화면·빠른 실행 바로가기와 파일 연결을 껐다. Oracle의 최소 구성은 USB, 브리지, Host-only, Python 기능을 제외한다. [Oracle 설치 옵션](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html) · [Microsoft Restart Manager 제어](https://learn.microsoft.com/en-us/windows/win32/msi/msirestartmanagercontrol)

두 실행 요청은 Windows 경로 오류로 설치기 핸들을 반환하지 않았다. 두 번째 요청은 설치 파일의 폴더를 작업 디렉터리로 명시했다. 설치 프로그램 종료 코드 및 재부팅 요구는 NOT VERIFIED다. 이후 기본 설치 위치의 본체와 지정 VBoxSup·USB·네트워크 드라이버 서비스 등록 항목을 읽기 전용으로 확인했으나 발견되지 않았다. 이 조회는 시스템 전체 변경이나 롤백의 증거로 사용하지 않는다.

설치는 BLOCKED다. 다음 단계는 사용자가 직접 제어하는 Windows 세션에서 같은 최소 구성의 설치 마법사를 열고, 범위 밖 요구가 나타나면 중단하는 것이다. VM 생성·부팅 및 게스트 이미지는 별도 승인이다. P2 A/B/C 및 P3는 BLOCKED다.

## P2.26 격리 백엔드 결정

기준 `416d041b8d95dd1b28b91b93d9cba7920ba47d95`. **주력은 호스트 통합 기능을 끈 전용 VirtualBox Windows x64 게스트, 대체 후보는 명시적 네이티브 MXC**다. 주력 선정은 구현·인수 순서를 정한 것이다. 현재 두 후보 모두 실제 개발에는 BLOCKED이고 자동 전환은 없다. 이전 MXC 우선순위는 이력으로 유지한다.

### MXC 기술 판정

OpenAI는 지원 환경에서 MXC를 권장하며, 기본 시작 확인에는 `:workspace`를 사용한다. 이는 작업 공간 쓰기를 제공하지만 DevOrchestra의 외부 읽기 차단 정책으로 그대로 사용할 수 없다. 공식적으로 사용자 정의 독립 프로필과 `:minimal` 읽기가 지원된다. 고정 태그의 구현은 제한된 정책에서 `:minimal`이 활성화되면 Windows, Program Files 계열과 ProgramData를 플랫폼 읽기 후보로 추가한다. 명시적 읽기 허용은 별도의 규칙이며, 이 태그에는 플랫폼 기본 루트 전체가 사용자별 DLL 목록으로 바뀌는 보장이 없다. 공식 사용에서 모든 DLL 경로를 직접 적어야 한다는 요구는 확인되지 않았다. 반면 DevOrchestra는 플랫폼 예외가 보호 경로와 겹치지 않는다는 증거, 프로세스·데스크톱 비간섭 및 소유 트리 회수를 별도로 요구한다. P2.25의 EXE만 읽는 초안은 이 구현에서 실행 검증되지 않았고, 그 차단 판정은 유지한다. [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox) · [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions) · [고정 태그 MXC 플랫폼 경로](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/windows.rs)

고정 태그는 런타임 호환성을 위해 Win32k와 데스크톱 핸들을 사용할 수 있게 하며 일부 UI 제한을 적용한다. 이 계약만으로 다른 프로세스 탐지·창 접근·입력·화면 금지 전체를 입증할 수 없다. P2.10의 cmd.exe 시작 PASS, P2.15의 Python 실패, P2.18/P2.23의 네이티브 실패와 내부 단계 UNKNOWN은 유지한다. 알파 빌드 결함, MXC 준비, 이미지·로더 및 생성 실패 중 원인은 미확정이다. [고정 태그 MXC 계약](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/README.md)

확인 시 공식 GitHub latest 안정 릴리스는 **0.167.0**이었다. 릴리스 노트는 읽기 전용 볼륨 루트 확장 생략(#57985)과 deny 경로의 누락 부모 처리(#58168)를 열거한다. 고정 알파 태그의 정책 소스에도 두 처리 경로가 보인다. 이 항목들은 설치 바이너리에서의 P2.23 원인 또는 안정판 교체 효과를 입증하지 않는다. 배포에서는 현재 알파의 경로·해시가 영구 요구가 되지 않도록 백엔드별 지원 버전·서명·정책을 별도 확인하고, 새 조합은 다시 인수한다. 이번에 업데이트하지 않았다. [공식 0.167.0 릴리스](https://github.com/openai/codex/releases/tag/rust-v0.167.0) · [고정 태그 정책 소스](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/policy.rs)

### Windows 11 Home x64 후보 판정

| 후보 | 판정 | 비간섭·데이터·네트워크·종료 | 호스트 영향·배포·부담 |
| --- | --- | --- | --- |
| 전용 VirtualBox Windows 게스트 | **주력 · 조건부 후보** | 독립 커널과 데스크톱이 호스트 프로세스·창·입력·화면에 대한 직접 경로를 분리한다. NIC·호스트 공유·클립보드·드래그앤드롭·USB/장치 전달·원격 콘솔을 끄고 합성 복제본만 전달한다. 게스트 표준 사용자·ACL, 게스트 Job 및 소유 VM 종료·검역은 별도 인수한다. | x64 호스트 후보이나 펌웨어, 드라이버, 하이퍼바이저 공존, 자원 여유가 필요하다. RAM·CPU·디스크와 드라이버가 게임 환경에 미치는 영향은 미측정이다. 전용 테스트 PC를 우선하고 포터블 Controller는 VM 설치와 분리한다. |
| 명시적 MXC | **대체 · 조건부 후보** | 파일·네트워크는 유효 프로필을 실측해야 한다. 공유 호스트 커널에서 요구하는 프로세스·데스크톱 비간섭과 외부 Job 결합은 미검증이다. | 별도 VM보다 배포 부담이 작고 지원 호스트에서 관리자 초기화가 요구되지 않는 공식 방식이다. 현재 고정 CLI의 사용자 이미지 실패를 해결하거나 더 새 버전·프로필을 고정해 다시 인수해야 한다. |
| 제한 Windows 사용자 및 AppContainer | **BLOCKED** | 별도 계정만으로 같은 호스트의 프로세스·창·입력·파일·네트워크를 모두 차단할 수 없다. AppContainer는 Windows 보안 경계지만, Codex와 모든 자식에 필요한 토큰·ACL·capability·세션·Job 구성과 금지 경로를 DevOrchestra에서 검증하지 않았다. | 계정·ACL·패키징 변경은 별도 승인이다. 현재 조합의 실행 호환성과 배포 재현성이 미확인이다. |
| Windows Sandbox·Hyper-V 역할 | **현재 PC BLOCKED** | 지원 에디션에서는 별도 VM 경계 후보다. 통합 기능, 종료·반출, 프로젝트·자격증명 경계는 별도 인수가 필요하다. | Windows Home은 Windows Sandbox와 Hyper-V 역할을 지원하지 않는다. 기존 Microsoft 하이퍼바이저 관측을 역할 설치 가능성으로 해석하지 않는다. |
| WSL 2 | **이번 Windows 개발 백엔드 BLOCKED** | Linux 실행 환경 후보이나 Windows 실행 도구의 동등한 대체가 아니며, 자동 마운트·Windows 실행 상호운용 등 호스트 연결을 별도로 차단·검증해야 한다. | 설치·구성이 필요한 경우 별도 승인이다. 현재 Windows 네이티브 프로젝트 인수 경로에는 포함하지 않는다. |

후보 수준의 PASS는 없다. VM도 하이퍼바이저·호스트 통합·게스트 탈출 위험과 자원 경합이 남는다. Job Object·선언 기반 검사·CLI 종료만으로 파일·네트워크·데스크톱·트리 격리를 확정하지 않는다. [Oracle 호스트·게스트 지원](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/Introduction.html) · [Oracle 통합 기능](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/working-with-vms.html) · [Microsoft AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation) · [Windows Sandbox 지원](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/) · [Hyper-V 설치](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v) · [WSL 설정](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)

### 현재 PC와 다음 단일 승인

현재 PC에서 기본 경로의 VBoxManage는 발견되지 않았다. 사용자 지정 설치는 미확인이다. P2.6의 AMD-V·Microsoft 하이퍼바이저 관측은 이력이며 WHP 활성·호환성과 현재 자원 여유는 확인하지 않았다. 이 PC에 새로 설치하려면 공식 서명 패키지·관리자 승인·핵심 호스트 드라이버 설치가 필요하다. 재부팅 필요 여부는 미확정이며 별도 승인 대상이다. Microsoft 하이퍼바이저를 유지한 조합의 WHP 가용성과 성능을 확인하고 VBS/Hyper-V/보안 설정을 끄는 우회는 사용하지 않는다. 기존 P2.6 자원 상한과 호스트 여유 기준을 시작 전에 대조한다. [P2.6 사전 점검](#p26-현재-pc-도입-사전-점검) · [Oracle 설치](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html) · [Oracle Hyper-V 공존](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/AdvancedTopics.html)

**다음 단 하나의 승인 후보는 준비된 전용 VM의 A 정상 수명주기 시험 1회**다. 사전 확보한 VM UUID와 이미지·구성 해시, NIC·통합 비활성, 자원 여유를 확인한 뒤, 단일 슬롯 아래 읽기 전용 합성 입력으로 지정 VM을 헤드리스로 한 번 시작한다. 고정 게스트 작업의 정상 종료와 같은 UUID의 poweroff 및 해당 소유 실행 핸들의 종료를 확인한다. 다른 VM·프로세스·창을 열거하거나 제어하지 않는다. 소유권·증거·종료가 불명확하거나 기한 초과면 중단하고 차단한다. 이 승인은 VM/이미지 확보, 다운로드, 설치, 드라이버, 재부팅 또는 B/C 시험을 포함하지 않는다. 준비된 환경이 없으면 시험은 BLOCKED다.

A 정상 경계 다음에는 별도 승인으로 A 취소·장애 복구, B 합성 파일·프로필·자격증명·네트워크·데스크톱 비간섭, C 호스트 원장 보호와 종료 후 검역을 증명한다. 이후 최소 한 역할에서 승인된 App Server·모델 송신과 실제 Verifier 영수증을 확인한 뒤 순차 실행·검토·커밋을 연결한다. 호스트 인증정보를 게스트로 전달하지 않고 모델 연결은 별도 제한 브로커와 사용자 승인 범위를 사용한다.

Controller는 Manager → Developer → Reviewer → Verifier → Manager, 전역 단일 슬롯, 승인 복제본, 예산·감사·종료 예약·이어하기·검증 기반 커밋을 소유한다. 백엔드는 버전이 고정된 시작·취소·소유 자원 회수·격리 및 검역 영수증을 반환한다. 다른 PC 초기 설정에서는 Windows 에디션/아키텍처·빌드, 가상화·WHP·보안 정책, 서명된 백엔드/이미지·CLI 버전과 해시, 자원 여유, 통합 기능·네트워크·복제본·원장 경계를 확인한다. 서명 Windows x64 포터블 배포와 초기 설정은 유지하되 설정 완료로 실연동을 열지 않는다.

기존 Windows 합성 회귀 87건 및 PowerShell 독립 프로필 정적 검사 PASS. 신규 MXC·VM·Codex 호출, 설치 및 시스템 변경은 없었다. P2 A/B/C 및 P3는 BLOCKED다.

P2.7에서 [현재 PC의 네이티브 MXC](P2-MXC-VALIDATION.md)를 우선 경로로 선정했다. 이 문서의 VirtualBox 다운로드·설치·VM 승인안은 보류한 이력이다.

## P2.6 현재 PC 도입 사전 점검

소유자는 현재 Windows PC에 도입하는 방향을 선택했다. 아래 항목은 P2.5 전용 PC 선정 이후의 우선 경로를 기록한다. Windows Core 25H2 x64와 AMD-V 지원 CPU는 Oracle Windows x64 호스트 후보에 해당한다. Windows의 펌웨어 가상화 API가 활성 상태를 보고했고 CPUID에 실행 중인 Microsoft 하이퍼바이저가 보였다. 현재 실행 문맥에서 직접 SVM/NPT 및 SLAT 표식은 노출되지 않았다. 이 결과만으로 물리 CPU의 지원 여부를 부정할 수 없다. 메모리 무결성(HVCI)의 레지스트리 설정은 활성 값이었다. Device Guard 및 선택적 기능 CIM 조회, WHP 기능 조회는 접근 제한으로 실제 VBS/HVCI 서비스 실행과 WHP 활성 여부를 확정하지 못했다. VirtualBox는 PATH, 기본 설치 위치, Oracle의 두 레지스트리 뷰에서 발견되지 않았다. 사용자 지정 설치 여부는 미확인이다. [AMD CPU 사양](https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-7-7800x3d.html) · [Microsoft CPUID 탐색](https://learn.microsoft.com/en-us/virtualization/hyper-v-on-windows/tlfs/feature-discovery) · [WHP 조회](https://learn.microsoft.com/en-us/virtualization/api/hypervisor-platform/funcs/whvgetcapability) · [메모리 무결성](https://learn.microsoft.com/en-us/windows/security/hardware-security/enable-virtualization-based-protection-of-code-integrity)

Oracle 7.2 문서의 Microsoft 하이퍼바이저 백엔드는 WHP가 실행 중일 때 사용되며 성능 저하 가능성이 있다. Microsoft도 공존 시 부팅 실패나 느린 모드를 경고한다. 현재 하이퍼바이저와 보안 설정을 유지한 조합으로만 첫 시험을 계획한다. WHP 비활성, 서명 드라이버 로드 거부, 성능 저하, 부팅 실패가 확인되면 중지하고 별도 결정한다. Windows Home의 Hyper-V 역할 또는 Sandbox를 먼저 설치할 필요는 없다. `HypervisorPlatform`(WHP)과 `VirtualMachinePlatform`, Hyper-V 역할은 서로 별개다. [Oracle Hyper-V 공존](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/AdvancedTopics.html) · [Microsoft 공존 제약](https://learn.microsoft.com/en-gb/troubleshoot/windows-client/application-management/virtualization-apps-not-work-with-hyper-v)

### 최초 설치와 부팅 제안

- Oracle이 게시한 후보는 Windows x64 기본 패키지 `VirtualBox-7.2.22-175245-Win.exe`다. 별도 다운로드 승인이 있으면 공식 SHA-256과 Authenticode 서명·서명자·버전을 확인한다. 설치 직전 승인 패키지의 지원·보안 공지를 다시 확인한다. [Oracle 배포 안내](https://www.oracle.com/virtualization/technologies/vm/downloads/virtualbox-downloads.html) · [해당 버전 배포 인덱스](https://download.oracle.com/virtualbox/7.2.22/)
- 제안하는 최소 MSI 기능은 `ADDLOCAL=VBoxApplication`이다. Oracle이 필수로 지정한 본체와 Windows 호스트 가상화 커널 지원 드라이버가 설치 범위다. 정확한 서명 카탈로그·INF·서비스 목록은 승인 후 해당 패키지에서 확인한다. 핵심 드라이버를 수동 설치하거나 서명 경고를 우회하지 않는다. `VBoxUSB`, `VBoxNetwork` 및 하위 `VBoxNetworkFlt`(브리지 필터)·`VBoxNetworkAdp`(Host-only 어댑터), `VBoxPython`은 제외한다. 파일 연결·바로가기·자동 실행도 승인 범위에서 제외한다. Extension Pack과 Guest Additions는 최초 시험에 포함하지 않는다. [Oracle Windows 설치 옵션](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html)
- 브리지 필터의 바인딩·Host-only 어댑터 생성은 호스트 네트워크 상태와 연결에 영향을 줄 수 있다. 해당 기능을 제외해 이 경로를 줄이지만 기본 설치의 영향과 재부팅 여부는 실측 전 미확인이다. 핵심 드라이버 설치는 관리자 승인과 유지보수 시간이 필요하다. 설치 과정에서 네트워크 구성이나 재부팅이 추가로 요구되면 중지한다. [Oracle 네트워크 구성](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/networkingdetails.html) · [Oracle 설치 문제 해결](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/Troubleshooting.html)
- 최초 단독 부팅의 제안치는 **2 vCPU, CPU 실행 캡 50%, 게스트 RAM 4 GiB, OS 디스크 64 GiB 고정, 반출 디스크 16 MiB 고정, 시험 20분**이다. 4 GiB는 Windows 11 최소 사양이다. GUI 시험용 6 GiB는 별도 승인과 여유 확인 후 검토한다. 시작 직전 호스트 가용 물리 RAM 12 GiB, 커밋 여유 16 GiB, 대상 볼륨 여유 최소 100 GiB와 이미지·복제·로그의 실제 공간 합계를 확인한다. 이 숫자는 보수적인 운용 기준으로 벤더 보장값이 아니다. 관측 시 디스크는 예비 기준을 충족했으나 RAM과 커밋 여유는 모두 시작 기준에 못 미쳤다. 다른 프로그램을 조작해 여유를 확보하지 않는다. [Windows 11 최소 사양](https://www.microsoft.com/en-us/windows/windows-11-specifications) · [Windows 메모리 조회](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-globalmemorystatusex)
- VM은 새 합성 전용 저장 루트에 생성한다. 현재 계획은 모든 NIC `none`, 공유 폴더·클립보드·드래그앤드롭·USB 전달·3D 가속·원격 콘솔 끔이다. 라이선스가 승인된 사전 준비 Windows x64 이미지를 우선한다. 개인용 Windows 11 Home/Pro의 초기 설정은 인터넷 및 계정을 요구할 수 있어 현재 무네트워크 시험에서 해결하지 않는다. 자격증명 없는 승인 이미지가 없으면 게스트 준비를 차단한다. [Windows 초기 설정 요구사항](https://www.microsoft.com/en-us/windows/windows-11-specifications)
- 최초 설치와 부팅은 사용자가 정한 유지보수 시간에 수행하는 안이다. 커널 드라이버 공존 및 자원 경합은 일반 기술 위험이며 게임·안티치트 호환성과 제재 위험은 확인되지 않았다. 앱이나 게임의 상태를 조사하거나 제어해 판단하지 않는다. 최초 시험에 동시 게임 사용을 포함하지 않는다.

### 승인 순서와 현재 PC 인수

1. **다운로드만** 승인한다. Oracle 기본 패키지와 공식 SHA-256 목록을 저장소 밖 `%LOCALAPPDATA%\DevOrchestra-Lab\downloads`에 저장하는 안이다. Windows 게스트 이미지·라이선스·설치 자료는 별도 승인이다.
2. **기본 설치와 커널 드라이버**를 승인한다. 대상 버전·호스트·유지보수 시간, `VBoxApplication` 범위, 관리자 작업, 설치 로그를 특정한다. BIOS/UEFI·Hyper-V/VBS·WHP·Windows 기능·네트워크 설정과 재부팅은 각각 필요성이 확인된 뒤 별도로 승인한다. 기존 앱·게임 제어는 이 승인에 포함하지 않는다.
3. **VM 생성·시작과 종료 시험**을 승인한다. 저장 루트는 `%LOCALAPPDATA%\DevOrchestra-Lab\vm`, 검역은 그 밖의 `%LOCALAPPDATA%\DevOrchestra-Lab\quarantine`을 제안한다. 경로·게스트 이미지 해시·소유 VM 및 매체 UUID·자원 한도·시작 전 여유를 확인한다. 단독 합성 부팅으로 실제 백엔드를 관측한 뒤 A에서 정상·시간초과·강제 종료·실행기 장애·중복 입장을 순차 확인한다. 소유 자원 종료가 불명확하면 B/C로 넘기지 않는다.
4. **B 양성 대조 및 차단**을 실시한다. 게스트 표준 계정의 허용 파일·가짜 프로필·토큰으로 파일·자격증명·통합 대조를 수행한다. 네트워크 없는 기준 VM은 그대로 유지한다. DNS/TCP의 별도 격리 양성 대조망은 네트워크 설정 변경과 합성 수신기 범위를 별도로 승인한 경우에만 수행한다. 대조망이 없으면 B 네트워크를 BLOCKED로 유지한다.
5. **C 오프라인 반출**을 승인한다. 전원 종료 증거 뒤 지정 반출 매체만 분리해 검역에서 검사한다. 합성 호스트 원장·프로젝트의 해시 불변과 정상/변조 번들 결과를 확인한다. 실제 호스트 프로젝트 적용은 별도다. GUI 인수는 독립 게스트 자체 창에 대한 별도 승인 범위다.

현재는 승인 준비까지 완료했다. A 일부 Win32 Job 실측과 C 합성 검역만 PASS이며 VM A, OS B 및 실제 C 반출과 P3는 BLOCKED다. 공개 문서에는 원시 호스트 진단과 순간 자원 수치를 포함하지 않는다.

기준 DevOrchestra `1519b7874d6e6716aef33746b94d7b79adb24ece`. 이 문서는 실행 계획이다. VM, 반출기, 네트워크 브로커는 아직 연결되지 않았다.

## P2.5 환경 비교와 이전 선정

**1순위는 전용 x64 테스트 PC의 VirtualBox 7.2 계열과 별도 Windows 11 x64 테스트 게스트다.** Oracle의 Windows 호스트 설치 절차와 UUID 기반 VM 상태·저장 매체 제어를 사용할 수 있다. 전용 PC에서는 현재 사용자의 Windows, 게임, 네트워크 설정을 건드리지 않고 부정 시험과 GUI 검증을 수행할 수 있다. 이미 승인된 VMware 전용 환경이 있다면 동일한 A/B/C 증거를 제공하는 조건으로 대체한다. [Oracle 설치 가이드](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html) · [VBoxManage 7.2](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html)

현재 호스트는 이전에 확인한 Windows Core이며 x64다. `VBoxManage.exe`와 `vmrun.exe`는 PATH와 표준 설치 경로에서 발견되지 않았다. 사용자 지정 설치와 VT-x/AMD-V 활성 여부는 확인하지 않았다. **현재 PC에서 즉시 사용할 수 있다고 확인한 후보는 없다.** Sandbox와 Hyper-V 역할의 Home 제한은 기존 결과를 재사용한다.

| 후보 | 실현 가능성·비용·제약 |
| --- | --- |
| VirtualBox | Oracle은 Windows 11 x64 호스트 패키지를 제공한다. 현재 Core에서 설치 가능한 후보이나 펌웨어, 호스트 보안 정책, 드라이버와 실제 자원 여유가 전제다. GPLv3 기본 패키지 비용은 0이며 Windows 게스트 라이선스는 별도다. Extension Pack은 이 계획에 필요 없다. 현 PC에 설치할 경우 호스트 드라이버·서비스, 선택적 네트워크 기능과 재부팅 영향 검토가 필요하다. [Oracle 배포](https://www.oracle.com/ae/virtualization/technologies/vm/downloads/virtualbox-downloads.html) · [설치 기능](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html) |
| VMware Workstation Pro | 기존 전용 환경의 대안이다. Broadcom은 무료 버전을 안내하며 공식 다운로드 포털에는 계정이 필요하다. 대상 Windows 빌드 지원, 통합 기능 해제, VM 종료와 별도 매체 회수를 제품별로 검증한다. 드라이버와 가상 네트워크 설치 영향은 별도 검토한다. [Broadcom 배포·라이선스](https://knowledge.broadcom.com/external/article/368667/download-and-license-vmware-desktop-hyper.html) · [VMware FAQ](https://www.vmware.com/docs/desktop-hypervisor-faqs) |
| Windows Sandbox / Hyper-V | Home에서 Sandbox와 Hyper-V 역할은 지원되지 않는다. 지원 에디션의 Sandbox는 별도 B 초기 시험 후보이나 종료 시 게스트 상태가 폐기되므로 공유를 차단한 채 종료 후 반출을 증명하기 어렵다. Sandbox 네트워크 기본값은 켜져 있다. 에디션 변경이나 기능 활성화는 이 경로의 선행 승인이다. [Sandbox 지원·제약](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/) · [Hyper-V 요구사항](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v) |
| WSL 2 / AppContainer / Job Object | WSL 2는 Home 후보지만 Linux 워크로드 및 Windows 통합·마운트 경계가 있어 이 Windows 게스트·GUI 인수 환경으로 채택하지 않는다. AppContainer는 제한 토큰 후보로 별도 경계 검증이 필요하다. Job Object는 소유 프로세스의 종료·자원 제어에 사용한다. [WSL FAQ](https://learn.microsoft.com/en-us/windows/wsl/faq) · [WSL 통합 설정](https://learn.microsoft.com/en-us/windows/wsl/wsl-config) · [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) |
| 전용 클라우드 Windows VM | 별도 PC가 없으면 보조 후보로 검토한다. 전용 프로젝트·계정, 공용 IP와 NAT 없는 사설 서브넷, 명시적 송신 차단, 관리 ID·메타데이터 경계, 오프라인 디스크 회수가 필요하다. 공용 IP 유무로 송신 여부를 판정할 수 없다. 비용은 지역·SKU의 실행시간 단가 × 시간 + 디스크·스냅샷·반출 비용이며 계정, 지역, 예산 상한이 정해지기 전에는 견적·생성을 승인하지 않는다. 호스트 Win32 Job 회귀는 별도다. [Azure 기본 송신](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access) · [Windows VM 가격](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/windows/) |

## 승인 후 준비 순서

1. **기존 전용 PC/VM을 먼저 지정한다.** 승인에 물리 호스트, 테스트 사용자, 격리 저장 루트, 버전과 이미지 해시, 허용 일시, 소유 VM UUID를 귀속한다. 해당 시험 중 다른 DevOrchestra 설치에서 실행을 시작하지 않는다. 현 뮤텍스는 한 Windows 호스트의 범위이며 호스트 사이의 배타 실행은 아직 자동 강제되지 않는다.
2. 새 설치가 필요한 경우에만 공식 x64 기본 패키지 및 승인된 Windows 이미지·Python 설치 파일을 다운로드한다. 벤더 서명·공식 SHA-256·지원 버전을 대조한다. 사용자에게 패키지 버전, 저장 경로, 설치 대상 PC, 필요한 관리자 작업과 드라이버·서비스 범위를 제시하고 승인 후 설치한다. Oracle 설치 기능 중 네트워크·USB 부가 기능은 오프라인 기준에서 제외한다. BIOS/UEFI·Hyper-V·VBS·에디션 변경과 재부팅은 개별 승인 항목이다. 설치 후 기능을 강제로 끄는 우회는 사용하지 않는다. [Oracle Windows 설치 옵션](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html)
3. 빈 Windows 11 x64 테스트 게스트를 합성 전용 로컬 계정으로 준비한다. UEFI·Secure Boot·vTPM과 승인된 게스트 라이선스를 확인한다. 호스트 로그인·브라우저·운영 DB·프로필·토큰·공유 폴더·클립보드·드래그앤드롭·USB 전달·원격 콘솔·vGPU·오디오는 연결하지 않는다. 게스트의 Python, Tk, 합성 시험 코드만 승인된 읽기 전용 ISO로 공급한다. 시험 역할은 게스트 표준 사용자다. 기초 이미지 설치·ACL·매체 설정에 필요한 게스트 관리자 권한은 별도 범위로 다룬다. [Windows 11 요구사항](https://www.microsoft.com/en-us/windows/windows-11-specifications)
4. 최초 시험의 제안 상한은 **2 vCPU, CPU 실행 캡 60%, 게스트 RAM 6 GiB, 게스트 디스크 64 GiB 고정, 반출 디스크 16 MiB 고정, 1회 20분**이다. 수치는 실측·벤더 보장이 아닌 승인용 시작값이다. 호스트에는 이미지 복제·RAW 변환·감사 로그를 포함해 100 GiB 이상 여유를 먼저 확인한다. 고정 디스크만으로 로그·스냅샷·덤프의 호스트 디스크 소비가 제한되지는 않는다. 전용 볼륨의 별도 한도와 사전/실행 중 집계 자원 점검을 적용한다. 예산 부족 시 입장을 차단한다. Windows 권장 성능, 캡 효과 및 부트 시간은 첫 시험에서 확인한다.
5. 전원 OFF 상태에서 승인된 UUID만 대상으로 구성한다. `VBoxManage modifyvm`의 CPU·RAM·캡 옵션을 사용하고 **모든 NIC를 none**으로 설정한다. VM별·전역 공유 폴더가 모두 비어 있는지 확인한다. 클립보드·드래그앤드롭을 명시적으로 끈다. `showvminfo <UUID> --machinereadable`로 구성과 저장 매체를 검증한다. 명령을 지원하지 않거나 출력·상태가 모호하면 시작하지 않는다. [VBoxManage 명령 참조](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html)

## 단일 슬롯과 종료 증거

신뢰 실행기는 기존 순서 **Manager → Developer → Reviewer → Verifier → Manager**와 전역 프로젝트·작업·역할·실행 슬롯 각 1개를 유지한다. 한 슬롯 안에 VM UUID, 세대, 매체 UUID·해시, 시작/종료 영수증과 게스트 Job 증거를 결합한다. 모든 게스트 실행은 하나씩 진행한다. VM 부팅·종료·반출도 다른 작업과 겹치지 않는다. 시작 전 실행 의도를 원장에 기록하며, 게스트에는 호스트 원장 파일이나 쓰기 자격증명을 전달하지 않는다.

동일 세대의 게스트 Job 활성 수 0 또는 VM 강제 종료로 작업이 제거됐다는 증거와, 하이퍼바이저의 `poweroff` 상태, 해당 VM에 귀속되는 소유 실행 핸들의 종료를 확인한다. 종료 요청 성공, 클라이언트 CLI 종료, PID 단독, 일시정지·저장 상태는 종료 증거로 채택하지 않는다. 하이퍼바이저의 공용 데몬에는 종료·조작을 가하지 않는다. VM별 실행 프로세스 귀속을 입증할 수 없으면 다음 작업을 차단한다. 기한을 넘기면 승인된 UUID에만 종료를 요청하고, 정리 결과가 불명확하면 증거를 보존하고 BLOCKED 상태로 둔다. 재시작은 기존 세대를 조회하며 동일 작업이나 전원을 자동으로 다시 켜지 않는다.

## A/B/C 인수 시험

승인된 전용 환경의 합성 표식만 사용한다. 각 기록에 코드 SHA, 하이퍼바이저·게스트·시험 도구 버전, VM/매체 UUID, 실행 세대, 구성 해시, 시작·종료 시각, 대조 결과, OS 오류 코드와 증거 해시를 남긴다. 정상 대조가 실패하거나 계측이 불완전하면 해당 항목은 NOT VERIFIED다. 제품 우회나 가짜 표식 노출이 재현되면 FAIL이다.

| 게이트 | 양성 대조 | 본 시험과 PASS 조건 |
| --- | --- | --- |
| A · 소유 트리와 VM | 고정 합성 자식과 손자를 생성해 루트 종료 후에도 Job에 자식이 남는지 관측한다. 같은 VM UUID에 대해 실행 중 상태와 작업 표식이 관측되어야 한다. | 정상 종료, 시간 초과, 취소, 실행기 강제 종료, 하이퍼바이저 조회 장애를 순차 실행한다. 자원이 남거나 상태 불명일 때 경쟁 실행이 거부되어야 한다. 확인된 소유 자원 종료 뒤에만 다음 세대를 허용한다. 실행기 크래시 복구는 별도 독립 호스트 증거가 필요하다. |
| B · 파일·자격증명·통합 | 같은 시험 계정이 승인된 게스트 작업 폴더를 읽고 쓴다. 허용 ACL에서 같은 Win32 읽기·쓰기 및 링크 도구가 작동하는지 먼저 확인한다. 가짜 프로필·토큰·DB·클립보드 표식은 테스트 호스트에서 존재와 해시를 확인한다. | 별도 게스트 보호 폴더의 ACL은 OS 오류로 읽기·쓰기를 거부해야 한다. 호스트 경계는 구성에서 마운트·공유·전달 채널 부재를 확인하고 게스트에서 합성 호스트 표식 접근과 양방향 클립보드·공유 시도가 실패해야 한다. 허용 대상 링크는 대조에서 성공해야 하며 junction·reparse·경로 교체 공격은 허용 경계 밖의 표식 읽기·쓰기나 반출을 발생시키면 FAIL이다. 파일 부재와 ACL 거부를 별도 기록한다. |
| B · 네트워크 | 승인된 전용 테스트망의 별도 대조 부팅에서 같은 게스트 도구로 합성 DNS 및 TCP 수신기에 도달해 양쪽 증거를 남긴다. 생산 네트워크나 인터넷은 연결하지 않는다. | 동일 이미지의 모든 NIC를 제거한 시험 부팅에서 IPv4/IPv6 경로와 DNS 구성을 기록한다. 같은 사설 주소·DNS·가짜 호스트 수신기에 접근하고 OS의 연결 실패와 수신기 무수신을 함께 확인한다. 대조 부팅과 차단 부팅은 겹치지 않는다. |
| C · 호스트 보호와 반출 | 합성 프로젝트·감사 원장 복제본의 초기 해시를 호스트에서 읽는다. 승인 목록에 맞는 합성 ZIP이 정해진 반출 경로와 검역을 통과해야 한다. | 게스트가 테스트 호스트 프로젝트·원장 복제본에 쓰려 해도 변경이 없어야 한다. VM 실행 중이거나 종료 불명일 때 반출을 거부한다. 종료 뒤에만 게스트가 쓴 별도 고정 VDI를 검역 위치에 분리한다. 호스트 생성 VDI UUID만 `clonemedium ... --format=RAW` 대상으로 삼고, RAW를 마운트하지 않는다. 고정 위치의 버전·길이·세대·번들 해시를 검사해 제한된 바이트만 `inspect_bundle`에 넘기는 반출기는 구현·실측이 필요하다. 잘못된 길이·해시·ZIP·링크·경로는 거부하고 원래 호스트 자료의 해시를 재확인한다. |

반출 디스크 작성은 역할과 분리된 합성 전송 도구의 계획이다. 도구는 게스트 안에서 승인된 출력 매체만 사용하며 호스트 볼륨 접근 권한을 받지 않는다. 게스트의 선언이나 해시는 승인 증거가 아니다. 작업 파일 적용은 이 시험에서 수행하지 않는다. 향후 검역 결과를 별도 검토하고 승인된 작업 복제본·현재 Git SHA·파일 핸들 경계를 재검증한 뒤에만 적용한다. [VirtualBox 매체 복제](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html#vboxmanage-clonemedium)

## 두 가지 네트워크 모드

**격리 검증 모드**는 모든 게스트 NIC를 제거하고 실제 서비스나 토큰을 사용하지 않는다. 합성 대조망은 별도 승인·별도 부팅에서만 사용한다. 이 모드가 P2 B의 기준이다.

**향후 Codex 개발 모드**는 별도 이미지·구성 해시·승인으로만 활성화한다. VM의 모든 직접 송신을 차단하고 전용 분리망에서 신뢰 게이트웨이 하나에만 연결한다. 일반 NAT, 브리지, 공유 프록시와 직접 DNS는 차단한다.

- 게이트웨이는 승인된 DNS로만 해석한다. 연결 시 이름과 IP를 함께 검증하고 사설·루프백·링크로컬·메타데이터·IPv6 우회 주소를 거부한다. DNS 리바인딩, 외부 DNS/DoH, QUIC 및 IP 직접 접속을 시험한다.
- TLS 인증서와 호스트 이름을 검증하고 리다이렉트는 기본 거부한다. 도메인 allowlist만으로 TLS 안의 HTTP 경로나 업로드를 제한할 수 없으므로 범용 CONNECT 프록시는 사용하지 않는다. 정확한 모델 요청 메서드·경로·스키마, 허용 데이터 분류, 요청/응답 크기, 요청 수·비용·시간 제한을 강제하는 애플리케이션 브로커가 필요하다. 파일 업로드, 커넥터, 원격 도구·명령 요청은 별도 권한으로 기본 차단한다.
- 인증정보는 게스트 밖 승인된 비밀 저장소에서 관리한다. 브로커가 승인된 요청에만 단기·최소 권한으로 붙이고 원문 토큰·본문은 감사 원장에 남기지 않는다. 모델 호출 자체가 허용 데이터의 외부 전송이므로 소유자가 데이터 범위를 별도로 승인해야 한다.
- 선택한 Codex/App Server 버전이 이 인증·브로커 계약을 공식 지원하는지 별도로 검증한다. 경로·본문 집행을 우회해야만 동작하는 경우에는 P3가 BLOCKED다. 게이트웨이 장애·인증 만료·미계측은 모두 송신을 차단한다. 이 모드는 이번 작업에서 구성하거나 시험하지 않았다.

## 독립 GUI 인수

동일 전용 Windows 게스트에 합성 원장만 둔다. 1280×720과 1920×1080, 100/125/150/200% 배율에서 창 최소 크기·리사이즈·스크롤·긴 한국어/영문 텍스트를 확인한다. 프로젝트 변경, 승인 대기, 두 작업 세션, 합성 이벤트 수신, 끊김·복구, 역할 카드와 상세 감사의 ID·순번·사용량을 합성 원장과 대조한다. Tab/Shift+Tab, 포커스 표시, 키보드 선택, 고대비·텍스트 확대 및 게스트 스크린리더에서 이름·상태를 확인한다. 게스트 자체 창의 증거만 별도 승인 아래 수집한다. 실제 사용자 데스크톱을 입력·캡처 대상으로 삼지 않는다. 현재 독립 GUI 환경은 확보되지 않아 NOT VERIFIED다.

## 다음 승인과 P3 판정

다음 승인의 우선 범위는 **기존 전용 테스트 PC/VM 한 대**, 합성 매체 연결, 지정 UUID의 시작·정상/강제 종료, 전용 합성 수신기에서만 수행하는 대조 통신, 오프라인 반출 및 자체 게스트 창 검증이다. 해당 환경이 없을 때만 다운로드·설치·게스트 라이선스·관리자 작업을 각각 제안한다. BIOS/UEFI 변경, Windows 기능·에디션 변경, 재부팅, 클라우드 계정·비용, 실제 모델 송신·인증은 별도 승인이다.

A는 기존 Win32 합성 Job 일부만 PASS다. B는 실제 OS 경계 시험이 BLOCKED다. C는 메모리 검역만 합성 PASS이며 게스트 경계와 반출 실측은 BLOCKED다. **P3 진입 조건은 A/B/C 실측 PASS와 버전 고정 App Server 스키마·사용량 정산, 별도 모델 송신 승인 및 차단 시험이다.** 이후에도 최초 연결은 합성 입력의 단일 역할로 제한한다.
