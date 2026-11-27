"""runtime / budget."""


class DailyReviewBudgetExceeded(RuntimeError):
    """No model call was started; the persisted queue can be retried another day."""
