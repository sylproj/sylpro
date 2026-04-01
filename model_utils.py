import os
import json
import joblib
import numpy as np
import pandas as pd

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR  = os.path.join(BASE_DIR, "model")

MODEL_PATH    = os.path.join(MODEL_DIR, "dropout_risk_model.pkl")
SCALER_PATH   = os.path.join(MODEL_DIR, "feature_scaler.pkl")
METADATA_PATH = os.path.join(MODEL_DIR, "model_metadata.json")

# Thresholds (defaults — overridden by metadata if present)

GPA_THRESHOLD  = 1.5
ABS_THRESHOLD  = 22
MH_THRESHOLD   = 2

# Feature columns (must match training order exactly)

FEATURE_COLS = [
    'MHScore', 'AttendanceRate', 'GPA',
    'Depression', 'Anxiety', 'PanicAttack', 'SoughtTreatment',
    'Absences',
    'GradeClass', 'StudyTimeWeekly', 'StudyEfficiency',
    'MH_x_Absences', 'MH_x_GPA', 'Absences_x_StudyTime', 'GPA_x_StudyTime',
    'SupportScore', 'ActivityScore', 'ParentalEducation', 'Tutoring',
    'Age', 'Gender',
]


def load_model():
    """Load model, scaler and metadata. Returns (model, scaler, meta)."""
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. "
            "Please copy dropout_risk_model.pkl into the model/ folder."
        )

    model  = joblib.load(MODEL_PATH)
    scaler = joblib.load(SCALER_PATH) if os.path.exists(SCALER_PATH) else None

    meta = {}
    if os.path.exists(METADATA_PATH):
        with open(METADATA_PATH) as f:
            meta = json.load(f)

    return model, scaler, meta


def build_feature_row(student_data: dict) -> pd.DataFrame:
    """
    Build a single-row feature DataFrame from student_data dict.

    Expected keys:
        gpa, absences, study_time, grade_class,
        depression, anxiety, panic_attack, sought_treatment,
        parental_support, tutoring, extracurricular, sports,
        music, volunteering, parental_education, age, gender
    """
    gpa         = float(student_data.get('gpa', 0))
    absences    = int(student_data.get('absences', 0))
    study_time  = float(student_data.get('study_time', 0))
    grade_class = int(student_data.get('grade_class', 4))
    depression  = int(student_data.get('depression', 0))
    anxiety     = int(student_data.get('anxiety', 0))
    panic       = int(student_data.get('panic_attack', 0))
    treatment   = int(student_data.get('sought_treatment', 0))
    par_support = int(student_data.get('parental_support', 2))
    tutoring    = int(student_data.get('tutoring', 0))
    extra       = int(student_data.get('extracurricular', 0))
    sports      = int(student_data.get('sports', 0))
    music       = int(student_data.get('music', 0))
    volunteer   = int(student_data.get('volunteering', 0))
    par_edu     = int(student_data.get('parental_education', 2))
    age         = int(student_data.get('age', 20))
    gender      = int(student_data.get('gender', 0))

    # Derived features
    attendance_rate  = round((29 - absences) / 29 * 100, 2)
    mh_score         = depression + anxiety + panic
    study_efficiency = round(gpa / (study_time + 0.1), 4)
    support_score    = par_support + tutoring
    activity_score   = extra + sports + music + volunteer

    row = {
        'MHScore'            : mh_score,
        'AttendanceRate'     : attendance_rate,
        'GPA'                : gpa,
        'Depression'         : depression,
        'Anxiety'            : anxiety,
        'PanicAttack'        : panic,
        'SoughtTreatment'    : treatment,
        'Absences'           : absences,
        'GradeClass'         : grade_class,
        'StudyTimeWeekly'    : study_time,
        'StudyEfficiency'    : study_efficiency,
        'MH_x_Absences'      : mh_score * absences,
        'MH_x_GPA'           : mh_score * gpa,
        'Absences_x_StudyTime': absences * study_time,
        'GPA_x_StudyTime'    : gpa * study_time,
        'SupportScore'       : support_score,
        'ActivityScore'      : activity_score,
        'ParentalEducation'  : par_edu,
        'Tutoring'           : tutoring,
        'Age'                : age,
        'Gender'             : gender,
    }

    return pd.DataFrame([row])[FEATURE_COLS]


def risk_level_label(score: float) -> str:
    if score >= 0.75:   return 'HIGH'
    elif score >= 0.50: return 'MEDIUM'
    elif score >= 0.25: return 'LOW'
    else:               return 'MINIMAL'


def predict_student(student_data: dict) -> dict:
    """
    Run prediction for a single student.
    Returns a dict with risk_score, risk_level, conditions, explanation.
    """
    model, scaler, meta = load_model()

    # Override thresholds from metadata if available
    thresholds  = meta.get('label_thresholds', {})
    gpa_thresh  = thresholds.get('gpa', GPA_THRESHOLD)
    abs_thresh  = thresholds.get('absences_p75', ABS_THRESHOLD)
    mh_thresh   = thresholds.get('mh_score', MH_THRESHOLD)

    X = build_feature_row(student_data)

    best_model_name = meta.get('best_model', '')
    if 'Logistic' in best_model_name and scaler:
        X_input = scaler.transform(X)
    else:
        X_input = X.values

    risk_score = float(model.predict_proba(X_input)[0][1])
    level      = risk_level_label(risk_score)

    # Evaluate 2-of-3 conditions
    gpa      = float(student_data.get('gpa', 0))
    absences = int(student_data.get('absences', 0))
    mh_score = (int(student_data.get('depression', 0)) +
                int(student_data.get('anxiety', 0)) +
                int(student_data.get('panic_attack', 0)))

    cond_gpa = int(gpa < gpa_thresh)
    cond_att = int(absences >= abs_thresh)
    cond_mh  = int(mh_score >= mh_thresh)
    conditions_met = cond_gpa + cond_att + cond_mh

    att_rate = round((29 - absences) / 29 * 100, 1)

    # Build plain-English explanation
    triggers = []
    if cond_gpa: triggers.append(f"GPA of {gpa:.2f} is below the threshold of {gpa_thresh}")
    if cond_att: triggers.append(f"{absences} absences exceeds the high-risk threshold of {int(abs_thresh)}")
    if cond_mh:  triggers.append(f"Mental health score of {mh_score} indicates {mh_score} active condition(s)")

    if triggers:
        explanation = "Risk triggered by: " + "; ".join(triggers) + "."
    else:
        explanation = "No individual threshold exceeded. Overall pattern suggests low risk."

    return {
        'risk_score'     : round(risk_score, 4),
        'risk_level'     : level,
        'conditions_met' : conditions_met,
        'cond_gpa'       : cond_gpa,
        'cond_attendance': cond_att,
        'cond_mh'        : cond_mh,
        'gpa'            : gpa,
        'attendance_rate': att_rate,
        'mh_score'       : mh_score,
        'explanation'    : explanation,
    }


def predict_bulk_csv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run predictions on a DataFrame of students.
    Expects columns matching the field names used in build_feature_row.
    Returns the DataFrame with risk columns appended.
    """
    model, scaler, meta = load_model()
    thresholds = meta.get('label_thresholds', {})
    gpa_thresh = thresholds.get('gpa', GPA_THRESHOLD)
    abs_thresh = thresholds.get('absences_p75', ABS_THRESHOLD)
    mh_thresh  = thresholds.get('mh_score', MH_THRESHOLD)

    # Map common column name variations
    col_map = {
        'study_time_weekly': 'study_time', 'studytimeweekly': 'study_time',
        'panic': 'panic_attack', 'panicattack': 'panic_attack',
        'sought_treatment': 'sought_treatment',
        'parental_support': 'parental_support',
        'parental_education': 'parental_education',
    }
    df.columns = [col_map.get(c.lower().replace(' ', '_'), c.lower().replace(' ', '_'))
                  for c in df.columns]

    rows = []
    for _, row in df.iterrows():
        result = predict_student(row.to_dict())
        rows.append(result)

    result_df = pd.DataFrame(rows)

    # Preserve original identifying columns if present
    id_cols = [c for c in ['studentid', 'student_id', 'name', 'student_no'] if c in df.columns]
    if id_cols:
        result_df = pd.concat([df[id_cols].reset_index(drop=True), result_df], axis=1)

    return result_df


def get_nudge_messages(student_data: dict, risk_result: dict) -> list:
    """
    Generate behavioural nudge messages for the student portal.
    Returns plain-English guidance WITHOUT revealing the risk label.
    """
    nudges  = []
    absences = int(student_data.get('absences', 0))
    gpa      = float(student_data.get('gpa', 0))
    mh_score = risk_result.get('mh_score', 0)
    att_rate = risk_result.get('attendance_rate', 100)

    if absences >= 18:
        nudges.append({
            'type'   : 'warning',
            'message': f"You have missed {absences} sessions this semester. "
                       f"Students with high absences often find it harder to keep up. "
                       f"Consider speaking to your lecturer about catching up."
        })
    elif absences >= 10:
        nudges.append({
            'type'   : 'info',
            'message': f"You've missed {absences} sessions so far. "
                       f"Try to maintain regular attendance to stay on track."
        })

    if gpa < 1.5:
        nudges.append({
            'type'   : 'warning',
            'message': "Your current GPA is below the passing threshold. "
                       "We recommend visiting the academic support centre or "
                       "requesting extra sessions from your lecturer."
        })
    elif gpa < 2.5:
        nudges.append({
            'type'   : 'info',
            'message': "Your GPA has room for improvement. "
                       "A study group or tutoring session could make a real difference."
        })

    if mh_score >= 2:
        nudges.append({
            'type'   : 'support',
            'message': "Our counseling team is available for a confidential chat. "
                       "Many students find it helpful to talk through how they're feeling. "
                       "You can book a session through the counselor's office."
        })
    elif mh_score == 1:
        nudges.append({
            'type'   : 'support',
            'message': "University can be stressful. If you ever need someone to talk to, "
                       "our counselors are here for you."
        })

    if not nudges:
        nudges.append({
            'type'   : 'success',
            'message': "You're on track! Keep up the good attendance and study habits."
        })

    return nudges
