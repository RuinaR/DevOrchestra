# P2.8 MXC 합성 파일 인수 시험

## P2.14 Python 시작 경로의 정적 분석

기준 `0d6b124ed6528004fbf7ea38fc2a1a8694f93441`. P2.10과 P2.13은 동일 SHA-256의 OpenAI 서명 CLI `0.167.0-alpha.3.2`, 명시적 MXC, `mxc-file-test`, 동일 런처 환경 변수 목록을 사용했다. 둘 다 새 합성 CODEX_HOME을 사용했으나 P2.10 작업 디렉터리는 비어 있었고 P2.13에는 프로브, 표식과 두 junction이 있었다. 성공한 P2.10 명령은 System32의 cmd.exe와 내장 echo였다. P2.13은 작업 공간 밖의 사용자별 Python 설치 경로에서 `python.exe -I -S -B .\probe.py`를 실행했다. 실제 LOCALAPPDATA는 신뢰 런처 초기화 입력이며 설치 디렉터리에 대한 읽기 또는 실행 권한을 부여하지 않는다.

승인된 Python 설치 파일의 메타데이터와 PE 정적 import만 확인했다. 현재 인터프리터는 x64 CPython 3.10.11이며 SHA-256은 `3cce33d75d6fdae4e004d0bdf149320b3147482a9caf370079dcb9c191a1b260`이다. 설치 루트의 python.exe는 같은 루트에 존재하는 python310.dll과 VCRUNTIME140.dll을 import한다. `Lib/encodings` 및 `Lib/ctypes`가 있고, `DLLs/_ctypes.pyd`는 같은 DLLs 디렉터리에 존재하는 libffi-7.dll에 의존한다. 확인 범위에서 python310.zip, python310._pth 및 pyvenv.cfg는 없었다. 사용자별 일반 설치의 배치와 일치한다. 정적 import는 실제 로더가 선택한 DLL 경로나 접근 결과를 보여주지 않는다. `-I`는 Python 환경·사용자 패키지 경로의 영향을 제한하고, `-S`는 site 자동 import를 끄며, `-B`는 .pyc 쓰기를 방지한다. 이 옵션들은 실행 이미지, DLL 또는 필수 표준 라이브러리 접근을 없애지 않는다. [Python 3.10 CLI](https://docs.python.org/3.10/using/cmdline.html) · [Microsoft DLL 검색 순서](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order)

가장 유력한 가설은 작업 공간 밖 Python 실행 이미지 또는 앱 로컬 DLL에 대한 접근이 MXC에서 거부됐다는 것이다. 프로필의 `:root` 거부와 `:minimal` 예외에는 해당 Python 설치에 대한 명시적 허용이 없다. 공식 문서는 최소 읽기 집합을 Codex가 결정한다고 설명하며 이 설치가 포함되는지는 확인되지 않았다. PATH를 System32로 고정해도 실행 파일 옆 DLL에 읽기 권한을 부여하지 않는다. P2.13의 `0xC0000022`는 대상별 Win32 오류가 아니며, Python 생성 거부, 로더·DLL 거부, 표준 라이브러리·프로브 초기화 접근 거부, MXC 프로세스 정책 충돌 또는 다른 초기화 오류를 구분하지 못한다. 두 실행의 작업 공간 배치도 달라 단일 변수 비교가 아니다. [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions)

### 별도 승인으로 진행할 최소 진단

각 단계는 직전 단계가 PASS이고 해당 단계의 실행이 별도 승인된 경우에만 한 번 실행한다. P2.10의 고정 CLI 해시·서명, 환경 변수 목록, 전역 슬롯, 명시적 MXC·기존 프로필을 유지한다. 새 합성 CODEX_HOME과 작업 폴더를 사용하고 승인된 Python 경로·해시를 재확인한다. 관리 정책·작업 루트·경로 또는 종료가 불명확하거나, 종료 코드가 0이 아니거나, 예상 출력이 없으면 중단한다. 정제된 표준 오류와 종료 코드만 기록하고 권한 확대·폴백·재시도를 하지 않는다.

| 단계 | 고정 내부 명령 | PASS 범위 |
| --- | --- | --- |
| 1 | 승인된 절대 python.exe 경로와 `-V` | `Python 3.10.11` 및 종료 0. 버전 출력 경로만 확인 |
| 2 | 동일 Python, `-I -S -B -c "print('MXC_PY_OK')"` | 표식 및 종료 0. 명령에 파일 접근 코드는 없으며 인터프리터 자체의 초기화 읽기는 여전히 필요 |
| 3 | 동일 Python, `-I -S -B -c "print(open('allowed.txt', 'rb').read(1).hex())"` | 새 합성 표식의 예상 첫 바이트와 종료 0. 허용 파일 읽기만 확인 |
| 4 | 호스트 양성 대조 1회 후 복원, 기존 일곱 경로 MXC 프로브 1회 | 파일별 Win32 결과와 해시·junction 검사로 판정 |

다음 승인 요청은 1단계에만 해당한다. 1단계 PASS도 표준 라이브러리·ctypes·파일 경계를 검증하지 않는다. 실패 시 동일 조건 재실행 대신 합성 전용 런타임의 배치·의존성·허용 경로를 정적으로 검토할 수 있다. 다운로드·설치·복사·실행은 별도 승인 전 차단한다. 이번에는 Python과 MXC 실행 0회다. P2.10 시작 PASS 및 기존 증거는 유지하며 B 파일 경계와 C 원장 보호는 NOT VERIFIED, P2 A/B/C 전체와 P3는 BLOCKED다.

## P2.13 승인된 파일 시험 결과

기준 `53e446e095dc1f954c15ba7021d3b35b07230b90`. P2.10과 동일한 OpenAI 서명 CLI 바이너리(`0.167.0-alpha.3.2`, 동일 SHA-256), 명시적 MXC, `mxc-file-test` 및 제한 런처 환경을 사용했다. 새 GUID 루트와 합성 홈을 만들고 전역 뮤텍스를 유지했다. 고정 Python 프로브에서 `CreateFileW`로 `GENERIC_READ`와 `GENERIC_WRITE`를 별도로 요청하고 각각 한 바이트를 읽거나 썼다. 기존 파일만 열도록 `OPEN_EXISTING`을 사용했다. 프로브가 실행되면 API별 Win32 오류를 기록한다.

호스트 양성 대조 1회는 종료 0으로 일곱 경로의 읽기·쓰기가 모두 성공했다. 초기 표식을 복원한 뒤 MXC 파일 시험 1회를 호출했다. MXC 호출은 종료 코드 `-1073741790` (`0xC0000022`), 표준 오류 없음, 대상 결과 0건이었다. 이 코드는 호출의 종료 코드이며 개별 파일 접근의 Win32 오류 코드가 아니다. Python 시작과 첫 결과 출력 사이 어느 단계에서 종료됐는지는 판별되지 않았다. 권한을 넓히거나 재시도하지 않았다.

| 합성 경로 | 호스트 읽기 / 쓰기 | MXC 읽기 / 쓰기 | 전후 SHA-256 |
| --- | --- | --- | --- |
| 허용 파일 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 내부 junction | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 보호 파일 상대 경로 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 같은 보호 파일 절대 경로 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 가짜 원장 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 가짜 자격증명 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 외부 junction | 성공 / 성공 | 미측정 / 미측정 | 동일 |

| 보호 표식 | 전후 동일 SHA-256 |
| --- | --- |
| 보호 파일, 상대·절대 경로 공통 | `ec6e37a6467c6b6b74ddce398f6feb7dfd15eb4d03495eb0153dd3c45464fa87` |
| 가짜 원장 | `a73039c22a9408b177195a1e0e50e3341364c18dd15cca2a0efef6da652e968b` |
| 가짜 자격증명 | `bf583f7e90ab3624e7483cf5bf9052f795614cc7c8134da0fc895e2b425093ef` |
| 외부 junction 대상 표식 | `12e0845a93e8fd18ed376c0ef15e238cb67d248a648acac83bf6fee28339d78e` |

junction 두 개의 종류와 대상, 프로브 해시가 불변이었다. 확인된 새 표식·프로브·설정과 junction 자체를 제거했다. 새 디렉터리는 빈 경우만 제거했고 이전 자료는 보존했다. 호스트 대조 PASS, 이번 MXC 호출은 비정상 종료, B 파일 경계와 C의 MXC 원장 보호는 NOT VERIFIED다. A의 기존 증거와 C의 합성 검역 증거만 유지한다. 다음은 Python 런타임과 필요한 DLL에 대한 해당 프로필의 시작 조건을 정적으로 확인하는 일이다. 추가 실행은 별도 승인 대상이다. P2 A/B/C 전체와 P3는 BLOCKED다.

P2.9부터 [런처 환경 계약과 최소 시작 진단](P2-MXC-VALIDATION.md#p29-런처와-내부-명령의-경계)을 선행한다. 아래 파일 시험 기록과 명령은 보존하지만, 시작 진단 결과와 별도 파일 시험 승인 전에는 일곱 경로를 실행하지 않는다. 합성 `LOCALAPPDATA`는 호환성이 미검증이며 현재 시험 계약의 확정값이 아니다.

## 2026-11-09 별도 승인 재시험

기준 `d14c0b8ab9b73e0827e18d70e5be599574580ef6`. 새 GUID 루트에서 호스트 양성 대조 1회는 종료 코드 0으로 일곱 대상의 읽기·쓰기가 모두 성공했다. 표식의 초기 바이트·SHA-256을 복원했다. Windows 시스템 디렉터리, `SystemRoot`, 호스트 `SystemDrive`에서 동일한 드라이브를 교차 검증해 명시적 MXC 시험에만 전달했다. 실제 `LOCALAPPDATA` 및 사용자 프로필은 전달하지 않았다.

MXC 시험 1회는 준비 단계에서 종료 코드 1, `MXC requires nonempty Unicode host LOCALAPPDATA`를 반환했다. 대상별 접근 결과와 Windows 오류 코드는 출력되지 않았다. `LOCALAPPDATA`를 추가하지 않았고 재시도하지 않았다.

| 대상 | 호스트 읽기 / 쓰기 | MXC 읽기 / 쓰기 | 전후 SHA-256 |
| --- | --- | --- | --- |
| 허용 파일 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 내부 junction | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 보호 파일 `..` 경로 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 같은 보호 파일 절대 경로 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 가짜 원장 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 가짜 자격증명 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 외부 junction | 성공 / 성공 | 미측정 / 미측정 | 동일 |

두 junction의 대상이 불변임을 확인했다. 검증된 새 합성 파일과 junction만 정리했으며 빈 디렉터리로 확인되지 않은 새 Codex 홈과 상위 루트, 기존 시험 자료는 보존했다. 호스트 대조 PASS, MXC 시작 FAIL, B 파일 접근 거부 및 C의 MXC 원장 보호 NOT VERIFIED다. A 기존 증거와 C 합성 검역 PASS를 유지하며 P3는 BLOCKED다. 후속 검토 후보는 실제 사용자 경로를 노출하지 않는 합성 `LOCALAPPDATA`이며, 이 후보의 동작은 미검증이다.

## 재시험용 최소 환경 수정안

기준 `fe1f1e2dc145833398ee2ffe9ceed11648ca362d`. 이전 런처가 자식 환경을 비운 뒤 명시적으로 복원한 목록에 `SystemDrive`가 없었다. 설치 CLI `0.167.0-alpha.3.2`의 준비 오류가 이 변수의 필요성을 직접 보여 준다. OpenAI Windows 문서는 시작 진단과 장치 요구사항을 안내하며, 검토한 문서에는 이 빌드의 최소 호스트 환경 변수 목록이 없다. 아래 수정안의 부팅 성공 여부는 미검증이다. [OpenAI Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

기존 합성 루트와 `codex-home`은 해당 경로 및 직계 항목의 메타데이터만 읽어 확인했다. 두 디렉터리는 reparse point가 아니었고 각각 하위 디렉터리 한 개가 있었다. 내부 파일 내용 열기, 삭제, 변경은 하지 않았다. 기존 자료는 별도 정리 승인까지 보존한다.

새 승인 후 런처는 Windows가 보고한 시스템 디렉터리에서 시스템 드라이브를 구한다. 로컬 드라이브 문자이고 호스트 `SystemRoot`의 드라이브 및 호스트 `SystemDrive`와 일치하는지 검사한다. 확인된 호스트 값은 세 경로가 일치했다. 새 합성 루트는 운영 저장소 밖의 새 GUID 경로만 허용하고 상위 경로, reparse point, 기존 파일 및 다른 프로젝트 루트와의 중첩을 다시 확인한다. 이전 시험 폴더는 재사용하지 않는다.

```powershell
$systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
$systemDrive = [IO.Path]::GetPathRoot($systemDirectory).TrimEnd('\')
if ($systemDrive -notmatch '^[A-Za-z]:$' -or
    $systemDrive -ne [IO.Path]::GetPathRoot($env:SystemRoot).TrimEnd('\') -or
    $systemDrive -ne $env:SystemDrive) { throw 'Host SystemDrive evidence mismatch' }
$info.Environment['SystemDrive'] = $systemDrive
```

`SystemDrive`만 새 변수로 추가한다. 기존 런처의 `SystemRoot`·`WINDIR`는 Windows 구성요소 경로, `ProgramData`는 관리 정책 경로, `PATH`는 확인된 System32, `TEMP`·`TMP`·`CODEX_HOME`은 새 합성 루트의 경로로 제한한다. 실제 `LOCALAPPDATA`, `USERPROFILE`, 자격증명·토큰 등 사용자 환경은 전달하지 않는다. 이 목록은 보수적인 시험 런처 계약이며 각 변수의 제품 요구사항이 모두 입증됐다는 뜻은 아니다. 호스트 양성 대조의 Python에는 Codex 전용 변수를 전달하지 않는다.

이번에는 읽기 전용 점검과 수정안 문서화만 했다. 새 승인에는 합성 루트 준비, 양성 대조 1회, 복원, MXC 시험 1회와 종료 후 해시·junction 확인 및 검증된 새 자료만의 정리가 포함돼야 한다. 준비 오류가 재발하면 메시지와 종료 코드만 기록하고 종료한다. 새 변수를 자동 추가하거나 재시도하지 않는다. 불완전한 대상 결과나 불명확한 오류는 NOT VERIFIED다. 기존 A 증거와 C 합성 검역은 유지하며 B 파일 경계 및 C의 MXC 원장 보호는 NOT VERIFIED, P3와 실제 Codex 연결은 BLOCKED다.

## 2026-11-09 실행 결과

시험 기준 `96125464afb62848999647e1aaa5fa578c1dfcef`. 새 합성 루트와 내부·외부 junction을 확인했다. 동일 Python의 호스트 양성 대조 1회에서 일곱 경로 모두 읽기·쓰기 성공, 종료 코드 0을 관측했다. 초기 바이트와 해시를 복원한 후 MXC 시험을 1회 실행했다.

MXC 종료 코드는 1이며 준비 오류는 `MXC requires nonempty Unicode host SystemDrive`였다. 시험 런처의 최소 환경에 `SystemDrive`를 전달하지 않았다. 대상별 Windows 오류 코드나 접근 결과는 출력되지 않았다. 해시와 junction 대상이 그대로였지만 이는 접근 거부 실측이 아니다. 확인된 표식과 junction은 정리했고, 빈 디렉터리로 확인되지 않은 시험용 Codex 홈과 상위 합성 루트는 보존했다. 재시도는 수행하지 않았다.

| 대상 | 호스트 읽기 / 쓰기 | MXC 읽기 / 쓰기 | 전후 SHA-256 |
| --- | --- | --- | --- |
| 허용 파일 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 내부 junction | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 보호 파일 `..` 경로 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 같은 보호 파일 절대 경로 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 가짜 원장 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 가짜 자격증명 | 성공 / 성공 | 미측정 / 미측정 | 동일 |
| 외부 junction | 성공 / 성공 | 미측정 / 미측정 | 동일 |

A의 이전 시작 진단과 제한 Job 증거만 유지한다. B 파일 경계와 C의 MXC 원장 보호는 NOT VERIFIED다. C의 기존 합성 검역 PASS는 유지한다. P3와 실제 Codex 자동 개발은 BLOCKED다. 재시험에는 `SystemDrive`가 포함된 최소 환경 검토와 별도 승인이 필요하다.

기준 `ac306f4ef05e0e939b4e9cf5c7f9c5f4794f90f4`. 앞선 별도 승인에서 명시적 MXC와 `:workspace`로 `MXC_OK` 및 종료 코드 0을 관측했다. 이는 명령 시작만 PASS다. 이 문서의 프로필, 코드와 명령은 아직 실행하지 않았다.

## 승인 범위와 배치

별도 승인 후에만 저장소 밖 `%LOCALAPPDATA%\DevOrchestra-Lab\mxc-file-<GUID>`에 일회성 루트를 만든다. 루트와 모든 상위 구성 요소가 승인 경로 안의 일반 로컬 디렉터리인지 확인한다. 기존 경로는 재사용하지 않는다. 다른 DevOrchestra 실행과 겹치지 않도록 기존 전역 입장 계약을 적용하고, 소유 실행과 종료가 확인되지 않으면 다음 시험을 시작하지 않는다. Win32 Job 결합 실측은 별도 시험이다.

| 위치 | 내용 |
| --- | --- |
| `work\` | 유일한 작업 루트. `probe.py`, `allowed.txt`, `inside\linked.txt` |
| `protected\` | `outside.txt`, `fake-ledger.txt`, `fake-credential.txt`, `linked.txt` |
| `codex-home\` | 테스트 전용 설정. 실제 계정·자격증명·기존 설정은 복사하지 않는다. |

각 표식은 짧은 합성 바이트를 넣은 일반 파일이다. `work`에 아래 두 junction만 호스트 준비 단계에서 만든다. 명령은 승인된 새 루트에서만 사용한다. 생성 실패나 권한 상승 필요 시 junction 시험을 차단한다.

```powershell
New-Item -ItemType Junction -Path (Join-Path $work 'inside-link') -Target (Join-Path $work 'inside')
New-Item -ItemType Junction -Path (Join-Path $work 'outside-link') -Target $protected
```

junction의 대상과 태그를 검사하고 다른 reparse point나 하드링크가 없는지 해당 합성 루트만 확인한다. 시험 전후 경로 정규화와 파일 해시는 신뢰 호스트에서 확인한다. 현재 계획은 junction 및 정적인 `..` 경로만 시험한다. symlink, 다른 reparse 태그 및 경로 교체 경쟁은 별도 승인·시험이 필요하다. 실제 Windows 시스템 파일·레지스트리 및 사용자 파일을 열지 않으므로 **시스템 경로 거부는 NOT VERIFIED**다.

## 후보 프로필

아래 내용은 별도 합성 `CODEX_HOME`의 `config.toml`에만 둔다. 일반 사용자 설정이나 관리 정책을 변경하지 않는다. 합성 작업 공간 외 추가 유효 workspace root가 있거나 관리 정책과의 합성이 불명확하면 시험을 중지한다. 공식 예시의 기본 거부와 최소 런타임 읽기를 사용하며 시스템 임시 쓰기를 제외한다. [OpenAI 권한 프로필](https://learn.chatgpt.com/docs/permissions)

```toml
default_permissions = "mxc-file-test"
approval_policy = "never"

[permissions.mxc-file-test]
extends = ":workspace"

[permissions.mxc-file-test.filesystem]
":root" = "deny"
":minimal" = "read"
":tmpdir" = "deny"
":slash_tmp" = "deny"

[permissions.mxc-file-test.network]
enabled = false
```

## 고정 시험 코드

아래 스크립트를 `work\probe.py`로 저장하는 안이다. 파일 원문이나 보호 바이트는 출력하지 않는다. 호스트가 미리 존재와 쓰기 가능 여부를 확인한 합성 파일만 연다. 쓰기는 파일을 새로 만들거나 잘라내지 않고 첫 바이트를 합성 바이트로 덮는다. 차단 대상이 열리면 FAIL 판정에 필요한 결과를 남기고 합성 파일만 바뀐다.

```python
import json
import os
import sys

targets = (
    ("allowed", "allowed.txt"),
    ("inside_junction", "inside-link/linked.txt"),
    ("outside", "../protected/outside.txt"),
    ("outside_absolute", os.path.abspath("../protected/outside.txt")),
    ("fake_ledger", "../protected/fake-ledger.txt"),
    ("fake_credential", "../protected/fake-credential.txt"),
    ("outside_junction", "outside-link/linked.txt"),
)

def attempt(path, write):
    try:
        with open(path, "r+b" if write else "rb") as stream:
            if write:
                stream.write(b"X")
                stream.flush()
            else:
                stream.read(1)
        return {"status": "OK"}
    except OSError as error:
        return {"status": "OS_ERROR", "winerror": getattr(error, "winerror", None),
                "errno": error.errno}

if len(sys.argv) != 1:
    raise SystemExit(2)
for label, path in targets:
    print(json.dumps({"target": label, "read": attempt(path, False),
                      "write": attempt(path, True)}), flush=True)
```

## 실행 순서와 판정

1. 소유자 승인 후 절대 경로로 확인한 동일 Python과 동일 스크립트를 `work`에서 **호스트 양성 대조 1회** 실행한다. 명령은 `& $python -I -S -B .\probe.py`다. 일곱 대상의 읽기·쓰기가 모두 성공해야 한다. 표식을 원래 합성 바이트로 복원한 후 파일 존재, 태그, 해시 및 스크립트 해시를 기록한다. 테스트 Python과 표준 라이브러리가 승인된 런타임에서 읽힐 수 있어야 한다.
2. `work`를 현재 디렉터리로 지정하고 다음 **MXC 파일 시험 1회**만 실행한다. `$python`은 사전 승인한 절대 실행 파일 경로다. `$testHome`은 위 합성 설정 폴더다. 실행기 환경은 자격증명이 없는 최소 환경으로 제한한다. 아래 변수 설정은 해당 시험 셸에만 적용한다. 저장 설정은 변경하지 않는다.

```powershell
$env:CODEX_HOME = $testHome
codex -c windows.sandbox=mxc sandbox --include-managed-config --permission-profile mxc-file-test -- $python -I -S -B .\probe.py
$LASTEXITCODE
```

3. 프로필 또는 런타임이 거부되거나 완전한 일곱 결과가 없으면 NOT VERIFIED다. 종료 코드 0만으로 PASS를 판정하지 않는다. 허용 파일과 내부 junction의 읽기·쓰기는 성공해야 한다. 보호 표식과 외부 junction은 읽기·쓰기가 모두 Windows 접근 거부를 보여야 한다. `winerror=5` 또는 동일 Python 런타임에서 접근 거부로 확인된 `errno=EACCES`를 근거로 삼는다. 파일 부재, 경로 오류, 기타 오류는 원인이 구분될 때까지 NOT VERIFIED다. 보호 대상 접근 성공은 FAIL이다.
4. 실행 종료 후 신뢰 호스트가 승인 목록과 전후 해시를 대조한다. 보호 표식은 불변이어야 하고, 허용 파일의 예상 바이트 변경만 허용한다. 태그와 대상도 불변이어야 한다. 로컬에는 최소 영수증만 남긴다. 정리 시 junction 자체를 먼저 비재귀적으로 제거하고, 승인된 합성 파일만 제거한다. 불명확하면 보존한다.

P2.8에서는 승인 범위와 명령만 확정했다. A에는 기존 제한 Job 실측과 MXC 시작 관측만 있으며 MXC/외부 Job 결합은 미검증이다. B 파일 시험은 별도 승인 대기다. C의 기존 합성 검역 PASS는 유지하며 MXC 원장 보호는 미검증이다. 네트워크, 실제 시스템 파일, 실제 Codex 모델 호출 및 P3는 계속 차단한다. [MXC 전체 인수 계획](P2-MXC-VALIDATION.md)
