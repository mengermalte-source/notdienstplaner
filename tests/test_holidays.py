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
