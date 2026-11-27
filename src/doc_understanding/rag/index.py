# 역할: 기존 검색 항목·근거 함수의 import 호환 창구다. 구현은 책임별 패키지에 한 곳씩 둔다.
from doc_understanding.domain.numeric import parse_value as parse_value
from doc_understanding.rag.evidence.cells import cells_for_table as cells_for_table, display_path as display_path
from doc_understanding.rag.evidence.pack import evidence_pack as evidence_pack
from doc_understanding.rag.indexing.entries import make_entries as make_entries
from doc_understanding.rag.retrieval.lexical import terms as terms, lexical_score as lexical_score
