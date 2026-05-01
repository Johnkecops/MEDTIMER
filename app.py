#!/usr/bin/env python3
"""
Module:  MediTimer Streamlit Application
Purpose: Browser-based interface for patients and pharmacists to manage
         medication schedules, monitor adherence, and simulate NFC unlock.
Author:  Dr. Arli Aditya Parikesit (arli.parikesit@i3l.ac.id)
         Bioinformatics Department, i3L University, Jakarta
Date:    2022 (original design); Python/Streamlit port 2026
Version: 1.0.0

Parameters:
    DB_PATH (meditimer_core) - SQLite database location
    SLOT_TOLERANCE           - grace window in minutes for timer_check()

Paper reference:
    Tera, T. C., Triwijaya, R., Natasya, N., Natasya, J., & Parikesit, A. A. (2022).
    MEDITIMER: YOUR PERSONAL MEDICAL TIMER. RINarxiv.

Run:
    streamlit run app.py

Roles:
    pharmacist  - register patients, enter prescriptions, view all schedules
    patient     - view own schedule, next dose, mark taken, simulate NFC unlock

Limitations:
    This is a research prototype based on the paper's pseudocode. It has not
    been validated as a clinical device and should not replace professional
    medical oversight in real treatment settings.
"""

import datetime

import streamlit as st
import pandas as pd

from meditimer_core import (
    init_database,
    seed_demo_data,
    authenticate_user,
    create_user,
    connect_to_database,
    build_schedule_from_prescription,
    get_next_alarm,
    get_adherence_report,
    mark_slot_taken,
    nfc_connection,
    timer_check,
    prescription_to_slots,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MediTimer",
    page_icon="💊",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Database — one connection shared across the Streamlit session
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db():
    conn = init_database()
    seed_demo_data(conn)
    return conn


conn = get_db()

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

if "user" not in st.session_state:
    st.session_state.user = None


# ---------------------------------------------------------------------------
# Sidebar: About + Citation
# ---------------------------------------------------------------------------

def render_sidebar():
    with st.sidebar:
        st.markdown("## About MediTimer")
        st.markdown(
            "MediTimer helps patients take the right drug at the right time. "
            "It was built at i3L University in Jakarta to cut down on "
            "medication errors — particularly in long-course treatments "
            "like tuberculosis therapy, where missing doses can have "
            "serious consequences."
        )
        st.divider()
        st.markdown("**Paper**")
        st.caption(
            "Tera et al. (2022). *MEDITIMER: YOUR PERSONAL MEDICAL TIMER.* "
            "RINarxiv. Bioinformatics Dept., i3L University, Jakarta."
        )
        st.divider()
        st.warning(
            "**Research prototype only.** MediTimer has not been validated as "
            "a certified medical device. Do not use it as your sole means of "
            "medication management without professional oversight."
        )
        if st.session_state.user:
            st.divider()
            role_badge = (
                "Pharmacist" if st.session_state.user["role"] == "pharmacist"
                else "Patient"
            )
            st.markdown(
                f"Signed in as **{st.session_state.user['full_name'] or st.session_state.user['username']}** "
                f"({role_badge})"
            )
            if st.button("Sign out", use_container_width=True):
                st.session_state.user = None
                st.rerun()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def countdown_str(scheduled_iso: str) -> str:
    """Returns a human-readable HH:MM:SS countdown string."""
    try:
        sched = datetime.datetime.fromisoformat(scheduled_iso)
        delta = sched - datetime.datetime.now()
        if delta.total_seconds() < 0:
            return "Due now"
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}h {m:02d}m {s:02d}s"
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

def page_login():
    st.title("💊 MediTimer")
    st.markdown("*Take the right medicine at the right time, every time.*")
    st.divider()

    tab_login, tab_register = st.tabs(["Sign in", "Create account"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True)

        if submitted:
            if not username or not password:
                st.error("Please fill in both username and password.")
            else:
                user = authenticate_user(conn, username, password)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error("Those credentials don't match anything on file. Try again.")

        st.info(
            "**Try the demo:**  \n"
            "Pharmacist → `pharmacist` / `pharma123`  \n"
            "Patient → `patient1` / `patient123`"
        )

    with tab_register:
        st.markdown("Patients can create their own account here. "
                    "A pharmacist will add your prescription after you register.")
        with st.form("register_form"):
            new_name     = st.text_input("Full name")
            new_username = st.text_input("Choose a username")
            new_password = st.text_input("Choose a password", type="password")
            reg_btn      = st.form_submit_button("Create my account", use_container_width=True)

        if reg_btn:
            if not all([new_name, new_username, new_password]):
                st.error("Please fill in all three fields.")
            elif len(new_password) < 6:
                st.error("Password should be at least 6 characters.")
            else:
                try:
                    create_user(conn, new_username, new_password, "patient", new_name)
                    st.success(
                        f"Account created for {new_name}. "
                        "Sign in above, then ask your pharmacist to add your prescription."
                    )
                except Exception:
                    st.error("That username is taken — try a different one.")


# ---------------------------------------------------------------------------
# Patient dashboard
# ---------------------------------------------------------------------------

def page_patient():
    user = st.session_state.user
    st.title("💊 MediTimer")
    st.markdown(f"Hi, **{user['full_name'] or user['username']}**.")
    st.divider()

    tab_alarm, tab_schedule, tab_adherence, tab_nfc = st.tabs(
        ["Next dose", "My schedule", "Adherence", "NFC unlock"]
    )

    # ---- Next dose ---------------------------------------------------------
    with tab_alarm:
        next_slot = get_next_alarm(conn, user["id"])

        if next_slot is None:
            st.success("You're all caught up — no doses scheduled right now.")
        else:
            sched_dt = datetime.datetime.fromisoformat(next_slot["scheduled_time"])
            is_due   = timer_check(next_slot["scheduled_time"])

            col1, col2 = st.columns(2)
            with col1:
                st.metric("Next dose", sched_dt.strftime("%H:%M"))
                st.caption(sched_dt.strftime("%A, %d %b %Y"))
            with col2:
                if is_due:
                    st.metric("Status", "⚠️ Due now")
                else:
                    st.metric("Time left", countdown_str(next_slot["scheduled_time"]))

            st.markdown(
                f"**What to take:** {next_slot['drug']}  \n"
                f"**Dose:** {next_slot['dose']} {next_slot['unit']}"
            )

            if is_due:
                if st.button("I took this dose", use_container_width=True, type="primary"):
                    mark_slot_taken(conn, next_slot["slot_id"])
                    st.success("Done. Dose logged.")
                    st.rerun()
            else:
                st.button(
                    "I took this dose",
                    use_container_width=True,
                    disabled=True,
                    help="This button activates when your dose window opens.",
                )

        if st.button("Refresh", use_container_width=True):
            st.rerun()

    # ---- My schedule -------------------------------------------------------
    with tab_schedule:
        data = connect_to_database(conn, user["id"])
        rows = data["rows"]

        if not rows:
            st.info(
                "No medication scheduled yet. "
                "Your pharmacist will add your prescription once they see your account."
            )
        else:
            seen = set()
            for row in rows:
                key = (row["time"], row["drug"])
                if key in seen:
                    continue
                seen.add(key)

                try:
                    dt    = datetime.datetime.fromisoformat(row["time"])
                    label = dt.strftime("%-I:%M %p, %d %b")
                except Exception:
                    label = row["time"]

                icon = "✅" if row["taken"] else "⏳"
                st.markdown(
                    f"{icon} **{label}** — "
                    f"{row['dose']} {row['unit']} {row['drug']}"
                )

    # ---- Adherence ---------------------------------------------------------
    with tab_adherence:
        report = get_adherence_report(conn, user["id"])

        if report["total"] == 0:
            st.info("No schedule data yet.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Taken", report["taken"])
            c2.metric("Missed", report["missed"])
            c3.metric(
                "Adherence rate",
                f"{report['rate_pct']}%" if report["rate_pct"] is not None else "—"
            )

            if report["rate_pct"] is not None:
                if report["rate_pct"] >= 90:
                    st.success("Great adherence — keep it up.")
                elif report["rate_pct"] >= 70:
                    st.warning("Fairly consistent, but a few doses were missed.")
                else:
                    st.error(
                        "Adherence is low. Missed doses can let the disease progress "
                        "and, for antibiotics, may contribute to resistance."
                    )

    # ---- NFC unlock --------------------------------------------------------
    with tab_nfc:
        st.markdown(
            "Tap your NFC-enabled phone against the medication container, "
            "or type your NFC code below to simulate unlocking your slot."
        )
        nfc_input = st.text_input("NFC code", max_chars=16, placeholder="e.g. A1B2C3D4E5F6...")
        if st.button("Unlock my slot", use_container_width=True, type="primary"):
            expected = user.get("nfc_code", "")
            if not nfc_input.strip():
                st.error("Please enter your NFC code first.")
            elif nfc_connection(nfc_input.strip().upper(), expected):
                st.success("Slot open. Go ahead and take your medication.")
            else:
                st.error("That code didn't match. Check it and try again.")
        st.caption(f"Your NFC code (for testing): `{user.get('nfc_code', 'N/A')}`")


# ---------------------------------------------------------------------------
# Pharmacist dashboard
# ---------------------------------------------------------------------------

def page_pharmacist():
    user = st.session_state.user
    st.title("💊 MediTimer — Pharmacist panel")
    st.markdown(f"Welcome, **{user['full_name'] or user['username']}**.")
    st.divider()

    tab_add, tab_patients, tab_all, tab_matrix = st.tabs(
        ["Add prescription", "Patients", "Upcoming doses", "Slot matrix"]
    )

    # ---- Add prescription --------------------------------------------------
    with tab_add:
        st.subheader("Write a new prescription")

        patients = conn.execute(
            "SELECT id, username, full_name FROM users WHERE role='patient'"
        ).fetchall()

        if not patients:
            st.warning("No patients are registered yet — they need to create accounts first.")
        else:
            patient_opts = {
                f"{p['full_name'] or p['username']} (@{p['username']})": p["id"]
                for p in patients
            }
            selected_label = st.selectbox("Patient", list(patient_opts))
            patient_id     = patient_opts[selected_label]

            st.markdown("Add one row per drug in this prescription.")
            num_drugs = st.number_input(
                "How many drugs?", min_value=1, max_value=10, value=1
            )

            drug_names, frequencies, durations, doses = [], [], [], []

            for i in range(int(num_drugs)):
                with st.container(border=True):
                    st.markdown(f"**Drug {i+1}**")
                    c1, c2, c3, c4 = st.columns(4)
                    drug_names.append(
                        c1.text_input("Name", key=f"dname_{i}", placeholder="Rifampicin")
                    )
                    doses.append(
                        c2.number_input("Dose (mg)", min_value=0.1, value=500.0,
                                        step=50.0, key=f"dose_{i}")
                    )
                    frequencies.append(int(
                        c3.number_input("Doses/day", min_value=1, max_value=24,
                                        value=2, key=f"freq_{i}")
                    ))
                    durations.append(int(
                        c4.number_input("Days", min_value=1, value=7, key=f"dur_{i}")
                    ))

            start_date = st.date_input("Start date", value=datetime.date.today())
            start_hour = st.slider("First dose at (hour)", 0, 23, 8,
                                   help="Subsequent doses are spaced evenly through the day.")

            if st.button("Save this prescription", use_container_width=True, type="primary"):
                missing = [i+1 for i, n in enumerate(drug_names) if not n.strip()]
                if missing:
                    st.error(f"Drug name missing for row(s): {missing}.")
                else:
                    start_dt = datetime.datetime(
                        start_date.year, start_date.month, start_date.day, start_hour
                    )
                    try:
                        n_slots = build_schedule_from_prescription(
                            conn=conn,
                            patient_id=patient_id,
                            drugs=drug_names,
                            frequencies=frequencies,
                            durations=durations,
                            doses=doses,
                            start_datetime=start_dt,
                        )
                        st.success(
                            f"Prescription saved. "
                            f"{n_slots} dose events have been added to "
                            f"{selected_label.split('(')[0].strip()}'s schedule."
                        )
                    except ValueError as e:
                        st.error(f"Input error: {e}")
                    except Exception as e:
                        st.error(f"Something went wrong: {e}")

    # ---- Patients ----------------------------------------------------------
    with tab_patients:
        st.subheader("Your patients")
        patients = conn.execute(
            "SELECT id, username, full_name, nfc_code FROM users WHERE role='patient'"
        ).fetchall()

        if not patients:
            st.info("No patients registered yet.")
        else:
            for p in patients:
                report = get_adherence_report(conn, p["id"])
                rate   = (
                    f"{report['rate_pct']}%"
                    if report["rate_pct"] is not None else "no data yet"
                )
                with st.expander(
                    f"**{p['full_name'] or p['username']}** (@{p['username']}) — "
                    f"adherence: {rate}"
                ):
                    st.markdown(f"NFC code: `{p['nfc_code']}`")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total slots", report["total"])
                    c2.metric("Taken", report["taken"])
                    c3.metric("Missed", report["missed"])

                    # Adherence bar
                    if report["total"] > 0:
                        bar_data = pd.DataFrame({
                            "Status": ["Taken", "Missed", "Upcoming"],
                            "Count":  [report["taken"], report["missed"], report["upcoming"]],
                        }).set_index("Status")
                        st.bar_chart(bar_data)

    # ---- All upcoming doses ------------------------------------------------
    with tab_all:
        st.subheader("All upcoming dose events")
        now_iso = datetime.datetime.now().isoformat(timespec="minutes")
        rows = conn.execute("""
            SELECT
                COALESCE(u.full_name, u.username) AS patient,
                d.name              AS drug,
                p.dose              AS dose,
                d.unit              AS unit,
                ms.scheduled_time   AS scheduled_time,
                ms.taken            AS taken
            FROM medication_slots ms
            JOIN users u         ON ms.patient_id = u.id
            JOIN prescriptions p ON ms.prescription_id = p.id
            JOIN drugs d         ON p.drug_id = d.id
            WHERE ms.scheduled_time >= ?
            ORDER BY ms.scheduled_time
            LIMIT 300
        """, (now_iso,)).fetchall()

        if not rows:
            st.info("Nothing scheduled from this point forward.")
        else:
            df = pd.DataFrame([dict(r) for r in rows])
            df["taken"] = df["taken"].map({0: "pending", 1: "taken"})
            df.columns = [c.replace("_", " ").title() for c in df.columns]
            st.dataframe(df, use_container_width=True, hide_index=True)

    # ---- Slot matrix visualiser --------------------------------------------
    with tab_matrix:
        st.subheader("Slot matrix explorer")
        st.markdown(
            "This is the 2-D binary array from the paper's pseudocode (p. 3-4). "
            "Each row is a time slot; each column is a drug. "
            "A 1 means the drug is due at that slot; 0 means it isn't. "
            "Adjust the inputs to see how the distribution changes."
        )

        n = st.number_input("Number of drugs", 1, 5, 3, key="vis_n")
        v_drugs, v_freq, v_dur, v_dose = [], [], [], []
        defaults = [(2, 4), (2, 4), (1, 4)]

        for i in range(int(n)):
            with st.container(border=True):
                st.markdown(f"**Drug {i+1}**")
                c1, c2, c3, c4 = st.columns(4)
                v_drugs.append(c1.text_input("Name", value=f"Drug{i+1}", key=f"v_d_{i}"))
                def_freq, def_dur = defaults[i] if i < 3 else (1, 4)
                v_freq.append(int(c2.number_input("Freq/day", 1, 24, def_freq, key=f"v_f_{i}")))
                v_dur.append(int(c3.number_input("Days", 1, 30, def_dur, key=f"v_dur_{i}")))
                v_dose.append(c4.number_input("Dose (mg)", 0.1, 5000.0, 500.0, key=f"v_dose_{i}"))

        if st.button("Generate matrix", use_container_width=True):
            try:
                matrix, max_s = prescription_to_slots(v_drugs, v_freq, v_dur, v_dose)
                df = pd.DataFrame(
                    list(zip(*matrix)),
                    columns=v_drugs,
                    index=[f"Slot {i+1}" for i in range(max_s)],
                )
                st.dataframe(df, use_container_width=True)
                st.caption(
                    f"Matrix shape: {max_s} rows × {len(v_drugs)} columns. "
                    f"Drug totals: "
                    + ", ".join(
                        f"{d}={sum(col)}" for d, col in zip(v_drugs, matrix)
                    )
                )
            except ValueError as e:
                st.error(f"Input error: {e}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def main():
    render_sidebar()
    if st.session_state.user is None:
        page_login()
    elif st.session_state.user["role"] == "pharmacist":
        page_pharmacist()
    else:
        page_patient()


if __name__ == "__main__":
    main()
