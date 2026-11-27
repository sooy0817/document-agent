"""Create and run an offline local document campaign."""

import argparse
import json
from pathlib import Path

from doc_understanding.runtime.paths import ROOT
from .service import execute
from .store import CampaignStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "work" / "campaigns")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("goal")
    add = sub.add_parser("add")
    add.add_argument("campaign_id")
    add.add_argument("pdf", type=Path)
    add.add_argument("--role", choices=["development", "regression", "holdout"], required=True)
    for command in ("run", "show"):
        item = sub.add_parser(command)
        item.add_argument("campaign_id")
    pilot_parser = sub.add_parser("pilot", help="Run the selected real-document campaign")
    pilot_parser.add_argument("--manifest", type=Path, required=True)
    pilot_parser.add_argument("--resume", action="store_true")
    pilot_parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    store = CampaignStore(args.data)
    if args.command == "pilot":
        from . import pilot
        if args.retry_failed and not args.resume:
            parser.error("--retry-failed requires --resume")
        cid = pilot.prepare(store, args.manifest, ROOT)
        if store.get(cid)["status"] != "ready" and not args.resume:
            output = {"campaign_id": cid, "status": store.get(cid)["status"], "resumed": False}
        else:
            output = pilot.run(store, cid, ROOT, resume=args.resume, retry_failed=args.retry_failed)
    elif args.command == "create":
        output = {"campaign_id": store.create(args.goal)}
    elif args.command == "add":
        output = {"sample_id": store.add_sample(args.campaign_id, args.pdf, args.role)}
    elif args.command == "run":
        output = execute(store, args.campaign_id)
    else:
        output = store.get(args.campaign_id)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
