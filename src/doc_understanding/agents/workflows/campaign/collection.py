"""Bounded discovery in a designated local development-only inbox."""

import sqlite3
from pathlib import Path


def collect_local(store, campaign_id, inbox, *, limit=20):
    """Register direct PDF children; never search existing evaluation datasets."""
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    count = skipped = 0
    for path in sorted(inbox.iterdir(), key=lambda p: p.name.casefold()):
        if count >= limit:
            break
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != '.pdf':
            continue
        try:
            store.add_sample(campaign_id, path, 'development')
            count += 1
        except (ValueError, sqlite3.IntegrityError, OSError):
            skipped += 1
    store.event(campaign_id, 'collect', f'local_registered={count};skipped={skipped}')
    return count
