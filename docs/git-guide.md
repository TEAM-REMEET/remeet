# Git / GitHub 가이드 (초보자용)

명령어가 어렵다면 **VS Code 왼쪽 'Source Control' 탭**이나 **GitHub Desktop**으로 같은 작업을 할 수 있습니다.

## 하루 작업 흐름

```bash
# 1. 최신 main 받기
git checkout main
git pull

# 2. 내 브랜치 만들기 (이름/주제)
git checkout -b eunje/goldenset

# 3. 작업 → 저장
git status                      # 뭐가 바뀌었는지 확인
git add playground/eunje/       # 올릴 파일만 골라서 추가 (git add . 는 조심!)
git commit -m "exp: 정답"

# 4. GitHub에 올리기
git push -u origin eunje/goldenset
```
5. GitHub 페이지에 뜨는 **"Compare & pull request"** 버튼 → PR 생성
6. 리뷰 승인 후 **"Squash and merge"** → 끝. 다시 1번부터.

## 커밋 메시지 앞머리
| 접두어 | 의미 |
|---|---|
| `feat:` | 새 기능 |
| `fix:` | 버그 수정 |
| `exp:` | 실험 |
| `docs:` | 문서 |
| `chore:` | 설정, 정리 |

## 리뷰 규칙
- `playground/내이름/` 안만 바꾼 PR → 본인이 바로 merge 해도 됩니다.
- `web/`, `ai/`, `ml/`, `docs/`, `experiments/` 를 바꾸면 → 팀원 1명 승인 후 merge.
- PR은 **작게, 그날 안에.** 오래 끌수록 충돌이 커집니다.

## 자주 하는 실수
| 상황 | 해결 |
|---|---|
| `main`에서 작업해버렸다 | 커밋 전이라면 `git checkout -b 내브랜치` 로 브랜치를 만들면 변경사항이 그대로 따라옵니다 |
| push 했더니 거부된다 | `main`은 직접 push 금지입니다. 브랜치로 push 하세요 |
| 데이터/모델 파일을 add 해버렸다 | commit 전: `git restore --staged 파일`. **commit/push 했다면 혼자 해결하지 말고 바로 팀에 알리세요** |
| `.env` 를 올렸다 | 즉시 팀에 알리고 그 안의 키는 폐기(재발급)해야 합니다 |
| 충돌(conflict)이 났다 | 당황하지 말고 VS Code의 충돌 화면에서 'Accept Current/Incoming/Both' 선택. 어려우면 팀에 요청 |
| 내 브랜치가 main 보다 뒤쳐졌다 | `git checkout main && git pull && git checkout 내브랜치 && git merge main` |

## 절대 올리지 않는 것
- 원본/가공 데이터 (`data/`), `.env`
- 이 항목들은 `.gitignore`가 막아주지만, 강제로 add 하면 올라갑니다.

## 첫 연습 과제
`playground/본인이름/hello.md` 파일 하나를 만들어서 위 흐름대로 PR 을 올려보세요. 다른 팀원이 승인하고 merge 하면 성공입니다.
