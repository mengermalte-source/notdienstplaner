import pytest
from datetime import date, timedelta
from app.services.algorithm import solve_schedule
from app.services.fairness import compute_fairness_score


def make_doctors(n, credit_factor=1.0, desired_shifts=None, day_preference="alle"):
    class D:
        def __init__(self, id):
            self.id = id
            self.credit_factor = credit_factor
            self.part_time_factor = credit_factor  # Legacy-Compat für fairness.py
            self.carried_over_score = 0.0
            self.desired_shifts = desired_shifts
            self.day_preference = day_preference
    return [D(i) for i in range(1, n + 1)]


def date_range(start, days):
    return [start + timedelta(days=i) for i in range(days)]


def test_basic_schedule_assigns_all_days():
    doctors = make_doctors(5)
    days = date_range(date(2027, 1, 1), 30)
    result = solve_schedule(doctors, days, wishes=[], special_days=[], doctors_per_day=2)
    assert result is not None
    assigned_days = {d for _, d in result}
    assert assigned_days == set(days)


def test_no_consecutive_days():
    doctors = make_doctors(5)
    days = date_range(date(2027, 1, 1), 30)
    result = solve_schedule(doctors, days, wishes=[], special_days=[], doctors_per_day=2)
    from collections import defaultdict
    by_doc = defaultdict(list)
    for uid, d in result:
        by_doc[uid].append(d)
    for uid, assigned in by_doc.items():
        sorted_dates = sorted(assigned)
        for i in range(len(sorted_dates) - 1):
            diff = (sorted_dates[i + 1] - sorted_dates[i]).days
            assert diff > 1, f"Arzt {uid} hat Folgedienste: {sorted_dates[i]}, {sorted_dates[i+1]}"


def test_hard_negative_wish_respected():
    doctors = make_doctors(5)
    days = date_range(date(2027, 2, 1), 14)

    class W:
        user_id = 1
        date = date(2027, 2, 5)
        wish_type = "negative"
        priority = "hard"

    result = solve_schedule(doctors, days, wishes=[W()], special_days=[], doctors_per_day=2)
    assert result is not None
    assert (1, date(2027, 2, 5)) not in result


def test_fairness_score():
    assignments = [(1, date(2027, 1, 1)), (1, date(2027, 1, 3)), (2, date(2027, 1, 2))]

    class SD:
        def __init__(self, d, w):
            self.date = d
            self.weight = w

    special_days = [SD(date(2027, 1, 1), 3.0)]
    scores = compute_fairness_score(assignments, special_days)
    assert scores[1] > scores[2]
