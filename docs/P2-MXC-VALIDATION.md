# P2.7 Windows 네이티브 MXC 검토

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
