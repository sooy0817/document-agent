# 역할: 모델 비용 계산과 호출 예산의 명시적 CLI 설정을 제공한다.
"""Explicit model and cost assumptions for bounded Responses API calls.

The default rates preserve the historical project estimate; they are not a live
price lookup. Other models require caller-supplied rates instead of reusing it.
"""
import math


def rates(model, input_usd_per_million=None, output_usd_per_million=None):
    if not isinstance(model, str) or not model.strip():
        raise ValueError('MODEL_REQUIRED')
    if input_usd_per_million is None and output_usd_per_million is None and model == 'gpt-4.1':
        return 2.0, 8.0, 'historical project estimate (2026-10-22); cached discount ignored'
    values = (input_usd_per_million, output_usd_per_million)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('EXPLICIT_MODEL_PRICING_REQUIRED')
    return *values, 'caller-supplied USD per million tokens; cached discount ignored'


def cost(input_tokens, output_tokens, prices):
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def add_arguments(parser):
    parser.add_argument('--model', default='gpt-4.1')
    parser.add_argument('--input-usd-per-million', type=float)
    parser.add_argument('--output-usd-per-million', type=float)
    parser.add_argument('--max-cost-usd', type=float, default=.15, help='Per call cost ceiling')


def options(args):
    return {key: getattr(args, key) for key in (
        'model', 'input_usd_per_million', 'output_usd_per_million', 'max_cost_usd')}
