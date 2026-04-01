import os, io, csv
from datetime import datetime, date

import pandas as pd
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, send_file)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import database as db
from model_utils import predict_student, get_nudge_messages

# Setup 
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')

app = Flask(__name__)
app.secret_key = "sylpro-secret-key-2024"
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

with app.app_context():
    db.init_db()
    db.seed_users()

# HELPERS
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'csv'

def login_required(role=None):
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to continue.', 'warning')
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                flash('You do not have permission to access that page.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapper
    return decorator

def get_current_user():
    if 'user_id' in session:
        return db.get_user_by_id(session['user_id'])
    return None

def _get_data_status(student_id):
    grade      = db.get_student_latest_grade(student_id)
    assessment = db.get_student_latest_assessment(student_id)
    att        = db.get_student_attendance_summary(student_id)

    has_grade = grade is not None
    has_mh    = assessment is not None
    has_att   = att['total_sessions'] > 0

    missing = []
    if not has_grade: missing.append('grades')
    if not has_mh:    missing.append('MH assessment')

    return {
        'status'    : 'ready' if (has_grade and has_mh) else ('partial' if (has_grade or has_mh) else 'incomplete'),
        'has_grade' : has_grade,
        'has_mh'    : has_mh,
        'has_att'   : has_att,
        'missing'   : missing,
        'last_scored': db.get_student_latest_risk(student_id),
    }

def _auto_run_model(student_id, triggered_by_user_id):
    """Run model if data is sufficient. Returns True if prediction was saved."""
    grade      = db.get_student_latest_grade(student_id)
    assessment = db.get_student_latest_assessment(student_id)
    student    = db.get_student_by_id(student_id)
    att        = db.get_student_attendance_summary(student_id)

    if not grade or not assessment or not student:
        return False

    # Use actual attendance rate if available, else derive from absences
    if att['total_sessions'] > 0 and att['attendance_rate'] is not None:
        att_rate = att['attendance_rate']
        absences = att['absences']
    else:
        absences = grade['absences'] or 0
        att_rate = round((db.SEMESTER_SESSIONS - absences) / db.SEMESTER_SESSIONS * 100, 1)

    student_data = {
        'gpa'               : grade['gpa'] or 0,
        'absences'          : absences,
        'study_time'        : 0,  # removed from UI, default 0
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
        db.save_risk_score(
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
            run_by         = triggered_by_user_id
        )
        return True
    except Exception:
        return False

def _process_uploaded_csv(df, uploader_id):
    """
    Process uploaded CSV.
    - Matches rows to students by student_no or name.
    - Calls upsert_grade — always updates, never duplicates.
    - Auto-runs model for matched students.
    Returns (students_processed, students_scored).
    """
    df.columns = [c.lower().replace(' ', '_') for c in df.columns]
    processed  = 0
    scored_ids = []
    conn       = db.get_db()

    for _, row in df.iterrows():
        student_no = str(row.get('student_no', row.get('studentid', ''))).strip()
        name       = str(row.get('name',       row.get('student_name', ''))).strip()

        student = None
        if student_no and student_no != 'nan':
            student = conn.execute(
                "SELECT * FROM students WHERE student_no=?", (student_no,)
            ).fetchone()
        if not student and name and name != 'nan':
            student = conn.execute(
                "SELECT * FROM students WHERE name=?", (name,)
            ).fetchone()
        if not student:
            continue

        sid        = student['id']
        grade_cols = ['gpa', 'grade_class', 'absences', 'studytimeweekly', 'gradeclass']

        if any(c in df.columns for c in grade_cols):
            gpa         = float(row.get('gpa', 0) or 0)
            absences    = int(row.get('absences', 0) or 0)
            par_support = int(row.get('parental_support', 2) or 2)
            tutoring    = int(row.get('tutoring', 0) or 0)
            par_edu     = int(row.get('parental_education', 2) or 2)
            conn.close()
            db.upsert_grade(
                student_id=sid, uploaded_by=uploader_id,
                gpa=gpa, absences=absences,
                parental_support=par_support, tutoring=tutoring,
                parental_education=par_edu
            )
            conn = db.get_db()
            scored_ids.append(sid)
            processed += 1

    conn.close()

    scored = sum(1 for sid in scored_ids if _auto_run_model(sid, uploader_id))
    return processed, scored

def _build_explanation(risk):
    triggers = []
    if risk['cond_gpa']:
        triggers.append(f"GPA of {risk['gpa']} is below the passing threshold of 2.0")
    if risk['cond_attendance']:
        triggers.append(f"Attendance rate of {risk['attendance_rate']}% indicates high absenteeism")
    if risk['cond_mh']:
        triggers.append(f"Mental health score of {risk['mh_score']}/3 indicates 2 or more active conditions")
    if triggers:
        return "Risk triggered by: " + "; ".join(triggers) + "."
    return "No individual threshold exceeded. Overall pattern suggests low risk."

# AUTH
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user     = db.get_user_by_email(email)
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['name']    = user['name']
            session['role']    = user['role']
            flash(f"Welcome back, {user['name']}!", 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    role = session.get('role')
    if   role == 'admin':     return redirect(url_for('admin_dashboard'))
    elif role == 'counselor': return redirect(url_for('counselor_dashboard'))
    elif role == 'lecturer':  return redirect(url_for('lecturer_dashboard'))
    elif role == 'student':   return redirect(url_for('student_dashboard'))
    return redirect(url_for('login'))

# ADMIN
@app.route('/admin')
@login_required(role='admin')
def admin_dashboard():
    users    = db.get_all_users()
    students = db.get_all_students()
    summary  = db.get_risk_summary()
    conn     = db.get_db()
    uploads  = conn.execute(
        "SELECT ul.*, u.name as uploader FROM upload_log ul "
        "LEFT JOIN users u ON ul.uploaded_by=u.id "
        "ORDER BY ul.uploaded_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    stats = {
        'total_users'   : len(users),
        'total_students': len(students),
        'total_scored'  : summary['total_scored'] if summary else 0,
        'high_risk'     : summary['high_count']   if summary else 0,
    }
    return render_template('admin/dashboard.html',
        user=get_current_user(), stats=stats, users=users, recent_uploads=uploads)

@app.route('/admin/users')
@login_required(role='admin')
def admin_users():
    return render_template('admin/users.html',
        user=get_current_user(), users=db.get_all_users())

@app.route('/admin/users/add', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_add_user():
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        role     = request.form.get('role', '')

        if not all([name, email, password, role]):
            flash('All fields are required.', 'danger')
            return redirect(url_for('admin_add_user'))

        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (?,?,?,?)",
                (name, email, generate_password_hash(password), role)
            )
            conn.commit()
            if role == 'student':
                uid        = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                student_no = request.form.get('student_no', f'STU{uid:04d}')
                conn.execute(
                    "INSERT INTO students (user_id, student_no, name, age, gender, course, year) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uid, student_no,
                     name,
                     request.form.get('age', 18),
                     request.form.get('gender', 0),
                     request.form.get('course', ''),
                     request.form.get('year', 1))
                )
                conn.commit()
            flash(f"User '{name}' created successfully.", 'success')
        except Exception as e:
            flash(f"Error: {str(e)}", 'danger')
        finally:
            conn.close()
        return redirect(url_for('admin_users'))

    return render_template('admin/add_user.html', user=get_current_user())

@app.route('/admin/users/toggle/<int:user_id>')
@login_required(role='admin')
def admin_toggle_user(user_id):
    if user_id == session['user_id']:
        flash('You cannot disable your own account.', 'warning')
        return redirect(url_for('admin_users'))
    conn = db.get_db()
    conn.execute("UPDATE users SET is_active=1-is_active WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash('User status updated.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required(role='admin')
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        flash('You cannot delete your own account.', 'warning')
        return redirect(url_for('admin_users'))
    conn = db.get_db()
    try:
        user    = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        student = conn.execute("SELECT * FROM students WHERE user_id=?", (user_id,)).fetchone()
        if student:
            sid = student['id']
            for tbl in ['risk_scores', 'assessments', 'grades', 'attendance', 'students']:
                col = 'student_id' if tbl != 'students' else 'id'
                conn.execute(f"DELETE FROM {tbl} WHERE {col}=?", (sid,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        flash(f"User '{user['name']}' deleted.", 'success')
    except Exception as e:
        flash(f"Error: {str(e)}", 'danger')
    finally:
        conn.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/students')
@login_required(role='admin')
def admin_students():
    return render_template('admin/students.html',
        user=get_current_user(), students=db.get_all_students())

@app.route('/admin/upload', methods=['GET', 'POST'])
@login_required(role='admin')
def admin_upload():
    if request.method == 'POST':
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            flash('Please select at least one CSV file.', 'warning')
            return redirect(url_for('admin_upload'))
        for f in files:
            if f and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                f.save(filepath)
                try:
                    df               = pd.read_csv(filepath)
                    processed, scored = _process_uploaded_csv(df, session['user_id'])
                    db.log_upload(filename, session['user_id'], len(df), 'success')
                    flash(f"{filename}: {len(df)} rows, {processed} students updated, {scored} scored.", 'success')
                except Exception as e:
                    db.log_upload(filename, session['user_id'], 0, f'error: {e}')
                    flash(f"{filename}: Error — {e}", 'danger')
            else:
                flash(f"{f.filename}: Not a valid CSV.", 'warning')
        return redirect(url_for('admin_upload'))

    conn    = db.get_db()
    uploads = conn.execute(
        "SELECT ul.*, u.name as uploader FROM upload_log ul "
        "LEFT JOIN users u ON ul.uploaded_by=u.id "
        "ORDER BY ul.uploaded_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template('admin/upload.html',
        user=get_current_user(), uploads=uploads)

# COUNSELOR
@app.route('/counselor')
@login_required(role='counselor')
def counselor_dashboard():
    students = db.get_all_students()
    summary  = db.get_risk_summary()
    scores   = db.get_latest_risk_per_student()
    scores_by_sid = {s['student_id']: s for s in scores}

    student_status = []
    for s in students:
        status = _get_data_status(s['id'])
        student_status.append({
            'student': s,
            'status' : status,
            'risk'   : scores_by_sid.get(s['id']),
        })

    return render_template('counselor/dashboard.html',
        user=get_current_user(),
        student_status=student_status,
        summary=summary, scores=scores)

@app.route('/counselor/assess/<int:student_id>', methods=['GET', 'POST'])
@login_required(role='counselor')
def counselor_assess(student_id):
    student = db.get_student_by_id(student_id)
    if not student:
        flash('Student not found.', 'danger')
        return redirect(url_for('counselor_dashboard'))

    if request.method == 'POST':
        conn = db.get_db()
        conn.execute("""
            INSERT INTO assessments
                (student_id, counselor_id, depression, anxiety, panic_attack, sought_treatment, notes)
            VALUES (?,?,?,?,?,?,?)
        """, (student_id, session['user_id'],
              int(request.form.get('depression', 0)),
              int(request.form.get('anxiety', 0)),
              int(request.form.get('panic_attack', 0)),
              int(request.form.get('sought_treatment', 0)),
              request.form.get('notes', '')))
        conn.commit()
        conn.close()

        ran = _auto_run_model(student_id, session['user_id'])
        if ran:
            flash(f"Assessment saved — risk score updated for {student['name']}.", 'success')
            return redirect(url_for('counselor_predict_view', student_id=student_id))
        flash(f"Assessment saved. Add grade data to generate a risk score.", 'info')
        return redirect(url_for('counselor_dashboard'))

    return render_template('counselor/assessment.html',
        user=get_current_user(), student=student,
        latest=db.get_student_latest_assessment(student_id),
        grade=db.get_student_latest_grade(student_id))

@app.route('/counselor/predict/view/<int:student_id>')
@login_required(role='counselor')
def counselor_predict_view(student_id):
    student    = db.get_student_by_id(student_id)
    assessment = db.get_student_latest_assessment(student_id)
    grade      = db.get_student_latest_grade(student_id)
    risk       = db.get_student_latest_risk(student_id)

    if not risk:
        flash('No risk score yet. Ensure both grade and MH assessment data exist.', 'warning')
        return redirect(url_for('counselor_dashboard'))

    result = {
        'risk_score'     : risk['risk_score'],
        'risk_level'     : risk['risk_level'],
        'conditions_met' : risk['conditions_met'],
        'cond_gpa'       : risk['cond_gpa'],
        'cond_attendance': risk['cond_attendance'],
        'cond_mh'        : risk['cond_mh'],
        'gpa'            : risk['gpa'],
        'attendance_rate': risk['attendance_rate'],
        'mh_score'       : risk['mh_score'],
        'explanation'    : _build_explanation(risk),
    }
    return render_template('counselor/result.html',
        user=get_current_user(), student=student,
        result=result, assessment=assessment, grade=grade)

@app.route('/counselor/predict/run/<int:student_id>')
@login_required(role='counselor')
def counselor_predict_run(student_id):
    student = db.get_student_by_id(student_id)
    if _auto_run_model(student_id, session['user_id']):
        flash(f"Risk model run for {student['name']}.", 'success')
        return redirect(url_for('counselor_predict_view', student_id=student_id))
    flash('Cannot run model — missing grade or MH assessment data.', 'warning')
    return redirect(url_for('counselor_dashboard'))

@app.route('/counselor/batch-run', methods=['POST'])
@login_required(role='counselor')
def counselor_batch_run():
    conn     = db.get_db()
    students = conn.execute("SELECT id FROM students").fetchall()
    conn.close()
    scored  = sum(1 for s in students if _auto_run_model(s['id'], session['user_id']))
    skipped = len(students) - scored
    flash(f"Batch run complete: {scored} scored, {skipped} skipped (missing data).", 'success')
    return redirect(url_for('counselor_results'))

@app.route('/counselor/results')
@login_required(role='counselor')
def counselor_results():
    level  = request.args.get('level', 'ALL')
    scores = db.get_latest_risk_per_student()
    if level != 'ALL':
        scores = [s for s in scores if s['risk_level'] == level]
    return render_template('counselor/results.html',
        user=get_current_user(), scores=scores,
        summary=db.get_risk_summary(), selected_level=level)

@app.route('/counselor/download')
@login_required(role='counselor')
def counselor_download():
    scores = db.get_latest_risk_per_student()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['StudentID','StudentNo','Name','Course','Year',
                     'RiskScore','RiskLevel','ConditionsMet',
                     'FlagLowGPA','FlagHighAbsences','FlagMHCondition',
                     'GPA','AttendanceRate','MHScore','DateRun'])
    for s in scores:
        writer.writerow([s['student_id'], s['student_no'], s['student_name'],
                         s['course'], s['year'], s['risk_score'], s['risk_level'],
                         s['conditions_met'], s['cond_gpa'], s['cond_attendance'],
                         s['cond_mh'], s['gpa'], s['attendance_rate'],
                         s['mh_score'], s['date_run']])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f"risk_report_{date.today()}.csv")

# LECTURER
@app.route('/lecturer')
@login_required(role='lecturer')
def lecturer_dashboard():
    students = db.get_all_students()
    conn     = db.get_db()
    today_count = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND lecturer_id=?",
        (str(date.today()), session['user_id'])
    ).fetchone()[0]
    conn.close()
    return render_template('lecturer/dashboard.html',
        user=get_current_user(), students=students, today_count=today_count)

@app.route('/lecturer/attendance', methods=['GET', 'POST'])
@login_required(role='lecturer')
def lecturer_attendance():
    students = db.get_all_students()

    if request.method == 'POST':
        session_name = request.form.get('session', '').strip()
        att_date     = request.form.get('date', str(date.today()))

        if not session_name:
            flash('Please enter a session name before saving.', 'warning')
            return render_template('lecturer/attendance.html',
                user=get_current_user(), students=students, today=str(date.today()))

        # Duplicate session check
        existing = db.check_duplicate_attendance(att_date, session_name)
        if existing > 0:
            flash(
                f'Attendance for "{session_name}" on {att_date} has already been recorded '
                f'({existing} entries exist). Edit is not supported — contact admin if a correction is needed.',
                'warning'
            )
            return redirect(url_for('lecturer_attendance'))

        conn  = db.get_db()
        saved = 0
        for student in students:
            present = 1 if request.form.get(f'present_{student["id"]}') else 0
            conn.execute(
                "INSERT OR IGNORE INTO attendance (student_id, lecturer_id, date, present, session) "
                "VALUES (?,?,?,?,?)",
                (student['id'], session['user_id'], att_date, present, session_name)
            )
            saved += 1
        conn.commit()
        conn.close()
        flash(f"Attendance saved: {saved} students for {session_name} on {att_date}.", 'success')
        return redirect(url_for('lecturer_attendance'))

    return render_template('lecturer/attendance.html',
        user=get_current_user(), students=students, today=str(date.today()))

@app.route('/lecturer/grades', methods=['GET', 'POST'])
@login_required(role='lecturer')
def lecturer_grades():
    students = db.get_all_students()

    if request.method == 'POST':
        # CSV upload
        if 'file' in request.files and request.files['file'].filename:
            f = request.files['file']
            if allowed_file(f.filename):
                filename = secure_filename(f.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                f.save(filepath)
                df               = pd.read_csv(filepath)
                processed, scored = _process_uploaded_csv(df, session['user_id'])
                db.log_upload(filename, session['user_id'], len(df), 'success')
                flash(f"Grades uploaded: {processed} updated, {scored} scored.", 'success')
            else:
                flash('Please upload a valid CSV file.', 'danger')
        else:
            # Manual entry — UPSERT via db.upsert_grade
            student_id = request.form.get('student_id')
            gpa        = float(request.form.get('gpa', 0) or 0)
            absences   = int(request.form.get('absences', 0) or 0)

            if not student_id:
                flash('Please select a student.', 'warning')
                return redirect(url_for('lecturer_grades'))

            db.upsert_grade(
                student_id=int(student_id),
                uploaded_by=session['user_id'],
                gpa=gpa,
                absences=absences
            )

            # Grade class label for feedback
            labels = {0:'A', 1:'B', 2:'C', 3:'D', 4:'F'}
            gc     = db.gpa_to_grade_class(gpa)

            ran = _auto_run_model(int(student_id), session['user_id'])
            msg = f"Grade saved (GPA {gpa} → Grade {labels[gc]}). "
            if gpa < 2.0:
                msg += " GPA is below the failing threshold of 2.0. "
            msg += "Risk score updated." if ran else "Risk score will update once MH assessment is added."
            flash(msg, 'warning' if gpa < 2.0 else 'success')

        return redirect(url_for('lecturer_grades'))

    # Load current grades for display
    grade_map = {}
    for s in students:
        g = db.get_student_latest_grade(s['id'])
        if g:
            grade_map[s['id']] = g

    return render_template('lecturer/grades.html',
        user=get_current_user(), students=students, grade_map=grade_map)

@app.route('/lecturer/class-risk')
@login_required(role='lecturer')
def lecturer_class_risk():
    return render_template('lecturer/class_risk.html',
        user=get_current_user(), scores=db.get_latest_risk_per_student())

# STUDENT
@app.route('/student')
@login_required(role='student')
def student_dashboard():
    student = db.get_student_by_user_id(session['user_id'])
    if not student:
        flash('Student record not found. Contact admin.', 'warning')
        return render_template('student/dashboard.html',
            user=get_current_user(), student=None, nudges=[])

    sid         = student['id']
    att_summary = db.get_student_attendance_summary(sid)
    grade       = db.get_student_latest_grade(sid)
    assessment  = db.get_student_latest_assessment(sid)

    student_data = {
        'gpa'         : grade['gpa'] if grade else 3.0,
        'absences'    : (grade['absences'] or 0) if grade else 0,
        'depression'  : assessment['depression'] if assessment else 0,
        'anxiety'     : assessment['anxiety']    if assessment else 0,
        'panic_attack': assessment['panic_attack'] if assessment else 0,
    }
    risk_result = {
        'mh_score'       : sum([student_data['depression'], student_data['anxiety'], student_data['panic_attack']]),
        'attendance_rate': att_summary['attendance_rate'] if att_summary['attendance_rate'] else 100,
    }
    nudges = get_nudge_messages(student_data, risk_result)

    return render_template('student/dashboard.html',
        user=get_current_user(), student=student,
        att_summary=att_summary, grade=grade, nudges=nudges)

@app.route('/student/attendance')
@login_required(role='student')
def student_attendance():
    student = db.get_student_by_user_id(session['user_id'])
    if not student:
        return redirect(url_for('student_dashboard'))
    return render_template('student/attendance.html',
        user=get_current_user(), student=student,
        records=db.get_student_attendance(student['id']),
        summary=db.get_student_attendance_summary(student['id']))

# RUN
if __name__ == '__main__':
    print("\n" + "="*50)
    print("  SylPro — Student Risk Detection System")
    print("  http://127.0.0.1:5000")
    print("="*50 + "\n")
    app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False)
