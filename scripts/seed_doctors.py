import asyncio
from app.database import init_db, AsyncSessionLocal
from app.models.user import User, UserRole, DoctorProfile
from app.services.auth import hash_password

# (Name, E-Mail, credit_factor)
DOCTORS = [
    ("Anna Bauer",          "anna.bauer@praxis.de",          1.0),
    ("Thomas Müller",       "thomas.mueller@praxis.de",      1.0),
    ("Maria Schmidt",       "maria.schmidt@praxis.de",       0.75),
    ("Klaus Weber",         "klaus.weber@praxis.de",         1.0),
    ("Sabine Hoffmann",     "sabine.hoffmann@praxis.de",     0.5),
    ("Michael Fischer",     "michael.fischer@praxis.de",     1.0),
    ("Laura Wagner",        "laura.wagner@praxis.de",        1.0),
    ("Stefan Becker",       "stefan.becker@praxis.de",       0.75),
    ("Julia Schulz",        "julia.schulz@praxis.de",        1.0),
    ("Andreas Koch",        "andreas.koch@praxis.de",        1.0),
    ("Sandra Richter",      "sandra.richter@praxis.de",      0.5),
    ("Markus Klein",        "markus.klein@praxis.de",        1.0),
    ("Christine Wolf",      "christine.wolf@praxis.de",      0.75),
    ("Daniel Schröder",     "daniel.schroeder@praxis.de",    1.0),
    ("Petra Neumann",       "petra.neumann@praxis.de",       1.0),
    ("Jörg Braun",          "joerg.braun@praxis.de",         1.0),
    ("Monika Schwarz",      "monika.schwarz@praxis.de",      0.75),
    ("Frank Zimmermann",    "frank.zimmermann@praxis.de",    1.0),
    ("Ursula Krause",       "ursula.krause@praxis.de",       0.5),
    ("Tobias Lange",        "tobias.lange@praxis.de",        1.0),
    ("Katharina Vogt",      "katharina.vogt@praxis.de",      1.0),
    ("Rainer Hartmann",     "rainer.hartmann@praxis.de",     1.0),
    ("Ines Lorenz",         "ines.lorenz@praxis.de",         0.75),
    ("Carsten Krüger",      "carsten.krueger@praxis.de",     1.0),
    ("Brigitte Maier",      "brigitte.maier@praxis.de",      0.5),
    ("Oliver Berger",       "oliver.berger@praxis.de",       1.0),
    ("Susanne Frank",       "susanne.frank@praxis.de",       1.0),
    ("Holger Lehmann",      "holger.lehmann@praxis.de",      1.0),
    ("Renate Kaufmann",     "renate.kaufmann@praxis.de",     0.75),
    ("Uwe Pohl",            "uwe.pohl@praxis.de",            1.0),
    ("Annette Jung",        "annette.jung@praxis.de",        1.0),
    ("Bernd König",         "bernd.koenig@praxis.de",        0.5),
    ("Claudia Roth",        "claudia.roth@praxis.de",        1.0),
    ("Eberhard Fuchs",      "eberhard.fuchs@praxis.de",      1.0),
    ("Gabi Herrmann",       "gabi.herrmann@praxis.de",       0.75),
    ("Helmut Albrecht",     "helmut.albrecht@praxis.de",     1.0),
    ("Ingrid Schreiber",    "ingrid.schreiber@praxis.de",    0.5),
    ("Jürgen Dietrich",     "juergen.dietrich@praxis.de",    1.0),
    ("Kerstin Haas",        "kerstin.haas@praxis.de",        1.0),
    ("Ludwig Sauer",        "ludwig.sauer@praxis.de",        1.0),
]


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        for full_name, email, factor in DOCTORS:
            user = User(
                email=email,
                hashed_password=hash_password("arzt123"),
                full_name=full_name,
                role=UserRole.doctor,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            session.add(DoctorProfile(user_id=user.id, credit_factor=factor))
            await session.commit()
            print(f"  {full_name} ({int(factor*100)} %)")

    print(f"\n{len(DOCTORS)} Arztaccounts angelegt. Passwort: arzt123")


asyncio.run(main())
