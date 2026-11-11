# DevOrchestra 0.3.0 Windows x64 RC

공식 Codex ZIP 등록과 ChatGPT 로그인, 로컬 실행 확인, Git 프로젝트 등록 및 독립 복제본 개발을 Command Center에서 안내합니다. Windows 기본 텍스트 검사는 복제본에 있는 일반 파일 하나의 내용을 기대 출력과 비교합니다. 재개 검토에는 추가 예산을 입력하는 화면이 있습니다.

포터블 ZIP에서 `DevOrchestra.exe`를 실행하세요. 첫 설치의 사용자 상태는 `%LOCALAPPDATA%\DevOrchestra\.ai-local`에 저장합니다. 기존 EXE 옆에 사용자 저장소가 있으면 그 위치를 계속 사용합니다. ZIP 및 EXE 무결성은 각각 ZIP의 SHA-256 파일과 내장 매니페스트로 확인할 수 있습니다.

## 알려진 제한

- 공식 Codex Windows x64 ZIP 0.167.1과 서명된 Git for Windows가 필요합니다.
- 별도 Windows 사용자 계정의 공식 로그인, 새 계정의 온라인 개발·취소·재개와 실제 Windows DPI 전환은 검증 대기입니다.
- 배포 EXE는 코드 서명 전 상태입니다. 제품 배포 라이선스와 GitHub Release 공개 게시 여부는 소유자가 결정합니다.
