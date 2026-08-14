from datetime import date
import holidays


def get_bavarian_holidays(year: int) -> list[tuple[date, str]]:
    by_holidays = holidays.Germany(subdiv="BY", years=year)
    return sorted([(d, name) for d, name in by_holidays.items()])


def get_school_vacation_windows(year: int) -> list[tuple[date, date, str]]:
    """
    Bayerische Schulferienzeiten — hartcodiert für 2027/2028.
    Für Produktion: jährliches Update nötig.
    """
    return [
        (date(year, 1, 1), date(year, 1, 7), "Weihnachtsferien"),
        (date(year, 2, 28), date(year, 3, 6), "Faschingsferien"),
        (date(year, 4, 9), date(year, 4, 23), "Osterferien"),
        (date(year, 6, 3), date(year, 6, 3), "Pfingstferien Brücke"),
        (date(year, 7, 31), date(year, 9, 10), "Sommerferien"),
        (date(year, 10, 30), date(year, 11, 7), "Herbstferien"),
        (date(year, 12, 24), date(year + 1, 1, 5), "Weihnachtsferien"),
    ]
