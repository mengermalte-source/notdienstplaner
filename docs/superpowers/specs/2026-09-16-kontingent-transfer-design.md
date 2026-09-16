# Kontingent-Transfer zwischen Ärzten

**Datum:** 2026-09-16

## Ziel

Ärzte können einen Teil ihres Dienstkontingents dauerhaft an einen Kollegen abtreten. Arzt A sendet einen Antrag mit X Prozentpunkten an Arzt B. Nimmt B an, wird A's `credit_factor` um `X/100` reduziert und B's entsprechend erhöht. Der Transfer wirkt dauerhaft und wird beim nächsten Algorithmus-Lauf berücksichtigt. Er kann von beiden Seiten einseitig widerrufen werden.

---

## Datenmodell

Neue Datei `app/models/contingent_transfer.py`:

```python
class TransferStatus(str, Enum):
    pending  = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revoked  = "revoked"

class ContingentTransfer(SQLModel, table=True):
    id:             Optional[int]      # PK
    sender_id:      int                # FK user.id
    receiver_id:    int                # FK user.id
    percent:        float              # Prozentpunkte, z.B. 20.0 → 0.20 credit_factor
    status:         TransferStatus     # default: pending
    message:        str = ""           # optionale Begründung
    created_at:     datetime
    resolved_at:    Optional[datetime] # Zeitpunkt accept/reject
    revoked_at:     Optional[datetime]
    revoked_by_id:  Optional[int]      # FK user.id
```

Alembic-Migration erforderlich.

---

## Effekt auf credit_factor

**Bei Annahme:**
```
sender.credit_factor   -= percent / 100
receiver.credit_factor += percent / 100
```

**Bei Widerruf:**
```
sender.credit_factor   += percent / 100
receiver.credit_factor -= percent / 100
```

Der `credit_factor` wird in `DoctorProfile` direkt geändert. Da der Algorithmus immer den aktuellen `credit_factor` liest, wirkt jede Änderung beim nächsten Planungs-Lauf.

---

## Validierung

- `sender != receiver`
- `percent` liegt in `(0.0, 50.0]` (max. 50 Prozentpunkte pro Transfer)
- Nach Transfer: `sender.credit_factor - percent/100 >= 0.05` (Sender behält Mindestanteil)
- Nach Transfer: `receiver.credit_factor + percent/100 <= 2.0` (Empfänger kann Mehrarbeit annehmen)
- Sender darf nur senden, wenn kein anderer offener (`pending`) Transfer an dieselbe Person existiert

---

## Routes (`app/routers/contingent_transfer.py`)

| Method | Path | Beschreibung |
|--------|------|--------------|
| `GET`  | `/me/transfers` | Eigene Transfers (gesendet + empfangen) |
| `POST` | `/me/transfers` | Neuen Antrag senden |
| `POST` | `/me/transfers/{id}/accept` | Annehmen (nur Empfänger, nur pending) |
| `POST` | `/me/transfers/{id}/reject` | Ablehnen (nur Empfänger, nur pending) |
| `POST` | `/me/transfers/{id}/revoke` | Widerrufen (Sender oder Empfänger, nur accepted) |

---

## UI (`app/templates/doctor/transfers.html`)

Eigene Seite `/me/transfers` mit drei Bereichen:

1. **Eingehende offene Anträge** — Accept / Reject-Buttons
2. **Aktive Transfers** — Tabelle mit Sender/Empfänger/Prozent/Datum, Revoke-Button
3. **Antrag senden** — Formular: Empfänger (Dropdown aller Ärzte außer sich selbst), Prozent (1–50), optionale Nachricht

Badge-Counter im Navigationsmenü für offene eingehende Anträge (analog zu Tauschbörse).

---

## Dateien

| Datei | Aktion |
|-------|--------|
| `app/models/contingent_transfer.py` | Neu |
| `app/models/__init__.py` | Import ergänzen |
| `app/routers/contingent_transfer.py` | Neu |
| `app/main.py` | Router registrieren |
| `app/templates/doctor/transfers.html` | Neu |
| `app/templates/base.html` | Navigationseintrag + Badge |
| `alembic/versions/xxxx_add_contingent_transfer.py` | Neu |

---

## Nicht im Scope

- Admin-Ansicht für Transfers (admin kann credit_factor direkt bearbeiten)
- Zeitlich befristete Transfers
- Teilwiderruf (immer vollständiger Widerruf des Transfers)
