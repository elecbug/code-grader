# code-grader

학생 과제 제출 현황 엑셀에서 **학번, 이름, GitHub 저장소 주소**를 수집하는 CLI입니다.
lang-grader의 제출 목록 정리 기능을 바탕으로 엑셀 직접 읽기와 예외 처리를 구성했습니다.

## 설치

Python 3.10 이상이 필요합니다.

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
처리에 성공하면 예외가 있어도 종료 코드는 `0`입니다. 파일 읽기, 헤더 확인, 결과 저장에 실패하면 `1`입니다.
주소의 형식만 정리하며 GitHub 접속이나 저장소 존재 여부 확인은 수행하지 않습니다.

## 검증

```bash
python -m unittest discover -s tests -v
```

테스트는 익명으로 만든 입력을 사용합니다. 실제 학생 정보가 담긴 로컬 예제는 `.agent/`에 두며 Git에 포함하지 않습니다.
