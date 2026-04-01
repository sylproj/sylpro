import database as db
import random
from datetime import date, timedelta

db.init_db()
conn = db.get_db()

LECTURER_ID  = 3
COUNSELOR_ID = 2

# Student profiles
# sid, name, gpa, absences, dep, anx, pan, trt, par_sup, par_edu, risk_level, risk_score, notes
PROFILES = [
    (1, 'Jane Student',     2.10, 14, 1, 1, 0, 0, 2, 2,
     'MEDIUM', 0.61,
     'Student reports persistent anxiety and low mood. GPA declining this semester. Monitoring required.'),

    (3, 'Sylvia Ngugi',     3.20,  3, 0, 0, 0, 0, 3, 3,
     'MINIMAL', 0.05,
     'No concerns. Student is performing well and reports positive wellbeing.'),

    (4, 'Max Stanley',      3.55,  2, 0, 0, 0, 0, 4, 3,
     'MINIMAL', 0.04,
     'Excellent standing. No concerns flagged.'),

    (5, 'Sylvester Arnold', 1.85, 16, 1, 1, 0, 0, 1, 1,
     'MEDIUM', 0.58,
     'Student struggling academically and reports anxiety and low mood. Parental support is low. Referral recommended.'),

    (6, 'Cindy Keruu',      1.10, 24, 1, 1, 1, 0, 1, 1,
     'HIGH', 0.91,
     'Critical concern. Very low GPA, extremely high absenteeism, and all three MH conditions present. Urgent intervention needed.'),

    (7, 'Madispe Kumbulu',  0.90, 26, 1, 1, 1, 0, 1, 1,
     'HIGH', 0.96,
     'Highest risk student. Near-zero GPA, near-total absenteeism, all MH conditions flagged. Has not sought treatment. Immediate action required.'),
]


# GRADES
print("Writing grades...")
for sid, name, gpa, absences, dep, anx, pan, trt, par_sup, par_edu, *_ in PROFILES:
    db.upsert_grade(
        student_id        = sid,
        uploaded_by       = LECTURER_ID,
        gpa               = gpa,
        absences          = absences,
        parental_support  = par_sup,
        tutoring          = 0,
        parental_education= par_edu,
    )
print(f"  Done — {len(PROFILES)} grade records")


# ATTENDANCE
print("Writing attendance...")

def session_dates(n=29):
    dates, d = [], date.today()
    while len(dates) < n:
        if d.weekday() in (0, 2, 4):
            dates.append(d)
        d -= timedelta(days=1)
    return sorted(dates)

def attendance_pattern(total, absences):
    random.seed(total + absences)
    present = [1] * total
    # Spread absences toward the latter half (realistic drift)
    late  = list(range(total // 2, total))
    early = list(range(0, total // 2))
    picks = random.sample(late, min(absences, len(late)))
    remaining = absences - len(picks)
    if remaining > 0:
        picks += random.sample(early, min(remaining, len(early)))
    for i in picks:
        present[i] = 0
    return present

DATES   = session_dates(29)
SESSIONS = [f"Week {i//3+1} - {'Lecture' if i%3 < 2 else 'Tutorial'}" for i in range(29)]

att_count = 0
# Clear existing attendance first
conn.execute("DELETE FROM attendance")
conn.commit()

for sid, name, gpa, absences, *_ in PROFILES:
    pattern = attendance_pattern(29, absences)
    for i, (d, s) in enumerate(zip(DATES, SESSIONS)):
        conn.execute(
            "INSERT OR IGNORE INTO attendance (student_id, lecturer_id, date, present, session) VALUES (?,?,?,?,?)",
            (sid, LECTURER_ID, str(d), pattern[i], s)
        )
        att_count += 1

conn.commit()
print(f"  Done — {att_count} attendance records")


# 3. MH ASSESSMENTS
print("Writing MH assessments...")
conn.execute("DELETE FROM assessments")
conn.commit()

for sid, name, gpa, absences, dep, anx, pan, trt, par_sup, par_edu, risk_level, risk_score, notes in PROFILES:
    conn.execute("""
        INSERT INTO assessments
            (student_id, counselor_id, depression, anxiety, panic_attack, sought_treatment, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (sid, COUNSELOR_ID, dep, anx, pan, trt, notes))

conn.commit()
print(f"  Done — {len(PROFILES)} assessments")


# RISK SCORES
print("Writing risk scores...")
conn.execute("DELETE FROM risk_scores")
conn.commit()

for sid, name, gpa, absences, dep, anx, pan, trt, par_sup, par_edu, risk_level, risk_score, notes in PROFILES:
    att_rate = round((29 - absences) / 29 * 100, 1)
    mh_score = dep + anx + pan
    cond_gpa  = 1 if gpa < 1.5 else 0
    cond_att  = 1 if absences >= 22 else 0
    cond_mh   = 1 if mh_score >= 2 else 0
    conditions_met = cond_gpa + cond_att + cond_mh

    conn.execute("""
        INSERT INTO risk_scores
            (student_id, risk_score, risk_level, conditions_met,
             cond_gpa, cond_attendance, cond_mh,
             gpa, attendance_rate, mh_score, run_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (sid, risk_score, risk_level, conditions_met,
          cond_gpa, cond_att, cond_mh,
          gpa, att_rate, mh_score, COUNSELOR_ID))

conn.commit()
conn.close()
print(f"  Done — {len(PROFILES)} risk scores")

