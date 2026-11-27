"""Minimal graph state; document evidence remains in the campaign store."""

from typing import TypedDict


class CampaignState(TypedDict, total=False):
    campaign_id: str
    next_team: str
    reason: str
    sample_id: str
    specialist_finished: bool
    specialist_reason: str
