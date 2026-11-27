"""runtime / model_client."""

import base64
import hashlib
import json
import sqlite3
import socket
import time
from urllib.error import URLError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from doc_understanding.agents.roles import analyst, judge
from doc_understanding.runtime import artifacts
from doc_understanding.runtime.openai_http import http_transport
from doc_understanding.agents.workflows.legacy_review import version as legacy_review_version

MODEL = "gpt-4.1-2025-04-14"


class ModelCalls:
    """Durable call reservations prevent silent re-billing after ambiguous interruption."""

    def __init__(
        self,
        data,
        *,
        key=None,
        transport=http_transport,
        daily_limit=20,
        profiles=None,
        version=legacy_review_version.VERSION,
        model=MODEL,
    ):
        self.data = Path(data)
        self.key = key
        self.transport = transport
        self.daily_limit = daily_limit
        self.profiles = dict(profiles) if profiles is not None else None
        self.version = version
        self.model = model

    def call(self, role, packet, images, run_dir):
        dest = run_dir / (role + ".json")
        started = run_dir / (role + "-started.json")
        prompt = analyst.ANALYST if role == "analyst" else judge.JUDGE
        cls = analyst.Analysis if role == "analyst" else judge.Judgement
        if self.profiles is not None:
            prompt, cls = self.profiles[role]
        request_spec = dict(
            packet=packet,
            prompt=prompt,
            model=self.model,
            images=[hashlib.sha256(i["bytes"]).hexdigest() for i in images],
        )
        if self.profiles is not None:
            request_spec.update(schema=cls.model_json_schema(), version=self.version)
        request_hash = artifacts.digest(request_spec)
        if dest.exists():
            stored = artifacts.read(dest)
            if stored["request_hash"] != request_hash:
                raise ValueError("PINNED_REQUEST_CHANGED")
            return stored
        if started.exists():
            return dict(
                status="interrupted_unknown_usage",
                output=None,
                usage=None,
                estimated_cost_usd=None,
                api_calls=1,
                request_hash=request_hash,
                error="PREVIOUS_CALL_OUTCOME_UNKNOWN",
            )
        if not self.key:
            return dict(
                status="blocked",
                output=None,
                usage=None,
                estimated_cost_usd=0,
                api_calls=0,
                error="API_KEY_UNAVAILABLE",
            )
        db = self.data / "review-queue" / "agent-budget.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db, timeout=10) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS calls (call_key TEXT PRIMARY KEY, day TEXT NOT NULL)"
            )
            c.execute("BEGIN IMMEDIATE")
            day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
            if (
                c.execute("SELECT count(*) FROM calls WHERE day=?", (day,)).fetchone()[0]
                >= self.daily_limit
            ):
                return dict(
                    status="blocked",
                    output=None,
                    usage=None,
                    estimated_cost_usd=0,
                    api_calls=0,
                    error="DAILY_CALL_BUDGET",
                )
            call_key = str(run_dir.resolve()) + "/" + role
            if c.execute("SELECT 1 FROM calls WHERE call_key=?", (call_key,)).fetchone():
                return dict(
                    status="interrupted_unknown_usage",
                    output=None,
                    usage=None,
                    estimated_cost_usd=None,
                    api_calls=1,
                    error="RESERVED_CALL_UNKNOWN",
                )
            c.execute("INSERT INTO calls VALUES (?,?)", (call_key, day))
        artifacts.save(
            started, dict(request_hash=request_hash, role=role, model=self.model, day=day)
        )
        content = [dict(type="input_text", text=json.dumps(packet, ensure_ascii=False))]
        for i in images:
            content.extend(
                [
                    dict(type="input_text", text="Original image: " + i["source_id"]),
                    dict(
                        type="input_image",
                        detail="high",
                        image_url="data:image/png;base64," + base64.b64encode(i["bytes"]).decode(),
                    ),
                ]
            )
        payload = dict(
            model=self.model,
            store=False,
            temperature=0,
            max_output_tokens=4500,
            input=[dict(role="system", content=prompt), dict(role="user", content=content)],
            text=dict(
                format=dict(
                    type="json_schema",
                    name="review_" + role,
                    strict=True,
                    schema=cls.model_json_schema(),
                )
            ),
        )
        record = dict(
            status="failed",
            output=None,
            model=self.model,
            request_hash=request_hash,
            usage=None,
            estimated_cost_usd=None,
            api_calls=1,
            prompt_version=self.version,
        )
        start = time.perf_counter()
        try:
            response = self.transport(payload, self.key, 120)
            usage = response.get("usage")
            record["usage"] = usage
            if usage:
                cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                rates = {MODEL: (2, 0.5, 8), "gpt-4.1-mini-2025-04-14": (0.4, 0.1, 1.6)}.get(
                    self.model
                )
                if rates:
                    record["estimated_cost_usd"] = (
                        (usage["input_tokens"] - cached) * rates[0]
                        + cached * rates[1]
                        + usage["output_tokens"] * rates[2]
                    ) / 1e6
            if response.get("status") != "completed":
                raise ValueError("INCOMPLETE_MODEL_RESPONSE")
            text = "".join(
                part.get("text", "")
                for out in response.get("output", [])
                for part in out.get("content", [])
                if part.get("type") == "output_text"
            )
            record["response_text"] = text
            record["output"] = cls.model_validate_json(text).model_dump(mode="json")
            record["status"] = "completed"
        except Exception as exc:
            record["error"] = type(exc).__name__
            if isinstance(exc, URLError) and isinstance(exc.reason, socket.gaierror):
                record["error_code"] = "DNS_RESOLUTION_FAILED"
        record["elapsed_seconds"] = time.perf_counter() - start
        artifacts.save(dest, record)
        return record
