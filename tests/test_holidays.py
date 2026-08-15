from datetime import date
from app.services.holidays import get_bavarian_holidays


def test_weihnachten_in_bavarian_holidays():
    holidays = get_bavarian_holidays(2027)
    dates = [h[0] for h in holidays]
    assert date(2027, 12, 25) in dates


def test_dreikoenige_is_bavarian_only():
    holidays = get_bavarian_holidays(2027)
    dates = [h[0] for h in holidays]
    assert date(2027, 1, 6) in dates


def test_neujahr_in_holidays():
    dates = [h[0] for h in get_bavarian_holidays(2027)]
    assert date(2027, 1, 1) in dates


def test_karfreitag_2027():
    """Karfreitag 2027 = 26. März."""
    dates = [h[0] for h in get_bavarian_holidays(2027)]
    assert date(2027, 3, 26) in dates


def test_ostermontag_2027():
    """Ostermontag 2027 = 29. März."""
    dates = [h[0] for h in get_bavarian_holidays(2027)]
    assert date(2027, 3, 29) in dates


def test_tag_der_arbeit():
    dates = [h[0] for h in get_bavarian_holidays(2027)]
    assert date(2027, 5, 1) in dates


def test_christi_himmelfahrt():
    """Christi Himmelfahrt (immer Donnerstag) ist bundesweit Feiertag."""
    # 2027: Ostern 28.3. + 39 Tage = 6. Mai
    dates = [h[0] for h in get_bavarian_holidays(2027)]
    assert date(2027, 5, 6) in dates
