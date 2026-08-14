from datetime import date
from collections import defaultdict


def compute_fairness_score(
    assignments: list[tuple[int, date]],
    special_days: list,
) -> dict[int, float]:
    weight_by_date = {sd.date: sd.weight for sd in special_days}
    scores: dict[int, float] = defaultdict(float)
    for user_id, d in assignments:
        scores[user_id] += weight_by_date.get(d, 1.0)
    return dict(scores)


def compute_target_duties(doctors, total_days: int, doctors_per_day: int) -> dict[int, float]:
    total_factor = sum(d.part_time_factor for d in doctors)
    total_assignments = total_days * doctors_per_day
    return {d.id: (d.part_time_factor / total_factor) * total_assignments for d in doctors}
