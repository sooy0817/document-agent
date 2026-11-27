# 개발 및 실행

프로젝트 목적과 현재 한계는 [DESIGN.md](DESIGN.md), 파일 위치는
[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)를 따른다. 아래 명령은 개발 루트에서 실행한다.
설치·서버 실행 예시는 사용 방법이며, 문서 갱신 시 실제 실행됐다는 뜻이 아니다.

## 설치와 설정

Python 3.11 이상을 사용한다. 기존 `.venv`가 있다면 재생성하지 않고 현재 설치를 확인한다.
처음 구성하는 환경의 예시는 다음과 같다.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev,postgres,llm,graph]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

의존성 범위와 선택 extra는 [pyproject.toml](../pyproject.toml)이 기준이다.
`.env`의 DB 비밀번호를 설정한다. Docker Compose는 `.env`를 읽지만 Python 앱에
필요한 값은 해당 실행 경로의 프로세스 환경변수·명시적 설정으로 전달한다.
키·비밀번호·실문서·로컬 DB는 커밋하지 않는다.

## 검토 화면

```powershell
.venv/Scripts/python.exe -B -m doc_understanding.web
```

<http://127.0.0.1:8766/> 상단의 **PDF 파일 선택** 또는 드래그 앤 드롭으로 문서를 넣는다.
업로드하면 문서 분석이 시작되고, 아래에서 원본과 표 구조 후보를 확인한다.
임베딩·검색은 업로드 시 자동 실행되지 않는다. 완료 문서를 연 뒤 아래 **이 문서로 검색하고 질문하기**에서
모델·단가·한도와 외부 전송 동의를 설정하고 **검색 준비 · 임베딩**을 누른다.
**DB 적재만 실행**과 키워드 검색은 외부 모델 없이 가능하다. 자세한 흐름은 [RAG 실행](RAG_RUNTIME.md)을 따른다.
기존 문서 평가 팀 상태는 화면 아래의 접힌 **문서 평가 팀**에서 확인한다.
웹 API를 추가한 코드로 갱신할 때는 기존 서버를 종료하고 같은 명령으로 재시작한다.
`OPENAI_API_KEY`는 서버를 시작하는 프로세스의 환경변수여야 한다. 화면에는 키의 설정 여부만 표시한다.
웹 RAG는 단일 서버 프로세스에서 한 작업씩 실행하며, 다중 worker 실행은 지원하지 않는다.
검토 팀의 실행 조건·모델 호출·큐는 [AGENT_TEAMS.md](AGENT_TEAMS.md)를 따른다.
브랜치 전환 전에 실행 중인 서버·worker를 종료하고 전환 후 다시 시작한다.

## 배포 패키지의 독립 설치 확인

배포 후보에서 아래 명령을 실행한다. 첫 명령은 현재 운영체제·Python에 맞는 앱과 필수 의존성의 wheel을 수집하므로 패키지 저장소 접근이 필요하다. 매 릴리스마다 비어 있는 wheel 폴더와 새 가상환경을 사용한다. 폐쇄망에서는 대상 장비와 같은 플랫폼에서 준비한 wheel 폴더와 검사 스크립트를 반입한 뒤 두 번째 명령부터 실행한다.

```powershell
python -m pip wheel . --wheel-dir work/wheelhouse
python -m venv work/install-check
work/install-check/Scripts/python.exe -m pip install --no-index --find-links work/wheelhouse nh-document-understanding
work/install-check/Scripts/python.exe -m pip check
work/install-check/Scripts/python.exe -I scripts/check_installed_parser.py
```

Linux에서는 가상환경의 `bin/python`을 사용한다. 검사는 임시 폴더에서 설치된 `nh-doc` 명령으로 합성 PDF를 분석하고, 페이지·원문 span·입력 해시를 확인한다. 웹 업로드에서 질문까지의 흐름은 DB, 검색 설정, 답변 모델까지 갖춘 환경에서 별도로 검증해야 한다.

## 추출 명령의 범위

| 명령 | 결과 |
|---|---|
| extract-ir | DocumentIR, 원문·페이지·span·native 표 후보 |
| physical-grid | 물리 표·셀·병합·진단과 원본 근거 |
| semantic-structure | PhysicalTableIR에서 행·열 구조 후보, 모델 호출 없음 |
| semantic-resolve | 별도 의미 overlay; 기본 rule_only는 모델 호출 없음 |

```powershell
.venv/Scripts/python.exe -m doc_understanding --help
.venv/Scripts/python.exe -m doc_understanding extract-ir --pdf C:/path/report.pdf --output-dir work/manual-ir
.venv/Scripts/python.exe -m doc_understanding physical-grid --pdf C:/path/report.pdf --output-dir work/manual-physical
.venv/Scripts/python.exe -m doc_understanding semantic-structure --physical-dir C:/path/run/physical --output-dir work/manual-semantic
```

입력 예시 경로는 실제 파일로 바꾼다. 기존 KB 실행이나 결과 폴더를 출력으로 덮어쓰지 않는다.
원본 보존·동결·승인된 파일 이동 범위는 [PARSER_FREEZE.md](PARSER_FREEZE.md)를 따른다.
`extract-ir`만 실행했다고 CanonicalDocument 조립·DB 적재·RAG까지 끝난 것은 아니다.
화면의 문서 처리와 `parser/pipeline.py`, `rag/`의 흐름을 구분한다.

## PostgreSQL과 RAG

로컬 Docker와 `.env`를 준비한 뒤 DB를 시작한다.

```powershell
docker compose config --quiet
docker compose up -d postgres
docker compose exec postgres pg_isready -U nh_document -d nh_document
```

**DB 전송 방식.** `pip install -e ".[postgres]"`(psycopg·psycopg-pool)가 설치되어 있고 `DATABASE_URL`(환경변수
또는 `.env`)로 로컬 DB에 연결되면 커넥션 풀을 쓴다(SQL 1회 수 ms). 연결되지 않으면 기존 `docker compose exec psql`
전송으로 자동 대체한다(SQL 1회 약 0.27초). `NH_DB_TRANSPORT=pool|docker|auto`로 고정할 수 있다. Windows에서
`localhost`는 IPv6 해석으로 연결이 멈출 수 있어 풀 접속 시 `127.0.0.1`로 바꾼다. 두 방식은 같은 `sql()`/`query()`
결과와 SQLSTATE 오류를 돌려준다(`tests/test_pooled_sql_transport.py`).
새로 쓰는 SQL은 값을 문자열에 이어붙이지 말고 `sql_params()`/`query_params()`의 `%s` 자리로 넘긴다(`%%`는 `%`).
풀에서는 서버가 바인딩하고, docker 전송에서는 `literal()` 규칙으로 렌더링한다. 문장은 하나만 허용한다.
기존 `literal()` 기반 SQL은 검증된 동작을 유지하며 단계적으로 옮긴다.

기본 접속 포트는 localhost:5433이며 설정을 바꿨다면 해당 값을 사용한다.
pgvector 지원 이미지와 기존 데이터 볼륨을 사용하는 확장 설치·벡터 연산 확인은
`powershell -File .\scripts\setup_rag_vector.ps1`로 실행한다. Docker 엔진 접근 권한이 필요하며
스크립트는 기존 로컬 postgres 서비스를 갱신할 수 있다. 원격 DB와 별도 실험 DB는 변경하지 않는다.
DDL과 마이그레이션은 `storage/staging/schema.sql`, `rag/schema.sql`, `rag/embedding/schema.sql`, `migrations/`를
확인한다. DB가 실행된다는 사실만으로 문서 적재 완료나 스키마 일치를 가정하지 않는다.
검색·답변 명령, 명시적 외부 모델 호출과 비용 설정은 [RAG_RUNTIME.md](RAG_RUNTIME.md)에 둔다.

로컬 SQL은 UTF-8 바이트로 stdin에 전송하여 Windows의 LF→CRLF 변환을 피한다.
문자열 일치 조건에 쓰는 원문 줄바꿈을 정규화하거나 저장된 값을 일괄 변경하지 않는다.
DB 실패 시 원문·SQL 상세 대신 `LOCAL_POSTGRES_STATEMENT_FAILED SQLSTATE=...`만 표시한다.
예를 들어 `23514`는 CHECK 제약 위반이며, 통합 검사는 누락된 항목의 저장을 막는
CHECK 오류가 실제 발생했는지 확인한다. Docker 연결 오류는 SQLSTATE가 없을 수 있다.

`tests/test_rag_embeddings.py`, `test_rag_hybrid.py`는 모의 임베딩으로 계약·예산·RRF·원문 연결을
검사한다. 실제 pgvector 설치 후 사용자 터미널에서 `$env:NH_TEST_LOCAL_POSTGRES='1'`을 설정하고
`pytest tests/test_rag_vector_storage.py -q`를 실행하면 로컬 적재·배치 롤백·재사용·코사인 검색을
검사한다. 이 검사는 기존 회귀 문서를 적재하고 별도 프로필의 테스트 벡터만 제거하며 모델 API는
호출하지 않는다. 실행 후 `Remove-Item Env:NH_TEST_LOCAL_POSTGRES`로 기본 오프라인 모드로 돌아간다.

## 작업 큐와 일괄 분석

문서 여러 건을 작업자 여러 개로 처리한다. 대기열은 PostgreSQL 표 `jobs.job`
(`runtime/job_queue.py`, 마이그레이션 `029_job_queue`)이다. 단계는 **분석 → 색인 → 임베딩**이다.
색인 단계는 웹의 검색 준비와 같은 순서(규칙 구조 검토 → DB 적재·색인)이며 모델을 부르지 않는다.
임베딩 단계는 이 컴퓨터의 Ollama만 쓴다. 모델 구조 검토, OpenAI 임베딩, 웹 업로드는 기존 경로 그대로다.

```powershell
.venv/Scripts/python.exe -m doc_understanding.services.batch_ingest enqueue --queue q1 --through embed <폴더 또는 PDF>...
.venv/Scripts/python.exe -m doc_understanding.services.batch_ingest work --queue q1 --workers 4 --index-workers 1 --embed-workers 1
.venv/Scripts/python.exe -m doc_understanding.services.batch_ingest status --queue q1
.venv/Scripts/python.exe -m doc_understanding.services.batch_ingest report --queue q1 --stage index --output work/batch-ingest-report/q1-index
.venv/Scripts/python.exe -m doc_understanding.services.batch_ingest retry --queue q1 --stage embed --kind EMBED_FAILED
```

- 등록: 폴더 아래 PDF를 찾아 내용 해시로 중복을 뺀다. 같은 큐에 같은 해시는 한 번만 들어간다.
  `--through`(기본 `analyze`)로 어느 단계까지 이어갈지 정한다. 한 단계가 끝나면 완료 기록과 같은 문장에서
  다음 단계 작업이 대기에 들어간다. 앞 단계를 다시 실행하면 이미 끝난 다음 단계도 새 입력으로 다시 대기한다.
- `enqueue-reviewed --manifest 목록.json`: 이미 구조 검토가 끝난 문서를 받아 색인 단계부터 넣는다. 다시 분석하거나
  검토하지 않고 그대로 적재하므로, 기존 분석본과 당시 채택한 검토 판정을 유지한 채 재적재할 때 쓴다. 목록 항목은
  `source_path`, `source_sha256`, `manifest_path`(staging-manifest.json), `document_path`와 선택 항목 `name`,
  `previous_run_id`(그 실행의 표시 이름을 옮긴다), `pages`, `cells`(제한 시간 계산)다. PDF가 아닌 문서도 받는다.
- 단계별 작업자 수는 `--workers`(분석), `--index-workers`, `--embed-workers`로 따로 정한다(기본 0 = 돌리지 않음).
  `report`와 `retry`는 `--stage`로 단계를 고른다. 색인·임베딩 실패는 `INDEX_FAILED`, `EMBED_FAILED`로 남는다.
- 색인 단계는 **검색 DB에 바로 쓴다.** 끝난 문서는 `/ask`, `/search`와 평가의 검색 대상이 된다.
  측정이나 시험은 `DATABASE_URL`로 별도 PostgreSQL(pgvector, DB 이름 `nh_document`)을 가리키고 돌린다.
- 임베딩 모델은 **사용자가 고른다.** 설치 설정 파일 `config/embedding.json`(또는 `NH_EMBEDDING_CONFIG`가 가리키는
  파일)이나 `work`의 `--embedding-*` 인자로 정하고, 인자가 파일보다 먼저다. 정하지 않으면 이 컴퓨터의 Ollama `bge-m3`다.
  - `compatible`: OpenAI 형식 서버(`POST <base_url>/embeddings`). 사내 추론 서버, vLLM, TEI, Ollama의 `/v1` 등
    어떤 모델이든 주소·모델 이름·차원을 주면 쓴다. 키가 필요하면 키가 든 환경변수 이름(`api_key_env`)만 적는다.
    예: `{"provider": "compatible", "base_url": "http://10.0.0.5:8000/v1", "model": "bge-m3", "dimensions": 1024,
    "input_usd_per_million": 0}`
  - `local`: 이 컴퓨터의 Ollama 기본 API(루프백 주소만). `openai`: OpenAI(단가 필수, 키는 `OPENAI_API_KEY`).
  - `none`: 임베딩하지 않고 키워드 검색만 쓴다(`--through index`까지만 돌린다).
  - 문서 글자가 이 컴퓨터 밖으로 나가는 설정(OpenAI, 다른 컴퓨터의 `compatible` 서버)은 `--allow-external`이 있어야
    시작한다. 동의는 파일에 적지 않는다. `--embedding-max-cost-usd`(기본 1.0)는 문서 하나당 한도다.
  - 응답 벡터 길이가 설정한 차원과 다르면 저장하지 않는다. 모델·차원·공급자가 다르면 벡터를 따로 보관하므로
    섞이지 않는다. 모델을 바꾸면 그 모델로 다시 임베딩해야 벡터 검색에 쓰인다(질문도 같은 모델로 임베딩한다).
  - 한 입력의 길이 한도는 모델마다 다르다. Ollama `bge-m3`는 입력 하나가 약 2,048토큰을 넘으면 묶음 전체를 거부한다
    (측정에서 6,500바이트 항목이 거부됨). `max_input_bytes`(로컬 기본 4,500, 그 외 7,500)보다 긴 항목은 보내지 않고
    `skipped`로 세며 키워드 검색에는 남는다. 호출 기록은 산출물 폴더의 `embedding-audit.json`
    (상태·오류 종류·입력 해시)에 남는다.
  - 직접 실행하는 RAG 명령(`python -m doc_understanding.rag ...`)도 `--embedding-provider compatible
    --embedding-base-url ... --embedding-model ... --embedding-dimensions ...`를 받는다.
  - 아직 설정 파일을 읽지 않는 곳: `/search`, `/ask` 화면은 OpenAI `text-embedding-3-small`로 고정되어 있고,
    문서별 검색 패널은 OpenAI와 로컬만 고를 수 있다.
- 실행: 작업자마다 작업 하나를 별도 프로세스로 실행한다(분석은 기존 `services.documents.worker`).
  결과는 `work/batch-ingest/<큐>/<작업 번호>/r<실행 번호>/`에 남는다. 실행 번호는 줄지 않으므로
  멈춘 뒤 재개하거나 격리 후 `retry`해도 이전 실행의 로그와 산출물이 그대로 남는다. `work`를 여러 번(다른 서버에서도) 띄워도
  한 문서는 한 작업자만 가져간다(`FOR UPDATE SKIP LOCKED`).
- 제한 시간은 `--base-seconds + 쪽 수 × --seconds-per-page`이다. 색인은 칸 1,000개마다
  `--seconds-per-1000-cells`(기본 60초), 임베딩은 검색 항목마다 `--seconds-per-entry`(기본 2초)를 더한다
  (앞 단계가 센 값). 넘기면 프로세스를 끝내고 격리한다.
- 실패: 분석 오류·작업자 중단은 `max_attempts`(기본 2)까지 다시 대기하고 그 뒤 격리(`quarantined`)한다.
  PDF가 아님·암호·열 수 없음·쪽 없음·원본 없음/바뀜·시간 초과는 재시도 없이 격리한다.
  오류 기록에는 종류와 예외 이름만 남긴다(문서 글자가 섞일 수 있는 메시지는 남기지 않는다).
- 작업자가 죽으면 `--stale-seconds` 동안 신호가 없는 작업을 다른 작업자가 되찾는다.
  되찾은 뒤 원래 작업자가 늦게 낸 결과는 버린다. Ctrl+C로 멈추면 진행 중 작업은 시도 횟수를 쓰지 않고 대기로 돌아간다.
- `work`는 작업자가 큐 오류로 끝났거나 끝나지 않은 작업이 남으면 종료 코드 1로 끝난다.
- `report`는 `not-done.csv`도 쓴다: 끝나지 않은 작업을 모든 단계에서 모아 단계·상태·사유·파일·조치·시도 횟수·
  산출물 폴더(`<작업 번호>/r<실행 번호>`)를 한 줄씩 적는다(엑셀에서 열 수 있게 BOM 포함).
- 보고서 `summary.json`: 상태·오류 종류별 건수, 쪽·표·칸 수, 문서/쪽/칸 1,000개당 시간 분포, 분당 쪽 수,
  분석 프로세스 최대 메모리, 느린 문서 10건, 끝나지 않은 문서.

실제 DB 검사는 `$env:NH_TEST_LOCAL_POSTGRES='1'` 뒤 `pytest tests/test_batch_ingest.py -q`로 실행한다
(임의 이름의 테스트 큐만 만들고 지운다). 설정하지 않으면 메모리 큐 검사만 돈다.
실제 분석→색인 검사는 문서 하나를 검색 DB에 적재하므로 버려도 되는 DB를 `DATABASE_URL`로 가리키고
`$env:NH_TEST_SCRATCH_POSTGRES='1'`을 추가로 설정했을 때만 돈다.

2026-11-23 측정(개발 PC, 논리 CPU 14개, 잠금 자료를 뺀 로컬 PDF 132건·1,261쪽·칸 76,590개):

| 작업자 | 전체 시간 | 분당 쪽 | 문서당 중앙값 | 쪽당 중앙값 / 상위 10% |
|---:|---:|---:|---:|---:|
| 1 | 1,701초 | 44.5 | 4.0초 | 1.00초 / 3.72초 |
| 2 | 945초 | 80.1 | 4.6초 | 1.09초 / 3.02초 |
| 4 | 455초 | 167.2 | 4.8초 | 1.16초 / 3.52초 |
| 8 | 383초 | 198.6 | 7.6초 | 1.69초 / 5.55초 |

네 번 모두 130건 완료, 2건 격리(HTML을 PDF 확장자로 저장한 파일 1건, 좌표가 무한대인 문서에서 난
`ValidationError` 1건), 시간 초과 0건. 프로세스 최대 메모리는 중앙값 100MB, 상위 10% 330MB, 최대 1.8GB.
시간은 쪽 수보다 칸 수에 좌우된다(26쪽·칸 10,873개 문서 145초, 114쪽·칸 9,948개 문서 156초).
작업자 8개에서는 문서 하나의 시간이 1.6배로 늘어 처리량 증가가 작다. 문서 구성과 장비에 따라 달라지는 값이다.

같은 날 색인·임베딩 단계 측정(검색 DB를 건드리지 않도록 별도 PostgreSQL 컨테이너에서 실행):

- 색인(작업자 1개, 130건): 모두 완료, 검색 항목 28,330개(문단 13,557·표의 행 11,363·표 1,913·기타 1,497),
  1,880초. 문서당 중앙값 3.5초, 상위 10% 28.6초, 최대 389초. 규칙 구조 검토가 72건에 적용됐다.
  프로세스 최대 메모리는 중앙값 212MB, 상위 10% 1.1GB, 최대 5.5GB.
- 메모리가 큰 이유: `document.json`이 100MB를 넘는 문서가 있다(114쪽 문서 150MB, 18쪽·칸 388개 문서 101MB).
  후자는 101MB 중 97MB가 쪽의 도형 블록(그리기 명령 544,285개)이다. 이 JSON은 객체로 읽으면 약 1.3GB이고,
  색인 과정이 같은 문서를 큐·적재 준비·저장 후 재검증에서 각각 다시 읽어 동시에 든다(단계별 누적 최대
  1.3 → 3.0 → 7.5GB). 이미 저장된 문서를 다시 적재하는 데 421초가 걸렸다(저장본 전체를 꺼내 비교).
  색인·임베딩·질문 응답은 도형 명령을 읽지 않는다(읽는 곳은 분석과 구조 검토의 배치 판정).
  큐의 색인 단계가 적재 전에 자기 사본을 놓게 한 뒤 다시 재면 18쪽 문서는 4.7 → 2.9GB, 114쪽 문서는 5.5 → 5.2GB였다.
  나머지는 기존 적재 코드(`rag/repository.py`의 준비·저장 후 재검증)가 드는 몫이다.
- 임베딩(GPU 없음, CPU의 Ollama, 작업자 1개, 검색 항목 20~150개인 문서 40건·약 2,260개, `compatible` 공급자):

  | 모델 | 차원 | 걸린 시간 | 새로 만든 항목당 | 결과 |
  |---|---:|---:|---:|---|
  | `bge-m3` | 1024 | 1,378초 | 0.67초 | 39건 완료, 1건 격리(아래 수정 전) |
  | `granite-embedding:278m` | 768 | 572초 | 0.25초 | 40건 완료 |
  | `embeddinggemma` | 768 | 2,290초 | 1.02초 | 40건 완료(2건은 재시도 후) |

  세 모델 모두 4,500바이트를 넘는 항목 6개를 건너뛰었다. `bge-m3`의 격리 1건은 서버가 입력 하나를 400으로
  거부해 문서 전체가 실패한 것으로, 이후 거부된 항목만 건너뛰도록 고쳤다(`PROVIDER_REJECTED_INPUT`).
  속도만 잰 것이며 모델별 검색 품질은 재지 않았다. CPU만으로는 130건(28,330개)에 `bge-m3` 기준 5시간쯤이다.
- 글자 없이 숫자·기호뿐이거나 글자가 5자 이하인 검색 항목은 4,673개(16%)지만 용량으로는 5%다.
  용량의 60%가 표의 행이므로 이런 항목을 빼도 임베딩 양은 크게 줄지 않는다.

## GPU·OCR·표 인식 모델 측정 (2026-11-24)

빌린 GPU 서버(RTX 4090 24GB) 한 대에서 공개 문서만으로 쟀다. 채점 기준은 우리 격자의 원문 글자다.
결과 파일과 채점 스크립트는 `work/gpu-results/`(저장소 밖)에 있고, 바깥 표를 우리 격자와 짝짓는 코드는
`src/doc_understanding/interop/tables.py`다. 속도만 잰 항목과 정확도를 잰 항목을 구분해서 읽는다.

속도:

| 작업 | 이 PC(CPU) | GPU | 비고 |
|---|---:|---:|---|
| 임베딩 `bge-m3` | 항목당 0.67초 | 초당 398개(33,896개 85초) | 약 260배 |
| OCR, ONNX 실행 | 쪽당 3.2초 | 쪽당 0.38초 | 스캔 문서 14건·323쪽 |
| OCR, PaddleOCR 실행 | 실용 속도 안 나옴 | 쪽당 0.38초(309쪽 118초) | Windows CPU에서는 oneDNN 오류로 느린 경로만 동작 |
| 표 구조 인식(PP-StructureV3) | 재지 않음 | 쪽당 1.07초(990쪽 1,058초) | |
| GLM-OCR, 표 하나 | 재지 않음 | 중앙값 15초, 최대 71초 | 프로세스 3개가 GPU를 나눠 쓴 값, 프로세스당 초당 52토큰 |

OCR 정확도(글자 있는 PDF 39건·72쪽을 그림으로 바꿔 읽고 원문 칸 4,624개와 비교, 쪽 전체를 읽어 칸에 배정):

- 칸 전체 일치 89.5%(150dpi 89.8%, 잡음 추가 89.0%). 칸을 하나씩 잘라 읽으면 66%로 떨어진다.
- 숫자 칸 일치 96.0%, 자릿수가 틀린 칸은 없었다. 틀린 숫자 칸은 △를 다른 삼각형 글자로 읽은 것 1.79%,
  △를 빠뜨린 것 1.51%(대부분 신뢰도 0.95 이상이라 신뢰도로 걸러지지 않는다), 그 밖의 기호 0.57%다.
- 실제 스캔본이 아니라 깨끗한 그림이다. 스캔 문서 14건은 정답이 없어 속도와 신뢰도(평균 0.969)만 쟀다.

표를 읽는 모델(같은 방식으로 우리 격자와 비교, 분모는 우리 칸 전체):

| 모델 | 표 수 | 행·열 수 같음 | 칸 대응 | 글자 일치 | 숫자 칸 일치 | 머리글을 표시한 표 |
|---|---:|---:|---:|---:|---:|---:|
| PP-StructureV3, 12건 | 31 | 18 | 95.8% | 84.8% | 83.9% | 0 |
| PP-StructureV3, 111건 중 표 영역이 일치한 것 | 738 | 223 | 67.3% | 40.0% | 45.4% | 5 |
| GLM-OCR, 표 그림 | 120 | 60 | 95.0% | 64.8% | 90.7% | 20 |
| PaddleOCR-VL, 표 그림 | 8 | 6 | 97.6% | 83.4% | 78.2% | 0 |

- PP-StructureV3는 111건의 우리 표 1,368개 중 1,038개에서 겹치는 표를 냈고, 영역이 서로 80% 이상 겹친 것이 738개다.
  점수가 낮은 큰 원인은 촘촘한 표에서 줄을 놓치는 것이다(한 쪽 69줄인 목록 표를 42줄로 냄. 순번으로 69줄이 맞다).
  나머지 표는 우리 격자가 맞는지 하나씩 확인하지 않았으므로, 선 없는 표에서는 우리 쪽이 틀린 경우가 섞여 있을 수 있다.
- GLM-OCR은 쓸 수 있는 표·일부만·못 쓰는 표를 40개씩 넣었다. 머리글을 표시한 20개 중 머리글 줄 수가 우리 판정과 같은
  것은 15개다(우리가 모든 값을 확정한 표에서는 5개 중 5개). 한글 오독이 잦다(`강남구`→`강담구`).
- PaddleOCR-VL은 PaddleOCR에 포함된 실행이 GPU를 쓰지 못해 `transformers` 4.55로 돌렸고 표 하나에 5~621초가 걸려
  8개에서 멈췄다. 속도는 잰 것으로 보지 않는다. 출력은 OTSL 표기이고 머리글 표기를 쓰지 않았다.
- 판단: 이 모델들은 머리글 판단을 주지 않거나 드물게만 주고, 글자·숫자는 원문보다 부정확하다. 표의 글자와 칸은
  우리 격자를 쓰고, 스캔본도 선으로 칸을 찾은 뒤 쪽 전체 OCR 글자를 칸에 배정한다. "머리글이 몇 줄인가"만 좁혀 묻는
  방식은 시험하지 않았다.

설치 메모: PyTorch와 Paddle GPU 패키지를 한 환경에 넣으면 CUDA 라이브러리가 충돌한다(환경을 나눈다).
GLM-OCR은 `transformers` 5 이상, PaddleOCR-VL의 `transformers` 실행은 4.55 계열에서만 동작했다.

## 검증

문서만 수정한 경우 링크·경로·지침의 일관성과 `git diff --check`를 확인한다.
코드를 수정했다면 영향을 받는 테스트를 실행하고, 물리·의미 관련 변경에는 다음 검사를 포함한다.

```powershell
.venv/Scripts/python.exe scripts/check_parser_freeze.py
.venv/Scripts/python.exe scripts/check_semantic_contracts.py
.venv/Scripts/python.exe -m pytest tests/test_frozen_contracts.py tests/test_parser_freeze_relocation.py -q
```

의미 계약 검사는 승인된 analyzer_version 허용값, 모델 경로 이동, 역할 주석과 줄바꿈을
명시적으로 환산하여 원래 계약 해시와 비교한다. 필드·기본값·알 수 없는 버전·의존성 변경은
실패하고 JSON 스키마는 바이트 단위로 검사한다. 원본 `frozen-contracts.json`과 동결
manifest·ZIP을 새 해시로 덮어쓰지 않는다. 코드 기준은
[check_semantic_contracts.py](../scripts/check_semantic_contracts.py)다.

추가 테스트는 변경된 기능과 관련된 파일을 선택한다. 실제 PDF·DB·모델·HTTP가 필요한
검사를 구분하고, 실패·skip·timeout과 실행 환경을 기록한다. 기존 결과를 재사용했다면
소스·테스트 일치 근거와 재실행하지 않은 항목을 명시한다. 전체 검사를 매번 자동 반복하지 않는다.
합성 테스트 통과를 실문서 의미 정확도로 해석하지 않는다.

## 과거 코드·자료를 확인할 때

현재 코드에서 제거한 재무 전용 모듈과 이전 실험의 이력은 Git으로 확인한다.

```powershell
git show 78e1fa9 --stat
git show b465406:src/doc_understanding/metric_mapping/core.py
git show ee19016:docs/GENERIC_PARSER_SCOPE.md
```

로컬 보존본은 `reports/generic-cleanup-20261109/retired-code.zip` 및
`reports/runtime-cleanup-20261109/retired-code.zip`이고, 각각 같은 폴더의 `manifest.json`에
원래 경로와 SHA256이 있다. 이 ZIP과 실행 자료는 Git clone에 포함되지 않을 수 있다.
필요한 경우 해시를 확인한 뒤 별도 분석 위치에서 읽고, 현재 `src/`에 자동 복원하지 않는다.

`data/`, `work/`, `reports/`, `output/`, `outputs/` 및 공개 준비 폴더에는 테스트나
도구에서 사용하는 자료가 있다. 경로 의존성과 재현 방법을 확인하기 전 일괄 삭제하지 않는다.

## 평가·실험 책임 경계 검사

`tests/test_evaluation_boundaries.py`는 평가기가 실행·모델 계층을 import하지 않는지,
실행 검증·사람 검토가 채점 모듈에 의존하지 않는지, 원본 보존 비교가 변경을 검출하는지 검사한다.
`tests/test_package_boundaries.py`는 기본 파서의 RAG·평가·실험 의존을 차단한다.

`--basetemp`는 짧은 경로를 사용한다. 실제 PDF를 처리하는 캠페인 테스트는 추출기의 중첩 산출물 경로가
Windows 경로 길이 한도(260자)를 넘으면 `PROCESSING_FAILED`로 실패한다.

```powershell
$testRoot = Join-Path $env:TEMP ('nh-eval-' + [guid]::NewGuid().ToString('N').Substring(0,8))
.venv/Scripts/python.exe -B -m pytest tests/test_evaluation_boundaries.py tests/test_package_boundaries.py tests/test_parser_repair.py tests/test_parser_refresh.py tests/test_automatic_repair.py tests/test_registered_patch.py tests/test_physical_recovery.py tests/test_campaign_sources_evaluation.py tests/test_tree_accuracy.py tests/test_aggregation_arithmetic.py -q -p no:cacheprovider --basetemp $testRoot
.venv/Scripts/python.exe -B scripts/check_parser_freeze.py
```

Windows 샌드박스의 기본 임시 폴더에서는 파일 교체가 `WinError 5`로 거부될 수 있다.
위 명령은 쓰기가 허용된 임시 경로에 매번 새 테스트 폴더를 사용한다.
다른 환경에서는 첫 경로를 해당 환경에서 쓰기가 허용된 임시 경로로 바꾼다.

## 셀 매핑 평가와 검토 화면

`config/evaluation/mapping-dev-v1.json`은 코퍼스 표 셀마다 기대 역할(값·행 라벨·열 머리글·모서리·채점 제외)과
값 셀의 행·열 의미 경로를 적은 테스트셋이다. 셀은 문서 별칭·표 위치(`pNNNN/physicalNNN`)·행열 번호와
원문 앞부분으로 고정한다. 초안은 사람 검토 전이며, 셀별 `reviewed`가 사람 확인 여부다.

- 셀 보기 `rag/evidence/inspect.py`: 분석기가 정한 역할·경로·매핑 상태와, 근거로 쓰일 수 있는지
  (`lookup_eligible`과 같은 규칙), 못 쓰면 그 이유(`PATH_NODE_UNRESOLVED`, `COLUMN_PATH_MISSING` 등).
- 채점 `evaluation/mapping`: 셀 보기 데이터만 읽는다. 지표는 역할 정확도, 근거 사용 가능, 경로 정확,
  틀린 확정(근거로 쓰이는데 경로가 틀림), 경로는 맞지만 근거에서 빠짐.
  `mapping/2-full-denominator`부터 누락 표·셀·원문 불일치도 기대값 기준 분모에 남긴다.
  `exact`는 전체 대상, `exact_matched`는 원문이 일치한 대상만의 조건부 점수다.
  `missing_tables`, `missing_table_cells`, `cell_mismatch`, `source_match`를 함께 확인한다.
  구버전 점수와 직접 비교하지 않고 양쪽 산출물을 같은 지표 버전으로 채점한다.
- `python scripts/evaluate_mapping.py`: 코퍼스(DB) 최신 문서를 채점한다. `--candidate a.json b.json`은
  후보 구조 분석 결과(CanonicalDocument)를 채점하며 기준선 문서를 바꾸지 않는다. 모델 호출 없음.
- 화면 `/mapping`: 표를 고르고 셀을 누르면 분석 결과와 기대값을 비교한다. 기대값의 경로는
  "표에서 셀 눌러 추가"로 라벨 셀을 차례로 눌러 만든다. 저장은 테스트셋 파일만 바꾼다(좌표·원문은 바꿀 수 없음).
  주소 `#doc=kb&table=p0001/physical002&cell=4,2`로 표·셀을 바로 연다.
- 내용을 담는 칸(`container`): 셀 안이 하나의 내용(문장·표가 원본 순서로 놓인 작은 문서)인 칸이다.
  안쪽 표는 그 내용 안의 또 하나의 문맥이며, 같은 구조가 재귀적으로 반복된다(칸 › 표 › 칸 › 표).
  셀 보기는 파서 중첩 연결(기하 연결, 의미 미검증)을 물리 셀에 대응시켜 칸 안 항목과 안쪽 표의 담긴 위치(`parents`)를 보여 준다.
  테스트셋에는 위치 사실(칸 안 항목 순서, 담긴 위치)만 적는다. 안쪽 표가 어느 상품·조건에 적용되는지는
  정답으로 적지 않고 문맥 판단으로 남기며, RAG 평가셋의 대조 문항(ct-*)으로 평가한다.
  채점: 담는 칸 인식, 칸 안 항목 일치, 이어 붙인 원문이 값 근거로 쓰인 수(중복), 담긴 위치 일치,
  근거가 담은 칸 라벨을 함께 싣는 비율.

## 검증 전용 세트와 실패 장부

개발용 세트(`mapping-dev-v1`, `rag-dev-v1`: KB·수협·강동농협)는 규칙·프롬프트를 고칠 때 본다.
기존 `mapping-holdout-v1`, `rag-holdout-v1`도 2026-11-12부터 개선 방향 결정에 사용한 회귀 자료다.
이름에 holdout이 있어도 독립 검증으로 보고하지 않는다. 출시 확인 자료는 개발에 사용하지 않은 문서로 따로 확보한다.
같은 기관의 다른 연도나 같은 양식의 다른 기관 문서는 같은 계열로 묶어 개발/검증을 분리한다.
평가셋 파일 수보다 고유 문서·문서 계열·유형별 문항 수와 사람 검증률을 보고한다.
분석기나 모델이 만든 정답 초안은 원문과 대조해 사람이 승인하기 전까지 개발용으로만 사용한다.

새 문서를 넣을 때의 순서:
1. 적재(구조 검토 포함) 후 `python scripts/check_corpus_intake.py` — 정답표 없이 매핑 이전 문제를 모은다
   (OCR_REQUIRED 스캔본, TABLE_WITHOUT_TEXT, SPARSE_NUMERIC_TABLE 그래프 추정, OVERLAPPING_CELLS 조각난 표, EMPTY_GRID).
2. `python scripts/draft_mapping_set.py --name <세트> --doc 별칭=<실행 ID 앞부분>` — 분석 결과를 기대값 초안으로 옮긴다
   (모든 셀 미확인). `/mapping`에서 세트를 골라 틀린 곳만 고치고 확인 표시를 한다.
3. `python scripts/evaluate_mapping.py --set config/evaluation/<세트>.json`,
   `python scripts/evaluate_rag.py --set …` — 채점하면 실패 기록이 남는다.

실패 장부:
- 실행 폴더의 `failures.json`: 셀·문항 단위 기록. 매핑은 문서·쪽·표·셀 좌표·원문, 파싱된 행·열 경로와 기대 경로,
  실패 종류(wrong_confirmed·withheld·false_value·role_wrong·cell_mismatch·container_*·parent_wrong·context_missing),
  분석기 이유 코드, 표 유형 태그. 질문은 질문·기대·실제 답과 놓친 단계(retrieval·evidence·selection·answer·abstention).
- `work/failures/ledger.jsonl`: 실행마다 한 줄(시각·종류·세트·코드 버전·건수 요약·기록 파일). 덧붙이기만 한다.
- `/mapping`의 "실패 모아 보기": 현재 분석 결과와 기대값이 다른 셀을 종류별로 보여 주고 누르면 그 셀로 간다.

### RAG 지표와 비교 기준

`rag/2-required-anchors`는 `evidence_any_hit`(하나라도 포함)과 `required_evidence_coverage`
(필수 anchor별 충족률), `all_required_evidence`(필수 근거 전체 충족)를 구분한다.
`gold`의 각 항목은 필수 근거이며 같은 anchor에 대응하는 여러 원본 ID는 대체 가능한 위치다.
일부 anchor를 해석하지 못해도 분모에서 빼지 않고 `unresolved_gold_items`에 기록한다.
`evidence_recall`은 호환용으로 남긴 any-hit의 옛 이름이다.
`answer_string_match`는 기대·금지 문자열 검사다. `rag/3-selected-comparison-evidence`부터 비교 문항의
`answer_correct`는 이 검사와 최종 답변의 근거 선택을 함께 평가한다. 선택한 근거 ID를 해당 실행의
원본 셀·본문과 연결해 각 문서의 필수 위치가 모두 있는지 확인한다. 값이 같거나 검색 후보에 양쪽이 있어도
한쪽만 선택하면 오답이다. 문항 결과에 선택한 필수 위치 수와 누락된 문서 별칭을 기록한다.
조건·부정·단위·시점의 의미 정확도는 사람이 확인한 정답 및 판정 기준으로 별도 검증해야 한다.

현재 지표 `rag/4-grounded-ambiguity`는 모호한 질문의 단순 보류를 성공으로 세지 않는다.
되묻기는 문항에 지정한 쟁점을 포함해야 하며, 조건부 답은 가정·값·선택한 원문 위치를 함께 검사한다.
`rag-maturity-paired-dev-v1`은 모호 질문과 조건을 명시한 대조 문항을 포함한다.
새 문서 문항의 사람 검토 상태는 [원문 검토표](../config/evaluation/rag-cross-document-review-v1.md)에 남긴다.
두 세트 모두 개발용 초안이며 전체 사람 검증이나 독립 holdout 평가를 마쳤다는 뜻이 아니다.
`--corpus-from-set`은 평가셋에 등록된 문서들로 검색·문서 선택 범위를 고정한다.

실행 오류도 전체 문항 분모에 남긴다. 지연은 실패를 포함한 기록값의 평균과 nearest-rank P50/P90/P95/P99를 제공한다.
시간·비용이 없는 항목은 누락 목록에 남기며 비용 누락이 있으면 `total_cost_usd`는 null이다.
`observed_cost_usd`는 기록된 추정 비용의 합이며 실제 청구액이나 누락된 호출 비용을 뜻하지 않는다.

새 RAG 실행은 `evaluation-snapshot.json`에 문항 전체, 선택된 문항, 해석된 정답 ID, 코퍼스 실행 ID,
문서 해시, 코드 파일 해시, 모델 설정·계획 캐시 위치를 고정한다. 재채점은 이 자료만 읽는다.
평가셋이 바뀌거나 스냅샷이 없는 옛 실행을 현재 DB로 조용히 재채점하지 않는다.
기존 보고서의 재집계는 정답 재채점과 구분한다. 계획 캐시를 사용하는 비교는 모델의 반복 변동성 검증이 아니다.

### 회귀·성능 점검

```powershell
.venv/Scripts/python.exe -m pytest tests/test_evaluation_regressions.py tests/test_rag_evaluation.py tests/test_mapping_evaluation.py tests/test_evaluation_boundaries.py -q
.venv/Scripts/python.exe scripts/check_parser_freeze.py
.venv/Scripts/python.exe scripts/check_evaluation_readiness.py --out work/evaluation-audit/readiness.json
```

오프라인 점검의 `--batch <보고서폴더> ...`는 같은 입력의 완료 상태와 전체 `result.json`을 비교한다.
`--rag <report.json> ...`는 옛 점수의 지연·비용 누락을 재집계한다. DB·파서·모델 호출은 하지 않는다.
출력 파일은 덮어쓰지 않으므로 새 비교에는 새 `--out`을 사용한다.

| 점검 | 통과 판단과 범위 |
|---|---|
| 원본·출력 보존 | 동결 검사 통과, 같은 입력의 원문·좌표·병합·경로·상태 비교. 일치는 의미 정답 증명이 아니다. |
| 정확도 회귀 | 같은 정답·코퍼스·지표 버전으로 누락, 새 오답, 불필요한 보류, 필수 근거 누락을 비교. 정답 변경은 별도 검토. |
| 속도 | 전체 처리 시간과 대기 포함 문서/질문 P95를 분리. 동일 장비·설정, 캐시 유무와 반복 횟수를 기록. |
| 자원 | 작업자별 메모리 외에 호스트 전체 최대 RAM·CPU·I/O·디스크 여유 측정. 개별 최대값으로 동시 사용량을 추정하지 않음. |
| 복구 | 중단·소유권 상실·재시도·격리 후 재개, 중복 적재 및 이전 시도 산출물 보존. 메모리 큐 테스트와 DB 통합 검증을 구분. |
| 비용 | 실패·재시도 포함 호출/추정 비용과 누락 수 확인. 기록이 빠진 실행은 비용 감소 근거로 쓰지 않음. |
| 일반화 | 문서 계열별 독립 검증과 사람이 승인한 정답. OCR 미지원 문서는 지원 범위 밖/보류 기대값으로 구분. |

허용 지연·동시 사용자 수·장비와 정확도 목표는 출시 범위에 맞춰 정한 뒤 부하 시험한다.
단일 PC의 한 차례 적재 결과나 Mock 테스트만으로 운영 성능을 승인하지 않는다.

## 표 배치 명세와 구조 검토 (적재 시점)

기본 구조 분석기는 표 하나에 "머리글 n행·라벨 m열" 하나를 추정한다(기준선, 동작 불변). 적재 시점 구조 검토
(`agents/structure_review`)가 표 배치 명세 `TableLayout`(머리글 행 수 0 허용 + 행별 라벨 깊이,
`parser/structure/layout.py`)로 분석기를 다시 돌려 검토 후보 문서를 만든다. 원본 문서·물리 표·동결 계약은 바꾸지 않는다.

판정 순서(`review_document`):
1. 머리글 근거(규칙): 첫 행만 배경색·굵은 글씨, 글자 머리글 아래 숫자 본문(NUMERIC_BODY), 숫자·길이 대비.
2. 행별 라벨 깊이(규칙): 라벨 경계에 걸친 칸의 점유 범위로 정한다(`derive_layout`). 라벨 열에 걸친 머리글은 열 머리글.
3. 머리글 없는 표(규칙): 첫 행까지 모두 "짧은 라벨 | 긴 내용"이거나 첫 행 칸이 표를 담고 있으면 `header_rows=0`
   (값은 행 경로만 가진다).
4. 그래도 모호한 표만 모델(선택 + Judge): 규칙이 만든 후보 배치(머리글 있음·없음)와 보류 중에서만 고른다.
   적재 요청이 모델·단가를 주고 외부 호출을 허용했을 때만 호출한다(`/ask` 적재 화면의 별도 체크박스).
   실패·기권·Judge 불일치는 보류다.
5. 채택 검사: 근거로 쓸 수 있는 값이 늘고 문서 검증을 통과해야 하며, 표를 담은 칸을 머리글로 삼는 배치는 받지 않는다.
   반복 머리글 패널(좌우로 나란한 표)의 라벨 열을 값으로 읽은 배치도 받지 않는다. 머리글 1행·라벨 1열 배치는 분석기가
   패널을 나눠 다시 분석한다.

근거 사용: 경로는 한쪽만 있어도 된다(배치가 그 축이 없다고 한 경우). 같은 행·같은 경로의 나란한 칸은 한 값 묶음이며
각 칸이 나머지 칸 원문을 함께 싣는다.

규칙 변경의 전후 비교: `python scripts/evaluate_mapping.py --review-originals`(규칙만, 저장 안 함),
`--review-model <모델> --review-input-usd-per-million … --review-output-usd-per-million …`(모호한 표에 모델, 유료).

파서의 `validation.py`나 RAG 출처·인용 검사를 평가 폴더로 옮기지 않는다.
실제 Docker·모델·DB 실행은 위 오프라인 테스트 통과와 구분해서 기록한다.

## 검토 표시·실행 분리 검사

`tests/test_review_presentation.py`는 공통 표시 집계와 화면이 일치하는지,
중첩 연결·미해결 경고·원문 escaping을 유지하는지, 구조 검토 CLI의 산출물을 검사한다.

```powershell
$testRoot = Join-Path $env:TEMP ('nh-review-' + [guid]::NewGuid().ToString('N').Substring(0,8))
.venv/Scripts/python.exe -B -m pytest tests/test_review_presentation.py tests/test_web.py tests/test_parser_repair.py tests/test_parser_refresh.py tests/test_evaluation_boundaries.py tests/test_package_boundaries.py tests/test_agent_team_migration.py tests/test_parser_freeze_relocation.py -q -p no:cacheprovider --basetemp $testRoot
.venv/Scripts/python.exe -B scripts/check_parser_freeze.py
```

실행 중인 서버는 이전 import를 메모리에 보유할 수 있다. 새 구조 사용에는 서버 재시작이 필요하며,
오프라인 테스트 통과를 실행 중 서버 갱신이나 브라우저 검증으로 보고하지 않는다.
