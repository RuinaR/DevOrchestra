# DevOrchestra 설계 근거 및 검토 출처

기준일: 2026-11-08 KST. 본 문서는 공식 문서와 실제 사용자 프로젝트에서 확인한 개발 안전 경계를 기록한다. 사용한 참조가 기능 존재·OS 권한 강제의 실측 증명은 아니다.

## 1. OpenAI 공식 문서

- [Codex Prompting Guide — Using agents.md](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide): CLI가 전역 및 저장소/하위 디렉터리의 AGENTS 지침을 읽는 경로·우선순위 설명. **지침 로딩은 OS 샌드박스가 아님**.
- [OpenAI Agents API — Sandbox security](https://developers.openai.com/api/docs/guides/agents-api/environments/security): 네트워크 제한, 자격증명 분리, 승인과 격리의 경계를 설명. 자동 승인이 네트워크/키 제한을 대체하지 않음을 명시.
- [OpenAI Agents API — Automatic review](https://developers.openai.com/api/docs/guides/agents-api/sessions/approvals): 위험 도구 실행·파일 변경·권한 확장 요청에 승인 요청이 발생할 수 있음. **사용자 승인 대신 모델의 자동 판단을 신뢰 경계로 쓰지 않는다**.

## 2. GitHub 공식 문서

- [Push protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection): 지원되는 비밀 값이 Git으로 전송되는 것을 방어. 미지원 패턴·크기 한계가 있어 보조 방어로만 활용.
- [Secret leakage risks](https://docs.github.com/en/code-security/concepts/secret-security/secret-leakage-risks): 비밀 최소화·유출 감지·push protection, 유출 후 폐기/교체 필요.
- [GitHub MCP push protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection-and-the-github-mcp-server): GitHub MCP를 통한 파일 생성/변경도 비밀정보 노출 위험이 있는 쓰기 작업. 원문 커밋 전 자체 스캔 필수.

## 3. 사용자 프로젝트에서 재사용한 안전 규칙 (2026-11-08 확인)

- [EchoMate/AGENTS.md](https://github.com/RuinaR/EchoMate/blob/main/AGENTS.md): 라이브 Discord 운영과 합성 SDK 구분, 사용자별 동의·출처·철회·삭제, 모델/캐릭터 버전과 권한, 운영 DB 보호, 문서 인수인계.
- [EchoMate/MASTER-VISION.md](https://github.com/RuinaR/EchoMate/blob/main/docs/MASTER-VISION.md): 장기 목표 유지, 검증된 능력과 연구 가설 분리.
- [AITrader/AGENTS.md](https://github.com/RuinaR/AITrader/blob/main/AGENTS.md): 합성·검증 회계와 실제시장/실금전 차단, 재현 가능한 감사·위험·원장 및 재개 기준. 이 저장소는 Private이므로 공개 사용자가 링크에 접근할 수 있는 것으로 가정하지 않는다.
- [MarketCrate/AGENTS.md](https://github.com/RuinaR/MarketCrate/blob/main/AGENTS.md): 게임과 타 프로세스 비간섭, Steam 계정·Guard 및 실제 판매 승인 경계, 미확정/Unknown 후 임의 재시도 금지.
- AVManager/LumiPeek 등 기존 다른 작업 맥락: 격리된 테스트·원본 보호·QA 미실행 구분·사용자 환경 비간섭을 참고하되, 현재 저장소의 개별 AGENTS는 전부 직접 검증한 것으로 주장하지 않는다.

## 4. 해석상 한계

- `.gitignore`는 추적 이전 파일의 기본 포함을 줄일 뿐 이미 Git에 들어간 비밀을 제거하지 못한다.
- 정책 문서의 파일/네트워크 allowlist가 실제 OS에서 강제된다고 보장하지 않는다. 구현된 sandbox/VM/격리 사용자의 권한 실패 시험이 필요하다.
- Codex CLI/SDK, GitHub 앱, Windows 환경 기능은 버전·인증 경로별로 차이가 있을 수 있다. 사용 가능한 설정·잔여 한도·실행 권한은 직접 조회 후 표시해야 한다.
- 에이전트 수를 늘려도 검토 정확도가 자동으로 보장되지 않는다. 독립 코드 검토와 결정적 테스트·반례 평가를 함께 실행한다.
