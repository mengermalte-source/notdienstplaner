import pytest
from datetime import date, timedelta
from app.services.algorithm import solve_schedule, get_day_coverage, get_day_weight
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


# ---------------------------------------------------------------------------
# get_day_coverage tests (Step 1)
# ---------------------------------------------------------------------------

def test_coverage_wednesday():
    # Mittwoch ohne Feiertag → 1 Arzt
    wed = date(2027, 1, 6)  # Mittwoch
    assert wed.weekday() == 2
    assert get_day_coverage(wed, set()) == 1


def test_coverage_friday():
    # Freitag ohne Feiertag → 1 Arzt
    fri = date(2027, 1, 8)
    assert get_day_coverage(fri, set()) == 1


def test_coverage_saturday():
    sat = date(2027, 1, 9)
    assert get_day_coverage(sat, set()) == 2


def test_coverage_holiday_on_wednesday():
    # Feiertag (auch wenn Mittwoch) → 2 Ärzte
    wed = date(2027, 1, 6)
    assert get_day_coverage(wed, {wed}) == 2


# ---------------------------------------------------------------------------
# get_day_weight tests (Step 1)
# ---------------------------------------------------------------------------

def test_weight_friday():
    fri = date(2027, 1, 8)
    assert get_day_weight(fri, set()) == 1.0


def test_weight_wednesday():
    wed = date(2027, 1, 6)
    assert get_day_weight(wed, set()) == 2.0


def test_weight_saturday():
    sat = date(2027, 1, 9)
    assert get_day_weight(sat, set()) == 2.0


def test_weight_holiday():
    # Feiertag immer 2.0, egal welcher Wochentag
    fri_holiday = date(2027, 4, 2)  # Karfreitag
    assert get_day_weight(fri_holiday, {fri_holiday}) == 2.0


# ---------------------------------------------------------------------------
# solve_schedule tests (Step 7)
# ---------------------------------------------------------------------------

def test_basic_schedule_covers_all_days():
    """Mittwoch, Freitag, Samstag, Sonntag werden abgedeckt."""
    # Woche: Mi 5.1., Fr 7.1., Sa 8.1., So 9.1.2027
    doctors = make_doctors(8)
    days = [date(2027, 1, 5), date(2027, 1, 7), date(2027, 1, 8), date(2027, 1, 9)]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    assigned_days = {d for _, d in result}
    assert assigned_days == set(days)


def test_coverage_one_doctor_on_wednesday():
    """Mittwoch braucht genau 1 Arzt."""
    doctors = make_doctors(5)
    wed = date(2027, 1, 6)
    result = solve_schedule(doctors, [wed], wishes=[], holiday_dates=set())
    assert result is not None
    assert len([d for _, d in result if d == wed]) == 1


def test_coverage_two_doctors_on_saturday():
    """Samstag braucht genau 2 Ärzte."""
    doctors = make_doctors(5)
    sat = date(2027, 1, 9)
    result = solve_schedule(doctors, [sat], wishes=[], holiday_dates=set())
    assert result is not None
    assert len([d for _, d in result if d == sat]) == 2


def test_hard_negative_wish_respected():
    doctors = make_doctors(5)
    fri = date(2027, 2, 5)

    class W:
        user_id = 1
        date = fri
        wish_type = "negative"
        priority = "hard"

    result = solve_schedule(doctors, [fri], wishes=[W()], holiday_dates=set())
    assert result is not None
    assert (1, fri) not in result


# ---------------------------------------------------------------------------
# fairness score test
# ---------------------------------------------------------------------------

def test_fairness_score_uses_day_weight():
    # Sa = 2.0, Fr = 1.0
    sat = date(2027, 1, 9)  # Samstag
    fri = date(2027, 1, 8)  # Freitag
    assignments = [(1, sat), (1, sat), (2, fri)]
    scores = compute_fairness_score(assignments, set())
    # Doc 1: 2*2.0 = 4.0, Doc 2: 1*1.0 = 1.0
    assert scores[1] == pytest.approx(4.0)
    assert scores[2] == pytest.approx(1.0)
    assert scores[1] > scores[2]
