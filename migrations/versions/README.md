No schema revisions in Phase 0/1. Phase 5 must add reviewed migrations, constraints,
JSONB snapshots, approved-fact publication, rollback and idempotency tests.
An empty `alembic upgrade head` would NOT establish the target database schema.
