# code-grader

학생 과제 제출 현황 엑셀에서 **학번, 이름, GitHub 저장소 주소**를 수집하고, 저장소 클론과 소스 파일 병합을 수행하는 CLI입니다.
lang-grader의 제출 목록 정리와 여러 제출 주소 처리 규칙을 바탕으로 구성했습니다.

## 설치

Python 3.10 이상과 Git이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 사용

```bash
# JSON: 정상 목록과 예외 목록을 한 파일에 저장
code-grader collect "과제 제출 현황.xls" -o output/students.json

# CSV: 정상 목록을 저장하고 students.exceptions.json에 예외를 별도로 저장
code-grader collect "과제 제출 현황.xlsx" -o output/students.csv

# 시트 지정 / 미제출 학생도 예외 목록에 포함
code-grader collect "과제 제출 현황.xlsx" --sheet "제출 목록" --include-unsubmitted

# 표준 출력으로 CSV 전달, 예외는 별도 JSON으로 저장
code-grader collect "과제 제출 현황.xls" --format csv --exceptions-output output/exceptions.json
```

`python -m code_grader`로도 실행할 수 있습니다. 출력 파일을 생략하면 결과를 표준 출력으로 보냅니다.
요약, 예외 알림, 경고는 표준 오류로 출력합니다. CSV 파일은 Excel에서 한글을 읽을 수 있도록 UTF-8 BOM을 포함합니다.

## 입력 처리

- 실제 Excel `.xls`, `.xlsx`와 학교 시스템에서 `.xls`로 내려받는 **HTML 표**를 지원합니다. 파일 내용으로 형식을 구분합니다.
- 첫 50행에서 `학번`/`아이디`, `이름`/`성명` 헤더를 찾습니다. 기본적으로 조건에 맞는 모든 시트를 읽습니다.
- `과제설명`, `제출내용`, `링크`, `GitHub URL` 등 제출 주소 열의 텍스트와 하이퍼링크를 함께 확인합니다. 해당 열이 없으면 학번·이름·제출상태 외의 열을 확인합니다.
- 셀 안의 여러 링크, 전각 문자, `HTTPS ://`, `2번https://...`, Markdown/HTML 링크를 처리합니다.
- 저장소 루트와 `blob`/`tree` 링크에서 `https://github.com/<owner>/<repo>`를 정리하고, 파일 경로가 있는 제출 링크도 보존합니다.
- 같은 학번의 제출은 합칩니다. 동일한 저장소와 링크는 중복 제거하며 파일 경로의 대소문자는 구별합니다.
- 학번은 문자열로 저장합니다. 숫자 셀의 `0000000` 같은 표시 형식도 반영합니다. Excel에서 이미 사라진 앞자리 0은 복원하지 않습니다.
- `미제출` 행은 기본적으로 제외합니다. `--include-unsubmitted`를 사용하면 `not_submitted` 예외로 남깁니다.

## 정상 목록과 예외

**`github.com/<owner>/<repo>` 형태의 일반 GitHub 저장소만 정상 수집합니다.**
Gist, raw 도메인, GitLab, 외부 파일 공유 주소 등은 모두 예외입니다.
정상 주소와 예외 주소가 한 제출에 섞여 있으면 정상 주소만 `students`에 포함하고 나머지는 `exceptions`에 기록합니다.
정상 저장소 주소가 하나도 없는 제출은 예외 목록에만 남깁니다.

```json
{
  "students": [
    {
      "student_id": "0012345",
      "name": "홍길동",
      "repository_urls": ["https://github.com/student/homework"],
      "submission_urls": ["https://github.com/student/homework/blob/main/main.c"],
      "submission_status": "제출"
    }
  ],
  "exceptions": [
    {
      "sheet": "제출 목록",
      "row": 4,
      "student_id": "0012346",
      "name": "김학생",
      "reason": "non_github_url",
      "value": "https://gist.github.com/student/0123456789abcdef"
    }
  ],
  "warnings": [],
  "skipped_unsubmitted": 0
}
```

| `reason` | 의미 |
| --- | --- |
| `non_github_url` | Gist를 포함한 `github.com` 외 도메인 |
| `invalid_github_url` | 사용자 프로필 등 저장소로 해석할 수 없는 GitHub 주소 |
| `invalid_url` | URL 형식 오류 |
| `missing_repository_url` | 제출 내용에 저장소 주소가 없음 |
| `missing_student_id`, `invalid_student_id`, `missing_name` | 학생 식별 정보 누락·오류 |
| `conflicting_name` | 같은 학번에 다른 이름이 존재함 |
| `not_submitted` | 미제출 학생을 포함하도록 요청한 경우 |

예외에는 엑셀 시트명, 1부터 시작하는 행 번호, 학번, 이름, 주소 또는 제출 내용을 기록합니다.
CSV는 학생당 한 행이며 여러 주소는 셀 안에서 줄바꿈으로 구분합니다. 예외 파일 경로는 `--exceptions-output`으로 바꿀 수 있습니다.
`collect`는 처리에 성공하면 예외가 있어도 종료 코드는 `0`입니다. 파일 읽기, 헤더 확인, 결과 저장에 실패하면 `1`입니다.
`collect`에서는 주소의 형식만 정리하며 GitHub에 접속하지 않습니다.

## 저장소 클론

먼저 `collect` 결과를 JSON으로 저장한 뒤 `clone`에 전달합니다.

```bash
code-grader collect "과제 제출 현황.xls" -o output/students.json

# .env.example을 복사한 뒤 .env의 GITHUB_TOKEN 값을 입력
cp .env.example .env

code-grader clone output/students.json -o output/repositories

# 마감 시각 이하의 커밋으로 체크아웃
code-grader clone output/students.json -o output/deadline-repositories \
  --limit "2026-11-02T23:59:59+09:00"

# GitHub에 실제 푸시된 시각으로 마감 판정
code-grader clone output/students.json -o output/push-deadline-repositories \
  --limit "2026-11-02T23:59:59+09:00" --limit-basis push

# 네트워크에 접속하지 않고 클론 대상과 예외 확인
code-grader clone output/students.json --dry-run --report output/clone-plan.json
```

`.env`에는 다음 형식으로 토큰을 입력합니다.

```dotenv
GITHUB_TOKEN=your_github_token
```

기본적으로 **명령을 실행한 현재 디렉터리의 `.env`**를 읽습니다. 다른 경로는 `--env-file /path/to/.env`로 지정합니다.
환경변수 `GITHUB_TOKEN`이 이미 설정되어 있으면 해당 값이 우선합니다. 토큰이 없으면 공개 저장소에 익명으로 접근합니다.
토큰은 Git 인증 헤더로 전달하며 클론 URL, `.git/config`, 리포트에 저장하지 않습니다. `.env`는 Git에서 제외합니다.

### 여러 제출 주소 처리

- 같은 학생이 같은 저장소의 여러 파일·디렉터리를 제출해도 **저장소를 한 번만 클론**합니다.
- 서로 다른 저장소는 `<학번>/<소유자>/<저장소>`로 나눠 보관합니다. 학생이 다르면 별도로 보관합니다.
- Git 저장소 전체를 클론하며, 제출한 파일·디렉터리 경로를 리포트에 기록합니다. 파일별로 저장소 내용을 잘라내지 않습니다.
- 저장소 루트 URL은 기본 브랜치, `blob`/`tree` URL은 명시한 브랜치·태그·커밋을 사용합니다. `/`가 포함된 브랜치 이름도 실제 참조 목록으로 해석합니다.
- 제출한 경로가 선택된 커밋에 존재하는지, 파일·디렉터리 유형이 맞는지 확인합니다.
- 서로 다른 커밋의 제출은 Git worktree로 나눠 코드가 덮어써지지 않게 합니다. 같은 커밋의 링크는 작업 폴더를 공유합니다.
- `--limit` 또는 입력 JSON의 최상위 `limit`이 있으면 지정한 마감 기준으로 커밋을 선택합니다. CLI 옵션이 JSON보다 우선하며 시간대를 반드시 지정해야 합니다.
- Gist·외부 도메인과 `edit`, `new`, `commit` 등 지원하지 않는 GitHub URL은 예외로 남깁니다. 지원하는 제출 형태는 저장소 루트, `blob`, `tree`입니다.
- 일부 링크나 저장소 처리가 실패해도 나머지는 계속합니다. 기존 결과 폴더가 있으면 예외로 기록하고 보존합니다. 다시 수집할 때는 새 출력 디렉터리를 사용합니다.

### 마감 판정 기준

`--limit-basis commit`이 기본값이며 커미터 시각으로 마감 커밋을 고릅니다.
`--limit-basis push`는 [GitHub 저장소 활동 API](https://docs.github.com/en/rest/repos/repos#list-repository-activities)를 조회해
**제출 브랜치에서 마감 시각 이하에 일어난 마지막 푸시 직후의 커밋**을 선택합니다. 강제 푸시나 병합으로 바뀐 브랜치 상태도 활동 이력에 따라 판단합니다.
API 인증에도 `.env`의 같은 토큰을 사용합니다.

예를 들어 커밋 시각이 오전 10시여도 오후 6시에 처음 푸시했다면 정오 마감에는 포함하지 않습니다.
반대로 마감 뒤에 추가 푸시가 있어도, 마감 전에 받아들여진 마지막 푸시의 코드 상태를 선택합니다.
저장소 전체의 마지막 푸시 시각과 다른 브랜치의 활동은 제출 브랜치의 마감 판정에 사용하지 않습니다.

활동 기록에 접근할 수 없거나 마감 이하의 기록을 확인하지 못하면 `push_api_failed` 또는 `push_history_unavailable` 예외로 남깁니다.
커미터 시각으로 자동 대체하지 않습니다. 커밋 SHA만 포함한 URL은 브랜치 푸시 이력을 특정할 수 없어 `push_ref_required` 예외입니다.
강제 푸시로 현재 이력에서 사라진 커밋은 동일 저장소에서 SHA로 가져오며, 가져올 수 없으면 `push_commit_unavailable` 예외로 남깁니다.
마감 시각 당시 브랜치가 삭제된 상태였다면 `ref_deleted_at_limit` 예외입니다.
마감 시각을 지정하지 않으면 두 모드 모두 현재 제출 참조의 최신 커밋을 사용하며 활동 API를 조회하지 않습니다.

### 결과 구조

```text
output/repositories/
├── clone-report.json
└── 0012345/student/homework/
    ├── submission.json
    ├── repository/                # 첫 유효 제출의 커밋으로 체크아웃한 Git 저장소
    └── checkouts/<commit>/        # 다른 커밋 제출이 있을 때 추가 Git worktree
```

`submission.json`과 전체 `clone-report.json`에는 학생 정보, 저장소 주소, 각 제출의 원본 URL·유형·참조·커밋·경로·작업 폴더를 기록합니다.
마감 판정 기준은 `limit_basis`에 기록합니다. 푸시 기준으로 판정한 제출에는 `pushed_at`, `push_activity_id`, `push_ref`, `push_type`도 기록합니다.
수집 단계에서 발생한 예외는 전체 리포트의 `input_exceptions`에 보존합니다. 새 예외는 `exceptions`에 기록하며,
브랜치·경로 해석 실패는 `submission_resolution_failed`, 클론 실패는 `clone_failed`, 기존 폴더는 `destination_exists` 등으로 구분합니다.

`clone`은 새 예외가 없으면 `0`, 새 예외나 실행 오류가 있으면 `1`을 반환합니다. `--dry-run`은 브랜치·경로의 존재를 검증하지 않으며 기본적으로 파일을 만들지 않습니다.
`--report`를 지정하면 처리 계획을 JSON으로 저장합니다. Git 작업별 제한 시간은 기본 120초이며 `--timeout`으로 변경합니다.

## 소스 파일 병합

기존 lang-grader의 `merge.sh`처럼 디렉터리 안의 `.c`, `.h`, `.md`를 재귀적으로 찾아 상대 경로순으로 합칩니다.
각 파일 앞에 `# 상대/경로`와 빈 줄을 붙이고, 파일 내용 뒤에 빈 줄을 넣습니다.

```bash
# 전체 클론 결과를 하나로 병합: output/repositories/merged_output.txt
code-grader merge output/repositories

# 특정 학생의 여러 저장소와 작업 폴더를 하나로 병합
code-grader merge output/repositories/0012345

# 결과 파일 경로 지정
code-grader merge output/repositories -o output/all-submissions.txt

# 파일을 만들지 않고 전체 내용을 화면에 출력
code-grader merge output/repositories --stdout

# 기존 all_merge.sh처럼 각 폴더에 merged_output.txt 생성
code-grader merge output/repositories --all

# 다른 언어의 파일을 포함하려면 확장자 지정
code-grader merge output/repositories --extensions .py .java .md
```

기본 출력 파일은 대상 디렉터리의 `merged_output.txt`입니다. `--all`은 병합할 파일이 있는 폴더와 그 상위 폴더 각각에 결과를 생성하며,
각 결과에는 해당 폴더의 하위 파일까지 포함합니다. `-o`, `--stdout`, `--all`은 하나만 선택할 수 있습니다.

Git 내부 파일, 가상환경·의존성 폴더, 심볼릭 링크, 기존 `merged_output.txt`는 제외합니다.
클론의 `repository`와 추가 `checkouts`는 상대 경로로 구별되어 함께 포함됩니다.
UTF-8, CP949, EUC-KR과 BOM이 있는 UTF-16 입력을 읽고 결과는 UTF-8로 저장합니다.
일반적인 한글 파일명과 공백이 있는 경로를 지원합니다.

처리에 성공하면 종료 코드 `0`을 반환합니다. 읽지 못하는 파일이나 바이너리 파일은 표준 오류로 알리고 나머지를 병합한 뒤 `1`을 반환합니다.
요약은 표준 오류에 출력하므로 `--stdout`의 표준 출력에는 병합 내용만 포함됩니다.

## 검증

```bash
python -m unittest discover -s tests -v
```

테스트는 익명 입력과 로컬 Git 저장소를 사용해 인증 전달, 여러 제출, 브랜치, 마감 시각, 실패 처리를 검증합니다.
실제 학생 정보가 담긴 로컬 예제는 `.agent/`에 두며 Git에 포함하지 않습니다.
