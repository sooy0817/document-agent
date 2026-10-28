"""Explicit, budgeted embedding calls. Content-addressed cache; no hidden retries."""
import hashlib
import json
import math
import os
from pathlib import Path
from time import perf_counter
import urllib.request

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
PRICE_PER_MILLION = .02


def http_embeddings(payload, key, timeout):
    request = urllib.request.Request('https://api.openai.com/v1/embeddings',
        data=json.dumps(payload, ensure_ascii=False).encode('utf8'),
        headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class Embeddings:
    def __init__(self, cache, *, allow_external=False, budget_usd=.10, transport=http_embeddings):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.allow_external, self.budget_usd, self.transport = allow_external, budget_usd, transport
        self.calls = 0
        self.tokens = 0
        self.cost = 0
        self.elapsed_seconds = 0

    @staticmethod
    def key(text):
        return hashlib.sha256((MODEL+':'+str(DIMENSIONS)+':'+text).encode()).hexdigest()

    def embed(self, texts):
        unique = list(dict.fromkeys(texts))
        result, missing = {}, []
        for text in unique:
            if not text or len(text.encode('utf8')) > 8000:
                raise ValueError('EMBEDDING_TEXT_BYTE_LIMIT')
            path = self.cache/(self.key(text)+'.json')
            if path.exists():
                item = json.loads(path.read_text())
                if item['model'] != MODEL or item['dimensions'] != DIMENSIONS:
                    raise ValueError('EMBEDDING_CACHE_MODEL_MISMATCH')
                result[text] = item['embedding']
            else:
                missing.append(text)
        while missing:
            batch, size = [], 0
            while missing and len(batch) < 64 and size+len(missing[0].encode()) <= 100000:
                text = missing.pop(0)
                batch.append(text)
                size += len(text.encode())
            reserve = size*PRICE_PER_MILLION/1e6
            if not self.allow_external:
                raise ValueError('UNCACHED_EMBEDDING_REQUIRES_EXPLICIT_EXTERNAL_FLAG')
            if self.cost+reserve > self.budget_usd:
                raise ValueError('EMBEDDING_BUDGET_EXCEEDED')
            key = os.environ.get('OPENAI_API_KEY')
            if not key:
                raise ValueError('API_KEY_UNAVAILABLE')
            payload = dict(model=MODEL, input=batch, encoding_format='float', dimensions=DIMENSIONS)
            started = perf_counter()
            self.calls += 1
            try:
                response = self.transport(payload, key, 45)
                usage = response.get('usage', {})
                tokens = usage.get('total_tokens', usage.get('prompt_tokens', 0))
                self.tokens += tokens
                cost = tokens*PRICE_PER_MILLION/1e6
                self.cost += cost
                data = sorted(response['data'], key=lambda d: d['index'])
                if len(data) != len(batch) or [d['index'] for d in data] != list(range(len(batch))):
                    raise ValueError('EMBEDDING_RESPONSE_COUNT_MISMATCH')
                for text, item in zip(batch, data, strict=True):
                    vector = item['embedding']
                    if len(vector) != DIMENSIONS or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                        raise ValueError('EMBEDDING_INVALID_VECTOR')
                    result[text] = vector
                    path = self.cache/(self.key(text)+'.json')
                    path.write_text(json.dumps(dict(model=MODEL, dimensions=DIMENSIONS, embedding=vector)), encoding='utf8')
                record = dict(status='completed', model=MODEL, texts=len(batch), usage=usage,
                              estimated_cost_usd=cost, request_text_hashes=[self.key(t) for t in batch])
            except Exception as error:
                record = dict(status='failed_usage_may_be_unknown', error=type(error).__name__, texts=len(batch),
                              reserved_cost_usd=reserve)
                raise RuntimeError('EMBEDDING_FAILED_SEE_LOCAL_LEDGER') from None
            finally:
                seconds = perf_counter()-started
                self.elapsed_seconds += seconds
                with (self.cache/'calls.jsonl').open('a', encoding='utf8') as stream:
                    stream.write(json.dumps({**record, 'elapsed_seconds': seconds})+'\n')
        return [result[text] for text in texts]

    def usage(self):
        return dict(model=MODEL, dimensions=DIMENSIONS, model_calls=self.calls, input_tokens=self.tokens,
                    estimated_cost_usd=self.cost, elapsed_seconds=self.elapsed_seconds,
                    pricing_basis='standard embedding input $0.02/M tokens, 2026-10-22')
