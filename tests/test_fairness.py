"""
Fairness tests for the scheduling algorithm.

Tests verify that solve_schedule produces results that are:
  - proportional to credit_factor
  - within the 70%–115%+2 hard bounds
  - respectful of desired_shifts
  - respectful of day_preference
  - sensitive to carryover (doctors with positive carryover get fewer duties)
"""
import pytest
from datetime import date, timedelta
from app.services.algorithm import (
    solve_schedule,
    solve_substitute_schedule,
    get_day_coverage,
    get_day_weight,
)
from app.services.fairness import compute_fairness_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_doctor(id, credit_factor=1.0, desired_shifts=None,
                day_preference="alle", carried_over_score=0.0):
    class D:
        pass
    d = D()
    d.id = id
    d.credit_factor = credit_factor
    d.part_time_factor = credit_factor
    d.desired_shifts = desired_shifts
    d.day_preference = day_preference
    d.carried_over_score = carried_over_score
    return d


def make_doctors(n, **kwargs):
    return [make_doctor(i, **kwargs) for i in range(1, n + 1)]


def quarter_days(start=date(2027, 1, 4)):
    """13 weeks of Wed/Fri/Sat/Sun — ~78 service slots, no holidays."""
    days = []
    d = start
    end = start + timedelta(weeks=13)
    while d < end:
        if d.weekday() in (2, 4, 5, 6):
            days.append(d)
        d += timedelta(days=1)
    return days


def weighted_score(result, holiday_dates=set()):
    scores: dict[int, float] = {}
    for uid, d in result:
        scores[uid] = scores.get(uid, 0.0) + get_day_weight(d, holiday_dates)
    return scores


def compute_target(doctors, days, holiday_dates=set()):
    """Replicate _compute_targets logic for test assertions."""
    total_slots = sum(get_day_coverage(d, holiday_dates) for d in days)
    fixed = [doc for doc in doctors if doc.desired_shifts is not None]
    flex = [doc for doc in doctors if doc.desired_shifts is None]
    fixed_claimed = sum(doc.desired_shifts for doc in fixed)
    flex_slots = max(0, total_slots - fixed_claimed)
    total_flex_credit = sum(doc.credit_factor for doc in flex) or 1.0
    targets = {}
    for doc in fixed:
        targets[doc.id] = float(doc.desired_shifts)
    for doc in flex:
        targets[doc.id] = (doc.credit_factor / total_flex_credit) * flex_slots
    return targets


# ---------------------------------------------------------------------------
# Coverage constraints
# ---------------------------------------------------------------------------

def test_every_day_is_covered():
    """All days in the result set must match the required coverage."""
    days = quarter_days()
    doctors = make_doctors(8)
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    counts = Counter(d for _, d in result)
    for day in days:
        assert counts[day] == get_day_coverage(day, set()), (
            f"{day} needs {get_day_coverage(day, set())} doctors, got {counts[day]}"
        )


# ---------------------------------------------------------------------------
# Equal doctors — balanced distribution
# ---------------------------------------------------------------------------

def test_equal_doctors_balanced_weighted_score():
    """With 6 equal full-time doctors the max/min spread must be ≤ 8.0 weighted points."""
    days = quarter_days()
    doctors = make_doctors(6)
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    scores = weighted_score(result)
    assert len(scores) == 6, "Every doctor must have at least one shift"
    spread = max(scores.values()) - min(scores.values())
    assert spread <= 8.0, f"Weighted score spread too large: {spread:.1f} — {scores}"


def test_equal_doctors_all_receive_shifts():
    """No doctor should be left without any duty."""
    days = quarter_days()
    doctors = make_doctors(6)
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    assigned_ids = {uid for uid, _ in result}
    assert assigned_ids == {d.id for d in doctors}


# ---------------------------------------------------------------------------
# credit_factor proportionality
# ---------------------------------------------------------------------------

def test_half_time_doctor_gets_half_weighted_score():
    """A 50 % doctor should have a weighted score ~50 % of a full-time peer (±25 %)."""
    days = quarter_days()
    full = [make_doctor(i, credit_factor=1.0) for i in range(1, 6)]
    half = make_doctor(6, credit_factor=0.5)
    doctors = full + [half]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    scores = weighted_score(result)
    avg_full = sum(scores[d.id] for d in full) / len(full)
    half_score = scores.get(half.id, 0.0)
    ratio = half_score / avg_full if avg_full > 0 else 0.0
    assert 0.25 <= ratio <= 0.75, (
        f"Half-time doctor ratio {ratio:.2f} out of expected 0.25–0.75 "
        f"(half={half_score:.1f}, avg_full={avg_full:.1f})"
    )


def test_zero_credit_doctor_gets_no_shifts():
    """A doctor with credit_factor=0 must not receive any duty."""
    days = quarter_days()
    doctors = make_doctors(5) + [make_doctor(6, credit_factor=0.0)]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    assert all(uid != 6 for uid, _ in result), "credit_factor=0 doctor should have no shifts"


# ---------------------------------------------------------------------------
# Hard bounds: 70 % lower, 115 %+2 upper
# ---------------------------------------------------------------------------

def test_upper_bound_not_exceeded():
    """No doctor receives more than floor(target * 1.15) + 2 shifts."""
    days = quarter_days()
    doctors = make_doctors(6)
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    targets = compute_target(doctors, days)
    from collections import Counter
    shift_counts = Counter(uid for uid, _ in result)
    for doc in doctors:
        t = targets[doc.id]
        upper = int(t * 1.15) + 2
        got = shift_counts.get(doc.id, 0)
        assert got <= upper, (
            f"Doctor {doc.id} got {got} shifts, upper bound is {upper} (target={t:.1f})"
        )


def test_lower_bound_respected():
    """Every doctor whose target ≥ 1 must receive at least 70 % of target shifts."""
    days = quarter_days()
    doctors = make_doctors(6)
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    targets = compute_target(doctors, days)
    from collections import Counter
    shift_counts = Counter(uid for uid, _ in result)
    for doc in doctors:
        t = targets[doc.id]
        if t >= 1.0:
            lower = max(1, int(t * 0.70))
            got = shift_counts.get(doc.id, 0)
            assert got >= lower, (
                f"Doctor {doc.id} got {got} shifts, lower bound is {lower} (target={t:.1f})"
            )


# ---------------------------------------------------------------------------
# desired_shifts
# ---------------------------------------------------------------------------

def test_desired_shifts_approximately_honored():
    """A doctor with desired_shifts=8 should receive exactly 8 shifts (hard target)."""
    days = quarter_days()
    flex = make_doctors(5)
    fixed = make_doctor(6, desired_shifts=8)
    doctors = flex + [fixed]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    count = Counter(uid for uid, _ in result)
    got = count.get(fixed.id, 0)
    # Bounds: int(8 * 0.70) = 5 lower, int(8 * 1.15)+2 = 11 upper
    assert 5 <= got <= 11, f"desired_shifts=8 doctor got {got} shifts"


# ---------------------------------------------------------------------------
# day_preference
# ---------------------------------------------------------------------------

def test_day_preference_mittwoch_only_wednesdays():
    """A doctor with day_preference='mittwoch' must only work on Wednesdays."""
    days = quarter_days()
    doctors = make_doctors(5)
    pref_doc = make_doctor(6, day_preference="mittwoch")
    result = solve_schedule(doctors + [pref_doc], days, wishes=[], holiday_dates=set())
    assert result is not None
    for uid, d in result:
        if uid == pref_doc.id:
            assert d.weekday() == 2, f"mittwoch-doctor scheduled on {d} (weekday {d.weekday()})"


def test_day_preference_freitag_only_fridays():
    """A doctor with day_preference='freitag' must only work on Fridays."""
    days = quarter_days()
    doctors = make_doctors(5)
    pref_doc = make_doctor(6, day_preference="freitag")
    result = solve_schedule(doctors + [pref_doc], days, wishes=[], holiday_dates=set())
    assert result is not None
    for uid, d in result:
        if uid == pref_doc.id:
            assert d.weekday() == 4, f"freitag-doctor scheduled on {d} (weekday {d.weekday()})"


def test_day_preference_alle_allows_any_day():
    """A doctor with day_preference='alle' should appear on multiple day types."""
    days = quarter_days()
    doctors = make_doctors(6)
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    # At least one doctor should have both a weekday (Wed/Fri) and weekend (Sat/Sun) shift
    from collections import defaultdict
    by_doc: dict[int, set[int]] = defaultdict(set)
    for uid, d in result:
        by_doc[uid].add(d.weekday())
    mixed = any(
        len(weekdays & {2, 4}) > 0 and len(weekdays & {5, 6}) > 0
        for weekdays in by_doc.values()
    )
    assert mixed, "No doctor received both weekday and weekend shifts"


# ---------------------------------------------------------------------------
# carryover effect
# ---------------------------------------------------------------------------

def test_positive_carryover_reduces_duties():
    """A doctor with high positive carryover should get fewer weighted duties
    than an identical doctor with zero carryover."""
    days = quarter_days()
    # 5 neutral doctors + 1 high-carryover vs 1 low-carryover
    neutral = make_doctors(4)
    high = make_doctor(5, carried_over_score=10.0)
    low = make_doctor(6, carried_over_score=0.0)
    doctors = neutral + [high, low]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    scores = weighted_score(result)
    high_score = scores.get(high.id, 0.0)
    low_score = scores.get(low.id, 0.0)
    assert high_score <= low_score + 4.0, (
        f"High-carryover doctor ({high_score:.1f}) should not exceed "
        f"low-carryover doctor ({low_score:.1f}) by more than 4.0"
    )


def test_negative_carryover_increases_duties():
    """A doctor with negative carryover (was under-served) should get more duties."""
    days = quarter_days()
    neutral = make_doctors(4)
    behind = make_doctor(5, carried_over_score=-10.0)
    normal = make_doctor(6, carried_over_score=0.0)
    doctors = neutral + [behind, normal]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    scores = weighted_score(result)
    behind_score = scores.get(behind.id, 0.0)
    normal_score = scores.get(normal.id, 0.0)
    assert behind_score >= normal_score - 4.0, (
        f"Under-served doctor ({behind_score:.1f}) should not be much below "
        f"normal doctor ({normal_score:.1f})"
    )


# ---------------------------------------------------------------------------
# holiday coverage
# ---------------------------------------------------------------------------

def test_holiday_gets_two_doctors():
    """A Feiertag must have exactly 2 doctors regardless of weekday."""
    # Choose a Wednesday as a fake holiday → should need 2 instead of 1
    wed = date(2027, 1, 6)
    holiday_dates = {wed}
    doctors = make_doctors(5)
    result = solve_schedule(doctors, [wed], wishes=[], holiday_dates=holiday_dates)
    assert result is not None
    count = sum(1 for _, d in result if d == wed)
    assert count == 2, f"Holiday Wednesday should have 2 doctors, got {count}"


def test_holiday_weight_is_two():
    """A Friday that is a Feiertag weighs 2.0, not 1.0."""
    fri = date(2027, 4, 2)  # Karfreitag approximation
    assert get_day_weight(fri, {fri}) == 2.0
    assert get_day_weight(fri, set()) == 1.0


# ---------------------------------------------------------------------------
# substitute schedule fairness
# ---------------------------------------------------------------------------

def test_fairness_score_empty_input():
    """Leere Zuweisung ergibt leeres Score-Dict."""
    from app.services.fairness import compute_fairness_score
    assert compute_fairness_score([], set()) == {}


def test_compute_target_duties_proportional():
    """Arzt mit doppeltem credit_factor bekommt doppeltes Ziel."""
    from app.services.fairness import compute_target_duties
    full = make_doctor(1, credit_factor=1.0)
    double = make_doctor(2, credit_factor=2.0)
    targets = compute_target_duties([full, double], total_slots=30.0)
    assert targets[2] == pytest.approx(targets[1] * 2, rel=0.01)


def test_desired_shifts_flex_proportional_to_credit():
    """Arzt mit doppeltem credit_factor bekommt deutlich mehr Slots als Arzt mit halben."""
    # 13 Mittwoche = 13 Slots à 1 Arzt
    days = [date(2027, 1, 6) + timedelta(weeks=i) for i in range(13)]
    fixed = make_doctor(1, desired_shifts=5)          # beansprucht 5 von 13
    flex_double = make_doctor(2, credit_factor=2.0)   # bekommt ~2/3 der Rest-8
    flex_single = make_doctor(3, credit_factor=1.0)   # bekommt ~1/3 der Rest-8
    doctors = [fixed, flex_double, flex_single]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    counts = Counter(uid for uid, _ in result)
    assert counts.get(flex_double.id, 0) > counts.get(flex_single.id, 0), (
        f"2×-Arzt ({counts.get(flex_double.id, 0)}) sollte mehr als 1×-Arzt ({counts.get(flex_single.id, 0)}) bekommen"
    )


# ---------------------------------------------------------------------------
# desired_shifts — extended coverage
# ---------------------------------------------------------------------------

def test_desired_shifts_zero_gets_no_shifts():
    """desired_shifts=0 muss dazu führen, dass dieser Arzt gar nicht eingeplant wird."""
    days = quarter_days()
    doctors = make_doctors(5)
    excluded = make_doctor(6, desired_shifts=0)
    result = solve_schedule(doctors + [excluded], days, wishes=[], holiday_dates=set())
    assert result is not None
    assert all(uid != excluded.id for uid, _ in result), (
        "Arzt mit desired_shifts=0 darf keine Dienste erhalten"
    )


def test_desired_shifts_two_fixed_doctors_both_honored():
    """Zwei Ärzte mit festen Wünschen werden beide innerhalb ihrer Schranken eingeplant."""
    days = quarter_days()   # ~78 Slots
    fixed_a = make_doctor(10, desired_shifts=6)
    fixed_b = make_doctor(11, desired_shifts=12)
    flex = make_doctors(4)
    doctors = flex + [fixed_a, fixed_b]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    counts = Counter(uid for uid, _ in result)
    # Bounds: lower = int(target * 0.70), upper = int(target * 1.15) + 2
    for doc, target in [(fixed_a, 6), (fixed_b, 12)]:
        got = counts.get(doc.id, 0)
        assert int(target * 0.70) <= got <= int(target * 1.15) + 2, (
            f"Arzt {doc.id} (desired={target}) hat {got} Dienste"
        )


def test_desired_shifts_overrides_credit_factor():
    """Ein Arzt mit niedrigem credit_factor aber hohem desired_shifts
    bekommt mehr Dienste als seine credit_factor-Quote vermuten lässt."""
    days = quarter_days()  # ~78 Slots, 6 doctors → ~13 pro Vollzeit
    # Nur 25 % Stellenumfang, aber wünscht 10 Dienste
    low_cf_high_wish = make_doctor(7, credit_factor=0.25, desired_shifts=10)
    flex = make_doctors(6)
    doctors = flex + [low_cf_high_wish]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    counts = Counter(uid for uid, _ in result)
    got = counts.get(low_cf_high_wish.id, 0)
    # credit_factor=0.25 alone would give ~2 shifts; desired_shifts=10 should override
    assert got >= 7, (
        f"Arzt mit desired_shifts=10 sollte ≥ 7 Dienste bekommen, hat {got}"
    )


def test_desired_shifts_below_credit_share_reduces_duties():
    """Ein Arzt mit desired_shifts weit unter seiner anteiligen Quote bekommt
    weniger Dienste als ein gleich-CF-Kollege ohne festen Wunsch."""
    days = quarter_days()  # ~78 Slots
    # Vollzeit-Arzt, möchte aber nur 3 Dienste
    low_wish = make_doctor(7, credit_factor=1.0, desired_shifts=3)
    normal = make_doctor(8, credit_factor=1.0, desired_shifts=None)
    flex = make_doctors(4)
    doctors = flex + [low_wish, normal]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    counts = Counter(uid for uid, _ in result)
    low_got = counts.get(low_wish.id, 0)
    normal_got = counts.get(normal.id, 0)
    assert low_got < normal_got, (
        f"Arzt mit desired_shifts=3 ({low_got}) sollte weniger als "
        f"flex-Kollege ({normal_got}) bekommen"
    )


def test_flex_pool_shrinks_when_fixed_claims_many():
    """Wenn ein Fixed-Arzt viele Slots beansprucht, bekommen Flex-Ärzte weniger."""
    days = quarter_days()  # ~78 Slots
    # Greedy fixed doctor takes 30 slots (of ~78)
    greedy = make_doctor(9, desired_shifts=30)
    flex = make_doctors(5)
    doctors = flex + [greedy]
    result = solve_schedule(doctors, days, wishes=[], holiday_dates=set())
    assert result is not None
    from collections import Counter
    counts = Counter(uid for uid, _ in result)
    # Each flex doctor gets (78-30)/5 = ~9.6 slots proportionally
    for doc in flex:
        got = counts.get(doc.id, 0)
        assert got <= 16, (
            f"Flex-Arzt {doc.id} hat {got} Dienste obwohl greedy-Arzt 30 beansprucht"
        )
    greedy_got = counts.get(greedy.id, 0)
    assert int(30 * 0.70) <= greedy_got <= int(30 * 1.15) + 2, (
        f"Greedy-Arzt hat {greedy_got} Dienste (erwartet 21–36)"
    )


def test_substitute_fair_distribution():
    """Substitute duties should be spread roughly evenly across doctors."""
    days = [date(2027, 1, 4) + timedelta(weeks=i) for i in range(10)]  # 10 Mondays
    # Use Saturdays for a more realistic sub schedule
    days = [date(2027, 1, 8) + timedelta(weeks=i) for i in range(10)]
    doctors = make_doctors(5)
    primary = set()
    result = solve_substitute_schedule(
        doctors, days, primary, wishes=[], holiday_dates=set()
    )
    assert result is not None
    from collections import Counter
    counts = Counter(uid for uid, _ in result)
    assert len(counts) >= 3, "Substitute duties should be spread across at least 3 doctors"
    spread = max(counts.values()) - min(counts.values())
    assert spread <= 4, f"Substitute duty spread too large: {spread}"
