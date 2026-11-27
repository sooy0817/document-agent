"""agents / diagnostic_judge."""

from doc_understanding.agents.roles import judge
from doc_understanding.agents.roles.role import AgentRole

JUDGE = (
    judge.JUDGE
    + """Compare the analyst with the pre-recorded blind_source_review and relation_checks.
Assess ONLY analyst_candidate.issues by their exact issue_id. If it is empty, assessments MUST be [].
Never assess the user feedback error_type as an issue_id. State a no-issue review in summary instead.
Use short exact text substrings without added ellipses.
Do not agree with a missing-link claim when storage lookup says present. That only refutes absence;
the existing link may still be incorrect. If original-image review conflicts with the analyst, explain.
Tool records may refute assumptions. Do not echo a claim simply because the analyst sounds confident.
Do not request an already supplied image as missing without specifying which unreadable area matters.
"""
)


ROLE = AgentRole("judge_v2", JUDGE, judge.Judgement)


def run(calls, packet, images, run_dir):
    return ROLE.invoke(calls, packet, images, run_dir)
