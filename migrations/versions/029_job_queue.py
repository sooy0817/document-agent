"""문서 작업 큐 표(jobs.job)를 추가한다."""

from pathlib import Path
from alembic import op

revision = "029_job_queue"
down_revision = "028_rag_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    schema = Path(__file__).parents[2] / "src/doc_understanding/runtime/job_queue.sql"
    op.get_bind().exec_driver_sql(schema.read_text(encoding="utf-8"))


def downgrade():
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS jobs.job; DROP SCHEMA IF EXISTS jobs;")
