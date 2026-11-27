# 탐색 안내: document.py는 문서 단위 조립, nested_content.py는 부모 셀 안의 본문·중첩 표 연결을 담당한다.
# 폴더 역할: 원본 IR과 표 구조 후보를 문서 단위 CanonicalDocument로 조립한다.
# document.py가 조립·파일 읽기·검증을 담당하고, 서비스와 RAG가 그 결과를 사용한다.
# DB 적재와 검색 인덱스 생성은 이 패키지에서 수행하지 않는다.
