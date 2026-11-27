"""검색 항목별 임베딩 프로필·원문 연결과 벡터 저장소를 추가한다."""

from pathlib import Path
from alembic import op

revision = "028_rag_embeddings"
down_revision = "027_document_rag"
branch_labels = None
depends_on = None


def upgrade():
    schema = Path(__file__).parents[2] / "src/doc_understanding/rag/embedding/schema.sql"
    op.get_bind().exec_driver_sql(schema.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Evidence retention: destructive downgrade requires a separate plan.")
