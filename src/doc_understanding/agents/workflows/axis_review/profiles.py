"""teams / axis_review / profiles."""

from doc_understanding.agents.roles import axis_interpreter, axis_judge

PROFILES = {
    "interpret_axes": (axis_interpreter.SOURCE_PROMPT, axis_interpreter.Interpretation),
    "judge_axes": (axis_judge.JUDGE_PROMPT, axis_judge.Judgment),
}
