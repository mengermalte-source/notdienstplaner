from datetime import date, timedelta
from ortools.sat.python import cp_model


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_day_coverage(d: date, holiday_dates: set) -> int:
    """Anzahl benötigter Ärzte für diesen Tag."""
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
) -> dict[int, float]:
    """
    Zweiphasige Zielberechnung:
    1. Ärzte mit desired_shifts bekommen ihren Wunschwert als Ziel.
    2. Restslots werden proportional zu credit_factor auf Minimum-Ärzte verteilt.
    """
    total_slots = sum(get_day_coverage(d, holiday_dates) for d in days)

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
    min_free_weekends_between: int = 2,
) -> list[tuple[int, date]] | None:

    if not doctors or not days:
        return None

    model = cp_model.CpModel()
    n_days = len(days)
    day_idx = {d: i for i, d in enumerate(days)}

    x = {(doc.id, i): model.new_bool_var(f"x_{doc.id}_{i}")
         for doc in doctors for i in range(n_days)}

    # Abdeckung pro Tag (Mi/Fr = 1, Sa/So/FT = 2)
    for i, day in enumerate(days):
        req = get_day_coverage(day, holiday_dates)
        model.add(sum(x[doc.id, i] for doc in doctors) == req)

    # Tagespräferenz: nur Mittwoch oder nur Freitag
    for doc in doctors:
        pref = getattr(doc, "day_preference", "alle")
        # Support DayPreference enum or string
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

    # Wochenendabstand: ≥2 freie Wochenenden zwischen Sa/So/FT-Diensten
    required_week_gap = min_free_weekends_between + 1
    for doc in doctors:
        for i in range(n_days):
            if not (days[i].weekday() >= 5 or days[i] in holiday_dates):
                continue
            mon_i = _monday_of_week(days[i])
            for j in range(i + 1, n_days):
                if not (days[j].weekday() >= 5 or days[j] in holiday_dates):
                    continue
                week_gap = (_monday_of_week(days[j]) - mon_i).days // 7
                if week_gap >= required_week_gap:
                    break
                model.add(x[doc.id, i] + x[doc.id, j] <= 1)

    # Targets (zweiphasig: Wunschanzahl + Proportional)
    targets = _compute_targets(doctors, days, holiday_dates)

    # Schranken
    for doc in doctors:
        t = targets[doc.id]
        if t >= 1.0:
            model.add(sum(x[doc.id, i] for i in range(n_days)) >= max(1, int(t * 0.70)))
        model.add(sum(x[doc.id, i] for i in range(n_days)) <= int(t * 1.15) + 2)

    # Harte Ablehnungswünsche
    for wish in wishes:
        if (getattr(wish, "wish_type", None) == "negative"
                and getattr(wish, "priority", None) == "hard"
                and wish.date in day_idx):
            model.add(x[wish.user_id, day_idx[wish.date]] == 0)

    # Objektiv: Fairness-Abweichung + Wunschboni
    # Gewicht aus get_day_weight (Mi/Sa/So/FT = 200, Fr = 100 in Ganzzahl)
    weight_by_day = {i: int(get_day_weight(days[i], holiday_dates) * 100)
                     for i in range(n_days)}

    fairness_penalties = []
    for doc in doctors:
        weighted_sum = sum(x[doc.id, i] * weight_by_day[i] for i in range(n_days))
        target_scaled = int(targets[doc.id] * 100)
        carryover_scaled = int(getattr(doc, "carried_over_score", 0.0) * 100)
        adjusted_target = target_scaled - carryover_scaled

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

    model.minimize(sum(fairness_penalties) * 10 - sum(wish_bonus))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
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
    min_free_weekends_between: int = 1,
) -> list[tuple[int, date]] | None:
    """
    Plant 1 Bereitschaftsarzt pro Tag (nur Dez–Apr).
    Bereitschaftsarzt darf an demselben Tag nicht Primärarzt sein.
    Schwächere Wochenendabstands-Regel (default: 1 freies Wochenende).
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

    # Schwächerer Wochenendabstand
    required_week_gap = min_free_weekends_between + 1
    for doc in doctors:
        for i in range(n_days):
            if not (days[i].weekday() >= 5 or days[i] in holiday_dates):
                continue
            mon_i = _monday_of_week(days[i])
            for j in range(i + 1, n_days):
                if not (days[j].weekday() >= 5 or days[j] in holiday_dates):
                    continue
                week_gap = (_monday_of_week(days[j]) - mon_i).days // 7
                if week_gap >= required_week_gap:
                    break
                model.add(x[doc.id, i] + x[doc.id, j] <= 1)

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
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    return [
        (doc.id, days[i])
        for doc in doctors
        for i in range(n_days)
        if solver.value(x[doc.id, i])
    ]
