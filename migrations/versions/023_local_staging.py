"""Local staging tables; no financial fact publication."""
from pathlib import Path
from alembic import op

revision = "023_local_staging"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = (Path(__file__).parents[2] / "src/doc_understanding/storage/staging/schema.sql").read_text(encoding='utf-8')
    op.get_bind().exec_driver_sql(sql)


def downgrade():
    raise RuntimeError("Evidence retention: destructive staging downgrade requires a separate plan.")
