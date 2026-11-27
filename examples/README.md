# 예시

| 파일 | 내용 |
| --- | --- |
| `rate_table_ko.py` | 가상의 한글 금리표 PDF를 만들고 파서로 읽어 아래 결과를 저장한다. 모델·DB 호출 없음. |
| `rate-table-ko.pdf` | 위 스크립트가 만든 합성 PDF. 실제 금융기관·상품·금리가 아니다. |
| `rate-table-ko.json`, `rate-table-ko.txt` | 그 PDF의 파서 출력(`tabledoc/0.5`)과 읽기용 텍스트. 파서가 낸 그대로이며 정답 판정이 아니다. |
| `make_readme_figures.py` | 위 PDF와 JSON에서 README 그림(`docs/figures/`)을 다시 그린다. |
| `physical-grid/` | 물리 표 복원 예시(중첩 표, 미완성 격자, 세로 스캔)의 원본·결과·겹쳐 그린 그림. |
| `synthetic-document-ir.json` | Pydantic 계약을 설명하는 test fixture. 실제 PDF에서 추출한 결과가 아니며 bbox·hash·파일 참조는 합성 값이다. |

실제 실행 결과는 `data/runs/<run_id>/`에 저장되며 Git에 포함하지 않는다.
