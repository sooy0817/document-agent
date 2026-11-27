// 역할: 화면이 쓰는 고정 설정과 문구. 모델·단가는 화면에서 고르지 않는다(서버가 호출별 상한을 검사한다).
// 임베딩 설정은 web/search_routes.py의 SCREEN_EMBEDDING과 같아야 한다('검색 가능' 판정 기준).
export const EMBEDDING = {provider: 'openai', model: 'text-embedding-3-small', dimensions: 1536, input_usd_per_million: 0.02, max_cost_usd: 1};
export const ANSWER = {model: 'gpt-4.1', input_usd_per_million: 2.0, output_usd_per_million: 8.0, max_cost_usd: 0.15,
  plan_model: 'gpt-4.1-mini', plan_input_usd_per_million: 0.4, plan_output_usd_per_million: 1.6, plan_max_cost_usd: 0.05,
  rerank_model: 'gpt-4.1-mini', rerank_input_usd_per_million: 0.4, rerank_output_usd_per_million: 1.6, rerank_max_cost_usd: 0.05};
export const REVIEW_MODEL = {structure_review_model: 'gpt-4.1-mini', structure_review_input_usd_per_million: 0.4, structure_review_output_usd_per_million: 1.6};

export const STAGES = {queued: '대기', planning: '질문 이해', selecting: '문서 고르기', searching: '검색', evidence: '근거 준비', reranking: '근거 선별', reading: '표 전체 읽기', grid: '표 원문 읽기', answering: '답변 생성',
  retry: '근거가 부족해 한 번 더 찾는 중'};

const ERRORS = {
  API_KEY_UNAVAILABLE: '서버에 OPENAI_API_KEY가 없습니다. 키를 설정한 터미널에서 서버를 다시 시작하세요.',
  RAG_BUSY: '같은 종류의 작업이 실행 중입니다. 끝난 뒤 다시 시도하세요.',
  CORPUS_EMPTY: '검색 가능한 문서가 없습니다. 오른쪽 위 "문서 추가"로 문서를 올리세요.',
  RUN_NOT_IN_CORPUS: '고른 문서가 지금은 검색 대상이 아닙니다. 범위를 다시 고르세요.',
  DOCUMENT_NOT_READY: '분석이 끝나지 않은 문서입니다.',
  SOURCE_MANIFEST_REQUIRED: '원본 추출 기록을 찾을 수 없는 문서입니다. 파일을 다시 올리세요.',
  NO_MATCHING_EMBEDDINGS: '임베딩이 준비된 문서가 없습니다. 문서의 검색 준비가 끝났는지 확인하세요.',
  EMBEDDING_CALL_FAILED_SEE_AUDIT: '임베딩 호출에 실패했습니다(외부 모델 오류).',
  ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT: '답변 모델 호출에 실패했습니다(외부 모델 오류).',
  MODEL_REQUEST_BUDGET_EXCEEDED: '질문 한 건의 입력 또는 비용 한도를 넘었습니다.',
  EMBEDDING_RUN_BUDGET_EXCEEDED: '임베딩 예산·호출 한도에 도달했습니다. 끝난 부분은 보존됩니다. 다시 시도하면 이어서 진행합니다.',
  SERVER_RESTARTED: '서버가 다시 시작되어 중단됐습니다. 다시 실행하세요.',
  RAG_OPERATION_FAILED: '처리 중 오류가 났습니다(상세 내용은 서버 기록에만 남습니다).',
  DOCUMENT_IR_NOT_IDENTICAL: '분석 결과가 원본 추출 기록과 달라 적재하지 않았습니다.',
  EVIDENCE_STORE_STALE: '예전 방식으로 색인된 문서입니다. 다시 적재해야 합니다.',
};
export const say = code => ERRORS[code] || (String(code || '').startsWith('LOCAL_POSTGRES') ? 'DB 연결 또는 조회에 실패했습니다. Docker와 PostgreSQL 상태를 확인하세요.' : code);

// 원문에서 근거 위치를 표시하지 못하는 이유(서버의 positions_reason).
export const REASONS = {
  NO_PAGE_COORDINATES: '이 문서는 HTML 원문이라 쪽 좌표가 없습니다. 근거 위치를 표시하지 않으니 아래 인용 문구로 원문에서 직접 확인하세요.',
  SYNTHETIC_COORDINATES: '이 문서의 좌표는 원문 위치가 아니라 읽은 순서로 만든 값입니다. 위치를 표시하지 않습니다.',
  PAGE_SIZE_MISMATCH: '저장된 좌표의 쪽 크기가 이 파일과 달라 위치를 표시하지 않습니다(추정한 위치를 표시하지 않음).',
  PAGE_NOT_FOUND: '원문에서 이 쪽을 찾지 못했습니다.',
  SOURCE_NOT_AVAILABLE: '이 문서의 원문 파일을 찾지 못했습니다. 아래는 색인에 저장된 인용 문구와 표입니다(원문 그림과 대조하지 못한 내용).',
  SOURCE_UNREADABLE: '원문 파일을 열 수 없습니다. 아래는 색인에 저장된 인용 문구와 표입니다.',
};
