# save-your-memory

로컬 하드디스크에 흩어진 문서를 검색 가능한 Markdown 위키로 컴파일하고, GitHub Copilot 또는 Codex가 근거 링크와 함께 답변하도록 만드는 로컬 우선 지식 관리 플러그인입니다.

- 원본 파일은 읽기 전용으로 유지
- Windows 폴더 전체를 증분 인덱싱
- SQLite FTS5 external-content 청크 검색과 파일별 Markdown 위키 생성
- VS Code GitHub Copilot Agent Plugin 지원
- Codex skills-only plugin 지원
- 외부 LLM API 키 없이 Copilot/Codex를 답변 생성 계층으로 사용

## 왜 만들었나

자료를 폴더와 파일로 오래 보관하다 보면 다음 문제가 반복됩니다.

- 어떤 정보가 어느 파일에 있는지 기억하기 어렵습니다.
- 파일을 찾더라도 문서가 크면 필요한 내용을 다시 탐색해야 합니다.
- 같은 주제를 여러 폴더와 형식에서 반복 검색하게 됩니다.
- 답변을 찾은 뒤에도 그 근거가 어느 원본인지 확인하기 어렵습니다.

`save-your-memory`는 원본을 변경하지 않고, 파일 경로·메타데이터·읽을 수 있는 내용을 별도 위키와 검색 인덱스로 컴파일합니다.

## 동작 구조

```text
SAVE_YOUR_MEMORY_ROOT              원본 자료, 읽기 전용
          │
          ├─ 안전한 재귀 스캔
          ├─ size/mtime 기반 증분 변경 감지
          ├─ 텍스트·Office·PDF 내용 추출
          ▼
SAVE_YOUR_MEMORY_HOME              생성 데이터
          ├─ index.sqlite3         SQLite FTS5 카탈로그
          ├─ AGENTS.md             위키 운영 계약
          └─ wiki/
              ├─ index.md          탐색 시작점
              ├─ overview.md       상태 요약
              ├─ log.md            ingest/query 이력
              └─ sources/*.md      파일별 내용과 provenance
```

질문 흐름은 다음과 같습니다.

```text
사용자 질문 → FTS5 검색 → 관련 wiki 페이지 → Copilot/Codex 답변
                                           └→ 원본 파일 링크
```

## 주요 기능

### 안전한 인덱싱

- 원본을 수정·이동·삭제하지 않습니다.
- symlink, junction, Windows reparse point를 따라가지 않습니다.
- 생성 위키가 원본 아래에 있어도 다시 인덱싱하지 않습니다.
- 파일 변경이 없으면 내용을 다시 추출하지 않습니다.
- `venv`, `site-packages`, `build`, `dist`, CSV 같은 캐시/배포성 파일은 기본 제외됩니다.
- 삭제된 원본은 플러그인이 관리하는 카탈로그와 위키 페이지만 정리합니다.

### 내용 추출

- UTF-8, CP949, UTF-16 등 일반 텍스트 인코딩
- Markdown, JSON, XML, HTML, 소스 코드와 설정 파일
- 확장자가 없거나 생소해도 text sniffing을 통과한 파일
- DOCX, PPTX, XLSX의 ZIP/XML 텍스트
- `pdftotext` 또는 선택적으로 설치된 PyMuPDF를 이용한 PDF 텍스트
- 추출된 본문은 파일 전체가 아니라 SQLite external-content FTS5 청크로 저장합니다.

바이너리·이미지·동영상·손상 문서처럼 의미 있는 텍스트를 추출할 수 없는 파일도 경로, 크기, 수정 시각, 추출 상태를 가진 위키 페이지로 남습니다.

### 저장 공간 최적화

- `catalog_entries`에는 파일 경로와 상태 같은 메타데이터만 저장합니다.
- 추출 본문은 UTF-8 기준 기본 32 KiB의 토큰 안전 청크로 나눠 `catalog_chunks`에 한 번 저장합니다.
- `catalog_search`는 `catalog_chunks`를 참조하는 external-content FTS5 인덱스이므로 본문 사본을 위한 `catalog_search_content` 테이블을 만들지 않습니다.
- 한 파일의 여러 청크가 검색되더라도 파일별 최상위 결과 하나로 묶습니다.
- 검색어가 청크 경계 양쪽에 나뉘면 파일 단위 교집합 검색으로 보완합니다.
- 변경되지 않은 파일은 청크를 다시 읽거나 Markdown 페이지를 다시 생성하지 않습니다.

`SAVE_YOUR_MEMORY_CHUNK_BYTES`는 목표 크기입니다. 하나의 검색 토큰이 목표보다 길면 검색 가능성을 보존하기 위해 해당 토큰을 자르지 않습니다.

### 지속 가능한 위키

- 파일별 안정적인 Markdown 페이지 이름
- 원본 절대 경로와 상대 경로
- 추출 방식과 상태
- SHA-256 내용 해시
- append-only ingest/query 로그
- 누락·고아 페이지 lint

## 요구 사항

- Windows 10/11
- Python 3.11 이상
- SQLite FTS5가 포함된 Python
- 선택 사항: `pdftotext` 또는 PyMuPDF
- AI 답변 사용 시 GitHub Copilot 또는 Codex

핵심 Python 런타임에는 필수 서드파티 패키지가 없습니다.

## 빠른 시작

### 1. 저장소 복제

```powershell
git clone https://github.com/m1nd0322/save-your-memory.git
cd save-your-memory
```

### 2. 환경 설정

```powershell
Copy-Item .env.example .env
```

`.env`에 자신의 로컬 경로를 입력합니다. 실제 `.env`는 Git에 포함되지 않습니다.

```dotenv
SAVE_YOUR_MEMORY_ROOT="D:\Documents\Knowledge"
SAVE_YOUR_MEMORY_HOME="D:\SaveYourMemoryData"
SAVE_YOUR_MEMORY_MAX_FILE_BYTES=26214400
SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORIES="vendor, third_party"
SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORY_GLOBS="cache-*"
SAVE_YOUR_MEMORY_EXCLUDED_EXTENSIONS=".csv, .tsv"
SAVE_YOUR_MEMORY_CHUNK_BYTES=32768
```

`SAVE_YOUR_MEMORY_HOME`은 원본 폴더 밖의 별도 경로를 권장합니다.
`SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORIES`, `SAVE_YOUR_MEMORY_EXCLUDED_DIRECTORY_GLOBS`, `SAVE_YOUR_MEMORY_EXCLUDED_EXTENSIONS`는 기본 제외 목록에 추가됩니다. 기본값은 `venv`, `.venv`, `site-packages`, `build`, `dist`, `downloads`, `vendor`, `third_party`, `*.git`, `.csv` 등을 제외합니다.

기본 제외 규칙은 항상 유지되며 위 환경변수는 제외 대상을 추가합니다. CSV를 포함한 대용량 데이터 파일을 검색해야 한다면 필요한 형식만 선별적으로 허용하는 별도 설정 기능이 추가되기 전까지 원본 범위를 더 작은 폴더로 나누는 방식을 권장합니다.

이 버전은 이전 파일 전체/contentful FTS5 DB와 스키마가 다릅니다. 이전 `index.sqlite3`가 남아 있다면 생성 데이터 폴더를 백업 또는 비운 다음 `index`를 다시 실행해야 합니다. 인덱서는 기존 DB를 자동 삭제하지 않습니다.

### 3. 최초 인덱싱

```powershell
python scripts/save_your_memory.py --env-file .env index --json
```

첫 실행은 자료량에 따라 오래 걸릴 수 있습니다. 다음 실행부터는 변경된 파일만 처리합니다.

### 4. 검색

```powershell
python scripts/save_your_memory.py --env-file .env query "찾고 싶은 주제" --limit 5 --json
```

CLI `query`는 검색 근거를 JSON으로 반환합니다. GitHub Copilot 또는 Codex 플러그인을 사용하면 이 결과를 읽고 자연어 답변과 출처 링크를 생성합니다.

## VS Code GitHub Copilot에서 설치

이 저장소는 루트 [plugin.json](plugin.json)을 사용하는 Agent Plugins 1.0 패키지입니다.

1. VS Code Command Palette에서 **Chat: Install Plugin From Source**를 실행합니다.
2. 다음 URL을 입력합니다.

```text
https://github.com/m1nd0322/save-your-memory
```

3. 설치 source와 trust 정보를 확인합니다.
4. **Chat: Open Customizations** → **Plugins**에서 활성화합니다.
5. Copilot Chat의 Agent 모드에서 다음처럼 사용합니다.

```text
/save-your-memory:save-your-memory 내 위키에서 원하는 자료를 찾아 요약해줘.
```

플러그인 제공 스킬은 `/<plugin-name>:<skill-name>` 형식으로 표시됩니다.

Copilot Chat에서 `/`를 입력해 스킬을 선택하거나 다음처럼 직접 실행합니다.

```text
/save-your-memory:save-your-memory Data Builder 관련 자료를 찾아 원본 링크와 함께 요약해줘.
```

처음 실행할 때 Copilot이 플러그인에 포함된 Python CLI 실행 승인을 요청할 수 있습니다. 실행 대상이 이 저장소의 `scripts/save_your_memory.py`인지 확인한 뒤 승인하세요.

자세한 내용은 [VS Code GitHub Copilot 설치 가이드](docs/vscode-copilot.md)를 참고하세요.

## Codex에서 사용

Codex용 manifest는 [.codex-plugin/plugin.json](.codex-plugin/plugin.json)에 있습니다. 로컬 marketplace에 이 저장소를 등록한 뒤 `save-your-memory` 플러그인을 활성화하면 다음과 같이 요청할 수 있습니다.

```text
$save-your-memory 내 위키에서 원하는 자료를 찾아 요약해줘.
```

## CLI 명령

```powershell
# 변경분 반영
python scripts/save_your_memory.py --env-file .env index --json

# 이전 unsupported/error 항목 재시도
python scripts/save_your_memory.py --env-file .env index --retry-unreadable --json

# 검색 근거 조회
python scripts/save_your_memory.py --env-file .env query "질문" --limit 5 --json

# 카탈로그 상태
python scripts/save_your_memory.py --env-file .env status --json

# 위키 일관성 검사
python scripts/save_your_memory.py --env-file .env lint --json
```

## 프로젝트 구조

```text
.codex-plugin/plugin.json          Codex plugin manifest
plugin.json                        Agent Plugins 1.0 manifest
skills/save-your-memory/SKILL.md   portable Agent Skill
scripts/save_your_memory.py        CLI 진입점
save_your_memory/                  인덱서·추출기·검색·위키 엔진
tests/                              unit/integration tests
docs/                               설계와 설치 문서
```

## 개인정보와 정보보안

- `.env`는 `.gitignore`에 포함돼 있으며 공개 저장소에 올리지 않습니다.
- 원본 파일과 생성 위키·SQLite DB는 저장소에 포함되지 않습니다.
- 예제 설정에는 실제 사용자 이름, 사내 경로, 자격증명을 넣지 않습니다.
- 원본 자료는 읽기 전용으로 처리됩니다.
- 질문 결과에는 로컬 원본 경로가 표시될 수 있으므로 화면 공유 시 주의하세요.
- 플러그인 설치 전 manifest, skill, script 내용을 검토하는 것을 권장합니다.

공개 저장소에는 코드, 일반화된 예제, 테스트 fixture만 포함합니다.

## 제한사항

- 이미지 기반 문서에는 별도 OCR extractor가 필요합니다.
- 암호화되거나 손상된 PDF는 추출하지 못할 수 있습니다.
- 바이너리·동영상·압축파일은 메타데이터만 저장될 수 있습니다.
- 대규모 첫 ingest에는 충분한 디스크 공간과 시간이 필요합니다.
- 데이터가 변경되면 `index`를 다시 실행해야 합니다.
- 검색 성능은 청크 크기와 파일 수에 영향을 받습니다. 기본 청크 크기를 유지하고, 제외 규칙으로 빌드/가상환경/CSV 같은 잡음을 줄이는 것이 좋습니다.

## 개발 및 검증

```powershell
python -m unittest discover -s tests -v
python -m compileall save_your_memory scripts tests
```

테스트는 다음을 포함합니다.

- Windows 경로와 `.env` 처리
- symlink/reparse point 안전성
- 텍스트·OOXML·PDF 추출
- 증분 인덱싱과 FTS 검색
- external-content FTS5 트리거 일관성과 본문 중복 방지
- UTF-8 청크 경계, 장문 토큰, 파일 단위 검색 병합
- 대규모 catalog 스트리밍
- Markdown provenance와 lint
- Codex 및 VS Code Copilot plugin manifest

## 기여

Issue와 Pull Request를 환영합니다. 버그를 보고할 때는 실제 문서나 개인정보 대신 재현 가능한 최소 fixture를 사용해 주세요.

## 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE)를 참고하세요.
