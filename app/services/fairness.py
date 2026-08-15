from datetime import date
from collections import defaultdict
from app.services.algorithm import get_day_weight


def compute_fairness_score(
    assignments: list[tuple[int, date]],
    holiday_dates: set,
) -> dict[int, float]:
    scores: dict[int, float] = defaultdict(float)
    for user_id, d in assignments:
        scores[user_id] += get_day_weight(d, holiday_dates)
    return dict(scores)


def compute_target_duties(doctors, total_slots: float) -> dict[int, float]:
    """Für Rückwärtskompatibilität (Publish-Logik). Nutzt credit_factor."""
    total_factor = sum(
        getattr(d, "credit_factor", getattr(d, "part_time_factor", 1.0))
        for d in doctors
    ) or 1.0
    return {
        d.id: (getattr(d, "credit_factor", getattr(d, "part_time_factor", 1.0)) / total_factor) * total_slots
        for d in doctors
    }
