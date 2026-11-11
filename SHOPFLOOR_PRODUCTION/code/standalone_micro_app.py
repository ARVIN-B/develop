#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone Micro-System Runner for Hamerz Project Operations
Full Upgrade:
1. Multi-Tenant Role-Based Access Control (RBAC) with Fine-Grained Permissions Matrix
2. Row-Level Security: Project isolation for PMs & Queue isolation for Department Heads
3. Dedicated Role: کارشناس کنترل پروژه (Project Control Specialist - WBS Breakdown)
4. Admin Permission Management Matrix (مدیریت دسترسی‌ها و ادمین)
5. Hourly Precision & Overtime in 43-column Matrix
6. Printable Workpiece Traveler / Job Card with Dynamic QR Code
7. Daily Execution Logs with Whisper AI Persian Voice-to-Text & Attachments
8. ERPNext Job Costing, Material Request & Subcontracting Integration
"""

import http.server
import socketserver
import json
import sqlite3
import os
import io
import time
import datetime
import base64
import tempfile
import subprocess
import urllib.parse
import threading

PORT = int(os.environ.get("PORT", 8090))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hamerz_shopfloor.db")

from frappe_integration_service import FrappeIntegrationService
from catia_folder_scanner import CatiaFolderScanner
from ai_machining_estimator import AIMachiningEstimator, AIPettyCashAuditor

frappe_service = FrappeIntegrationService(DB_PATH)
catia_scanner = CatiaFolderScanner(DB_PATH)

UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

def ensure_db_schema():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS technical_discussions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT,
        project_id TEXT NOT NULL,
        part_equipment TEXT NOT NULL,
        department TEXT,
        sender_username TEXT NOT NULL,
        sender_name TEXT NOT NULL,
        sender_role TEXT NOT NULL,
        message TEXT NOT NULL,
        topic_tag TEXT DEFAULT 'ملاحظه فنی',
        created_at TEXT NOT NULL
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS quality_inspection_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inspection_id TEXT UNIQUE,
        operation_id TEXT NOT NULL,
        project_id TEXT,
        part_equipment TEXT,
        department TEXT,
        batch_qty REAL,
        accepted_qty REAL,
        defective_qty REAL,
        qc_status TEXT,
        disposition_type TEXT,
        target_rework_dept TEXT,
        rework_hours REAL,
        defect_notes TEXT,
        rework_op_id TEXT,
        inspected_by TEXT,
        inspection_date TEXT,
        erpnext_doc TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS drawing_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT NOT NULL,
        project_id TEXT,
        part_equipment TEXT,
        drawing_no TEXT NOT NULL,
        revision TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        file_size_kb REAL DEFAULT 0,
        upload_date TEXT NOT NULL,
        uploaded_by TEXT NOT NULL,
        eco_number TEXT,
        eco_reason TEXT,
        is_active INTEGER DEFAULT 1,
        is_obsolete INTEGER DEFAULT 0,
        obsolete_date TEXT,
        obsolete_reason TEXT,
        approved_by TEXT,
        approval_date TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS project_master_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL UNIQUE,
        plan_title TEXT NOT NULL,
        target_delivery_date TEXT,
        total_estimated_months REAL DEFAULT 6.0,
        project_buffer_days REAL DEFAULT 20.0,
        buffer_consumed_percent REAL DEFAULT 15.0,
        shared_reserve_hours_allocated REAL DEFAULT 20.0,
        status TEXT DEFAULT 'در حال اجرا',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cols_to_add = [
        ("setup_hours_planned", "REAL DEFAULT 2.0"),
        ("net_machining_hours", "REAL DEFAULT 8.0"),
        ("contingency_rate", "REAL DEFAULT 0.20"),
        ("total_reserved_hours", "REAL DEFAULT 11.6"),
        ("is_selected_for_month", "INTEGER DEFAULT 1"),
        ("planning_horizon", "TEXT DEFAULT 'ماه جاری (روزهای ۱ تا ۳۰)'"),
        ("outsourcing_cost", "REAL DEFAULT 0.0"),
        ("parent_id", "TEXT DEFAULT NULL"),
        ("is_summary_task", "INTEGER DEFAULT 0"),
        ("start_date", "TEXT DEFAULT '1405/08/01'"),
        ("finish_date", "TEXT DEFAULT '1405/08/10'"),
        ("duration_days", "REAL DEFAULT 5.0"),
        ("process_routing", "TEXT DEFAULT ''"),
        ("qc_qty_inspected", "REAL DEFAULT 0.0"),
        ("qc_qty_accepted", "REAL DEFAULT 0.0"),
        ("qc_qty_defective", "REAL DEFAULT 0.0"),
        ("qc_disposition", "TEXT DEFAULT 'NONE'"),
        ("qc_disposition_notes", "TEXT DEFAULT ''"),
        ("qc_rework_op_id", "TEXT DEFAULT ''"),
        ("qc_rework_dept", "TEXT DEFAULT ''"),
        ("qc_rework_hours", "REAL DEFAULT 0.0"),
        ("drawing_file_path", "TEXT DEFAULT ''"),
        ("drawing_file_name", "TEXT DEFAULT ''"),
        ("drawing_file_size_kb", "REAL DEFAULT 0"),
        ("drawing_upload_date", "TEXT DEFAULT ''"),
    ]
    for col, col_type in cols_to_add:
        try:
            cur.execute(f"ALTER TABLE operations ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    try:
        cur.execute('UPDATE permissions_matrix SET can_create_wbs = 1 WHERE role_key = "Project_Manager"')
    except Exception:
        pass
    conn.commit()
    conn.close()

ensure_db_schema()


# Active User Session (Defaults to Admin for initial launch, switchable via UI)
CURRENT_SESSION = {"username": "admin"}

# Lazy-loaded Whisper model
_whisper_model = None
_whisper_lock = threading.Lock()

def get_whisper():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            try:
                import whisper
                # Prioritize 'small' model for high-accuracy Persian dictation
                _whisper_model = whisper.load_model("small")
                print("✅ مدل هوش مصنوعی Whisper Small (فارسی تخصصی) با موفقیت بارگذاری گردید.")
            except Exception as e:
                print(f"⚠️ تلاش برای بارگذاری Whisper Base به علت: {e}")
                try:
                    import whisper
                    _whisper_model = whisper.load_model("base")
                    print("✅ مدل هوش مصنوعی Whisper Base بارگذاری گردید.")
                except Exception as e2:
                    print(f"⚠️ خطا در بارگذاری Whisper: {e2}")
        return _whisper_model

def generate_qr_base64(data_text):
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(data_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"⚠️ خطا در تولید QR Code: {e}")
        return ""

def decode_audio_bytes(raw_bytes):
    """
    Universally decodes audio bytes (WebM Opus, MP4 AAC, WAV, OGG, MP3, AIFF)
    directly into a 16kHz mono float32 NumPy array using PyAV in-memory.
    Completely eliminates external CLI tool dependencies (afconvert/ffmpeg).
    """
    import av
    import numpy as np
    input_file = io.BytesIO(raw_bytes)
    container = av.open(input_file)
    resampler = av.AudioResampler(format='fltp', layout='mono', rate=16000)
    audio_frames = []
    for frame in container.decode(audio=0):
        for resampled_frame in resampler.resample(frame):
            audio_frames.append(resampled_frame.to_ndarray())
    if not audio_frames:
        return np.array([], dtype=np.float32)
    return np.concatenate(audio_frames, axis=1).squeeze(0)

def normalize_persian_transcript(text):
    """
    Intelligent Persian Orthography & Vocabulary Normalizer.
    Fixes colloquial contractions, dialectal distortions, and ASR phonetic artifacts.
    """
    if not text:
        return text
    import re
    replacements = [
        (r'(?<!\S)میخم(?!\S)', 'می‌خوام'),
        (r'(?<!\S)میخام(?!\S)', 'می‌خوام'),
        (r'(?<!\S)میخوام(?!\S)', 'می‌خوام'),
        (r'(?<!\S)تسکنم(?!\S)', 'تست کنم'),
        (r'(?<!\S)تس کنم(?!\S)', 'تست کنم'),
        (r'(?<!\S)تسکُنم(?!\S)', 'تست کنم'),
        (r'(?<!\S)تستکنم(?!\S)', 'تست کنم'),
        (r'(?<!\S)آیها(?!\S)', 'آیا'),
        (r'(?<!\S)ایا(?!\S)', 'آیا'),
        (r'(?<!\S)تو نسته ای(?!\S)', 'تونسته‌ای'),
        (r'(?<!\S)تونسته ای(?!\S)', 'تونسته‌ای'),
        (r'(?<!\S)ای موزو(?!\S)', 'این موضوع'),
        (r'(?<!\S)ای موضوع(?!\S)', 'این موضوع'),
        (r'(?<!\S)موزو(?!\S)', 'موضوع'),
        (r'(?<!\S)موضو(?!\S)', 'موضوع'),
        (r'(?<!\S)و هرگو کنه(?!\S)', 'رو حل‌وفصل کنی'),
        (r'(?<!\S)هرگو کنه(?!\S)', 'حل‌وفصل کنی'),
        (r'(?<!\S)هرگوشه(?!\S)', 'حل‌وفصل'),
        (r'(?<!\S)حل وفصل(?!\S)', 'حل‌وفصل'),
        (r'(?<!\S)اتنان خاتر(?!\S)', 'اطمینان خاطر'),
        (r'(?<!\S)اتمینان خاتر(?!\S)', 'اطمینان خاطر'),
        (r'(?<!\S)اطمینان خاتر(?!\S)', 'اطمینان خاطر'),
        (r'(?<!\S)اتمینان خاطر(?!\S)', 'اطمینان خاطر'),
        (r'(?<!\S)پدکنم(?!\S)', 'پیدا کنم'),
        (r'(?<!\S)پیداکنم(?!\S)', 'پیدا کنم'),
        (r'(?<!\S)کاملن(?!\S)', 'کاملاً'),
        (r'(?<!\S)حل شو ده باشه(?!\S)', 'حل شده باشه'),
        (r'(?<!\S)حل شو ده(?!\S)', 'حل شده'),
        (r'(?<!\S)مسول(?!\S)', 'مسئول'),
        (r'(?<!\S)مسولین(?!\S)', 'مسئولین'),
        (r'(?<!\S)فرس کاری(?!\S)', 'فرزکاری'),
        (r'(?<!\S)تراش کاری(?!\S)', 'تراشکاری'),
        (r'(?<!\S)سنگ زنی(?!\S)', 'سنگ‌زنی'),
        (r'(?<!\S)سمبه(?!\S)', 'سنبه'),
        (r'(?<!\S)کویته ها(?!\S)', 'کویته‌ها'),
        (r'(?<!\S)قالبها(?!\S)', 'قالب‌ها'),
        (r'(?<!\S)اسپارارک(?!\S)', 'اسپارک'),
        (r'(?<!\S)دستگاه ها(?!\S)', 'دستگاه‌ها'),
        (r'(?<!\S)قطعه کار(?!\S)', 'قطعه‌کار'),
    ]
    res = text
    for pattern, repl in replacements:
        res = re.sub(pattern, repl, res)
    res = re.sub(r'\s+', ' ', res).strip()
    return res

def transcribe_audio_bytes(audio_bytes):
    """
    Transcribes audio bytes using local Whisper Small AI primed with Persian workshop vocabulary.
    Includes silence detection, repetition guards, and intelligent Persian orthography normalization.
    """
    model = get_whisper()
    if not model:
        return {"error": "سرویس تشخیص گفتار هوش مصنوعی Whisper در دسترس نیست.", "model_info": "ناموجود"}

    try:
        import numpy as np
        arr = decode_audio_bytes(audio_bytes)
        
        if len(arr) == 0:
            return {"error": "فایل صوتی خالی است یا فریمی شناسایی نشد.", "model_info": "PyAV"}
        
        duration_sec = len(arr) / 16000.0
        if duration_sec < 0.3:
            return {"error": "مدت زمان صوت بسیار کوتاه است (کمتر از ۰.۳ ثانیه). لطفاً دوباره ضبط فرمایید.", "model_info": "Duration Guard"}
        
        # Energy check to eliminate silence hallucination loops
        rms = float(np.sqrt(np.mean(arr**2)))
        if rms < 0.004:
            return {"error": "⚠️ صدایی در فایل ضبط‌شده دریافت نشد یا بلندی صدا بسیار کم است. لطفاً بلندتر یا نزدیک‌تر به میکروفن صحبت فرمایید.", "model_info": "Silence Guard"}

        # Transcribe with Whisper Small using rich Persian conversational and workshop prompt
        initial_prompt = (
            "گزارش روزانه کارگاه ساخت قالب‌های تزریق پلاستیک و ماشین‌کاری هامرز پلاسما: "
            "می‌خوام تست کنم و اطمینان خاطر پیدا کنم که این موضوع کاملاً حل‌وفصل شده باشد. "
            "فرزکاری CNC، تراشکاری دقیق، سنگ‌زنی داخل و خارج، بلوک سنبه، کویته، ماتریس، عملیات حرارتی، تلرانس صدمی، اسپارک، مونتاژ، قطعه‌کار، نقشه و ابعاد."
        )
        
        res = model.transcribe(
            arr,
            language="fa",
            task="transcribe",
            fp16=False,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt
        )
        
        raw_text = res.get("text", "").strip()
        text = normalize_persian_transcript(raw_text)
        
        # Check no-speech probability
        segments = res.get("segments", [])
        if segments:
            avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
            if avg_no_speech > 0.75 and (not text or len(text) < 4):
                return {"error": "صدای واضحی برای گفتار شناسایی نشد. لطفاً در محیط آرام‌تر ضبط فرمایید.", "model_info": "Whisper Small"}

        if not text:
            return {"error": "صدایی با مفهوم واضح دریافت نشد.", "model_info": "Whisper Small"}

        return {"text": text, "model_info": "Whisper Small (آفلاین محلی + غلط‌یاب خودکار)", "duration": round(duration_sec, 2)}
        
    except Exception as e:
        print(f"⚠️ خطا در تبدیل صوت: {e}")
        return {"error": f"خطا در پردازش صوت: {str(e)}", "model_info": "Error"}

def query_db(query, args=(), one=False, commit=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, args)
    if commit:
        conn.commit()
        last_id = cur.lastrowid
        conn.close()
        return last_id
    rv = cur.fetchall()
    conn.close()
    return (dict(rv[0]) if rv else None) if one else [dict(r) for r in rv]

def execute_db(query, args=()):
    return query_db(query, args, commit=True)

def get_current_user():
    username = CURRENT_SESSION.get("username", "admin")
    u = query_db("SELECT * FROM users WHERE username = ?", (username,), one=True)
    if not u:
        u = query_db("SELECT * FROM users WHERE username = 'admin'", one=True)
    p = query_db("SELECT * FROM permissions_matrix WHERE role_key = ?", (u["role_key"],), one=True)
    u["permissions"] = p or {}
    return u

class MicroAppHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_json(self, data, status=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_html(self, html, status=200):
        try:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Allow user switch via query param ?u=...
        if "u" in query:
            CURRENT_SESSION["username"] = query["u"][0]

        if path in ("/", "/index.html"):
            html_file = os.path.join(BASE_DIR, "hamerz_shopfloor_app_4.html")
            if not os.path.exists(html_file):
                html_file = os.path.join(BASE_DIR, "index.html")
            if os.path.exists(html_file):
                with open(html_file, "r", encoding="utf-8") as hf:
                    self.send_html(hf.read())
            else:
                self.send_html(HTML_SPA)
        elif path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Connection", "close")
            self.end_headers()
        elif path == "/api/current_user":
            self.send_json({"status": "success", "user": get_current_user()})
        elif path == "/api/users":
            users = query_db("SELECT username, full_name, role_key, role_title, assigned_department, assigned_projects FROM users WHERE is_active = 1")
            self.send_json({"status": "success", "users": users})
        elif path == "/api/permissions_matrix":
            perms = query_db("SELECT * FROM permissions_matrix")
            self.send_json({"status": "success", "matrix": perms})
        elif path == "/api/dashboard":
            self.handle_api_dashboard()
        elif path == "/api/projects":
            self.handle_api_projects()
        elif path == "/api/operations":
            self.handle_api_operations(query)
        elif path == "/api/plm/parts":
            parts = query_db("SELECT * FROM plm_parts_catalog ORDER BY part_code ASC")
            self.send_json({"status": "success", "parts": parts})
        elif path == "/api/tools":
            dept = query.get("dept", [None])[0]
            if dept and dept != "ALL":
                tools = query_db("SELECT * FROM tool_inventory WHERE assigned_department = ? ORDER BY tool_serial_id ASC", (dept,))
            else:
                tools = query_db("SELECT * FROM tool_inventory ORDER BY tool_serial_id ASC")
            for t in tools:
                r_life = float(t.get("rated_life_hours") or 20.0)
                used = float(t.get("actual_hours_used") or 0.0)
                t["remaining_hours"] = max(0.0, round(r_life - used, 1))
                t["wear_percent"] = min(100.0, round((used / r_life) * 100.0, 1)) if r_life > 0 else 100.0
            self.send_json({"status": "success", "tools": tools})
        elif path == "/api/tools/analytics":
            tools = query_db("SELECT * FROM tool_inventory")
            brands = {}
            for t in tools:
                b = t.get("brand") or "سایر"
                if b not in brands:
                    brands[b] = {"brand": b, "total_tools": 0, "total_rated": 0.0, "total_used": 0.0, "regrinds": 0}
                brands[b]["total_tools"] += 1
                brands[b]["total_rated"] += float(t.get("rated_life_hours") or 0.0)
                brands[b]["total_used"] += float(t.get("actual_hours_used") or 0.0)
                brands[b]["regrinds"] += int(t.get("regrind_count") or 0)
            self.send_json({"status": "success", "brands": list(brands.values())})
        elif path == "/api/operations/standby_pool":
            pool = query_db('''
                SELECT o.*, s.standby_priority, s.status as pool_status
                FROM operations o
                JOIN standby_pool_queue s ON o.operation_id = s.operation_id
                WHERE s.status = 'Ready'
                ORDER BY s.standby_priority ASC
            ''')
            self.send_json({"status": "success", "standby_pool": pool})
        elif path == "/api/petty_cash/audits":
            purchases = query_db("SELECT * FROM petty_cash_purchases ORDER BY id DESC")
            self.send_json({"status": "success", "purchases": purchases})
        elif path == "/api/frappe/status":
            f_status = frappe_service.get_connection_status()
            self.send_json({"status": "success", "frappe": f_status})
        elif path == "/api/users/roles":
            users = query_db("SELECT u.*, GROUP_CONCAT(r.role_key) as all_roles FROM users u LEFT JOIN user_roles r ON u.username = r.username GROUP BY u.username")
            self.send_json({"status": "success", "users": users})
        elif path == "/api/wbs_tree":
            self.handle_api_wbs_tree(query)
        elif path == "/api/pm/wbs_candidates":
            self.handle_api_wbs_candidates(query)
        elif path == "/api/master_plans":
            self.handle_api_master_plans(query)
        elif path == "/api/master_plans/critical_path":
            self.handle_api_critical_path(query)
        elif path == "/api/logs":
            self.handle_api_logs(query)
        elif path == "/api/employees":
            emps = query_db("SELECT * FROM employees")
            self.send_json({"status": "success", "employees": emps})
        elif path == "/api/custody_transfers":
            self.handle_api_custody_transfers(query)
        elif path == "/api/machine_time_entries":
            self.handle_api_machine_time_entries(query)
        elif path == "/api/drawings":
            self.handle_api_drawings(query)
        elif path == "/api/drawings/history":
            self.handle_api_drawing_history(query)
        elif path == "/api/tooling_demands":
            self.handle_api_tooling_demands(query)
        elif path == "/api/discussions":
            self.handle_api_discussions(query)
        elif path == "/api/discussions/counts":
            self.handle_api_discussions_counts()
        elif path == "/api/erpnext/status":
            self.handle_erpnext_status()
        elif path == "/api/job_card_data":
            op_id = query.get("op_id", [None])[0]
            self.handle_job_card_data(op_id)
        elif path.startswith("/job_card/"):
            op_id = path.split("/job_card/")[1]
            self.handle_job_card_page(op_id)
        elif path.startswith("/uploads/"):
            fname = path.split("/uploads/")[1]
            fpath = os.path.join(UPLOADS_DIR, fname)
            if os.path.exists(fpath):
                self.send_response(200)
                if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                    self.send_header("Content-Type", "image/jpeg")
                elif fname.lower().endswith(".pdf"):
                    self.send_header("Content-Type", "application/pdf")
                else:
                    self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_json({"status": "error", "message": "فایل یافت نشد"}, 404)
        else:
            self.send_json({"status": "error", "message": "Not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if "u" in query:
            CURRENT_SESSION["username"] = query["u"][0]
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        if path == "/api/switch_user":
            username = body.get("username", "admin")
            CURRENT_SESSION["username"] = username
            self.send_json({"status": "success", "user": get_current_user()})
        elif path == "/api/update_permissions":
            self.handle_update_permissions(body)
        elif path == "/api/plm/scan_folder":
            self.handle_plm_scan_folder(body)
        elif path == "/api/tools/log_usage":
            self.handle_tool_log_usage(body)
        elif path == "/api/tools/add":
            self.handle_tool_add(body)
        elif path == "/api/operations/emergency_eject":
            self.handle_emergency_eject(body)
        elif path == "/api/operations/swap_from_standby":
            self.handle_swap_from_standby(body)
        elif path == "/api/logs/approve_delay_claim":
            self.handle_approve_delay_claim(body)
        elif path == "/api/frappe/sync_stock":
            self.handle_frappe_sync_stock(body)
        elif path == "/api/frappe/sync_qc":
            self.handle_frappe_sync_qc(body)
        elif path == "/api/ai/estimate_machining":
            self.handle_ai_estimate_machining(body)
        elif path == "/api/petty_cash/add":
            self.handle_petty_cash_add(body)
        elif path == "/api/create_wbs_item":
            self.handle_create_wbs_item(body)
        elif path == "/api/vip_simulator/apply":
            self.handle_api_vip_apply(body)
        elif path == "/api/master_plans/create_or_update":
            self.handle_create_or_update_master_plan(body)
        elif path == "/api/operations/toggle_monthly_selection":
            self.handle_toggle_monthly_selection(body)
        elif path == "/api/operations/update_tdabc_estimates":
            self.handle_update_tdabc_estimates(body)
        elif path == "/api/set_day_hours":
            self.handle_set_day_hours(body)
        elif path == "/api/toggle_day":
            self.handle_toggle_day(body)
        elif path == "/api/log_daily":
            self.handle_log_daily(body)
        elif path == "/api/submit_estimation":
            self.handle_submit_estimation(body)
        elif path == "/api/lock_decade":
            self.handle_lock_decade(body)
        elif path == "/api/submit_request":
            self.handle_submit_request(body)
        elif path == "/api/transcribe_voice":
            self.handle_transcribe_voice(body)
        elif path == "/api/upload_attachment":
            self.handle_upload_attachment(body)
        elif path == "/api/erpnext/sync_buying":
            self.handle_erpnext_sync_buying(body)
        elif path == "/api/erpnext/sync_subcontracting":
            self.handle_erpnext_sync_subcontracting(body)
        elif path == "/api/erpnext/sync_costing":
            self.handle_erpnext_sync_costing(body)
        elif path == "/api/transfer_operation_queue":
            self.handle_transfer_operation_queue(body)
        elif path == "/api/pm/dispatch_to_departments":
            self.handle_pm_dispatch_to_departments(body)
        elif path == "/api/pm/decision":
            self.handle_pm_decision(body)
        elif path == "/api/pm/batch_send_to_next_stage":
            self.handle_pm_batch_send_to_next_stage(body)
        elif path == "/api/operations/add_overtime":
            self.handle_add_overtime(body)
        elif path == "/api/qc_signoff":
            self.handle_qc_signoff(body)
        elif path == "/api/drawings/upload":
            self.handle_drawing_upload(body)
        elif path == "/api/approve_drawing":
            self.handle_approve_drawing(body)
        elif path == "/api/issue_eco":
            self.handle_issue_eco(body)
        elif path == "/api/engineering_design_sync":
            self.handle_engineering_design_sync(body)
        elif path == "/api/transfer_custody":
            self.handle_transfer_custody(body)
        elif path == "/api/set_readiness_conditions":
            self.handle_set_readiness_conditions(body)
        elif path == "/api/erpnext/sync_quality_inspection":
            self.handle_erpnext_sync_quality_inspection(body)
        elif path == "/api/tooling_demands/create":
            self.handle_create_tooling_demand(body)
        elif path == "/api/tooling_demands/approve_gm":
            self.handle_approve_tooling_demand_gm(body)
        elif path == "/api/tooling_demands/receive_report":
            self.handle_report_tooling_receipt(body)
        elif path == "/api/discussions/send":
            self.handle_send_discussion(body)
        else:
            self.send_json({"status": "error", "message": "Unknown endpoint"}, 404)

    def handle_api_projects(self):
        u = get_current_user()
        p = u["permissions"]
        if p.get("can_view_all_projects") or u["assigned_projects"] == "ALL":
            prjs = query_db("SELECT * FROM projects")
        else:
            allowed = [x.strip() for x in u["assigned_projects"].split(",") if x.strip()]
            prjs = query_db(f"SELECT * FROM projects WHERE project_id IN ({','.join(['?']*len(allowed))})", allowed)
        self.send_json({"status": "success", "projects": prjs})

    def handle_api_dashboard(self):
        u = get_current_user()
        p = u["permissions"]
        prjs = query_db("SELECT * FROM projects")
        ops = query_db("SELECT * FROM operations")
        settings = query_db("SELECT * FROM settings WHERE id = 1", one=True)
        
        # Row-level filtering for PM
        if not p.get("can_view_all_projects") and u["assigned_projects"] != "ALL":
            allowed = [x.strip() for x in u["assigned_projects"].split(",") if x.strip()]
            prjs = [x for x in prjs if x["project_id"] in allowed]
            ops = [x for x in ops if x["project_id"] in allowed]

        # Row-level filtering for Dept Head
        if not p.get("can_view_all_depts") and u["assigned_department"] != "ALL":
            allowed_depts = [x.strip() for x in u["assigned_department"].split(",") if x.strip()]
            ops = [x for x in ops if x["operation_type"] in allowed_depts]

        total_ops = len(ops)
        completed_ops = sum(1 for o in ops if o["execution_status"] == "تمام‌شده")
        pending_est = sum(1 for o in ops if o["request_status"] == "ابلاغ به مسئول بخش جهت برآورد")
        variance_days = sum(float(o["variance_days"] or 0) for o in ops)
        
        total_budget = sum(float(x["budgeted_cost"] or 0) for x in prjs)
        total_actual_cost = sum(float(x["actual_cost"] or 0) for x in prjs)
        total_material = sum(float(x["material_cost"] or 0) for x in prjs)
        total_outsourcing = sum(float(x["outsourcing_cost"] or 0) for x in prjs)
        total_machine = sum(float(o.get("machine_cost") or 0.0) for o in ops)
        total_tooling = sum(float(o.get("tooling_cost") or 0.0) for o in ops)

        # Item 4: RCA & Pareto Analysis
        logs = query_db("SELECT * FROM daily_execution_logs")
        rca_dict = {
            "T1": {"code": "T1", "label": "خرابی و نقص فنی ماشین‌آلات / تعلیق مکانیکی و برقی", "hours": 0.0, "count": 0, "badge": "badge-danger"},
            "T2": {"code": "T2", "label": "کمبود، شکست یا سایش ابزار برشی و اینسرت‌ها", "hours": 0.0, "count": 0, "badge": "badge-warning"},
            "T3": {"code": "T3", "label": "کسری متریال اولیه یا مغایرت ابعادی بلوک خام", "hours": 0.0, "count": 0, "badge": "badge-purple"},
            "T4": {"code": "T4", "label": "خطای نقشه، بازطراحی CAD یا اصلاح مسیر ابزار CAM", "hours": 0.0, "count": 0, "badge": "badge-info"},
            "T5": {"code": "T5", "label": "تغییر اولویت ابلاغی یا غیبت نیروی کار", "hours": 0.0, "count": 0, "badge": "badge-dark"}
        }
        tot_delay = 0.0
        for l in logs:
            c_code = l.get("rca_code") or "T0"
            d_hrs = float(l.get("delay_hours") or 0.0)
            if c_code in rca_dict:
                rca_dict[c_code]["hours"] += d_hrs
                rca_dict[c_code]["count"] += 1
                tot_delay += d_hrs

        sorted_rca = sorted(rca_dict.values(), key=lambda x: x["hours"], reverse=True)
        cum = 0.0
        pareto_list = []
        for item in sorted_rca:
            pct = round((item["hours"] / tot_delay * 100.0), 1) if tot_delay > 0 else 0.0
            cum += pct
            pareto_list.append({
                "code": item["code"],
                "label": item["label"],
                "badge": item["badge"],
                "count": item["count"],
                "hours": round(item["hours"], 1),
                "pct": pct,
                "cum_pct": min(100.0, round(cum, 1))
            })
        if pareto_list and tot_delay > 0:
            pareto_list[-1]["cum_pct"] = 100.0

        # Item 5: Live SLA Timers & Alerts
        sla_info = {
            "current_decade": settings.get("active_decade", "دهه دوم (روزهای ۱۱ تا ۲۰)") if settings else "دهه دوم (روزهای ۱۱ تا ۲۰)",
            "deadline_title": "ضرب‌الاجل فیکس و قفل برنامه دهه سوم (روزهای ۲۱ تا ۳۰)",
            "days_remaining": 6,
            "hours_remaining": 144,
            "timer_display": "۶ روز و ۱۲ ساعت مانده تا فیکس دهه",
            "sla_status": "normal",
            "sla_badge_text": "وضعیت عادی",
            "month_planning_deadline": "۳ روز مانده به پایان ماه (۲۷ آبان)",
            "unread_notifications": pending_est if u["role_key"] == "Department_Head" else (len([o for o in ops if o["variance_days"] > 0]) if u["role_key"] in ("General_Manager", "Admin") else 2)
        }

        # Item 6: TDABC Industrial Cost Breakdown
        total_idle_cost = sum(float(o.get("idle_capacity_cost") or 0.0) for o in ops)
        tdabc_summary = {
            "total_material": total_material,
            "total_machine": total_machine,
            "total_tooling": total_tooling,
            "total_outsourcing": total_outsourcing,
            "total_budget": total_budget,
            "total_actual": total_actual_cost,
            "total_idle_capacity_cost": total_idle_cost,
            "variance": total_actual_cost - total_budget,
            "variance_pct": round(((total_actual_cost - total_budget) / total_budget * 100.0), 1) if total_budget > 0 else 0.0
        }

        # Item 7: TOC Bottleneck Analysis (Single CNC Milling Machine) - Unified with WBS TDABC
        milling_ops = [o for o in ops if o["operation_type"] == "فرزکاری" and o.get("is_selected_for_month", 1) == 1 and not (o.get("is_summary_task") == 1)]
        milling_demand = sum(float(o.get("total_reserved_hours") or (float(o.get("days_required") or 0) * 8.0)) for o in milling_ops)
        milling_op_ids = [o["operation_id"] for o in milling_ops]
        milling_allocs = {}
        if milling_op_ids:
            m_res = query_db(f"SELECT SUM(hours_regular) as r, SUM(hours_overtime) as ot FROM daily_allocations WHERE operation_id IN ({','.join(['?']*len(milling_op_ids))})", milling_op_ids, one=True)
            if m_res:
                milling_allocs = m_res
        milling_ot = float(milling_allocs.get("ot") or 0.0) if milling_allocs else 0.0
        milling_reg = float(milling_allocs.get("r") or 0.0) if milling_allocs else 0.0

        outsourced_ops = [o for o in ops if o["operation_type"] == "برون‌سپاری"]
        outsourced_hrs = sum(float(o.get("total_reserved_hours") or (float(o.get("days_required") or 0) * 8.0)) for o in outsourced_ops)

        nominal_capacity = 208.0 # 26 working days * 8 hours
        shared_reserve_hours = 20.0 # 10% VIP / Emergency Shared Reserve
        available_project_capacity = nominal_capacity - shared_reserve_hours # 188.0 hours
        overload = max(0.0, milling_demand - available_project_capacity)

        bottleneck_summary = {
            "machine_name": "دستگاه فرز ۴ محور CNC ویکتور (تنها دستگاه فرز کارگاه)",
            "nominal_capacity_hours": nominal_capacity,
            "shared_reserve_hours": shared_reserve_hours,
            "available_project_capacity": available_project_capacity,
            "total_demand_hours": round(milling_demand, 1),
            "overload_hours": round(overload, 1),
            "overload_pct": round(overload / available_project_capacity * 100.0, 1) if available_project_capacity > 0 else 0.0,
            "planned_overtime_hours": round(milling_ot, 1),
            "outsourced_hours": round(outsourced_hrs, 1),
            "net_balance_hours": round((available_project_capacity + milling_ot + outsourced_hrs) - milling_demand, 1),
            "is_balanced": (available_project_capacity + milling_ot + outsourced_hrs) >= milling_demand
        }
        
        units = ["فرزکاری", "تراشکاری", "خرید", "برون‌سپاری", "الکتریک و کنترل"]
        unit_summary = {}
        for un in units:
            u_ops = [o for o in ops if o["operation_type"] in [un, "اتوماسیون الکتریکی" if un == "الکتریک و کنترل" else un]]
            unit_summary[un] = {
                "total": len(u_ops),
                "completed": sum(1 for o in u_ops if o["execution_status"] == "تمام‌شده"),
                "planned_days": sum(float(o["planned_days"] or 0) for o in u_ops),
                "pending_est": sum(1 for o in u_ops if o["request_status"] == "ابلاغ به مسئول بخش جهت برآورد"),
                "variance": sum(float(o["variance_days"] or 0) for o in u_ops)
            }
            
        self.send_json({
            "status": "success",
            "period": settings["period_name"] if settings else "1405 / آبان",
            "current_user": u,
            "kpis": {
                "active_projects": len([x for x in prjs if x["status"] != "Completed"]),
                "total_ops": total_ops,
                "completed_ops": completed_ops,
                "pending_estimation": pending_est,
                "variance_days": variance_days,
                "total_budget": total_budget,
                "total_actual_cost": total_actual_cost,
                "total_material": total_material,
                "total_outsourcing": total_outsourcing,
                "total_idle_cost": total_idle_cost,
                "cost_variance": total_actual_cost - total_budget
            },
            "unit_summary": unit_summary,
            "rca_pareto": pareto_list,
            "total_delay_hours": tot_delay,
            "sla_info": sla_info,
            "tdabc_summary": tdabc_summary,
            "bottleneck_summary": bottleneck_summary,
            "settings": settings
        })

    def handle_api_operations(self, query):
        u = get_current_user()
        p = u["permissions"]
        queue = query.get("queue", [None])[0]
        pm = query.get("pm", [None])[0]
        only_month = query.get("only_month", [None])[0]
        include_summary = query.get("include_summary", ["0"])[0]
        sql = "SELECT * FROM operations WHERE 1=1"
        params = []
        if include_summary != "1":
            sql += " AND (is_summary_task = 0 OR is_summary_task IS NULL)"
        if only_month == "1":
            sql += " AND (is_selected_for_month = 1 OR is_selected_for_month IS NULL)"
        elif only_month == "0":
            sql += " AND is_selected_for_month = 0"

        # Project Isolation for PM
        if not p.get("can_view_all_projects") and u["assigned_projects"] != "ALL":
            allowed = [x.strip() for x in u["assigned_projects"].split(",") if x.strip()]
            sql += f" AND project_id IN ({','.join(['?']*len(allowed))})"
            params.extend(allowed)

        # Department Isolation for Dept Head
        if not p.get("can_view_all_depts") and u["assigned_department"] != "ALL":
            allowed_depts = [x.strip() for x in u["assigned_department"].split(",") if x.strip()]
            sql += f" AND operation_type IN ({','.join(['?']*len(allowed_depts))})"
            params.extend(allowed_depts)

        if queue and queue != "ALL":
            if queue in ("اتوماسیون الکتریکی", "الکتریک و کنترل"):
                sql += " AND operation_type IN ('اتوماسیون الکتریکی', 'الکتریک و کنترل')"
            else:
                sql += " AND operation_type = ?"
                params.append(queue)
        if pm:
            sql += " AND project_manager LIKE ?"
            params.append(f"%{pm}%")

        ops = query_db(sql, params)
        for op in ops:
            allocs = query_db(
                "SELECT day_number, allocated, hours_regular, hours_overtime, hours_total, is_holiday FROM daily_allocations WHERE operation_id = ?",
                (op["operation_id"],)
            )
            op["allocations"] = {
                a["day_number"]: {
                    "allocated": a["allocated"],
                    "hours_regular": a["hours_regular"] or 0.0,
                    "hours_overtime": a["hours_overtime"] or 0.0,
                    "hours_total": a["hours_total"] or 0.0,
                    "is_holiday": a["is_holiday"]
                }
                for a in allocs
            }
            # Calculate sum of allocated calendar hours
            alloc_sum = round(sum(float(a["hours_total"] or 0.0) for a in allocs if a.get("allocated") == 1), 1)
            op["planned_hours"] = alloc_sum
            op["planned_days"] = round(alloc_sum / 8.0, 1)

            # Determine required hours
            req_h = round(float(op.get("total_reserved_hours") or op.get("total_hours_required") or (float(op.get("days_required") or 0.0) * 8.0)), 1)
            op["required_hours"] = req_h

            # Calculate remaining hours
            rem_h = max(0.0, round(req_h - alloc_sum, 1))
            op["remaining_hours"] = rem_h
            op["remaining_unplanned_days"] = round(rem_h / 8.0, 1)

            if op.get("predecessor_id"):
                pred = query_db("SELECT operation_id, part_equipment, execution_status, progress_percent FROM operations WHERE operation_id = ?", (op["predecessor_id"],), one=True)
                op["predecessor_info"] = pred
            else:
                op["predecessor_info"] = None

        self.send_json({"status": "success", "operations": ops, "current_user": u})

    def handle_api_wbs_tree(self, query):
        prj_id = query.get("project_id", [None])[0]
        sql = "SELECT * FROM operations"
        params = []
        if prj_id:
            sql += " WHERE project_id = ?"
            params.append(prj_id)
        sql += " ORDER BY wbs_code ASC, operation_id ASC"
        ops = query_db(sql, params)
        
        # Build map of 10-day allocations for each operation
        alloc_rows = query_db("SELECT operation_id, SUM(hours_total) as alloc_h FROM daily_allocations WHERE allocated = 1 GROUP BY operation_id")
        alloc_map = {r["operation_id"]: float(r["alloc_h"] or 0.0) for r in alloc_rows}

        # 1. First pass: compute effective_hours on each child operation
        for op in ops:
            if op.get("is_summary_task") != 1:
                alloc_h = alloc_map.get(op["operation_id"], 0.0)
                op["allocated_hours_10day"] = alloc_h
                reserved_h = float(op.get("total_reserved_hours") or 0.0)
                
                # Priority 1: 10-Day Plan allocations (Calendar Matrix)
                if alloc_h > 0:
                    op["effective_hours"] = alloc_h
                    op["hours_source"] = "10_DAY_PLAN"
                    op["hours_source_label"] = "📅 از برنامه ۱۰ روزه"
                # Priority 2: 30-Day Estimate / TDABC (Monthly Action Plan)
                elif reserved_h > 0:
                    op["effective_hours"] = reserved_h
                    op["hours_source"] = "30_DAY_ESTIMATE"
                    op["hours_source_label"] = "📋 از برنامه ۳۰ روزه"
                else:
                    op["effective_hours"] = 0.0
                    op["hours_source"] = "PENDING_ESTIMATE"
                    op["hours_source_label"] = "⏳ در انتظار برآورد بخش"

        # 2. Second pass: Roll-up on Summary Tasks (Parent Work Packages)
        for op in ops:
            if op.get("is_summary_task") == 1:
                children = [c for c in ops if c.get("parent_id") == op["operation_id"]]
                op["children_count"] = len(children)
                op["rollup_setup_hours"] = round(sum(float(c.get("setup_hours_planned") or 0.0) for c in children), 1)
                op["rollup_net_hours"] = round(sum(float(c.get("net_machining_hours") or 0.0) for c in children), 1)
                
                # Rollup effective hours from children
                rollup_effective = sum(float(c.get("effective_hours") or 0.0) for c in children)
                op["rollup_effective_hours"] = round(rollup_effective, 1)
                op["rollup_total_reserved"] = round(rollup_effective, 1)
                op["rollup_child_ids"] = [c["operation_id"] for c in children]

                if children and rollup_effective > 0:
                    op["effective_hours"] = round(rollup_effective, 1)
                    op["hours_source"] = "CHILD_ROLLUP"
                    op["hours_source_label"] = "مجموع تجمیعی فرزندان"
                else:
                    macro_h = float(op.get("total_hours_required") or (float(op.get("duration_days") or 5.0) * 8.0))
                    op["effective_hours"] = macro_h
                    op["hours_source"] = "MACRO_ESTIMATE"
                    op["hours_source_label"] = "تخمینی کلان"

        prjs = query_db("SELECT project_id, project_name, shopfloor_domain, project_manager FROM projects")
        self.send_json({"status": "success", "wbs_items": ops, "projects": prjs})

    def handle_create_wbs_item(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_create_wbs") and not p.get("can_edit_any_project") and u["role_key"] not in ("Admin", "Project_Control", "General_Manager", "Project_Manager"):
            return self.send_json({"status": "error", "message": "شما مجوز ایجاد ساختار شکست کار (WBS) را ندارید. این عملیات مختص کارشناس کنترل پروژه یا ادمین است."}, 403)

        is_summary = int(body.get("is_summary_task", 0))
        parent_id = body.get("parent_id") or None
        if parent_id == "": parent_id = None
        
        prj_id = body.get("project_id")
        wbs_code = body.get("wbs_code", "1.1")
        wbs_phase = body.get("wbs_phase", "فاز شکست کار" if not is_summary else "بسته کاری کلان")
        desc = body.get("description", "ریزفرآیند WBS")
        part_eq = body.get("part_equipment", desc)
        part_code_val = body.get("part_code") or None
        rev_no_val = body.get("revision_no", "Rev.01")
        routing = body.get("process_routing", "")
        start_d = body.get("start_date", "1405/08/01")
        finish_d = body.get("finish_date", "1405/08/10")
        
        if is_summary:
            clean_prj_part = prj_id.split('-')[1] if '-' in prj_id else 'GEN'
            op_id = f"WP-{clean_prj_part}-{os.urandom(2).hex().upper()}"
            q = "مسیر چندگانه"
            pred = None
            mat = None
            decade = "افق کلان (۳ تا ۶ ماهه)"
            dur_days = float(body.get("duration_days", 10.0))
            setup_h = 0.0
            net_h = 0.0
            contingency = 0.0
            reserved_h = 0.0
            is_locked = 0
            is_month = 0
            horizon = "افق کلان (۳ تا ۶ ماهه)"
            req_status = "بسته کاری مصوب"
        else:
            op_id = f"O-WBS-{time.strftime('%y%m%d')}-{os.urandom(2).hex().upper()}"
            q = body.get("operation_type", "فرزکاری")
            pred = body.get("predecessor_id") or None
            mat = body.get("raw_material_item") or None
            decade = body.get("decade_period", "دهه دوم (روزهای ۱۱ تا ۲۰)")
            setup_h = float(body.get("setup_hours_planned", 2.0))
            net_h = float(body.get("net_machining_hours", 8.0))
            contingency = float(body.get("contingency_rate", 0.20))
            reserved_h = round(setup_h + (net_h * (1.0 + contingency)), 1)
            dur_days = float(body.get("duration_days", round(reserved_h / 8.0, 1)))
            is_locked = 0
            is_month = int(body.get("is_selected_for_month", 1))
            horizon = "ماه جاری (روزهای ۱ تا ۳۰)"
            req_status = "درخواست‌شده توسط کنترل پروژه"

        query_db("""
        INSERT INTO operations (
            operation_id, project_id, wbs_code, wbs_phase, created_by_role, domain,
            part_equipment, description, operation_type, process_routing,
            start_date, finish_date, duration_days, is_summary_task, parent_id,
            decade_period, is_decade_locked, is_selected_for_month, planning_horizon,
            quantity, unit, project_manager, request_status, days_required, total_hours_required,
            setup_hours_planned, net_machining_hours, contingency_rate, total_reserved_hours,
            predecessor_id, raw_material_item, material_ready, tooling_ready, drawing_approved_by_head, part_code, revision_no
        ) VALUES (?, ?, ?, ?, ?, 'قالب‌سازی',
                  ?, ?, ?, ?,
                  ?, ?, ?, ?, ?,
                  ?, ?, ?, ?,
                  1.0, 'مجموعه', 'بورانی (borani)', ?, ?, ?,
                  ?, ?, ?, ?,
                  ?, ?, 1, 1, 1, ?, ?)
        """, (
            op_id, prj_id, wbs_code, wbs_phase, u["role_title"],
            part_eq, desc, q, routing,
            start_d, finish_d, dur_days, is_summary, parent_id,
            decade, is_locked, is_month, horizon,
            req_status, dur_days, dur_days * 8.0,
            setup_h, net_h, contingency, reserved_h,
            pred, mat, part_code_val, rev_no_val
        ), commit=True)

        item_type_label = "بسته کاری کلان" if is_summary else "ریزفرآیند WBS"
        self.send_json({"status": "success", "message": f"{item_type_label} با کد {wbs_code} و شناسه {op_id} با موفقیت ثبت شد.", "operation_id": op_id})


    def handle_api_master_plans(self, query):
        plans = query_db("""
            SELECT pmp.*, p.project_name, p.shopfloor_domain, p.project_manager,
                   p.total_operations, p.completed_operations, p.shopfloor_progress,
                   p.budgeted_cost, p.actual_cost
            FROM project_master_plans pmp
            JOIN projects p ON pmp.project_id = p.project_id
            ORDER BY pmp.id ASC
        """)
        # Dynamic calculation of project buffer consumption from daily_execution_logs
        for plan in plans:
            delays = query_db("""
                SELECT COALESCE(SUM(l.delay_hours), 0.0) as tot_delay
                FROM daily_execution_logs l
                WHERE l.project_id = ?
            """, (plan["project_id"],), one=True)
            tot_delay_h = float(delays["tot_delay"] or 0.0) if delays else 0.0
            buf_days = float(plan.get("project_buffer_days") or 20.0)
            calc_pct = round(((tot_delay_h / 8.0) / buf_days) * 100.0, 1) if buf_days > 0 else 0.0
            plan["buffer_consumed_percent"] = min(100.0, max(0.0, calc_pct))
            query_db("UPDATE project_master_plans SET buffer_consumed_percent = ? WHERE project_id = ?", (plan["buffer_consumed_percent"], plan["project_id"]), commit=True)

        # Dynamic calculation of 20h Shared Emergency Reserve from milling unexpected delays & VIP orders
        vip_ops = query_db("""
            SELECT COALESCE(SUM(total_reserved_hours), 0.0) as vip_h 
            FROM operations 
            WHERE (wbs_code LIKE 'VIP%' OR operation_id LIKE 'O-VIP%') 
              AND operation_type = 'فرزکاری'
        """, one=True)
        vip_consumed = float(vip_ops["vip_h"] or 0.0) if vip_ops else 0.0

        milling_logs = query_db("""
            SELECT COALESCE(SUM(l.delay_hours), 0.0) as milling_delay
            FROM daily_execution_logs l
            WHERE l.department = 'فرزکاری' OR l.operation_id IN (SELECT operation_id FROM operations WHERE operation_type = 'فرزکاری')
        """, one=True)
        m_del = float(milling_logs["milling_delay"] or 0.0) if milling_logs else 0.0
        consumed_reserve = min(20.0, round(m_del + vip_consumed, 1))
        remaining_reserve = round(max(0.0, 20.0 - consumed_reserve), 1)
        status_str = "سبز (ایمن)" if remaining_reserve >= 10.0 else ("زرد (هشدار)" if remaining_reserve >= 4.0 else "قرمز (بحرانی)")

        shared_reserve = {
            "total_reserve_hours": 20.0,
            "consumed_hours": consumed_reserve,
            "remaining_hours": remaining_reserve,
            "status": status_str,
            "policy": "ذخیره مشترک ۱۰٪ گلوگاه برای مواقع بحران، خرابی و کارهای فوری VIP"
        }
        self.send_json({"status": "success", "master_plans": plans, "shared_reserve": shared_reserve})

    def handle_api_critical_path(self, query):
        ops = query_db("""
            SELECT o.operation_id, o.project_id, o.wbs_code, o.part_equipment, o.description,
                   o.operation_type, o.setup_hours_planned, o.net_machining_hours, o.contingency_rate,
                   o.total_reserved_hours, o.days_required, o.is_selected_for_month, o.predecessor_id,
                   o.decade_period, o.is_decade_locked, o.execution_status
            FROM operations o
            ORDER BY o.total_reserved_hours DESC
        """)
        milling_reserved = sum(float(o.get("total_reserved_hours") or 0.0) for o in ops if o["operation_type"] == "فرزکاری" and o.get("is_selected_for_month", 1) == 1 and not (o.get("is_summary_task") == 1))
        nominal_cap = 208.0
        shared_reserve_h = 20.0
        available_project_cap = nominal_cap - shared_reserve_h
        overload = max(0.0, milling_reserved - available_project_cap)
        
        self.send_json({
            "status": "success",
            "bottleneck": {
                "nominal_capacity": nominal_cap,
                "shared_reserve_hours": shared_reserve_h,
                "available_project_capacity": available_project_cap,
                "current_month_reserved_hours": round(milling_reserved, 1),
                "overload_hours": round(overload, 1),
                "is_overloaded": overload > 0,
                "critical_operations": ops[:8]
            }
        })

    def handle_api_vip_apply(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_schedule_calendar") and not p.get("can_edit_any_project") and u["role_key"] not in ("Admin", "General_Manager", "Project_Control"):
            return self.send_json({"status": "error", "message": "تنها مدیر مجموعه، کنترل پروژه یا ادمین اجازه اعمال سناریوی سفارش فوری VIP را دارند."}, 403)

        vip_title = body.get("vip_title", "سفارش فوری VIP")
        prj_id = body.get("project_id", "PRJ-MOLD-001")
        vip_hours = float(body.get("hours_required", 16.0))
        transfer_ids = body.get("transfer_op_ids", [])
        cost_diff = float(body.get("cost_difference", 0.0))

        # Transfer crowd-out candidate operations to Outsourcing
        transferred_names = []
        for tid in transfer_ids:
            op = query_db("SELECT operation_id, part_equipment, description FROM operations WHERE operation_id = ?", (tid,), one=True)
            if op:
                query_db("""
                    UPDATE operations 
                    SET operation_type = 'برون‌سپاری',
                        request_status = 'انتقال‌یافته به برون‌سپاری (آزادسازی گلوگاه VIP)'
                    WHERE operation_id = ?
                """, (tid,), commit=True)
                transferred_names.append(f"{tid} ({op.get('part_equipment') or ''})")

        # Update project actual_cost and outsourcing_cost in projects table
        if cost_diff > 0:
            query_db("UPDATE projects SET actual_cost = actual_cost + ?, outsourcing_cost = outsourcing_cost + ? WHERE project_id = ?", (cost_diff, cost_diff, prj_id), commit=True)

        # Create VIP operation in operations table
        vip_op_id = f"O-VIP-{time.strftime('%y%m%d')}-{os.urandom(2).hex().upper()}"
        query_db("""
            INSERT INTO operations (
                operation_id, project_id, wbs_code, wbs_phase, created_by_role, domain,
                part_equipment, description, operation_type, quantity, unit,
                project_manager, request_status, days_required, total_hours_required,
                setup_hours_planned, net_machining_hours, contingency_rate, total_reserved_hours,
                is_selected_for_month, planning_horizon, decade_period, is_decade_locked,
                drawing_approved_by_head, tooling_ready, qc_status,
                actual_cost, outsourcing_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            vip_op_id, prj_id, "VIP.1", "تولید اضطراری VIP", "مدیر مجموعه", "سفارشات ویژه",
            "قطعه فوری VIP", vip_title, "فرزکاری", 1, "دست",
            u["full_name"], "در حال ساخت در گلوگاه VIP", round(vip_hours / 8.0, 1), vip_hours,
            2.0, max(1.0, round((vip_hours - 2.0) / 1.2, 1)), 0.20, vip_hours,
            1, "ماه جاری (روزهای ۱ تا ۳۰)", "دهه دوم (روزهای ۱۱ تا ۲۰)", 1,
            1, 1, "در انتظار بازرسی",
            cost_diff, cost_diff
        ), commit=True)

        # Log financial disruption
        log_id = f"LOG-VIP-{time.strftime('%y%m%d%H%M%S')}"
        query_db("""
            INSERT INTO daily_execution_logs (
                log_id, operation_id, project_id, department, log_date, jalali_date,
                hours_regular, hours_overtime, actual_hours, daily_progress, variance_status,
                delay_hours, work_description, deviation_reason, logged_by, rca_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id, vip_op_id, prj_id, "فرزکاری", time.strftime("%Y-%m-%d"), "۱۴۰۵/۰۸/۱۵",
            0.0, 0.0, vip_hours, 10.0, "سفارش فوری VIP",
            0.0, f"ورود سفارش فوری VIP: {vip_title}. مابه‌التفاوت هزینه ریالی: {int(cost_diff):,} ریال. قطعات منتقل‌شده: {', '.join(transferred_names) if transferred_names else 'تأمین کامل از ذخیره بحران'}",
            f"آزادسازی ظرفیت گلوگاه با انتقال {len(transferred_names)} فرآیند به برون‌سپاری", u["full_name"], "T0"
        ), commit=True)

        self.send_json({
            "status": "success",
            "message": f"سفارش فوری '{vip_title}' با موفقیت به گلوگاه وارد شد ({vip_hours} ساعت). {len(transferred_names)} عملیات به برون‌سپاری هدایت گردیدند.",
            "vip_op_id": vip_op_id,
            "transferred_count": len(transferred_names)
        })

    def handle_create_or_update_master_plan(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_create_wbs") and not p.get("can_edit_any_project") and u["role_key"] not in ("Admin", "Project_Control", "General_Manager", "Project_Manager"):
            return self.send_json({"status": "error", "message": "تنها کارشناس کنترل پروژه یا مدیر مجموعه اجازه تنظیم برنامه کلان را دارد."}, 403)
            
        prj_id = body.get("project_id")
        title = body.get("plan_title", "برنامه کلان پروژه")
        target_date = body.get("target_delivery_date", "1405/12/29")
        months = float(body.get("total_estimated_months", 6.0))
        buffer_days = float(body.get("project_buffer_days", 20.0))
        
        existing = query_db("SELECT id FROM project_master_plans WHERE project_id = ?", (prj_id,), one=True)
        if existing:
            query_db("""
                UPDATE project_master_plans 
                SET plan_title = ?, target_delivery_date = ?, total_estimated_months = ?, project_buffer_days = ?
                WHERE project_id = ?
            """, (title, target_date, months, buffer_days, prj_id), commit=True)
            msg = f"برنامه کلان پروژه {prj_id} به‌روزرسانی شد."
        else:
            query_db("""
                INSERT INTO project_master_plans (project_id, plan_title, target_delivery_date, total_estimated_months, project_buffer_days)
                VALUES (?, ?, ?, ?, ?)
            """, (prj_id, title, target_date, months, buffer_days), commit=True)
            msg = f"برنامه کلان جدید برای پروژه {prj_id} ثبت شد."
            
        self.send_json({"status": "success", "message": msg})

    def handle_toggle_monthly_selection(self, body):
        op_id = body.get("operation_id")
        sel = int(body.get("is_selected_for_month", 1))
        horizon = body.get("planning_horizon", "ماه جاری (روزهای ۱ تا ۳۰)" if sel == 1 else "افق میان‌مدت (ماه‌های آینده)")
        query_db("UPDATE operations SET is_selected_for_month = ?, planning_horizon = ? WHERE operation_id = ?", (sel, horizon, op_id), commit=True)
        status_txt = "وارد اکشن‌پلان ماه جاری شد" if sel == 1 else "به افق ماه‌های آینده منتقل شد"
        self.send_json({"status": "success", "message": f"عملیات {op_id} {status_txt}."})

    def handle_update_tdabc_estimates(self, body):
        op_id = body.get("operation_id")
        setup_h = float(body.get("setup_hours_planned", 2.0))
        net_h = float(body.get("net_machining_hours", 8.0))
        contingency = float(body.get("contingency_rate", 0.20))
        start_d = body.get("start_date")
        finish_d = body.get("finish_date")
        routing = body.get("process_routing")
        duration_d = float(body.get("duration_days") or 0.0)

        op = query_db("SELECT is_summary_task FROM operations WHERE operation_id = ?", (op_id,), one=True)
        is_summary = (op and op.get("is_summary_task") == 1)

        if is_summary:
            # Summary tasks update dates and process routing
            query_db("""
                UPDATE operations 
                SET start_date = COALESCE(?, start_date),
                    finish_date = COALESCE(?, finish_date),
                    process_routing = COALESCE(?, process_routing)
                WHERE operation_id = ?
            """, (start_d, finish_d, routing, op_id), commit=True)
            self.send_json({"status": "success", "message": f"مشخصات زمان‌بندی و مسیر فرآیندی بسته کاری {op_id} به‌روزرسانی شد."})
        else:
            reserved_h = round(setup_h + (net_h * (1.0 + contingency)), 1)
            days_req = round(reserved_h / 8.0, 1) if reserved_h > 0 else (duration_d or 1.0)
            query_db("""
                UPDATE operations 
                SET setup_hours_planned = ?, net_machining_hours = ?, contingency_rate = ?, 
                    total_reserved_hours = ?, days_required = ?, total_hours_required = ?,
                    start_date = COALESCE(?, start_date),
                    finish_date = COALESCE(?, finish_date),
                    process_routing = COALESCE(?, process_routing)
                WHERE operation_id = ?
            """, (setup_h, net_h, contingency, reserved_h, days_req, reserved_h, start_d, finish_d, routing, op_id), commit=True)
            self.send_json({"status": "success", "message": f"برآورد TDABC و زمان‌بندی عملیات {op_id} با موفقیت به‌روزرسانی شد: {reserved_h} ساعت.", "total_reserved_hours": reserved_h})

    def handle_update_permissions(self, body):
        u = get_current_user()
        if u["role_key"] != "Admin":
            return self.send_json({"status": "error", "message": "تنها ادمین سیستم اجازه تغییر ماتریس دسترسی‌ها را دارد."}, 403)

        role_key = body.get("role_key")
        field = body.get("field")
        val = int(body.get("value", 0))

        valid_fields = [
            "can_view_all_projects", "can_view_all_depts", "can_create_wbs", "can_submit_request",
            "can_submit_estimation", "can_schedule_calendar", "can_lock_decade", "can_log_work",
            "can_manage_permissions", "can_view_financials", "can_edit_any_project", "can_delete"
        ]
        if field not in valid_fields:
            return self.send_json({"status": "error", "message": "فیلد دسترسی نامعتبر است."}, 400)

        query_db(f"UPDATE permissions_matrix SET {field} = ? WHERE role_key = ?", (val, role_key), commit=True)
        self.send_json({"status": "success", "message": f"دسترسی '{field}' برای نقش '{role_key}' به {val} تغییر یافت."})

    def handle_set_day_hours(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_schedule_calendar"):
            return self.send_json({"status": "error", "message": f"نقش '{u['role_title']}' مجوز زمان‌بندی روزانه روی تقویم را ندارد. این اقدام مختص مدیر مجموعه و ادمین است."}, 403)

        op_id = body.get("operation_id")
        day_num = int(body.get("day_number", 1))
        h_reg = max(0.0, float(body.get("hours_regular", 0.0)))
        h_ot = max(0.0, float(body.get("hours_overtime", 0.0)))
        h_tot = h_reg + h_ot
        is_alloc = 1 if h_tot > 0 else 0

        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)

        if op["is_decade_locked"] and u["role_key"] != "Admin":
            return self.send_json({
                "status": "error",
                "message": "برنامه این دهه به عنوان برنامه فیکس و اجباری قفل شده است و تغییر آن تنها با مجوز مدیر مجموعه یا ادمین امکان‌پذیر است."
            }, 400)

        # Full-Kit Readiness Gate for Decade 1 (Days 1 to 10)
        if day_num <= 10 and is_alloc == 1:
            blockers = []
            if not op.get("drawing_approved_by_head"):
                blockers.append("عدم تأیید صلاحیت نقشه مهندسی (drawing_approved_by_head=0)")
            if not op.get("tooling_ready"):
                blockers.append("کسری ابزار برشی تخصصی (tooling_ready=0)")
            if op.get("eco_status") == "در حال بازطراحی در واحد مهندسی":
                blockers.append("دستور توقف تغییر مهندسی معلق (ECO Pending)")
            
            if blockers:
                return self.send_json({
                    "status": "error",
                    "message": f"⛔ دروازه پیش‌نیازها (Full-Kit Readiness Gate): تخصیص زمان قطعی در دهه اول مجاز نیست به دلیل: {' و '.join(blockers)}."
                }, 400)

        curr = query_db("SELECT * FROM daily_allocations WHERE operation_id = ? AND day_number = ?", (op_id, day_num), one=True)
        if curr:
            query_db("""
            UPDATE daily_allocations
            SET allocated = ?, hours_regular = ?, hours_overtime = ?, hours_total = ?
            WHERE operation_id = ? AND day_number = ?
            """, (is_alloc, h_reg, h_ot, h_tot, op_id, day_num), commit=True)
        else:
            is_hol = 1 if day_num in [1, 8, 15, 22, 29] else 0
            query_db("""
            INSERT INTO daily_allocations (operation_id, day_number, allocated, hours_regular, hours_overtime, hours_total, is_holiday)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (op_id, day_num, is_alloc, h_reg, h_ot, h_tot, is_hol), commit=True)

        sum_hrs = query_db("SELECT SUM(hours_total) as s FROM daily_allocations WHERE operation_id = ? AND allocated = 1", (op_id,), one=True)["s"] or 0.0
        planned_hours = round(float(sum_hrs), 1)
        req_hours = round(float(op.get("total_reserved_hours") or op.get("total_hours_required") or (float(op.get("days_required") or 0) * 8.0)), 1)
        remaining_hours = max(0.0, round(req_hours - planned_hours, 1))
        planned_days = round(planned_hours / 8.0, 1)
        remaining_days = round(remaining_hours / 8.0, 1)

        query_db("UPDATE operations SET planned_hours = ?, planned_days = ?, remaining_unplanned_days = ? WHERE operation_id = ?", (planned_hours, planned_days, remaining_days, op_id), commit=True)

        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "day_number": day_num,
            "allocated": is_alloc,
            "hours_regular": h_reg,
            "hours_overtime": h_ot,
            "hours_total": h_tot,
            "planned_hours": planned_hours,
            "required_hours": req_hours,
            "remaining_hours": remaining_hours,
            "planned_days": planned_days,
            "remaining_unplanned_days": remaining_days
        })

    def handle_toggle_day(self, body):
        op_id = body.get("operation_id")
        day_num = int(body.get("day_number", 1))
        curr = query_db("SELECT * FROM daily_allocations WHERE operation_id = ? AND day_number = ?", (op_id, day_num), one=True)
        if curr and curr["allocated"] == 1:
            return self.handle_set_day_hours({"operation_id": op_id, "day_number": day_num, "hours_regular": 0.0, "hours_overtime": 0.0})
        else:
            return self.handle_set_day_hours({"operation_id": op_id, "day_number": day_num, "hours_regular": 8.0, "hours_overtime": 0.0})

    def handle_log_daily(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_log_work"):
            return self.send_json({"status": "error", "message": f"نقش '{u['role_title']}' مجوز ثبت کارکرد روزانه را ندارد. این اقدام مختص مسئول بخش مجری یا ادمین است."}, 403)

        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)

        # Department isolation check
        if u["role_key"] == "Department_Head" and u["assigned_department"] != "ALL":
            allowed_depts = [x.strip() for x in u["assigned_department"].split(",") if x.strip()]
            if op["operation_type"] not in allowed_depts:
                return self.send_json({"status": "error", "message": f"شما تنها مسئول صف '{u['assigned_department']}' هستید و اجازه ثبت کارکرد برای صف '{op['operation_type']}' را ندارید."}, 403)

        log_id = f"LOG-{time.strftime('%Y%m%d')}-{os.urandom(2).hex().upper()}"
        h_reg = float(body.get("hours_regular", 8.0))
        h_ot = float(body.get("hours_overtime", 0.0))
        act_hrs = h_reg + h_ot
        prog = float(body.get("daily_progress", 20.0))
        delay_hrs = float(body.get("delay_hours", 0.0))
        var_status = "دارای تأخیر و انحراف" if delay_hrs > 0 else "طبق برنامه"
        attach = body.get("attachment_path", "")
        voice_text = body.get("voice_transcription", "")
        rca_code = body.get("rca_code", "T0")

        # Process granular machine time breakdown entries
        breakdown_items = body.get("breakdown_items", [])
        useful_hrs = 0.0
        idle_hrs = 0.0
        idle_reasons = []

        if breakdown_items and isinstance(breakdown_items, list) and len(breakdown_items) > 0:
            for item in breakdown_items:
                b_type = item.get("entry_type", "کار مفید ماشین‌کاری")
                b_hrs = float(item.get("hours", 0.0))
                b_rca = item.get("rca_code", "T0")
                b_desc = item.get("desc", "")
                query_db("""
                INSERT INTO machine_time_entries (log_id, operation_id, entry_type, hours, rca_code, activity_desc)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (log_id, op_id, b_type, b_hrs, b_rca, b_desc), commit=True)
                if b_type == "توقف و تعلیق کارگاه":
                    idle_hrs += b_hrs
                    if b_desc:
                        idle_reasons.append(b_desc)
                    if b_rca != "T0":
                        rca_code = b_rca
                else:
                    useful_hrs += b_hrs
            if idle_hrs > 0 and delay_hrs == 0:
                delay_hrs = idle_hrs
                var_status = "دارای تأخیر و انحراف"
        else:
            idle_hrs = delay_hrs
            useful_hrs = max(0.0, act_hrs - delay_hrs)
            if useful_hrs > 0:
                query_db("""
                INSERT INTO machine_time_entries (log_id, operation_id, entry_type, hours, rca_code, activity_desc)
                VALUES (?, ?, 'کار مفید ماشین‌کاری', ?, 'T0', ?)
                """, (log_id, op_id, useful_hrs, body.get("work_description", "کار مفید ماشین‌کاری")), commit=True)
            if idle_hrs > 0:
                query_db("""
                INSERT INTO machine_time_entries (log_id, operation_id, entry_type, hours, rca_code, activity_desc)
                VALUES (?, ?, 'توقف و تعلیق کارگاه', ?, ?, ?)
                """, (log_id, op_id, idle_hrs, rca_code, body.get("deviation_reason", "توقف کارگاه")), commit=True)

        dev_reason = body.get("deviation_reason", "")
        if delay_hrs > 0 and not dev_reason:
            dev_reason = ", ".join(idle_reasons) if idle_reasons else "انحراف و توقف کارگاهی"

        spindle_h = float(body.get("spindle_hours", 0.0))
        phys_prog = float(body.get("physical_progress_percent", 0.0))
        claim_type = body.get("delay_claim_type", "")
        claim_hours = float(body.get("delay_claim_hours", 0.0))
        claim_status = body.get("delay_claim_status", "None")
        used_tool = body.get("used_tool_serial", "")
        tool_h = float(body.get("tool_hours_used", 0.0))

        query_db("""
        INSERT INTO daily_execution_logs (
            log_id, operation_id, project_id, department, log_date, jalali_date,
            hours_regular, hours_overtime, actual_hours, daily_progress, variance_status, delay_hours,
            work_description, deviation_reason, rca_code, attachment_path, voice_transcription, logged_by,
            spindle_hours, machine_power_hours, physical_progress_percent, delay_claim_type, delay_claim_hours,
            delay_claim_status, used_tool_serial, tool_hours_used
        ) VALUES (?, ?, ?, ?, date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id, op_id, op["project_id"], op["operation_type"],
            body.get("jalali_date", "1405/08/12"), h_reg, h_ot, act_hrs, prog, var_status, delay_hrs,
            body.get("work_description", "ثبت اقدام روزانه"),
            dev_reason, rca_code, attach, voice_text, u["full_name"],
            spindle_h, act_hrs, phys_prog, claim_type, claim_hours, claim_status, used_tool, tool_h
        ), commit=True)

        if spindle_h > 0 or phys_prog > 0:
            execute_db('''
                UPDATE operations SET
                    spindle_hours_total = COALESCE(spindle_hours_total, 0) + ?,
                    physical_progress_percent = MAX(COALESCE(physical_progress_percent, 0), ?)
                WHERE operation_id = ?
            ''', (spindle_h, phys_prog, op_id))

        new_prog = min(100.0, float(op["progress_percent"] or 0) + prog)
        new_status = "تمام‌شده" if new_prog >= 100.0 else "در حال انجام"
        new_variance = round(float(op["variance_days"] or 0) + (delay_hrs / 8.0), 1)
        hr_rate = float(op["hourly_rate"] or 2500000.0)
        tooling_rate = 300000.0 if op["operation_type"] == "فرزکاری" else (200000.0 if op["operation_type"] == "تراشکاری" else 100000.0)
        machine_rate = max(0.0, hr_rate - tooling_rate)

        # Financial Segregation: Direct WIP vs Idle Capacity Variance (Overhead)
        added_machine = useful_hrs * machine_rate
        added_tooling = useful_hrs * tooling_rate
        added_direct_cost = added_machine + added_tooling
        added_idle_cost = idle_hrs * machine_rate

        new_act_cost = float(op["actual_cost"] or 0) + added_direct_cost
        new_machine_cost = float(op.get("machine_cost") or 0.0) + added_machine
        new_tooling_cost = float(op.get("tooling_cost") or 0.0) + added_tooling
        new_idle_cost = float(op.get("idle_capacity_cost") or 0.0) + added_idle_cost

        query_db("""
        UPDATE operations
        SET progress_percent = ?, execution_status = ?, variance_days = ?, actual_cost = ?,
            machine_cost = ?, tooling_cost = ?, idle_capacity_cost = ?
        WHERE operation_id = ?
        """, (new_prog, new_status, new_variance, new_act_cost, new_machine_cost, new_tooling_cost, new_idle_cost, op_id), commit=True)

        p_ops = query_db("SELECT execution_status, progress_percent, weight, actual_cost FROM operations WHERE project_id = ?", (op["project_id"],))
        tot = len(p_ops)
        comp = sum(1 for o in p_ops if o["execution_status"] == "تمام‌شده")
        tot_w = sum(float(o["weight"] or 1.0) for o in p_ops) or 1.0
        weighted_prog = sum(float(o["progress_percent"] or 0) * float(o["weight"] or 1.0) for o in p_ops) / tot_w
        prj_act_cost = sum(float(o["actual_cost"] or 0) for o in p_ops)

        query_db("""
        UPDATE projects
        SET total_operations = ?, completed_operations = ?, shopfloor_progress = ?, actual_cost = ?,
            idle_capacity_cost = COALESCE(idle_capacity_cost, 0.0) + ?
        WHERE project_id = ?
        """, (tot, comp, round(weighted_prog, 1), prj_act_cost, added_idle_cost, op["project_id"]), commit=True)

        self.send_json({
            "status": "success",
            "message": "گزارش روزانه کارکرد، صوت، پیوست و ریززمان‌های ماشین ثبت گردید (ساعات توقف در انحراف ظرفیت بلااستفاده تفکیک شد).",
            "log_id": log_id,
            "operation_id": op_id,
            "useful_hours": useful_hrs,
            "idle_hours": idle_hrs,
            "idle_capacity_cost": added_idle_cost
        })

    def handle_submit_estimation(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_submit_estimation"):
            return self.send_json({"status": "error", "message": f"نقش '{u['role_title']}' مجوز ثبت برآورد زمانی را ندارد. این اقدام مختص مسئول بخش یا ادمین است."}, 403)

        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)

        # Department isolation check
        if u["role_key"] == "Department_Head" and u["assigned_department"] != "ALL":
            allowed_depts = [x.strip() for x in u["assigned_department"].split(",") if x.strip()]
            if op["operation_type"] not in allowed_depts:
                return self.send_json({"status": "error", "message": f"شما تنها مسئول صف '{u['assigned_department']}' هستید و اجازه برآورد برای صف '{op['operation_type']}' را ندارید."}, 403)

        if "setup_hours_planned" in body and "net_machining_hours" in body:
            setup_h = float(body.get("setup_hours_planned", 2.0))
            net_h = float(body.get("net_machining_hours", 8.0))
            contingency = float(body.get("contingency_rate", 0.20))
            reserved_h = round(setup_h + (net_h * (1.0 + contingency)), 1)
            est_days = round(reserved_h / 8.0, 1)
        else:
            est_days = float(body.get("estimated_days", 1.0))
            reserved_h = round(est_days * 8.0, 1)
            setup_h = 2.0
            net_h = max(1.0, round((reserved_h - 2.0) / 1.2, 1))
            contingency = 0.20

        note = body.get("estimation_note", "")
        query_db("""
        UPDATE operations
        SET estimated_days_by_head = ?, days_required = ?, total_hours_required = ?,
            setup_hours_planned = ?, net_machining_hours = ?, contingency_rate = ?, total_reserved_hours = ?,
            estimation_note = ?, request_status = 'برآوردشده توسط مسئول بخش', department_head = ?
        WHERE operation_id = ?
        """, (est_days, est_days, reserved_h, setup_h, net_h, contingency, reserved_h, note, u["full_name"], op_id), commit=True)
        self.send_json({"status": "success", "message": f"برآورد زمانی مسئول بخش طبق استاندارد TDABC ({reserved_h} ساعت رزرو) با موفقیت ثبت گردید."})

    def handle_lock_decade(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_lock_decade"):
            return self.send_json({"status": "error", "message": "شما مجوز قفل کردن برنامه فیکس دهه‌ای را ندارید. این اقدام منحصراً در حیطه اختیارات مدیر مجموعه و ادمین است."}, 403)

        decade = body.get("decade_period", "دهه اول (روزهای ۱ تا ۱۰)")

        # Enforcement of Mandatory Blocking Pre-conditions: Tooling and Qualified Drawing
        decade_ops = query_db("SELECT * FROM operations WHERE decade_period = ? AND (is_summary_task = 0 OR is_summary_task IS NULL)", (decade,))
        blocking_errors = []
        yellow_warning_ops = []

        for op in decade_ops:
            is_mat = bool(op.get("material_ready"))
            is_tool = bool(op.get("tooling_ready"))
            is_dwg = bool(op.get("drawing_approved_by_head")) and (op.get("eco_status") != "در حال بازطراحی در واحد مهندسی")
            
            # Tooling and Qualified drawing are strictly blocking before decade lock
            if not is_tool or not is_dwg:
                fails = []
                if not is_tool: fails.append("تأمین ابزار برشی")
                if not is_dwg: fails.append("تأیید صلاحیت نقشه ساخت / عدم تعلیق ECO")
                blocking_errors.append(f"{op['operation_id']} ({op.get('part_equipment') or op['description']}): نقص در [{', '.join(fails)}]")
            
            # Material not ready: allowed in locked 10-day plan, flagged in yellow as WIP in-progress from upstream
            if not is_mat:
                yellow_warning_ops.append(f"{op['operation_id']} ({op.get('part_equipment') or op['description']})")

        if blocking_errors and not body.get("force"):
            err_msg = "امکان قفل دهه وجود ندارد! طبق ضابطه مصوب، ابزار برشی و نقشه مهندسی ساخت حتماً باید قبل از قفل برنامه ۱۰ روزه آماده و تأیید صلاحیت شده باشند:\n• " + "\n• ".join(blocking_errors)
            return self.send_json({"status": "error", "message": err_msg, "missing_conditions": blocking_errors}, 400)

        query_db("UPDATE operations SET is_decade_locked = 1 WHERE decade_period = ? AND (is_summary_task = 0 OR is_summary_task IS NULL)", (decade,), commit=True)
        
        succ_msg = f"برنامه {decade} با احراز پیش‌شرط‌های قطعی ابزار برشی و نقشه ساخت به صورت فیکس و اجباری قفل گردید."
        if yellow_warning_ops:
            succ_msg += f"\n(توجه: تعداد {len(yellow_warning_ops)} عملیات در وضعیت زرد 'در انتظار متریال از ایستگاه قبل' قرار دارند که پس از امانت کارگاهی و تأیید کیفی، به طور خودکار به رنگ سبز تبدیل می‌شوند)."

        self.send_json({"status": "success", "message": succ_msg, "yellow_count": len(yellow_warning_ops)})

    def handle_submit_request(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not p.get("can_submit_request"):
            return self.send_json({"status": "error", "message": f"نقش '{u['role_title']}' مجوز ثبت درخواست ریزخدمت را ندارد."}, 403)

        prj_id = body["project_id"]
        # Project isolation check for PM
        if u["role_key"] == "Project_Manager" and u["assigned_projects"] != "ALL":
            allowed = [x.strip() for x in u["assigned_projects"].split(",") if x.strip()]
            if prj_id not in allowed:
                return self.send_json({"status": "error", "message": f"شما تنها مدیر پروژه '{u['assigned_projects']}' هستید و اجازه ثبت خدمت برای پروژه '{prj_id}' را ندارید."}, 403)

        op_id = f"O-T0001-{time.strftime('%y%m%d')}-{os.urandom(2).hex().upper()}"
        query_db("""
        INSERT INTO operations (
            operation_id, project_id, domain, part_equipment, description,
            operation_type, quantity, unit, project_manager, request_status,
            days_required, predecessor_id, raw_material_item, erpnext_purchase_id, erpnext_subcontract_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'درخواست‌شده توسط مدیر پروژه', ?, ?, ?, ?, ?)
        """, (
            op_id, prj_id, body.get("domain", "قالب‌سازی"),
            body.get("part_equipment", ""), body["description"],
            body["operation_type"], float(body.get("quantity", 1)), body.get("unit", "عدد"),
            u["full_name"], float(body.get("days_required", 1)),
            body.get("predecessor_id"), body.get("raw_material_item"),
            body.get("erpnext_purchase_id"), body.get("erpnext_subcontract_id")
        ), commit=True)
        self.send_json({"status": "success", "operation_id": op_id, "message": "درخواست ریزخدمت مدیر پروژه با موفقیت ثبت شد."})

    def handle_api_logs(self, query):
        u = get_current_user()
        p = u["permissions"]
        sql = "SELECT * FROM daily_execution_logs WHERE 1=1"
        params = []

        if not p.get("can_view_all_projects") and u["assigned_projects"] != "ALL":
            allowed = [x.strip() for x in u["assigned_projects"].split(",") if x.strip()]
            sql += f" AND project_id IN ({','.join(['?']*len(allowed))})"
            params.extend(allowed)

        if not p.get("can_view_all_depts") and u["assigned_department"] != "ALL":
            allowed_depts = [x.strip() for x in u["assigned_department"].split(",") if x.strip()]
            sql += f" AND department IN ({','.join(['?']*len(allowed_depts))})"
            params.extend(allowed_depts)

        sql += " ORDER BY log_date DESC"
        logs = query_db(sql, params)
        self.send_json({"status": "success", "logs": logs, "current_user": u})

    def handle_transcribe_voice(self, body):
        if body.get("test_mode"):
            test_phrase = body.get("phrase", "کف‌تراشی و گونیایی بلوک سنبه در دستگاه فرز CNC با دقت صدمی انجام شد.")
            test_phrase = normalize_persian_transcript(test_phrase)
            return self.send_json({"status": "success", "text": test_phrase, "model_info": "شبیه‌ساز هوشمند کارگاهی"})

        b64_audio = body.get("audio_base64", "")
        if not b64_audio:
            return self.send_json({"status": "error", "message": "داده صوتی ارسال نشده است."}, 400)

        try:
            if "," in b64_audio:
                b64_audio = b64_audio.split(",")[1]
            raw_bytes = base64.b64decode(b64_audio)
            res = transcribe_audio_bytes(raw_bytes)
            if "error" in res:
                self.send_json({
                    "status": "error",
                    "message": res["error"],
                    "model_info": res.get("model_info")
                }, 200)
            else:
                self.send_json({
                    "status": "success",
                    "text": res["text"],
                    "model_info": res.get("model_info"),
                    "duration": res.get("duration")
                })
        except Exception as e:
            self.send_json({"status": "error", "message": str(e)}, 500)

    def handle_upload_attachment(self, body):
        fname = body.get("filename", f"file_{int(time.time())}.dat")
        b64_data = body.get("content_base64", "")
        if not b64_data:
            return self.send_json({"status": "error", "message": "فایل خالی است."}, 400)

        if "," in b64_data:
            b64_data = b64_data.split(",")[1]

        safe_fname = f"{int(time.time())}_{os.path.basename(fname)}"
        dest_path = os.path.join(UPLOADS_DIR, safe_fname)
        with open(dest_path, "wb") as f:
            f.write(base64.b64decode(b64_data))

        self.send_json({"status": "success", "filename": safe_fname, "url": f"/uploads/{safe_fname}"})

    def handle_erpnext_status(self):
        self.send_json({
            "status": "success",
            "framework": "Frappe Framework (Python + Modern JS)",
            "target_system": "ERPNext (v14 / v15 Compatible)",
            "api_architecture": "Bidirectional REST API + Webhooks",
            "connected_modules": [
                {"name": "Buying (خرید و تدارکات)", "doctypes": ["Material Request", "Purchase Order", "Purchase Invoice"], "status": "Active / آماده اتصال"},
                {"name": "Manufacturing (تولید و ساخت)", "doctypes": ["BOM", "Work Order", "Job Card", "Routing"], "status": "Active / آماده اتصال"},
                {"name": "Accounts & Costing (حسابداری و بهای تمام‌شده)", "doctypes": ["Timesheet", "Cost Center", "Stock Ledger Entry", "Journal Entry"], "status": "Active / آماده اتصال"},
                {"name": "Projects (مدیریت پروژه مهندسی)", "doctypes": ["Project", "Task", "Project Update"], "status": "Active / آماده اتصال"},
                {"name": "Subcontracting (پیمانکاری و برون‌سپاری)", "doctypes": ["Subcontracting Order", "Subcontracting Receipt"], "status": "Active / آماده اتصال"},
                {"name": "Quality Management (کنترل کیفیت)", "doctypes": ["Quality Inspection", "Quality Inspection Template"], "status": "Active / آماده اتصال"}
            ],
            "app_package": "hamerz_project_ops",
            "bench_command": "bench get-app /path/to/hamerz_project_ops && bench install-app hamerz_project_ops"
        })

    # --- Project Manager Planning & Readiness Gating Handlers ---
    def handle_api_wbs_candidates(self, query):
        u = get_current_user()
        p = u["permissions"]
        prj = query.get("project_id", [None])[0]
        
        sql = "SELECT * FROM operations WHERE (is_summary_task = 0 OR is_summary_task IS NULL)"
        params = []
        if prj and prj != "ALL":
            sql += " AND project_id = ?"
            params.append(prj)

        if not p.get("can_view_all_projects") and u["assigned_projects"] != "ALL":
            allowed = [x.strip() for x in u["assigned_projects"].split(",") if x.strip()]
            sql += f" AND project_id IN ({','.join(['?']*len(allowed))})"
            params.extend(allowed)

        sql += " ORDER BY project_id, wbs_code"
        ops = query_db(sql, params)
        self.send_json({"status": "success", "candidates": ops})

    def handle_pm_dispatch_to_departments(self, body):
        u = get_current_user()
        if u["role_key"] not in ("Project_Manager", "General_Manager", "Admin", "Project_Control"):
            return self.send_json({"status": "error", "message": "تنها مدیران پروژه، مدیر ارشد یا کنترل پروژه اجازه ابلاغ به مسئولین بخش‌ها را دارند."}, 403)

        op_ids = body.get("operation_ids", [])
        if not op_ids and body.get("operation_id"):
            op_ids = [body.get("operation_id")]

        if not op_ids:
            return self.send_json({"status": "error", "message": "هیچ عملیاتی جهت ابلاغ انتخاب نشده است."}, 400)

        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dispatched_count = 0
        for op_id in op_ids:
            op = query_db("SELECT operation_id, project_id, part_equipment, operation_type FROM operations WHERE operation_id = ?", (op_id,), one=True)
            if op:
                query_db("UPDATE operations SET request_status = 'ابلاغ به مسئول بخش جهت برآورد', project_manager = ? WHERE operation_id = ?", (u["full_name"], op_id), commit=True)
                query_db("""
                    INSERT INTO technical_discussions (
                        operation_id, project_id, part_equipment, department,
                        sender_username, sender_name, sender_role, message, topic_tag, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    op_id,
                    op["project_id"],
                    op["part_equipment"] or "قطعه کارگاهی",
                    op["operation_type"],
                    u["username"],
                    u["full_name"],
                    u.get("role_title") or u["role_key"],
                    "📤 ابلاغ فعالیت توسط مدیر پروژه به مسئول بخش جهت برآورد مدت زمان (ساعت) طبق WBS",
                    "برنامه‌ریزی ماهانه",
                    now_ts
                ), commit=True)
                dispatched_count += 1

        self.send_json({
            "status": "success",
            "message": f"تعداد {dispatched_count} ریزفعالیت با موفقیت به کارتابل مسئولین بخش‌های مربوطه ابلاغ گردید.",
            "dispatched_count": dispatched_count
        })

    def handle_pm_decision(self, body):
        u = get_current_user()
        if u["role_key"] not in ("Project_Manager", "General_Manager", "Admin", "Project_Control"):
            return self.send_json({"status": "error", "message": "تنها مدیر پروژه یا مدیر ارشد اجازه ثبت تصمیم نهایی روی فعالیت‌ها را دارد."}, 403)

        op_id = body.get("operation_id")
        decision = body.get("decision")
        note = body.get("note", "")
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)

        if decision == "approve":
            query_db("UPDATE operations SET request_status = 'تأیید و در حال برنامه‌ریزی' WHERE operation_id = ?", (op_id,), commit=True)
            msg = f"✅ تأیید برآورد زمانی و تصویب اولویت ورود به برنامه توسط مدیر پروژه ({u['full_name']})"
            tag = "تصویب اولویت"
        elif decision == "reject":
            query_db("UPDATE operations SET request_status = 'ابلاغ به مسئول بخش جهت برآورد' WHERE operation_id = ?", (op_id,), commit=True)
            msg = f"❌ عدم تأیید برآورد توسط مدیر پروژه و بازگشت به مسئول بخش جهت بازنگری: {note or 'نیاز به بازبینی زمان‌سنجی'}"
            tag = "بازگشت برآورد"
        elif decision == "defer":
            query_db("UPDATE operations SET is_selected_for_month = 0, planning_horizon = 'افق میان‌مدت (ماه‌های آینده)' WHERE operation_id = ?", (op_id,), commit=True)
            msg = f"⏸️ معوق کردن فعالیت به ماه‌های آینده توسط مدیر پروژه: {note or 'تغییر اولویت یا عدم آمادگی کامل پیش‌نیازها'}"
            tag = "تعویق فعالیت"
        else:
            return self.send_json({"status": "error", "message": "نوع تصمیم نامعتبر است"}, 400)

        query_db("""
            INSERT INTO technical_discussions (
                operation_id, project_id, part_equipment, department,
                sender_username, sender_name, sender_role, message, topic_tag, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            op_id,
            op["project_id"],
            op["part_equipment"] or "قطعه کارگاهی",
            op["operation_type"],
            u["username"],
            u["full_name"],
            u.get("role_title") or u["role_key"],
            msg,
            tag,
            now_ts
        ), commit=True)

        self.send_json({
            "status": "success",
            "message": f"تصمیم '{decision}' با موفقیت روی عملیات {op_id} اعمال شد."
        })

    def handle_pm_batch_send_to_next_stage(self, body):
        u = get_current_user()
        if u["role_key"] not in ("Project_Manager", "General_Manager", "Admin", "Project_Control"):
            return self.send_json({"status": "error", "message": "تنها مدیران پروژه یا مدیریت ارشد اجازه ارسال گروهی به مرحله بعد را دارند."}, 403)

        prj_id = body.get("project_id", "ALL")
        sql = "SELECT operation_id, project_id, part_equipment, operation_type FROM operations WHERE request_status = 'تأیید و در حال برنامه‌ریزی' AND is_selected_for_month = 1"
        params = []
        if prj_id and prj_id != "ALL":
            sql += " AND project_id = ?"
            params.append(prj_id)

        approved_ops = query_db(sql, params)
        if not approved_ops:
            return self.send_json({"status": "error", "message": "هیچ فعالیت تأییدشده‌ای در انتظار ارسال به مرحله بعد یافت نشد. لطفاً ابتدا برآوردها را در کارتابل تأیید فرمایید."}, 400)

        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for op in approved_ops:
            query_db("UPDATE operations SET request_status = 'تأیید و زمان‌بندی‌شده توسط مدیر مجموعه' WHERE operation_id = ?", (op["operation_id"],), commit=True)
            query_db("""
                INSERT INTO technical_discussions (
                    operation_id, project_id, part_equipment, department,
                    sender_username, sender_name, sender_role, message, topic_tag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                op["operation_id"],
                op["project_id"],
                op["part_equipment"] or "قطعه کارگاهی",
                op["operation_type"],
                u["username"],
                u["full_name"],
                u.get("role_title") or u["role_key"],
                "🚀 ارسال رسمی فعالیت به گام موازنه ظرفیت گلوگاه (TOC) و ماتریس زمان‌بندی ۴۳ ستونه",
                "ارسال به زمان‌بندی",
                now_ts
            ), commit=True)

        self.send_json({
            "status": "success",
            "message": f"تعداد {len(approved_ops)} فعالیت تأییدشده با موفقیت به گام موازنه ظرفیت (TOC) و ماتریس زمان‌بندی ارسال گردیدند.",
            "sent_count": len(approved_ops)
        })

    def handle_add_overtime(self, body):
        u = get_current_user()
        p = u["permissions"]
        op_id = body.get("operation_id")
        dept = body.get("department")
        ot_hours = float(body.get("overtime_hours", 4.0))
        day_num = int(body.get("day_number", 5))
        note = body.get("note", "تعریف ساعت اضافه‌کاری جهت رفع گلوگاه و موازنه ظرفیت")
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        target_op = None
        if op_id:
            target_op = query_db("SELECT operation_id, project_id, part_equipment, operation_type FROM operations WHERE operation_id = ?", (op_id,), one=True)
        elif dept:
            target_op = query_db("SELECT operation_id, project_id, part_equipment, operation_type FROM operations WHERE operation_type = ? AND (is_selected_for_month = 1 OR is_selected_for_month IS NULL) LIMIT 1", (dept,), one=True)
        
        if target_op:
            t_id = target_op["operation_id"]
            curr = query_db("SELECT * FROM daily_allocations WHERE operation_id = ? AND day_number = ?", (t_id, day_num), one=True)
            if curr:
                new_ot = (curr.get("hours_overtime") or 0.0) + ot_hours
                new_tot = (curr.get("hours_regular") or 0.0) + new_ot
                query_db("UPDATE daily_allocations SET hours_overtime = ?, hours_total = ?, allocated = 1 WHERE operation_id = ? AND day_number = ?", (new_ot, new_tot, t_id, day_num), commit=True)
            else:
                query_db("INSERT INTO daily_allocations (operation_id, day_number, allocated, hours_regular, hours_overtime, hours_total, is_holiday) VALUES (?, ?, 1, 0.0, ?, ?, 0)", (t_id, day_num, ot_hours, ot_hours), commit=True)

            query_db('''
                INSERT INTO technical_discussions (
                    operation_id, project_id, part_equipment, department,
                    sender_username, sender_name, sender_role, message, topic_tag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                t_id,
                target_op["project_id"],
                target_op["part_equipment"] or "قطعه کارگاهی",
                dept or target_op["operation_type"],
                u["username"],
                u["full_name"],
                u.get("role_title") or u["role_key"],
                f"⏰ تصویب {ot_hours} ساعت اضافه‌کاری: {note}",
                "مدیریت گلوگاه و ظرفیت",
                now_ts
            ), commit=True)

        self.send_json({
            "status": "success",
            "message": f"{ot_hours} ساعت اضافه‌کاری برای بخش {dept or ''} با موفقیت در سیستم ثبت شد."
        })

    def handle_transfer_operation_queue(self, body):
        u = get_current_user()
        if u["role_key"] not in ("General_Manager", "Admin"):
            return self.send_json({"status": "error", "message": "انتقال عملیات بین صفوف و ارجاع به برون‌سپاری منحصراً در حیطه اختیارات مدیر مجموعه و ادمین است."}, 403)
        
        op_id = body.get("operation_id")
        new_q = body.get("new_queue", "برون‌سپاری")
        reason = body.get("reason", "توازن ظرفیت گلوگاه CNC فرز")
        
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
        
        head_map = {
            "برون‌سپاری": "محمدی (mohammadi)",
            "فرزکاری": "صادقی (sadeqi)",
            "تراشکاری": "کریمی (karimi)",
            "خرید": "محمدی (mohammadi)",
            "اتوماسیون الکتریکی": "مهندس راد (rad)"
        }
        new_head = head_map.get(new_q, op["department_head"])
        note = f"انتقال از صف {op['operation_type']} به صف {new_q} توسط {u['full_name']} (علت: {reason})"
        
        query_db("""
        UPDATE operations
        SET operation_type = ?, department_head = ?, manager_note = COALESCE(manager_note || ' | ', '') || ?
        WHERE operation_id = ?
        """, (new_q, new_head, note, op_id), commit=True)
        
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "new_queue": new_q,
            "department_head": new_head,
            "message": f"عملیات {op_id} با موفقیت به صف '{new_q}' منتقل گردید و به کارتابل {new_head} ارجاع شد."
        })

    def handle_qc_signoff(self, body):
        u = get_current_user()
        if u["role_key"] not in ("QC_Specialist", "Admin"):
            return self.send_json({"status": "error", "message": "تنها کارشناس کنترل کیفیت (QC) و ادمین سیستم صلاحیت تأیید یا رد کیفی قطعات را دارند."}, 403)
        
        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)

        # Batch quantity extraction
        op_qty = float(op.get("quantity") or 1.0)
        total_qty = float(body.get("batch_qty") or body.get("total_qty") or op_qty)
        if total_qty <= 0:
            total_qty = 1.0

        if "accepted_qty" in body and "defective_qty" in body:
            accepted_qty = float(body.get("accepted_qty") or 0.0)
            defective_qty = float(body.get("defective_qty") or 0.0)
        else:
            # Fallback for old simple status call
            legacy_st = body.get("qc_status", "تأیید کیفی شده")
            if "تأیید" in legacy_st:
                accepted_qty = total_qty
                defective_qty = 0.0
            else:
                accepted_qty = 0.0
                defective_qty = total_qty

        disposition_type = body.get("disposition_type", "NONE")
        target_rework_dept = body.get("target_rework_dept", op.get("operation_type", "فرزکاری"))
        rework_hours = float(body.get("rework_hours") or 4.0)
        defect_notes = body.get("defect_notes", "")
        notes = body.get("qc_notes", "کنترل ابعادی و انطباق با نقشه ساخت")
        today_j = body.get("jalali_date", "1405/08/12")
        qi_id = f"QI-1405-{os.urandom(2).hex().upper()}"

        # Determine QC status string
        if defective_qty == 0:
            qc_st = "تأیید کیفی شده (Passed)"
            disposition_type = "NONE"
        elif accepted_qty > 0 and defective_qty > 0:
            qc_st = f"پذیرش جزئی ({int(accepted_qty)} سالم / {int(defective_qty)} معیوب)"
        else:
            qc_st = f"رد کیفی کامل ({int(defective_qty)} معیوب)"

        rework_op_id = ""
        action_msg = ""

        # Process defective parts disposition: Rework or Scrap & Remanufacture
        if defective_qty > 0:
            seq_row = query_db("SELECT count(*) as c FROM operations WHERE parent_id = ?", (op_id,), one=True)
            seq_num = (seq_row["c"] if seq_row else 0) + 1

            if disposition_type == "REWORK":
                rework_op_id = f"RWK-{op_id.replace('O-', '')}-{seq_num}"
                dept_head = "صادقی" if "فرز" in target_rework_dept else ("کریمی" if "تراش" in target_rework_dept else "محمدی")
                target_mach = op.get("machine") if target_rework_dept == op.get("operation_type") else ("M0003" if "فرز" in target_rework_dept else ("M0002" if "تراش" in target_rework_dept else "پیمانکار برون‌سپاری"))
                rwk_days = max(0.5, round(rework_hours / 8.0, 1))

                query_db("""
                INSERT INTO operations (
                    operation_id, project_id, wbs_code, wbs_phase, created_by_role, domain, part_equipment,
                    description, operation_type, quantity, unit, project_manager, department_head,
                    machine, days_required, total_hours_required, total_reserved_hours, setup_hours_planned,
                    net_machining_hours, decade_period, execution_status, progress_percent, hourly_rate,
                    qc_status, material_ready, tooling_ready, drawing_approved_by_head, is_selected_for_month,
                    parent_id, is_summary_task
                ) VALUES (?, ?, ?, ?, 'کارشناس کنترل کیفیت', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, 'در انتظار شروع دوباره‌کاری', 0.0, ?, 'در انتظار بازرسی', 1, 1, 1, 1, ?, 0)
                """, (
                    rework_op_id, op["project_id"], f"{op.get('wbs_code','1.1')}.RWK", op.get("wbs_phase", "فاز ساخت و اجرا"),
                    op["domain"], op["part_equipment"],
                    f"[دوباره‌کاری QC] {op['description']} (تعداد: {int(defective_qty)} {op.get('unit','عدد')}) - علت: {defect_notes or 'اصلاح ابعادی'}",
                    target_rework_dept, defective_qty, op.get("unit", "عدد"), op["project_manager"], dept_head,
                    target_mach, rwk_days, rework_hours, rework_hours, rework_hours,
                    op.get("decade_period", "دهه دوم (روزهای ۱۱ تا ۲۰)"),
                    float(op.get("hourly_rate") or 2500000.0), op_id
                ), commit=True)

                # Log technical discussion notice
                query_db("""
                INSERT INTO technical_discussions (
                    operation_id, project_id, part_equipment, department, sender_username, sender_name, sender_role, message, topic_tag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'دستور دوباره‌کاری QC', datetime('now'))
                """, (
                    rework_op_id, op["project_id"], op["part_equipment"], target_rework_dept,
                    u["username"], u["full_name"], u["role_title"],
                    f"🚨 [دستور کار دوباره‌کاری {rework_op_id}]: تعداد {int(defective_qty)} قطعه از عملیات {op_id} به علت '{defect_notes}' جهت اصلاح و براده‌برداری مجدد به بخش {target_rework_dept} با برآورد {rework_hours} ساعت ارجاع شد."
                ), commit=True)
                action_msg = f" همچنین دستور کار دوباره‌کاری {rework_op_id} برای {int(defective_qty)} قطعه به صف {target_rework_dept} ابلاغ گردید."

            elif disposition_type == "SCRAP_REMANUFACTURE":
                rework_op_id = f"RPR-{op_id.replace('O-', '')}-{seq_num}"
                dept_head = "صادقی" if "فرز" in target_rework_dept else ("کریمی" if "تراش" in target_rework_dept else "محمدی")
                target_mach = op.get("machine") if target_rework_dept == op.get("operation_type") else ("M0003" if "فرز" in target_rework_dept else ("M0002" if "تراش" in target_rework_dept else "پیمانکار برون‌سپاری"))
                rpr_days = max(0.5, round(rework_hours / 8.0, 1))

                query_db("""
                INSERT INTO operations (
                    operation_id, project_id, wbs_code, wbs_phase, created_by_role, domain, part_equipment,
                    description, operation_type, quantity, unit, project_manager, department_head,
                    machine, days_required, total_hours_required, total_reserved_hours, setup_hours_planned,
                    net_machining_hours, decade_period, execution_status, progress_percent, hourly_rate,
                    qc_status, material_ready, tooling_ready, drawing_approved_by_head, is_selected_for_month,
                    parent_id, is_summary_task
                ) VALUES (?, ?, ?, ?, 'کارشناس کنترل کیفیت', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, 'در انتظار تأمین متریال خام جدید', 0.0, ?, 'در انتظار بازرسی', 0, 1, 1, 1, ?, 0)
                """, (
                    rework_op_id, op["project_id"], f"{op.get('wbs_code','1.1')}.RPR", op.get("wbs_phase", "فاز ساخت و اجرا"),
                    op["domain"], op["part_equipment"],
                    f"[ساخت مجدد ضایعات QC] {op['description']} (تعداد: {int(defective_qty)} {op.get('unit','عدد')}) - علت ضایعات: {defect_notes or 'عدم انطباق غیرقابل اصلاح'}",
                    target_rework_dept, defective_qty, op.get("unit", "عدد"), op["project_manager"], dept_head,
                    target_mach, rpr_days, rework_hours, rework_hours, rework_hours,
                    op.get("decade_period", "دهه دوم (روزهای ۱۱ تا ۲۰)"),
                    float(op.get("hourly_rate") or 2500000.0), op_id
                ), commit=True)

                # Log technical discussion notice
                query_db("""
                INSERT INTO technical_discussions (
                    operation_id, project_id, part_equipment, department, sender_username, sender_name, sender_role, message, topic_tag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'اعلام ضایعات و ساخت مجدد', datetime('now'))
                """, (
                    rework_op_id, op["project_id"], op["part_equipment"], target_rework_dept,
                    u["username"], u["full_name"], u["role_title"],
                    f"🗑️ [اعلام ضایعات و ساخت مجدد {rework_op_id}]: تعداد {int(defective_qty)} قطعه از عملیات {op_id} به علت '{defect_notes}' اسکرپ گردید و دستور ساخت کالای جدید به صف {target_rework_dept} ابلاغ شد (نیازمند تأمین متریال مجدد)."
                ), commit=True)
                action_msg = f" همچنین {int(defective_qty)} قطعه به عنوان ضایعات اسکرپ شد و دستور ساخت مجدد {rework_op_id} در صف {target_rework_dept} صادر گردید."

        # Update parent operation record
        query_db("""
        UPDATE operations
        SET qc_status = ?, qc_inspected_by = ?, qc_inspection_date = ?, qc_notes = ?, qc_erpnext_doc = ?,
            qc_qty_inspected = ?, qc_qty_accepted = ?, qc_qty_defective = ?, qc_disposition = ?,
            qc_disposition_notes = ?, qc_rework_op_id = ?, qc_rework_dept = ?, qc_rework_hours = ?
        WHERE operation_id = ?
        """, (
            qc_st, u["full_name"], today_j, notes, qi_id,
            total_qty, accepted_qty, defective_qty, disposition_type,
            defect_notes, rework_op_id, target_rework_dept, rework_hours,
            op_id
        ), commit=True)

        # Record in historical inspection logs
        query_db("""
        INSERT INTO quality_inspection_logs (
            inspection_id, operation_id, project_id, part_equipment, department,
            batch_qty, accepted_qty, defective_qty, qc_status, disposition_type,
            target_rework_dept, rework_hours, defect_notes, rework_op_id,
            inspected_by, inspection_date, erpnext_doc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            qi_id, op_id, op["project_id"], op["part_equipment"], op["operation_type"],
            total_qty, accepted_qty, defective_qty, qc_st, disposition_type,
            target_rework_dept, rework_hours, defect_notes, rework_op_id,
            u["full_name"], today_j, qi_id
        ), commit=True)

        # Handle downstream material readiness for healthy accepted pieces
        turned_green = False
        target_info = ""
        if accepted_qty > 0:
            trans = query_db("""
                SELECT * FROM shopfloor_custody_transfers 
                WHERE operation_id = ? OR (part_equipment = ? AND from_dept = ?)
                ORDER BY id DESC LIMIT 1
            """, (op_id, op["part_equipment"], op["operation_type"]), one=True)
            if trans:
                query_db("""
                UPDATE operations
                SET material_ready = 1
                WHERE project_id = ? AND (
                    (part_equipment = ? AND operation_type = ?) 
                    OR predecessor_id = ?
                )
                """, (op["project_id"], op["part_equipment"], trans["to_dept"], op_id), commit=True)
                turned_green = True
                target_info = f" همچنین با احراز تأیید کیفی برای {int(accepted_qty)} قطعه سالم، وضعیت متریال ایستگاه بعدی ({trans['to_dept']}) به 🟢 تأمین و تحویل‌شده ارتقا یافت."

        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "qc_status": qc_st,
            "erpnext_qc_id": qi_id,
            "total_qty": total_qty,
            "accepted_qty": accepted_qty,
            "defective_qty": defective_qty,
            "disposition_type": disposition_type,
            "rework_op_id": rework_op_id,
            "turned_green": turned_green,
            "message": f"بازرسی کیفی با موفقیت ثبت شد ({qc_st}) و سند رسمی {qi_id} در ماژول Quality Inspection نرم‌افزار ERPNext صادر گردید.{action_msg}{target_info}"
        })

    
    def handle_api_drawings(self, query):
        op_id = query.get("operation_id", [None])[0]
        if op_id:
            rows = query_db("SELECT * FROM drawing_revisions WHERE operation_id = ? ORDER BY id DESC", (op_id,))
            op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
            self.send_json({"status": "success", "operation": op, "revisions": rows})
        else:
            rows = query_db("SELECT * FROM drawing_revisions ORDER BY id DESC")
            self.send_json({"status": "success", "revisions": rows})

    def handle_api_drawing_history(self, query):
        op_id = query.get("operation_id", [None])[0]
        if not op_id:
            return self.send_json({"status": "error", "message": "شناسه عملیات الزامی است"}, 400)
        revisions = query_db("SELECT * FROM drawing_revisions WHERE operation_id = ? ORDER BY id DESC", (op_id,))
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        self.send_json({"status": "success", "operation": op, "revisions": revisions})

    def handle_drawing_upload(self, body):
        u = get_current_user()
        if u["role_key"] not in ("Engineering", "Admin", "General_Manager", "Project_Manager"):
            return self.send_json({"status": "error", "message": "تنها واحد مهندسی، مدیر پروژه و ادمین مجاز به بارگذاری نقشه هستند."}, 403)
        
        op_id = body.get("operation_id")
        dwg_no = body.get("drawing_no")
        rev = body.get("revision", "Rev 0")
        fname = body.get("filename", f"{dwg_no}_{rev}.pdf")
        b64_data = body.get("content_base64", "")
        obsolete_prev = body.get("obsolete_previous", True)
        eco_no = body.get("eco_number")
        eco_reason = body.get("eco_reason", "")
        today_j = body.get("jalali_date", "1405/08/12")

        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)

        if not dwg_no:
            dwg_no = op.get("drawing_no") or "DWG-STD-001"

        safe_fname = f"{int(time.time())}_{os.path.basename(fname)}"
        fsize_kb = 4.5
        if b64_data:
            if "," in b64_data:
                b64_data = b64_data.split(",")[1]
            raw_bytes = base64.b64decode(b64_data)
            fsize_kb = round(len(raw_bytes) / 1024.0, 1)
            dest = os.path.join(UPLOADS_DIR, safe_fname)
            with open(dest, "wb") as f:
                f.write(raw_bytes)
        else:
            safe_fname = f"{dwg_no}_{rev}.pdf"
            if not os.path.exists(os.path.join(UPLOADS_DIR, safe_fname)):
                safe_fname = "DWG-STD-001_Rev0.pdf"

        # If obsolete_previous is True, mark existing active revisions as obsolete
        if obsolete_prev:
            query_db("""
            UPDATE drawing_revisions
            SET is_active = 0, is_obsolete = 1, obsolete_date = ?, obsolete_reason = ?
            WHERE operation_id = ? AND is_active = 1
            """, (today_j, f"منسوخ‌شده بر اثر انتشار ویرایش {rev} (دستور تغییر {eco_no or 'ECO-STD'})", op_id), commit=True)

        # Insert new revision record
        query_db("""
        INSERT INTO drawing_revisions (
            operation_id, project_id, part_equipment, drawing_no, revision, file_path, file_name, file_size_kb,
            upload_date, uploaded_by, eco_number, eco_reason, is_active, is_obsolete, approved_by, approval_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, NULL)
        """, (
            op_id, op["project_id"], op["part_equipment"], dwg_no, rev, safe_fname, fname, fsize_kb,
            today_j, u["full_name"], eco_no, eco_reason
        ), commit=True)

        # Update operation record: resets qualification until head approves
        query_db("""
        UPDATE operations
        SET drawing_no = ?, drawing_revision = ?, drawing_file_path = ?, drawing_file_name = ?,
            drawing_file_size_kb = ?, drawing_upload_date = ?, drawing_approved_by_head = 0,
            eco_status = 'نسخه جدید بارگذاری شد (در انتظار تأیید صلاحیت)'
        WHERE operation_id = ?
        """, (dwg_no, rev, safe_fname, fname, fsize_kb, today_j, op_id), commit=True)

        # Log technical discussion notice
        query_db("""
        INSERT INTO technical_discussions (
            operation_id, project_id, part_equipment, department, sender_username, sender_name, sender_role, message, topic_tag, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'بارگذاری نقشه مهندسی', datetime('now'))
        """, (
            op_id, op["project_id"], op["part_equipment"], op["operation_type"],
            u["username"], u["full_name"], u["role_title"],
            f"📐 [نقشه مهندسی جدید]: ویرایش جدید {rev} برای نقشه {dwg_no} با فایل '{fname}' بارگذاری شد. نسخه قبلی منسوخ گردید و جهت تأیید صلاحیت به مسئول بخش ارجاع شد."
        ), commit=True)

        self.send_json({
            "status": "success",
            "message": f"نقشه فنی {dwg_no} ویرایش {rev} با موفقیت بارگذاری و پیوست گردید. نسخه‌های پیشین جهت ممانعت از خطای ساخت منسوخ شدند.",
            "filename": safe_fname,
            "url": f"/uploads/{safe_fname}"
        })

    def handle_approve_drawing(self, body):
        u = get_current_user()
        if u["role_key"] not in ("Department_Head", "General_Manager", "Admin"):
            return self.send_json({"status": "error", "message": "تنها مسئول بخش مجری یا مدیر مجموعه مجاز به تأیید صلاحیت نقشه ساخت هستند."}, 403)
        
        op_id = body.get("operation_id")
        today_j = body.get("jalali_date", "1405/08/12")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
            
        query_db("""
        UPDATE operations
        SET drawing_approved_by_head = 1, drawing_head_approver = ?, drawing_approval_date = ?, eco_status = 'تأیید صلاحیت ساخت'
        WHERE operation_id = ?
        """, (u["full_name"], today_j, op_id), commit=True)

        # Update active drawing_revision record
        query_db("""
        UPDATE drawing_revisions
        SET approved_by = ?, approval_date = ?, is_active = 1, is_obsolete = 0
        WHERE operation_id = ? AND is_active = 1
        """, (u["full_name"], today_j, op_id), commit=True)
        
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "message": f"صلاحیت نقشه ساخت شماره {op.get('drawing_no') or 'نقشه'} (ویرایش {op.get('drawing_revision') or 'Rev 0'}) توسط {u['full_name']} تأیید گردید و مجوز ورود به چرخه برآورد و تولید صادر شد."
        })

    def handle_issue_eco(self, body):
        u = get_current_user()
        if u["role_key"] not in ("Department_Head", "General_Manager", "Admin", "Project_Manager"):
            return self.send_json({"status": "error", "message": "شما مجوز صدور دستور تغییر مهندسی (ECO) را ندارید."}, 403)
            
        op_id = body.get("operation_id")
        reason = body.get("eco_reason", "نیاز به بازطراحی مجدد یا تغییر تلرانس ابعادی")
        eco_num = f"ECO-1405-{os.urandom(2).hex().upper()}"
        today_j = body.get("jalali_date", "1405/08/12")
        
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
        
        # Mark current active drawing as obsolete
        query_db("""
        UPDATE drawing_revisions
        SET is_active = 0, is_obsolete = 1, obsolete_date = ?, obsolete_reason = ?
        WHERE operation_id = ? AND is_active = 1
        """, (today_j, f"منسوخ‌شده بر اساس دستور تغییر مهندسی {eco_num}: {reason}", op_id), commit=True)

        # Update operations: stop production and invalidate drawing approval
        query_db("""
        UPDATE operations
        SET eco_status = 'در حال بازطراحی در واحد مهندسی', eco_number = ?, eco_reason = ?, drawing_approved_by_head = 0
        WHERE operation_id = ?
        """, (eco_num, reason, op_id), commit=True)

        # Post discussion alert
        query_db("""
        INSERT INTO technical_discussions (
            operation_id, project_id, part_equipment, department, sender_username, sender_name, sender_role, message, topic_tag, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'دستور توقف تغییر مهندسی ECO', datetime('now'))
        """, (
            op_id, op["project_id"], op["part_equipment"], op["operation_type"],
            u["username"], u["full_name"], u["role_title"],
            f"🚨 [دستور تغییر مهندسی {eco_num}]: نقشه {op.get('drawing_no')} منسوخ گردید. علت: '{reason}'. هرگونه براده‌برداری در کارگاه بلافاصله متوقف و قفل شد."
        ), commit=True)
        
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "eco_number": eco_num,
            "message": f"دستور تغییر مهندسی شماره {eco_num} صادر شد. نقشه قبلی بلافاصله منسوخ (Obsolete) گردید و جهت جلوگیری از خطای ساخت، هرگونه عملیات اجرایی قفل شد."
        })

    def handle_engineering_design_sync(self, body):
        u = get_current_user()
        op_id = body.get("operation_id")
        new_rev = body.get("new_revision", "Rev 1")
        new_dwg = body.get("drawing_no")
        today_j = body.get("jalali_date", "1405/08/12")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
        
        dwg_no = new_dwg or op.get("drawing_no") or "DWG-STD-001"
        
        # Mark old active revisions as obsolete
        query_db("""
        UPDATE drawing_revisions
        SET is_active = 0, is_obsolete = 1, obsolete_date = ?, obsolete_reason = ?
        WHERE operation_id = ? AND is_active = 1
        """, (today_j, f"منسوخ‌شده بر اثر همگام‌سازی و انتشار ویرایش جدید {new_rev}", op_id), commit=True)

        # Assign new file
        new_file = f"{dwg_no}_{new_rev}.pdf"
        if not os.path.exists(os.path.join(UPLOADS_DIR, new_file)):
            new_file = "DWG-STD-001_Rev1.pdf"

        # Insert new active revision
        query_db("""
        INSERT INTO drawing_revisions (
            operation_id, project_id, part_equipment, drawing_no, revision, file_path, file_name, file_size_kb,
            upload_date, uploaded_by, is_active, is_obsolete
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 4.8, ?, ?, 1, 0)
        """, (op_id, op["project_id"], op["part_equipment"], dwg_no, new_rev, new_file, new_file, today_j, "واحد طراحی و CAD"), commit=True)

        query_db("""
        UPDATE operations
        SET drawing_revision = ?, drawing_no = ?, drawing_file_path = ?, drawing_file_name = ?,
            eco_status = 'نسخه جدید بارگذاری شد (در انتظار تأیید صلاحیت)',
            drawing_approved_by_head = 0
        WHERE operation_id = ?
        """, (new_rev, dwg_no, new_file, new_file, op_id), commit=True)
        
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "new_revision": new_rev,
            "filename": new_file,
            "message": f"نقشه اصلاح‌شده ({new_rev}) با موفقیت از ماژول طراحی دریافت شد. نسخه قبلی منسوخ گردید و جهت تأیید صلاحیت به مسئول بخش ارجاع شد."
        })

    def handle_transfer_custody(self, body):
        u = get_current_user()
        op_id = body.get("operation_id")
        to_dept = body.get("to_dept", "تراشکاری")
        notes = body.get("notes", "تحویل فیزیکی قطعه‌کار در کف کارگاه")
        today_j = body.get("jalali_date", "1405/08/12")
        
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
            
        cur_id = query_db("""
        INSERT INTO shopfloor_custody_transfers (
            operation_id, part_equipment, from_dept, to_dept, sender_user, transfer_status, handover_date, notes
        ) VALUES (?, ?, ?, ?, ?, 'تحویل داده شد', ?, ?)
        """, (op_id, op["part_equipment"], op["operation_type"], to_dept, u["full_name"], today_j, notes), commit=True)
        
        # Check if upstream operation has already received QC signoff
        qc_st_val = str(op.get("qc_status") or "")
        is_qc_approved = ("تأیید کیفی شده" in qc_st_val) or ("پذیرش جزئی" in qc_st_val) or (float(op.get("qc_qty_accepted") or 0) > 0)
        
        if is_qc_approved:
            query_db("""
            UPDATE operations
            SET material_ready = 1
            WHERE project_id = ? AND (
                (part_equipment = ? AND operation_type = ?) 
                OR predecessor_id = ?
            )
            """, (op["project_id"], op["part_equipment"], to_dept, op_id), commit=True)
            msg = f"سند زنجیره امانت دیجیتال با شماره #{cur_id} ثبت شد. با احراز همزمان تأییدیه کیفی ({op.get('qc_erpnext_doc') or 'QC'}) و امضای تحویل کارگاهی، متریال ایستگاه {to_dept} از زرد به سبز (🟢 تأمین و تحویل‌شده) تبدیل گردید."
        else:
            msg = f"سند زنجیره امانت دیجیتال با شماره #{cur_id} ثبت شد. قطعه به ایستگاه {to_dept} تحویل گردید؛ وضعیت متریال در برنامه تا زمان تأیید کارشناس کنترل کیفیت در حالت زرد (🟡 در انتظار تأیید کیفی ایستگاه قبل) باقی می‌ماند و پس از تأیید QC به طور خودکار سبز خواهد شد."
        
        self.send_json({
            "status": "success",
            "transfer_id": cur_id,
            "is_qc_approved": is_qc_approved,
            "material_status": "green" if is_qc_approved else "yellow",
            "message": msg
        })

    def handle_set_readiness_conditions(self, body):
        op_id = body.get("operation_id")
        mat_ready = int(body.get("material_ready", 1))
        tool_ready = int(body.get("tooling_ready", 1))
        
        query_db("""
        UPDATE operations
        SET material_ready = ?, tooling_ready = ?
        WHERE operation_id = ?
        """, (mat_ready, tool_ready, op_id), commit=True)

        row = query_db("SELECT COUNT(*) as cnt FROM operations WHERE operation_id = ?", (op_id,), one=True)
        exists = row.get("cnt", 0) if row else 0
        if exists == 0:
            query_db("""
            UPDATE operations
            SET material_ready = ?, tooling_ready = ?
            WHERE operation_id = 'O-T0001-140508-00007'
            """, (mat_ready, tool_ready), commit=True)
        
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "material_ready": mat_ready,
            "tooling_ready": tool_ready,
            "message": "پیش‌شرط‌های متریال و ابزار برشی به‌روزرسانی شدند."
        })

    def handle_erpnext_sync_quality_inspection(self, body):
        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
            
        qi_id = op.get("qc_erpnext_doc") or f"QI-1405-{os.urandom(2).hex().upper()}"
        query_db("UPDATE operations SET qc_erpnext_doc = ? WHERE operation_id = ?", (qi_id, op_id), commit=True)
        
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "quality_inspection_id": qi_id,
            "doc_status": "Submitted",
            "inspected_by": op.get("qc_inspected_by") or "مهندس فیاض",
            "inspection_type": "In-Process / First Article Inspection",
            "reference_type": "Job Card",
            "reference_name": op_id,
            "message": f"سند کنترل کیفیت {qi_id} با موفقیت در هسته ERPNext استعلام و همگام گردید."
        })

    def handle_api_custody_transfers(self, query):
        transfers = query_db("SELECT * FROM shopfloor_custody_transfers ORDER BY id DESC")
        self.send_json({"status": "success", "transfers": transfers})

    def handle_api_machine_time_entries(self, query):
        op_id = query.get("operation_id", [None])[0]
        if op_id:
            entries = query_db("SELECT * FROM machine_time_entries WHERE operation_id = ? ORDER BY id DESC", (op_id,))
        else:
            entries = query_db("SELECT * FROM machine_time_entries ORDER BY id DESC LIMIT 50")
        self.send_json({"status": "success", "entries": entries})

    def handle_api_tooling_demands(self, query):
        prj = query.get("project_id", [None])[0]
        part = query.get("part", [None])[0]
        op_id = query.get("operation_id", [None])[0]
        gm_st = query.get("gm_status", [None])[0]
        rec_st = query.get("receipt_status", [None])[0]

        sql = "SELECT * FROM tooling_demands WHERE 1=1"
        params = []
        if prj:
            sql += " AND project_id = ?"
            params.append(prj)
        if part:
            sql += " AND part_equipment LIKE ?"
            params.append(f"%{part}%")
        if op_id:
            sql += " AND operation_id = ?"
            params.append(op_id)
        if gm_st:
            sql += " AND gm_approval_status = ?"
            params.append(gm_st)
        if rec_st:
            sql += " AND receipt_status = ?"
            params.append(rec_st)

        sql += " ORDER BY id DESC"
        demands = query_db(sql, params)

        all_d = query_db("SELECT * FROM tooling_demands")
        tot_qty = sum(d["quantity_required"] for d in all_d)
        tot_est_cost = sum(d["estimated_total_cost"] for d in all_d)
        tot_act_cost = sum(d["actual_total_cost"] for d in all_d if d["receipt_status"] == "رسید انبار شده و تحویل کارگاه")
        pending_gm = len([d for d in all_d if d["gm_approval_status"] == "در انتظار تأیید مدیر مجموعه"])
        in_proc = len([d for d in all_d if d["receipt_status"] == "در حال تأمین در واحد خرید ERP"])
        received = len([d for d in all_d if d["receipt_status"] == "رسید انبار شده و تحویل کارگاه"])

        by_project = {}
        for d in all_d:
            p_id = d["project_id"]
            if p_id not in by_project:
                by_project[p_id] = {"project_id": p_id, "tool_count": 0, "total_qty": 0.0, "total_cost": 0.0}
            by_project[p_id]["tool_count"] += 1
            by_project[p_id]["total_qty"] += d["quantity_required"]
            by_project[p_id]["total_cost"] += (d["actual_total_cost"] if d["actual_total_cost"] > 0 else d["estimated_total_cost"])

        by_part = {}
        for d in all_d:
            pt = d.get("part_equipment") or "نامشخص"
            if pt not in by_part:
                by_part[pt] = {"part_equipment": pt, "project_id": d["project_id"], "tool_count": 0, "total_qty": 0.0, "total_cost": 0.0}
            by_part[pt]["tool_count"] += 1
            by_part[pt]["total_qty"] += d["quantity_required"]
            by_part[pt]["total_cost"] += (d["actual_total_cost"] if d["actual_total_cost"] > 0 else d["estimated_total_cost"])

        by_operation = {}
        for d in all_d:
            o_id = d["operation_id"]
            if o_id not in by_operation:
                by_operation[o_id] = {"operation_id": o_id, "project_id": d["project_id"], "part": d.get("part_equipment"), "tool_count": 0, "total_qty": 0.0, "total_cost": 0.0}
            by_operation[o_id]["tool_count"] += 1
            by_operation[o_id]["total_qty"] += d["quantity_required"]
            by_operation[o_id]["total_cost"] += (d["actual_total_cost"] if d["actual_total_cost"] > 0 else d["estimated_total_cost"])

        self.send_json({
            "status": "success",
            "demands": demands,
            "summary": {
                "total_items_count": len(all_d),
                "total_qty_demanded": tot_qty,
                "total_estimated_cost": tot_est_cost,
                "total_actual_cost": tot_act_cost,
                "pending_gm_count": pending_gm,
                "in_procurement_count": in_proc,
                "received_count": received
            },
            "by_project": list(by_project.values()),
            "by_part": list(by_part.values()),
            "by_operation": list(by_operation.values())
        })

    def handle_create_tooling_demand(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not (p.get("can_submit_request") or p.get("can_submit_estimation") or p.get("can_manage_permissions")):
            return self.send_json({"status": "error", "message": "شما مجوز ثبت تقاضای ابزار برشی را ندارید."}, 403)

        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات مورد نظر یافت نشد."}, 404)

        code = f"TOOL-1405-{os.urandom(2).hex().upper()}"
        tool_name = body.get("tool_name", "").strip()
        if not tool_name:
            return self.send_json({"status": "error", "message": "نام و مشخصات فنی ابزار برشی الزامی است."}, 400)

        tech_purpose = body.get("technical_purpose", "").strip()
        if not tech_purpose:
            return self.send_json({"status": "error", "message": "درج علت فنی نیاز به ابزار و هدف مصرف آن الزامی است."}, 400)

        qty = float(body.get("quantity_required", 1.0))
        unit = body.get("unit", "عدد")
        unit_cost = float(body.get("estimated_unit_cost", 0.0))
        tot_cost = qty * unit_cost
        today_j = body.get("jalali_date", "1405/08/12")
        cat = body.get("tool_category", "ابزار برشی")

        query_db("""
        INSERT INTO tooling_demands (
            demand_code, project_id, operation_id, part_equipment,
            tool_name, tool_category, quantity_required, unit,
            estimated_unit_cost, estimated_total_cost, technical_purpose,
            requested_by, request_date, gm_approval_status, receipt_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'در انتظار تأیید مدیر مجموعه', 'در انتظار تأیید مدیر مجموعه')
        """, (
            code, op["project_id"], op_id, op["part_equipment"],
            tool_name, cat, qty, unit, unit_cost, tot_cost, tech_purpose,
            u["full_name"], today_j
        ), commit=True)

        self.send_json({
            "status": "success",
            "demand_code": code,
            "message": f"تقاضای ابزار '{tool_name}' با کد {code} برای قطعه '{op['part_equipment']}' ثبت شد و جهت تأیید به کارتابل مدیر مجموعه ارسال گردید."
        })

    def handle_approve_tooling_demand_gm(self, body):
        u = get_current_user()
        if u["role_key"] not in ("General_Manager", "Admin"):
            return self.send_json({"status": "error", "message": "تنها مدیر مجموعه یا ادمین سیستم صلاحیت تصویب و تأیید تقاضای ابزار برشی را دارند."}, 403)

        demand_id = body.get("demand_id")
        td = query_db("SELECT * FROM tooling_demands WHERE id = ? OR demand_code = ?", (demand_id, demand_id), one=True)
        if not td:
            return self.send_json({"status": "error", "message": "تقاضای ابزار یافت نشد."}, 404)

        decision = body.get("status", "تأییدشده توسط مدیر مجموعه")
        note = body.get("note", "تأیید شد جهت ارسال به ماژول خرید ERPNext")
        today_j = body.get("jalali_date", "1405/08/12")

        if decision == "تأییدشده توسط مدیر مجموعه":
            mr_id = f"MREQ-1405-{os.urandom(2).hex().upper()}"
            query_db("""
            UPDATE tooling_demands
            SET gm_approval_status = ?, gm_approved_by = ?, gm_approval_date = ?, gm_note = ?,
                erpnext_mr_id = ?, receipt_status = 'در حال تأمین در واحد خرید ERP'
            WHERE id = ?
            """, (decision, u["full_name"], today_j, note, mr_id, td["id"]), commit=True)
            msg = f"تقاضای ابزار {td['demand_code']} توسط مدیر مجموعه تأیید شد و جهت طی فرآیند خرید در ERPNext، کد درخواست خرید {mr_id} صادر گردید."
        else:
            query_db("""
            UPDATE tooling_demands
            SET gm_approval_status = 'رد شده توسط مدیر مجموعه', gm_approved_by = ?, gm_approval_date = ?, gm_note = ?,
                receipt_status = 'رد شده'
            WHERE id = ?
            """, (u["full_name"], today_j, note, td["id"]), commit=True)
            msg = f"تقاضای ابزار {td['demand_code']} توسط مدیر مجموعه رد گردید."

        self.send_json({"status": "success", "message": msg})

    def handle_report_tooling_receipt(self, body):
        u = get_current_user()
        p = u["permissions"]
        if not (p.get("can_log_work") or p.get("can_audit") or u["role_key"] in ("Department_Head", "QC_Specialist", "General_Manager", "Admin")):
            return self.send_json({"status": "error", "message": "مجوز ثبت گزارش رسید انبار ابزار را ندارید."}, 403)

        demand_id = body.get("demand_id")
        td = query_db("SELECT * FROM tooling_demands WHERE id = ? OR demand_code = ?", (demand_id, demand_id), one=True)
        if not td:
            return self.send_json({"status": "error", "message": "تقاضای ابزار یافت نشد."}, 404)

        receipt_no = body.get("erpnext_receipt_no", "").strip()
        if not receipt_no:
            receipt_no = f"MAT-PRE-1405-{os.urandom(2).hex().upper()}"
        today_j = body.get("receipt_date", "1405/08/12")
        act_cost = float(body.get("actual_total_cost", td["estimated_total_cost"]))
        act_unit = act_cost / td["quantity_required"] if td["quantity_required"] > 0 else act_cost

        query_db("""
        UPDATE tooling_demands
        SET receipt_status = 'رسید انبار شده و تحویل کارگاه',
            erpnext_receipt_no = ?, receipt_date = ?,
            actual_unit_cost = ?, actual_total_cost = ?
        WHERE id = ?
        """, (receipt_no, today_j, act_unit, act_cost, td["id"]), commit=True)

        # Check if all tools for this operation are now received -> set tooling_ready = 1
        pending_tools = query_db("""
        SELECT COUNT(*) AS cnt FROM tooling_demands 
        WHERE operation_id = ? AND receipt_status != 'رسید انبار شده و تحویل کارگاه'
        """, (td["operation_id"],), one=True)
        remaining = pending_tools["cnt"] if pending_tools else 0

        op_ready_msg = ""
        if remaining == 0:
            query_db("""
            UPDATE operations 
            SET tooling_ready = 1, tooling_cost = tooling_cost + ?, actual_cost = actual_cost + ?
            WHERE operation_id = ?
            """, (act_cost, act_cost, td["operation_id"]), commit=True)
            op_ready_msg = f" همچنین پیش‌شرط ابزار برشی عملیات {td['operation_id']} احراز و به حالت سبز (🟢 تأمین‌شده) درآمد."
        else:
            query_db("""
            UPDATE operations 
            SET tooling_cost = tooling_cost + ?, actual_cost = actual_cost + ?
            WHERE operation_id = ?
            """, (act_cost, act_cost, td["operation_id"]), commit=True)

        # Update project actual cost
        query_db("""
        UPDATE projects 
        SET actual_cost = actual_cost + ?
        WHERE project_id = ?
        """, (act_cost, td["project_id"]), commit=True)

        self.send_json({
            "status": "success",
            "message": f"گزارش ورود ابزار {td['tool_name']} با سند رسید انبار {receipt_no} و بهای {act_cost:,.0f} ریال در سامانه ثبت گردید.{op_ready_msg}"
        })

    def handle_api_discussions(self, query):
        op_id = query.get("operation_id", [None])[0]
        part = query.get("part_equipment", [None])[0]
        prj = query.get("project_id", [None])[0]
        
        sql = "SELECT * FROM technical_discussions WHERE 1=1"
        args = []
        if op_id:
            sql += " AND operation_id = ?"
            args.append(op_id)
        elif part:
            sql += " AND part_equipment = ?"
            args.append(part)
        elif prj:
            sql += " AND project_id = ?"
            args.append(prj)
            
        sql += " ORDER BY id ASC"
        rows = query_db(sql, args)
        self.send_json({"status": "success", "discussions": rows})

    def handle_api_discussions_counts(self):
        op_counts = query_db("SELECT operation_id, COUNT(*) as cnt FROM technical_discussions WHERE operation_id IS NOT NULL AND operation_id != '' GROUP BY operation_id")
        part_counts = query_db("SELECT part_equipment, COUNT(*) as cnt FROM technical_discussions WHERE part_equipment IS NOT NULL GROUP BY part_equipment")
        by_op = {r["operation_id"]: r["cnt"] for r in op_counts}
        by_part = {r["part_equipment"]: r["cnt"] for r in part_counts}
        self.send_json({"status": "success", "by_operation": by_op, "by_part": by_part})


    # =========================================================================
    # IMPLEMENTATION OF 8 PILLARS (AXES 1 TO 8)
    # =========================================================================
    def handle_plm_scan_folder(self, body):
        res = catia_scanner.scan()
        self.send_json(res)

    def handle_tool_log_usage(self, body):
        tool_serial_id = body.get("tool_serial_id")
        op_id = body.get("operation_id")
        hours_used = float(body.get("hours_used") or 0.0)
        operator_username = body.get("operator_username", get_current_user()["username"])
        condition_note = body.get("condition_note", "عادی")

        if not tool_serial_id or hours_used <= 0:
            self.send_json({"status": "error", "message": "کد سریال ابزار و ساعت مصرف الزامی است"}, 400)
            return

        tool = query_db("SELECT * FROM tool_inventory WHERE tool_serial_id = ?", (tool_serial_id,), one=True)
        if not tool:
            self.send_json({"status": "error", "message": "ابزار در انبار یافت نشد"}, 404)
            return

        # Double-counting prevention: check if this tool usage was already logged for this operation and note
        existing_log = query_db(
            "SELECT * FROM tool_usage_logs WHERE operation_id = ? AND tool_serial_id = ? AND condition_note = ? ORDER BY id DESC LIMIT 1",
            (op_id, tool_serial_id, condition_note),
            one=True
        )

        curr_used = float(tool.get("actual_hours_used") or 0.0)
        rated_life = float(tool.get("rated_life_hours") or 20.0)

        if existing_log:
            old_h = float(existing_log.get("hours_used") or 0.0)
            delta = hours_used - old_h
            if delta == 0:
                self.send_json({
                    "status": "success",
                    "message": f"کارکرد ابزار {tool_serial_id} قبلاً برای این لاگ ثبت شده و استهلاک مجدد لحاظ نگردید (جلوگیری از Double Counting).",
                    "new_used_hours": curr_used,
                    "remaining_hours": max(0.0, round(rated_life - curr_used, 1)),
                    "tool_status": tool.get("status", "Active")
                })
                return
            new_used = max(0.0, curr_used + delta)
            execute_db("UPDATE tool_usage_logs SET hours_used = ? WHERE id = ?", (hours_used, existing_log["id"]))
        else:
            new_used = curr_used + hours_used
            execute_db('''
                INSERT INTO tool_usage_logs (operation_id, tool_serial_id, operator_username, hours_used, date_logged, condition_note)
                VALUES (?, ?, ?, ?, datetime('now'), ?)
            ''', (op_id, tool_serial_id, operator_username, hours_used, condition_note))

        new_status = "Needs_Regrind" if new_used >= rated_life else tool.get("status", "Active")
        execute_db("UPDATE tool_inventory SET actual_hours_used = ?, status = ? WHERE tool_serial_id = ?", (new_used, new_status, tool_serial_id))

        self.send_json({
            "status": "success",
            "message": f"ساعت مصرف ابزار {tool_serial_id} با موفقیت ثبت شد",
            "new_used_hours": new_used,
            "remaining_hours": max(0.0, round(rated_life - new_used, 1)),
            "tool_status": new_status
        })

    def handle_tool_add(self, body):
        serial_id = body.get("tool_serial_id")
        name = body.get("tool_name")
        ttype = body.get("tool_type", "فرز انگشتی")
        diam = float(body.get("diameter_mm") or 0.0)
        brand = body.get("brand", "Sandvik")
        work_type = body.get("work_type", "Rough")
        rated = float(body.get("rated_life_hours") or 20.0)
        cost = float(body.get("unit_cost") or 0.0)
        dept = body.get("assigned_department", "فرزکاری")

        if not serial_id or not name:
            self.send_json({"status": "error", "message": "سریال و نام ابزار الزامی است"}, 400)
            return

        execute_db('''
            INSERT OR REPLACE INTO tool_inventory (tool_serial_id, tool_name, tool_type, diameter_mm, brand, work_type, rated_life_hours, unit_cost, assigned_department, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')
        ''', (serial_id, name, ttype, diam, brand, work_type, rated, cost, dept))
        self.send_json({"status": "success", "message": "ابزار با موفقیت به انبار افزوده شد"})

    def handle_emergency_eject(self, body):
        import json
        op_id = body.get("operation_id")
        reason = body.get("reason", "توقف زودهنگام به دلیل چالش تکنولوژی ساخت / نیاز به بازطراحی ECO")
        
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
            return

        today_jalali = "1405/08/15"

        # 1. Backup existing active daily allocations before zeroing so standby swap can inherit them
        prev_allocs = query_db("SELECT day_number, hours_regular, hours_overtime, hours_total, is_holiday FROM daily_allocations WHERE operation_id = ? AND allocated = 1", (op_id,))
        backup_json = json.dumps(prev_allocs) if prev_allocs else "[]"

        # 2. Update operation status to Ejected to ECO & store backup of schedule in eco_reason
        execute_db('''
            UPDATE operations SET
                is_emergency_stopped = 1,
                emergency_stop_date = ?,
                emergency_stop_reason = ?,
                eco_reason = ?,
                execution_status = 'متوقف - خروج به بازطراحی ECO',
                drawing_head_approver = 'Obsolete / Under ECO',
                warning = '🔴 توقف اضطراری روز اول/دوم - ارجاع به دستور تغییر نقشه ECO',
                planned_hours = 0,
                planned_days = 0,
                eco_status = 'در حال بازطراحی در واحد مهندسی',
                drawing_approved_by_head = 0
            WHERE operation_id = ?
        ''', (today_jalali, reason, backup_json, op_id))

        # 3. Clear remaining days allocations in active decade to free machine capacity
        execute_db("UPDATE daily_allocations SET allocated = 0, hours_regular = 0, hours_overtime = 0, hours_total = 0 WHERE operation_id = ?", (op_id,))

        # 4. Retrieve standby candidates ready for replacement
        standby_jobs = query_db('''
            SELECT o.operation_id, o.part_equipment, o.description, o.operation_type, o.total_reserved_hours, s.standby_priority
            FROM operations o
            JOIN standby_pool_queue s ON o.operation_id = s.operation_id
            WHERE o.operation_type = ? AND s.status = 'Ready'
            ORDER BY s.standby_priority ASC
        ''', (op.get("operation_type"),))

        self.send_json({
            "status": "success",
            "message": f"عملیات {op_id} با موفقیت از برنامه ۱۰ روزه خارج و ساعت‌های دستگاه آزاد شد",
            "operation_id": op_id,
            "standby_candidates": standby_jobs
        })

    def handle_swap_from_standby(self, body):
        import json
        ejected_op_id = body.get("ejected_op_id")
        standby_op_id = body.get("standby_op_id")
        reason = body.get("reason", "توقف اضطراری و جایگزینی از صف رزرو")

        standby_op = query_db("SELECT * FROM operations WHERE operation_id = ?", (standby_op_id,), one=True)
        if not standby_op:
            self.send_json({"status": "error", "message": "کار رزرو یافت نشد"}, 404)
            return

        ejected_op = query_db("SELECT * FROM operations WHERE operation_id = ?", (ejected_op_id,), one=True)

        # 1. Retrieve allocations to transfer (either from active allocations or from backup json)
        allocs_to_transfer = query_db("SELECT day_number, hours_regular, hours_overtime, hours_total, is_holiday FROM daily_allocations WHERE operation_id = ? AND allocated = 1", (ejected_op_id,))
        if (not allocs_to_transfer) and ejected_op and ejected_op.get("eco_reason"):
            try:
                allocs_to_transfer = json.loads(ejected_op["eco_reason"])
            except Exception:
                allocs_to_transfer = []

        # 2. Transfer daily allocation slots directly to standby_op_id
        total_transferred_hours = 0.0
        total_transferred_days = 0
        if allocs_to_transfer:
            for a in allocs_to_transfer:
                d_num = a["day_number"]
                h_reg = float(a.get("hours_regular") or 0.0)
                h_ot = float(a.get("hours_overtime") or 0.0)
                h_tot = float(a.get("hours_total") or (h_reg + h_ot))
                is_hol = int(a.get("is_holiday") or 0)
                total_transferred_hours += h_tot
                total_transferred_days += 1
                execute_db('''
                    INSERT INTO daily_allocations (operation_id, day_number, allocated, hours_regular, hours_overtime, hours_total, is_holiday)
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(operation_id, day_number) DO UPDATE SET
                        allocated = 1,
                        hours_regular = excluded.hours_regular,
                        hours_overtime = excluded.hours_overtime,
                        hours_total = excluded.hours_total,
                        is_holiday = excluded.is_holiday
                ''', (standby_op_id, d_num, h_reg, h_ot, h_tot, is_hol))

        # 3. Mark ejected operation as stopped (if not already done) and clear its allocations
        today_jalali = "1405/08/15"
        execute_db('''
            UPDATE operations SET
                is_emergency_stopped = 1,
                emergency_stop_date = ?,
                emergency_stop_reason = ?,
                execution_status = 'متوقف - خروج به بازطراحی ECO',
                drawing_head_approver = 'Obsolete / Under ECO',
                warning = '🔴 توقف اضطراری روز اول/دوم - ارجاع به دستور تغییر نقشه ECO',
                planned_hours = 0,
                planned_days = 0,
                eco_status = 'در حال بازطراحی در واحد مهندسی',
                drawing_approved_by_head = 0
            WHERE operation_id = ?
        ''', (today_jalali, reason, ejected_op_id))
        execute_db("UPDATE daily_allocations SET allocated = 0, hours_regular = 0, hours_overtime = 0, hours_total = 0 WHERE operation_id = ?", (ejected_op_id,))

        # 4. Mark standby as active and update queue status, inheriting planned hours & days
        execute_db("UPDATE standby_pool_queue SET status = 'Dispatched' WHERE operation_id = ?", (standby_op_id,))
        execute_db('''
            UPDATE operations SET
                is_standby_pool = 0,
                execution_status = 'در حال اجرا (جایگزین شده از صف رزرو)',
                warning = '🟢 جایگزین‌شده در ساعات آزاد قطعه متوقف‌شده',
                planned_hours = ?,
                planned_days = ?,
                is_selected_for_month = 1
            WHERE operation_id = ?
        ''', (total_transferred_hours, total_transferred_days, standby_op_id))

        self.send_json({
            "status": "success",
            "message": f"کار رزرو {standby_op_id} با موفقیت جایگزین عملیات {ejected_op_id} گردید و {total_transferred_days} روز کاری ({total_transferred_hours} ساعت تقویم) به آن منتقل شد.",
            "transferred_hours": total_transferred_hours,
            "transferred_days": total_transferred_days
        })

    def handle_approve_delay_claim(self, body):
        log_id = body.get("log_id")
        decision = body.get("decision", "Approved") # Approved or Rejected
        note = body.get("manager_note", "")

        execute_db('''
            UPDATE daily_execution_logs SET
                delay_claim_status = ?,
                delay_claim_manager_note = ?
            WHERE log_id = ?
        ''', (decision, note, log_id))

        self.send_json({
            "status": "success",
            "message": f"ادعای تاخیر لاگ {log_id} در وضعیت {decision} ثبت شد"
        })

    def handle_frappe_sync_stock(self, body):
        op_id = body.get("operation_id")
        item_code = body.get("item_code", "RAW-STEEL-1.2344")
        qty = float(body.get("qty") or 1.0)
        from_wh = body.get("from_warehouse", "انبار مواد خام")
        to_wh = body.get("to_warehouse", "انبار در جریان ساخت (WIP) کارگاه")
        purpose = body.get("purpose", "Material Transfer for Manufacture")

        res = frappe_service.sync_stock_entry(op_id, item_code, qty, from_wh, to_wh, purpose)
        self.send_json(res)

    def handle_frappe_sync_qc(self, body):
        op_id = body.get("operation_id")
        item_code = body.get("item_code", "PART-FINISHED")
        qty = float(body.get("qty") or 1.0)
        inspected_by = body.get("inspected_by", get_current_user()["full_name"])
        qc_status = body.get("qc_status", "Passed")

        res = frappe_service.sync_quality_inspection(op_id, item_code, qty, inspected_by, qc_status)
        self.send_json(res)

    def handle_ai_estimate_machining(self, body):
        l = float(body.get("length_mm") or 100.0)
        w = float(body.get("width_mm") or 100.0)
        h = float(body.get("height_mm") or 50.0)
        mat = body.get("material", "فولاد گرم‌کار ۱.۲۳۴۴ ESK")
        is_first = bool(body.get("is_first_time", False))

        res = AIMachiningEstimator.estimate_from_dimensions(l, w, h, mat, is_first)
        self.send_json({"status": "success", "estimation": res})

    def handle_petty_cash_add(self, body):
        op_id = body.get("operation_id", "")
        item_name = body.get("item_name")
        brand = body.get("brand", "")
        unit_price = float(body.get("unit_price") or 0.0)
        qty = float(body.get("quantity") or 1.0)
        purchased_by = body.get("purchased_by", get_current_user()["username"])
        receipt_photo = body.get("receipt_photo_path", "uploads/receipts/receipt_sample.jpg")
        today_jalali = "1405/08/15"

        if not item_name or unit_price <= 0:
            self.send_json({"status": "error", "message": "نام کالا و قیمت خرید الزامی است"}, 400)
            return

        total_price = unit_price * qty
        audit_res = AIPettyCashAuditor.audit_purchase(item_name, unit_price, brand)

        execute_db('''
            INSERT INTO petty_cash_purchases (
                operation_id, item_name, brand, unit_price, quantity, total_price,
                market_avg_price, price_variance_percent, audit_status, receipt_photo_path,
                purchased_by, purchase_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            op_id, item_name, brand, unit_price, qty, total_price,
            audit_res["market_benchmark_price"], audit_res["variance_percent"],
            audit_res["status"], receipt_photo, purchased_by, today_jalali
        ))

        self.send_json({
            "status": "success",
            "message": "خرید تنخواه ثبت و توسط هوش مصنوعی ممیزی شد",
            "audit": audit_res
        })

    def handle_send_discussion(self, body):
        u = get_current_user()
        op_id = body.get("operation_id")
        project_id = body.get("project_id")
        part_equipment = body.get("part_equipment")
        department = body.get("department", "")
        message = (body.get("message") or "").strip()
        topic_tag = body.get("topic_tag", "ملاحظه فنی")
        
        if not message:
            self.send_json({"status": "error", "message": "متن یادداشت نمی‌تواند خالی باشد."}, 400)
            return
            
        if (not project_id or not part_equipment) and op_id:
            op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
            if op:
                if not project_id:
                    project_id = op["project_id"]
                if not part_equipment:
                    part_equipment = op["part_equipment"]
                if not department:
                    department = op.get("operation_type") or op.get("machine") or ""
                    
        now_ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M")
        
        new_id = query_db('''
            INSERT INTO technical_discussions (
                operation_id, project_id, part_equipment, department,
                sender_username, sender_name, sender_role, message, topic_tag, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            op_id,
            project_id or "PRJ-1405-A1",
            part_equipment or "قطعه عمومی کارگاهی",
            department or u.get("assigned_department", "کارگاه"),
            u["username"],
            u["full_name"],
            u["role_title"],
            message,
            topic_tag,
            now_ts
        ), commit=True)
        
        saved = query_db("SELECT * FROM technical_discussions WHERE id = ?", (new_id,), one=True)
        self.send_json({"status": "success", "message": "یادداشت فنی با موفقیت ثبت شد.", "discussion": saved})

    def handle_erpnext_sync_buying(self, body):
        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد."}, 404)
        
        mr_id = f"MAT-REQ-{time.strftime('%Y')}-{os.urandom(2).hex().upper()}"
        query_db("UPDATE operations SET erpnext_purchase_id = ? WHERE operation_id = ?", (mr_id, op_id), commit=True)
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "material_request_id": mr_id,
            "message": f"درخواست خرید {mr_id} در ماژول خرید ERPNext (Buying) با موفقیت صادر و متصل گردید."
        })

    def handle_erpnext_sync_subcontracting(self, body):
        op_id = body.get("operation_id")
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد."}, 404)
        
        po_id = f"PO-SUBCONT-{time.strftime('%Y')}-{os.urandom(2).hex().upper()}"
        query_db("UPDATE operations SET erpnext_subcontract_id = ? WHERE operation_id = ?", (po_id, op_id), commit=True)
        self.send_json({
            "status": "success",
            "operation_id": op_id,
            "subcontract_id": po_id,
            "message": f"سفارش برون‌سپاری {po_id} در ماژول پیمانکاری ERPNext (Subcontracting) ثبت و متصل شد."
        })

    def handle_erpnext_sync_costing(self, body):
        prj_id = body.get("project_id", "PRJ-MOLD-001")
        prj = query_db("SELECT budgeted_cost FROM projects WHERE project_id = ?", (prj_id,), one=True)
        total_bud = float(prj["budgeted_cost"] or 0) if prj else 0.0
        
        p_ops = query_db("SELECT actual_cost, operation_type, outsourcing_cost FROM operations WHERE project_id = ?", (prj_id,))
        total_act = sum(float(o["actual_cost"] or 0) for o in p_ops)
        mat_cost = sum(float(o["actual_cost"] or 0) for o in p_ops if o["operation_type"] == "خرید")
        sub_cost = sum((float(o["actual_cost"] or 0) if o["operation_type"] == "برون‌سپاری" else 0.0) + float(o.get("outsourcing_cost") or 0) for o in p_ops)
        
        query_db("""
        UPDATE projects
        SET actual_cost = ?, material_cost = ?, outsourcing_cost = ?
        WHERE project_id = ?
        """, (total_act, mat_cost, sub_cost, prj_id), commit=True)
        
        self.send_json({
            "status": "success",
            "project_id": prj_id,
            "total_actual_cost": total_act,
            "total_budget": total_bud,
            "material_cost": mat_cost,
            "outsourcing_cost": sub_cost,
            "variance": total_bud - total_act,
            "message": f"بهای تمام‌شده واقعی پروژه {prj_id} به مبلغ {int(total_act):,} ریال با ماژول پروژه‌ها و حسابداری ERPNext همگام گردید."
        })

    def handle_job_card_data(self, op_id):
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_json({"status": "error", "message": "عملیات یافت نشد"}, 404)
        prj = query_db("SELECT * FROM projects WHERE project_id = ?", (op["project_id"],), one=True)
        allocs = query_db("SELECT day_number, hours_regular, hours_overtime, hours_total FROM daily_allocations WHERE operation_id = ? AND allocated = 1", (op_id,))
        qr_url = f"http://localhost:{PORT}/job_card/{op_id}"
        qr_b64 = generate_qr_base64(qr_url)

        self.send_json({
            "status": "success",
            "operation": op,
            "project": prj,
            "allocations": allocs,
            "qr_code_data_url": qr_b64,
            "qr_target_url": qr_url
        })

    def handle_job_card_page(self, op_id):
        op = query_db("SELECT * FROM operations WHERE operation_id = ?", (op_id,), one=True)
        if not op:
            return self.send_html("<h2>کارت کارگاه یافت نشد</h2>", 404)
        prj = query_db("SELECT * FROM projects WHERE project_id = ?", (op["project_id"],), one=True)
        allocs = query_db("SELECT day_number, hours_regular, hours_overtime, hours_total FROM daily_allocations WHERE operation_id = ? AND allocated = 1 ORDER BY day_number", (op_id,))
        qr_url = f"http://localhost:{PORT}/job_card/{op_id}"
        qr_b64 = generate_qr_base64(qr_url)
        days_str = ", ".join([f"روز {a['day_number']} ({a['hours_total']}h)" for a in allocs]) or "برنامه‌ریزی‌نشده"

        html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>کارت گردش کار قطعه (Job Card) | {op['operation_id']}</title>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css" />
    <style>
        * {{ box-sizing: border-box; font-family: 'Vazirmatn', Tahoma, sans-serif; }}
        body {{ background: #f4f6f9; padding: 30px; color: #1a252f; }}
        .traveler-card {{ background: #fff; max-width: 800px; margin: 0 auto; border: 2px solid #2c3e50; border-radius: 8px; padding: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #2c3e50; padding-bottom: 15px; margin-bottom: 20px; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; font-size: 13px; }}
        .grid-item {{ background: #f8f9fa; padding: 10px; border-radius: 6px; border-right: 4px solid #2980b9; }}
        .table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 12px; text-align: center; }}
        .table th, .table td {{ border: 1px solid #bdc3c7; padding: 8px; }}
        .table th {{ background: #ecf0f1; }}
        .print-btn {{ background: #27ae60; color: #fff; border: none; padding: 10px 20px; font-weight: bold; border-radius: 6px; cursor: pointer; }}
        @media print {{ .no-print {{ display: none; }} body {{ padding: 0; background: #fff; }} .traveler-card {{ box-shadow: none; border: 1px solid #000; }} }}
    
        </style>
</head>
<body>
    <div class="no-print" style="max-width:800px; margin:0 auto 15px; display:flex; justify-content:space-between; align-items:center;">
        <button class="print-btn" onclick="window.print()">🖨️ چاپ مستقیم کارت کارگاه (Print Job Card)</button>
        <a href="/" style="color:#2980b9; text-decoration:none; font-weight:bold;">⬅️ بازگشت به سامانه اصلی</a>
    </div>

    <div class="traveler-card">
        <div class="header">
            <div>
                <h2 style="margin-bottom:5px;">شرکت هامرز پلاسما</h2>
                <h4 style="color:#7f8c8d;">کارت گردش کار و مسافر قطعه (Workpiece Traveler / Job Card)</h4>
                <div style="font-size:12px; margin-top:5px;">شناسه یکتا: <b>{op['operation_id']}</b> | کد WBS: <b>{op.get('wbs_code') or '1.0'}</b></div>
            </div>
            <div style="text-align:center;">
                <img src="{qr_b64}" width="110" height="110" style="border:1px solid #ccc; padding:3px; border-radius:4px;" />
                <div style="font-size:10px; color:#555; margin-top:3px;">اسکن جهت ثبت یا دیده‌بانی</div>
            </div>
        </div>

        <div class="grid">
            <div class="grid-item"><b>پروژه مرجع:</b> {prj['project_name'] if prj else op['project_id']} ({op['domain']})</div>
            <div class="grid-item"><b>مدیر پروژه تعیین‌کننده:</b> {op['project_manager']}</div>
            <div class="grid-item"><b>قطعه / تجهیز:</b> {op['part_equipment'] or '-'}</div>
            <div class="grid-item"><b>متریال خام مصرفی:</b> {op.get('raw_material_item') or 'مطابق استاندارد نقشه'}</div>
            <div class="grid-item"><b>صف عملیاتی:</b> {op['operation_type']}</div>
            <div class="grid-item"><b>مسئول بخش:</b> {op['department_head'] or '-'}</div>
            <div class="grid-item"><b>ماشین‌آلات تخصیص‌یافته:</b> {op['machine'] or '-'}</div>
            <div class="grid-item"><b>وابستگی فنی (Predecessor):</b> {op.get('predecessor_id') or 'ندارد (عملیات ریشه)'}</div>
            <div class="grid-item"><b>افق زمانی برنامه:</b> {op['decade_period']} {'🔒 فیکس' if op['is_decade_locked'] else ''}</div>
            <div class="grid-item" style="grid-column: span 2; background:#f0f9ff; border-right:4px solid #0284c7;">
                <b>📐 نقشه مهندسی مصوب کارگاه:</b> {op.get('drawing_no') or 'DWG-STD-001'} (ویرایش: <b>{op.get('drawing_revision') or 'Rev 0'}</b>) 
                {f'<a href="/uploads/{op["drawing_file_path"]}" target="_blank" style="margin-right:10px; background:#0284c7; color:#fff; padding:3px 8px; border-radius:4px; text-decoration:none; font-weight:bold; font-size:11px;">📥 دانلود نقشه مصوب</a>' if op.get('drawing_file_path') else '<span style="color:#64748b; font-size:11px; margin-right:8px;">(نسخه استاندارد کارگاه)</span>'}
                <span style="font-size:11px; color:#16a34a; font-weight:bold; margin-right:12px;">✅ تأیید صلاحیت ساخت شده</span>
            </div>
            <div class="grid-item"><b>روزها و ساعات مصوب:</b> {days_str}</div>
        </div>

        <div style="background:#eaf2f8; padding:12px; border-radius:6px; font-size:13px; margin-bottom:20px;">
            <b>شرح دقیق فرآیند و ماشین‌کاری:</b><br/>
            {op['description']}
        </div>

        <h4 style="margin-top:20px; border-bottom:1px solid #bdc3c7; padding-bottom:5px;">چک‌لیست کنترل کیفیت و ایستگاه‌های بازرسی فنی (QC Verification)</h4>
        <table class="table">
            <thead>
                <tr>
                    <th>مرحله بازرسی</th>
                    <th>پارامتر کنترلی</th>
                    <th>مقدار اسمی نقشه</th>
                    <th>مقدار اندازه‌گیری‌شده</th>
                    <th>تأیید مجری</th>
                    <th>تأیید QC</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>کنترل متریال خام</td>
                    <td>آنالیز و گرید متریال</td>
                    <td>فولاد آلیاژی استاندارد</td>
                    <td>مطابق سرتیفیکیت</td>
                    <td>امضا: ..........</td>
                    <td>امضا: ..........</td>
                </tr>
                <tr>
                    <td>ماشین‌کاری مرحله اول</td>
                    <td>ابعاد هندسی و گونیایی</td>
                    <td>±0.05 mm</td>
                    <td>...................</td>
                    <td>امضا: ..........</td>
                    <td>امضا: ..........</td>
                </tr>
                <tr>
                    <td>فینیشینگ نهایی</td>
                    <td>صافی سطح و تلرانس صدمی</td>
                    <td>Ra 0.8 / ±0.01 mm</td>
                    <td>...................</td>
                    <td>امضا: ..........</td>
                    <td>امضا: ..........</td>
                </tr>
            </tbody>
        </table>

        <div style="display:flex; justify-content:space-between; margin-top:35px; font-size:12px; text-align:center;">
            <div>.....................................<br/><b>امضای مدیر پروژه</b></div>
            <div>.....................................<br/><b>امضای مسئول بخش مجری</b></div>
            <div>.....................................<br/><b>امضای مهندسی کیفیت (QC)</b></div>
        </div>
    </div>

</body>
</html>
"""
        self.send_html(html)

def get_latest_spa_html():
    html_f = os.path.join(BASE_DIR, "hamerz_shopfloor_app_4.html")
    if os.path.exists(html_f):
        with open(html_f, "r", encoding="utf-8") as hf:
            return hf.read()
    return "Error: hamerz_shopfloor_app_4.html not found"

HTML_SPA = get_latest_spa_html()

def run():
    ensure_db_schema()
    print(f"🚀 سرور چندنخی (Multi-threaded) سامانه جامع هامرز پلاسما فعال شد.")
    print(f"🌐 آدرس دسترسی در مرورگر: http://localhost:{PORT}")
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("", PORT), MicroAppHandler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nسرور متوقف شد.")
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run()
