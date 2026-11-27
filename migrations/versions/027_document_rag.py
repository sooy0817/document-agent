"""Derived search index; all source JSON stays in existing staging.artifact."""
from pathlib import Path
from alembic import op

revision = '027_document_rag'
down_revision = '023_local_staging'
branch_labels = None
depends_on = None


def upgrade():
    statement = (Path(__file__).parents[2] / 'src/doc_understanding/rag/schema.sql').read_text(encoding='utf8')
    op.get_bind().exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Evidence retention: destructive downgrade requires a separate plan.')
