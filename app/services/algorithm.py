from datetime import date, timedelta
from ortools.sat.python import cp_model
from app.services.fairness import compute_target_duties


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def solve_schedule(
    doctors: list,
    days: list[date],
    wishes: list,
    special_days: list,
    doctors_per_day: int = 2,
    time_limit_seconds: int = 30,
    min_free_weekends_between: int = 2,
) -> list[tuple[int, date]] | None:

    model = cp_model.CpModel()
    n_days = len(days)
    day_idx = {d: i for i, d in enumerate(days)}
    doctor_ids = [doc.id for doc in doctors]

    x = {(doc.id, i): model.new_bool_var(f"x_{doc.id}_{i}")
         for doc in doctors for i in range(n_days)}

    # Each planned day gets exactly the required number of doctors
    for i in range(n_days):
        model.add(sum(x[did, i] for did in doctor_ids) == doctors_per_day)

    # No two consecutive planned days for the same doctor
    for doc in doctors:
        for i in range(n_days - 1):
            model.add(x[doc.id, i] + x[doc.id, i + 1] <= 1)

    # At least `min_free_weekends_between` free weekends between any two
    # weekend/holiday duties. Uses calendar week distance for correctness.
    holiday_dates = {sd.date for sd in special_days}

    def is_weekend_or_holiday(d: date) -> bool:
        return d.weekday() >= 5 or d in holiday_dates

    required_week_gap = min_free_weekends_between + 1

    for doc in doctors:
        for i in range(n_days):
            if not is_weekend_or_holiday(days[i]):
                continue
            mon_i = _monday_of_week(days[i])
            for j in range(i + 1, n_days):
                week_gap = (_monday_of_week(days[j]) - mon_i).days // 7
                if week_gap >= required_week_gap:
                    break
                if is_weekend_or_holiday(days[j]):
                    model.add(x[doc.id, i] + x[doc.id, j] <= 1)

    # Fair-share targets for this period
    targets = compute_target_duties(doctors, n_days, doctors_per_day)

    # Maximum duties: ~15% above fair share (ceiling)
    for doc in doctors:
        max_duties = int(targets[doc.id] * 1.15) + 2
        model.add(sum(x[doc.id, i] for i in range(n_days)) <= max_duties)

    # Minimum duties: 70% of fair share, but only when the doctor is expected
    # to work at least 1 shift (avoids infeasibility in short periods)
    for doc in doctors:
        target = targets[doc.id]
        if target >= 1.0:
            min_duties = max(1, int(target * 0.70))
            model.add(sum(x[doc.id, i] for i in range(n_days)) >= min_duties)

    # Hard unavailability wishes
    for wish in wishes:
        if wish.wish_type == "negative" and wish.priority == "hard" and wish.date in day_idx:
            model.add(x[wish.user_id, day_idx[wish.date]] == 0)

    # Objective: minimise weighted fairness deviation (adjusted by carryover from
    # previous periods) + soft-wish violations; maximise positive wishes
    weight_by_date = {sd.date: int(sd.weight * 100) for sd in special_days}

    fairness_penalties = []
    for doc in doctors:
        weighted_sum = sum(
            x[doc.id, i] * weight_by_date.get(days[i], 100)
            for i in range(n_days)
        )
        target_scaled = int(targets[doc.id] * 100)
        # Subtract carryover: doctors who worked more in the past get a lower
        # effective target so the algorithm compensates automatically.
        carryover_scaled = int(getattr(doc, "carried_over_score", 0.0) * 100)
        adjusted_target = target_scaled - carryover_scaled

        dev = model.new_int_var(-20000, 20000, f"dev_{doc.id}")
        model.add(dev == weighted_sum - adjusted_target)
        abs_dev = model.new_int_var(0, 20000, f"absdev_{doc.id}")
        model.add_abs_equality(abs_dev, dev)
        fairness_penalties.append(abs_dev)

    wish_bonus = []
    for wish in wishes:
        if wish.date in day_idx and wish.wish_type == "positive":
            wish_bonus.append(x[wish.user_id, day_idx[wish.date]])
        elif wish.date in day_idx and wish.wish_type == "negative" and wish.priority == "soft":
            fairness_penalties.append(x[wish.user_id, day_idx[wish.date]])

    model.minimize(sum(fairness_penalties) * 10 - sum(wish_bonus))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    result = []
    for doc in doctors:
        for i, day in enumerate(days):
            if solver.value(x[doc.id, i]):
                result.append((doc.id, day))
    return result
