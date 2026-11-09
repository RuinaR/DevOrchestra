# P2.7 Windows 네이티브 MXC 검토

## P2.51 네이티브 제한 읽기 후보

Controller의 `NativeBoundaryApproval`은 독립 원장의 temp 아래 `app-server-<요청 ID>`만 받는다. 합성 홈의 config 전체 바이트, 승인된 요청의 정규 JSON, 작업 폴더에 배치한 EXE를 각각 SHA-256에 귀속한다. `devorchestra-probe-read`는 상속 없는 프로필이며, 절대 `work` 경로 한 개만 읽기로 선언한다. 쓰기 규칙과 플랫폼 기본 읽기는 없고, 네트워크 비활성, 승인 정책 `never`, Windows MXC를 명시한다. 고정 후보 요청은 `work/mxc-probe.exe --app-server-file-test`, cwd `work`, 5초, `permissionProfile`만 사용한다. 실제 연결에는 기존 서명·해시 고정 CLI, 실험적 기능을 명시하는 초기화 및 명령 cwd의 유효 정책 확인이 필요하다.

MSVC 19.44.35228.0 및 Windows SDK 10.0.26100.0으로 기존 CRT 없는 소스에서 AMD64 PE32+ 콘솔 EXE를 빌드했다. 빌드 입력 `probe.c` SHA-256은 `bedc090824c659dd9a7a0cd84094cbcf27ec21b4740baac638f009073100150f`, EXE는 `6f3121bb3bd50b250479f994f0a890f3a53e1325f542b5a9397605987d97399e`다. 직접 import는 KERNEL32.dll만 있고 지연 import는 없다. Windows 시스템 로더·전이 DLL 접근은 별도 문제이며 범위는 미확인이다. 호스트 합성 자기 점검, 추가 인자·잘못된 cwd 거부, 양성 파일 대조 4건을 통과했다. 파일 대조에서 호스트 읽기·쓰기는 의도대로 성공했고 보호 표식은 불변이었다. EXE와 합성 결과는 로컬에만 보존한다.

현재 연결 범위는 Controller 사전검사까지다. config, 요청, 바이너리 및 표식이 일치해도 `NATIVE_LOADER_SCOPE_UNVERIFIED`로 차단하며 App Server, 전송 의도, 프로브는 시작하지 않는다. 향후 입장은 정확한 로더 범위 및 유효 프로필 증거를 요구한다. 그 뒤 별도 승인된 1회 시험에 한해 기존 Job·파이프 및 원장 경계를 사용한다. 상한은 App Server 15초, 명령 5초, Job 512 MiB 및 소유 보조 프로세스 포함 8개다. 기대 결과는 허용 읽기 성공, 쓰기와 보호 읽기에서 Win32 5, 두 표식 해시 일치와 소유 자원 회수다. MXC 집행, 자식 권한 전파와 외부 앱 비간섭은 NOT_VERIFIED다. 이전 MXC EXE 실패는 유지한다. 새 후보는 App Server의 무상속 프로필과 별도 고정 인자를 사용하지만, 이미지·로더 실패를 해결했다는 증거는 없다.

## P2.50 Windows 제한 읽기 실행 계약

고정 CLI `0.167.0-alpha.3.2`의 `command/exec`는 `readOnly.access.restricted`를 거부한다. 지원되는 설정 경로는 상속 없는 named profile의 명시적 파일 읽기 규칙과 비활성 네트워크다. `command/exec.permissionProfile`은 초기화 시 실험적 기능을 명시해야 하고 `sandboxPolicy`와 함께 보낼 수 없다. 서버는 명령 cwd를 기준으로 설정과 관리 제약을 다시 해석한다. `permissionProfile/list`의 `allowed`는 프로필 선택 가능 여부이며, `windowsSandbox/readiness`의 `ready`는 백엔드 준비 상태다. 둘 다 실제 적용 경로 목록이나 접근 거부 영수증을 제공하지 않는다. [고정 요청 스키마](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/app-server-protocol/src/protocol/v2/command_exec.rs) · [프로필 선택 코드](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/app-server/src/request_processors/command_exec_processor.rs#L212-L241) · [프로필 목록 스키마](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/app-server-protocol/schema/json/v2/PermissionProfileListResponse.json)

MXC는 유효 프로필에서 읽기·쓰기·거부 경로를 생성한다. 플랫폼 기본 읽기가 활성화되면 Windows 디렉터리, Program Files 계열과 ProgramData가 추가된다. 이 범위에는 System32와 로더 관련 경로가 포함되지만, 그 자체로 필요한 런타임 파일의 정확한 집합이나 실행 성공을 보장하지 않는다. 합성 작업 폴더만 읽는 시험에는 `:minimal`을 추가하지 않는다. 기본 거부와 쓰기 허용 규칙 부재는 정적 정책 계약이다. OS의 외부 읽기·쓰기 거부, PowerShell 및 자식에서의 권한 전파는 NOT_VERIFIED다. MXC는 정책 수명을 유지하고 소유 자식 회수를 지원하지만, Win32k와 데스크톱 핸들이 남으며 하드링크 권한은 경로별이다. 합성 경로의 링크와 reparse point는 계속 사전 거부한다. [MXC 정책 변환](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/policy.rs#L209-L247) · [Windows 플랫폼 경로](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/windows.rs#L117-L154) · [MXC 수명주기와 제약](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/README.md#L19-L58)

| 후보 | 판정 |
|---|---|
| A. 명시적 MXC + named profile | 일반 개발 연결의 주력 후보. 고정 API와 설정 구문은 확인했다. 현재 System32 PowerShell 조합으로 승인 폴더 밖 읽기를 금지하면서 로더를 시작할 근거가 부족하다. 광범위한 플랫폼 읽기를 허용하지 않는다. |
| B. 검증된 Windows 게스트 + Codex 읽기 전용 | 강화 격리 대안. 호스트와 게스트 경계, 공유·네트워크 차단, 회수 및 반출 검역을 별도로 검증해야 한다. 게스트 내부 작업 폴더 밖 읽기 제한도 별도 조건이다. 호스트 변경과 자원 비용이 크며 자동 전환하지 않는다. |
| C. 작업 폴더 내 고정 네이티브 프로브 | A를 검증하는 우선 수단. 외부 PowerShell 의존을 줄이지만 Windows 로더 의존성은 남는다. 이전 네이티브 자기 점검 실패는 보존하며, 프로브 성공이 Codex 도구 전체의 인수 증거가 되지는 않는다. |

현재 전송기와 권한 사전검사는 P2.49의 차단 상태를 유지한다. 다음 단일 실측을 제안하기 전에 실행 이미지와 필수 로더 범위, 무상속 프로필의 정확한 바이트와 해시, 유효 cwd와 관리 정책, 네트워크·승인 차단을 확정해야 한다. 이후 별도 승인 범위는 고정 CLI와 해시가 확인된 자체 프로브, 새 독립 원장 및 합성 루트, 명시적 MXC, 고정 모델 없는 명령 1회, 접근 결과·표식 해시·Job/파이프 회수 확인이다. 파일 경계 PASS 이후에도 일반 개발의 소유권·도구·비간섭·예산 인수 및 단일 모델 승인이 필요하며 강화 모드는 P2 A/B/C 전체를 요구한다. Manager·Developer와 P3는 BLOCKED다.

## P2.25 독립 자기 점검 프로필의 정적 계약

기준 `98fca687baadcd130725554c257a992ec056ccb2`. 고정 태그 `rust-v0.167.0-alpha.3.2`의 named profile 컴파일은 제한된 빈 파일 정책과 제한된 네트워크 정책에서 시작한다. 부모가 없고 매칭되는 경로 규칙이 없으면 파일 접근은 기본 거부다. 더 구체적인 경로가 상위 경로에 우선하고 동일 경로 충돌에서는 거부가 우선한다. 새 초안은 충돌 우선순위에 의존하지 않고 중복·허용/거부 충돌을 전부 거부한다. 절대 Windows 경로를 TOML 문자열 키로 사용할 수 있다. 경로 구분 문자는 TOML에서 이스케이프한다. [고정 태그 프로필 컴파일](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/core/src/config/permissions.rs) · [고정 태그 경로 우선순위](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/protocol/src/permissions.rs) · [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions)

`tools/mxc-probe/independent-profile.ps1`은 `mxc-native-minimal` 이름으로 메모리 내 초안을 생성한다. 상속·루트 규칙·`:minimal`·추가 루트·glob·쓰기 없이, 새 GUID 합성 work의 정확한 EXE만 읽기 대상으로 표현한다. 승인 요청과 네트워크는 차단한다. Controller·원장·보호 표식·사용자 파일에는 권한을 선언하지 않는다. 결정적 검사기는 허용한 한 가지 직렬화만 비교하므로 추가·중복·충돌·미지원 구문을 모두 거부한다. 작업 루트·EXE 경로·두 바이너리 핀과 관리 정책 증거를 대조하며 개인 경로를 코드에 넣지 않는다. 이 검사는 호스트가 제공한 값의 정합성을 확인하며 파일을 열어 해시를 다시 재거나 실제 객체·reparse 여부를 확인하지 않는다.

### 실행 입장 판정

**PROFILE_UNSUPPORTED · BLOCKED.** 이 판정은 필수 런타임 허용 목록을 확인하지 못한 현재 시험 계약에 해당한다. EXE의 직접 PE import가 KERNEL32.dll 하나라는 P2.17 증거로는 Windows 로더·API set·전이 DLL·SDK가 실제로 요구할 경로 전부를 확정할 수 없다. 고정 태그 프로필에 독립적인 EXE 실행 허용 키도 확인되지 않았다. 파일 읽기 규칙, 실행 명령·이미지 고정, OS 프로세스 생성과 초기화는 서로 다른 검증이다. `:minimal`이나 System32·ProgramData 전체 읽기를 추정으로 부여하지 않는다. [고정 태그 MXC 변환](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/policy.rs) · [Microsoft CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)

관리 계층은 같은 이름의 프로필에 규칙과 workspace roots를 추가할 수 있으며, Windows 정책은 요구 파일 위치를 재지정할 수 있다. 검사기의 VerifiedAbsent는 신뢰 호스트가 전달하는 증거의 입력 자리이며 실제 PC의 관리 정책을 조회하거나 증명하지 않는다. 미확인·충돌은 BLOCKED다. 합성 비교가 성공해도 Codex의 최종 구성, OS 집행이나 비간섭 PASS로 승격하지 않는다. [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions) · [Windows 관리 배포](https://learn.chatgpt.com/docs/enterprise/windows-deployment)

진단 전용 프로필은 네이티브 자기 점검만 대상으로 한다. 향후 개발용 계약은 검증된 복제본 하나에만 쓰기를 부여하고 고정 컴파일러·런타임만 사용한다. 원본·다른 프로젝트·사용자 자료는 기본 거부하며 네트워크·외부 도구는 별도 승인하고, 생성 코드·빌드·테스트에도 같은 경계를 적용해야 한다. 개발용 실행과 프로필은 미연결이다.

P2.23 원인은 UNKNOWN이다. 기존의 광범위한 루트 읽기 충돌 가설은 약한 상태를 유지하며, 구성 해석·MXC 초기화·이미지 준비·프로세스 생성·알파 빌드 문제를 구분하지 못했다. 이번에는 새 프로필을 런처에 연결하거나 적용하지 않았다. 우선 정확한 런타임 목록과 유효 정책 증거가 필요하다. 이를 확보하기 전에는 실제 시험 승인을 제안하지 않는다. 이후 범위는 고정 CLI·EXE, 새 합성 원장·작업 루트, 검증된 독립 프로필 및 `--self-test` 1회이며 이미지 시작만 판정한다.

PowerShell 합성 정적 검사와 `git diff --check`는 통과했다. Python 실행 금지에 따라 전체 Windows Python 회귀는 미실행이다. Python·MXC·네이티브 EXE·Codex 실행은 0회다. 기존 P2.20/P2.22 경로를 변경하지 않았다. P2 A/B/C 전체와 P3 실제 자동 개발·커밋은 BLOCKED다.

## P2.24 루트 권한 및 초기화 가설

기준 `e75234b36d70aaca77b4c80f6bf99f8b81e8890c`. P2.23은 사전검사·소유 CLI 시작·종료만 CONFIRMED이며 종료 1, 표식 없음, UNRECOGNIZED_STDERR다. HRESULT와 원문은 남아 있지 않다.

| 후보 | 증거와 한계 |
| --- | --- |
| 광범위한 루트 정책 준비와 공유 위반 | #52547의 공개 사용자 재현은 좁은 구성에 루트 읽기만 더해 초기화 오류 `0x80070020`을 재현했다. #52042에는 합성 잠금 재현과 다른 환경의 복수 사례가 있다. 이는 준비 단계가 공유 잠금과 충돌할 수 있다는 외부 관측이며, 이 호스트의 원인이나 고정 알파 빌드의 재현 증거는 아니다. |
| 사용자 정의 프로필 해석·유효 정책 충돌 | 상속, 관리 구성, 실제 workspace roots 및 Windows 기능에 의존한다. 고정 TOML 사전검사만 통과했으며 유효 정책과 실패 단계는 UNKNOWN이다. |
| EXE·DLL 또는 프로세스 생성 | 같은 프로필의 P2.10 System32 echo는 시작했고 P2.18/P2.23 네이티브 EXE는 호스트에서만 성공했다. Python 실패와 종료 코드도 다르다. 이미지 또는 생성 경계는 여전히 유력하지만 로더·생성·준비를 구분할 직접 증거는 없다. |
| 고정 CLI 알파 또는 다른 초기화 문제 | 업스트림 태그를 비교했으나 다른 바이너리와 통제 비교하지 않았다. 호스트 경로·정책 준비·SDK 실패는 여전히 가능하다. |

[공개 이슈 #52547](https://github.com/openai/codex/issues/52547) · [공개 이슈 #52042](https://github.com/openai/codex/issues/52042)

### 고정 버전 소스와 권한 범위

- `:workspace` 상속은 workspace 쓰기와 부모의 루트 읽기·임시 쓰기 설정을 가져온다. `:root = "deny"`는 부모의 같은 키를 덮어쓴다. 따라서 **현재 구성에서 해당 줄만 삭제하면 부모의 넓은 루트 읽기가 다시 활성화**된다. 임시 경로 차단 두 줄을 남겨도 일반적인 루트 읽기를 막지는 못한다. [고정 태그 프로필 해석](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/core/src/config/permissions.rs) · [고정 태그 workspace 정책](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/protocol/src/permissions.rs)
- 고정 태그의 MXC는 논리 볼륨 목록에 맞춰 심볼릭 루트를 해석한다. 읽기 루트에는 일반적으로 로컬 볼륨과 작업에 해당하는 공유가 포함된다. 이 단계의 루트 매핑과 드라이브 하위 파일 열거는 별개다. 네이티브 정책 생성 소스는 심볼릭 루트 거부를 기본 거부로 취급하고, 볼륨 직계 하위 확장은 쓰기 루트에만 적용한다. 이 프로필은 루트에 읽기·쓰기 허용을 선언하지 않는다. 따라서 공개 이슈의 광범위한 읽기 허용 충돌을 현재 루트 거부가 같은 방식으로 일으켰다는 가설은 약하다. 다만 논리 볼륨 확인과 SDK의 정책 준비·파일 핸들 동작 전체까지 정적 소스로 입증할 수는 없다. [고정 태그 MXC 정책 변환](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/policy.rs) · [고정 태그 Windows 루트 처리](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/mxc-sandbox/src/windows.rs)
- `:minimal`은 특정 DLL만 허용하는 표현이 아니다. 이 태그의 플랫폼 읽기 후보에는 Windows, Program Files 계열과 ProgramData가 포함된다. `:tmpdir`은 필터링한 TEMP/TMP에 대응하며 `:slash_tmp`는 Windows에서 별도 경로로 확장되지 않는다. 이 프로필에는 deny glob이 없어 공식 문서의 glob 일치 파일 스캔을 이 실행의 근거로 삼을 수 없다. 네트워크 비활성·승인 금지는 유지하되, 선언을 OS 실측으로 보지 않는다. [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions) · [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

### 좁은 비교안과 입장 조건

후보는 **부모 상속 없는 독립 이름**으로 설계한다. 루트 허용·glob·추가 workspace roots 없이 제한 정책을 사용한다. 자기 점검에는 승인된 합성 EXE와 필요한 Windows 런타임만 읽기 후보이며 쓰기 허용은 필요 없다. 런타임 목록을 정확히 산정할 수 없으면 `:minimal`로 조용히 확대하지 말고 차단한다. 후속 파일 시험의 쓰기 범위도 합성 work에만 제한한다. 보호 표식·원장·Controller·사용자 프로필은 허용 목록 밖에 두고, reparse·hard link 및 관리 구성으로 권한이 확대되지 않는지 확인한다. 설정만 보고 OS 파일 읽기·쓰기 거부나 EXE 실행 가능성을 확정하지 않는다.

이 변경은 루트 문법 하나만 바꾸는 비교가 아니다. 현 시점에서 실제 재실행은 제안하지 않는다. 먼저 후보의 유효 접근 목록과 기본 거부, 런타임 항목, 동일 바이너리·관리 정책을 정적으로 확인한다. 통과한 경우에만 별도 승인 범위를 **동일 CLI·EXE 해시, 새 합성 원장·작업 공간, 명시적 MXC, 승인된 별도 프로필, 고정 `--self-test` 1회**로 제한한다. 성공은 해당 프로필의 이미지 시작 관측으로만 기록하고, 파일 읽기·쓰기 거부 시험은 별도 승인과 증거로 분리한다. 원인이 좁혀지지 않거나 권한이 넓어지면 실행하지 않는다. 다른 PC도 OS 격리와 비간섭 통과 전 실제 자동 개발은 차단한다.

정제 분류기에 공개 보고의 정확한 MXC 보안 환경 오류 접두사를 추가했다. 일치하더라도 REPORTED_FAILURE로만 기록한다. P2.23의 원문을 복원하거나 판정을 바꾸지 않는다. 기존 런처·프로필·종료 예약은 유지했고 MXC·Python·네이티브 EXE·Codex 호출은 0회다. P2 A/B/C 전체와 P3 실제 자동 개발·커밋은 BLOCKED다.

## P2.23 승인된 수집기 실측

기준 `9ad63f0bd71de77b48f0c2b54c8d400a00a272b9`. 운영 원장과 분리된 새 합성 원장 및 새 GUID 합성 홈·작업 폴더에서 P2.22 런처를 그대로 사용했다. 승인 ID·만료, 기존 CLI 경로·오프라인 서명·고정 해시, 네이티브 EXE 해시 및 고정 프로필 사전검사가 통과했다. 명시적 MXC와 `mxc-file-test`로 EXE의 `--self-test`만 정확히 1회 호출했다. 원장에는 실행 의도 1건과 정제 결과 1건이 남았다.

| 단계 | 판정 |
| --- | --- |
| 신뢰 런처 사전검사 | CONFIRMED |
| 소유 CLI 시작 | CONFIRMED |
| MXC 초기화·프로필 적용 | UNKNOWN |
| 내부 실행 이미지 준비 | UNKNOWN |
| 내부 명령 시작 | UNKNOWN |
| 내부 명령 종료·결과 | UNKNOWN |
| 소유 CLI 종료 | CONFIRMED, 종료 코드 1 |

자기 점검은 FAIL이다. 고정 표식은 미관측이었다. 스트림 수집은 완료됐고 상한 초과·타임아웃은 없었다. 정제 오류는 `UNRECOGNIZED_STDERR`이며 허용된 숫자 오류 코드는 없었다. 원시 stdout·stderr는 원장에 저장하지 않았다. MXC 초기화·정책 처리, 이미지 준비 또는 프로세스 생성이 후보로 남지만 이번 영수증은 어느 단계에서 실패했는지 좁히지 못했다. 기존 P2.18과 같은 종료 코드가 관측됐으나 동일 원인 여부는 알 수 없다.

재시도·권한 확대·폴백 없이 중단했다. 소유 CLI 종료와 전체 트리 회수는 별개이며 트리 상태는 UNKNOWN이다. P2.10 시작 PASS와 P2.17 호스트 증거를 유지한다. A의 MXC 전체 회수·외부 Job 결합, B의 OS 격리·비간섭, C의 MXC 원장 보호는 NOT VERIFIED다. P2 A/B/C 전체 및 P3 실제 Codex 자동 개발·커밋은 BLOCKED다.

## P2.22 소유 CLI 진단 수집기

기준 `dc79a929777d4cdd1e911ce47ce970929ffef7be`. 내부 `mxc_launcher._run_approved_self_test`는 신뢰 소유자 승인 ID·만료와 원장을 받는다. CLI 또는 GUI 진입점이 없으며 호출자에게 명령·경로·프로필을 받지 않는다. OpenAI 서명 CLI와 P2.17 EXE의 해시를 고정한다. 버전은 이전 실측과 동일한 CLI 바이트로 확인하며 별도 CLI 버전 프로세스를 열지 않는다. Windows 신뢰 검증은 캐시 전용이며 최신 온라인 인증서 상태의 증거가 아니다. 호스트 폴더 및 관리 구성 후보를 확인하고 새 합성 홈과 EXE만 들어 있는 작업 공간을 만든다. 고정 TOML과 argv를 확인하며 유효 MXC 정책의 OS 적용 여부는 여전히 UNKNOWN이다. [Microsoft WINTRUST_DATA](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/ns-wintrust-wintrust_data)

전역 슬롯을 확보하고 종료 예약과 경쟁하는 동일 SQLite 트랜잭션으로 실행 의도·세대를 기록한 뒤에만 CLI를 생성한다. 슬롯과 원장 기반 일회성 제한을 사용하므로 실행한 원장은 새 승인 ID로도 자동 재실행할 수 없다. 결과 저장 실패 시 의도가 남아 신규 실행과 정상 종료를 차단한다. 같은 PC에서의 다음 실측은 실제 운영 원장과 분리된 소유 합성 원장을 지정하고, 시험 뒤 보존한다. 원장을 바꾸어 재시도하는 기능은 없다.

소유 CLI 파이프 두 개를 동시에 읽고 각각 최대 16 KiB만 메모리에 보관한다. 실행 대기는 15초, 시간 초과 시 직접 생성한 CLI 핸들에만 종료를 요청하고 최대 2초 추가 대기한다. 종료 코드는 해당 CLI의 wait 결과에서 얻는다. 스트림 초과·수집 불완전은 PASS로 승격하지 않는다. stderr 원문과 런처 예외 내용은 기록하지 않고 정제 영수증·종료 코드·증거 수준만 원장에 남긴다. 호스트 확인과 CLI 보고를 구분한다.

기존 Job 프로브는 고정 합성 전용으로 유지한다. 이 런처는 MXC 외부 Job 편입·전체 자식 회수의 증거를 만들지 않는다. CLI 생성이 시도된 뒤에는 원장의 소유 트리 상태를 UNKNOWN으로 보존하며 다음 작업 입장과 정상 체크포인트를 차단한다. 이번에는 소유 프로세스와 스트림을 합성으로 검증했고 MXC 호출은 0회다. P2.18 FAIL, P2 A/B/C 전체와 P3 BLOCKED를 유지한다.

다음 별도 승인 범위는 소유 합성 원장·새 GUID 합성 작업 공간, 기존 서명 CLI·고정 해시, 기존 네이티브 EXE 해시, 명시적 MXC와 동일 `mxc-file-test`, `--self-test` 정확히 1회다. 이 수집기로 정제 영수증만 보존하고 추가 실행 없이 멈춘다. 관리 구성이나 고정 증거가 맞지 않으면 호출하지 않는다. 파일 시험·Python·외부 Job·인증·모델 호출은 별도 범위다.

## P2.21 네이티브 자기 점검 실패 분석

기준 `60bb7f8fd15ebeea9f1de70ca8c63941ba548c20`. P2.18의 런처 인자, 새 합성 홈·작업 폴더, 바이너리 고정, 프로필 및 오류 처리 기록을 P2.10·P2.13·P2.15·P2.17과 대조했다. P2.18은 명시적 `mxc`, 관리 구성 포함, `mxc-file-test` 및 절대 EXE 경로와 `--self-test`를 사용했다. 소유 CLI 종료 1과 표식 미확인만 남았고 원시 stderr는 보존되지 않았다. 이전 분류는 접근 거부·호스트 변수·프로필/정책 표현 외의 오류를 단일 범주로 접었다. 원문과 실패 위치는 복원할 수 없다.

우선 가설은 MXC가 작업 공간의 실행 이미지나 의존성을 준비하거나 프로세스를 생성하는 경계에서 실패했다는 것이다. 동일 프로필의 System32 cmd.exe는 성공했으며, 외부 CPython은 `0xC0000022`, 작업 공간 안 네이티브 EXE는 종료 1이었다. 네이티브 EXE의 호스트 검증과 PE 검사는 통과했으므로 Python/CRT 하나로 공통 원인을 설명할 수 없다. 두 실패의 종료 코드와 실행 대상이 달라 동일 원인이라고 볼 수도 없다. MXC 초기화, 관리 정책 합성, 경로 해석 또는 SDK/CLI 빌드 문제는 여전히 가능하다.

- `mxc-file-test`의 구성은 workspace 상속, 루트 읽기 거부, `:minimal` 예외, 임시 루트 제한, 네트워크 비활성 및 승인 요청 금지다. 공식 문서는 최소 읽기 집합을 Codex가 정한다고 설명한다. 현재 공개 소스는 Windows 디렉터리 등을 플랫폼 읽기 후보로 수집하고 정책을 네이티브 요청으로 변환한다. 현재 소스는 호스트에 고정된 알파 바이너리의 정확한 구현을 입증하지 않는다. System32의 한 이미지 시작으로 전체 System32나 작업 공간 EXE 실행 권한을 인증할 수 없다. [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions) · [공개 MXC 호스트 경로 처리](https://github.com/openai/codex/blob/main/codex-rs/mxc-sandbox/src/windows.rs)
- EXE 읽기, DLL 매핑, 프로세스 생성 및 초기화는 별개다. Win32 프로세스 생성 성공도 초기화 완료를 보장하지 않는다. 다음 런처는 새 합성 루트의 상위 경로와 reparse 여부, 실제 절대 경로, 원본·배치 해시를 다시 확인한다. 관리 정책 충돌이나 추가 workspace root가 확인되거나 유효 설정을 확정할 수 없으면 호출 전 중단한다. `--include-managed-config`와 로컬 TOML 확인은 OS 집행 증거가 아니다. [Microsoft CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw) · [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

### 정제 진단 영수증

`devorchestra/mxc_diagnostic.py`는 실행 기능이 없는 순수 분류기다. 사전검사, 소유 CLI 시작, MXC 초기화·프로필, 이미지 준비, 명령 시작, 명령 결과, 소유 CLI 종료를 별도로 기록한다. 호스트가 직접 확인한 사실과 stderr가 보고한 실패를 구분한다. 이미지 준비·유효 정책을 직접 입증하는 자료가 없으면 UNKNOWN이다. 정확한 표식, 종료 0 및 완결된 깨끗한 스트림만 해당 자기 점검 PASS로 인정한다. 파일 경계나 소유 트리 회수는 판정하지 않는다.

정확한 호스트 변수·플랫폼 경로·정책 준비·SDK 프로세스 생성·대기 오류 패턴과 일부 숫자 코드만 허용한다. 공개 소스의 문구 및 공개 보고에서 확인된 SDK 오류 형식을 참고했으며, 해당 문구가 설치 빌드에 반드시 나온다고 가정하지 않는다. 불일치·상충·상한 초과는 별도 범주로 남긴다. 다음 승인 런처는 stdout/stderr를 동시에 배출하면서 메모리 캡처에 상한을 적용하고 초과 시 불완전으로 표시한다. 원문·경로·환경·토큰을 기록하지 않으며 분류기에는 소유 CLI에서 수집한 결과만 전달한다. [공개 MXC 네이티브 런처](https://github.com/openai/codex/blob/main/codex-rs/mxc-sandbox/src/native.rs) · [공개 SDK 생성 오류 보고](https://github.com/openai/codex/issues/53188)

다음 별도 승인 후보는 기존 OpenAI 서명 CLI와 EXE SHA-256 `5bcaf68f8f75cc58db781812ff84c85e541042b3b7e6504f7d6d09816e598357`을 재확인하고, 새 합성 홈·작업 폴더에서 같은 제한 환경과 명시적 MXC·동일 프로필로 `--self-test` **정확히 1회** 호출하는 것이다. 정제 영수증만 남기고 첫 실패에서 멈춘다. 파일·Python 시험이나 폴백은 별도 범위다. 이 단계에서는 실제 실행이 없었으며 P2.18 FAIL과 P2 A/B/C 전체·P3 BLOCKED를 유지한다.

## P2.10 승인된 시작 진단 결과

기준 `bbfe379be2408712fcf0294aa33e2dd45879b772`. 별도 승인 범위에서 OpenAI OpCo, LLC의 유효 서명, 앞서 확인한 실행 위치, `codex-cli 0.167.0-alpha.3.2` 및 SHA-256 `4b3286e35d7fe866c6b0eb3f07d7a4cdb5e96489576170dfd6b3a15aa1c67382`를 재확인했다. 실행 직전 바이너리 해시·합성 프로필 원문·빈 작업 디렉터리를 대조했다. 표준 위치의 관리 요구 파일은 발견되지 않았으며 명령에는 `--include-managed-config`를 유지했다.

새 GUID 기반 합성 홈과 빈 작업 폴더, 전역 실행 뮤텍스를 사용했다. 런처에는 `SystemRoot`, `WINDIR`, `SystemDrive`, `LOCALAPPDATA`, `ProgramData`, `PATH`, `TEMP`, `TMP`, `CODEX_HOME`만 전달했다. 실제 `LOCALAPPDATA` 값은 Known Folder와 대조했고 디렉터리 내용은 조사하지 않았다. 기존 설정·인증 파일을 복사하거나 수정하지 않았다. 새 합성 자료는 보존했다.

명시적 `windows.sandbox=mxc` 및 `mxc-file-test`로 System32의 고정 echo 명령을 정확히 1회 호출했다. 결과는 `MXC_OK`, 종료 코드 0, CLI 종료 확인, 표준 오류 없음으로 **시작 PASS**다. 설정·명령에서 네트워크 비활성과 승인 금지를 선택했으나 OS 거부 동작을 실측하지 않았다. 내부 환경 노출, 파일·네트워크·자격증명·프로세스 비간섭, 외부 Job 결합 및 소유 트리 회수는 NOT VERIFIED다. 재시도와 폴백은 없었다. 다음은 별도 승인된 합성 파일 경계 시험이며, P2 A/B/C 전체와 P3 실제 Codex·커밋은 BLOCKED다.

## P2.12 현재 CLI 확인

PATH가 해석한 OpenAI Codex 설치 트리의 절대 실행 파일에서 `--version`으로 `0.167.0-alpha.3.2`를 확인했다. Windows Authenticode는 Valid, 서명자는 OpenAI OpCo, LLC였다. SHA-256은 `4b3286e35d7fe866c6b0eb3f07d7a4cdb5e96489576170dfd6b3a15aa1c67382`다. 실제 사용자 경로는 공개 기록에서 제외했다. 버전 조회 중 기존 홈의 임시 별칭 정리와 생성 시도가 접근 거부됐다는 경고가 있었다. 후속 조회와 승인된 진단에는 처음부터 전용 합성 홈을 지정한다.

공식 진단의 CLI 최소 버전 0.162.0을 넘지만, 현재 바이너리·장치·유효 프로필 조합의 MXC 시작은 아직 검증되지 않았다. 별도 승인 후 실행 직전 경로와 해시를 다시 대조하고, 변경 또는 유효 정책 불명 시 중단한다. P2.10의 제한 환경, Known Folder로 확인한 `LOCALAPPDATA`의 런처 초기화 입력, 고정 echo 1회 및 폴백 금지를 유지한다. 런처 입력은 내부 파일 접근 허가가 아니며 내부 환경 노출 여부도 미검증이다. [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

## P2.10 최소 시작 진단 사전 점검

기준 `cf6ab48d48fcb53717b239138e35aa7a2a5a97a0`. Windows LocalApplicationData Known Folder와 호스트 `LOCALAPPDATA`가 일치했고, `SystemDrive` 및 System32 `cmd.exe` 사전 점검도 통과했다. 경로 값은 기록하지 않았다. 현재 PATH에서 확인한 CLI 위치는 이전 관측과 다르고, 파일 버전 메타데이터가 없어 이전 `0.167.0-alpha.3.2`를 현재 버전으로 확정할 수 없다. 이 차이가 설치 버전 변경을 증명하지는 않는다. CLI 및 MXC를 실행하지 않았다.

별도 승인 후 확인된 절대 실행 파일에 대해 버전을 확인한다. 승인 범위의 바이너리와 프로필 계약을 확인할 수 없으면 MXC 호출 전에 중단한다. 새 GUID 기반 합성 `CODEX_HOME`, 임시 경로 및 빈 작업 디렉터리만 사용한다. 기존 P2.9의 제한 환경에서 Known Folder로 교차 확인한 `LOCALAPPDATA`를 신뢰 런처에 전달하며 값을 출력하지 않는다. 기존 저장 설정·인증·합성 자료는 건드리지 않는다. `--include-managed-config`를 유지하고 `mxc-file-test`의 `approval_policy = "never"`와 네트워크 비활성 정책이 적용되는지 확인한다. 후보 설정의 정적 확인만으로 유효 프로필을 인증하지 않는다.

내부 명령은 System32 절대 경로의 `cmd.exe /d /c echo MXC_OK` 한 번이다. 결과는 `MXC_OK`와 0 또는 종료 코드와 정제한 오류 유형만 보존한다. 실패 시 환경 확장·재시도·폴백 없이 중단한다. 경로 전달은 내부 명령의 환경에서 해당 경로가 숨겨졌다는 증거가 아니다. 성공하더라도 시작만 PASS이며 파일·네트워크·자격증명 및 비간섭은 NOT VERIFIED다. P2 A/B/C 전체 및 P3는 BLOCKED다.

## P2.9 런처와 내부 명령의 경계

기준 `c86d63b65e9b8baf4302c1f658975689371d440b`, 시험 당시 설치 CLI `0.167.0-alpha.3.2`. 아래 비교는 이전 영수증과 실행 인자를 검토한 결과다. 환경 값이나 파일 내용을 새로 수집하지 않았다.

| 관측 | 신뢰 런처 | 시험 범위와 결과 |
| --- | --- | --- |
| 공식 시작 진단 | 보통 호스트 셸 환경을 상속했고 별도 `CODEX_HOME`은 지정하지 않았다. 실제 상속 값은 계측하지 않았다. | `:workspace`와 고정 echo가 시작됨. `MXC_OK` 및 0. |
| 파일 시험 1회차 | 환경을 비우고 `SystemRoot`, `WINDIR`, `LOCALAPPDATA`, `ProgramData`, System32 `PATH`, 합성 `TEMP`·`TMP`·`CODEX_HOME`만 설정했다. | `mxc-file-test`에서 `SystemDrive` 요구로 준비 실패. |
| 파일 시험 2회차 | 검증한 `SystemDrive`를 추가했고 실제 `LOCALAPPDATA`는 제외했다. | 같은 시험 프로필에서 `LOCALAPPDATA` 요구로 준비 실패. |

두 파일 시험 모두 실제 파일 프로브 전에 종료됐다. 시작 진단과는 런처 환경 외에도 Codex 홈·프로필·실행 파일이 달랐다. 따라서 성공한 시작 진단에서 필수 변수 목록을 역추론할 수 없다. 현재 확인된 것은 해당 빌드가 준비 중 비어 있지 않은 유니코드 `SystemDrive`와 `LOCALAPPDATA`를 각각 요구했다는 사실이다. 공식 문서는 이 빌드의 전체 필수 목록, `USERPROFILE` 필요 여부 또는 합성 `LOCALAPPDATA` 지원을 보장하지 않는다. [OpenAI Windows 샌드박스](https://learn.chatgpt.com/docs/windows/windows-sandbox)

### 두 경계의 계약

- **신뢰 런처**는 바이너리와 실행 인자를 고정하고 환경을 비운 뒤 승인된 이름만 주입한다. `SystemDrive`는 Windows 시스템 디렉터리와 `SystemRoot`에 대조한다. `SystemRoot`, `WINDIR`, `ProgramData`, System32 `PATH`는 기존 부트스트랩 후보로만 유지하며 이 각각이 MXC 필수라는 증거는 없다. `TEMP`·`TMP` 및 `CODEX_HOME`은 새 GUID 루트의 승인된 경로에 둔다. 실제 `USERPROFILE`·`HOME`·`APPDATA`, 토큰·프록시·셸 사용자 설정은 상속하지 않는다. 공개 영수증에는 값 대신 변수명, 값의 출처 유형, 검증 결과를 남긴다.
- **`LOCALAPPDATA` 선택**은 별도 승인 항목이다. Windows의 현재 계정 Known Folder를 확인한 실제 `LOCALAPPDATA`는 신뢰 런처의 초기화 입력 후보로 제안한다. 일치 여부만 기록하고 해당 디렉터리의 내용은 읽거나 복사하지 않는다. 이것이 모든 MXC 버전의 필수 경로라는 증거는 없다. 합성 경로는 비어 있지 않은 문자열 검사를 만족할 수 있어도 내부 Known Folder 조회·경로 일치·쓰기 조건이 공개 문서에 고정돼 있지 않아 지원 여부는 UNKNOWN이다. 임의 리디렉션을 전제로 후속 시험을 승인하지 않는다. [Microsoft Known Folder ID](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)
- **`CODEX_HOME`**은 시험 전용 사용자 설정·상태의 위치로 사용한다. 기존 설정, 인증 파일, 로그, 규칙 및 플러그인을 복사하지 않는다. 이는 샌드박스 파일 접근 허용 목록이나 OS 사용자 프로필을 바꾸는 기능이 아니다. 이전 로컬 CLI 출력에 `CODEX_HOME` 아래 `tmp/arg0-windows` 임시 별칭 준비가 관측됐다. 합성 `TEMP`·`TMP`와 Codex 홈을 지정해도 내부 런타임과 브로커의 모든 파일 사용 위치가 제한된다고 단정할 수 없다. [OpenAI CLI 옵션](https://learn.chatgpt.com/docs/cli/reference)
- **MXC 내부 명령**의 환경 상속과 파일 권한은 별도로 확인한다. OpenAI가 문서화한 `shell_environment_policy`는 생성되는 명령의 상속·필터 설정이다. 이 설치 빌드의 직접 `codex sandbox` 경로에서도 동일한 필터가 적용되는지, MXC가 추가하는 변수가 무엇인지는 미검증이다. 실제 경로를 런처에 넣었다고 내부 환경에서 그 문자열이 숨겨진다고 가정하지 않는다. 반대로 경로 문자열이 노출돼도 OS 파일 접근 여부는 유효 프로필과 보호 표식의 접근 거부로 판정한다. 가짜 값만 사용하는 별도 승인된 검사로 상속을 확인하기 전에는 일반 명령·모델 입력을 열지 않는다. [OpenAI 설정 참조](https://learn.chatgpt.com/docs/config-file/config-reference) · [OpenAI 권한](https://learn.chatgpt.com/docs/permissions)
- **정책 합성**은 새 합성 홈과 비어 있는 작업 루트, 명시적 `windows.sandbox=mxc`, `--permission-profile`, `--include-managed-config`를 사용한다. 일반 설정의 기본값 및 CLI 덮어쓰기와 관리자 강제 요구사항을 구분한다. 정책 충돌, 신뢰되지 않은 프로젝트 설정, 추가 루트 또는 유효 정책이 불명확하면 중단한다. 명시적 MXC 실패에는 자동 폴백을 두지 않는다. [OpenAI 구성 우선순위](https://learn.chatgpt.com/docs/config-file/config-basic) · [관리 구성](https://learn.chatgpt.com/docs/enterprise/managed-configuration)

### 별도 승인 후의 최소 시작 진단

새 GUID 합성 홈·임시 디렉터리와 빈 작업 폴더만 만든다. 기존 자료와 저장 설정을 유지한다. 별도로 승인된 실제 `LOCALAPPDATA`를 신뢰 런처에만 입력하는 안을 먼저 검토한다. 그 범위를 승인하지 않으면 진단은 차단 상태다. 내부 명령은 환경을 출력하거나 파일을 여는 동작 없이 고정 echo 한 번으로 제한한다. 파일 시험과 같은 `mxc-file-test`의 파일·네트워크 정책을 적용해 부트스트랩을 비교한다. `cmd.exe`는 검증된 System32의 절대 경로를 사용한다.

```powershell
& $codex -c windows.sandbox=mxc sandbox --include-managed-config --permission-profile mxc-file-test -- $cmdExe /d /c echo MXC_OK
$LASTEXITCODE
```

예상 표식과 종료 코드 0은 그 조합의 시작만 증명한다. 실패 시 정제한 오류와 종료 코드만 기록하고 멈춘다. 새 환경 변수 추가, 폴백 및 재시도는 자동 실행하지 않는다. 성공 후에도 새 승인과 유효 경계 확인을 거쳐 일곱 경로 시험, 접근 거부와 전후 SHA-256 검사, 별도 승인된 네트워크·프로세스 비간섭 시험 순서로 진행한다. 전역 단일 실행·Win32 Job·기존 원장과 권한 계약은 유지한다. 실제 파일·네트워크·프로세스 격리와 P3는 BLOCKED다.

P2.8에서는 별도 승인된 시작 진단 1회에서 `MXC_OK`와 종료 코드 0을 관측했다. [합성 파일 시험](P2-MXC-FILE-TEST.md)은 별도 승인 대기이며 시작 관측으로 파일 경계를 판정하지 않는다.

기준 SHA `71abfd91f6ac80dcd46548e44d13a4b0866eaa76`. 현재 PC에서는 MXC를 우선 검토한다. VirtualBox 다운로드·설치·VM 계획은 보류한다. 이 문서는 승인 전 읽기 전용 점검과 시험 계약이며 런타임에는 새 실행 경로가 없다.

## 확인한 범위

- 호스트는 Windows 11 Home x64 25H2, 빌드 26200.9711이다. OpenAI 문서가 명시한 25H2의 MXC 도입 빌드 26200.9278 이상이다. OS 수치만으로 장치의 네이티브 기능 제공이나 정책 허용을 확정할 수 없다. [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)
- PATH의 설치 CLI는 `codex-cli 0.167.0-alpha.3.2`이고 로컬 도움말에서 sandbox 명령과 `--include-managed-config`, `--permission-profile`를 확인했다. `app-server`는 같은 CLI의 하위 명령이며 독립 `--version` 옵션을 받지 않았다. 별도 서버 프로세스를 시작하지 않았다. 이 알파 빌드는 시험 후보이며 실서비스용 고정 버전 승인이 아니다.
- 확인한 사용자 설정에는 `windows.sandbox = "unelevated"`가 저장되어 있었고 사용자 지정 권한 프로필 정의는 찾지 못했다. 표준 위치의 로컬 관리 요구사항과 이 저장소의 프로젝트 설정은 발견되지 않았다. 다른 설정 계층과 계정/조직 정책까지 해석하지 않았으므로 유효 구현·프로필은 아직 미확인이다. 버전·도움말 명령은 CLI의 임시 별칭 준비를 시도했고 실행 환경에서 접근이 거부됐다. 저장된 설정은 수정하지 않았다.
- 공식 구현은 `mxc`, `elevated`, `unelevated`이며, 문서의 기본 권한 프로필은 `:read-only`, `:workspace`, `:danger-full-access`다. `:workspace`에는 시스템 임시 경로 쓰기도 포함된다. 실제 허용 목록은 관리 정책과 버전별 설정으로 결정된다. DevOrchestra는 좁은 읽기/쓰기 프로필만 후보로 삼는다. `:danger-full-access`는 입장 금지다. [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions)

## 후보 비교

| 후보 | 장점·비용 | 판단 |
| --- | --- | --- |
| MXC | 네이티브 프로세스 격리. 관리자 초기 설정, 새 Windows 계정 및 로컬 방화벽 규칙이 필요하지 않다고 문서화돼 있다. 별도 게스트 OS를 부팅하지 않는다. | 우선 시험. 전체 장치 적용과 성능은 미측정. |
| elevated | 낮은 권한의 별도 샌드박스 사용자와 파일·방화벽 경계. 관리자 승인 초기 설정과 로컬 정책 변경이 필요하다. | 별도 설계 및 승인 후에만 재검토. |
| unelevated | 관리자 초기 설정 부담이 적다. 네트워크 격리가 더 약하고 denied-read 경로를 지원하지 않는다. | 현재 저장 선택이지만 DevOrchestra의 자동 대체 경로에서 제외. |
| 별도 Windows 사용자 | ACL 및 자격증명 분리를 보강할 수 있다. 계정·권한 설정과 수명주기 관리가 추가된다. | 단독으로 네트워크·데스크톱·자식 프로세스 경계를 입증하지 않는다. |
| VirtualBox VM | 게스트 OS 경계, 별도 매체 및 오프라인 반출 설계가 가능하다. 드라이버·라이선스·메모리·디스크·부팅 부담이 있다. | 보류. 이전 승인안은 이 단계에 적용하지 않는다. |

`prefer_mxc`는 레거시 폴백을 허용하므로 DevOrchestra의 강제 입장에 사용하지 않는다. 명시적 MXC 선택으로 실패를 노출한다. 향후 선택한 버전에서 관리 요구사항 `windows.require_mxc` 지원과 유효 정책을 확인한다. 이 필드는 MXC 선택과 별도로 폴백을 거부하는 용도다. 현재 사용자 설정 또는 조직 정책을 수정하지 않는다. [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox) · [OpenAI 설정 참조](https://learn.chatgpt.com/docs/config-file/config-reference)

## 문서상 경계와 입장 조건

| 영역 | 공식 문서의 범위 | DevOrchestra의 검증 |
| --- | --- | --- |
| 파일·권한 | 유효 프로필의 읽기·쓰기·네트워크 권한이 샌드박스 명령과 자식에 적용된다. MXC의 deny-read 글롭은 프로세스 시작 시점에 확장된다. | 복제본 하나와 꼭 필요한 런타임 읽기만 허용한다. 다른 합성 프로젝트, 원장, 권한 엔진, 설정, 자격증명은 정확한 루트 경계로 보호한다. reparse/junction·경로 교체·나중에 생성된 표식도 시험한다. 글롭만으로 경계를 승인하지 않는다. |
| 네트워크 | 관리 네트워크에는 유효 `allow_local_binding = true`가 필요하며 호스트 loopback 통신이 허용된다. 프록시 도메인 정책이 있어도 추가 사설망 목적지 검사는 제거된다. | 최초 시험은 네트워크 비활성 프로필로 제한한다. 합성 loopback 수신기만 승인된 때에 사용한다. 호스트 서비스 접근이 남거나 우회 가능하면 차단한다. 미래 모델 송신 정책과 애플리케이션 브로커는 별도 검증이 필요하다. |
| 다른 프로세스·데스크톱 | 네이티브 프로세스 격리와 명령 권한을 설명한다. | 타 프로세스·창·입력·화면의 모든 Win32 경계가 입증된 것으로 보지 않는다. 기능을 비활성화하고 필요 시 별도 승인된 합성 프로세스와 격리 데스크톱에서만 부정 시험한다. 그런 환경이 없으면 NOT VERIFIED를 유지한다. |
| 도구·자격증명 | 명령 샌드박스와 승인은 별개다. 웹·브라우저·Computer Use·MCP 등에는 별도 통제가 필요하다. | 상위 CLI/App Server 및 Controller를 신뢰 경계로 본다. 명령에 자격증명 환경변수·프로필·핸들을 상속하지 않는다. 외부 도구·커넥터·원격 도구를 끈다. Provider의 권한 요청은 권한 부여로 해석하지 않으며 최초 시험에서는 모두 거부한다. |
| 자식 회수 | 문서는 MXC의 포그라운드 명령 종료 시 남은 자식이 중지된다고 안내한다. Windows는 조건부 중첩 Job을 지원한다. | MXC와 기존 DevOrchestra 외부 Job의 결합은 미검증이다. 소유 CLI만 생성 시점에 Job에 편입하고 자식 귀속, 정상·취소·시간초과·장애 후 활성 0과 핸들 종료를 독립 확인한다. 별도 서비스·브로커에 위임된 실행도 포함한다. 입장 실패 시 breakaway, Job 우회 또는 타 프로세스 조회·종료를 사용하지 않는다. |

근거: [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox) · [OpenAI 권한](https://learn.chatgpt.com/docs/permissions) · [Microsoft 중첩 Job](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)

전역 프로젝트·작업·에이전트·실행 슬롯은 각각 하나다. 기존 합성 Job의 프로세스 상한을 실제 Codex 프로세스 수로 간주하지 않는다. 정책이나 자원 회수 증거가 불명확하면 다음 역할을 차단한다. A는 MXC 및 소유 트리 회수, B는 호스트 접근 및 비간섭 경계, C는 원장·Controller 보호와 검역으로 검증한다.

## 다음 승인과 시험 순서

**이미 완료한 첫 진단의 승인 범위는 빈 합성 프로젝트 디렉터리에서 공식 일회성 시작 진단 한 번이었다.** 사용자 저장 설정 변경이나 로그인 없이 관리 요구사항을 포함했다. OpenAI 공식 명령은 다음과 같다. [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

```powershell
codex -c windows.sandbox=mxc sandbox --include-managed-config --permission-profile :workspace -- cmd.exe /d /c echo MXC_OK
$LASTEXITCODE
```

`MXC_OK`와 종료 코드 0은 해당 조합의 명령 시작 증거다. 이후 시험은 별도 승인으로 다음 순서를 따른다.

1. 독립 합성 작업 공간의 허용 파일 읽기·쓰기 양성 대조.
2. 별도 합성 보호 파일의 직접·reparse 접근 거부. 원장·권한 설정 표식도 보호한다.
3. 승인된 자체 loopback 수신기의 양성 대조와 차단 모드 비교. 외부 요청은 보내지 않는다.
4. 미승인 도구·권한 승격 거부. 합성 자격증명 및 승인되지 않은 프로필 상속 검사.
5. 전역 슬롯과 외부 Job을 적용한 소유 트리 정상·취소·장애 종료. 프로세스 집계 불명 시 정지한다.
6. 감사 원장 쓰기 차단 및 합성 결과의 검역. 소유 작업이 종료된 뒤 승인 목록·크기·해시를 확인한다.

실제 앱·게임·안티치트·사용자 파일은 시험 대상에 포함하지 않는다. P3는 명시적 MXC와 고정 바이너리·유효 프로필, A/B/C 실측, 별도 데스크톱 비간섭 증거, 버전별 이벤트·사용량 정산과 제한 송신 승인까지 확인될 때만 별도 검토한다. 이 중 하나라도 입증되지 않으면 P3와 실제 Codex 실행은 BLOCKED다.
