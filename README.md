# MediTimer — Your Personal Medical Timer

> Python/Streamlit implementation of the MediTimer software described in:
>
> Tera, T. C., Triwijaya, R., Natasya, N., Natasya, J., & Parikesit, A. A. (2022).
> **MEDITIMER: YOUR PERSONAL MEDICAL TIMER.** *RINarxiv.*

---

## Background

Medication errors — wrong time, wrong dose, or outright omission — are a major global health concern documented by the WHO. MediTimer addresses this by pairing a physical drug-container (whose compartments unlock on a schedule) with a mobile application that reminds the patient at the exact moment each dose is due.

The original paper proposed a Java-based implementation. This repository provides a faithful Python port of the four pseudocode procedures described in the paper, plus a browser-based interface built with Streamlit.

---

## System Architecture

```
HOSPITAL SIDE                        PATIENT SIDE
─────────────────────────            ─────────────────────────
Doctor writes prescription  ──►  Pharmacist enters into DB
Pharmacist loads device slots        Patient receives device + credentials
Alarm + NFC code set in DB  ──►  Patient logs into app
                                     Timer fires → alarm + device unlock
```

This mirrors Figure 1 (Flowchart of the Software Design) from the paper.

---

## Pseudocode → Python mapping

| Paper procedure | Python function (`meditimer_core.py`) |
|---|---|
| `ConnecttoDatabase` | `connect_to_database(conn, patient_id)` |
| `Prescription to slot` | `prescription_to_slots(drugs, frequencies, durations, doses)` |
| `NFCConnection` | `nfc_connection(rfid, expected_code)` |
| `timer(time)` | `timer_check(scheduled_time, tolerance_minutes)` |

### Slot matrix algorithm

The paper's pseudocode builds a 2-D binary array where:

- **rows** = total time slots (`max(frequency × duration)` across all drugs)
- **columns** = drugs
- **cell = 1** → drug is scheduled for that slot

```python
# Paper (p. 3-4), R-like notation:
# drug1 = c(1,1,1,1,1,1,1,1)   # every slot
# drug2 = c(1,0,1,0,1,0,1,0)   # every other slot
# drugn = c(1,0,0,1,0,0,1,0)   # every third slot

from meditimer_core import prescription_to_slots

matrix, total_slots = prescription_to_slots(
    drugs       = ["Rifampicin", "Isoniazid", "Pyrazinamide"],
    frequencies = [2, 2, 1],        # doses per day
    durations   = [4, 4, 4],        # days
    doses       = [600.0, 300.0, 1500.0],
)
```

---

## Project structure

```
MEDTIMER/
├── meditimer_core.py   # Core logic (DB, pseudocode implementation)
├── app.py              # Streamlit web application
├── README.md
├── LICENSE.txt
└── meditimer.db        # SQLite database (auto-created on first run)
```

---

## Quick start

### 1. Install dependencies

```bash
pip install streamlit pandas
```

### 2. Run the app

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

### 3. Demo accounts

| Role | Username | Password |
|---|---|---|
| Pharmacist | `pharmacist` | `pharma123` |
| Patient | `patient1` | `patient123` |

The demo patient has a pre-loaded TB regimen (Rifampicin + Isoniazid + Pyrazinamide) as a realistic example matching the paper's TB use-case.

---

## Features

### Patient view
- **Next Alarm** — countdown to next scheduled dose; auto-marks due doses
- **Schedule** — full list of upcoming and taken slots
- **NFC Unlock simulation** — enter/scan your NFC code to unlock the virtual device slot

### Pharmacist view
- **Add Prescription** — select patient, enter drugs (name, dose, frequency, duration), set start date
- **Patients** — adherence overview per patient (doses taken / total)
- **All Schedules** — filterable table of every upcoming slot across all patients
- **Slot Matrix Visualiser** — interactive reproduction of the paper's pseudocode array

---

## Database schema

```
users            — id, username, password_hash, role, full_name, nfc_code
drugs            — id, name, unit
prescriptions    — id, patient_id, drug_id, dose, frequency, duration_days, start_date
medication_slots — id, patient_id, prescription_id, slot_index, scheduled_time, taken, taken_at
```

---

## Extending the implementation

The paper mentions several future directions that can be layered on top of this codebase:

| Enhancement | Implementation note |
|---|---|
| IoT integration | Replace `nfc_connection()` with an MQTT/HTTP call to a real device |
| GPS pharmacy finder | Add a Folium/Leaflet map tab using the patient's coordinates |
| Healthcare professional remote access | Add a `doctor` role with read-only prescription monitoring |
| Push notifications | Use `st.experimental_rerun` or a background thread with `plyer`/`ntfy` |

---

## Citation

```bibtex
@article{tera2022meditimer,
  title   = {MEDITIMER: YOUR PERSONAL MEDICAL TIMER},
  author  = {Tera, Tyniana C. and Triwijaya, Renata and Natasya, Nadya
             and Natasya, Jacqulin and Parikesit, Arli Aditya},
  journal = {RINarxiv},
  year    = {2022}
}
```

---

## License

MIT — see [LICENSE.txt](LICENSE.txt)
