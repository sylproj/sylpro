import sqlite3
import os
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "sylpro.db")

# Semester config
# Change this to match your actual semester session count.
# Used as the denominator for attendance rate when no records exist yet.
SEMESTER_SESSIONS = 29


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# SCHEMA
def init_db():
    conn = get_db()
    c    = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL CHECK(role IN ('admin','counselor','lecturer','student')),
            created_at    TEXT    DEFAULT (datetime('now')),
            is_active     INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER REFERENCES users(id),
            student_no TEXT    NOT NULL UNIQUE,
            name       TEXT    NOT NULL,
            age        INTEGER,
            gender     INTEGER,
            course     TEXT,
            year       INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  INTEGER NOT NULL REFERENCES students(id),
            lecturer_id INTEGER NOT NULL REFERENCES users(id),
            date        TEXT    NOT NULL,
            present     INTEGER NOT NULL DEFAULT 0,
            session     TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(student_id, date, session)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS assessments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id       INTEGER NOT NULL REFERENCES students(id),
            counselor_id     INTEGER NOT NULL REFERENCES users(id),
            depression       INTEGER NOT NULL DEFAULT 0,
            anxiety          INTEGER NOT NULL DEFAULT 0,
            panic_attack     INTEGER NOT NULL DEFAULT 0,
            sought_treatment INTEGER DEFAULT 0,
            notes            TEXT,
            date             TEXT DEFAULT (datetime('now'))
        )
    """)

    # Grades: one record per student (UPSERT on student_id)
    # study_time kept for model compatibility but not shown in UI
    c.execute("""
        CREATE TABLE IF NOT EXISTS grades (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id         INTEGER NOT NULL UNIQUE REFERENCES students(id),
            uploaded_by        INTEGER NOT NULL REFERENCES users(id),
            gpa                REAL,
            grade_class        INTEGER,
            study_time         REAL    DEFAULT 0,
            absences           INTEGER,
            parental_support   INTEGER DEFAULT 2,
            tutoring           INTEGER DEFAULT 0,
            extracurricular    INTEGER DEFAULT 0,
            sports             INTEGER DEFAULT 0,
            music              INTEGER DEFAULT 0,
            volunteering       INTEGER DEFAULT 0,
            parental_education INTEGER DEFAULT 2,
            semester           TEXT,
            uploaded_at        TEXT    DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_scores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            risk_score      REAL    NOT NULL,
            risk_level      TEXT    NOT NULL,
            conditions_met  INTEGER NOT NULL,
            cond_gpa        INTEGER DEFAULT 0,
            cond_attendance INTEGER DEFAULT 0,
            cond_mh         INTEGER DEFAULT 0,
            gpa             REAL,
            attendance_rate REAL,
            mh_score        INTEGER,
            run_by          INTEGER REFERENCES users(id),
            date_run        TEXT    DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS upload_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            filename       TEXT NOT NULL,
            uploaded_by    INTEGER REFERENCES users(id),
            rows_processed INTEGER,
            status         TEXT,
            uploaded_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print("Database tables created")

# SEED
def seed_users():
    conn = get_db()
    c    = conn.cursor()

    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        conn.close()
        return

    for name, email, password, role in [
        ("Admin User",    "admin@sylpro.com",     "admin123",   "admin"),
        ("Dr. Counselor", "counselor@sylpro.com", "counsel123", "counselor"),
        ("Mr. Lecturer",  "lecturer@sylpro.com",  "lecture123", "lecturer"),
        ("Jane Student",  "student@sylpro.com",   "student123", "student"),
    ]:
        c.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?,?,?,?)",
            (name, email, generate_password_hash(password), role)
        )
    conn.commit()

    student_uid = c.execute(
        "SELECT id FROM users WHERE role='student' LIMIT 1"
    ).fetchone()['id']
    c.execute(
        "INSERT INTO students (user_id, student_no, name, age, gender, course, year) VALUES (?,?,?,?,?,?,?)",
        (student_uid, "STU001", "Jane Student", 20, 0, "Computer Science", 2)
    )
    conn.commit()
    conn.close()
    print("Seed users created")

# HELPERS
def gpa_to_grade_class(gpa):
    """Auto-calculate grade class from GPA. Used on every grade save."""
    gpa = float(gpa or 0)
    if gpa >= 3.5: return 0   # A
    if gpa >= 3.0: return 1   # B
    if gpa >= 2.5: return 2   # C
    if gpa >= 2.0: return 3   # D
    return 4                  # F  

# USERS
def get_user_by_email(email):
    conn = get_db()
    u = conn.execute(
        "SELECT * FROM users WHERE email=? AND is_active=1", (email,)
    ).fetchone()
    conn.close()
    return u

def get_user_by_id(user_id):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return u

def get_all_users():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    conn.close()
    return users

# STUDENTS
def get_all_students():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.*, u.email,
            (SELECT risk_level FROM risk_scores r
             WHERE r.student_id = s.id
             ORDER BY r.date_run DESC LIMIT 1) as latest_risk
        FROM students s
        LEFT JOIN users u ON s.user_id = u.id
        ORDER BY s.name
    """).fetchall()
    conn.close()
    return rows

def get_student_by_id(student_id):
    conn = get_db()
    s = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    conn.close()
    return s

def get_student_by_user_id(user_id):
    conn = get_db()
    s = conn.execute("SELECT * FROM students WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return s

# ATTENDANCE
def get_student_attendance(student_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, u.name as lecturer_name
        FROM attendance a
        JOIN users u ON a.lecturer_id = u.id
        WHERE a.student_id = ?
        ORDER BY a.date DESC, a.session ASC
    """, (student_id,)).fetchall()
    conn.close()
    return rows


def get_student_attendance_summary(student_id):
    """
    Returns a plain dict (never None) with attendance stats.
    total_sessions = actual sessions recorded in DB.
    attendance_rate = attended / total * 100, or None if no sessions yet.
    """
    conn = get_db()
    row = conn.execute("""
        SELECT
            COUNT(*)                   as total_sessions,
            COALESCE(SUM(present), 0)  as attended,
            COUNT(*) - COALESCE(SUM(present), 0) as absences
        FROM attendance
        WHERE student_id = ?
    """, (student_id,)).fetchone()
    conn.close()

    total    = row['total_sessions'] if row else 0
    attended = row['attended']       if row else 0
    absences = row['absences']       if row else 0

    if total > 0:
        rate = round(attended * 100.0 / total, 1)
    else:
        rate = None   

    return {
        'total_sessions' : total,
        'attended'       : attended,
        'absences'       : absences,
        'attendance_rate': rate,
    }


def check_duplicate_attendance(date, session):
    """
    Check if attendance has already been recorded for a given date+session.
    Returns count of existing records.
    """
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND session=?",
        (date, session)
    ).fetchone()[0]
    conn.close()
    return count

# GRADES
def upsert_grade(student_id, uploaded_by, gpa, absences,
                 parental_support=2, tutoring=0,
                 extracurricular=0, sports=0, music=0,
                 volunteering=0, parental_education=2, semester=None):
    """
    Insert or update the single grade record for a student.
    grade_class is always auto-calculated from GPA.
    study_time defaults to 0 (removed from UI).
    """
    grade_class = gpa_to_grade_class(gpa)
    conn = get_db()
    conn.execute("""
        INSERT INTO grades
            (student_id, uploaded_by, gpa, grade_class, study_time, absences,
             parental_support, tutoring, extracurricular, sports, music,
             volunteering, parental_education, semester, uploaded_at)
        VALUES (?,?,?,?,0,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(student_id) DO UPDATE SET
            uploaded_by        = excluded.uploaded_by,
            gpa                = excluded.gpa,
            grade_class        = excluded.grade_class,
            absences           = excluded.absences,
            parental_support   = excluded.parental_support,
            tutoring           = excluded.tutoring,
            extracurricular    = excluded.extracurricular,
            sports             = excluded.sports,
            music              = excluded.music,
            volunteering       = excluded.volunteering,
            parental_education = excluded.parental_education,
            semester           = excluded.semester,
            uploaded_at        = datetime('now')
    """, (student_id, uploaded_by, gpa, grade_class, absences,
          parental_support, tutoring, extracurricular, sports,
          music, volunteering, parental_education, semester))
    conn.commit()
    conn.close()


def get_student_latest_grade(student_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM grades WHERE student_id=?", (student_id,)
    ).fetchone()
    conn.close()
    return row

# ASSESSMENTS
def get_student_latest_assessment(student_id):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM assessments
        WHERE student_id=?
        ORDER BY date DESC LIMIT 1
    """, (student_id,)).fetchone()
    conn.close()
    return row

# RISK SCORES
def get_student_latest_risk(student_id):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM risk_scores
        WHERE student_id=?
        ORDER BY date_run DESC LIMIT 1
    """, (student_id,)).fetchone()
    conn.close()
    return row


def get_latest_risk_per_student():
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*, s.name as student_name, s.student_no, s.course, s.year
        FROM risk_scores r
        JOIN students s ON r.student_id = s.id
        WHERE r.id = (
            SELECT id FROM risk_scores r2
            WHERE r2.student_id = r.student_id
            ORDER BY date_run DESC LIMIT 1
        )
        ORDER BY r.risk_score DESC
    """).fetchall()
    conn.close()
    return rows


def get_risk_summary():
    conn = get_db()
    row = conn.execute("""
        SELECT
            COUNT(DISTINCT student_id) as total_scored,
            SUM(CASE WHEN risk_level='HIGH'    THEN 1 ELSE 0 END) as high_count,
            SUM(CASE WHEN risk_level='MEDIUM'  THEN 1 ELSE 0 END) as medium_count,
            SUM(CASE WHEN risk_level='LOW'     THEN 1 ELSE 0 END) as low_count,
            SUM(CASE WHEN risk_level='MINIMAL' THEN 1 ELSE 0 END) as minimal_count
        FROM (
            SELECT student_id, risk_level,
                   ROW_NUMBER() OVER (PARTITION BY student_id ORDER BY date_run DESC) as rn
            FROM risk_scores
        ) WHERE rn = 1
    """).fetchone()
    conn.close()
    return row


def save_risk_score(student_id, risk_score, risk_level, conditions_met,
                    cond_gpa, cond_att, cond_mh, gpa, att_rate, mh_score, run_by):
    conn = get_db()
    conn.execute("""
        INSERT INTO risk_scores
            (student_id, risk_score, risk_level, conditions_met,
             cond_gpa, cond_attendance, cond_mh, gpa, attendance_rate,
             mh_score, run_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (student_id, risk_score, risk_level, conditions_met,
          cond_gpa, cond_att, cond_mh, gpa, att_rate, mh_score, run_by))
    conn.commit()
    conn.close()

# UPLOAD LOG
def log_upload(filename, uploaded_by, rows_processed, status):
    conn = get_db()
    conn.execute(
        "INSERT INTO upload_log (filename, uploaded_by, rows_processed, status) VALUES (?,?,?,?)",
        (filename, uploaded_by, rows_processed, status)
    )
    conn.commit()
    conn.close()


def _auto_run_model_standalone(student_id, run_by_user_id):
    """
    Standalone model runner — used by populate_data.py and any
    script running outside the Flask request context.
    Identical logic to app.py's _auto_run_model but self-contained.
    """
    from model_utils import predict_student

    grade      = get_student_latest_grade(student_id)
    assessment = get_student_latest_assessment(student_id)
    student    = get_student_by_id(student_id)
    att        = get_student_attendance_summary(student_id)

    if not grade or not assessment or not student:
        return False

    if att['total_sessions'] > 0 and att['attendance_rate'] is not None:
        att_rate = att['attendance_rate']
        absences = att['absences']
    else:
        absences = grade['absences'] or 0
        att_rate = round((SEMESTER_SESSIONS - absences) / SEMESTER_SESSIONS * 100, 1)

    student_data = {
        'gpa'               : grade['gpa'] or 0,
        'absences'          : absences,
        'study_time'        : 0,
        'grade_class'       : grade['grade_class'] or 4,
        'depression'        : assessment['depression'],
        'anxiety'           : assessment['anxiety'],
        'panic_attack'      : assessment['panic_attack'],
        'sought_treatment'  : assessment['sought_treatment'],
        'parental_support'  : grade['parental_support'] or 2,
        'tutoring'          : grade['tutoring'] or 0,
        'extracurricular'   : grade['extracurricular'] or 0,
        'sports'            : grade['sports'] or 0,
        'music'             : grade['music'] or 0,
        'volunteering'      : grade['volunteering'] or 0,
        'parental_education': grade['parental_education'] or 2,
        'age'               : student['age'] or 20,
        'gender'            : student['gender'] or 0,
    }

    try:
        result = predict_student(student_data)
        save_risk_score(
            student_id     = student_id,
            risk_score     = result['risk_score'],
            risk_level     = result['risk_level'],
            conditions_met = result['conditions_met'],
            cond_gpa       = result['cond_gpa'],
            cond_att       = result['cond_attendance'],
            cond_mh        = result['cond_mh'],
            gpa            = result['gpa'],
            att_rate       = result['attendance_rate'],
            mh_score       = result['mh_score'],
            run_by         = run_by_user_id
        )
        return True
    except Exception as e:
        print(f"  Model error for student_id={student_id}: {e}")
        return False


if __name__ == "__main__":
    init_db()
    seed_users()
    print(f"Database ready at: {DB_PATH}")
