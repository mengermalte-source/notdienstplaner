import pytest
from datetime import date
from app.models.vacation import VacationPeriod


def test_vacation_period_model():
    vp = VacationPeriod(user_id=1, start_date=date(2027, 7, 1), end_date=date(2027, 7, 14))
    assert vp.start_date < vp.end_date
    assert vp.reason == ""
