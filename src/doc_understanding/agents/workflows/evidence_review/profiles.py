"""teams / evidence_review / profiles."""

from doc_understanding.agents.roles import diagnostic_analyst, diagnostic_judge, judge, source_reviewer

PROFILES = {
    "source_review": (source_reviewer.SOURCE, source_reviewer.SourceReview),
    "analyst_v2": (diagnostic_analyst.ANALYST, diagnostic_analyst.Analysis),
    "reanalysis": (diagnostic_analyst.ANALYST, diagnostic_analyst.Analysis),
    "judge_v2": (diagnostic_judge.JUDGE, judge.Judgement),
}
