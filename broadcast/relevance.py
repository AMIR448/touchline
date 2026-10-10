"""Which moments deserve AI commentary for a given viewer. Plain code, no model needed."""

from __future__ import annotations

from .models import ViewerProfile

TACTICAL = {"pressing_spell", "chaos_spike", "momentum_swing", "sustained_possession", "key_pass"}
NEVER = {"substitution", "half_time", "full_time"}  # subs are routine; HT/FT get a recap instead


def involves(profile: ViewerProfile, moment: dict) -> bool:
    return bool((profile.favorite_team and moment.get("team_id") == profile.favorite_team)
                or (profile.favorite_player and profile.favorite_player in moment.get("player_ids", [])))


def wants_commentary(profile: ViewerProfile, moment: dict) -> bool:
    if moment["type"] in NEVER:
        return False
    importance = moment["importance"]
    if involves(profile, moment):
        return importance >= 0.3
    if profile.persona == "analyst":
        return importance >= 0.4
    return importance >= 0.5 and moment["type"] not in TACTICAL - {"momentum_swing"}


def wants_recap(moment: dict) -> bool:
    return moment["type"] in ("half_time", "full_time")
