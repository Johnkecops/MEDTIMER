#!/usr/bin/env python3
"""
Module:  MediTimer Core Logic
Purpose: Database layer, pseudocode implementation, and schedule computation
         for the MediTimer medication-reminder system.
Author:  Dr. Arli Aditya Parikesit (arli.parikesit@i3l.ac.id)
         Bioinformatics Department, i3L University, Jakarta
Date:    2022 (original paper); Python port 2026
Version: 1.0.0

Parameters:
    DB_PATH        - Path to the SQLite database file (default: meditimer.db)
    SLOT_TOLERANCE - Minutes of grace period around each scheduled dose (default: 5)

Paper reference:
    Tera, T. C., Triwijaya, R., Natasya, N., Natasya, J., & Parikesit, A. A. (2022).
    MEDITIMER: YOUR PERSONAL MEDICAL TIMER. RINarxiv.
    Corresponding author: arli.parikesit@i3l.ac.id

Notes:
    The original system was designed in Java. This port follows the paper's
    pseudocode (pp. 3-5) exactly, translated to idiomatic Python.
    Indonesian TB regimen (Rifampicin + Isoniazid + Pyrazinamide) is used as the
    demo dataset because tuberculosis is a highly prevalent disease in Indonesia
    where medication adherence failure carries severe clinical consequences.

Limitations:
    - This is a research prototype, not a certified medical device.
    - NFC unlock is simulated; real hardware integration requires additional drivers.
    - The slot-distribution algorithm evenly spaces doses but does not account for
      meal-timing constraints (e.g. Rifampicin must be taken on an empty stomach).
    - No external EHR or pharmacy-system integration is provided.
"""

import hashlib
import logging
import secrets
import sqlite3
import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

DB_PATH        = Path(__file__).parent / "meditimer.db"
SLOT_TOLERANCE = 5  # minutes of grace period around each scheduled time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("meditimer")


# ---------------------------------------------------------------------------
# Input validation  (GIGO principle — data integrity first)
# ---------------------------------------------------------------------------

def validate_prescription_inputs(
    drugs:       list,
    frequencies: list,
    durations:   list,
    doses:       list,
) -> None:
    """
    Raises ValueError with a clear message if any prescription parameter fails
    basic sanity checks before it reaches the slot algorithm.

    Checked invariants
    ------------------
    - All four lists must have the same length.
    - Every drug name must be a non-empty string.
    - Every frequency must be a positive integer (1–24 doses/day).
    - Every duration must be a positive integer (at least 1 day).
    - Every dose must be a positive number.
    """
    if not (len(drugs) == len(frequencies) == len(durations) == len(doses)):
        raise ValueError(
            f"Input lists must have equal length. Got: "
            f"drugs={len(drugs)}, frequencies={len(frequencies)}, "
            f"durations={len(durations)}, doses={len(doses)}."
        )
    for i, (name, freq, dur, dose) in enumerate(
        zip(drugs, frequencies, durations, doses)
    ):
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Drug {i+1}: name must be a non-empty string.")
        if not isinstance(freq, int) or not (1 <= freq <= 24):
            raise ValueError(f"Drug '{name}': frequency must be 1–24 doses/day, got {freq}.")
        if not isinstance(dur, int) or dur < 1:
            raise ValueError(f"Drug '{name}': duration must be ≥ 1 day, got {dur}.")
        if not (isinstance(dose, (int, float)) and dose > 0):
            raise ValueError(f"Drug '{name}': dose must be a positive number, got {dose}.")


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

def init_database(db_path: str = str(DB_PATH)) -> sqlite3.Connection:
    """
    procedure ConnecttoDatabase  (paper p. 3)

    Establishes a connection to the SQLite database and creates the schema
    if the tables do not yet exist. Returns the open connection.

    Parameters
    ----------
    db_path : str
        File system path to the SQLite database. A new file is created if
        it does not exist (SQLite's default behaviour).

    Returns
    -------
    sqlite3.Connection
        Open connection with row_factory set to sqlite3.Row so callers
        can access columns by name.
    """
    log.info("Connecting to database: %s", db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent Streamlit access
    _create_schema(conn)
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    """Creates all tables. Safe to call repeatedly (IF NOT EXISTS)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL DEFAULT 'patient',
            full_name     TEXT,
            nfc_code      TEXT    UNIQUE
        );

        CREATE TABLE IF NOT EXISTS drugs (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT    NOT NULL,
            unit TEXT    NOT NULL DEFAULT 'mg'
        );

        -- One row per drug per prescription.
        -- frequency = doses per day; duration_days = total days.
        CREATE TABLE IF NOT EXISTS prescriptions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id    INTEGER NOT NULL REFERENCES users(id),
            drug_id       INTEGER NOT NULL REFERENCES drugs(id),
            dose          REAL    NOT NULL,
            frequency     INTEGER NOT NULL,
            duration_days INTEGER NOT NULL,
            start_date    TEXT    NOT NULL,
            notes         TEXT
        );

        -- Materialised schedule: one row per dose event.
        CREATE TABLE IF NOT EXISTS medication_slots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id      INTEGER NOT NULL REFERENCES users(id),
            prescription_id INTEGER NOT NULL REFERENCES prescriptions(id),
            slot_index      INTEGER NOT NULL,
            scheduled_time  TEXT    NOT NULL,
            taken           INTEGER NOT NULL DEFAULT 0,
            taken_at        TEXT
        );
    """)
    conn.commit()
    log.debug("Schema verified.")


# ---------------------------------------------------------------------------
# procedure ConnecttoDatabase  (paper p. 3)
# ---------------------------------------------------------------------------

def connect_to_database(conn: sqlite3.Connection, patient_id: int) -> dict:
    """
    Fetches medication schedule variables for one patient from the database.

    Maps directly to the paper's pseudocode variable names:
        variable time      = database.time
        variable drug      = database.drug
        variable frequency = database.frequency
        variable dose      = database.dose
        variable duration  = database.duration

    Parameters
    ----------
    conn       : open SQLite connection
    patient_id : integer primary key of the patient in the users table

    Returns
    -------
    dict with keys: time, drug, frequency, dose, duration, rows
        Each list is ordered by scheduled_time ascending.
        'rows' is a list of dicts containing all columns for use in the UI.
    """
    rows = conn.execute("""
        SELECT
            ms.scheduled_time   AS time,
            d.name              AS drug,
            p.frequency         AS frequency,
            p.dose              AS dose,
            p.duration_days     AS duration,
            p.id                AS prescription_id,
            ms.id               AS slot_id,
            ms.taken            AS taken,
            d.unit              AS unit
        FROM medication_slots ms
        JOIN prescriptions p ON ms.prescription_id = p.id
        JOIN drugs d         ON p.drug_id = d.id
        WHERE ms.patient_id = ?
        ORDER BY ms.scheduled_time
    """, (patient_id,)).fetchall()

    return {
        "time":      [r["time"]      for r in rows],
        "drug":      [r["drug"]      for r in rows],
        "frequency": [r["frequency"] for r in rows],
        "dose":      [r["dose"]      for r in rows],
        "duration":  [r["duration"]  for r in rows],
        "rows":      [dict(r)        for r in rows],
    }


# ---------------------------------------------------------------------------
# Prescription to slot  (paper pp. 3-4)
# ---------------------------------------------------------------------------

def prescription_to_slots(
    drugs:       list[str],
    frequencies: list[int],
    durations:   list[int],
    doses:       list[float],
) -> tuple[list[list[int]], int]:
    """
    Converts prescription parameters into a 2-D binary slot matrix.

    Paper pseudocode (p. 3-4):
    ~~~
    Prescription to slot
    ([drug.1,...,drug.n], [frequency_d1,...,frequency_dn],
     [duration_d1,...,duration_dn], [dose1,...,dosen])
        Find max.frequency * duration
        drug1 = c()
        For sum(drug1) != max.frequency * duration
            Drug1.append(1)
            Break
        Append rest accordingly
        Return array(c(drug1, drug2, drugn),
                     dim=c(max.frequency*duration, drug.n))
    ~~~

    The algorithm distributes each drug's total doses (frequency × duration)
    evenly across the global time window (max frequency × max duration).
    This produces a regular, evenly-spaced dosing schedule for all drugs.

    Parameters
    ----------
    drugs       : list of drug names (for reference only; not used in computation)
    frequencies : doses per day for each drug
    durations   : treatment duration in days for each drug
    doses       : dose amount for each drug (mg or other unit)

    Returns
    -------
    slot_matrix : list[list[int]]
        Outer list: one sublist per drug.
        Each sublist has length max_slots.
        1 = dose scheduled at that slot; 0 = no dose.
    max_slots   : int
        Total number of time slots across all drugs.

    Example (paper p. 4):
        drug1 = [1,1,1,1,1,1,1,1]   (every slot)
        drug2 = [1,0,1,0,1,0,1,0]   (every other slot)
        drugn = [1,0,0,1,0,0,1,0]   (every third slot)
    """
    validate_prescription_inputs(drugs, frequencies, durations, doses)

    total_slots_per_drug = [f * d for f, d in zip(frequencies, durations)]
    max_slots = max(total_slots_per_drug)

    slot_matrix: list[list[int]] = []

    for total_doses in total_slots_per_drug:
        slots = [0] * max_slots
        if total_doses >= max_slots:
            slots = [1] * max_slots
        else:
            # Space doses evenly: place a 1 every (max_slots / total_doses) steps.
            interval = max_slots / total_doses
            for i in range(total_doses):
                idx = round(i * interval)
                if idx < max_slots:
                    slots[idx] = 1
        slot_matrix.append(slots)

    log.debug(
        "Slot matrix built: %d slots, %d drugs. Totals per drug: %s",
        max_slots, len(drugs), total_slots_per_drug,
    )
    return slot_matrix, max_slots


def build_schedule_from_prescription(
    conn:           sqlite3.Connection,
    patient_id:     int,
    drugs:          list[str],
    frequencies:    list[int],
    durations:      list[int],
    doses:          list[float],
    start_datetime: datetime.datetime,
) -> int:
    """
    Materialises the slot matrix into medication_slots rows in the database.

    Parameters
    ----------
    conn           : open SQLite connection
    patient_id     : patient's user id
    drugs          : drug names
    frequencies    : doses per day per drug
    durations      : treatment days per drug
    doses          : dose amounts
    start_datetime : datetime of the very first dose

    Returns
    -------
    int : number of slot rows inserted
    """
    validate_prescription_inputs(drugs, frequencies, durations, doses)

    slot_matrix, max_slots = prescription_to_slots(
        drugs, frequencies, durations, doses
    )

    drug_ids = []
    for name in drugs:
        row = conn.execute("SELECT id FROM drugs WHERE name = ?", (name,)).fetchone()
        if row:
            drug_ids.append(row["id"])
        else:
            cur = conn.execute("INSERT INTO drugs (name) VALUES (?)", (name,))
            drug_ids.append(cur.lastrowid)

    prescription_ids = []
    for drug_id, freq, dur, dose in zip(drug_ids, frequencies, durations, doses):
        cur = conn.execute("""
            INSERT INTO prescriptions
                (patient_id, drug_id, dose, frequency, duration_days, start_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (patient_id, drug_id, dose, freq, dur, start_datetime.date().isoformat()))
        prescription_ids.append(cur.lastrowid)

    max_daily_freq = max(frequencies)
    slot_duration  = datetime.timedelta(hours=24.0 / max_daily_freq)

    rows_inserted = 0
    for slot_idx in range(max_slots):
        slot_time = start_datetime + slot_duration * slot_idx
        for presc_id, drug_slots in zip(prescription_ids, slot_matrix):
            if drug_slots[slot_idx] == 1:
                conn.execute("""
                    INSERT INTO medication_slots
                        (patient_id, prescription_id, slot_index, scheduled_time)
                    VALUES (?, ?, ?, ?)
                """, (
                    patient_id, presc_id, slot_idx,
                    slot_time.isoformat(timespec="minutes"),
                ))
                rows_inserted += 1

    conn.commit()
    log.info(
        "Prescription saved for patient %d: %d dose events across %d drugs.",
        patient_id, rows_inserted, len(drugs),
    )
    return rows_inserted


# ---------------------------------------------------------------------------
# NFC procedure  (paper pp. 4-5)
# ---------------------------------------------------------------------------

def nfc_connection(rfid: str, expected_code: str) -> bool:
    """
    procedure NFCConnection  (paper p. 4)

        If rfid = get.xxxx
            Transfer code
        Else
            sout("Not a match")
        End if
    end procedure

    Simulates the NFC handshake between the patient's phone and the
    physical medication container. On a real device, a successful match
    would trigger a servo or solenoid to open the appropriate slot.

    Parameters
    ----------
    rfid          : code scanned/entered by the patient
    expected_code : code stored in the database for this patient

    Returns
    -------
    bool : True if codes match (slot unlocks), False otherwise
    """
    if rfid == expected_code:
        log.info("NFC match — slot unlocked.")
        return True
    log.warning("NFC mismatch — access denied.")
    print("Not a match")
    return False


def generate_nfc_code() -> str:
    """Returns a random 16-character hex NFC token (cryptographically secure)."""
    return secrets.token_hex(8).upper()


# ---------------------------------------------------------------------------
# Timer procedure  (paper p. 5)
# ---------------------------------------------------------------------------

def timer_check(
    scheduled_time:    str,
    tolerance_minutes: int = SLOT_TOLERANCE,
) -> bool:
    """
    procedure timer(time)  (paper p. 5)

        if time = datetime.now then
            return procedure NFCConnection
        end if
    end procedure

    Returns True when the current wall-clock time falls within
    ±tolerance_minutes of the scheduled dose time.

    Parameters
    ----------
    scheduled_time    : ISO-format datetime string (e.g. "2026-05-01T14:00")
    tolerance_minutes : grace window in minutes (default 5)

    Returns
    -------
    bool
    """
    try:
        sched = datetime.datetime.fromisoformat(scheduled_time)
    except ValueError:
        log.error("timer_check: cannot parse scheduled_time '%s'", scheduled_time)
        return False
    delta = abs((datetime.datetime.now() - sched).total_seconds())
    return delta <= tolerance_minutes * 60


# ---------------------------------------------------------------------------
# Schedule query helpers
# ---------------------------------------------------------------------------

def get_next_alarm(
    conn:       sqlite3.Connection,
    patient_id: int,
) -> Optional[dict]:
    """
    Returns the next untaken medication slot for the given patient,
    or None if no future doses are scheduled.
    """
    now = datetime.datetime.now().isoformat(timespec="minutes")
    row = conn.execute("""
        SELECT
            ms.id              AS slot_id,
            ms.scheduled_time  AS scheduled_time,
            ms.slot_index      AS slot_index,
            d.name             AS drug,
            p.dose             AS dose,
            d.unit             AS unit,
            p.id               AS prescription_id
        FROM medication_slots ms
        JOIN prescriptions p ON ms.prescription_id = p.id
        JOIN drugs d         ON p.drug_id = d.id
        WHERE ms.patient_id = ?
          AND ms.taken = 0
          AND ms.scheduled_time >= ?
        ORDER BY ms.scheduled_time
        LIMIT 1
    """, (patient_id, now)).fetchone()
    return dict(row) if row else None


def mark_slot_taken(conn: sqlite3.Connection, slot_id: int) -> None:
    """Records that a patient took a specific dose at the current time."""
    conn.execute("""
        UPDATE medication_slots
        SET taken = 1, taken_at = ?
        WHERE id = ?
    """, (datetime.datetime.now().isoformat(timespec="seconds"), slot_id))
    conn.commit()
    log.info("Slot %d marked as taken.", slot_id)


def get_adherence_report(
    conn:       sqlite3.Connection,
    patient_id: int,
) -> dict:
    """
    Computes basic adherence statistics for a patient.

    Returns
    -------
    dict with keys:
        total    - all scheduled slots to date
        taken    - slots marked as taken
        missed   - past due slots not taken
        upcoming - future slots not yet due
        rate_pct - taken / (taken + missed) × 100, or None if no past slots
    """
    now = datetime.datetime.now().isoformat(timespec="minutes")

    total = conn.execute(
        "SELECT COUNT(*) FROM medication_slots WHERE patient_id=?",
        (patient_id,)
    ).fetchone()[0]

    taken = conn.execute(
        "SELECT COUNT(*) FROM medication_slots WHERE patient_id=? AND taken=1",
        (patient_id,)
    ).fetchone()[0]

    missed = conn.execute(
        "SELECT COUNT(*) FROM medication_slots "
        "WHERE patient_id=? AND taken=0 AND scheduled_time < ?",
        (patient_id, now)
    ).fetchone()[0]

    upcoming = total - taken - missed
    denominator = taken + missed
    rate = round(100 * taken / denominator, 1) if denominator else None

    return {
        "total":    total,
        "taken":    taken,
        "missed":   missed,
        "upcoming": upcoming,
        "rate_pct": rate,
    }


# ---------------------------------------------------------------------------
# User authentication
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(
    conn:      sqlite3.Connection,
    username:  str,
    password:  str,
    role:      str = "patient",
    full_name: str = "",
) -> int:
    """Creates a new user and returns the new user's primary key id."""
    nfc = generate_nfc_code() if role == "patient" else None
    cur = conn.execute("""
        INSERT INTO users (username, password_hash, role, full_name, nfc_code)
        VALUES (?, ?, ?, ?, ?)
    """, (username, hash_password(password), role, full_name, nfc))
    conn.commit()
    log.info("User created: username='%s', role='%s'", username, role)
    return cur.lastrowid


def authenticate_user(
    conn:     sqlite3.Connection,
    username: str,
    password: str,
) -> Optional[dict]:
    """
    Returns the user dict if credentials are valid, otherwise None.
    Passwords are stored as SHA-256 hashes; plain-text is never persisted.
    """
    row = conn.execute("""
        SELECT id, username, role, full_name, nfc_code
        FROM users
        WHERE username = ? AND password_hash = ?
    """, (username, hash_password(password))).fetchone()
    if row:
        log.info("Login successful: '%s'", username)
    else:
        log.warning("Failed login attempt for username: '%s'", username)
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Demo seed data  (Indonesian TB regimen — matches paper's use-case)
# ---------------------------------------------------------------------------

def seed_demo_data(conn: sqlite3.Connection) -> None:
    """
    Inserts one demo pharmacist and one demo patient with a standard
    first-line Indonesian TB regimen (Rifampicin, Isoniazid, Pyrazinamide).

    TB is used because the paper explicitly cites it as the primary
    motivating disease: long-course, complex dosing, severe consequences
    for missed doses, and high prevalence in Indonesia.

    Skip if users already exist to prevent re-seeding on restart.
    """
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return

    log.info("Seeding demo data (Indonesian TB regimen).")
    create_user(conn, "pharmacist", "pharma123", "pharmacist", "Demo Pharmacist")
    pid = create_user(conn, "patient1", "patient123", "patient", "Ahmad Rizki")

    start = datetime.datetime.now().replace(second=0, microsecond=0)
    start = (start + datetime.timedelta(hours=1)).replace(minute=0)

    # WHO first-line TB regimen: intensive phase (2 months) weight-based dosing
    # Simplified to fixed doses here for demo purposes.
    build_schedule_from_prescription(
        conn=conn,
        patient_id=pid,
        drugs=["Rifampicin", "Isoniazid", "Pyrazinamide"],
        frequencies=[2, 2, 1],     # doses per day
        durations=[4, 4, 4],       # days (shortened for demo)
        doses=[600.0, 300.0, 1500.0],
        start_datetime=start,
    )
