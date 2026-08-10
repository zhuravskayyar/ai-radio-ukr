from dataclasses import asdict, dataclass, field


@dataclass
class TransitionPlan:
    transition_type: str
    variant: str
    voice_duration_ms: int
    duck_percent: int
    events: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self):
        return asdict(self)


class TransitionDirector:
    """Produces deterministic timing; language models never control the mixer."""

    def __init__(self, duck_percent=27):
        self.duck_percent = max(10, min(40, int(duck_percent)))

    def _variant(self, available_ms, short_ms, full_ms):
        safety = 500
        if full_ms and (available_ms <= 0 or full_ms + safety <= available_ms):
            return "full", full_ms
        if short_ms and (available_ms <= 0 or short_ms + safety <= available_ms):
            return "short", short_ms
        if short_ms:
            return "short", short_ms
        if full_ms:
            return "full", full_ms
        return "none", 0

    def plan(self, current_track, next_track, content_type, short_ms=0, full_ms=0):
        if content_type == "clean_segue":
            return TransitionPlan(
                transition_type="clean_segue",
                variant="none",
                voice_duration_ms=0,
                duck_percent=self.duck_percent,
                events=[{"at_ms": 0, "action": "next_start", "volume": 100}],
                reason="ContentPlanner selected a clean segue",
            )

        vocal_start = int((next_track or {}).get("vocal_start_ms") or 0)
        outro_start = int((current_track or {}).get("outro_start_ms") or 0)
        current_duration = int((current_track or {}).get("duration_ms") or 0)
        hard_end = int((current_track or {}).get("hard_end_ms") or 0)
        effective_end = hard_end if hard_end > 0 else current_duration
        outro_window = max(0, effective_end - outro_start) if outro_start else 0
        end_type = (current_track or {}).get("end_type") or "unknown"

        variant, voice_ms = self._variant(vocal_start, short_ms, full_ms)
        if variant == "none":
            return TransitionPlan(
                transition_type="clean_segue",
                variant="none",
                voice_duration_ms=0,
                duck_percent=self.duck_percent,
                events=[{"at_ms": 0, "action": "next_start", "volume": 100}],
                reason="No prepared voice asset",
            )

        if vocal_start >= voice_ms + 500:
            return TransitionPlan(
                transition_type="talk_up",
                variant=variant,
                voice_duration_ms=voice_ms,
                duck_percent=self.duck_percent,
                events=[
                    {"at_ms": 0, "action": "next_start", "volume": self.duck_percent},
                    {"at_ms": 0, "action": "voice_start", "variant": variant},
                    {"at_ms": voice_ms, "action": "voice_end"},
                    {"at_ms": voice_ms + 200, "action": "next_fade", "to": 100, "duration_ms": 800},
                    {"at_ms": vocal_start, "action": "vocal_post"},
                ],
                reason="Voice fits before first vocal with a 500 ms safety post",
            )

        if outro_window >= voice_ms + 300 and end_type not in {"cold", "hard"}:
            voice_start = -voice_ms - 200
            return TransitionPlan(
                transition_type="talk_over_outro",
                variant=variant,
                voice_duration_ms=voice_ms,
                duck_percent=self.duck_percent,
                events=[
                    {"at_ms": voice_start - 500, "action": "current_fade", "to": 32, "duration_ms": 500},
                    {"at_ms": voice_start, "action": "voice_start", "variant": variant},
                    {"at_ms": -200, "action": "voice_end"},
                    {"at_ms": 0, "action": "next_start", "volume": 100},
                ],
                reason="Current track has a measured outro window",
            )

        # Unknown metadata must never make the automation miss a transition.
        return TransitionPlan(
            transition_type="between",
            variant=variant,
            voice_duration_ms=voice_ms,
            duck_percent=self.duck_percent,
            events=[
                {"at_ms": 0, "action": "voice_start", "variant": variant},
                {"at_ms": voice_ms, "action": "voice_end"},
                {"at_ms": voice_ms + 150, "action": "next_start", "volume": 100},
            ],
            reason="No trusted intro/outro markers; using a safe deterministic break",
        )
