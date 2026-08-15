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
    """Mi, Fr, Sa, So werden abgedeckt."""
    # Mi 6.1., Fr 8.1., Sa 9.1., So 10.1.2027
    doctors = make_doctors(8)
    days = [date(2027, 1, 6), date(2027, 1, 8), date(2027, 1, 9), date(2027, 1, 10)]
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


# ---------------------------------------------------------------------------
# solve_substitute_schedule tests (Task 5)
# ---------------------------------------------------------------------------

def test_substitute_not_already_primary():
    """Bereitschaftsarzt darf an demselben Tag nicht Primärarzt sein."""
    from app.services.algorithm import solve_substitute_schedule
    doctors = make_doctors(6)
    sat = date(2027, 1, 9)
    # Ärzte 1 und 2 sind Primärärzte an diesem Samstag
    primary_set = {(1, sat), (2, sat)}
    result = solve_substitute_schedule(
        doctors, [sat], primary_set, wishes=[], holiday_dates=set()
    )
    assert result is not None
    sub_on_sat = [(uid, d) for uid, d in result if d == sat]
    assert len(sub_on_sat) == 1
    assert sub_on_sat[0][0] not in (1, 2)


def test_coverage_sunday():
    """Sonntag braucht genau 2 Ärzte."""
    sun = date(2027, 1, 10)
    assert sun.weekday() == 6
    assert get_day_coverage(sun, set()) == 2


def test_coverage_holiday_on_friday():
    """Feiertag an einem Freitag → 2 Ärzte (nicht 1)."""
    fri = date(2027, 1, 8)
    assert fri.weekday() == 4
    assert get_day_coverage(fri, set()) == 1         # kein Feiertag: 1
    assert get_day_coverage(fri, {fri}) == 2         # Feiertag: 2


def test_infeasible_returns_none():
    """Zu wenige Ärzte für die Abdeckung → None."""
    sat = date(2027, 1, 9)  # Samstag braucht 2 Ärzte
    result = solve_schedule(make_doctors(1), [sat], wishes=[], holiday_dates=set())
    assert result is None


def test_no_duplicate_assignment_on_same_day():
    """Kein Arzt darf an demselben Tag doppelt eingeplant sein."""
    doctors = make_doctors(8)
    days = [date(2027, 1, 6) + timedelta(weeks=i) for i in range(8)]  # 8 Mittwoche
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    seen: set[tuple[int, date]] = set()
    for uid, d in result:
        assert (uid, d) not in seen, f"Arzt {uid} doppelt an {d}"
        seen.add((uid, d))


def test_positive_wish_preferred():
    """Ein positiver Wunsch auf einem Tag sollte zu einer Zuweisung führen."""
    fri = date(2027, 2, 5)  # Freitag, 1 Arzt nötig
    doctors = make_doctors(3)

    class W:
        user_id = 1
        date = fri
        wish_type = "positive"
        priority = "soft"

    result = solve_schedule(doctors, [fri], wishes=[W()], holiday_dates=set())
    assert result is not None
    assert any(uid == 1 for uid, _ in result), "Arzt mit positivem Wunsch sollte eingeplant werden"


def test_soft_negative_wish_avoided():
    """Ein 'Lieber nicht'-Wunsch wird vermieden, wenn genug andere Ärzte verfügbar sind."""
    wed = date(2027, 2, 3)  # Mittwoch, 1 Arzt nötig
    doctors = make_doctors(5)  # viel Slack → prefer_not kann eingehalten werden

    class W:
        user_id = 1
        date = wed
        wish_type = "negative"
        priority = "soft"

    result = solve_schedule(doctors, [wed], wishes=[W()], holiday_dates=set())
    assert result is not None
    assert all(uid != 1 for uid, _ in result), "Arzt mit 'Lieber nicht' sollte nicht eingeplant werden"
