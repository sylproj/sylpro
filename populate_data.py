import database as db
import random
from datetime import date, timedelta

db.init_db()
conn = db.get_db()

# Student profiles
# (student_id, user_id, name, profile)
# profile: gpa, absences, depression, anxiety, panic, treatment, risk_expectation
STUDENTS = [
    # id, name,              gpa,  abs, dep, anx, pan, trt,  notes
    (1,  "Jane Student",     3.45,  3,   0,   0,   0,   0,  "Good standing"),
    (3,  "Sylvia Ngugi",     2.85,  6,   0,   1,   0,   0,  "Moderate concern"),
    (4,  "Max Stanley",      1.60, 18,   1,   1,   0,   1,  "High risk - low GPA + absences + MH"),
    (5,  "Sylvester Arnold", 2.20, 11,   1,   0,   0,   0,  "Medium risk - borderline GPA + absences"),
    (6,  "Cindy Keruu",      3.75,  1,   0,   0,   0,   0,  "Top student"),
    (7,  "Madispe Kumbulu",  1.35, 22,   1,   1,   1,   0,  "Very high risk"),
]

LECTURER_ID  = 3
COUNSELOR_ID = 2
SEMESTER_SESSIONS = 29

# GRADES 
print("Inserting grades...")
grade_data = {
    1: dict(gpa=3.45, absences=3,  parental_support=3, tutoring=0, parental_education=3),
    3: dict(gpa=2.85, absences=6,  parental_support=2, tutoring=1, parental_education=2),
    4: dict(gpa=1.60, absences=18, parental_support=1, tutoring=0, parental_education=1),
    5: dict(gpa=2.20, absences=11, parental_support=2, tutoring=1, parental_education=2),
    6: dict(gpa=3.75, absences=1,  parental_support=4, tutoring=0, parental_education=4),
    7: dict(gpa=1.35, absences=22, parental_support=1, tutoring=0, parental_education=1),
}
for sid, g in grade_data.items():
    db.upsert_grade(
        student_id        = sid,
        uploaded_by       = LECTURER_ID,
        gpa               = g['gpa'],
        absences          = g['absences'],
        parental_support  = g['parental_support'],
        tutoring          = g['tutoring'],
        parental_education= g['parental_education'],
    )
print(f"  ✓ {len(grade_data)} grade records upserted")


# ATTENDANCE
# Generate 29 sessions over the past ~14 weeks (Mon/Wed/Fri pattern)
print("Inserting attendance...")

def generate_session_dates(n=29):
    """Generate n weekday dates going backwards from today."""
    sessions = []
    d = date.today()
    while len(sessions) < n:
        if d.weekday() in (0, 2, 4):  # Mon, Wed, Fri
            sessions.append(d)
        d -= timedelta(days=1)
    return sorted(sessions)

SESSION_DATES = generate_session_dates(SEMESTER_SESSIONS)
SESSION_NAMES = [f"Week {i//3 + 1} — {'Lecture' if i%3 in (0,1) else 'Tutorial'}"
                 for i in range(SEMESTER_SESSIONS)]

# Per-student attendance: derive present/absent from their absence count
# Distribute absences spread across later sessions (realistic pattern)
def build_attendance(total_sessions, absences):
    """Return list of 1/0 (present/absent). Absences cluster toward recent sessions."""
    present = [1] * total_sessions
    # Spread absences: weight toward last third of semester
    late_pool  = list(range(total_sessions // 2, total_sessions))
    early_pool = list(range(0, total_sessions // 2))
    absent_indices = []
    late_count  = min(absences, len(late_pool))
    early_count = absences - late_count
    absent_indices += random.sample(late_pool,  late_count)
    absent_indices += random.sample(early_pool, min(early_count, len(early_pool)))
    for i in absent_indices:
        present[i] = 0
    return present

random.seed(42)
att_inserted = 0
for sid, name, gpa, absences, *_ in STUDENTS:
    attendance_pattern = build_attendance(SEMESTER_SESSIONS, absences)
    for i, (session_date, session_name) in enumerate(zip(SESSION_DATES, SESSION_NAMES)):
        present = attendance_pattern[i]
        try:
            conn.execute(
                "INSERT OR IGNORE INTO attendance "
                "(student_id, lecturer_id, date, present, session) VALUES (?,?,?,?,?)",
                (sid, LECTURER_ID, str(session_date), present, session_name)
            )
            att_inserted += 1
        except Exception as e:
            print(f"  Warning: {e}")

conn.commit()
print(f"  ✓ {att_inserted} attendance records inserted ({SEMESTER_SESSIONS} sessions × {len(STUDENTS)} students)")


# MENTAL HEALTH ASSESSMENTS
print("Inserting MH assessments...")
mh_data = {
    # sid: (depression, anxiety, panic_attack, sought_treatment, notes)
    1: (0, 0, 0, 0, "Student appears well-adjusted. No concerns flagged at this time."),
    3: (0, 1, 0, 0, "Student reported mild anxiety, particularly around exam periods. Monitoring advised."),
    4: (1, 1, 0, 1, "Student has sought treatment. Reports persistent low mood and anxiety. High absenteeism noted. Follow-up scheduled."),
    5: (1, 0, 0, 0, "Student reports low mood but has not sought treatment. Borderline academic performance. Refer for counseling."),
    6: (0, 0, 0, 0, "No concerns. Student is performing excellently and reports good wellbeing."),
    7: (1, 1, 1, 0, "Student presents with depression, anxiety, and panic attacks. Has not sought treatment. Urgent follow-up required. Very high absenteeism."),
}

mh_inserted = 0
for sid, (dep, anx, pan, trt, notes) in mh_data.items():
    conn.execute("""
        INSERT INTO assessments
            (student_id, counselor_id, depression, anxiety, panic_attack, sought_treatment, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (sid, COUNSELOR_ID, dep, anx, pan, trt, notes))
    mh_inserted += 1

conn.commit()
conn.close()
print(f"  ✓ {mh_inserted} MH assessments inserted")


# AUTO-RUN RISK MODEL for all students
print("Running risk model for all students...")
scored = 0
for sid, *_ in STUDENTS:
    if db._auto_run_model_standalone(sid, COUNSELOR_ID):
        scored += 1
    else:
        print(f"  Skipped student_id={sid} — insufficient data")

print(f"  ✓ {scored} risk scores generated")
