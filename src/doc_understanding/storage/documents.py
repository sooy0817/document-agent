# 역할: 문서와 원본 산출물의 일치 여부를 검사하고 실행별 원본을 저장한다.
# 검색 항목·RAG 스키마는 다루지 않으며, 물리 파서를 다시 실행하지 않는다.
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from doc_understanding.domain.models import DocumentIR
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.assembly.document import validate_document
from doc_understanding.parser.layout import DocumentLayoutProjection
from doc_understanding.parser.pipeline import load
from doc_understanding.storage.staging.local import ingest


ARTIFACT_ID = 'canonical-document'


@dataclass
class PreparedDocument:
    # 검사를 마친 원본과 저장 인자를 묶는다. 준비 단계에서는 DB에 쓰지 않는다.
    manifest: dict
    ir: DocumentIR
    layout: DocumentLayoutProjection
    artifacts: list
    document: CanonicalDocument
    sha256: str


def prepare(manifest_path, document_path):
    # 원본 IR·참조 파일의 해시를 검사하고 JSON 원문을 저장 목록에 포함한다.
    manifest, ir, layout, artifacts = load(manifest_path)
    path = Path(document_path)
    doc = CanonicalDocument.model_validate_json(path.read_bytes())
    validate_document(doc)
    if doc.document_ir != ir:
        raise ValueError('DOCUMENT_IR_NOT_IDENTICAL')
    for ref in doc.source_artifact_refs:
        source = Path(manifest['run_dir']) / ref.path
        if hashlib.sha256(source.read_bytes()).hexdigest() != ref.sha256:
            raise ValueError('SOURCE_ARTIFACT_CHANGED')
    raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    original_payload = json.loads(path.read_text(encoding='utf8'))
    artifacts.append((ARTIFACT_ID, 'CanonicalDocument', path, original_payload, None))
    return PreparedDocument(manifest, ir, layout, artifacts, doc, raw_hash)


def persist(prepared, *, key, detail=False):
    # detail=False: 읽는 곳이 없는 staging 세부 표(evidence·content_block·relation_candidate)는 쓰지 않고,
    # 문서 기록(ARTIFACT_ID) 말고 다른 artifact는 해시와 경로만 남긴다(내용이 문서 기록 안에 이미 있다). ADR-080.
    # 호출자가 스키마를 준비한 뒤 저장한다. 재실행 키는 호출자의 기존 정책을 따른다.
    # ingest가 파일 내용과 해시를 다시 검사하고 중복 실행·트랜잭션을 처리한다.
    return ingest(prepared.ir, prepared.layout, Path(prepared.manifest['run_dir']),
                  Path(prepared.manifest['source']), key, str(uuid4()), prepared.artifacts, detail=detail,
                  payload_ids=None if detail else {ARTIFACT_ID})
