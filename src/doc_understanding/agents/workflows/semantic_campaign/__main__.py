"""Explicit bounded execution; opening a report never starts paid work."""

import argparse
import json
from pathlib import Path

from .report import record_judgments
from .service import create, read, run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("start", "resume", "judge"))
    p.add_argument("--project", type=Path, default=Path.cwd())
    p.add_argument("--data", type=Path, default=Path("work/document-explorer"))
    p.add_argument("--output", type=Path, default=Path("work/semantic-campaigns"))
    p.add_argument("--jobs", nargs="+")
    p.add_argument("--campaign")
    p.add_argument("--public", action="store_true")
    p.add_argument("--max-tables", type=int, default=12)
    p.add_argument("--per-type", type=int, default=3)
    p.add_argument("--max-llm-cells", type=int, default=1)
    p.add_argument("--judgments", type=Path)
    a = p.parse_args()
    if a.command == "start":
        if not a.jobs:
            p.error("--jobs is required")
        cid = create(
            a.project,
            a.data,
            a.output,
            a.jobs,
            public=a.public,
            per_type=a.per_type,
            max_tables=a.max_tables,
            max_llm_cells=a.max_llm_cells,
        )
        print(json.dumps({"campaign_id": cid}), flush=True)
        print(json.dumps(run(a.output, cid), ensure_ascii=False))
    elif a.command == "resume":
        if not a.campaign:
            p.error("--campaign is required")
        print(json.dumps(run(a.output, a.campaign), ensure_ascii=False))
    else:
        if not a.campaign or not a.judgments:
            p.error("--campaign and --judgments are required")
        from doc_understanding.agents.workflows.campaign.store import CampaignStore

        CampaignStore(a.output).get(a.campaign)
        print(json.dumps(record_judgments(a.output / a.campaign, read(a.judgments))))


if __name__ == "__main__":
    main()
