from icalendar import Calendar, Event
from app.models.user import User


def build_ical(user: User, assignments: list) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Notdienstplaner//DE")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", f"Notdienste {user.full_name}")

    for a in assignments:
        event = Event()
        event.add("summary", "Notdienst")
        event.add("dtstart", a.date)
        event.add("dtend", a.date)
        event.add("uid", f"notdienst-{a.id}@notdienstplaner")
        cal.add_component(event)

    return cal.to_ical()
