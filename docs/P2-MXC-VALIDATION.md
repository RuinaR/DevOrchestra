# P2.7 Windows 네이티브 MXC 검토

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
