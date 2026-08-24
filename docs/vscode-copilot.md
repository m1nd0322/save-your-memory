# VS Code GitHub Copilot 설치 및 사용

`save-your-memory`는 Agent Plugins 1.0 형식과 portable Agent Skill을 제공합니다. GitHub Copilot이 설치된 VS Code에서 저장소 URL을 통해 플러그인을 설치하면 터미널 명령을 직접 입력하지 않고 Copilot Chat에서 사용할 수 있습니다.

## 요구 사항

- 최신 Visual Studio Code
- GitHub Copilot 확장 기능 및 로그인
- Python 3.11 이상
- 로컬 원본·위키 경로를 지정한 `.env`

## GitHub 저장소에서 설치

1. Command Palette에서 **Chat: Install Plugin From Source**를 실행합니다.
2. 다음 저장소 URL을 입력합니다.

```text
https://github.com/m1nd0322/save-your-memory
```

3. VS Code가 표시하는 source와 trust 정보를 확인하고 설치를 승인합니다.
4. Command Palette에서 **Chat: Open Customizations**를 실행합니다.
5. **Plugins** 탭에서 `save-your-memory`가 enabled 상태인지 확인합니다.
6. 플러그인이 보이지 않으면 **Developer: Reload Window**를 실행합니다.

VS Code의 Agent Plugins 기능은 workspace 설정에서 활성화돼 있습니다.

```json
{
  "chat.plugins.enabled": true
}
```

## 로컬 데이터 경로 설정

질문을 실행할 workspace에 `.env`를 생성합니다. 이 파일에는 개인 경로가 포함되므로 Git에 커밋하지 마세요.

```dotenv
SAVE_YOUR_MEMORY_ROOT="D:\Documents\Knowledge"
SAVE_YOUR_MEMORY_HOME="D:\SaveYourMemoryData"
SAVE_YOUR_MEMORY_MAX_FILE_BYTES=26214400
```

- `SAVE_YOUR_MEMORY_ROOT`: 읽기 전용으로 인덱싱할 부모 폴더
- `SAVE_YOUR_MEMORY_HOME`: SQLite 인덱스와 Markdown 위키가 생성될 별도 폴더
- `SAVE_YOUR_MEMORY_MAX_FILE_BYTES`: 파일 하나당 추출 허용 크기

## Copilot Chat에서 사용

1. Copilot Chat을 엽니다.
2. Chat 모드를 **Agent**로 선택합니다.
3. `/`를 입력하고 `/save-your-memory:save-your-memory`를 선택합니다.
4. 질문을 이어서 입력합니다.

```text
/save-your-memory:save-your-memory 내 위키에서 온도 보정 자료를 찾아 요약해줘.
```

```text
/save-your-memory:save-your-memory TinyML 관련 자료가 어느 원본 파일에 있는지 알려줘.
```

```text
/save-your-memory:save-your-memory 새로 추가된 파일을 다시 인덱싱해줘.
```

플러그인 제공 스킬은 `/<plugin-name>:<skill-name>` 형식으로 표시되므로 명령이 두 번 반복되는 것이 정상입니다.

Copilot Agent는 내부적으로 플러그인의 Python CLI를 실행합니다. 사용자가 터미널에 명령을 직접 입력할 필요는 없지만 최초 실행 시 VS Code가 Terminal tool 명령의 승인을 요청할 수 있습니다.

## 로컬 개발 버전 등록

GitHub URL 대신 로컬 checkout을 사용하려면 User Settings에서 `chat.pluginLocations`를 검색하고 자신의 로컬 경로를 enabled 상태로 추가합니다.

```json
{
  "chat.pluginLocations": {
    "C:\\path\\to\\save-your-memory": true
  }
}
```

실제 사용자 이름이나 사내 경로를 저장소 파일에 기록하지 마세요.

## 문제 해결

- **플러그인이 표시되지 않음:** `chat.plugins.enabled`를 확인하고 VS Code 창을 reload합니다.
- **스킬이 `/` 메뉴에 없음:** `Chat: Open Customizations`의 Plugins·Skills 탭을 확인합니다.
- **명령 승인이 반복됨:** Terminal tool 승인 정책에서 플러그인의 Python 실행 명령을 검토합니다.
- **설정 파일을 찾지 못함:** 현재 workspace에 `.env`가 있는지 확인합니다.
- **검색 결과가 오래됨:** `/save-your-memory:save-your-memory memory 폴더를 다시 인덱싱해줘`라고 요청합니다.
- **위키 상태 확인:** `/save-your-memory:save-your-memory 위키의 누락 페이지와 고아 페이지를 검사해줘`라고 요청합니다.

## 보안 원칙

- `.env`, SQLite DB, 생성 위키, 원본 자료는 공개 저장소에 올리지 않습니다.
- 원본 경로는 읽기 전용으로 처리됩니다.
- 플러그인 설치 전 `plugin.json`, `skills/`, `scripts/` 내용을 검토하세요.
- 공유 PC에서는 위키와 인덱스 출력 경로의 접근 권한을 별도로 관리하세요.
