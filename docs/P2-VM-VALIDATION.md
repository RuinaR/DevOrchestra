# P2.5 격리 실행 환경과 인수 시험

기준 DevOrchestra `1519b7874d6e6716aef33746b94d7b79adb24ece`. 이 문서는 실행 계획이다. VM, 반출기, 네트워크 브로커는 아직 연결되지 않았다.

## 환경 결정

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