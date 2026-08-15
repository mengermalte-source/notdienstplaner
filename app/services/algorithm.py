import random
from datetime import date
from ortools.sat.python import cp_model



def get_day_coverage(d: date, holiday_dates: set, special_day_overrides: dict | None = None) -> int:
    """Anzahl benötigter Ärzte für diesen Tag."""
    if special_day_overrides and d in special_day_overrides:
        return special_day_overrides[d]
    if d in holiday_dates:
        return 2
    if d.weekday() in (2, 4):  # Mittwoch, Freitag
        return 1
    return 2  # Samstag, Sonntag


def get_day_weight(d: date, holiday_dates: set) -> float:
    """Fairness-Gewicht: Freitag (kein FT) = 1.0, alle anderen = 2.0."""
    if d.weekday() == 4 and d not in holiday_dates:
        return 1.0
    return 2.0


def _compute_targets(
    doctors: list,
    days: list[date],
    holiday_dates: set,
    special_day_overrides: dict | None = None,
) -> dict[int, float]:
    """
    Zweiphasige Zielberechnung:
    1. Ärzte mit desired_shifts bekommen ihren Wunschwert als Ziel.
    2. Restslots werden proportional zu credit_factor auf Minimum-Ärzte verteilt.
    """
    total_slots = sum(get_day_coverage(d, holiday_dates, special_day_overrides) for d in days)

    fixed = [doc for doc in doctors if getattr(doc, "desired_shifts", None) is not None]
    flex = [doc for doc in doctors if getattr(doc, "desired_shifts", None) is None]

    fixed_claimed = sum(doc.desired_shifts for doc in fixed)
    flex_slots = max(0, total_slots - fixed_claimed)
    total_flex_credit = sum(
        getattr(doc, "credit_factor", getattr(doc, "part_time_factor", 1.0))
        for doc in flex
    ) or 1.0

    targets: dict[int, float] = {}
    for doc in fixed:
        targets[doc.id] = float(doc.desired_shifts)
    for doc in flex:
        cf = getattr(doc, "credit_factor", getattr(doc, "part_time_factor", 1.0))
        targets[doc.id] = (cf / total_flex_credit) * flex_slots
    return targets


def solve_schedule(
    doctors: list,
    days: list[date],
    wishes: list,
    holiday_dates: set,
    time_limit_seconds: int = 30,
    holiday_carryover_penalty: dict | None = None,
    key_holiday_dates: dict | None = None,
    strict_wishes: bool = True,
    special_day_overrides: dict | None = None,
) -> list[tuple[int, date]] | None:

    if not doctors or not days:
        return None

    model = cp_model.CpModel()
    n_days = len(days)
    day_idx = {d: i for i, d in enumerate(days)}

    x = {(doc.id, i): model.new_bool_var(f"x_{doc.id}_{i}")
         for doc in doctors for i in range(n_days)}

    # Abdeckung pro Tag (Mi/Fr = 1, Sa/So/FT = 2, ggf. Sondertag-Override)
    for i, day in enumerate(days):
        req = get_day_coverage(day, holiday_dates, special_day_overrides)
        model.add(sum(x[doc.id, i] for doc in doctors) == req)

    # Tagespräferenz: nur Mittwoch oder nur Freitag
    for doc in doctors:
        pref = getattr(doc, "day_preference", "alle")
        pref_val = pref.value if hasattr(pref, "value") else str(pref)
        if pref_val == "mittwoch":
            for i, day in enumerate(days):
                if day.weekday() != 2:
                    model.add(x[doc.id, i] == 0)
        elif pref_val == "freitag":
            for i, day in enumerate(days):
                if day.weekday() != 4:
                    model.add(x[doc.id, i] == 0)

    # Kein Folgedienstverbot mehr (bewusst entfernt)

    # Targets (zweiphasig: Wunschanzahl + Proportional)
    targets = _compute_targets(doctors, days, holiday_dates, special_day_overrides)

    # Schranken (in Anzahl Dienste)
    for doc in doctors:
        t = targets[doc.id]
        shifts = sum(x[doc.id, i] for i in range(n_days))
        if t == 0.0:
            model.add(shifts == 0)
        else:
            if strict_wishes and t >= 1.0:
                model.add(shifts >= max(1, int(t * 0.70)))
            model.add(shifts <= int(t * 1.15) + 2)

    # Harte Ablehnungswünsche
    hard_wish_penalties = []
    for wish in wishes:
        if (getattr(wish, "wish_type", None) == "negative"
                and getattr(wish, "priority", None) == "hard"
                and wish.date in day_idx):
            if strict_wishes or getattr(wish, "is_vacation", False):
                model.add(x[wish.user_id, day_idx[wish.date]] == 0)
            else:
                hard_wish_penalties.append(x[wish.user_id, day_idx[wish.date]] * 500)

    # Objektiv: Fairness-Abweichung + Wunschboni
    weight_by_day = {i: int(get_day_weight(days[i], holiday_dates) * 100)
                     for i in range(n_days)}

    total_weighted_slots = sum(
        get_day_weight(days[i], holiday_dates) * get_day_coverage(days[i], holiday_dates, special_day_overrides)
        for i in range(n_days)
    )
    total_slots_count = sum(get_day_coverage(days[i], holiday_dates, special_day_overrides) for i in range(n_days))
    avg_weight = total_weighted_slots / total_slots_count if total_slots_count > 0 else 1.0

    fairness_penalties = []

    # Feiertagswiederholer-Strafterm: wer letztes Mal an einem Schlüsselfeiertag Dienst hatte,
    # bekommt einen Soft-Malus für dieselben Feiertage in dieser Periode
    if holiday_carryover_penalty and key_holiday_dates:
        key_holiday_dates_in_period: dict[str, list[int]] = {}
        for key, date_set in key_holiday_dates.items():
            key_holiday_dates_in_period[key] = [day_idx[d] for d in date_set if d in day_idx]
        for doc in doctors:
            worked_keys = holiday_carryover_penalty.get(doc.id, set())
            for key, indices in key_holiday_dates_in_period.items():
                if key in worked_keys:
                    for i in indices:
                        fairness_penalties.append(x[doc.id, i] * 200)

    for doc in doctors:
        weighted_sum = sum(x[doc.id, i] * weight_by_day[i] for i in range(n_days))
        # Gewichtetes Ziel: count_target × Durchschnittsgewicht (gleiche Einheit wie weighted_sum)
        weighted_target_scaled = int(targets[doc.id] * avg_weight * 100)
        carryover_scaled = int(getattr(doc, "carried_over_score", 0.0) * 100)
        adjusted_target = weighted_target_scaled - carryover_scaled

        dev = model.new_int_var(-40000, 40000, f"dev_{doc.id}")
        model.add(dev == weighted_sum - adjusted_target)
        abs_dev = model.new_int_var(0, 40000, f"absdev_{doc.id}")
        model.add_abs_equality(abs_dev, dev)
        fairness_penalties.append(abs_dev)

    wish_bonus = []
    for wish in wishes:
        if wish.date in day_idx and getattr(wish, "wish_type", None) == "positive":
            wish_bonus.append(x[wish.user_id, day_idx[wish.date]])
        elif (wish.date in day_idx
              and getattr(wish, "wish_type", None) == "negative"
              and getattr(wish, "priority", None) == "soft"):
            fairness_penalties.append(x[wish.user_id, day_idx[wish.date]])

    model.minimize(sum(fairness_penalties) * 10 + sum(hard_wish_penalties) - sum(wish_bonus))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.random_seed = random.randint(0, 2**31 - 1)
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    return [
        (doc.id, days[i])
        for doc in doctors
        for i in range(n_days)
        if solver.value(x[doc.id, i])
    ]


def solve_substitute_schedule(
    doctors: list,
    days: list[date],
    primary_assignments: set[tuple[int, date]],
    wishes: list,
    holiday_dates: set,
    time_limit_seconds: int = 30,
    special_day_overrides: dict | None = None,
) -> list[tuple[int, date]] | None:
    """
    Plant 1 Bereitschaftsarzt pro Tag (nur Dez–Apr).
    Bereitschaftsarzt darf an demselben Tag nicht Primärarzt sein.
    Eigene Fairness-Berechnung via sub_carried_over_score
    (erwartet als carried_over_score auf den übergebenen doctor-Objekten).
    """
    if not doctors or not days:
        return None

    model = cp_model.CpModel()
    n_days = len(days)
    day_idx = {d: i for i, d in enumerate(days)}

    x = {(doc.id, i): model.new_bool_var(f"sub_{doc.id}_{i}")
         for doc in doctors for i in range(n_days)}

    # Genau 1 Bereitschaftsarzt pro Tag
    for i in range(n_days):
        model.add(sum(x[doc.id, i] for doc in doctors) == 1)

    # Darf nicht bereits Primärarzt an diesem Tag sein
    for (uid, d) in primary_assignments:
        if d in day_idx:
            for doc in doctors:
                if doc.id == uid:
                    model.add(x[uid, day_idx[d]] == 0)

    # Harte Ablehnungswünsche
    for wish in wishes:
        if (getattr(wish, "wish_type", None) == "negative"
                and getattr(wish, "priority", None) == "hard"
                and wish.date in day_idx):
            for doc in doctors:
                if doc.id == wish.user_id:
                    model.add(x[wish.user_id, day_idx[wish.date]] == 0)

    # Fairness-Ziel: proportional zu credit_factor, Basis = sub_carried_over_score
    total_slots = len(days)
    total_credit = sum(doc.credit_factor for doc in doctors) or 1.0
    sub_targets = {doc.id: (doc.credit_factor / total_credit) * total_slots for doc in doctors}

    fairness_penalties = []
    weight_by_day = {i: int(get_day_weight(days[i], holiday_dates) * 100) for i in range(n_days)}

    for doc in doctors:
        weighted_sum = sum(x[doc.id, i] * weight_by_day[i] for i in range(n_days))
        target_scaled = int(sub_targets[doc.id] * 100)
        carryover_scaled = int(getattr(doc, "carried_over_score", 0.0) * 100)

        dev = model.new_int_var(-10000, 10000, f"subdev_{doc.id}")
        model.add(dev == weighted_sum - (target_scaled - carryover_scaled))
        abs_dev = model.new_int_var(0, 10000, f"subabsdev_{doc.id}")
        model.add_abs_equality(abs_dev, dev)
        fairness_penalties.append(abs_dev)

    model.minimize(sum(fairness_penalties) * 10)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.random_seed = random.randint(0, 2**31 - 1)
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    return [
        (doc.id, days[i])
        for doc in doctors
        for i in range(n_days)
        if solver.value(x[doc.id, i])
    ]
