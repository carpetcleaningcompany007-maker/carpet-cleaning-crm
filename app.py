import os
import sqlite3
import json
import smtplib
import ssl
import logging
import secrets
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, timedelta, datetime
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo
import re
import io
import csv
import zipfile
import html as html_lib
import base64
import urllib.parse
import urllib.request
import urllib.error
import uuid
import calendar as pycalendar

from flask import Flask, render_template, request, redirect, url_for, flash, session, g, Response, send_file, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("CRM_SECRET_KEY", "change-this-secret")
app.config["UPLOAD_FOLDER"] = os.environ.get("CRM_UPLOAD_FOLDER", os.path.join("static", "uploads"))
DB_PATH = os.environ.get("CRM_DB_PATH", "crm.db")
BACKUP_DIR = os.environ.get("CRM_BACKUP_DIR", "backups")
XERO_SCOPES = "offline_access accounting.contacts accounting.contacts.read accounting.transactions accounting.transactions.read accounting.settings.read"
XERO_AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_CONTACTS_URL = "https://api.xero.com/api.xro/2.0/Contacts"
XERO_INVOICES_URL = "https://api.xero.com/api.xro/2.0/Invoices"
XERO_PAYMENTS_URL = "https://api.xero.com/api.xro/2.0/Payments"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carpet_crm")


def uk_today():
    return datetime.now(ZoneInfo("Europe/London")).date()


@app.after_request
def add_website_form_cors_headers(response):
    if request.path in ("/website-form", "/api/website-form"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
    return response

AREA_OPTIONS = [
    "Function room","Hallway","Restaurant","Bar","Reception","Bedroom corridor",
    "Stairs and landing","Meeting room","Lounge","Spa area","Gym","Office",
    "Conference room","Dining area","Ballroom","Entrance lobby","Toilet area",
    "Staff room","Kitchen","Kitchen access area","Banqueting room",
    "Hard floor corridor","Hard floor washroom","Custom"
]

EXPENSE_CATEGORY_OPTIONS = [
    "Chemicals", "Fuel", "Wages", "Equipment", "Repairs", "Marketing", "Insurance",
    "Supplies", "Office", "Laundry", "Training", "Subcontractor", "Vehicle", "Other"
]


RECURRING_COLLECTION_OPTIONS = [
    "Direct Debit", "Standing Order", "Bank Transfer", "Card", "Cash", "Invoice", "Other"
]

PRICING_DEFAULTS = {
    "domestic": [
        {"id":"living","name":"Living Room","desc":"Main family room","price":79.0,"group":"Residential"},
        {"id":"bedroom","name":"Bedroom","desc":"Standard bedroom","price":37.0,"group":"Residential"},
        {"id":"dining","name":"Dining Room","desc":"Dining or breakfast room","price":57.0,"group":"Residential"},
        {"id":"boxroom","name":"Box Room","desc":"Small bedroom or office","price":27.0,"group":"Residential"},
        {"id":"study","name":"Study","desc":"Office or spare room","price":27.0,"group":"Residential"},
        {"id":"loungediner","name":"Lounge Diner","desc":"Open plan room","price":99.0,"group":"Residential"},
        {"id":"stairslanding","name":"Stairs and Landing","desc":"Combined stairs and landing","price":75.0,"group":"Residential"},
        {"id":"rug_small","name":"Small Rug","desc":"Small rug clean","price":25.0,"group":"Rugs"},
        {"id":"rug_medium","name":"Medium Rug","desc":"Medium rug clean","price":35.0,"group":"Rugs"},
        {"id":"rug_large","name":"Large Rug","desc":"Large rug clean","price":49.0,"group":"Rugs"},
        {"id":"sofa_2","name":"2 Seat Sofa","desc":"Fabric or leather","price":80.0,"group":"Upholstery"},
        {"id":"sofa_3","name":"3 Seat Sofa","desc":"Fabric or leather","price":120.0,"group":"Upholstery"},
        {"id":"seat","name":"Sofa Per Seat","desc":"Useful for corner sofas","price":40.0,"group":"Upholstery"},
        {"id":"armchair","name":"Armchair","desc":"Single chair","price":30.0,"group":"Upholstery"}
    ],
    "hotelRooms": {"rotary":25.0,"hybrid":30.0,"hwe":35.0},
    "rotaryBands": [
        {"min":0,"max":150,"rate":2.50,"label":"Up to 150 m²"},
        {"min":151,"max":399,"rate":2.20,"label":"151 to 399 m²"},
        {"min":400,"max":999,"rate":2.00,"label":"400 to 999 m²"},
        {"min":1000,"max":99999999,"rate":1.80,"label":"1000+ m²"},
    ],
    "hybridBands": [
        {"min":0,"max":150,"rate":2.90,"label":"Up to 150 m²"},
        {"min":151,"max":300,"rate":2.60,"label":"151 to 300 m²"},
        {"min":301,"max":99999999,"rate":2.40,"label":"301+ m²"},
    ],
    "hweBands": [
        {"min":0,"max":150,"rate":3.40,"label":"Up to 150 m²"},
        {"min":151,"max":300,"rate":3.10,"label":"151 to 300 m²"},
        {"min":301,"max":99999999,"rate":2.80,"label":"301+ m²"},
    ],
    "hardfloorBands": [
        {"min":0,"max":150,"rate":2.80,"label":"Up to 150 m²"},
        {"min":151,"max":300,"rate":2.50,"label":"151 to 300 m²"},
        {"min":301,"max":99999999,"rate":2.20,"label":"301+ m²"},
    ],
}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def q(sql, params=(), one=False):
    cur = db().execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows[0] if one and rows else (None if one else rows)

def run(sql, params=()):
    cur = db().execute(sql, params)
    db().commit()
    return cur.lastrowid

def settings():
    return q("SELECT * FROM settings WHERE id=1", one=True)

def pricing():
    row = q("SELECT data_json FROM pricing_config WHERE id=1", one=True)
    return json.loads(row["data_json"]) if row and row["data_json"] else PRICING_DEFAULTS

def save_pricing(data):
    run("UPDATE pricing_config SET data_json=? WHERE id=1", (json.dumps(data),))

def save_upload(field):
    f = request.files.get(field)
    if not f or not f.filename:
        return ""
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    name = datetime.now().strftime("%Y%m%d%H%M%S_") + secure_filename(f.filename)
    f.save(os.path.join(app.config["UPLOAD_FOLDER"], name))
    return name


def is_password_hash(value):
    value = (value or "").strip()
    return value.startswith("pbkdf2:") or value.startswith("scrypt:")


def verify_password(stored_password, candidate_password):
    stored_password = stored_password or ""
    candidate_password = candidate_password or ""
    if is_password_hash(stored_password):
        try:
            return check_password_hash(stored_password, candidate_password)
        except Exception:
            return False
    return stored_password == candidate_password


def normalize_password_for_storage(password_value):
    password_value = (password_value or "").strip()
    if not password_value:
        return ""
    if is_password_hash(password_value):
        return password_value
    return generate_password_hash(password_value)


def merge_message_text(text, customer=None):
    s = settings()
    full_name = ""
    first_name = ""
    customer_email = ""
    customer_phone = ""
    if customer:
        first_name = customer["first_name"] or ""
        last_name = customer["last_name"] or ""
        full_name = (first_name + " " + last_name).strip()
        customer_email = customer["email"] or ""
        customer_phone = customer["phone"] or ""
    result = str(text or "")
    replacements = {
        "{{name}}": full_name,
        "{{first_name}}": first_name,
        "{{business_name}}": s["business_name"] or "",
        "{{phone}}": s["phone"] or "",
        "{{review_link}}": s["review_link"] or "",
        "{{website}}": s["website"] or "",
        "{{email}}": customer_email,
        "{{customer_email}}": customer_email,
        "{{customer_phone}}": customer_phone,
    }
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def build_email_html(body, footer=""):
    body = (body or "").strip()
    footer = (footer or "").strip()
    if "<" in body and ">" in body:
        content = body
    else:
        content = body.replace("\n", "<br>")
    return content + ((("<br><br>" + footer) if footer else ""))


def is_valid_email(value):
    value = (value or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def clean_str(value):
    return (value or "").strip()


def normalize_phone(value):
    phone = re.sub(r"[^0-9+]", "", clean_str(value))
    if phone.startswith("00"):
        return "+" + phone[2:]
    if phone.startswith("0") and len(phone) >= 10:
        return "+44" + phone[1:]
    return phone


def parse_sms_keywords(value, defaults=''):
    raw = str(value or defaults or '')
    parts = [x.strip().upper() for x in raw.replace(';', ',').split(',') if x.strip()]
    return list(dict.fromkeys(parts))


def sms_message_category(message_category='', template_row=None):
    if template_row and not message_category:
        try:
            message_category = template_row['category'] or ''
        except Exception:
            message_category = ''
    return clean_str(message_category)


def is_marketing_sms_category(message_category=''):
    value = clean_str(message_category).lower()
    return value in {'marketing', 'campaign', 'reactivation', 'review', 'reviews', 'promo', 'promotion'}


def add_sms_compliance_text(body, message_category=''):
    text = clean_str(body)
    s = settings()
    append_marketing_notice = int(s['sms_append_opt_out_on_marketing'] or 0) == 1 if s else False
    notice = clean_str(s['sms_marketing_opt_out_notice'] or '') if s else ''
    if append_marketing_notice and is_marketing_sms_category(message_category) and notice:
        upper_text = text.upper()
        if 'REPLY STOP' not in upper_text and notice.upper() not in upper_text:
            text = (text + "\n\n" + notice).strip() if text else notice
    return text


def inbound_sms_keyword_action(body_text=''):
    s = settings()
    body_upper = clean_str(body_text).upper()
    stop_words = parse_sms_keywords(s['sms_stop_keywords'] if s else '', 'STOP,STOPALL,UNSUBSCRIBE,CANCEL,END,QUIT')
    start_words = parse_sms_keywords(s['sms_start_keywords'] if s else '', 'START,UNSTOP,SUBSCRIBE')
    if body_upper in stop_words:
        return 'stop'
    if body_upper in start_words:
        return 'start'
    return ''


def parse_money(value, default=0.0):
    raw = clean_str(value)
    if not raw:
        return float(default)
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        raise ValueError("Please enter a valid number.")


def append_note(existing_text, extra_text):
    existing_text = clean_str(existing_text)
    extra_text = clean_str(extra_text)
    if not extra_text:
        return existing_text
    if not existing_text:
        return extra_text
    return existing_text + "\n\n" + extra_text


def find_existing_customer_id(first_name="", last_name="", email="", phone="", postcode=""):
    email = clean_str(email).lower()
    phone = normalize_phone(phone)
    first_name = clean_str(first_name)
    last_name = clean_str(last_name)
    postcode = clean_str(postcode)
    if email:
        row = q("SELECT id FROM customers WHERE archived_at IS NULL AND lower(IFNULL(email,''))=? ORDER BY id DESC LIMIT 1", (email,), one=True)
        if row:
            return row["id"]
    if phone:
        rows = q("SELECT id, phone FROM customers WHERE archived_at IS NULL AND IFNULL(phone,'')<>'' ORDER BY id DESC")
        for row in rows:
            if normalize_phone(row["phone"]) == phone:
                return row["id"]
    if first_name and last_name and postcode:
        row = q("""SELECT id FROM customers
                   WHERE archived_at IS NULL AND lower(IFNULL(first_name,''))=? AND lower(IFNULL(last_name,''))=? AND lower(IFNULL(postcode,''))=?
                   ORDER BY id DESC LIMIT 1""", (first_name.lower(), last_name.lower(), postcode.lower()), one=True)
        if row:
            return row["id"]
    return None


def import_customer_library_row(row):
    def pick(*names):
        for name in names:
            if name in row and row[name] is not None:
                value = clean_str(row[name])
                if value:
                    return value
        return ""

    full_name = pick("name", "customer_name", "full_name", "Name", "Customer Name", "Full Name")
    first_name = pick("first_name", "firstname", "FirstName", "first", "First Name")
    last_name = pick("last_name", "lastname", "LastName", "surname", "Last Name")
    if full_name and not (first_name or last_name):
        first_name, last_name = split_customer_name(full_name)
    first_name = first_name or "Customer"
    last_name = last_name or "Imported"
    phone = pick("phone", "mobile", "telephone", "Phone", "Mobile", "Telephone")
    email = pick("email", "Email", "email_address", "EmailAddress")
    address = pick("address", "Address", "full_address", "Full Address")
    town = pick("town", "Town", "city", "City")
    postcode = pick("postcode", "Postcode", "postal_code", "PostalCode")
    source = pick("source", "Source") or "Customer library import"
    tags = pick("tags", "Tags")
    notes = pick("notes", "Notes", "job_notes", "Job Notes")
    xero_contact_id = pick("xero_contact_id", "XeroContactID", "ContactID", "contact_id")

    if xero_contact_id:
        existing = q("SELECT id FROM customers WHERE IFNULL(xero_contact_id,'')=? ORDER BY id DESC LIMIT 1", (xero_contact_id,), one=True)
        customer_id = existing["id"] if existing else None
    else:
        customer_id = None
    if not customer_id:
        customer_id = find_existing_customer_id(first_name=first_name, last_name=last_name, email=email, phone=phone, postcode=postcode)

    if customer_id:
        run("""UPDATE customers
               SET first_name=COALESCE(NULLIF(?,''), first_name),
                   last_name=COALESCE(NULLIF(?,''), last_name),
                   phone=COALESCE(NULLIF(?,''), phone),
                   email=COALESCE(NULLIF(?,''), email),
                   address=COALESCE(NULLIF(?,''), address),
                   town=COALESCE(NULLIF(?,''), town),
                   postcode=COALESCE(NULLIF(?,''), postcode),
                   source=CASE WHEN IFNULL(source,'')='' THEN ? ELSE source END,
                   tags=CASE
                        WHEN ?='' THEN tags
                        WHEN IFNULL(tags,'')='' THEN ?
                        WHEN tags LIKE '%' || ? || '%' THEN tags
                        ELSE tags || ', ' || ?
                   END,
                   notes=CASE
                        WHEN ?='' THEN notes
                        WHEN IFNULL(notes,'')='' THEN ?
                        WHEN notes LIKE '%' || ? || '%' THEN notes
                        ELSE notes || char(10) || char(10) || ?
                   END,
                   xero_contact_id=COALESCE(NULLIF(?,''), xero_contact_id),
                   xero_contact_synced_at=CASE WHEN ?<>'' THEN datetime('now') ELSE xero_contact_synced_at END
               WHERE id=?""", (
            first_name, last_name, phone, email, address, town, postcode,
            source,
            tags, tags, tags, tags,
            notes, notes, notes, notes,
            xero_contact_id, xero_contact_id,
            customer_id,
        ))
        return "updated", customer_id

    customer_id = run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,tags,notes,xero_contact_id,xero_contact_synced_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ?<>'' THEN datetime('now') ELSE '' END)""", (
        first_name, last_name, phone, email, address, town, postcode, source, tags, notes,
        xero_contact_id, xero_contact_id,
    ))
    return "created", customer_id


def import_customer_library_from_db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "customers" not in tables:
            raise RuntimeError("That database does not contain a customers table.")
        rows = con.execute("SELECT * FROM customers").fetchall()
        return import_customer_library_rows([dict(r) for r in rows])
    finally:
        con.close()


def import_customer_library_from_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError("The CSV did not contain any customer rows.")
    return import_customer_library_rows(rows)


def import_customer_library_rows(rows):
    created = updated = skipped = failed = 0
    for row in rows:
        try:
            if not any(clean_str(v) for v in dict(row).values()):
                skipped += 1
                continue
            action, customer_id = import_customer_library_row(dict(row))
            if action == "created":
                created += 1
            else:
                updated += 1
            run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
                (customer_id, "Customer synced from customer library import.", ""))
        except Exception as exc:
            failed += 1
            logger.exception("Customer library row import failed")
    return {"created": created, "updated": updated, "skipped": skipped, "failed": failed, "total": len(rows)}


def archive_customer_record(customer_id):
    run("UPDATE customers SET archived_at=CURRENT_TIMESTAMP WHERE id=? AND archived_at IS NULL", (customer_id,))


def archive_quote_record(quote_id):
    quote = q("SELECT status, notes FROM quotes WHERE id=?", (quote_id,), one=True)
    if not quote:
        return
    notes = append_note(quote["notes"], f"Archived on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run("UPDATE quotes SET status='Archived', notes=? WHERE id=?", (notes, quote_id))


def archive_job_record(job_id):
    job = q("SELECT status, notes FROM jobs WHERE id=?", (job_id,), one=True)
    if not job:
        return
    notes = append_note(job["notes"], f"Archived on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run("UPDATE jobs SET status='Archived', notes=? WHERE id=?", (notes, job_id))


def archive_invoice_record(invoice_id):
    invoice = q("SELECT status, notes FROM invoices WHERE id=?", (invoice_id,), one=True)
    if not invoice:
        return
    notes = append_note(invoice["notes"], f"Archived on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run("UPDATE invoices SET status='Archived', notes=? WHERE id=?", (notes, invoice_id))


def restore_customer_record(customer_id):
    run("UPDATE customers SET archived_at=NULL WHERE id=?", (customer_id,))


def restore_quote_record(quote_id):
    quote = q("SELECT status, notes FROM quotes WHERE id=?", (quote_id,), one=True)
    if not quote:
        return
    notes = append_note(quote["notes"], f"Restored on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run("UPDATE quotes SET status='Draft', notes=? WHERE id=?", (notes, quote_id))


def restore_job_record(job_id):
    job = q("SELECT status, notes FROM jobs WHERE id=?", (job_id,), one=True)
    if not job:
        return
    notes = append_note(job["notes"], f"Restored on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run("UPDATE jobs SET status='Booked', notes=? WHERE id=?", (notes, job_id))


def restore_invoice_record(invoice_id):
    invoice = q("SELECT status, notes FROM invoices WHERE id=?", (invoice_id,), one=True)
    if not invoice:
        return
    notes = append_note(invoice["notes"], f"Restored on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run("UPDATE invoices SET status='Draft', notes=? WHERE id=?", (notes, invoice_id))


def list_scope_clause(table_name, scope, archived_column=None, status_column='status'):
    if scope == 'archived':
        if archived_column:
            return f"{table_name}.{archived_column} IS NOT NULL"
        return f"IFNULL({table_name}.{status_column},'') = 'Archived'"
    if scope == 'all':
        return '1=1'
    if archived_column:
        return f"{table_name}.{archived_column} IS NULL"
    return f"IFNULL({table_name}.{status_column},'') <> 'Archived'"


def export_rows_to_csv(filename_prefix, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    csv_data = output.getvalue()
    output.close()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename_prefix}_{stamp}.csv"}
    )


def create_backup_zip_bytes():
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(DB_PATH):
            zf.write(DB_PATH, arcname="crm.db")
        uploads_dir = app.config.get("UPLOAD_FOLDER") or os.path.join("static", "uploads")
        if os.path.isdir(uploads_dir):
            for root, _dirs, files in os.walk(uploads_dir):
                for name in files:
                    full_path = os.path.join(root, name)
                    arcname = os.path.relpath(full_path, start=os.getcwd())
                    zf.write(full_path, arcname=arcname)
    mem.seek(0)
    return mem


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def save_backup_snapshot():
    ensure_backup_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"crm_backup_{stamp}.zip"
    backup_path = os.path.join(BACKUP_DIR, filename)
    with open(backup_path, "wb") as f:
        f.write(create_backup_zip_bytes().getvalue())
    return backup_path


def list_backup_files():
    ensure_backup_dir()
    items = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not name.lower().endswith('.zip'):
            continue
        full_path = os.path.join(BACKUP_DIR, name)
        if not os.path.isfile(full_path):
            continue
        stat = os.stat(full_path)
        items.append({
            'name': name,
            'size_bytes': stat.st_size,
            'size_mb': round(stat.st_size / (1024 * 1024), 2),
            'created_at': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        })
    return items


def month_key_from_value(value):
    value = (value or '').strip()
    if not value:
        return ''
    if len(value) >= 7:
        return value[:7]
    return ''


def month_label(month_key):
    try:
        return datetime.strptime(month_key, '%Y-%m').strftime('%b %Y')
    except Exception:
        return month_key

def parse_iso_date(value):
    value = clean_str(value)
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    except Exception:
        return None

def recurring_frequency_options():
    return ["Weekly", "Fortnightly", "Monthly", "Quarterly", "Yearly"]


def recurring_collection_options():
    return list(RECURRING_COLLECTION_OPTIONS)


def next_due_date_for_frequency(start_obj, frequency):
    if not start_obj:
        return None
    frequency = (frequency or '').strip().lower()
    if frequency == 'weekly':
        return start_obj + timedelta(days=7)
    if frequency == 'fortnightly':
        return start_obj + timedelta(days=14)
    if frequency == 'monthly':
        month = start_obj.month + 1
        year = start_obj.year
        if month > 12:
            month = 1
            year += 1
        day = min(start_obj.day, [31,29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,31,30,31,30,31,31,30,31,30,31][month-1])
        return date(year, month, day)
    if frequency == 'quarterly':
        out = start_obj
        for _ in range(3):
            out = next_due_date_for_frequency(out, 'monthly')
        return out
    if frequency == 'yearly':
        try:
            return start_obj.replace(year=start_obj.year + 1)
        except ValueError:
            return start_obj.replace(month=2, day=28, year=start_obj.year + 1)
    return start_obj


def cashflow_snapshot():
    today = date.today()
    since = (today - timedelta(days=30)).isoformat()
    cash_in = q("SELECT ROUND(COALESCE(SUM(total),0),2) AS total FROM invoices WHERE IFNULL(status,'') <> 'Archived' AND lower(IFNULL(status,''))='paid' AND COALESCE(invoice_date,'') >= ?", (since,), one=True)['total']
    cash_out = q("SELECT ROUND(COALESCE(SUM(amount),0),2) AS total FROM expenses WHERE archived_at IS NULL AND COALESCE(expense_date,'') >= ?", (since,), one=True)['total']
    next_30 = (today + timedelta(days=30)).isoformat()
    due_income = q("SELECT ROUND(COALESCE(SUM(amount),0),2) AS total, COUNT(*) AS c FROM recurring_income WHERE archived_at IS NULL AND active=1 AND date(IFNULL(next_due_date,start_date)) <= date(?)", (next_30,), one=True)
    due_expenses = q("SELECT ROUND(COALESCE(SUM(amount),0),2) AS total, COUNT(*) AS c FROM recurring_expenses WHERE archived_at IS NULL AND active=1 AND date(IFNULL(next_due_date,start_date)) <= date(?)", (next_30,), one=True)
    return {
        'cash_in_last_30': round(float(cash_in or 0), 2),
        'cash_out_last_30': round(float(cash_out or 0), 2),
        'net_cashflow_last_30': round(float(cash_in or 0) - float(cash_out or 0), 2),
        'recurring_income_due_30_value': round(float(due_income['total'] or 0), 2),
        'recurring_income_due_30_count': int(due_income['c'] or 0),
        'recurring_expenses_due_30_value': round(float(due_expenses['total'] or 0), 2),
        'recurring_expenses_due_30_count': int(due_expenses['c'] or 0),
        'forecast_net_30_value': round(float(due_income['total'] or 0) - float(due_expenses['total'] or 0), 2),
    }


def recurring_income_plan_name(row):
    payer = clean_str(row['payer_name']) if row and 'payer_name' in row.keys() else ''
    if payer:
        return payer
    first_name = clean_str(row['first_name']) if row and 'first_name' in row.keys() else ''
    last_name = clean_str(row['last_name']) if row and 'last_name' in row.keys() else ''
    return (first_name + ' ' + last_name).strip()


def recurring_income_invoice_exists(recurring_id, invoice_date):
    row = q("SELECT id FROM invoices WHERE invoice_date=? AND notes LIKE ? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC LIMIT 1", (invoice_date, f'%recurring income #{recurring_id}%'), one=True)
    return row['id'] if row else None


def create_invoice_from_recurring_income(plan_row, invoice_date_obj, manual=False):
    invoice_date_obj = invoice_date_obj or date.today()
    existing_id = recurring_income_invoice_exists(plan_row['id'], invoice_date_obj.isoformat())
    if existing_id:
        return existing_id, False
    subtotal = round(float(plan_row['amount'] or 0), 2)
    include_vat = int(plan_row['include_vat'] or 0) == 1
    vat_rate = float(settings()['vat_rate'] or 0)
    vat = round(subtotal * vat_rate, 2) if include_vat else 0.0
    total = round(subtotal + vat, 2)
    payer_name = clean_str(plan_row['payer_name'])
    payment_rule = recurring_payment_rule_label(plan_row)
    invoice_status = invoice_status_for_recurring_plan(plan_row)
    note_prefix = ("Manually posted" if manual else "Auto posted") + f" from recurring income #{plan_row['id']} on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    notes = clean_str(plan_row['notes'])
    rule_note = f"Payment rule: {payment_rule}. Invoice status set to {invoice_status}."
    full_notes = note_prefix + "\n" + rule_note
    if notes:
        full_notes += "\n\n" + notes
    payload = {
        'lines': [{
            'item_name': clean_str(plan_row['description']) or 'Recurring Income',
            'method': clean_str(plan_row['collection_method']) or 'Recurring',
            'quantity': 1,
            'unit_price': subtotal,
            'line_total': subtotal,
            'group_name': 'Recurring Income'
        }],
        'include_vat': include_vat,
        'subtotal': subtotal,
        'vat': vat,
        'total': total,
        'raw_total': total
    }
    invoice_id = run("""INSERT INTO invoices(customer_id, job_id, quote_id, invoice_number, invoice_date, due_date, status, subtotal, vat, total, payload_json, notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
        plan_row['customer_id'], None, None, next_invoice_number(), invoice_date_obj.isoformat(), invoice_date_obj.isoformat(),
        invoice_status, subtotal, vat, total, json.dumps(payload), full_notes
    ))
    if payer_name and not plan_row['customer_id']:
        run("UPDATE invoices SET notes=? WHERE id=?", (full_notes + f"\n\nPayer: {payer_name}", invoice_id))
    log_recurring_income_history(plan_row, invoice_id, invoice_date_obj, invoice_status, subtotal, vat, total, manual=manual)
    return invoice_id, True


def expense_filter_clause(view_name, archived_column='archived_at'):
    view_name = (view_name or 'active').lower()
    if view_name == 'archived':
        return f"{archived_column} IS NOT NULL"
    if view_name == 'all':
        return '1=1'
    return f"{archived_column} IS NULL"


def expense_stage_label(invoice_row):
    reminder_count = int((invoice_row.get('reminder_count') if isinstance(invoice_row, dict) else invoice_row['reminder_count']) or 0)
    days_until_due = invoice_row.get('days_until_due') if isinstance(invoice_row, dict) else None
    is_overdue = bool(invoice_row.get('is_overdue')) if isinstance(invoice_row, dict) else False
    if reminder_count >= 2 or (is_overdue and days_until_due is not None and days_until_due <= -14):
        return 'Final Notice'
    if reminder_count >= 1 or (is_overdue and days_until_due is not None and days_until_due <= -7):
        return 'Second Reminder'
    if is_overdue:
        return 'First Reminder'
    return 'Upcoming Reminder'


def invoice_alert_rows(limit=None):
    rows = q("""SELECT invoices.*, customers.first_name || ' ' || customers.last_name AS customer_name,
                      customers.email AS customer_email, customers.phone AS customer_phone
               FROM invoices
               LEFT JOIN customers ON customers.id = invoices.customer_id
               WHERE IFNULL(invoices.status,'') <> 'Archived' AND lower(IFNULL(invoices.status,'')) <> 'paid'
               ORDER BY COALESCE(invoices.due_date,'9999-12-31') ASC, invoices.id DESC""")
    today = date.today()
    alerts = []
    for row in rows:
        due = parse_iso_date(row['due_date'])
        days_until_due = None
        is_overdue = False
        is_due_soon = False
        if due:
            days_until_due = (due - today).days
            is_overdue = days_until_due < 0
            is_due_soon = 0 <= days_until_due <= 7
        if not due or is_overdue or is_due_soon:
            item = dict(row)
            item['days_until_due'] = days_until_due
            item['is_overdue'] = is_overdue
            item['is_due_soon'] = is_due_soon
            item['status_label'] = 'Overdue' if is_overdue else ('Due Soon' if is_due_soon else 'No Due Date')
            item['reminder_subject'] = build_invoice_reminder_subject(item)
            item['reminder_body'] = build_invoice_reminder_body(item)
            alerts.append(item)
    overdue = [a for a in alerts if a['is_overdue']]
    due_soon = [a for a in alerts if a['is_due_soon']]
    no_due_date = [a for a in alerts if not a['due_date']]
    if limit:
        overdue = overdue[:limit]
        due_soon = due_soon[:limit]
        no_due_date = no_due_date[:limit]
    return {
        'overdue': overdue,
        'due_soon': due_soon,
        'no_due_date': no_due_date,
        'overdue_count': len([a for a in alerts if a['is_overdue']]),
        'due_soon_count': len([a for a in alerts if a['is_due_soon']]),
        'no_due_date_count': len([a for a in alerts if not a['due_date']]),
        'outstanding_total': round(sum(float(a['total'] or 0) for a in alerts), 2),
        'overdue_total': round(sum(float(a['total'] or 0) for a in alerts if a['is_overdue']), 2),
    }


def build_invoice_reminder_subject(invoice_row):
    invoice_number = invoice_row.get('invoice_number') if isinstance(invoice_row, dict) else invoice_row['invoice_number']
    stage = expense_stage_label(invoice_row)
    prefix = stage if stage != 'Upcoming Reminder' else 'Payment Reminder'
    return f"{prefix} for invoice {invoice_number or ''}".strip()


def build_invoice_reminder_body(invoice_row):
    customer_name = clean_str((invoice_row.get('customer_name') if isinstance(invoice_row, dict) else invoice_row['customer_name']) or 'there')
    invoice_number = clean_str((invoice_row.get('invoice_number') if isinstance(invoice_row, dict) else invoice_row['invoice_number']) or '')
    due_date = clean_str((invoice_row.get('due_date') if isinstance(invoice_row, dict) else invoice_row['due_date']) or '')
    total = float((invoice_row.get('total') if isinstance(invoice_row, dict) else invoice_row['total']) or 0)
    stage = expense_stage_label(invoice_row)
    reminder_count = int((invoice_row.get('reminder_count') if isinstance(invoice_row, dict) else invoice_row['reminder_count']) or 0)
    days_until_due = invoice_row.get('days_until_due') if isinstance(invoice_row, dict) else None
    if due_date:
        if days_until_due is not None and days_until_due >= 0:
            due_line = f"The invoice is due on {due_date}."
        else:
            due_line = f"The invoice was due on {due_date}."
    else:
        due_line = "This invoice is still showing as unpaid on our system."
    opening = {
        'Upcoming Reminder': f"Just a quick reminder that invoice {invoice_number} for £{total:.2f} is coming due.",
        'First Reminder': f"Just a quick reminder that invoice {invoice_number} for £{total:.2f} is still outstanding.",
        'Second Reminder': f"This is a further reminder that invoice {invoice_number} for £{total:.2f} is still outstanding.",
        'Final Notice': f"This is a final notice that invoice {invoice_number} for £{total:.2f} remains unpaid.",
    }.get(stage, f"Just a quick reminder that invoice {invoice_number} for £{total:.2f} is still outstanding.")
    closing = "Please let me know if you need a copy of the invoice or payment details."
    if stage == 'Final Notice':
        closing = "Please let me know today if you need a copy of the invoice or payment details."
    sent_note = f"This is reminder number {reminder_count + 1}." if stage != 'Upcoming Reminder' else ""
    return (
        f"Hi {customer_name}\n\n"
        f"{opening}\n\n"
        f"{due_line} If payment has already been sent, please ignore this message.\n\n"
        f"{sent_note}\n\n{closing}".strip()
    )


def build_reports_data(month_count=6):
    current = date.today().replace(day=1)
    months = []
    for _ in range(month_count):
        months.append(current.strftime('%Y-%m'))
        if current.month == 1:
            current = current.replace(year=current.year - 1, month=12)
        else:
            current = current.replace(month=current.month - 1)
    months = list(reversed(months))
    labels = [month_label(m) for m in months]

    quote_rows = q("SELECT quote_date, total, status FROM quotes WHERE IFNULL(status,'') <> 'Archived'")
    job_rows = q("SELECT job_date, amount, status FROM jobs WHERE IFNULL(status,'') <> 'Archived'")
    invoice_rows = q("SELECT invoice_date, due_date, total, vat, status FROM invoices WHERE IFNULL(status,'') <> 'Archived'")
    recurring_income_rows = q("""SELECT recurring_income.*, customers.first_name, customers.last_name
                                FROM recurring_income
                                LEFT JOIN customers ON customers.id = recurring_income.customer_id
                                WHERE recurring_income.archived_at IS NULL""")

    quote_counts = {m: 0 for m in months}
    quote_values = {m: 0.0 for m in months}
    job_values = {m: 0.0 for m in months}
    invoice_values = {m: 0.0 for m in months}
    paid_invoice_values = {m: 0.0 for m in months}
    net_invoice_values = {m: 0.0 for m in months}
    net_paid_invoice_values = {m: 0.0 for m in months}
    expense_values = {m: 0.0 for m in months}
    recurring_income_values = {m: 0.0 for m in months}
    expense_by_category = {}
    expense_by_supplier = {}
    income_by_method = {}

    today = date.today()
    outstanding_total = 0.0
    overdue_total = 0.0
    overdue_count = 0

    for row in quote_rows:
        m = month_key_from_value(row['quote_date'])
        if m in quote_counts:
            quote_counts[m] += 1
            quote_values[m] += float(row['total'] or 0)

    for row in job_rows:
        m = month_key_from_value(row['job_date'])
        if m in job_values:
            job_values[m] += float(row['amount'] or 0)

    for row in invoice_rows:
        m = month_key_from_value(row['invoice_date'])
        val = float(row['total'] or 0)
        vat_val = float(row['vat'] or 0)
        net_val = max(0.0, val - vat_val)
        status = (row['status'] or '').strip().lower()
        if m in invoice_values:
            invoice_values[m] += val
            net_invoice_values[m] += net_val
            if status == 'paid':
                paid_invoice_values[m] += val
                net_paid_invoice_values[m] += net_val
        if status != 'paid':
            outstanding_total += val
            due = parse_iso_date(row['due_date'])
            if due and due < today:
                overdue_total += val
                overdue_count += 1

    for row in q("SELECT * FROM expenses WHERE archived_at IS NULL"):
        d = parse_iso_date(row['expense_date']) or parse_iso_date(row['created_at'])
        if not d:
            continue
        key = d.strftime('%Y-%m')
        if key in expense_values:
            amount = float(row['amount'] or 0)
            expense_values[key] += amount
            category = clean_str(row['category']) or 'Other'
            supplier = clean_str(row['supplier']) or 'Unassigned'
            expense_by_category[category] = round(expense_by_category.get(category, 0.0) + amount, 2)
            expense_by_supplier[supplier] = round(expense_by_supplier.get(supplier, 0.0) + amount, 2)

    for row in recurring_income_rows:
        due_date = parse_iso_date(row['next_due_date']) or parse_iso_date(row['start_date'])
        if due_date:
            key = due_date.strftime('%Y-%m')
            if key in recurring_income_values:
                recurring_income_values[key] += float(row['amount'] or 0)
        method = clean_str(row['collection_method']) or 'Other'
        income_by_method[method] = round(income_by_method.get(method, 0.0) + float(row['amount'] or 0), 2)

    quote_count_series = [quote_counts[m] for m in months]
    quote_value_series = [round(quote_values[m], 2) for m in months]
    job_value_series = [round(job_values[m], 2) for m in months]
    invoice_value_series = [round(invoice_values[m], 2) for m in months]
    paid_invoice_series = [round(paid_invoice_values[m], 2) for m in months]
    net_invoice_series = [round(net_invoice_values[m], 2) for m in months]
    net_paid_invoice_series = [round(net_paid_invoice_values[m], 2) for m in months]
    expense_series = [round(expense_values[m], 2) for m in months]
    recurring_income_series = [round(recurring_income_values[m], 2) for m in months]
    true_profit_series = [round(net_paid_invoice_series[i] - expense_series[i], 2) for i in range(len(months))]
    max_money_value = max(invoice_value_series + job_value_series + quote_value_series + net_paid_invoice_series + expense_series + recurring_income_series + [1])
    max_quote_count = max(quote_count_series + [1])

    total_quotes_value = round(sum(quote_value_series), 2)
    total_invoice_value = round(sum(invoice_value_series), 2)
    total_paid_value = round(sum(paid_invoice_series), 2)
    total_expense_value = round(sum(expense_series), 2)
    total_recurring_templates = q("SELECT COUNT(*) AS c FROM recurring_expenses WHERE archived_at IS NULL AND active=1", one=True)['c']
    due_recurring_templates = q("SELECT COUNT(*) AS c FROM recurring_expenses WHERE archived_at IS NULL AND active=1 AND date(IFNULL(next_due_date, start_date)) <= date('now')", one=True)['c']
    total_recurring_income = q("SELECT COUNT(*) AS c FROM recurring_income WHERE archived_at IS NULL AND active=1", one=True)['c']
    due_recurring_income = q("SELECT COUNT(*) AS c FROM recurring_income WHERE archived_at IS NULL AND active=1 AND date(IFNULL(next_due_date, start_date)) <= date('now')", one=True)['c']

    category_items = [{'name': k, 'value': v} for k, v in sorted(expense_by_category.items(), key=lambda kv: kv[1], reverse=True)]
    supplier_items = [{'name': k, 'value': v} for k, v in sorted(expense_by_supplier.items(), key=lambda kv: kv[1], reverse=True)]
    income_method_items = [{'name': k, 'value': v} for k, v in sorted(income_by_method.items(), key=lambda kv: kv[1], reverse=True)]
    max_cat = max([x['value'] for x in category_items] + [1])
    max_sup = max([x['value'] for x in supplier_items] + [1])
    max_method = max([x['value'] for x in income_method_items] + [1])
    for item in category_items:
        item['width'] = max(8, round((item['value'] / max_cat) * 100)) if item['value'] > 0 else 0
    for item in supplier_items:
        item['width'] = max(8, round((item['value'] / max_sup) * 100)) if item['value'] > 0 else 0
    for item in income_method_items:
        item['width'] = max(8, round((item['value'] / max_method) * 100)) if item['value'] > 0 else 0

    cashflow = cashflow_snapshot()

    totals = {
        'quotes_total_value': total_quotes_value,
        'jobs_total_value': round(sum(job_value_series), 2),
        'invoices_total_value': total_invoice_value,
        'paid_invoices_total_value': total_paid_value,
        'net_invoices_total_value': round(sum(net_invoice_series), 2),
        'net_paid_invoices_total_value': round(sum(net_paid_invoice_series), 2),
        'expenses_total_value': total_expense_value,
        'true_profit_total_value': round(sum(true_profit_series), 2),
        'quotes_total_count': sum(quote_count_series),
        'average_quote_value': round((sum(quote_value_series) / sum(quote_count_series)), 2) if sum(quote_count_series) else 0.0,
        'average_invoice_value': round((sum(invoice_value_series) / len([v for v in invoice_value_series if v > 0])), 2) if len([v for v in invoice_value_series if v > 0]) else 0.0,
        'collection_rate_percent': round((total_paid_value / total_invoice_value) * 100, 1) if total_invoice_value else 0.0,
        'quote_to_invoice_percent': round((total_invoice_value / total_quotes_value) * 100, 1) if total_quotes_value else 0.0,
        'outstanding_total_value': round(outstanding_total, 2),
        'overdue_total_value': round(overdue_total, 2),
        'overdue_count': overdue_count,
        'recurring_templates_total': total_recurring_templates,
        'recurring_templates_due': due_recurring_templates,
        'recurring_income_total': total_recurring_income,
        'recurring_income_due': due_recurring_income,
        'recurring_income_pipeline_value': round(sum(recurring_income_series), 2),
        **cashflow,
    }

    monthly = []
    for i, m in enumerate(months):
        monthly.append({
            'month_key': m,
            'month_label': labels[i],
            'quotes_count': quote_count_series[i],
            'quotes_value': quote_value_series[i],
            'jobs_value': job_value_series[i],
            'invoices_value': invoice_value_series[i],
            'paid_invoices_value': paid_invoice_series[i],
            'net_invoices_value': net_invoice_series[i],
            'net_paid_invoices_value': net_paid_invoice_series[i],
            'expenses_value': expense_series[i],
            'recurring_income_value': recurring_income_series[i],
            'true_profit_value': true_profit_series[i],
            'money_width_quotes': max(8, round((quote_value_series[i] / max_money_value) * 100)) if quote_value_series[i] > 0 else 0,
            'money_width_jobs': max(8, round((job_value_series[i] / max_money_value) * 100)) if job_value_series[i] > 0 else 0,
            'money_width_invoices': max(8, round((invoice_value_series[i] / max_money_value) * 100)) if invoice_value_series[i] > 0 else 0,
            'money_width_net_paid': max(8, round((net_paid_invoice_series[i] / max_money_value) * 100)) if net_paid_invoice_series[i] > 0 else 0,
            'money_width_expenses': max(8, round((expense_series[i] / max_money_value) * 100)) if expense_series[i] > 0 else 0,
            'money_width_recurring_income': max(8, round((recurring_income_series[i] / max_money_value) * 100)) if recurring_income_series[i] > 0 else 0,
            'money_width_true_profit': max(8, round((abs(true_profit_series[i]) / max_money_value) * 100)) if true_profit_series[i] != 0 else 0,
            'count_width_quotes': max(8, round((quote_count_series[i] / max_quote_count) * 100)) if quote_count_series[i] > 0 else 0,
        })

    return {
        'labels': labels,
        'monthly': monthly,
        'totals': totals,
        'expense_by_category': category_items[:8],
        'expense_by_supplier': supplier_items[:8],
        'income_by_method': income_method_items[:8],
    }


def segmentation_follow_up_text(segment_name, customer_name='there'):
    templates = {
        'new': f"Hi {customer_name}, just checking in to see how things are settling in after your recent clean. If you need anything else, just let me know.",
        'active': f"Hi {customer_name}, thanks again for using us. If you would like to get your next clean booked in early, just reply to this message.",
        'warm': f"Hi {customer_name}, it has been a little while since your last clean, so I just wanted to check in and see if you would like a freshen up booked in.",
        'cooling_off': f"Hi {customer_name}, I hope you are well. It has been a while since we last cleaned for you, so I just wanted to see if you would like an updated quote.",
        'reactivation_6m': f"Hi {customer_name}, we have not seen you for a while, so I just wanted to check whether you would like a return visit or an updated quote for any carpets or upholstery.",
        'reactivation_12m': f"Hi {customer_name}, it has been quite a long time since your last booking, so I just wanted to get back in touch in case you would like a fresh clean or an updated quote.",
        'no_invoice_date': f"Hi {customer_name}, just checking in to see if you would like a quote for any carpets, rugs, or upholstery."
    }
    return templates.get(segment_name, f"Hi {customer_name}, just checking in to see if you would like to book another clean.")


def build_customer_segmentation_snapshot():
    today = date.today()
    customers = q("SELECT * FROM customers WHERE archived_at IS NULL ORDER BY first_name, last_name")
    invoices = q("SELECT customer_id, invoice_date, due_date, total, vat, status FROM invoices WHERE customer_id IS NOT NULL AND IFNULL(status,'') <> 'Archived'")

    lifetime = {}
    for c in customers:
        cid = int(c['id'])
        lifetime[cid] = {
            'customer_id': cid,
            'name': ((c['first_name'] or '') + ' ' + (c['last_name'] or '')).strip() or f'Customer {cid}',
            'email': c['email'] or '',
            'phone': c['phone'] or '',
            'town': c['town'] or '',
            'last_invoice_date': '',
            'days_since_last_invoice': None,
            'invoice_total': 0.0,
            'paid_total': 0.0,
            'invoice_count': 0,
            'paid_count': 0,
        }

    for row in invoices:
        cid = int(row['customer_id'] or 0)
        if cid not in lifetime:
            continue
        item = lifetime[cid]
        total_val = float(row['total'] or 0)
        item['invoice_total'] += total_val
        item['invoice_count'] += 1
        inv_date = clean_str(row['invoice_date']) or clean_str(row['due_date'])
        if inv_date and (not item['last_invoice_date'] or inv_date > item['last_invoice_date']):
            item['last_invoice_date'] = inv_date
        if clean_str(row['status']).lower() == 'paid':
            item['paid_total'] += total_val
            item['paid_count'] += 1

    counts = {
        'new': 0,
        'active': 0,
        'warm': 0,
        'cooling_off': 0,
        'reactivation_6m': 0,
        'reactivation_12m': 0,
        'no_invoice_date': 0,
    }
    lists = {k: [] for k in counts}

    for item in lifetime.values():
        last_dt = parse_iso_date(item['last_invoice_date'])
        if not last_dt:
            bucket = 'no_invoice_date'
            days_since = None
        else:
            days_since = (today - last_dt).days
            if item['invoice_count'] <= 1 and days_since <= 60:
                bucket = 'new'
            elif days_since <= 90:
                bucket = 'active'
            elif days_since <= 180:
                bucket = 'warm'
            elif days_since <= 365:
                bucket = 'cooling_off'
            elif days_since <= 730:
                bucket = 'reactivation_6m'
            else:
                bucket = 'reactivation_12m'
        item['days_since_last_invoice'] = days_since
        item['segment'] = bucket
        counts[bucket] += 1
        lists[bucket].append(item)

    def sort_rows(rows):
        return sorted(rows, key=lambda r: ((-1 if r['days_since_last_invoice'] is None else r['days_since_last_invoice']), r['paid_total'], r['invoice_total']), reverse=True)

    ordered = {
        'new': sort_rows(lists['new']),
        'active': sort_rows(lists['active']),
        'warm': sort_rows(lists['warm']),
        'cooling_off': sort_rows(lists['cooling_off']),
        'reactivation_6m': sort_rows(lists['reactivation_6m']),
        'reactivation_12m': sort_rows(lists['reactivation_12m']),
        'no_invoice_date': sort_rows(lists['no_invoice_date']),
    }
    ordered['reactivation_candidates'] = sort_rows(lists['reactivation_6m'] + lists['reactivation_12m'])
    return {'counts': counts, **ordered}


def active_archived_counts():
    return {
        "customers_active": q("SELECT COUNT(*) AS c FROM customers WHERE archived_at IS NULL", one=True)["c"],
        "customers_archived": q("SELECT COUNT(*) AS c FROM customers WHERE archived_at IS NOT NULL", one=True)["c"],
        "quotes_active": q("SELECT COUNT(*) AS c FROM quotes WHERE IFNULL(status,'') <> 'Archived'", one=True)["c"],
        "quotes_archived": q("SELECT COUNT(*) AS c FROM quotes WHERE IFNULL(status,'') = 'Archived'", one=True)["c"],
        "jobs_active": q("SELECT COUNT(*) AS c FROM jobs WHERE IFNULL(status,'') <> 'Archived'", one=True)["c"],
        "jobs_archived": q("SELECT COUNT(*) AS c FROM jobs WHERE IFNULL(status,'') = 'Archived'", one=True)["c"],
        "invoices_active": q("SELECT COUNT(*) AS c FROM invoices WHERE IFNULL(status,'') <> 'Archived'", one=True)["c"],
        "invoices_archived": q("SELECT COUNT(*) AS c FROM invoices WHERE IFNULL(status,'') = 'Archived'", one=True)["c"],
        "expenses_active": q("SELECT COUNT(*) AS c FROM expenses WHERE archived_at IS NULL", one=True)["c"],
        "expenses_archived": q("SELECT COUNT(*) AS c FROM expenses WHERE archived_at IS NOT NULL", one=True)["c"],
        "recurring_income_active": q("SELECT COUNT(*) AS c FROM recurring_income WHERE archived_at IS NULL", one=True)["c"],
        "recurring_income_archived": q("SELECT COUNT(*) AS c FROM recurring_income WHERE archived_at IS NOT NULL", one=True)["c"],
    }


def parse_email_list(value):
    items = []
    for raw in str(value or "").replace(";", ",").split(","):
        email = raw.strip()
        if email:
            items.append(email)
    return items


def strip_html_for_sms(text):
    text = str(text or "")
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li[^>]*>", "• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = text.replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_sms_text(body, customer=None):
    main_text = strip_html_for_sms(merge_message_text(body or "", customer))
    footer_text = strip_html_for_sms(merge_message_text(settings()["sms_footer_text"] or "", customer))
    if footer_text:
        return (main_text + "\n\n" + footer_text).strip() if main_text else footer_text
    return main_text


def http_post_form(url, data, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode('utf-8'), headers=headers or {}, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('utf-8', errors='replace')


def http_post_json(url, payload, headers=None):
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=hdrs, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('utf-8', errors='replace')


def website_form_email_payload(data, lead_id=None, customer_id=None):
    fields = [
        ("Name", request_value(data, "name", "full_name", "customer_name")),
        ("Phone", request_value(data, "phone", "phone_number", "telephone", "tel")),
        ("Email", request_value(data, "email", "email_address")),
        ("Address", request_value(data, "address", "full_address")),
        ("Postcode", request_value(data, "postcode", "post_code", "zip", "area")),
        ("Service", request_value(data, "service", "what_cleaned", "cleaning_required")),
        ("Rooms or areas", request_value(data, "rooms", "number_rooms", "rooms_or_areas", "areas")),
        ("Upholstery", request_value(data, "upholstery")),
        ("Rugs", request_value(data, "rugs")),
        ("Stains/problem areas", request_value(data, "stains", "problem_areas")),
        ("Pets", request_value(data, "pets")),
        ("Parking", request_value(data, "parking")),
        ("Preferred days/times", request_value(data, "preferred_days_times", "preferred_times")),
        ("Additional notes", request_value(data, "additional_notes", "notes", "message")),
        ("Klarna interest", request_value(data, "klarna_interest")),
        ("Source", request_value(data, "source") or "Website form"),
    ]
    body_lines = ["New website enquiry saved to the CRM and waiting for review.", ""]
    for label, value in fields:
        if value:
            body_lines.append(f"{label}: {value}")
    if customer_id:
        body_lines.append(f"CRM customer ID: {customer_id}")
    if lead_id:
        body_lines.append(f"CRM intake ID: {lead_id}")
    return {
        "_subject": request_value(data, "_subject") or "New Website Enquiry - Saved to CRM",
        "name": request_value(data, "name", "full_name", "customer_name") or "Website enquiry",
        "phone": request_value(data, "phone", "phone_number", "telephone", "tel") or "",
        "email": request_value(data, "email", "email_address") or "",
        "message": "\n".join(body_lines),
        "crm_customer_id": str(customer_id or ""),
        "crm_intake_id": str(lead_id or ""),
    }


def forward_website_form_to_formspree(data, lead_id=None, customer_id=None):
    endpoint = os.environ.get("WEBSITE_FORMSPREE_ENDPOINT", "https://formspree.io/f/mblnzwpv").strip()
    if not endpoint:
        return False, "Formspree forwarding is disabled."
    try:
        http_post_form(endpoint, website_form_email_payload(data, lead_id=lead_id, customer_id=customer_id), headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 Website Form Forwarder",
        })
        return True, "Formspree email copy sent."
    except Exception as exc:
        logger.warning("Website form saved to CRM but Formspree forwarding failed: %s", exc)
        return False, f"Saved to CRM, but Formspree email failed: {exc}"


def website_form_sms_text(data, customer_id=None):
    name = request_value(data, "name", "full_name", "customer_name") or "Website customer"
    phone = request_value(data, "phone", "phone_number", "telephone", "tel") or "No phone"
    postcode = request_value(data, "postcode", "post_code", "zip", "area") or "No postcode"
    service = request_value(data, "service", "what_cleaned", "cleaning_required") or "No service"
    rooms = request_value(data, "rooms", "number_rooms", "rooms_or_areas", "areas") or ""
    bits = [
        "New website form",
        f"{name}",
        f"Phone: {phone}",
        f"Postcode: {postcode}",
        f"Service: {service}" + (f" ({rooms})" if rooms else ""),
    ]
    if customer_id:
        bits.append(f"CRM ID: {customer_id}")
    bits.append("Next: review and approve for Xero.")
    return "\n".join(bits)


def send_website_form_sms_alert(data, customer_id=None):
    s = settings()
    to_phone = normalize_phone(os.environ.get("WEBSITE_FORM_SMS_TO") or s["sms_test_number"] or s["phone"] or "")
    if not to_phone:
        return False, "No business phone number is saved for website form text alerts."
    ok, msg = send_sms_gateway(
        to_phone,
        website_form_sms_text(data, customer_id=customer_id),
        customer=None,
        communication_id=None,
        message_category="Website Form Alert",
    )
    return ok, msg


def enquiry_public_site_url():
    return "https://www.thecarpetcleaningcrew.co.uk"


def public_static_url(filename):
    try:
        return url_for("static", filename=filename, _external=True)
    except RuntimeError:
        return ""


DEFAULT_MESSAGE_TEMPLATES = {
    "customer_enquiry_email": {
        "name": "Customer enquiry email",
        "subject": "Thank you for contacting The Carpet Cleaning Company",
        "body": "Hi {{name}},\n\nThank you for contacting The Carpet Cleaning Company.\n\nWe’ve received your enquiry and will be in touch shortly.\n\nWe provide professional carpet cleaning, upholstery cleaning and stain treatment services, with the aim of choosing the right cleaning approach for each job rather than guessing from a short message.\n\nIf you can, please send photos of the areas you would like us to quote for. This can include carpets, upholstery, stains, heavy soiling, pet marks, traffic lanes, rugs, stairs, hallways, sofas, chairs, or anything else you would like cleaned.\n\nPhotos help us understand the carpet or upholstery type, the condition, the stains, and the best cleaning method. We can then advise on the most suitable cleaning option and discuss the best way to get the best result for your budget.\n\nYou can send photos by replying to this email, or by replying to our SMS message from your phone.\n\nWhile you wait, please take a look at, like and follow our Facebook page to see our videos, recent work, before-and-after photos, and customer feedback:\nFacebook: https://www.facebook.com/profile.php?id=61559013150413\nGoogle Reviews: https://share.google/XHQjHHLwpmlugHP0c\nWebsite: https://www.thecarpetcleaningcrew.co.uk\n\nThank you for considering The Carpet Cleaning Company.\n\nPaul Nicholas\nThe Carpet Cleaning Company\n07802 563213\nwww.thecarpetcleaningcrew.co.uk",
    },
    "customer_enquiry_sms": {
        "name": "Customer enquiry SMS",
        "subject": "",
        "body": "Hi {{name}},\n\nThank you for contacting The Carpet Cleaning Company.\n\nWe’ve received your enquiry and will respond as soon as possible.\n\nWhile you wait, please follow us on Facebook to see our videos, recent work, and before-and-after photos:\nhttps://www.facebook.com/profile.php?id=61559013150413\n\nGoogle Reviews:\nhttps://share.google/XHQjHHLwpmlugHP0c\n\nThank you for considering The Carpet Cleaning Company. We look forward to assisting you.",
    },
    "owner_enquiry_alert_email": {
        "name": "Owner enquiry alert email",
        "subject": "New website enquiry received",
        "body": "{{owner_alert_details}}",
    },
    "owner_enquiry_alert_sms": {
        "name": "Owner enquiry alert SMS",
        "subject": "",
        "body": "New enquiry\nName: {{name}}\nPhone: {{phone}}\nEmail: {{email}}\nPostcode: {{postcode}}\nService: {{service}}\nMessage: {{message}}",
    },
    "booking_confirmation_email": {"name": "Booking confirmation email", "subject": "Booking confirmation", "body": "Hi {{name}},\n\nYour booking is confirmed.\n\nThanks\nPaul"},
    "booking_confirmation_sms": {"name": "Booking confirmation SMS", "subject": "", "body": "Your carpet cleaning booking is confirmed. Thanks, Paul."},
    "appointment_reminder_sms": {"name": "Appointment reminder SMS", "subject": "", "body": "Hi {{name}}, just a reminder that your carpet cleaning appointment is booked for {{date}} at {{time}}. Thanks, Paul."},
    "thank_you_message": {"name": "Thank you message", "subject": "Thank you", "body": "Hi {{name}}, thank you for using The Carpet Cleaning Company."},
    "review_request_message": {"name": "Review request message", "subject": "Review request", "body": "Hi {{name}}, thank you for using The Carpet Cleaning Company. If you are happy with the work, I would really appreciate a quick Google review: https://share.google/XHQjHHLwpmlugHP0c"},
}


def template_context_for_enquiry(data, customer_id=None, lead_id=None):
    service = request_value(data, "service", "what_cleaned", "service_required", "cleaning_required")
    phone = request_value(data, "phone", "phone_number", "telephone", "tel")
    owner_details = owner_enquiry_alert_text(data, customer_id=customer_id, lead_id=lead_id)
    return {
        "{{name}}": request_value(data, "name", "full_name", "customer_name") or "there",
        "{{phone}}": phone,
        "{{email}}": request_value(data, "email", "email_address"),
        "{{address}}": request_value(data, "address", "full_address", "street_address"),
        "{{postcode}}": request_value(data, "postcode", "post_code", "zip"),
        "{{service}}": service,
        "{{preferred_date}}": request_value(data, "preferred_date", "date", "preferred_days_times"),
        "{{message}}": request_value(data, "message", "notes", "additional_notes"),
        "{{owner_alert_details}}": owner_details,
        "{{website}}": enquiry_public_site_url(),
    }


def render_simple_template(text, replacements):
    rendered = str(text or "")
    for key, value in replacements.items():
        rendered = rendered.replace(key, clean_str(value))
    return rendered


def message_template(key):
    default = DEFAULT_MESSAGE_TEMPLATES.get(key, {"name": key, "subject": "", "body": ""})
    row = q("SELECT * FROM message_templates WHERE template_key=?", (key,), one=True)
    if not row:
        return default
    return {
        "name": row["name"] or default["name"],
        "subject": row["subject"] if row["subject"] is not None else default["subject"],
        "body": row["body"] if row["body"] is not None else default["body"],
    }


def status_text(ok, message="", skipped=False):
    if skipped:
        return "Skipped: " + clean_str(message)
    return ("Sent: " if ok else "Failed: ") + clean_str(message)


def update_intake_delivery_status(lead_id, **fields):
    allowed = {
        "xero_sync_status", "customer_email_status", "customer_sms_status",
        "owner_email_status", "owner_sms_status", "follow_up_status"
    }
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key}=?")
            params.append(clean_str(value))
    if not updates:
        return
    updates.append("updated_at=datetime('now')")
    params.append(lead_id)
    run(f"UPDATE intake_submissions SET {', '.join(updates)} WHERE id=?", tuple(params))


def enquiry_customer_email_html(data):
    replacements = template_context_for_enquiry(data)
    customer_name = html_lib.escape(replacements.get("{{name}}") or "there")
    logo_url = os.environ.get("CRM_LOGO_URL", "").strip() or public_static_url("site/logo.webp")
    hero_url = public_static_url("site/hero-carpet-cleaning.webp")
    website_url = enquiry_public_site_url()
    facebook_url = "https://www.facebook.com/profile.php?id=61559013150413"
    reviews_url = "https://share.google/XHQjHHLwpmlugHP0c"
    logo_html = f'<img src="{html_lib.escape(logo_url)}" alt="The Carpet Cleaning Company" width="92" style="display:block;width:92px;height:auto;border:0;margin:0 auto 14px">' if logo_url else ""
    hero_html = f"""
        <tr>
          <td style="padding:0 28px 24px">
            <img src="{html_lib.escape(hero_url)}" alt="Professional carpet cleaning" width="584" style="display:block;width:100%;max-width:584px;height:auto;border-radius:18px;border:0">
          </td>
        </tr>
    """ if hero_url else ""
    return f"""<!doctype html>
<html>
<body style="margin:0;background:#eef4f8;font-family:Arial,Helvetica,sans-serif;color:#0b1f33">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef4f8;margin:0;padding:0">
    <tr>
      <td align="center" style="padding:28px 14px">
        <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:#ffffff;border-radius:24px;overflow:hidden;border:1px solid #d8e4ee;box-shadow:0 18px 48px rgba(12,31,51,.10)">
          <tr>
            <td align="center" style="background:#071524;padding:28px 28px 24px;color:#ffffff">
              {logo_html}
              <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#e3bd66;font-weight:700">The Carpet Cleaning Company</div>
              <h1 style="margin:12px 0 0;font-size:30px;line-height:1.15;color:#ffffff">Thanks for your enquiry, {customer_name}</h1>
              <p style="margin:12px auto 0;max-width:520px;font-size:16px;line-height:1.55;color:#d9e7f2">We’ve received your message and will be in touch shortly.</p>
            </td>
          </tr>
          {hero_html}
          <tr>
            <td style="padding:0 28px 10px">
              <h2 style="margin:0 0 10px;font-size:23px;line-height:1.25;color:#071524">Professional cleaning, quoted properly</h2>
              <p style="margin:0;font-size:16px;line-height:1.65;color:#385066">We provide professional carpet cleaning, upholstery cleaning and stain treatment services. Every job is different, so we look at the fabric, condition, staining and access before advising on the best option.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 8px">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fbfd;border:1px solid #dce8f1;border-radius:18px">
                <tr>
                  <td style="padding:22px">
                    <h2 style="margin:0 0 10px;font-size:22px;line-height:1.25;color:#071524">Send photos for a faster, more accurate quote</h2>
                    <p style="margin:0;font-size:16px;line-height:1.65;color:#385066">If you can, please send photos of anything you would like us to quote for: carpets, upholstery, stains, heavy soiling, pet marks, traffic lanes, rugs, stairs, hallways, sofas or chairs.</p>
                    <p style="margin:14px 0 0;font-size:16px;line-height:1.65;color:#385066">You can send photos by replying to this email, or by replying to our SMS message from your phone.</p>
                    <p style="margin:14px 0 0;font-size:16px;line-height:1.65;color:#385066">Photos help us understand the carpet or upholstery type, the condition, the stains and the best cleaning approach. We can then recommend the most suitable option and discuss the best way to get the best result for your budget.</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 28px 8px">
              <p style="margin:0 0 16px;font-size:16px;line-height:1.65;color:#385066">While you wait, please take a look at, like and follow our Facebook page to see our videos, recent work, before-and-after photos and customer feedback. You can also read our Google reviews below.</p>
              <table role="presentation" cellspacing="0" cellpadding="0">
                <tr>
                  <td style="padding:0 10px 10px 0"><a href="{html_lib.escape(facebook_url)}" style="display:inline-block;background:#165dcc;color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;padding:13px 17px;border-radius:12px">Follow us on Facebook</a></td>
                  <td style="padding:0 10px 10px 0"><a href="{html_lib.escape(reviews_url)}" style="display:inline-block;background:#0d7c61;color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;padding:13px 17px;border-radius:12px">Read Google reviews</a></td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 28px 26px">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-top:1px solid #dce8f1">
                <tr>
                  <td style="padding-top:20px;font-size:15px;line-height:1.65;color:#385066">
                    <strong style="color:#071524">Paul Nicholas</strong><br>
                    The Carpet Cleaning Company<br>
                    <a href="tel:07802563213" style="color:#165dcc;text-decoration:none">07802 563213</a><br>
                    <a href="{html_lib.escape(website_url)}" style="color:#165dcc;text-decoration:none">www.thecarpetcleaningcrew.co.uk</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def enquiry_customer_email_text(data):
    return render_simple_template(message_template("customer_enquiry_email")["body"], template_context_for_enquiry(data))


def send_env_email(to_email, subject, text_body, html_body="", customer=None):
    clicksend_ok, clicksend_msg = send_clicksend_email(to_email, subject, text_body, html_body)
    if clicksend_ok or clicksend_msg:
        return clicksend_ok, clicksend_msg
    host = os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com"
    user = os.environ.get("SMTP_USER", "").strip()
    password_raw = os.environ.get("SMTP_PASSWORD", "").strip()
    password = re.sub(r"\s+", "", password_raw)
    port = int(os.environ.get("SMTP_PORT", "465") or 465)
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    from_name = os.environ.get("SMTP_FROM_NAME", "The Carpet Cleaning Company").strip()
    if not user and not password:
        return send_email_smtp(to_email, subject, html_body or text_body, customer=customer)
    if not user or not password:
        missing = "SMTP_USER" if not user else "SMTP_PASSWORD"
        return False, f"Gmail SMTP is missing {missing} in Render environment variables."
    if not sender:
        return False, "Gmail SMTP is missing SMTP_FROM or SMTP_USER in Render environment variables."
    recipients = parse_email_list(to_email)
    if not recipients:
        return False, "No email recipient was supplied."
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{sender}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body or " ", "plain", "utf-8"))
    msg.attach(MIMEText(html_body or html_lib.escape(text_body or " "), "html", "utf-8"))
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as server:
                if user:
                    server.login(user, password)
                server.sendmail(sender, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls(context=ssl.create_default_context())
                if user:
                    server.login(user, password)
                server.sendmail(sender, recipients, msg.as_string())
        return True, f"Email sent to {', '.join(recipients)}."
    except Exception as exc:
        fallback_ok, fallback_msg = send_email_smtp(to_email, subject, html_body or text_body, customer=customer)
        if fallback_ok:
            return True, fallback_msg
        smtp_user_hint = user if "@" in user else ("set" if user else "missing")
        smtp_debug = f"SMTP user: {smtp_user_hint}; app password length after spaces removed: {len(password)}."
        return False, f"{exc} {smtp_debug} CRM Gmail fallback also failed: {fallback_msg}"


def send_clicksend_email(to_email, subject, text_body, html_body=""):
    enabled = os.environ.get("CLICKSEND_EMAIL_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False, ""
    username = os.environ.get("CLICKSEND_USERNAME", "").strip()
    api_key = os.environ.get("CLICKSEND_API_KEY", "").strip()
    email_address_id = os.environ.get("CLICKSEND_EMAIL_ADDRESS_ID", "").strip()
    from_name = os.environ.get("CLICKSEND_EMAIL_FROM_NAME", "The Carpet Cleaning Company").strip()
    if not username or not api_key:
        return False, ""
    recipients = parse_email_list(to_email)
    if not recipients:
        return False, "No email recipient was supplied."
    payload = {
        "to": [{"email": recipient, "name": ""} for recipient in recipients],
        "subject": subject,
        "body": html_body or html_lib.escape(text_body or " "),
    }
    if email_address_id:
        try:
            payload["from"] = {"email_address_id": int(email_address_id), "name": from_name}
        except ValueError:
            return False, "CLICKSEND_EMAIL_ADDRESS_ID must be a number from ClickSend Email settings."
    try:
        response = http_post_basic_json("https://rest.clicksend.com/v3/email/send", payload, username, api_key)
        data = json.loads(response)
        response_code = str(data.get("response_code") or "").upper()
        response_msg = clean_str(data.get("response_msg") or "")
        if response_code == "SUCCESS":
            return True, f"ClickSend email accepted for {', '.join(recipients)}."
        return False, f"ClickSend email failed: {response_msg or response[:260]}"
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return False, f"ClickSend email failed HTTP {exc.code}: {error_body[:300]}"
    except Exception as exc:
        return False, f"ClickSend email failed: {exc}"


def send_clicksend_env_sms(to_phone, body, customer=None, category="Website Enquiry"):
    username = os.environ.get("CLICKSEND_USERNAME", "").strip()
    api_key = os.environ.get("CLICKSEND_API_KEY", "").strip()
    from_name = os.environ.get("CLICKSEND_FROM_NAME", "").strip()
    phone = normalize_phone(to_phone)
    if not phone:
        return False, "No recipient mobile number was supplied."
    if not username or not api_key:
        return send_sms_gateway(phone, body, customer=customer, message_category=category)
    try:
        message = {"source": "python", "to": phone, "body": body}
        if from_name:
            message["from"] = from_name
        payload = {"messages": [message]}
        response = http_post_basic_json("https://rest.clicksend.com/v3/sms/send", payload, username, api_key)
        data = json.loads(response)
        msg_data = (((data.get("data") or {}).get("messages")) or [{}])[0]
        ext = str(msg_data.get("message_id") or "")
        status = str(msg_data.get("status") or msg_data.get("status_text") or data.get("response_code") or "queued")
        response_code = str(data.get("response_code") or "").upper()
        status_upper = status.upper()
        error_text = str(msg_data.get("error_text") or data.get("response_msg") or "")
        accepted = bool(ext) and response_code in ("SUCCESS", "200", "")
        failed = any(word in status_upper for word in ("FAIL", "ERROR", "REJECT", "INVALID")) or response_code in ("FAILED", "ERROR")
        event_type = "send" if accepted and not failed else "send_failed"
        event_status = status.title() if status else ("Accepted" if accepted else "Failed")
        log_sms_event(customer["id"] if customer else None, None, "ClickSend", event_type, phone, from_name, body, ext, event_status, "outbound", data, error_text)
        if accepted and not failed:
            return True, f"SMS accepted by ClickSend for {phone}. Message ID: {ext}. Status: {status or response_code}."
        return False, f"ClickSend send failed for {phone}. Status: {status or response_code}. {error_text}".strip()
    except Exception as exc:
        log_sms_event(customer["id"] if customer else None, None, "ClickSend", "send_failed", phone, from_name, body, "", "Failed", "outbound", {}, str(exc))
        return False, str(exc)


def owner_enquiry_alert_text(data, customer_id=None, lead_id=None):
    customer_url = url_for("customer_view", customer_id=customer_id, _external=True) if customer_id else ""
    lines = [
        "New website enquiry received",
        f"Customer name: {request_value(data, 'name', 'full_name', 'customer_name')}",
        f"Phone number: {request_value(data, 'phone', 'phone_number', 'telephone', 'tel')}",
        f"Email address: {request_value(data, 'email', 'email_address')}",
        f"Address: {request_value(data, 'address', 'full_address', 'street_address')}",
        f"Postcode: {request_value(data, 'postcode', 'post_code', 'zip')}",
        f"Service requested: {request_value(data, 'service', 'what_cleaned', 'service_required', 'cleaning_required')}",
        f"Preferred date: {request_value(data, 'preferred_date', 'date', 'preferred_days_times')}",
        f"Message: {request_value(data, 'message', 'notes', 'additional_notes')}",
    ]
    if customer_url:
        lines.append(f"Open in CRM: {customer_url}")
    if lead_id:
        lines.append(f"Intake ID: {lead_id}")
    return "\n".join(lines)


def run_website_enquiry_automation(lead_id, customer_id, data):
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not lead or not customer:
        return {}
    results = {}

    try:
        contact_id = find_xero_contact_id_for_lead(lead)
        payload = xero_contact_payload_from_lead(lead)
        if contact_id:
            payload["Contacts"][0]["ContactID"] = contact_id
        result = xero_api_request(XERO_CONTACTS_URL, method="POST", payload=payload, idempotency_key=f"website-enquiry-contact-{lead_id}-{contact_id or 'new'}")
        contact = (result.get("Contacts") or [{}])[0]
        contact_id = contact.get("ContactID") or contact_id
        if not contact_id:
            raise RuntimeError("Xero did not return a ContactID.")
        run("""UPDATE intake_submissions SET xero_contact_id=?, xero_sent_at=datetime('now'), xero_error='', xero_sync_status=?, updated_at=datetime('now') WHERE id=?""", (contact_id, "Sent: Xero contact created or updated", lead_id))
        run("""UPDATE customers SET xero_contact_id=?, xero_contact_synced_at=datetime('now'), xero_contact_error='' WHERE id=?""", (contact_id, customer_id))
        run("INSERT INTO customer_timeline(customer_id, note_text, created_at) VALUES (?,?,datetime('now'))", (customer_id, "Xero contact created or updated from website enquiry."))
        results["xero"] = (True, f"Xero contact ready: {contact_id}")
    except Exception as exc:
        run("""UPDATE intake_submissions SET xero_error=?, xero_sync_status=?, updated_at=datetime('now') WHERE id=?""", (str(exc), f"Failed: {exc}", lead_id))
        run("UPDATE customers SET xero_contact_error=? WHERE id=?", (str(exc), customer_id))
        run("INSERT INTO customer_timeline(customer_id, note_text, created_at) VALUES (?,?,datetime('now'))", (customer_id, f"Xero sync failed: {exc}"))
        results["xero"] = (False, str(exc))

    customer_email = request_value(data, "email", "email_address")
    if customer_email:
        customer_email_template = message_template("customer_enquiry_email")
        subject = render_simple_template(customer_email_template["subject"] or "Thank you for your enquiry", template_context_for_enquiry(data, customer_id=customer_id, lead_id=lead_id))
        ok, msg = send_env_email(customer_email, subject, enquiry_customer_email_text(data), enquiry_customer_email_html(data), customer=customer)
        update_intake_delivery_status(lead_id, customer_email_status=status_text(ok, msg))
        run("INSERT INTO communications(customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, "Email", "Customer enquiry thank you", enquiry_customer_email_text(data)))
        run("INSERT INTO customer_timeline(customer_id, note_text, created_at) VALUES (?,?,datetime('now'))", (customer_id, ("Customer welcome email sent. " if ok else "Customer welcome email failed. ") + clean_str(msg)))
        results["customer_email"] = (ok, msg)
    else:
        update_intake_delivery_status(lead_id, customer_email_status=status_text(False, "No customer email supplied", skipped=True))

    customer_phone = request_value(data, "phone", "phone_number", "telephone", "tel")
    customer_sms = render_simple_template(message_template("customer_enquiry_sms")["body"], template_context_for_enquiry(data, customer_id=customer_id, lead_id=lead_id))
    ok, msg = send_clicksend_env_sms(customer_phone, customer_sms, customer=customer, category="Service")
    update_intake_delivery_status(lead_id, customer_sms_status=status_text(ok, msg))
    results["customer_sms"] = (ok, msg)

    owner_email = os.environ.get("OWNER_ALERT_EMAIL", "").strip()
    owner_email_template = message_template("owner_enquiry_alert_email")
    alert_body = render_simple_template(owner_email_template["body"], template_context_for_enquiry(data, customer_id=customer_id, lead_id=lead_id))
    if owner_email:
        subject = render_simple_template(owner_email_template["subject"] or "New website enquiry received", template_context_for_enquiry(data, customer_id=customer_id, lead_id=lead_id))
        ok, msg = send_env_email(owner_email, subject, alert_body, "<pre style='font-family:Arial, sans-serif; white-space:pre-wrap'>" + html_lib.escape(alert_body) + "</pre>")
        update_intake_delivery_status(lead_id, owner_email_status=status_text(ok, msg))
        results["owner_email"] = (ok, msg)
    else:
        update_intake_delivery_status(lead_id, owner_email_status=status_text(False, "OWNER_ALERT_EMAIL not set", skipped=True))

    owner_mobile = os.environ.get("OWNER_ALERT_MOBILE", "").strip()
    owner_sms = render_simple_template(message_template("owner_enquiry_alert_sms")["body"], template_context_for_enquiry(data, customer_id=customer_id, lead_id=lead_id))
    if owner_mobile:
        ok, msg = send_clicksend_env_sms(owner_mobile, owner_sms, customer=None, category="Service")
        update_intake_delivery_status(lead_id, owner_sms_status=status_text(ok, msg))
        results["owner_sms"] = (ok, msg)
    else:
        update_intake_delivery_status(lead_id, owner_sms_status=status_text(False, "OWNER_ALERT_MOBILE not set", skipped=True))

    update_intake_delivery_status(lead_id, follow_up_status="Follow up required")
    run("INSERT INTO customer_timeline(customer_id, note_text, created_at) VALUES (?,?,datetime('now'))", (customer_id, "Follow up required after website enquiry."))
    return results


def send_sms_gateway(to_phone, body, customer=None, communication_id=None, message_category=''):
    phone = normalize_phone(to_phone)
    if not phone:
        return False, 'No recipient phone number was provided.'
    if customer is None:
        customer = find_customer_by_phone(phone)
    if is_customer_sms_opted_out(customer):
        return False, 'This customer has opted out of SMS. Reply START from their phone to opt back in, or remove the opt out on their customer profile.'
    body = add_sms_compliance_text(body, message_category=message_category)
    sms_text = build_sms_text(body, customer)
    if not sms_text:
        return False, 'SMS body is empty.'
    s = settings()
    provider = (s['sms_gateway_name'] or '').strip().lower()
    sender_id = (s['sms_sender_id'] or s['business_name'] or '').strip()
    api_key = (s['sms_api_key'] or '').strip()
    api_secret = (s['sms_api_secret'] or '').strip()
    account_id = (s['sms_account_id'] or '').strip()
    gateway_url = (s['sms_gateway_url'] or '').strip()
    customer_id = customer['id'] if customer else None

    if not provider:
        return False, 'SMS gateway is not configured yet. Save it in Settings first.'

    try:
        if provider in ('demo', 'log', 'preview', 'test'):
            ext = f"demo-{uuid.uuid4().hex[:12]}"
            log_sms_event(customer_id, communication_id, 'Demo', 'send', phone, sender_id, sms_text, ext, 'Sent', 'outbound', {'mode': 'demo'})
            return True, f'Demo SMS marked as sent to {phone}.'

        if 'textlocal' in provider:
            if not api_key:
                return False, 'Textlocal API key is missing in Settings.'
            payload = {
                'apikey': api_key,
                'numbers': phone,
                'message': sms_text,
                'sender': (sender_id or 'CRM')[:11]
            }
            response = http_post_form('https://api.textlocal.in/send/', payload)
            try:
                data = json.loads(response)
            except Exception:
                data = {'raw': response}
            ext = ''
            messages = data.get('messages') or []
            if messages and isinstance(messages, list):
                ext = str(messages[0].get('id') or '')
            if data.get('status') == 'success' or 'success' in response.lower():
                log_sms_event(customer_id, communication_id, 'Textlocal', 'send', phone, sender_id, sms_text, ext, 'Sent', 'outbound', data)
                return True, f'SMS sent to {phone} via Textlocal.'
            log_sms_event(customer_id, communication_id, 'Textlocal', 'send_failed', phone, sender_id, sms_text, ext, 'Failed', 'outbound', data, response[:220])
            return False, f'Textlocal send failed: {response[:220]}'

        if 'twilio' in provider:
            if not account_id:
                return False, 'Twilio Account SID is missing in Settings.'
            if not api_secret:
                return False, 'Twilio Auth Token is missing in Settings.'
            if not sender_id:
                return False, 'Twilio From number is missing in Sender ID.'
            callback_base = gateway_url.rstrip('/') if gateway_url else ''
            payload = {
                'To': phone,
                'From': sender_id,
                'Body': sms_text,
            }
            if callback_base:
                payload['StatusCallback'] = callback_base + '/webhooks/sms/status/twilio'
            response = http_post_form(
                f'https://api.twilio.com/2010-04-01/Accounts/{account_id}/Messages.json',
                payload,
                headers={'Authorization': 'Basic ' + base64.b64encode(f'{account_id}:{api_secret}'.encode('utf-8')).decode('ascii')}
            )
            data = json.loads(response)
            ext = str(data.get('sid') or '')
            status = data.get('status') or 'queued'
            if ext:
                log_sms_event(customer_id, communication_id, 'Twilio', 'send', phone, sender_id, sms_text, ext, status.title(), 'outbound', data)
                return True, f'SMS accepted by Twilio for {phone}.'
            log_sms_event(customer_id, communication_id, 'Twilio', 'send_failed', phone, sender_id, sms_text, ext, 'Failed', 'outbound', data, response[:220])
            return False, f'Twilio send failed: {response[:220]}'

        if 'clicksend' in provider or 'click send' in provider:
            if not account_id:
                return False, 'ClickSend username is missing in Settings.'
            if not api_secret:
                return False, 'ClickSend API key is missing in Settings.'
            payload = {'messages': [{'source': 'python', 'to': phone, 'body': sms_text, 'from': sender_id or ''}]}
            response = http_post_basic_json('https://rest.clicksend.com/v3/sms/send', payload, account_id, api_secret)
            data = json.loads(response)
            msg_data = (((data.get('data') or {}).get('messages')) or [{}])[0]
            ext = str(msg_data.get('message_id') or '')
            status = str(msg_data.get('status') or msg_data.get('status_text') or 'queued')
            if ext or (data.get('http_code') in (200,201) or 'SUCCESS' in response.upper()):
                log_sms_event(customer_id, communication_id, 'ClickSend', 'send', phone, sender_id, sms_text, ext, status.title(), 'outbound', data)
                return True, f'SMS accepted by ClickSend for {phone}.'
            log_sms_event(customer_id, communication_id, 'ClickSend', 'send_failed', phone, sender_id, sms_text, ext, 'Failed', 'outbound', data, response[:220])
            return False, f'ClickSend send failed: {response[:220]}'

        if 'webhook' in provider:
            if not gateway_url:
                return False, 'Webhook gateway URL is missing in Settings.'
            headers = {}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'
            payload = {
                'to': phone,
                'message': sms_text,
                'sender_id': sender_id,
                'customer_id': customer_id,
                'customer_name': ((customer['first_name'] + ' ' + customer['last_name']).strip() if customer else ''),
                'status_callback_url': (gateway_url.rstrip('/') + '/status') if gateway_url else ''
            }
            response = http_post_json(gateway_url, payload, headers=headers)
            ext = ''
            try:
                data = json.loads(response)
                ext = str(data.get('id') or data.get('message_id') or '')
            except Exception:
                data = {'raw': response}
            log_sms_event(customer_id, communication_id, 'Webhook', 'send', phone, sender_id, sms_text, ext, 'Posted', 'outbound', data)
            return True, f'SMS posted to webhook for {phone}.'

        return False, 'Unsupported SMS gateway. Use Demo, Textlocal, Twilio, ClickSend, or Webhook in Settings.'
    except Exception as exc:
        log_sms_event(customer_id, communication_id, provider or 'Unknown', 'send_failed', phone, sender_id, sms_text, '', 'Failed', 'outbound', {'provider': provider}, str(exc))
        return False, f'SMS send failed: {exc}'




# --- v87 compatibility and SMS history helpers ---
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.context_processor
def inject_layout_globals():
    try:
        biz = settings()
    except Exception:
        biz = {}
    return {'biz': biz, 'app_settings': biz}


def sort_rows(rows, key, reverse=False):
    def getv(row):
        try:
            return row[key]
        except Exception:
            try:
                return row.get(key)
            except Exception:
                return None
    return sorted(list(rows or []), key=lambda r: (getv(r) is None, getv(r)), reverse=reverse)


def next_quote_number():
    row = q("SELECT quote_number FROM quotes WHERE IFNULL(quote_number,'')<>'' ORDER BY id DESC LIMIT 1", one=True)
    last = clean_str(row['quote_number']) if row else ''
    m = re.search(r'(\d+)$', last)
    num = int(m.group(1)) + 1 if m else 1001
    return f'Q-{num}'


def next_invoice_number():
    row = q("SELECT invoice_number FROM invoices WHERE IFNULL(invoice_number,'')<>'' ORDER BY id DESC LIMIT 1", one=True)
    last = clean_str(row['invoice_number']) if row else ''
    m = re.search(r'(\d+)$', last)
    num = int(m.group(1)) + 1 if m else 1001
    return f'INV-{num}'


def recurring_payment_rule_options():
    return ['Auto by Method', 'Mark Paid', 'Mark Sent', 'Manual Review']


def recurring_payment_rule_label(plan_row):
    rule = clean_str(plan_row['payment_rule'] if plan_row and 'payment_rule' in plan_row.keys() else '')
    return rule or 'Auto by Method'


def invoice_status_for_recurring_plan(plan_row):
    rule = recurring_payment_rule_label(plan_row).lower()
    method = clean_str(plan_row['collection_method'] if plan_row and 'collection_method' in plan_row.keys() else '').lower()
    if rule == 'mark paid':
        return 'Paid'
    if rule == 'mark sent':
        return 'Sent'
    if rule == 'manual review':
        return 'Draft'
    if method in ('direct debit', 'standing order'):
        return 'Paid'
    if method in ('bank transfer', 'card'):
        return 'Sent'
    return 'Draft'


def log_recurring_income_history(plan_row, invoice_id, invoice_date_obj, invoice_status, subtotal, vat, total, manual=False):
    try:
        run("""INSERT INTO recurring_income_history(recurring_income_id, customer_id, invoice_id, posted_date, invoice_status, subtotal, vat, total, manual_post, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""", (plan_row['id'], plan_row['customer_id'], invoice_id, invoice_date_obj.isoformat(), invoice_status, subtotal, vat, total, 1 if manual else 0))
    except Exception:
        pass


def customer_subscription_summary(customer_id):
    plans = q("SELECT * FROM recurring_income WHERE archived_at IS NULL AND customer_id=? ORDER BY id DESC", (customer_id,))
    history = q("SELECT * FROM recurring_income_history WHERE customer_id=? ORDER BY id DESC LIMIT 20", (customer_id,))
    active_plans = [r for r in plans if int(r['active'] or 0) == 1]
    paid_total = round(sum(float(r['total'] or 0) for r in history if clean_str(r['invoice_status']).lower() == 'paid'), 2)
    pending_total = round(sum(float(r['total'] or 0) for r in history if clean_str(r['invoice_status']).lower() != 'paid'), 2)
    return {
        'plans': plans,
        'history': history,
        'active_count': len(active_plans),
        'plan_count': len(active_plans),
        'history_count': len(history),
        'active_value': round(sum(float(r['amount'] or 0) for r in active_plans), 2),
        'paid_total': paid_total,
        'pending_total': pending_total,
    }


def customer_last_contact_map(customer_ids):
    ids = [int(x) for x in (customer_ids or []) if str(x).isdigit()]
    if not ids:
        return {}
    placeholders = ','.join(['?'] * len(ids))
    rows = q(f"SELECT customer_id, MAX(created_at) AS last_contact FROM communications WHERE customer_id IN ({placeholders}) GROUP BY customer_id", tuple(ids))
    return {int(r['customer_id']): r['last_contact'] for r in rows if r['customer_id'] is not None}


def contact_badge_text(last_contact):
    if not last_contact:
        return 'No contact logged'
    try:
        d = datetime.fromisoformat(str(last_contact).replace(' ', 'T'))
        days = (datetime.now() - d).days
        if days <= 0:
            return 'Contacted today'
        if days == 1:
            return 'Contacted yesterday'
        return f'Contacted {days} days ago'
    except Exception:
        return f'Last contact {last_contact}'


def customer_full_name(row):
    if not row:
        return "Customer"
    try:
        return clean_str(f"{row['first_name'] or ''} {row['last_name'] or ''}") or "Customer"
    except Exception:
        return "Customer"


def customer_address_text(row):
    if not row:
        return ""
    parts = []
    for key in ("address", "town", "postcode"):
        try:
            value = clean_str(row[key])
        except Exception:
            value = ""
        if value:
            parts.append(value)
    return ", ".join(parts)


def directions_url_for_customer(row):
    address = customer_address_text(row)
    if not address:
        return ""
    return "https://www.google.com/maps/search/?api=1&query=" + quote(address)


def day_run_message(kind, job):
    name = clean_str(job["first_name"]) or "there"
    business = settings()["business_name"] or "The Carpet Cleaning Company"
    phone = settings()["phone"] or ""
    job_date = clean_str(job["job_date"]) or uk_today().isoformat()
    review_link = settings()["review_link"] or "[GOOGLE REVIEW LINK]"
    if kind == "coming":
        return (
            f"Hi {name},\n\n"
            f"We are on our way for your carpet cleaning appointment today.\n\n"
            f"Thanks\nPaul\n{business}"
        )
    if kind == "reminder":
        return (
            f"Hi {name},\n\n"
            f"Just a reminder that your carpet cleaning appointment is booked for {job_date}.\n\n"
            f"Thanks\nPaul\n{business}"
        )
    if kind == "finished":
        return (
            f"Hi {name},\n\n"
            "The work is now complete. Thank you for using us today.\n\n"
            f"If you need anything, you can contact me on {phone}.\n\n"
            f"Thanks\nPaul\n{business}"
        )
    if kind == "review":
        return (
            f"Hi {name},\n\n"
            f"Thank you for using {business}.\n\n"
            "If you are happy with the work, I would really appreciate a quick Google review.\n\n"
            f"{review_link}\n\n"
            "Thank you\nPaul"
        )
    return ""


def log_customer_message(customer_id, channel, subject, body):
    if not customer_id:
        return None
    return run("INSERT INTO communications(customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (
        customer_id, channel, subject, body
    ))


def create_invoice_for_job(job, status="Draft", note_extra=""):
    existing_invoice = q("SELECT id FROM invoices WHERE job_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC LIMIT 1", (job["id"],), one=True)
    if existing_invoice:
        return existing_invoice["id"], False
    payload = {}
    if job["quote_id"]:
        qr = q("SELECT payload_json FROM quotes WHERE id=?", (job["quote_id"],), one=True)
        if qr and qr["payload_json"]:
            payload = json.loads(qr["payload_json"])
    calc = calc_from_payload(payload) if payload else {
        "subtotal": float(job["amount"] or 0),
        "vat": 0.0,
        "total": float(job["amount"] or 0),
        "lines": [],
        "raw_total": float(job["amount"] or 0),
        "minimum": float(settings()["minimum_charge"] or 100),
        "include_vat": False
    }
    notes = append_note(job["notes"] or "", note_extra)
    invoice_id = run("""INSERT INTO invoices(customer_id, job_id, quote_id, invoice_number, invoice_date, due_date, status, subtotal, vat, total, payload_json, notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
        job["customer_id"], job["id"], job["quote_id"], next_invoice_number(),
        uk_today().isoformat(), uk_today().isoformat(),
        status, calc["subtotal"], calc["vat"], calc["total"], json.dumps(payload), notes
    ))
    return invoice_id, True


def annotate_rows_with_last_contact(rows, customer_id_key='id', key=None):
    if key is not None:
        customer_id_key = key
    rows = list(rows or [])
    mapping = customer_last_contact_map([row[customer_id_key] for row in rows if customer_id_key in row.keys() and row[customer_id_key]])
    enriched = []
    for row in rows:
        item = dict(row)
        cid = item.get(customer_id_key)
        item['last_contact'] = mapping.get(int(cid)) if cid not in (None, '') else None
        item['last_contact_badge'] = contact_badge_text(item['last_contact'])
        enriched.append(item)
    return enriched


def http_post_basic_json(url, payload, username, password):
    raw = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=raw, method='POST')
    token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
    req.add_header('Authorization', f'Basic {token}')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('utf-8', errors='ignore')



def sms_stop_keywords():
    return {'stop', 'stopall', 'unsubscribe', 'cancel', 'end', 'quit'}


def sms_start_keywords():
    return {'start', 'unstop', 'subscribe', 'yes'}


def extract_sms_keyword(body):
    text = re.sub(r'[^a-z]', ' ', str(body or '').lower())
    parts = [p for p in text.split() if p]
    return parts[0] if parts else ''


def is_customer_sms_opted_out(customer):
    try:
        return bool(customer and int(customer['sms_opt_out'] or 0) == 1)
    except Exception:
        return False


def set_customer_sms_opt_out(customer_id, opted_out=True, source='Manual', note_body=''):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run("UPDATE customers SET sms_opt_out=?, sms_opt_out_at=?, sms_opt_out_source=? WHERE id=?", (
        1 if opted_out else 0,
        ts if opted_out else '',
        source or ('Inbound SMS' if opted_out else 'Manual'),
        customer_id
    ))
    status = 'Opted Out' if opted_out else 'Opted In'
    note = f'SMS {status.lower()} via {source or "manual"}.'
    if note_body:
        note += f' Message: {note_body[:200]}'
    run("INSERT INTO communications(customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (
        customer_id, 'SMS', f'SMS {status}', note
    ))


def active_sms_templates():
    try:
        return q("SELECT * FROM sms_templates WHERE IFNULL(is_active,1)=1 ORDER BY IFNULL(category,'') COLLATE NOCASE, name COLLATE NOCASE")
    except Exception:
        return []


def sms_thread_summaries(search_text='', unread_only=False):
    search_text = clean_str(search_text)
    rows = q("""SELECT c.id AS customer_id,
                       c.first_name || ' ' || c.last_name AS customer_name,
                       c.phone,
                       c.sms_opt_out,
                       c.sms_opt_out_at,
                       c.sms_opt_out_source,
                       MAX(e.created_at) AS last_message_at,
                       SUM(CASE WHEN lower(IFNULL(e.direction,''))='inbound' THEN 1 ELSE 0 END) AS inbound_count,
                       SUM(CASE WHEN lower(IFNULL(e.direction,''))='outbound' THEN 1 ELSE 0 END) AS outbound_count,
                       COUNT(*) AS total_count,
                       MAX(CASE WHEN lower(IFNULL(e.direction,''))='inbound' THEN e.created_at ELSE '' END) AS last_inbound_at,
                       MAX(CASE WHEN lower(IFNULL(e.direction,''))='outbound' THEN e.created_at ELSE '' END) AS last_outbound_at,
                       MAX(CASE WHEN e.created_at = (SELECT MAX(e2.created_at) FROM sms_events e2 WHERE e2.customer_id = c.id) THEN e.direction ELSE '' END) AS last_direction,
                       MAX(CASE WHEN e.created_at = (SELECT MAX(e2.created_at) FROM sms_events e2 WHERE e2.customer_id = c.id) THEN e.body ELSE '' END) AS last_body,
                       IFNULL(ts.last_viewed_at,'') AS last_viewed_at,
                       SUM(CASE WHEN lower(IFNULL(e.direction,''))='inbound' AND IFNULL(e.created_at,'') > IFNULL(ts.last_viewed_at,'') THEN 1 ELSE 0 END) AS unread_count
                FROM sms_events e
                LEFT JOIN customers c ON c.id = e.customer_id
                LEFT JOIN sms_thread_state ts ON ts.customer_id = c.id
                WHERE c.id IS NOT NULL
                  AND (?='' OR IFNULL(c.first_name || ' ' || c.last_name,'') LIKE ? OR IFNULL(c.phone,'') LIKE ? OR IFNULL(e.body,'') LIKE ?)
                GROUP BY c.id, c.first_name, c.last_name, c.phone, c.sms_opt_out, c.sms_opt_out_at, c.sms_opt_out_source, ts.last_viewed_at
                ORDER BY MAX(e.id) DESC""", (search_text, f'%{search_text}%', f'%{search_text}%', f'%{search_text}%'))
    if unread_only:
        rows = [r for r in rows if int(r['unread_count'] or 0) > 0]
    return rows


def sms_thread_rows(customer_id=None, phone=None, limit=200):
    where = []
    params = []
    if customer_id:
        where.append("e.customer_id=?")
        params.append(customer_id)
    elif phone:
        where.append("replace(replace(replace(ifnull(e.to_phone,''),' ',''),'-',''),'+','') LIKE ? OR replace(replace(replace(ifnull(e.from_phone,''),' ',''),'-',''),'+','') LIKE ?")
        ph = f"%{phone.replace('+','')}%"
        params.extend([ph, ph])
    else:
        return []
    sql = f"""SELECT e.*, c.first_name || ' ' || c.last_name AS customer_name
              FROM sms_events e
              LEFT JOIN customers c ON c.id = e.customer_id
              WHERE {' AND '.join(where)}
              ORDER BY e.id DESC LIMIT ?"""
    params.append(limit)
    rows = q(sql, tuple(params))
    return list(reversed(rows))


def send_email_smtp(to_email, subject, body, customer=None):
    s = settings()
    sender = clean_str(s['gmail_address'] if 'gmail_address' in s.keys() else '')
    password = clean_str(s['gmail_app_password'] if 'gmail_app_password' in s.keys() else '')
    if not sender or not password:
        return False, 'Gmail address or app password is missing in Settings.'
    recipients = [e.strip() for e in str(to_email or '').split(',') if e.strip()]
    if not recipients:
        return False, 'No email recipient was supplied.'
    for email in recipients:
        if not is_valid_email(email):
            return False, f'Invalid email address: {email}'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = merge_message_text(subject or '', customer)
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    rendered_body = merge_message_text(body or '', customer)
    footer = merge_message_text(s['email_footer_html'] if 'email_footer_html' in s.keys() else '', customer)
    html = build_email_html(rendered_body, footer)
    text_body = strip_html_for_sms(rendered_body + ('\n\n' + strip_html_for_sms(footer) if footer else ''))
    msg.attach(MIMEText(text_body or ' ', 'plain', 'utf-8'))
    msg.attach(MIMEText(html or ' ', 'html', 'utf-8'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context()) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        return True, f'Email sent to {", ".join(recipients)}.'
    except Exception as exc:
        return False, str(exc)


def evaluate_follow_up_results(rows):
    evaluated = []
    for row in list(rows or []):
        item = dict(row)
        customer_id = item.get('customer_id')
        sent_at = item.get('sent_at') or item.get('created_at')
        activity_at = latest_customer_activity_after(customer_id, sent_at) if customer_id and sent_at else None
        item['activity_at'] = activity_at
        item['reply_state'] = 'Activity Since Sent' if activity_at else 'No Activity Yet'
        evaluated.append(item)
    return evaluated


def follow_up_dashboard_summary(rows, days=60):
    rows = list(rows or [])
    total = len(rows)
    activity_count = sum(1 for r in rows if r.get('activity_at'))
    waiting_count = total - activity_count
    return {'days': days, 'total': total, 'activity_count': activity_count, 'waiting_count': waiting_count, 'recent_waiting': [r for r in rows if not r.get('activity_at')][:8], 'recent_activity': [r for r in rows if r.get('activity_at')][:8]}


def calc_from_payload(payload):
    payload = payload or {}
    lines = list(payload.get('lines') or [])
    subtotal = round(sum(float(line.get('line_total') or 0) for line in lines), 2)
    vat = round(float(payload.get('vat') or 0), 2)
    total = round(float(payload.get('total') or (subtotal + vat)), 2)
    raw_total = round(float(payload.get('raw_total') or total), 2)
    return {'lines': lines, 'subtotal': subtotal, 'vat': vat, 'total': total, 'raw_total': raw_total, 'minimum': 0}


def log_sms_event(customer_id, communication_id, provider, event_type, to_phone, from_phone, body, external_id='', status='Logged', direction='outbound', payload=None, error_text=''):
    return run("""INSERT INTO sms_events(customer_id, communication_id, provider, event_type, to_phone, from_phone, body, external_id, status, direction, payload_json, error_text, created_at, updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""", (customer_id, communication_id, provider, event_type, to_phone, from_phone, body, external_id, status, direction, json.dumps(payload or {}), error_text))


def update_sms_status_by_external(external_id, status='', payload=None, error_text=''):
    if not external_id:
        return
    run("UPDATE sms_events SET status=?, payload_json=?, error_text=CASE WHEN ?<>'' THEN ? ELSE error_text END, updated_at=datetime('now') WHERE external_id=?", (status or 'Updated', json.dumps(payload or {}), error_text or '', error_text or '', external_id))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        business_name TEXT DEFAULT 'The Carpet Cleaning Company',
        phone TEXT DEFAULT '07802 563213',
        email TEXT DEFAULT '',
        website TEXT DEFAULT '',
        address TEXT DEFAULT '',
        accent TEXT DEFAULT '#1f5fbf',
        review_link TEXT DEFAULT '',
        username TEXT DEFAULT 'admin',
        password TEXT DEFAULT 'admin123',
        minimum_charge REAL DEFAULT 100,
        vat_rate REAL DEFAULT 0.20,
        logo_filename TEXT DEFAULT '',
        dashboard_carpet_image TEXT DEFAULT '',
        dashboard_upholstery_image TEXT DEFAULT '',
        email_footer_html TEXT DEFAULT '',
        sms_footer_text TEXT DEFAULT '',
        bg_darkness INTEGER DEFAULT 58,
        bg_palette TEXT DEFAULT 'classic_blue',
        bg_color TEXT DEFAULT '#c7d7ea',
        sidebar_color TEXT DEFAULT '#102744',
        gmail_address TEXT DEFAULT '',
        gmail_app_password TEXT DEFAULT '',
        smtp_from_name TEXT DEFAULT '',
        test_email TEXT DEFAULT '',
        sms_gateway_name TEXT DEFAULT '',
        sms_sender_id TEXT DEFAULT '',
        sms_api_key TEXT DEFAULT '',
        sms_gateway_url TEXT DEFAULT '',
        sms_test_number TEXT DEFAULT '',
        sms_account_id TEXT DEFAULT '',
        sms_api_secret TEXT DEFAULT '',
        payment_rule TEXT DEFAULT '',
        sms_opt_out_message TEXT DEFAULT 'You have been opted out of SMS updates. Reply START to opt back in.',
        sms_stop_keywords TEXT DEFAULT 'STOP,STOPALL,UNSUBSCRIBE,CANCEL,END,QUIT',
        sms_start_keywords TEXT DEFAULT 'START,UNSTOP,SUBSCRIBE',
        sms_marketing_opt_out_notice TEXT DEFAULT 'Reply STOP to opt out.',
        sms_append_opt_out_on_marketing INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS pricing_config (
        id INTEGER PRIMARY KEY CHECK (id=1),
        data_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        town TEXT,
        postcode TEXT,
        source TEXT,
        tags TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        archived_at TEXT,
        sms_opt_out INTEGER DEFAULT 0,
        sms_opt_out_at TEXT DEFAULT '',
        sms_opt_out_source TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS customer_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        note_text TEXT,
        photo_filename TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        quote_number TEXT,
        title TEXT,
        quote_date TEXT,
        valid_until TEXT,
        status TEXT DEFAULT 'Draft',
        subtotal REAL DEFAULT 0,
        vat REAL DEFAULT 0,
        total REAL DEFAULT 0,
        payload_json TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS quote_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER,
        item_name TEXT,
        method TEXT,
        quantity REAL DEFAULT 0,
        unit_price REAL DEFAULT 0,
        line_total REAL DEFAULT 0,
        group_name TEXT
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        quote_id INTEGER,
        title TEXT,
        service_type TEXT,
        job_date TEXT,
        status TEXT DEFAULT 'Booked',
        amount REAL DEFAULT 0,
        assigned_to TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        job_id INTEGER,
        quote_id INTEGER,
        invoice_number TEXT,
        invoice_date TEXT,
        due_date TEXT,
        status TEXT DEFAULT 'Draft',
        subtotal REAL DEFAULT 0,
        vat REAL DEFAULT 0,
        total REAL DEFAULT 0,
        payload_json TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        reminder_count INTEGER DEFAULT 0,
        last_reminder_sent_at TEXT
    );
    CREATE TABLE IF NOT EXISTS xero_sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        local_type TEXT,
        local_id INTEGER,
        action TEXT,
        status TEXT,
        message TEXT,
        payload_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_date TEXT,
        category TEXT,
        supplier TEXT,
        description TEXT,
        amount REAL DEFAULT 0,
        vat_amount REAL DEFAULT 0,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        archived_at TEXT
    );
    CREATE TABLE IF NOT EXISTS recurring_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_date TEXT,
        next_due_date TEXT,
        category TEXT,
        supplier TEXT,
        description TEXT,
        amount REAL DEFAULT 0,
        vat_amount REAL DEFAULT 0,
        notes TEXT,
        frequency TEXT,
        last_posted_at TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        archived_at TEXT
    );
    CREATE TABLE IF NOT EXISTS recurring_income (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        payer_name TEXT,
        start_date TEXT,
        next_due_date TEXT,
        description TEXT,
        amount REAL DEFAULT 0,
        include_vat INTEGER DEFAULT 0,
        frequency TEXT,
        collection_method TEXT,
        auto_payment_rule TEXT DEFAULT 'Default by Method',
        notes TEXT,
        last_posted_at TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        archived_at TEXT
    );
    CREATE TABLE IF NOT EXISTS recurring_income_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recurring_income_id INTEGER,
        customer_id INTEGER,
        invoice_id INTEGER,
        posted_date TEXT,
        invoice_status TEXT,
        subtotal REAL DEFAULT 0,
        vat REAL DEFAULT 0,
        total REAL DEFAULT 0,
        manual_post INTEGER DEFAULT 0,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS communications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        channel TEXT,
        subject TEXT,
        body TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS communication_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        channel TEXT,
        subject TEXT,
        body TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS campaign_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT,
        segment TEXT,
        mode TEXT,
        title TEXT,
        subject TEXT,
        body TEXT,
        status TEXT DEFAULT 'Logged',
        recipient_count INTEGER DEFAULT 0,
        sent_count INTEGER DEFAULT 0,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS campaign_batch_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_batch_id INTEGER,
        customer_id INTEGER,
        recipient TEXT,
        phone TEXT,
        item_status TEXT DEFAULT 'Logged',
        sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
        error_text TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS quote_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        town TEXT,
        postcode TEXT,
        notes TEXT,
        status TEXT DEFAULT 'New',
        payload_json TEXT,
        estimate_total REAL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS intake_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT,
        email TEXT,
        full_address TEXT,
        postcode TEXT,
        google_maps_link TEXT,
        what3words TEXT,
        job_notes TEXT,
        rooms_areas TEXT,
        what_cleaned TEXT DEFAULT '',
        number_rooms TEXT DEFAULT '',
        upholstery TEXT DEFAULT '',
        rugs TEXT DEFAULT '',
        stains TEXT DEFAULT '',
        pets TEXT DEFAULT '',
        parking TEXT DEFAULT '',
        preferred_days_times TEXT DEFAULT '',
        additional_notes TEXT DEFAULT '',
        preferred_date TEXT,
        preferred_time TEXT,
        photo_filename TEXT,
        status TEXT DEFAULT 'New',
        review_notes TEXT DEFAULT '',
        customer_id INTEGER,
        job_id INTEGER,
        xero_contact_id TEXT DEFAULT '',
        xero_sent_at TEXT DEFAULT '',
        xero_error TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS xero_tokens (
        id INTEGER PRIMARY KEY CHECK (id=1),
        access_token TEXT,
        refresh_token TEXT,
        expires_at INTEGER DEFAULT 0,
        tenant_id TEXT,
        token_json TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS customer_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        job_id INTEGER,
        rating INTEGER DEFAULT 0,
        feedback_text TEXT,
        source TEXT DEFAULT 'Manual',
        review_requested INTEGER DEFAULT 0,
        review_link_sent_at TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS future_reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        job_id INTEGER,
        reminder_date TEXT,
        title TEXT,
        notes TEXT,
        reminder_type TEXT DEFAULT 'Follow up',
        status TEXT DEFAULT 'Open',
        completed_at TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sms_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        communication_id INTEGER,
        provider TEXT,
        event_type TEXT,
        to_phone TEXT,
        from_phone TEXT,
        body TEXT,
        external_id TEXT,
        status TEXT,
        direction TEXT,
        payload_json TEXT,
        error_text TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS sms_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        body TEXT,
        category TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT,
        usage_type TEXT DEFAULT 'General',
        auto_append_opt_out INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sms_thread_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        note_text TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS sms_thread_state (
        customer_id INTEGER PRIMARY KEY,
        last_viewed_at TEXT DEFAULT '',
        muted INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS message_templates (
        template_key TEXT PRIMARY KEY,
        name TEXT,
        subject TEXT,
        body TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur = conn.cursor()
    # Safe additive migrations for older databases
    migrations = [
        ("settings", "bg_darkness", "INTEGER DEFAULT 58"),
        ("settings", "bg_palette", "TEXT DEFAULT 'classic_blue'"),
        ("settings", "sidebar_color", "TEXT DEFAULT '#102744'"),
        ("settings", "bg_color", "TEXT DEFAULT '#c7d7ea'"),
        ("settings", "gmail_address", "TEXT DEFAULT ''"),
        ("settings", "gmail_app_password", "TEXT DEFAULT ''"),
        ("settings", "smtp_from_name", "TEXT DEFAULT ''"),
        ("settings", "test_email", "TEXT DEFAULT ''"),
        ("settings", "sms_gateway_name", "TEXT DEFAULT ''"),
        ("settings", "sms_sender_id", "TEXT DEFAULT ''"),
        ("settings", "sms_api_key", "TEXT DEFAULT ''"),
        ("settings", "sms_gateway_url", "TEXT DEFAULT ''"),
        ("settings", "sms_test_number", "TEXT DEFAULT ''"),
        ("settings", "sms_account_id", "TEXT DEFAULT ''"),
        ("settings", "sms_api_secret", "TEXT DEFAULT ''"),
        ("settings", "payment_rule", "TEXT DEFAULT ''"),
        ("settings", "sms_opt_out_message", "TEXT DEFAULT 'You have been opted out of SMS updates. Reply START to opt back in.'"),
        ("settings", "sms_stop_keywords", "TEXT DEFAULT 'STOP,STOPALL,UNSUBSCRIBE,CANCEL,END,QUIT'"),
        ("settings", "sms_start_keywords", "TEXT DEFAULT 'START,UNSTOP,SUBSCRIBE'"),
        ("settings", "sms_marketing_opt_out_notice", "TEXT DEFAULT 'Reply STOP to opt out.'"),
        ("settings", "sms_append_opt_out_on_marketing", "INTEGER DEFAULT 1"),
        ("customers", "archived_at", "TEXT"),
        ("customers", "sms_opt_out", "INTEGER DEFAULT 0"),
        ("customers", "sms_opt_out_at", "TEXT DEFAULT ''"),
        ("customers", "sms_opt_out_source", "TEXT DEFAULT ''"),
        ("customers", "xero_contact_id", "TEXT DEFAULT ''"),
        ("customers", "xero_contact_synced_at", "TEXT DEFAULT ''"),
        ("customers", "xero_contact_error", "TEXT DEFAULT ''"),
        ("customers", "workflow_status", "TEXT DEFAULT 'new_enquiry'"),
        ("customers", "next_action", "TEXT DEFAULT 'Send booking form'"),
        ("customers", "workflow_notes", "TEXT DEFAULT ''"),
        ("customers", "last_updated", "TEXT DEFAULT ''"),
        ("customers", "form_sent_at", "TEXT DEFAULT ''"),
        ("customers", "form_completed_at", "TEXT DEFAULT ''"),
        ("customers", "approved_at", "TEXT DEFAULT ''"),
        ("customers", "xero_synced_at", "TEXT DEFAULT ''"),
        ("customers", "quote_created_at", "TEXT DEFAULT ''"),
        ("customers", "quote_sent_at", "TEXT DEFAULT ''"),
        ("customers", "quote_accepted_at", "TEXT DEFAULT ''"),
        ("customers", "job_booked_at", "TEXT DEFAULT ''"),
        ("customers", "reminder_sent_at", "TEXT DEFAULT ''"),
        ("customers", "job_completed_at", "TEXT DEFAULT ''"),
        ("customers", "invoice_created_at", "TEXT DEFAULT ''"),
        ("customers", "invoice_sent_at", "TEXT DEFAULT ''"),
        ("customers", "payment_received_at", "TEXT DEFAULT ''"),
        ("customers", "review_request_sent_at", "TEXT DEFAULT ''"),
        ("customers", "workflow_history", "TEXT DEFAULT '[]'"),
        ("invoices", "reminder_count", "INTEGER DEFAULT 0"),
        ("invoices", "last_reminder_sent_at", "TEXT"),
        ("invoices", "xero_invoice_id", "TEXT DEFAULT ''"),
        ("invoices", "xero_invoice_number", "TEXT DEFAULT ''"),
        ("invoices", "xero_status", "TEXT DEFAULT ''"),
        ("invoices", "xero_amount_due", "REAL DEFAULT 0"),
        ("invoices", "xero_amount_paid", "REAL DEFAULT 0"),
        ("invoices", "xero_synced_at", "TEXT DEFAULT ''"),
        ("invoices", "xero_error", "TEXT DEFAULT ''"),
        ("invoices", "xero_last_payload", "TEXT DEFAULT ''"),
        ("expenses", "archived_at", "TEXT"),
        ("recurring_income", "auto_payment_rule", "TEXT DEFAULT 'Default by Method'"),
        ("sms_templates", "usage_type", "TEXT DEFAULT 'General'"),
        ("sms_templates", "auto_append_opt_out", "INTEGER DEFAULT 0"),
        ("intake_submissions", "review_notes", "TEXT DEFAULT ''"),
        ("intake_submissions", "customer_id", "INTEGER"),
        ("intake_submissions", "job_id", "INTEGER"),
        ("intake_submissions", "xero_contact_id", "TEXT DEFAULT ''"),
        ("intake_submissions", "xero_sent_at", "TEXT DEFAULT ''"),
        ("intake_submissions", "xero_error", "TEXT DEFAULT ''"),
        ("intake_submissions", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
        ("intake_submissions", "what_cleaned", "TEXT DEFAULT ''"),
        ("intake_submissions", "number_rooms", "TEXT DEFAULT ''"),
        ("intake_submissions", "upholstery", "TEXT DEFAULT ''"),
        ("intake_submissions", "rugs", "TEXT DEFAULT ''"),
        ("intake_submissions", "stains", "TEXT DEFAULT ''"),
        ("intake_submissions", "pets", "TEXT DEFAULT ''"),
        ("intake_submissions", "parking", "TEXT DEFAULT ''"),
        ("intake_submissions", "preferred_days_times", "TEXT DEFAULT ''"),
        ("intake_submissions", "additional_notes", "TEXT DEFAULT ''"),
        ("intake_submissions", "source", "TEXT DEFAULT ''"),
        ("intake_submissions", "marketing_consent", "TEXT DEFAULT ''"),
        ("intake_submissions", "xero_sync_status", "TEXT DEFAULT 'Pending'"),
        ("intake_submissions", "customer_email_status", "TEXT DEFAULT 'Pending'"),
        ("intake_submissions", "customer_sms_status", "TEXT DEFAULT 'Pending'"),
        ("intake_submissions", "owner_email_status", "TEXT DEFAULT 'Pending'"),
        ("intake_submissions", "owner_sms_status", "TEXT DEFAULT 'Pending'"),
        ("intake_submissions", "follow_up_status", "TEXT DEFAULT 'Follow up required'"),
        ("customer_feedback", "review_requested", "INTEGER DEFAULT 0"),
        ("customer_feedback", "review_link_sent_at", "TEXT DEFAULT ''"),
        ("future_reminders", "reminder_type", "TEXT DEFAULT 'Follow up'"),
        ("future_reminders", "completed_at", "TEXT DEFAULT ''"),
    ]
    for table, col, decl in migrations:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    conn.execute("INSERT OR IGNORE INTO pricing_config (id, data_json) VALUES (1, ?)", (json.dumps(PRICING_DEFAULTS),))
    for key, template in DEFAULT_MESSAGE_TEMPLATES.items():
        conn.execute(
            "INSERT OR IGNORE INTO message_templates(template_key, name, subject, body, updated_at) VALUES (?,?,?,?,datetime('now'))",
            (key, template["name"], template["subject"], template["body"]),
        )
    conn.execute(
        """UPDATE message_templates
              SET body=?, updated_at=datetime('now')
            WHERE template_key='customer_enquiry_sms'
              AND body IN (?,?,?)""",
        (
            DEFAULT_MESSAGE_TEMPLATES["customer_enquiry_sms"]["body"],
            "Thank you for contacting The Carpet Cleaning Company. We have received your enquiry and will get back to you shortly. You can view our work and reviews here: www.thecarpetcleaningcrew.co.uk",
            "Thank you for contacting The Carpet Cleaning Company. We have received your enquiry and Paul will call you from 07802 563213. Please follow us on Facebook to see our work: https://www.facebook.com/profile.php?id=61559013150413 Google reviews: https://share.google/XHQjHHLwpmlugHP0c",
            "Thank you for contacting The Carpet Cleaning Company. We have received your enquiry. Please follow us on Facebook to see our work: https://www.facebook.com/profile.php?id=61559013150413 Google reviews: https://share.google/XHQjHHLwpmlugHP0c",
        ),
    )
    conn.execute(
        """UPDATE message_templates
              SET subject=?, body=?, updated_at=datetime('now')
            WHERE template_key='customer_enquiry_email'
              AND (
                    body LIKE '%Website: https://www.thecarpetcleaningcrew.co.uk%'
                 OR body LIKE '%To help us prepare a faster and more accurate quotation%'
                 OR body LIKE '%please send a few photos%'
                 OR body LIKE '%Thank you for your enquiry. We have received your message%'
              )""",
        (
            DEFAULT_MESSAGE_TEMPLATES["customer_enquiry_email"]["subject"],
            DEFAULT_MESSAGE_TEMPLATES["customer_enquiry_email"]["body"],
        ),
    )
    conn.execute(
        """UPDATE message_templates
              SET body=?, updated_at=datetime('now')
            WHERE template_key='owner_enquiry_alert_sms'
              AND body LIKE 'New website enquiry for The Carpet Cleaning Company.%'""",
        (DEFAULT_MESSAGE_TEMPLATES["owner_enquiry_alert_sms"]["body"],),
    )
    conn.commit()
    conn.close()
    try:
        ensure_backup_dir()
    except Exception:
        pass


def find_customer_by_phone(phone):
    phone = normalize_phone(phone)
    if not phone:
        return None
    try:
        rows = q("SELECT * FROM customers WHERE IFNULL(phone,'')<>''")
    except Exception:
        return None
    for row in rows:
        if normalize_phone(row['phone']) == phone:
            return row
    return None


WORKFLOW_STAGES = [
    {"key": "new_enquiry", "label": "New enquiry received", "next": "Send booking form", "button": "Send Booking Form"},
    {"key": "booking_form_sent", "label": "Booking form sent", "next": "Wait for customer booking form", "button": "Mark Form Sent"},
    {"key": "form_completed", "label": "Customer completed booking form", "next": "Review customer information", "button": "Review Form"},
    {"key": "waiting_for_review", "label": "Form waiting for review", "next": "Approve or reject customer details", "button": "Approve Customer"},
    {"key": "customer_approved", "label": "Customer approved", "next": "Sync customer to Xero", "button": "Sync to Xero"},
    {"key": "xero_synced", "label": "Customer synced to Xero", "next": "Create quote", "button": "Create Quote"},
    {"key": "quote_created", "label": "Quote created", "next": "Send quote", "button": "Mark Quote Sent"},
    {"key": "quote_sent", "label": "Quote sent", "next": "Wait for quote acceptance", "button": "Mark Quote Accepted"},
    {"key": "quote_accepted", "label": "Quote accepted", "next": "Book job", "button": "Book Job"},
    {"key": "job_booked", "label": "Job booked", "next": "Send appointment reminder", "button": "Send Reminder"},
    {"key": "reminder_sent", "label": "Reminder sent", "next": "Complete the job", "button": "Complete Job"},
    {"key": "job_completed", "label": "Job completed", "next": "Create invoice", "button": "Create Invoice"},
    {"key": "invoice_created", "label": "Invoice created", "next": "Send invoice", "button": "Mark Invoice Sent"},
    {"key": "invoice_sent", "label": "Invoice sent", "next": "Wait for payment", "button": "Mark Payment Received"},
    {"key": "payment_received", "label": "Payment received", "next": "Send review request", "button": "Send Review Request"},
    {"key": "review_request_sent", "label": "Review request sent", "next": "Complete workflow", "button": "Mark Completed"},
    {"key": "completed", "label": "Completed", "next": "Nothing due", "button": "Completed"},
]

WORKFLOW_BY_KEY = {stage["key"]: stage for stage in WORKFLOW_STAGES}
WORKFLOW_INDEX = {stage["key"]: idx for idx, stage in enumerate(WORKFLOW_STAGES)}
WORKFLOW_TIMESTAMP_FIELDS = {
    "booking_form_sent": "form_sent_at",
    "form_completed": "form_completed_at",
    "waiting_for_review": "form_completed_at",
    "customer_approved": "approved_at",
    "xero_synced": "xero_synced_at",
    "quote_created": "quote_created_at",
    "quote_sent": "quote_sent_at",
    "quote_accepted": "quote_accepted_at",
    "job_booked": "job_booked_at",
    "reminder_sent": "reminder_sent_at",
    "job_completed": "job_completed_at",
    "invoice_created": "invoice_created_at",
    "invoice_sent": "invoice_sent_at",
    "payment_received": "payment_received_at",
    "review_request_sent": "review_request_sent_at",
}

WORKFLOW_DASHBOARD_COLUMNS = [
    ("needs_form_sent", "Needs Form Sent", ["new_enquiry"]),
    ("waiting_for_form", "Waiting For Form", ["booking_form_sent"]),
    ("needs_review", "Needs Review", ["form_completed", "waiting_for_review"]),
    ("needs_approval", "Needs Approval", ["waiting_for_review"]),
    ("needs_quote", "Needs Quote", ["customer_approved", "xero_synced"]),
    ("waiting_quote_acceptance", "Waiting For Quote Acceptance", ["quote_created", "quote_sent"]),
    ("needs_booking", "Needs Booking", ["quote_accepted"]),
    ("upcoming_jobs", "Upcoming Jobs", ["job_booked", "reminder_sent"]),
    ("needs_invoice", "Needs Invoice", ["job_completed"]),
    ("awaiting_payment", "Awaiting Payment", ["invoice_created", "invoice_sent"]),
    ("needs_review_request", "Needs Review Request", ["payment_received"]),
    ("completed", "Completed", ["review_request_sent", "completed"]),
]


def workflow_stage(status):
    return WORKFLOW_BY_KEY.get(clean_str(status) or "new_enquiry", WORKFLOW_BY_KEY["new_enquiry"])


def workflow_history_items(customer):
    if not customer:
        return []
    try:
        data = json.loads(customer["workflow_history"] or "[]")
    except (TypeError, ValueError):
        data = []
    return data if isinstance(data, list) else []


def customer_name(customer):
    if not customer:
        return "Customer"
    return clean_str(f"{customer['first_name'] or ''} {customer['last_name'] or ''}") or "Customer"


def row_get(row, key, default=""):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def booking_form_url(customer=None):
    if customer:
        return url_for("booking_form", customer_id=customer["id"], _external=True)
    return url_for("booking_form", _external=True)


def booking_form_message(customer):
    form_link = booking_form_url(customer)
    return (
        "Hi,\n\n"
        "Please fill in this quick booking form so I have the correct information for your quote and booking.\n\n"
        f"{form_link}\n\n"
        "Thanks\n\n"
        "Paul\n"
        "The Carpet Cleaning Company"
    )


def reminder_message(customer):
    return (
        "Hi,\n\n"
        "Just a reminder that your carpet cleaning appointment is booked for [DATE] at [TIME].\n\n"
        "Thanks\n\n"
        "Paul\n"
        "The Carpet Cleaning Company"
    )


def review_request_message(customer):
    review_link = settings()["review_link"] or "[GOOGLE REVIEW LINK]"
    first_name = clean_str(customer["first_name"]) if customer else ""
    greeting_name = first_name or customer_name(customer)
    return (
        f"Hi {greeting_name}\n\n"
        "Thank you for using The Carpet Cleaning Company.\n\n"
        "If you are happy with the work, I would really appreciate a quick Google review.\n\n"
        f"{review_link}\n\n"
        "Thank you\n\n"
        "Paul"
    )


def request_value(data, *names):
    for name in names:
        value = clean_str(data.get(name))
        if value:
            return value
    return ""


def create_intake_from_website_payload(data, source="Website form", photo_filename=""):
    name = request_value(data, "name", "full_name", "customer_name", "fullname")
    phone = request_value(data, "phone", "phone_number", "telephone", "tel")
    email = request_value(data, "email", "email_address")
    address = request_value(data, "address", "full_address", "street_address")
    postcode = request_value(data, "postcode", "post_code", "zip")
    town = request_value(data, "town", "city")
    what_cleaned = request_value(data, "what_cleaned", "what_would_you_like_cleaned", "service", "service_required", "cleaning_required", "message")
    number_rooms = request_value(data, "number_rooms", "rooms", "number_of_rooms", "room_count")
    upholstery = request_value(data, "upholstery", "any_upholstery")
    rugs = request_value(data, "rugs", "any_rugs")
    stains = request_value(data, "stains", "problem_areas", "stains_problem_areas")
    pets = request_value(data, "pets", "pets_in_property")
    parking = request_value(data, "parking", "parking_information")
    preferred = request_value(data, "preferred_days_times", "preferred_days", "preferred_time", "preferred_times", "availability")
    additional_notes = request_value(data, "additional_notes", "notes", "message", "comments")
    preferred_date = request_value(data, "preferred_date", "date")
    preferred_time = request_value(data, "preferred_time", "time")
    marketing_consent = request_value(data, "marketing_consent", "marketing", "consent")
    if not name:
        name = "Website Customer"
    if not phone and not email:
        raise ValueError("Please enter at least a phone number or email address.")
    notes = "\n".join([part for part in [
        f"Town: {town}" if town else "",
        additional_notes,
    ] if part])
    lead_id = run("""INSERT INTO intake_submissions
           (name, phone, email, full_address, postcode, google_maps_link, what3words, job_notes, rooms_areas,
            what_cleaned, number_rooms, upholstery, rugs, stains, pets, parking, preferred_days_times, additional_notes,
            preferred_date, preferred_time, photo_filename, status, source, marketing_consent,
            xero_sync_status, customer_email_status, customer_sms_status, owner_email_status, owner_sms_status, follow_up_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        name, phone, email, address, postcode, "", "", notes, what_cleaned,
        what_cleaned, number_rooms, upholstery, rugs, stains, pets, parking, preferred, additional_notes,
        preferred_date, preferred_time, photo_filename, "Waiting for review", source, marketing_consent,
        "Pending", "Pending", "Pending", "Pending", "Pending", "Follow up required",
    ))
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    customer_id = create_customer_from_intake(lead)
    if town:
        run("UPDATE customers SET town=CASE WHEN IFNULL(town,'')='' THEN ? ELSE town END WHERE id=?", (town, customer_id))
    run("UPDATE intake_submissions SET customer_id=?, status='Waiting for review', updated_at=datetime('now') WHERE id=?", (customer_id, lead_id))
    run("UPDATE customers SET source=?, next_action='Review website form and approve customer for Xero' WHERE id=?", (source, customer_id))
    return lead_id, customer_id


def workflow_overdue_warnings(customer):
    status = clean_str(customer["workflow_status"]) or "new_enquiry"
    updated = parse_iso_date(clean_str(customer["last_updated"])[:10])
    if not updated:
        return []
    age = (date.today() - updated).days
    rules = {
        "customer_approved": (2, "Quote has not been created after approval."),
        "xero_synced": (2, "Quote has not been created after Xero sync."),
        "quote_created": (1, "Quote has not been sent."),
        "quote_sent": (7, "Quote is still waiting for acceptance."),
        "job_completed": (1, "Invoice has not been created after job completion."),
        "invoice_created": (1, "Invoice has not been sent."),
        "invoice_sent": (14, "Payment is overdue or still not marked received."),
        "payment_received": (1, "Review request has not been sent."),
    }
    limit, message = rules.get(status, (None, None))
    return [message] if limit is not None and age > limit else []


def workflow_context(customer):
    current = workflow_stage(customer["workflow_status"] if customer else "")
    idx = WORKFLOW_INDEX.get(current["key"], 0)
    completed = WORKFLOW_STAGES[:idx]
    remaining = WORKFLOW_STAGES[idx + 1:]
    next_stage = remaining[0] if remaining else current
    progress = round((idx / max(1, len(WORKFLOW_STAGES) - 1)) * 100)
    history = workflow_history_items(customer)
    action = clean_str(customer["next_action"] if customer else "") or current["next"]
    return {
        "current": current,
        "completed": completed,
        "remaining": remaining,
        "next_stage": next_stage,
        "next_action": action,
        "button_label": current["button"],
        "progress": progress,
        "history": list(reversed(history[-12:])),
        "warnings": workflow_overdue_warnings(customer) if customer else [],
        "last_updated": clean_str(customer["last_updated"] if customer else "") or clean_str(customer["created_at"] if customer else ""),
    }


def set_customer_workflow(customer_id, status, notes="", action_label=None):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        return
    status = status if status in WORKFLOW_BY_KEY else "new_enquiry"
    old_status = clean_str(customer["workflow_status"]) or "new_enquiry"
    stage = workflow_stage(status)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history = workflow_history_items(customer)
    history.append({
        "at": now,
        "from": workflow_stage(old_status)["label"],
        "to": stage["label"],
        "action": action_label or stage["button"],
        "notes": clean_str(notes),
    })
    updates = [
        "workflow_status=?",
        "next_action=?",
        "workflow_notes=?",
        "last_updated=?",
        "workflow_history=?",
    ]
    params = [status, stage["next"], clean_str(notes) or clean_str(customer["workflow_notes"]), now, json.dumps(history[-80:])]
    ts_field = WORKFLOW_TIMESTAMP_FIELDS.get(status)
    if ts_field:
        updates.append(f"{ts_field}=CASE WHEN IFNULL({ts_field},'')='' THEN ? ELSE {ts_field} END")
        params.append(now)
    params.append(customer_id)
    run(f"UPDATE customers SET {', '.join(updates)} WHERE id=?", tuple(params))


def workflow_dashboard_data():
    rows = q("SELECT * FROM customers WHERE archived_at IS NULL ORDER BY datetime(IFNULL(last_updated, created_at)) DESC, id DESC")
    cards = []
    for row in rows:
        item = dict(row)
        ctx = workflow_context(row)
        item["customer_name"] = customer_name(row)
        item["stage_label"] = ctx["current"]["label"]
        item["next_action_label"] = ctx["next_action"]
        item["priority_status"] = "Overdue" if ctx["warnings"] else ("Done" if ctx["current"]["key"] == "completed" else "Action")
        item["warnings"] = ctx["warnings"]
        cards.append(item)
    grouped = []
    for key, title, statuses in WORKFLOW_DASHBOARD_COLUMNS:
        grouped.append({
            "key": key,
            "title": title,
            "cards": [card for card in cards if (clean_str(card.get("workflow_status")) or "new_enquiry") in statuses],
        })
    return grouped


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in") and request.method == "GET":
        return redirect(url_for("dashboard"))
    s = settings()
    if request.method == "POST":
        submitted_username = (request.form.get("username") or "").strip()
        submitted_password = request.form.get("password") or ""
        username_ok = submitted_username == (s["username"] or "")
        password_ok = verify_password(s["password"], submitted_password)
        if username_ok and password_ok:
            if s["password"] and not is_password_hash(s["password"]):
                run("UPDATE settings SET password=? WHERE id=1", (normalize_password_for_storage(s["password"]),))
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Login details were incorrect.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))



def log_campaign_batch(channel, segment, mode, title, subject, body, recipient_count=0, sent_count=0, status='Logged', notes=''):
    return run(
        """INSERT INTO campaign_batches(channel, segment, mode, title, subject, body, status, recipient_count, sent_count, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (channel, segment, mode, title, subject, body, status, int(recipient_count or 0), int(sent_count or 0), notes)
    )


def log_campaign_item(campaign_batch_id, customer_id, recipient='', phone='', item_status='Logged', error_text=''):
    run(
        """INSERT INTO campaign_batch_items(campaign_batch_id, customer_id, recipient, phone, item_status, sent_at, error_text, created_at)
               VALUES (?,?,?,?,?,datetime('now'),?,datetime('now'))""",
        (campaign_batch_id, customer_id, recipient, phone, item_status, error_text)
    )


def latest_customer_activity_after(customer_id, sent_at):
    if not customer_id or not sent_at:
        return None
    events = []
    for sql in [
        "SELECT MAX(created_at) AS d FROM communications WHERE customer_id=? AND created_at > ?",
        "SELECT MAX(created_at) AS d FROM quotes WHERE customer_id=? AND created_at > ? AND IFNULL(status,'') <> 'Archived'",
        "SELECT MAX(created_at) AS d FROM jobs WHERE customer_id=? AND created_at > ? AND IFNULL(status,'') <> 'Archived'",
        "SELECT MAX(created_at) AS d FROM invoices WHERE customer_id=? AND created_at > ? AND IFNULL(status,'') <> 'Archived'",
    ]:
        row = q(sql, (customer_id, sent_at), one=True)
        if row and row['d']:
            events.append(row['d'])
    return max(events) if events else None


def recent_customer_contacts(customer_id, limit=8):
    if not customer_id:
        return []
    return q(
        """SELECT id, channel, subject, body, created_at
               FROM communications
               WHERE customer_id=?
               ORDER BY datetime(created_at) DESC, id DESC
               LIMIT ?""",
        (customer_id, int(limit or 8))
    )


def customer_contact_summary(customer_id, days=30):
    if not customer_id:
        return {
            'last_contacted_at': None,
            'count_recent': 0,
            'email_recent': 0,
            'sms_recent': 0,
            'other_recent': 0,
        }
    row = q(
        """SELECT MAX(created_at) AS last_contacted_at,
                      SUM(CASE WHEN datetime(created_at) >= datetime('now', ?) THEN 1 ELSE 0 END) AS count_recent,
                      SUM(CASE WHEN datetime(created_at) >= datetime('now', ?) AND lower(IFNULL(channel,''))='email' THEN 1 ELSE 0 END) AS email_recent,
                      SUM(CASE WHEN datetime(created_at) >= datetime('now', ?) AND lower(IFNULL(channel,''))='sms' THEN 1 ELSE 0 END) AS sms_recent,
                      SUM(CASE WHEN datetime(created_at) >= datetime('now', ?) AND lower(IFNULL(channel,'')) NOT IN ('email','sms') THEN 1 ELSE 0 END) AS other_recent
               FROM communications WHERE customer_id=?""",
        (f'-{int(days)} days', f'-{int(days)} days', f'-{int(days)} days', f'-{int(days)} days', customer_id),
        one=True,
    )
    if row is None:
        row = {}
    elif not isinstance(row, dict):
        row = dict(row)
    return {
        'last_contacted_at': row.get('last_contacted_at'),
        'count_recent': int(row.get('count_recent') or 0),
        'email_recent': int(row.get('email_recent') or 0),
        'sms_recent': int(row.get('sms_recent') or 0),
        'other_recent': int(row.get('other_recent') or 0),
    }


def build_follow_up_dashboard(days=90, limit=12):
    since_expr = f"datetime('now','-{int(days)} days')"
    rows = q(f"""
        SELECT i.*, b.channel, b.segment, b.mode, b.title, b.subject,
               c.first_name, c.last_name, c.email, c.phone AS customer_phone, c.town
        FROM campaign_batch_items i
        LEFT JOIN campaign_batches b ON b.id = i.campaign_batch_id
        LEFT JOIN customers c ON c.id = i.customer_id
        WHERE i.sent_at >= {since_expr}
        ORDER BY i.sent_at DESC, i.id DESC
    """)
    evaluated = []
    for row in rows:
        name = (f"{row['first_name'] or ''} {row['last_name'] or ''}").strip() or 'Unknown customer'
        activity_at = latest_customer_activity_after(row['customer_id'], row['sent_at'])
        state = 'No Activity Yet'
        if activity_at:
            state = 'Activity Since Sent'
        elif (row['item_status'] or '').lower() not in {'sent', 'logged', 'prepared'}:
            state = row['item_status'] or 'Unknown'
        enriched = dict(row)
        enriched['customer_name'] = name
        enriched['activity_at'] = activity_at
        enriched['reply_state'] = state
        evaluated.append(enriched)
    total = len(evaluated)
    email_sent = sum(1 for r in evaluated if (r.get('channel') or '').lower() == 'email')
    sms_logged = sum(1 for r in evaluated if (r.get('channel') or '').lower() == 'sms')
    activity_count = sum(1 for r in evaluated if r.get('activity_at'))
    waiting = [r for r in evaluated if not r.get('activity_at')]
    return {
        'days': days,
        'total': total,
        'email_sent': email_sent,
        'sms_logged': sms_logged,
        'activity_count': activity_count,
        'waiting_count': len(waiting),
        'recent_waiting': waiting[:limit],
        'recent_activity': [r for r in evaluated if r.get('activity_at')][:limit],
    }


@app.route("/")
@login_required
def dashboard():
    archive_counts = active_archived_counts()
    stats = {
        "customers": archive_counts["customers_active"],
        "quotes": archive_counts["quotes_active"],
        "jobs": archive_counts["jobs_active"],
        "invoices": archive_counts["invoices_active"],
    }
    quotes = q("""SELECT quotes.*, customers.first_name || ' ' || customers.last_name AS customer_name
                  FROM quotes LEFT JOIN customers ON customers.id = quotes.customer_id
                  ORDER BY quotes.id DESC LIMIT 6""")
    jobs = q("""SELECT jobs.*, customers.first_name || ' ' || customers.last_name AS customer_name
                FROM jobs LEFT JOIN customers ON customers.id = jobs.customer_id
                ORDER BY COALESCE(job_date,'9999-12-31') ASC LIMIT 6""")
    report_summary = build_reports_data(3)
    invoice_alerts = invoice_alert_rows(limit=5)
    follow_up_summary = build_follow_up_dashboard(90, 8)
    cashflow = cashflow_snapshot()
    reminders_due = q("""SELECT future_reminders.*, customers.first_name || ' ' || customers.last_name AS customer_name
                         FROM future_reminders
                         LEFT JOIN customers ON customers.id = future_reminders.customer_id
                         WHERE IFNULL(future_reminders.status,'Open')='Open'
                         ORDER BY COALESCE(reminder_date,'9999-12-31') ASC, future_reminders.id DESC
                         LIMIT 6""")
    feedback_recent = q("""SELECT customer_feedback.*, customers.first_name || ' ' || customers.last_name AS customer_name
                           FROM customer_feedback
                           LEFT JOIN customers ON customers.id = customer_feedback.customer_id
                           ORDER BY customer_feedback.id DESC LIMIT 5""")
    intake_new = q("SELECT COUNT(*) AS c FROM intake_submissions WHERE IFNULL(status,'New') IN ('New','Reviewed')", one=True)
    recent_enquiries = q("""SELECT * FROM intake_submissions
                            ORDER BY id DESC LIMIT 8""")
    return render_template("dashboard.html", stats=stats, recent_quotes=quotes, recent_jobs=jobs, archive_counts=archive_counts, report_summary=report_summary, invoice_alerts=invoice_alerts, app_settings=settings(), follow_up_summary=follow_up_summary, cashflow=cashflow, reminders_due=reminders_due, feedback_recent=feedback_recent, intake_new=intake_new["c"] if intake_new else 0, recent_enquiries=recent_enquiries)



@app.route("/help")
@login_required
def help_page():
    return render_template("help.html")


@app.route("/workflow")
@login_required
def workflow():
    data = {
        "new_intake": q("SELECT COUNT(*) AS c FROM intake_submissions WHERE IFNULL(status,'New') IN ('New','Reviewed')", one=True)["c"],
        "open_quotes": q("SELECT COUNT(*) AS c FROM quotes WHERE IFNULL(status,'Draft') IN ('Draft','Sent')", one=True)["c"],
        "booked_jobs": q("SELECT COUNT(*) AS c FROM jobs WHERE IFNULL(status,'Booked') IN ('Booked','In Progress')", one=True)["c"],
        "completed_jobs": q("SELECT COUNT(*) AS c FROM jobs WHERE IFNULL(status,'')='Completed'", one=True)["c"],
        "unpaid_invoices": q("SELECT COUNT(*) AS c FROM invoices WHERE IFNULL(status,'Draft') NOT IN ('Paid','Archived')", one=True)["c"],
        "open_reminders": q("SELECT COUNT(*) AS c FROM future_reminders WHERE IFNULL(status,'Open')='Open'", one=True)["c"],
        "feedback_count": q("SELECT COUNT(*) AS c FROM customer_feedback", one=True)["c"],
    }
    recent_reminders = q("""SELECT future_reminders.*, customers.first_name || ' ' || customers.last_name AS customer_name
                            FROM future_reminders LEFT JOIN customers ON customers.id = future_reminders.customer_id
                            WHERE IFNULL(future_reminders.status,'Open')='Open'
                            ORDER BY COALESCE(reminder_date,'9999-12-31') ASC, future_reminders.id DESC LIMIT 12""")
    return render_template("workflow.html", data=data, reminders=recent_reminders, workflow_columns=workflow_dashboard_data())


@app.route("/today-run")
@login_required
def today_run():
    selected_date = clean_str(request.args.get("date")) or uk_today().isoformat()
    jobs_today = q("""SELECT jobs.*, customers.first_name, customers.last_name, customers.phone, customers.email,
                             customers.address, customers.town, customers.postcode, customers.sms_opt_out,
                             invoices.id AS invoice_id, invoices.status AS invoice_status, invoices.total AS invoice_total
                      FROM jobs
                      LEFT JOIN customers ON customers.id = jobs.customer_id
                      LEFT JOIN invoices ON invoices.job_id = jobs.id AND IFNULL(invoices.status,'') <> 'Archived'
                      WHERE IFNULL(jobs.status,'') <> 'Archived'
                        AND COALESCE(jobs.job_date,'') = ?
                      ORDER BY jobs.id ASC""", (selected_date,))
    cards = []
    for row in jobs_today:
        item = dict(row)
        item["customer_name"] = customer_full_name(row)
        item["address_text"] = customer_address_text(row)
        item["directions_url"] = directions_url_for_customer(row)
        item["coming_message"] = day_run_message("coming", row)
        item["reminder_message"] = day_run_message("reminder", row)
        item["finished_message"] = day_run_message("finished", row)
        item["review_message"] = day_run_message("review", row)
        item["is_done"] = clean_str(row["status"]).lower() in {"completed", "invoiced", "paid"}
        cards.append(item)
    stats = {
        "total": len(cards),
        "done": len([c for c in cards if c["is_done"]]),
        "remaining": len([c for c in cards if not c["is_done"]]),
        "paid": len([c for c in cards if clean_str(c.get("status")).lower() == "paid" or clean_str(c.get("invoice_status")).lower() == "paid"]),
    }
    return render_template("today_run.html", jobs=cards, selected_date=selected_date, stats=stats)


@app.route("/today-run/job/<int:job_id>/action", methods=["POST"])
@login_required
def today_run_job_action(job_id):
    job = q("""SELECT jobs.*, customers.first_name, customers.last_name, customers.phone, customers.email,
                      customers.address, customers.town, customers.postcode, customers.sms_opt_out
               FROM jobs LEFT JOIN customers ON customers.id = jobs.customer_id
               WHERE jobs.id=?""", (job_id,), one=True)
    if not job:
        flash("Job not found.")
        return redirect(url_for("today_run"))
    action = clean_str(request.form.get("action"))
    channel = clean_str(request.form.get("channel")).lower()
    next_url = request.form.get("next_url") or url_for("today_run", date=job["job_date"] or uk_today().isoformat())
    customer_id = job["customer_id"]

    if action in {"coming", "reminder", "finished", "review"}:
        body = day_run_message(action, job)
        subject_map = {
            "coming": "We are on our way",
            "reminder": "Appointment reminder",
            "finished": "Job completed",
            "review": "Review request",
        }
        subject = subject_map[action]
        if channel == "email":
            ok, msg = send_email_smtp(job["email"] or "", subject, body, customer=job)
            if ok:
                log_customer_message(customer_id, "Email", subject, body)
        elif channel == "sms":
            ok, msg = send_sms_gateway(job["phone"] or "", body, customer=job, message_category="review" if action == "review" else "reminder")
            if ok:
                log_customer_message(customer_id, "SMS", subject, body)
        else:
            ok, msg = True, "Message copied/logged."
            log_customer_message(customer_id, "Note", subject, body)
        if action == "reminder" and customer_id:
            set_customer_workflow(customer_id, "reminder_sent", "Reminder sent from Today Run.", "Reminder sent")
            run("UPDATE jobs SET status='Reminder Sent' WHERE id=? AND IFNULL(status,'') IN ('Booked','Lead','Quoted')", (job_id,))
        if action == "review" and customer_id:
            set_customer_workflow(customer_id, "completed", "Review request sent from Today Run.", "Review request sent")
            run("UPDATE customers SET review_request_sent_at=datetime('now') WHERE id=?", (customer_id,))
        flash(msg)
        return redirect(next_url)

    if action == "start":
        run("UPDATE jobs SET status='In Progress' WHERE id=?", (job_id,))
        if customer_id:
            run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
                (customer_id, "Today Run: job started.", ""))
        flash("Job marked as in progress.")
        return redirect(next_url)

    if action == "complete":
        run("UPDATE jobs SET status='Completed' WHERE id=?", (job_id,))
        if customer_id:
            set_customer_workflow(customer_id, "job_completed", "Job completed from Today Run.", "Job completed")
        flash("Job marked complete.")
        return redirect(next_url)

    if action == "cash_paid":
        notes = append_note(job["notes"] or "", f"Cash paid on {datetime.now().strftime('%Y-%m-%d %H:%M')}. No invoice created.")
        run("UPDATE jobs SET status='Paid', notes=? WHERE id=?", (notes, job_id))
        if customer_id:
            set_customer_workflow(customer_id, "payment_received", "Cash payment recorded from Today Run. No invoice created.", "Payment received")
            run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
                (customer_id, "Cash payment received. No invoice created.", ""))
        flash("Cash payment recorded. No invoice created.")
        return redirect(next_url)

    if action in {"create_invoice", "card_paid"}:
        status = "Paid" if action == "card_paid" else "Draft"
        invoice_id, created = create_invoice_for_job(job, status=status, note_extra="Created from Today Run.")
        run("UPDATE jobs SET status=? WHERE id=?", ("Paid" if action == "card_paid" else "Invoiced", job_id))
        if customer_id:
            set_customer_workflow(customer_id, "payment_received" if action == "card_paid" else "invoice_created",
                                  "Payment recorded from Today Run." if action == "card_paid" else "Invoice created from Today Run.",
                                  "Payment received" if action == "card_paid" else "Invoice created")
        flash("Invoice created and marked paid." if action == "card_paid" else ("Invoice created." if created else "Existing invoice opened."))
        return redirect(url_for("invoice_view", invoice_id=invoice_id))

    flash("Unknown Today Run action.")
    return redirect(next_url)


@app.route("/customers")
@login_required
def customers():
    search = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "active").strip().lower()
    starts = (request.args.get("starts") or "").strip().upper()
    if scope not in {"active", "archived", "all"}:
        scope = "active"
    if starts and (len(starts) != 1 or not starts.isalpha()):
        starts = ""
    where_parts = [list_scope_clause("customers", scope, archived_column="archived_at")]
    params = []
    if search:
        like = f"%{search}%"
        where_parts.append("(first_name LIKE ? OR last_name LIKE ? OR phone LIKE ? OR email LIKE ? OR town LIKE ? OR tags LIKE ?)")
        params += [like, like, like, like, like, like]
    if starts:
        where_parts.append("(UPPER(SUBSTR(first_name,1,1))=? OR UPPER(SUBSTR(last_name,1,1))=?)")
        params += [starts, starts]
    sql = "SELECT * FROM customers WHERE " + " AND ".join(where_parts) + " ORDER BY lower(last_name), lower(first_name), id DESC"
    rows = q(sql, tuple(params))
    annotate_rows_with_last_contact(rows, key="id")
    letters = [chr(c) for c in range(ord('A'), ord('Z')+1)]
    return render_template("customers.html", customers=rows, search=search, scope=scope, starts=starts, letters=letters)


@app.route("/customers/import-library", methods=["GET", "POST"])
@login_required
def customers_import_library():
    if request.method == "GET":
        flash("Customer sync now pulls from Xero. Use Pull All Xero Customers on this page.")
        return redirect(url_for("xero_dashboard"))
    if request.method == "POST":
        upload = request.files.get("customer_library")
        if not upload or not upload.filename:
            flash("Choose a customer library file first.")
            return redirect(url_for("customers_import_library"))
        filename = secure_filename(upload.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in {".db", ".sqlite", ".sqlite3", ".csv"}:
            flash("Upload a CRM database file (.db) or a customer CSV file.")
            return redirect(url_for("customers_import_library"))
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        temp_name = f"customer_library_{uuid.uuid4().hex}{ext}"
        temp_path = os.path.join(app.config["UPLOAD_FOLDER"], temp_name)
        upload.save(temp_path)
        try:
            if ext == ".csv":
                result = import_customer_library_from_csv(temp_path)
            else:
                result = import_customer_library_from_db(temp_path)
            flash(
                "Customer library sync complete. "
                f"Created {result['created']}; updated {result['updated']}; "
                f"skipped {result['skipped']}; failed {result['failed']}."
            )
            return redirect(url_for("customers", scope="all"))
        except Exception as exc:
            logger.exception("Customer library import failed")
            flash(f"Customer library sync failed: {exc}")
            return redirect(url_for("customers_import_library"))
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return render_template("customer_library_import.html")


@app.route("/customers/new", methods=["POST"])
@login_required
def customers_new():
    first_name = clean_str(request.form.get("first_name"))
    last_name = clean_str(request.form.get("last_name"))
    phone = clean_str(request.form.get("phone"))
    email = clean_str(request.form.get("email"))
    if not first_name or not last_name:
        flash("First name and last name are required.")
        return redirect(url_for("customers"))
    if email and not is_valid_email(email):
        flash("Please enter a valid email address.")
        return redirect(url_for("customers"))
    existing_customer_id = find_existing_customer_id(first_name=first_name, last_name=last_name, email=email, phone=phone, postcode=request.form.get("postcode"))
    if existing_customer_id:
        flash("That customer already seems to exist, so no duplicate was created.")
        return redirect(url_for("customer_view", customer_id=existing_customer_id))
    customer_id = run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,tags,notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", (
        first_name, last_name, phone,
        email, clean_str(request.form.get("address")), clean_str(request.form.get("town")),
        clean_str(request.form.get("postcode")), clean_str(request.form.get("source")), clean_str(request.form.get("tags")),
        clean_str(request.form.get("notes"))
    ))
    flash("Customer added.")
    return redirect(url_for("customer_view", customer_id=customer_id))


@app.route("/customers/<int:customer_id>")
@login_required
def customer_view(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash("Customer not found. It may have been deleted or the link is out of date.")
        return redirect(url_for("customers"))
    timeline = q("SELECT * FROM customer_timeline WHERE customer_id=? ORDER BY id DESC", (customer_id,))
    quotes = q("SELECT * FROM quotes WHERE customer_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC", (customer_id,))
    jobs = q("SELECT * FROM jobs WHERE customer_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC", (customer_id,))
    invoices = q("SELECT * FROM invoices WHERE customer_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC", (customer_id,))
    feedback = q("SELECT * FROM customer_feedback WHERE customer_id=? ORDER BY id DESC", (customer_id,))
    reminders = q("SELECT * FROM future_reminders WHERE customer_id=? ORDER BY COALESCE(reminder_date,'9999-12-31') ASC, id DESC", (customer_id,))
    subscription_summary = customer_subscription_summary(customer_id)
    last_contacted_at = customer_last_contact_map([customer_id]).get(customer_id)
    recent_contacts = recent_customer_contacts(customer_id, 8)
    contact_summary = customer_contact_summary(customer_id, 30)
    recent_sms = q("""SELECT * FROM sms_events WHERE customer_id=? ORDER BY id DESC LIMIT 8""", (customer_id,))
    sms_thread = sms_thread_rows(customer_id=customer_id, limit=24)
    sms_summary = q("""SELECT
        COUNT(*) AS total_count,
        SUM(CASE WHEN lower(IFNULL(direction,''))='outbound' THEN 1 ELSE 0 END) AS outbound_count,
        SUM(CASE WHEN lower(IFNULL(direction,''))='inbound' THEN 1 ELSE 0 END) AS inbound_count,
        SUM(CASE WHEN lower(IFNULL(status,'')) IN ('delivered','sent','queued','accepted') THEN 1 ELSE 0 END) AS ok_count,
        SUM(CASE WHEN lower(IFNULL(status,'')) IN ('failed','undelivered') OR IFNULL(error_text,'')<>'' THEN 1 ELSE 0 END) AS failed_count
        FROM sms_events WHERE customer_id=?""", (customer_id,), one=True)
    workflow = workflow_context(customer)
    workflow_messages = {
        "booking_form": booking_form_message(customer),
        "booking_form_url": booking_form_url(customer),
        "reminder": reminder_message(customer),
        "review": review_request_message(customer),
    }
    return render_template("customer_view.html", customer=customer, timeline=timeline, quotes=quotes, jobs=jobs, invoices=invoices, feedback=feedback, reminders=reminders, subscription_summary=subscription_summary, is_archived=bool(customer and customer["archived_at"]), last_contacted_at=last_contacted_at, last_contacted_label=contact_badge_text(last_contacted_at), recent_contacts=recent_contacts, contact_summary=contact_summary, recent_sms=recent_sms, sms_summary=sms_summary, sms_thread=sms_thread, workflow=workflow, workflow_stages=WORKFLOW_STAGES, workflow_messages=workflow_messages)


@app.route("/customers/<int:customer_id>/workflow-action", methods=["POST"])
@login_required
def customer_workflow_action(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash("Customer not found.")
        return redirect(url_for("customers"))
    status = clean_str(customer["workflow_status"]) or "new_enquiry"
    notes = clean_str(request.form.get("workflow_notes"))
    redirect_to = url_for("customer_view", customer_id=customer_id) + "#workflow-panel"
    if status == "new_enquiry":
        set_customer_workflow(customer_id, "booking_form_sent", notes, "Booking form link prepared")
        db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, "Copy Link", "Booking form", booking_form_message(customer)))
        db().commit()
        flash("Booking form message prepared and logged. Use SMS, Email, WhatsApp, or Copy Link on the customer panel.")
    elif status in {"booking_form_sent"}:
        set_customer_workflow(customer_id, "waiting_for_review", notes, "Marked form ready for review")
        flash("Customer moved to form review.")
    elif status in {"form_completed", "waiting_for_review"}:
        set_customer_workflow(customer_id, "customer_approved", notes, "Customer approved")
        flash("Customer approved. Next step is Xero sync.")
    elif status == "customer_approved":
        try:
            contact_id = ensure_xero_contact_for_customer(customer_id)
            set_customer_workflow(customer_id, "xero_synced", notes or f"Xero contact ready: {contact_id}", "Xero contact synced")
            flash("Xero contact created or already exists.")
        except Exception as exc:
            logger.exception("Workflow Xero sync failed for customer %s", customer_id)
            run("UPDATE customers SET xero_contact_error=? WHERE id=?", (str(exc), customer_id))
            flash(f"Xero sync failed: {exc}")
    elif status == "xero_synced":
        set_customer_workflow(customer_id, "quote_created", notes, "Quote creation started")
        flash("Customer moved to Quote Created. Open the calculator to build the quote.")
        return redirect(url_for("calculator"))
    elif status == "quote_created":
        set_customer_workflow(customer_id, "quote_sent", notes, "Quote sent")
        flash("Quote marked as sent.")
    elif status == "quote_sent":
        set_customer_workflow(customer_id, "quote_accepted", notes, "Quote accepted")
        flash("Quote marked as accepted. Next step is booking the job.")
    elif status == "quote_accepted":
        set_customer_workflow(customer_id, "job_booked", notes, "Job booked")
        flash("Job marked as booked. Use the Jobs page or calendar to set the exact date and time.")
        return redirect(url_for("jobs"))
    elif status == "job_booked":
        set_customer_workflow(customer_id, "reminder_sent", notes, "Reminder message prepared")
        db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, "Copy Message", "Appointment reminder", reminder_message(customer)))
        db().commit()
        flash("Reminder message prepared and logged.")
    elif status == "reminder_sent":
        set_customer_workflow(customer_id, "job_completed", notes, "Job completed")
        flash("Job marked as completed. Next step is invoice.")
    elif status == "job_completed":
        set_customer_workflow(customer_id, "invoice_created", notes, "Invoice creation started")
        flash("Customer moved to Invoice Created. Use the invoice tools to create the invoice.")
        return redirect(url_for("invoices"))
    elif status == "invoice_created":
        set_customer_workflow(customer_id, "invoice_sent", notes, "Invoice sent")
        flash("Invoice marked as sent.")
    elif status == "invoice_sent":
        set_customer_workflow(customer_id, "payment_received", notes, "Payment received")
        flash("Payment marked as received.")
    elif status == "payment_received":
        set_customer_workflow(customer_id, "review_request_sent", notes, "Review request prepared")
        db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, "Copy Message", "Review request", review_request_message(customer)))
        db().commit()
        flash("Review request message prepared and logged.")
    elif status == "review_request_sent":
        set_customer_workflow(customer_id, "completed", notes, "Workflow completed")
        flash("Customer workflow completed.")
    else:
        flash("This customer is already completed.")
    return redirect(redirect_to)


@app.route("/customers/<int:customer_id>/workflow-set/<status>", methods=["POST"])
@login_required
def customer_workflow_set(customer_id, status):
    if status not in WORKFLOW_BY_KEY:
        flash("Unknown workflow status.")
        return redirect(url_for("customer_view", customer_id=customer_id))
    set_customer_workflow(customer_id, status, clean_str(request.form.get("workflow_notes")), "Manual workflow update")
    flash("Workflow status updated.")
    return redirect(url_for("customer_view", customer_id=customer_id) + "#workflow-panel")


@app.route("/customers/<int:customer_id>/feedback/new", methods=["POST"])
@login_required
def customer_feedback_new(customer_id):
    rating_raw = clean_str(request.form.get("rating"))
    try:
        rating = max(0, min(5, int(rating_raw or 0)))
    except ValueError:
        rating = 0
    run("""INSERT INTO customer_feedback(customer_id, job_id, rating, feedback_text, source, review_requested, review_link_sent_at)
           VALUES (?,?,?,?,?,?,?)""", (
        customer_id,
        request.form.get("job_id") or None,
        rating,
        clean_str(request.form.get("feedback_text")),
        clean_str(request.form.get("source")) or "Manual",
        1 if request.form.get("review_requested") else 0,
        datetime.now().strftime("%Y-%m-%d %H:%M") if request.form.get("review_requested") else "",
    ))
    if request.form.get("review_requested"):
        set_customer_workflow(customer_id, "review_request_sent", "Google review request recorded.", "Review request sent")
    flash("Customer feedback recorded.")
    return redirect(url_for("customer_view", customer_id=customer_id) + "#customer-feedback")


@app.route("/customers/<int:customer_id>/reminders/new", methods=["POST"])
@login_required
def customer_reminder_new(customer_id):
    title = clean_str(request.form.get("title"))
    reminder_date = clean_str(request.form.get("reminder_date"))
    if not title:
        flash("Reminder title is required.")
        return redirect(url_for("customer_view", customer_id=customer_id) + "#customer-reminders")
    run("""INSERT INTO future_reminders(customer_id, job_id, reminder_date, title, notes, reminder_type, status)
           VALUES (?,?,?,?,?,?,?)""", (
        customer_id,
        request.form.get("job_id") or None,
        reminder_date,
        title,
        clean_str(request.form.get("notes")),
        clean_str(request.form.get("reminder_type")) or "Follow up",
        "Open",
    ))
    flash("Future reminder created.")
    return redirect(url_for("customer_view", customer_id=customer_id) + "#customer-reminders")

@app.route("/customers/<int:customer_id>/edit", methods=["POST"])
@login_required
def customer_edit(customer_id):
    first_name = clean_str(request.form.get("first_name"))
    last_name = clean_str(request.form.get("last_name"))
    email = clean_str(request.form.get("email"))
    if not first_name or not last_name:
        flash("First name and last name are required.")
        return redirect(url_for("customer_view", customer_id=customer_id))
    if email and not is_valid_email(email):
        flash("Please enter a valid email address.")
        return redirect(url_for("customer_view", customer_id=customer_id))
    existing_customer_id = find_existing_customer_id(first_name=first_name, last_name=last_name, email=email, phone=request.form.get("phone"), postcode=request.form.get("postcode"))
    if existing_customer_id and existing_customer_id != customer_id:
        flash("Another customer already matches those details, so the update was stopped to avoid duplicates.")
        return redirect(url_for("customer_view", customer_id=customer_id))
    run("""UPDATE customers SET first_name=?, last_name=?, phone=?, email=?, address=?, town=?, postcode=?, source=?, tags=?, notes=? WHERE id=?""", (
        first_name, last_name, clean_str(request.form.get("phone")),
        email, clean_str(request.form.get("address")), clean_str(request.form.get("town")),
        clean_str(request.form.get("postcode")), clean_str(request.form.get("source")), clean_str(request.form.get("tags")),
        clean_str(request.form.get("notes")), customer_id
    ))
    flash("Customer updated.")
    return redirect(url_for("customer_view", customer_id=customer_id))


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
@login_required
def customer_delete(customer_id):
    archive_customer_record(customer_id)
    flash("Customer archived instead of being permanently deleted.")
    return redirect(url_for("customers"))

@app.route("/customers/<int:customer_id>/restore", methods=["POST"])
@login_required
def customer_restore(customer_id):
    restore_customer_record(customer_id)
    flash("Customer restored.")
    return redirect(url_for("customer_view", customer_id=customer_id))

@app.route("/customers/<int:customer_id>/timeline/add", methods=["POST"])
@login_required
def customer_timeline_add(customer_id):
    photo = save_upload("photo")
    run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
        (customer_id, request.form.get("note_text"), photo))
    flash("Timeline entry added.")
    return redirect(url_for("customer_view", customer_id=customer_id))

@app.route("/customers/<int:customer_id>/email_link")
@login_required
def customer_email_link(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer or not customer["email"]:
        flash("No email on that customer.")
        return redirect(url_for("customer_view", customer_id=customer_id))
    subject = quote("Thanks for choosing " + (settings()["business_name"] or "our business"))
    body = quote(f"Hi {customer['first_name']},\n\nThanks again for choosing {settings()['business_name']}.\n\n")
    return redirect(f"mailto:{customer['email']}?subject={subject}&body={body}")

@app.route("/customers/<int:customer_id>/sms_link")
@login_required
def customer_sms_link(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer or not customer["phone"]:
        flash("No phone on that customer.")
        return redirect(url_for("customer_view", customer_id=customer_id))
    review_link = settings()["review_link"] or ""
    msg = quote(f"Hi {customer['first_name']}, thanks again for choosing {settings()['business_name']}. We would appreciate a review: {review_link}")
    return redirect(f"sms:{customer['phone']}?body={msg}")

@app.route("/customers/<int:customer_id>/send-review-sms", methods=["POST"])
@login_required
def customer_send_review_sms(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash("Customer not found.")
        return redirect(url_for("customers"))
    body = request.form.get("body") or "Hi {{first_name}}, thanks again for choosing {{business_name}}. We would really appreciate a review: {{review_link}}"
    rendered_body = safe_replace(body, comms_replacements(customer))
    ok, msg = send_sms_gateway(customer["phone"] or "", rendered_body, customer=customer, message_category=request.form.get('message_category') or 'Review')
    flash(msg)
    if ok:
        db().execute(
            "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
            (customer_id, 'SMS', 'Review request', rendered_body)
        )
        db().commit()
        set_customer_workflow(customer_id, "review_request_sent", "Review request sent by SMS.", "Review request sent")
    return redirect(url_for("customer_view", customer_id=customer_id))


@app.route("/customers/<int:customer_id>/sms-opt-out", methods=["POST"])
@login_required
def customer_sms_opt_out(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash("Customer not found.")
        return redirect(url_for("customers"))
    set_customer_sms_opt_out(customer_id, True, source='Manual CRM')
    flash('Customer has been opted out of SMS.')
    return redirect(url_for("customer_view", customer_id=customer_id))


@app.route("/customers/<int:customer_id>/sms-opt-in", methods=["POST"])
@login_required
def customer_sms_opt_in(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash("Customer not found.")
        return redirect(url_for("customers"))
    set_customer_sms_opt_out(customer_id, False, source='Manual CRM')
    flash('Customer has been opted back into SMS.')
    return redirect(url_for("customer_view", customer_id=customer_id))


@app.route('/sms-templates', methods=['GET', 'POST'])
@login_required
def sms_templates_page():
    if request.method == 'POST':
        template_id = int(request.form.get('template_id') or 0)
        name = (request.form.get('name') or '').strip()
        body = (request.form.get('body') or '').strip()
        category = (request.form.get('category') or '').strip()
        usage_type = (request.form.get('usage_type') or 'General').strip() or 'General'
        auto_append_opt_out = 1 if request.form.get('auto_append_opt_out') else 0
        if not name or not body:
            flash('Template name and body are required.')
            return redirect(url_for('sms_templates_page'))
        if template_id:
            run("UPDATE sms_templates SET name=?, body=?, category=?, usage_type=?, auto_append_opt_out=?, updated_at=datetime('now') WHERE id=?", (name, body, category, usage_type, auto_append_opt_out, template_id))
            flash('SMS template updated.')
        else:
            run("INSERT INTO sms_templates(name, body, category, usage_type, auto_append_opt_out, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))", (name, body, category, usage_type, auto_append_opt_out, 1))
            flash('SMS template saved.')
        return redirect(url_for('sms_templates_page', category=(category or request.args.get('category') or '')))
    category_filter = clean_str(request.args.get('category'))
    edit_id = int(request.args.get('edit') or 0)
    rows = q("SELECT * FROM sms_templates WHERE (?='' OR IFNULL(category,'')=?) ORDER BY IFNULL(is_active,1) DESC, IFNULL(category,'') COLLATE NOCASE, name COLLATE NOCASE", (category_filter, category_filter))
    categories = q("SELECT DISTINCT IFNULL(category,'') AS category FROM sms_templates ORDER BY IFNULL(category,'') COLLATE NOCASE")
    edit_row = q("SELECT * FROM sms_templates WHERE id=?", (edit_id,), one=True) if edit_id else None
    return render_template('sms_templates.html', rows=rows, categories=categories, category_filter=category_filter, edit_row=edit_row)


@app.route('/sms-templates/<int:template_id>/edit')
@login_required
def sms_template_edit(template_id):
    return redirect(url_for('sms_templates_page', edit=template_id))



@app.route('/sms-templates/<int:template_id>/toggle', methods=['POST'])
@login_required
def sms_template_toggle(template_id):
    row = q("SELECT * FROM sms_templates WHERE id=?", (template_id,), one=True)
    if row:
        run("UPDATE sms_templates SET is_active=?, updated_at=datetime('now') WHERE id=?", (0 if int(row['is_active'] or 0) == 1 else 1, template_id))
        flash('SMS template updated.')
    return redirect(url_for('sms_templates_page'))


@app.route('/sms-templates/<int:template_id>/delete', methods=['POST'])
@login_required
def sms_template_delete(template_id):
    run("DELETE FROM sms_templates WHERE id=?", (template_id,))
    flash('SMS template deleted.')
    return redirect(url_for('sms_templates_page'))


@app.route("/message-settings", methods=["GET", "POST"])
@login_required
def message_settings():
    init_db()
    if request.method == "POST":
        for key in DEFAULT_MESSAGE_TEMPLATES:
            name = clean_str(request.form.get(f"{key}_name")) or DEFAULT_MESSAGE_TEMPLATES[key]["name"]
            subject = clean_str(request.form.get(f"{key}_subject"))
            body = request.form.get(f"{key}_body") or ""
            run("""INSERT INTO message_templates(template_key, name, subject, body, updated_at)
                   VALUES (?,?,?,?,datetime('now'))
                   ON CONFLICT(template_key) DO UPDATE SET name=excluded.name, subject=excluded.subject, body=excluded.body, updated_at=datetime('now')""",
                (key, name, subject, body))
        flash("Message templates saved.")
        return redirect(url_for("message_settings"))
    rows = {row["template_key"]: row for row in q("SELECT * FROM message_templates ORDER BY name")}
    templates = []
    for key, default in DEFAULT_MESSAGE_TEMPLATES.items():
        row = rows.get(key)
        templates.append({
            "key": key,
            "name": row["name"] if row else default["name"],
            "subject": row["subject"] if row else default["subject"],
            "body": row["body"] if row else default["body"],
        })
    return render_template("message_settings.html", templates=templates)


@app.route('/sms-inbox')
@login_required
def sms_inbox():
    search_text = clean_str(request.args.get('q'))
    unread_only = (request.args.get('view') or '') == 'unread'
    rows = sms_thread_summaries(search_text=search_text, unread_only=unread_only)
    return render_template('sms_threads.html', rows=rows, search_text=search_text, unread_only=unread_only)


@app.route('/sms-threads')
@login_required
def sms_threads():
    return redirect(url_for('sms_inbox'))


@app.route('/sms-threads/<int:customer_id>')
@login_required
def sms_thread_view(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash('Customer not found.')
        return redirect(url_for('sms_inbox'))
    run("INSERT INTO sms_thread_state(customer_id, last_viewed_at) VALUES (?, datetime('now')) ON CONFLICT(customer_id) DO UPDATE SET last_viewed_at=datetime('now')", (customer_id,))
    thread = sms_thread_rows(customer_id=customer_id, limit=250)
    notes = q("SELECT * FROM sms_thread_notes WHERE customer_id=? ORDER BY id DESC", (customer_id,))
    templates = active_sms_templates()
    return render_template('sms_thread.html', customer=customer, thread=thread, notes=notes, templates=templates)


@app.route('/sms-threads/<int:customer_id>/note', methods=['POST'])
@login_required
def sms_thread_note(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash('Customer not found.')
        return redirect(url_for('sms_inbox'))
    note_text = (request.form.get('note_text') or '').strip()
    if note_text:
        run("INSERT INTO sms_thread_notes(customer_id, note_text, created_at) VALUES (?,?,datetime('now'))", (customer_id, note_text))
        flash('Thread note saved.')
    else:
        flash('Note was blank.')
    return redirect(url_for('sms_thread_view', customer_id=customer_id))


@app.route('/sms-threads/<int:customer_id>/reply', methods=['POST'])
@login_required
def sms_thread_reply(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash('Customer not found.')
        return redirect(url_for('sms_inbox'))
    body = clean_str(request.form.get('body'))
    template_id = int(request.form.get('template_id') or 0)
    template_row = q("SELECT * FROM sms_templates WHERE id=?", (template_id,), one=True) if template_id else None
    if template_row and not body:
        body = template_row['body'] or ''
    if not body:
        flash('Reply message is blank.')
        return redirect(url_for('sms_thread_view', customer_id=customer_id))
    rendered_body = safe_replace(body, comms_replacements(customer))
    message_category = sms_message_category(request.form.get('message_category') or '', template_row=template_row)
    if template_row and int(template_row['auto_append_opt_out'] or 0) == 1 and not message_category:
        message_category = 'Marketing'
    ok, msg = send_sms_gateway(customer['phone'] or '', rendered_body, customer=customer, message_category=message_category)
    flash(msg)
    if ok:
        db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, 'SMS', 'Thread reply', rendered_body))
        db().commit()
        run("INSERT INTO sms_thread_state(customer_id, last_viewed_at) VALUES (?, datetime('now')) ON CONFLICT(customer_id) DO UPDATE SET last_viewed_at=datetime('now')", (customer_id,))
    return redirect(url_for('sms_thread_view', customer_id=customer_id))


@app.route("/calculator")
@login_required
def calculator():
    customers = q("SELECT id, first_name || ' ' || last_name AS name FROM customers WHERE archived_at IS NULL ORDER BY first_name, last_name")
    return render_template("calculator.html", pricing=pricing(), customers=customers, area_options=AREA_OPTIONS)

@app.route("/quotes/create_from_calculator", methods=["POST"])
@login_required
def quotes_create_from_calculator():
    payload = json.loads(request.form.get("payload_json") or "{}")
    calc = calc_from_payload(payload)
    customer_id = request.form.get("customer_id") or None
    quote_id = run("""INSERT INTO quotes(customer_id, quote_number, title, quote_date, valid_until, status, subtotal, vat, total, payload_json, notes)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
        customer_id, next_quote_number(), request.form.get("title") or "Calculator Quote",
        request.form.get("quote_date") or date.today().isoformat(), request.form.get("valid_until") or "",
        "Draft", calc["subtotal"], calc["vat"], calc["total"], json.dumps(payload), request.form.get("notes") or ""
    ))
    for line in calc["lines"]:
        run("""INSERT INTO quote_lines(quote_id, item_name, method, quantity, unit_price, line_total, group_name)
               VALUES (?,?,?,?,?,?,?)""", (
            quote_id, line["item_name"], line["method"], line["quantity"], line["unit_price"], line["line_total"], line["group_name"]
        ))
    if customer_id:
        set_customer_workflow(int(customer_id), "quote_created", "Quote created from calculator.", "Quote created")
    flash("Quote created from calculator.")
    return redirect(url_for("quote_view", quote_id=quote_id))

@app.route("/quotes")
@login_required
def quotes():
    search = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "active").strip().lower()
    if scope not in {"active", "archived", "all"}:
        scope = "active"
    sql = f"""SELECT quotes.*, customers.first_name || ' ' || customers.last_name AS customer_name
             FROM quotes LEFT JOIN customers ON customers.id = quotes.customer_id
             WHERE {list_scope_clause('quotes', scope)}"""
    params = []
    if search:
        like = f"%{search}%"
        sql += " AND (quote_number LIKE ? OR title LIKE ? OR customer_name LIKE ?)"
        params += [like, like, like]
    sql += " ORDER BY quotes.id DESC"
    rows = q(sql, tuple(params))
    return render_template("quotes.html", quotes=rows, search=search, scope=scope)

@app.route("/quotes/<int:quote_id>")
@login_required
def quote_view(quote_id):
    quote = q("""SELECT quotes.*, customers.* FROM quotes
                 LEFT JOIN customers ON customers.id = quotes.customer_id
                 WHERE quotes.id=?""", (quote_id,), one=True)
    lines = q("SELECT * FROM quote_lines WHERE quote_id=? ORDER BY id", (quote_id,))
    existing_job = q("SELECT id FROM jobs WHERE quote_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC LIMIT 1", (quote_id,), one=True)
    recent_contacts = recent_customer_contacts(quote["customer_id"] if quote else None, 6)
    contact_summary = customer_contact_summary(quote["customer_id"] if quote else None, 30)
    return render_template("quote_view.html", quote=quote, lines=lines, is_archived=((quote["status"] or "") == "Archived"), existing_job_id=(existing_job["id"] if existing_job else None), recent_contacts=recent_contacts, contact_summary=contact_summary)

@app.route("/quotes/<int:quote_id>/edit", methods=["POST"])
@login_required
def quote_edit(quote_id):
    status = clean_str(request.form.get("status")) or "Draft"
    if status == "Archived":
        flash("Use the Archive button to archive a quote.")
        return redirect(url_for("quote_view", quote_id=quote_id))
    run("""UPDATE quotes SET title=?, quote_date=?, valid_until=?, status=?, notes=? WHERE id=?""", (
        clean_str(request.form.get("title")), clean_str(request.form.get("quote_date")), clean_str(request.form.get("valid_until")),
        status, clean_str(request.form.get("notes")), quote_id
    ))
    quote = q("SELECT customer_id FROM quotes WHERE id=?", (quote_id,), one=True)
    if quote and quote["customer_id"]:
        status_key = status.lower()
        if status_key in {"sent", "emailed"}:
            set_customer_workflow(quote["customer_id"], "quote_sent", "Quote marked as sent.", "Quote sent")
        elif status_key in {"accepted", "converted"}:
            set_customer_workflow(quote["customer_id"], "quote_accepted", "Quote marked as accepted.", "Quote accepted")
    flash("Quote updated.")
    return redirect(url_for("quote_view", quote_id=quote_id))

@app.route("/quotes/<int:quote_id>/delete", methods=["POST"])
@login_required
def quote_delete(quote_id):
    archive_quote_record(quote_id)
    flash("Quote archived instead of being permanently deleted.")
    return redirect(url_for("quotes"))

@app.route("/quotes/<int:quote_id>/restore", methods=["POST"])
@login_required
def quote_restore(quote_id):
    restore_quote_record(quote_id)
    flash("Quote restored.")
    return redirect(url_for("quote_view", quote_id=quote_id))

@app.route("/quotes/<int:quote_id>/convert_to_job", methods=["POST"])
@login_required
def quote_to_job(quote_id):
    quote = q("SELECT * FROM quotes WHERE id=?", (quote_id,), one=True)
    if not quote:
        flash("Quote not found.")
        return redirect(url_for("quotes"))
    if (quote["status"] or "") == "Archived":
        flash("Archived quotes cannot be converted until they are restored.")
        return redirect(url_for("quote_view", quote_id=quote_id))
    existing_job = q("SELECT id FROM jobs WHERE quote_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC LIMIT 1", (quote_id,), one=True)
    if existing_job:
        flash("This quote has already been converted to a live job, so a duplicate was not created.")
        return redirect(url_for("job_view", job_id=existing_job["id"]))
    job_id = run("""INSERT INTO jobs(customer_id, quote_id, title, service_type, job_date, status, amount, assigned_to, notes)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (
        quote["customer_id"], quote_id, clean_str(request.form.get("title")) or quote["title"] or "Job from Quote",
        "Quote Conversion", clean_str(request.form.get("job_date")) or date.today().isoformat(),
        "Booked", quote["total"], clean_str(request.form.get("assigned_to")) or "", quote["notes"] or ""
    ))
    run("UPDATE quotes SET status='Converted' WHERE id=?", (quote_id,))
    if quote["customer_id"]:
        set_customer_workflow(quote["customer_id"], "quote_accepted", "Quote converted to a job.", "Quote accepted")
        set_customer_workflow(quote["customer_id"], "job_booked", "Job created from accepted quote.", "Job booked")
    flash("Quote converted to job.")
    return redirect(url_for("job_view", job_id=job_id))

@app.route("/jobs")
@login_required
def jobs():
    search = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    scope = (request.args.get("scope") or "active").strip().lower()
    if scope not in {"active", "archived", "all"}:
        scope = "active"
    sql = f"""SELECT jobs.*, customers.first_name || ' ' || customers.last_name AS customer_name
             FROM jobs LEFT JOIN customers ON customers.id = jobs.customer_id WHERE {list_scope_clause('jobs', scope)}"""
    params = []
    if search:
        like = f"%{search}%"
        sql += " AND (jobs.title LIKE ? OR customer_name LIKE ? OR jobs.assigned_to LIKE ?)"
        params += [like, like, like]
    if status:
        sql += " AND jobs.status=?"
        params.append(status)
    sql += " ORDER BY COALESCE(job_date,'9999-12-31') ASC, jobs.id DESC"
    rows = q(sql, tuple(params))
    return render_template("jobs.html", jobs=rows, search=search, status_filter=status, scope=scope)

@app.route("/jobs/new", methods=["POST"])
@login_required
def jobs_new():
    title = clean_str(request.form.get("title"))
    if not title:
        flash("Job title is required.")
        return redirect(url_for("jobs"))
    try:
        amount = parse_money(request.form.get("amount"), 0)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("jobs"))
    job_id = run("""INSERT INTO jobs(customer_id, title, service_type, job_date, status, amount, assigned_to, notes)
                    VALUES (?,?,?,?,?,?,?,?)""", (
        request.form.get("customer_id") or None, title, clean_str(request.form.get("service_type")),
        clean_str(request.form.get("job_date")), clean_str(request.form.get("status")) or "Booked", amount,
        clean_str(request.form.get("assigned_to")), clean_str(request.form.get("notes"))
    ))
    customer_id = request.form.get("customer_id") or None
    if customer_id and (clean_str(request.form.get("status")) or "Booked").lower() in {"booked", "in progress"}:
        set_customer_workflow(int(customer_id), "job_booked", "Job created in CRM.", "Job booked")
    flash("Job created.")
    return redirect(url_for("job_view", job_id=job_id))


@app.route("/jobs/<int:job_id>")
@login_required
def job_view(job_id):
    job = q("""SELECT jobs.*, customers.* FROM jobs
               LEFT JOIN customers ON customers.id = jobs.customer_id
               WHERE jobs.id=?""", (job_id,), one=True)
    existing_invoice = q("SELECT id FROM invoices WHERE job_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC LIMIT 1", (job_id,), one=True)
    recent_contacts = recent_customer_contacts(job["customer_id"] if job else None, 6)
    contact_summary = customer_contact_summary(job["customer_id"] if job else None, 30)
    return render_template("job_view.html", job=job, is_archived=((job["status"] or "") == "Archived"), existing_invoice_id=(existing_invoice["id"] if existing_invoice else None), recent_contacts=recent_contacts, contact_summary=contact_summary)

@app.route("/jobs/<int:job_id>/edit", methods=["POST"])
@login_required
def job_edit(job_id):
    title = clean_str(request.form.get("title"))
    if not title:
        flash("Job title is required.")
        return redirect(url_for("job_view", job_id=job_id))
    try:
        amount = parse_money(request.form.get("amount"), 0)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("job_view", job_id=job_id))
    status = clean_str(request.form.get("status")) or "Booked"
    if status == "Archived":
        flash("Use the Archive button to archive a job.")
        return redirect(url_for("job_view", job_id=job_id))
    run("""UPDATE jobs SET title=?, service_type=?, job_date=?, status=?, amount=?, assigned_to=?, notes=? WHERE id=?""", (
        title, clean_str(request.form.get("service_type")), clean_str(request.form.get("job_date")),
        status, amount, clean_str(request.form.get("assigned_to")),
        clean_str(request.form.get("notes")), job_id
    ))
    job = q("SELECT customer_id FROM jobs WHERE id=?", (job_id,), one=True)
    if job and job["customer_id"]:
        status_key = status.lower()
        if status_key in {"booked", "in progress"}:
            set_customer_workflow(job["customer_id"], "job_booked", "Job details updated.", "Job booked")
        elif status_key == "completed":
            set_customer_workflow(job["customer_id"], "job_completed", "Job marked completed.", "Job completed")
    flash("Job updated.")
    return redirect(url_for("job_view", job_id=job_id))


@app.route("/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
def job_delete(job_id):
    archive_job_record(job_id)
    flash("Job archived instead of being permanently deleted.")
    return redirect(url_for("jobs"))

@app.route("/jobs/<int:job_id>/restore", methods=["POST"])
@login_required
def job_restore(job_id):
    restore_job_record(job_id)
    flash("Job restored.")
    return redirect(url_for("job_view", job_id=job_id))

@app.route("/jobs/<int:job_id>/convert_to_invoice", methods=["POST"])
@login_required
def job_to_invoice(job_id):
    job = q("SELECT * FROM jobs WHERE id=?", (job_id,), one=True)
    if not job:
        flash("Job not found.")
        return redirect(url_for("jobs"))
    if (job["status"] or "") == "Archived":
        flash("Archived jobs cannot be invoiced until they are restored.")
        return redirect(url_for("job_view", job_id=job_id))
    existing_invoice = q("SELECT id FROM invoices WHERE job_id=? AND IFNULL(status,'') <> 'Archived' ORDER BY id DESC LIMIT 1", (job_id,), one=True)
    if existing_invoice:
        flash("This job already has a live invoice, so a duplicate invoice was not created.")
        return redirect(url_for("invoice_view", invoice_id=existing_invoice["id"]))
    payload = {}
    if job["quote_id"]:
        qr = q("SELECT payload_json FROM quotes WHERE id=?", (job["quote_id"],), one=True)
        if qr and qr["payload_json"]:
            payload = json.loads(qr["payload_json"])
    calc = calc_from_payload(payload) if payload else {
        "subtotal": float(job["amount"] or 0),
        "vat": 0.0,
        "total": float(job["amount"] or 0),
        "lines": [],
        "raw_total": float(job["amount"] or 0),
        "minimum": float(settings()["minimum_charge"] or 100),
        "include_vat": False
    }
    invoice_id = run("""INSERT INTO invoices(customer_id, job_id, quote_id, invoice_number, invoice_date, due_date, status, subtotal, vat, total, payload_json, notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
        job["customer_id"], job_id, job["quote_id"], next_invoice_number(),
        clean_str(request.form.get("invoice_date")) or date.today().isoformat(), clean_str(request.form.get("due_date")) or "",
        "Draft", calc["subtotal"], calc["vat"], calc["total"], json.dumps(payload), job["notes"] or ""
    ))
    run("UPDATE jobs SET status='Invoiced' WHERE id=?", (job_id,))
    if job["customer_id"]:
        set_customer_workflow(job["customer_id"], "invoice_created", "Invoice created from completed job.", "Invoice created")
    flash("Invoice created from job.")
    return redirect(url_for("invoice_view", invoice_id=invoice_id))

@app.route("/invoices")
@login_required
def invoices():
    search = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "active").strip().lower()
    if scope not in {"active", "archived", "all"}:
        scope = "active"
    sql = f"""SELECT invoices.*, customers.first_name || ' ' || customers.last_name AS customer_name
             FROM invoices LEFT JOIN customers ON customers.id = invoices.customer_id WHERE {list_scope_clause('invoices', scope)}"""
    params = []
    if search:
        like = f"%{search}%"
        sql += " AND (invoice_number LIKE ? OR customer_name LIKE ?)"
        params += [like, like]
    sql += " ORDER BY invoices.id DESC"
    rows = q(sql, tuple(params))
    return render_template("invoices.html", invoices=rows, search=search, scope=scope)

@app.route("/invoices/<int:invoice_id>")
@login_required
def invoice_view(invoice_id):
    invoice = q("""SELECT invoices.*, customers.*, customers.first_name || ' ' || customers.last_name AS customer_name FROM invoices
                   LEFT JOIN customers ON customers.id = invoices.customer_id
                   WHERE invoices.id=?""", (invoice_id,), one=True)
    payload = json.loads(invoice["payload_json"] or "{}") if invoice["payload_json"] else {}
    calc = calc_from_payload(payload) if payload else {"lines": [], "subtotal": invoice["subtotal"], "vat": invoice["vat"], "total": invoice["total"]}
    due = parse_iso_date(invoice['due_date'])
    today = date.today()
    is_overdue = bool(due and due < today and clean_str(invoice['status']).lower() != 'paid' and clean_str(invoice['status']).lower() != 'archived')
    is_due_soon = bool(due and 0 <= (due - today).days <= 7 and clean_str(invoice['status']).lower() != 'paid' and clean_str(invoice['status']).lower() != 'archived')
    reminder_subject = build_invoice_reminder_subject(dict(invoice))
    reminder_body = build_invoice_reminder_body(dict(invoice))
    due_obj = parse_iso_date(invoice['due_date'])
    reminder_stage = expense_stage_label({**dict(invoice), 'is_overdue': is_overdue, 'days_until_due': ((due_obj - date.today()).days if due_obj else None)})
    recent_contacts = recent_customer_contacts(invoice["customer_id"] if invoice else None, 6)
    contact_summary = customer_contact_summary(invoice["customer_id"] if invoice else None, 30)
    return render_template("invoice_view.html", invoice=invoice, calc=calc, is_archived=((invoice["status"] or "") == "Archived"), is_overdue=is_overdue, is_due_soon=is_due_soon, reminder_subject=reminder_subject, reminder_body=reminder_body, reminder_stage=reminder_stage, recent_contacts=recent_contacts, contact_summary=contact_summary)

@app.route("/invoices/<int:invoice_id>/edit", methods=["POST"])
@login_required
def invoice_edit(invoice_id):
    status = clean_str(request.form.get("status")) or "Draft"
    if status == "Archived":
        flash("Use the Archive button to archive an invoice.")
        return redirect(url_for("invoice_view", invoice_id=invoice_id))
    run("""UPDATE invoices SET invoice_date=?, due_date=?, status=?, notes=? WHERE id=?""", (
        clean_str(request.form.get("invoice_date")), clean_str(request.form.get("due_date")), status,
        clean_str(request.form.get("notes")), invoice_id
    ))
    invoice = q("SELECT customer_id FROM invoices WHERE id=?", (invoice_id,), one=True)
    if invoice and invoice["customer_id"]:
        status_key = status.lower()
        if status_key in {"sent", "overdue"}:
            set_customer_workflow(invoice["customer_id"], "invoice_sent", "Invoice marked as sent.", "Invoice sent")
        elif status_key == "paid":
            set_customer_workflow(invoice["customer_id"], "payment_received", "Invoice marked as paid.", "Payment received")
    flash("Invoice updated.")
    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
def invoice_delete(invoice_id):
    archive_invoice_record(invoice_id)
    flash("Invoice archived instead of being permanently deleted.")
    return redirect(url_for("invoices"))

@app.route("/invoices/<int:invoice_id>/restore", methods=["POST"])
@login_required
def invoice_restore(invoice_id):
    restore_invoice_record(invoice_id)
    flash("Invoice restored.")
    return redirect(url_for("invoice_view", invoice_id=invoice_id))

@app.route("/exports/customers.csv")
@login_required
def export_customers_csv():
    rows = q("""SELECT id, first_name, last_name, phone, email, address, town, postcode, source, tags, notes, created_at, archived_at
                FROM customers ORDER BY id DESC""")
    data = [[r["id"], r["first_name"], r["last_name"], r["phone"], r["email"], r["address"], r["town"], r["postcode"], r["source"], r["tags"], r["notes"], r["created_at"], r["archived_at"]] for r in rows]
    return export_rows_to_csv("customers_export", ["ID", "First Name", "Last Name", "Phone", "Email", "Address", "Town", "Postcode", "Source", "Tags", "Notes", "Created At", "Archived At"], data)


@app.route("/exports/quotes.csv")
@login_required
def export_quotes_csv():
    rows = q("""SELECT quotes.id, quotes.quote_number, quotes.title, quotes.quote_date, quotes.valid_until, quotes.status,
                      quotes.subtotal, quotes.vat, quotes.total, quotes.created_at,
                      customers.first_name || ' ' || customers.last_name AS customer_name
               FROM quotes LEFT JOIN customers ON customers.id = quotes.customer_id
               ORDER BY quotes.id DESC""")
    data = [[r["id"], r["quote_number"], r["customer_name"], r["title"], r["quote_date"], r["valid_until"], r["status"], r["subtotal"], r["vat"], r["total"], r["created_at"]] for r in rows]
    return export_rows_to_csv("quotes_export", ["ID", "Quote Number", "Customer", "Title", "Quote Date", "Valid Until", "Status", "Subtotal", "VAT", "Total", "Created At"], data)


@app.route("/exports/jobs.csv")
@login_required
def export_jobs_csv():
    rows = q("""SELECT jobs.id, jobs.title, jobs.service_type, jobs.job_date, jobs.status, jobs.amount, jobs.assigned_to, jobs.notes, jobs.created_at,
                      customers.first_name || ' ' || customers.last_name AS customer_name
               FROM jobs LEFT JOIN customers ON customers.id = jobs.customer_id
               ORDER BY COALESCE(jobs.job_date,'9999-12-31') ASC, jobs.id DESC""")
    data = [[r["id"], r["customer_name"], r["title"], r["service_type"], r["job_date"], r["status"], r["amount"], r["assigned_to"], r["notes"], r["created_at"]] for r in rows]
    return export_rows_to_csv("jobs_export", ["ID", "Customer", "Title", "Service Type", "Job Date", "Status", "Amount", "Assigned To", "Notes", "Created At"], data)


@app.route("/exports/invoices.csv")
@login_required
def export_invoices_csv():
    rows = q("""SELECT invoices.id, invoices.invoice_number, invoices.invoice_date, invoices.due_date, invoices.status,
                      invoices.subtotal, invoices.vat, invoices.total, invoices.created_at,
                      customers.first_name || ' ' || customers.last_name AS customer_name
               FROM invoices LEFT JOIN customers ON customers.id = invoices.customer_id
               ORDER BY invoices.id DESC""")
    data = [[r["id"], r["invoice_number"], r["customer_name"], r["invoice_date"], r["due_date"], r["status"], r["subtotal"], r["vat"], r["total"], r["created_at"]] for r in rows]
    return export_rows_to_csv("invoices_export", ["ID", "Invoice Number", "Customer", "Invoice Date", "Due Date", "Status", "Subtotal", "VAT", "Total", "Created At"], data)

@app.route("/exports/expenses.csv")
@login_required
def export_expenses_csv():
    rows = q("SELECT * FROM expenses ORDER BY COALESCE(expense_date, created_at) DESC, id DESC")
    data = [[r["id"], r["expense_date"], r["category"], r["supplier"], r["description"], r["amount"], r["vat_amount"], (float(r["amount"] or 0) + float(r["vat_amount"] or 0)), r["notes"], r["created_at"], r["archived_at"]] for r in rows]
    return export_rows_to_csv("expenses_export", ["ID", "Expense Date", "Category", "Supplier", "Description", "Net Amount", "VAT Amount", "Gross Amount", "Notes", "Created At", "Archived At"], data)


@app.route("/backup/download")
@login_required
def download_backup():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        create_backup_zip_bytes(),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"crm_backup_{stamp}.zip"
    )

@app.route("/backup/create")
@login_required
def create_backup_snapshot_route():
    backup_path = save_backup_snapshot()
    flash(f"Backup saved: {os.path.basename(backup_path)}")
    return redirect(url_for("backup_centre"))

@app.route("/backups")
@login_required
def backup_centre():
    backups = list_backup_files()
    return render_template("backups.html", backups=backups)

@app.route("/backups/<path:filename>")
@login_required
def download_saved_backup(filename):
    safe_name = os.path.basename(filename)
    backup_path = os.path.join(ensure_backup_dir(), safe_name)
    if not os.path.isfile(backup_path):
        flash("That backup file was not found.")
        return redirect(url_for("backup_centre"))
    return send_file(backup_path, mimetype="application/zip", as_attachment=True, download_name=safe_name)

@app.route("/reports")
@login_required
def reports():
    report_data = build_reports_data(6)
    return render_template("reports.html", report_data=report_data, segment_data=build_customer_segmentation_snapshot())


@app.route("/feedback")
@login_required
def feedback_library():
    rows = q("""SELECT customer_feedback.*, customers.first_name || ' ' || customers.last_name AS customer_name, jobs.title AS job_title
                FROM customer_feedback
                LEFT JOIN customers ON customers.id = customer_feedback.customer_id
                LEFT JOIN jobs ON jobs.id = customer_feedback.job_id
                ORDER BY customer_feedback.id DESC""")
    return render_template("feedback.html", feedback_rows=rows)


@app.route("/reminders")
@login_required
def reminders_library():
    scope = clean_str(request.args.get("scope") or "open").lower()
    if scope not in {"open", "completed", "all"}:
        scope = "open"
    where = ""
    if scope == "open":
        where = "WHERE IFNULL(future_reminders.status,'Open')='Open'"
    elif scope == "completed":
        where = "WHERE IFNULL(future_reminders.status,'Open')='Completed'"
    rows = q(f"""SELECT future_reminders.*, customers.first_name || ' ' || customers.last_name AS customer_name, jobs.title AS job_title
                 FROM future_reminders
                 LEFT JOIN customers ON customers.id = future_reminders.customer_id
                 LEFT JOIN jobs ON jobs.id = future_reminders.job_id
                 {where}
                 ORDER BY COALESCE(reminder_date,'9999-12-31') ASC, future_reminders.id DESC""")
    return render_template("reminders.html", reminders=rows, scope=scope)


@app.route("/reminders/<int:reminder_id>/complete", methods=["POST"])
@login_required
def reminder_complete(reminder_id):
    row = q("SELECT * FROM future_reminders WHERE id=?", (reminder_id,), one=True)
    if not row:
        flash("Reminder not found.")
        return redirect(url_for("reminders_library"))
    run("UPDATE future_reminders SET status='Completed', completed_at=datetime('now') WHERE id=?", (reminder_id,))
    flash("Reminder completed.")
    return redirect(request.form.get("next_url") or url_for("reminders_library"))


def segment_followup_defaults(segment):
    labels = {
        'new': 'New Customers',
        'active': 'Active Customers',
        'warm': 'Warm Customers',
        'cooling_off': 'Cooling Off',
        'reactivation_6m': 'Reactivation 6 to 12 Months',
        'reactivation_12m': 'Reactivation 12 Plus Months',
        'no_invoice_date': 'No Invoice Date',
        'reactivation_candidates': 'All Reactivation',
    }
    subject_map = {
        'new': 'Thanks again for choosing {{business_name}}',
        'active': 'Quick follow up from {{business_name}}',
        'warm': 'Just checking in from {{business_name}}',
        'cooling_off': 'It may be time for another clean',
        'reactivation_6m': 'Ready for another carpet clean?',
        'reactivation_12m': 'We would love to help again',
        'no_invoice_date': 'Quick hello from {{business_name}}',
        'reactivation_candidates': 'Are you ready for another clean?',
    }
    body_map = {
        'new': "Hi {{first_name}}\n\nThank you again for choosing {{business_name}}. I just wanted to check that everything was spot on after the clean. If you need anything else, just reply to this email.\n\nThanks\n{{business_name}}",
        'active': "Hi {{first_name}}\n\nJust a quick follow up from {{business_name}}. If there is anything else you would like cleaned, or if you would like to get your next visit booked in, just reply to this email.\n\nThanks\n{{business_name}}",
        'warm': "Hi {{first_name}}\n\nI just wanted to check in and see whether you would like another clean booked in. If you want a freshen up for your carpets, rugs, or upholstery, just reply and I will get back to you.\n\nThanks\n{{business_name}}",
        'cooling_off': "Hi {{first_name}}\n\nIt has been a little while since your last clean, so I just wanted to check whether you would like a freshen up booked in. Reply to this email and I can price it up for you.\n\nThanks\n{{business_name}}",
        'reactivation_6m': "Hi {{first_name}}\n\nIt may be time for another carpet or upholstery clean, so I just wanted to get in touch. If you would like an updated quote, just reply with the rooms or items you want cleaned.\n\nThanks\n{{business_name}}",
        'reactivation_12m': "Hi {{first_name}}\n\nWe have not seen you for a while, so I just wanted to say hello. If you would like your carpets, rugs, or upholstery cleaned again, reply to this email and I will happily sort a quote for you.\n\nThanks\n{{business_name}}",
        'no_invoice_date': "Hi {{first_name}}\n\nJust a quick hello from {{business_name}}. If you would like a quote or want to get anything booked in, simply reply to this email and I will come back to you.\n\nThanks\n{{business_name}}",
        'reactivation_candidates': "Hi {{first_name}}\n\nI just wanted to get in touch to see whether you would like another clean booked in. If you want me to price anything up, just reply with the areas you want cleaned.\n\nThanks\n{{business_name}}",
    }
    return {
        'label': labels.get(segment, 'Segment Campaign'),
        'subject': subject_map.get(segment, 'Quick follow up from {{business_name}}'),
        'body': body_map.get(segment, "Hi {{first_name}}\n\nJust a quick follow up from {{business_name}}.\n\nThanks\n{{business_name}}"),
    }


@app.route('/segments')
@login_required
def segments_page():
    segment_data = build_customer_segmentation_snapshot()
    segment = clean_str(request.args.get('segment') or 'reactivation_6m') or 'reactivation_6m'
    valid_segments = ['new', 'active', 'warm', 'cooling_off', 'reactivation_6m', 'reactivation_12m', 'no_invoice_date', 'reactivation_candidates']
    if segment not in valid_segments:
        segment = 'reactivation_6m'
    rows = segment_data.get(segment, [])
    labels = {
        'new': 'New',
        'active': 'Active',
        'warm': 'Warm',
        'cooling_off': 'Cooling Off',
        'reactivation_6m': 'Reactivation 6 to 12 Months',
        'reactivation_12m': 'Reactivation 12 Months Plus',
        'no_invoice_date': 'No Invoice Date',
        'reactivation_candidates': 'All Reactivation Candidates',
    }
    return render_template('segments.html', segment_data=segment_data, current_segment=segment, current_rows=rows, segment_label=labels.get(segment, segment))


@app.route('/segments/batch-prepare', methods=['POST'])
@login_required
def segments_batch_prepare():
    segment = (request.form.get('segment') or 'reactivation_candidates').strip().lower()
    selected_ids = []
    for raw in request.form.getlist('customer_ids'):
        try:
            selected_ids.append(int(raw))
        except Exception:
            pass
    if not selected_ids:
        flash('Please tick at least one customer first.')
        return redirect(url_for('segments_page', segment=segment))
    placeholders = ','.join('?' for _ in selected_ids)
    sql = (
        "SELECT c.id AS customer_id, "
        "TRIM(COALESCE(c.first_name,'') || ' ' || COALESCE(c.last_name,'')) AS name, "
        "c.first_name, c.last_name, c.email, c.phone, c.town, "
        "COUNT(i.id) AS invoice_count, "
        "MAX(NULLIF(i.invoice_date,'')) AS last_invoice_date, "
        "COALESCE(SUM(CASE WHEN LOWER(IFNULL(i.status,''))='paid' THEN IFNULL(i.total,0) ELSE 0 END),0) AS paid_total "
        "FROM customers c "
        "LEFT JOIN invoices i ON i.customer_id = c.id AND IFNULL(i.status,'') <> 'Archived' "
        f"WHERE c.id IN ({placeholders}) "
        "GROUP BY c.id, c.first_name, c.last_name, c.email, c.phone, c.town "
        "ORDER BY c.first_name, c.last_name"
    )
    rows = q(sql, tuple(selected_ids))
    rows = [r for r in rows if r['email']]
    if not rows:
        flash('None of the selected customers have an email address saved.')
        return redirect(url_for('segments_page', segment=segment))
    defaults = segment_followup_defaults(segment)
    return render_template('segments_batch.html',
                           segment=segment,
                           segment_label=defaults['label'],
                           rows=rows,
                           selected_ids=selected_ids,
                           default_subject=defaults['subject'],
                           default_body=defaults['body'])


@app.route('/segments/batch-sms-prepare', methods=['POST'])
@login_required
def segments_batch_sms_prepare():
    segment = (request.form.get('segment') or 'reactivation_candidates').strip().lower()
    selected_ids = []
    for raw in request.form.getlist('customer_ids'):
        try:
            selected_ids.append(int(raw))
        except Exception:
            pass
    if not selected_ids:
        flash('Please tick at least one customer first.')
        return redirect(url_for('segments_page', segment=segment))
    placeholders = ','.join('?' for _ in selected_ids)
    rows = q(f"""SELECT id AS customer_id, first_name, last_name, email, phone, town
                 FROM customers WHERE id IN ({placeholders}) ORDER BY first_name, last_name""", tuple(selected_ids))
    rows = [r for r in rows if (r['phone'] or '').strip()]
    if not rows:
        flash('None of the selected customers have a phone number saved.')
        return redirect(url_for('segments_page', segment=segment))
    defaults = segment_followup_defaults(segment)
    return render_template('segments_sms.html',
                           segment=segment,
                           segment_label=defaults['label'],
                           rows=rows,
                           selected_ids=selected_ids,
                           default_body=defaults['body'])


@app.route('/segments/batch-sms-log', methods=['POST'])
@login_required
def segments_batch_sms_log():
    segment = (request.form.get('segment') or 'reactivation_candidates').strip().lower()
    body_template = request.form.get('body') or ''
    selected_ids = []
    for raw in request.form.getlist('customer_ids'):
        try:
            selected_ids.append(int(raw))
        except Exception:
            pass
    if not selected_ids:
        flash('No customers were selected.')
        return redirect(url_for('segments_page', segment=segment))
    placeholders = ','.join('?' for _ in selected_ids)
    customers = q(f"SELECT * FROM customers WHERE id IN ({placeholders}) ORDER BY first_name, last_name", tuple(selected_ids))
    customers = [c for c in customers if c['phone']]
    if not customers:
        flash('None of the selected customers have a phone number saved.')
        return redirect(url_for('segments_page', segment=segment))
    batch_id = log_campaign_batch('SMS', segment, 'prep', f'SMS prep {segment}', '', body_template, recipient_count=len(customers), sent_count=0, status='Prepared', notes='Prepared inside CRM only. No SMS gateway send.')
    for customer in customers:
        rendered_body = safe_replace(body_template, comms_replacements(customer))
        db().execute(
            "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
            (customer['id'], 'SMS', '', rendered_body)
        )
        log_campaign_item(batch_id, customer['id'], recipient=customer.get('email') or '', phone=customer.get('phone') or '', item_status='Prepared')
    db().commit()
    flash(f'SMS prep list saved for {len(customers)} customers. This logs the campaign inside the CRM but does not send texts automatically.')
    return redirect(url_for('campaign_history'))


@app.route('/segments/batch-send', methods=['POST'])
@login_required
def segments_batch_send():
    segment = (request.form.get('segment') or 'reactivation_candidates').strip().lower()
    subject_template = request.form.get('subject') or ''
    body_template = request.form.get('body') or ''
    mode = (request.form.get('mode') or 'individual').strip().lower()
    selected_ids = []
    for raw in request.form.getlist('customer_ids'):
        try:
            selected_ids.append(int(raw))
        except Exception:
            pass
    if not selected_ids:
        flash('No customers were selected.')
        return redirect(url_for('segments_page', segment=segment))
    placeholders = ','.join('?' for _ in selected_ids)
    customers = q(f"SELECT * FROM customers WHERE id IN ({placeholders}) ORDER BY first_name, last_name", tuple(selected_ids))
    customers = [c for c in customers if c['email']]
    if not customers:
        flash('None of the selected customers have an email address saved.')
        return redirect(url_for('segments_page', segment=segment))

    sent_count = 0
    errors = []
    batch_id = log_campaign_batch('Email', segment, mode, f'Email campaign {segment}', subject_template, body_template, recipient_count=len(customers), sent_count=0, status='Started')
    if mode == 'group':
        recipients = [c['email'] for c in customers if c['email']]
        rendered_subject = safe_replace(subject_template, comms_replacements(None))
        rendered_body = safe_replace(body_template, comms_replacements(None))
        ok, msg = send_email_smtp(', '.join(recipients), rendered_subject, rendered_body)
        if not ok:
            flash(msg)
            return redirect(url_for('segments_page', segment=segment))
        sent_count = len(recipients)
        for customer in customers:
            db().execute(
                "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
                (customer['id'], 'Email', rendered_subject, rendered_body)
            )
            log_campaign_item(batch_id, customer['id'], recipient=customer.get('email') or '', phone=customer.get('phone') or '', item_status='Sent')
        run("UPDATE campaign_batches SET sent_count=?, status=? WHERE id=?", (sent_count, 'Sent', batch_id))
        db().commit()
        flash(msg)
    else:
        for customer in customers:
            rendered_subject = safe_replace(subject_template, comms_replacements(customer))
            rendered_body = safe_replace(body_template, comms_replacements(customer))
            ok, msg = send_email_smtp(customer['email'], rendered_subject, rendered_body, customer=customer)
            if ok:
                sent_count += 1
                db().execute(
                    "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
                    (customer['id'], 'Email', rendered_subject, rendered_body)
                )
                log_campaign_item(batch_id, customer['id'], recipient=customer.get('email') or '', phone=customer.get('phone') or '', item_status='Sent')
            else:
                errors.append(f"{customer['first_name']} {customer['last_name']}: {msg}")
                log_campaign_item(batch_id, customer['id'], recipient=customer.get('email') or '', phone=customer.get('phone') or '', item_status='Failed', error_text=msg)
        run("UPDATE campaign_batches SET sent_count=?, status=? WHERE id=?", (sent_count, 'Sent' if sent_count else 'Failed', batch_id))
        db().commit()
        if sent_count:
            flash(f'Sent {sent_count} individual emails.')
        if errors:
            flash('Some emails failed: ' + ' | '.join(errors[:3]))
    return redirect(url_for('campaign_history'))


@app.route('/segments/export.csv')
@login_required
def export_segments_csv():
    segment_data = build_customer_segmentation_snapshot()
    segment = clean_str(request.args.get('segment') or 'reactivation_6m') or 'reactivation_6m'
    rows = segment_data.get(segment, [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Customer ID','Name','Email','Phone','Town','Segment','Last Invoice Date','Days Since Last Invoice','Invoice Count','Paid Count','Lifetime Invoiced','Lifetime Paid'])
    for r in rows:
        writer.writerow([
            r.get('customer_id'), r.get('name'), r.get('email'), r.get('phone'), r.get('town'), r.get('segment'),
            r.get('last_invoice_date'), r.get('days_since_last_invoice'), r.get('invoice_count'), r.get('paid_count'),
            f"{float(r.get('invoice_total') or 0):.2f}", f"{float(r.get('paid_total') or 0):.2f}"
        ])
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=segment_{segment}.csv'})


@app.route('/segments/<int:customer_id>/email')
@login_required
def segment_email_customer(customer_id):
    segment = clean_str(request.args.get('segment') or 'reactivation_6m') or 'reactivation_6m'
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        flash('Customer not found.')
        return redirect(url_for('segments_page', segment=segment))
    subject_map = {
        'new': 'Just checking in after your recent clean',
        'active': 'Would you like to get your next clean booked in',
        'warm': 'Would you like a freshen up booked in',
        'cooling_off': 'Would you like an updated clean quote',
        'reactivation_6m': 'Would you like a return visit or updated quote',
        'reactivation_12m': 'Would you like a fresh clean or updated quote',
        'no_invoice_date': 'Would you like a quote for carpet or upholstery cleaning',
    }
    first_name = clean_str(customer['first_name']) or 'there'
    body = segmentation_follow_up_text(segment, first_name)
    return redirect(url_for('communications', customer_id=customer_id, channel='Email', subject=subject_map.get(segment, 'Quick follow up'), body=body))


@app.route("/recurring-income", methods=["GET", "POST"])
@login_required
def recurring_income():
    view = (request.args.get('view') or 'active').lower()
    edit_id = int(request.args.get('edit') or 0)
    if request.method == 'POST':
        customer_id = request.form.get('customer_id') or None
        payer_name = clean_str(request.form.get('payer_name'))
        start_date = clean_str(request.form.get('start_date')) or date.today().isoformat()
        description = clean_str(request.form.get('description')) or 'Recurring Service Plan'
        amount = parse_money(request.form.get('amount'))
        frequency = clean_str(request.form.get('frequency')) or 'Monthly'
        collection_method = clean_str(request.form.get('collection_method')) or 'Direct Debit'
        include_vat = 1 if request.form.get('include_vat') else 0
        notes = clean_str(request.form.get('notes'))
        auto_payment_rule = clean_str(request.form.get('auto_payment_rule')) or 'Default by Method'
        if amount is None or amount < 0:
            flash('Please enter a valid recurring income amount.')
            return redirect(url_for('recurring_income', view=view))
        start_obj = parse_iso_date(start_date) or date.today()
        next_due = next_due_date_for_frequency(start_obj, frequency) or start_obj
        run("INSERT INTO recurring_income (customer_id, payer_name, start_date, next_due_date, description, amount, include_vat, frequency, collection_method, auto_payment_rule, notes, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
            (customer_id, payer_name, start_obj.isoformat(), next_due.isoformat(), description, amount, include_vat, frequency, collection_method, auto_payment_rule, notes))
        flash('Recurring income plan saved.')
        return redirect(url_for('recurring_income', view=view))
    where_clause = list_scope_clause('recurring_income', view, archived_column='archived_at')
    rows = q(f"""SELECT recurring_income.*, customers.first_name, customers.last_name, customers.email, customers.phone
                 FROM recurring_income
                 LEFT JOIN customers ON customers.id = recurring_income.customer_id
                 WHERE {where_clause}
                 ORDER BY recurring_income.archived_at IS NOT NULL, date(IFNULL(recurring_income.next_due_date, recurring_income.start_date)) ASC, recurring_income.id DESC""")
    totals = {
        'amount': round(sum(float(r['amount'] or 0) for r in rows if not r['archived_at']), 2),
        'due_count': len([r for r in rows if not r['archived_at'] and (parse_iso_date(r['next_due_date']) or parse_iso_date(r['start_date']) or date.today()) <= date.today()]),
        'direct_debit_count': len([r for r in rows if not r['archived_at'] and (r['collection_method'] or '') == 'Direct Debit']),
    }
    totals['due_value'] = round(sum(float(r['amount'] or 0) for r in rows if not r['archived_at'] and (parse_iso_date(r['next_due_date']) or parse_iso_date(r['start_date']) or date.today()) <= date.today()), 2)
    totals['forecast_30'] = cashflow_snapshot()['recurring_income_due_30_value']
    customers_active = q("SELECT id, first_name, last_name, email FROM customers WHERE archived_at IS NULL ORDER BY first_name, last_name")
    edit_plan = q("SELECT * FROM recurring_income WHERE id=?", (edit_id,), one=True) if edit_id else None
    return render_template('recurring_income.html', plans=rows, recurring_view=view, recurring_options=recurring_frequency_options(),
                           collection_options=recurring_collection_options(), payment_rule_options=recurring_payment_rule_options(), totals=totals, customers_active=customers_active,
                           edit_plan=edit_plan, payment_rule_label=recurring_payment_rule_label)


@app.route('/recurring-income/<int:plan_id>/edit', methods=['POST'])
@login_required
def recurring_income_edit(plan_id):
    plan = q("SELECT * FROM recurring_income WHERE id=?", (plan_id,), one=True)
    if not plan:
        flash('Recurring income plan not found.')
        return redirect(url_for('recurring_income'))
    customer_id = request.form.get('customer_id') or None
    payer_name = clean_str(request.form.get('payer_name'))
    start_date = clean_str(request.form.get('start_date')) or plan['start_date'] or date.today().isoformat()
    description = clean_str(request.form.get('description')) or 'Recurring Service Plan'
    amount = parse_money(request.form.get('amount'))
    frequency = clean_str(request.form.get('frequency')) or 'Monthly'
    collection_method = clean_str(request.form.get('collection_method')) or 'Direct Debit'
    include_vat = 1 if request.form.get('include_vat') else 0
    notes = clean_str(request.form.get('notes'))
    auto_payment_rule = clean_str(request.form.get('auto_payment_rule')) or 'Default by Method'
    if amount is None or amount < 0:
        flash('Please enter a valid recurring income amount.')
        return redirect(url_for('recurring_income', edit=plan_id))
    start_obj = parse_iso_date(start_date) or date.today()
    current_due = parse_iso_date(plan['next_due_date']) or start_obj
    if current_due < start_obj:
        current_due = start_obj
    run("UPDATE recurring_income SET customer_id=?, payer_name=?, start_date=?, next_due_date=?, description=?, amount=?, include_vat=?, frequency=?, collection_method=?, auto_payment_rule=?, notes=? WHERE id=?",
        (customer_id, payer_name, start_obj.isoformat(), current_due.isoformat(), description, amount, include_vat, frequency, collection_method, auto_payment_rule, notes, plan_id))
    flash('Recurring income plan updated.')
    return redirect(url_for('recurring_income'))


@app.route('/recurring-income/run', methods=['POST'])
@login_required
def recurring_income_run():
    rows = q("SELECT * FROM recurring_income WHERE archived_at IS NULL AND active=1 AND date(IFNULL(next_due_date,start_date)) <= date('now') ORDER BY date(IFNULL(next_due_date,start_date)) ASC, id ASC")
    created = 0
    duplicates = 0
    for row in rows:
        due_date = parse_iso_date(row['next_due_date']) or parse_iso_date(row['start_date']) or date.today()
        _invoice_id, was_created = create_invoice_from_recurring_income(row, due_date, manual=False)
        if was_created:
            next_due = next_due_date_for_frequency(due_date, row['frequency']) or due_date
            run("UPDATE recurring_income SET last_posted_at=CURRENT_TIMESTAMP, next_due_date=? WHERE id=?", (next_due.isoformat(), row['id']))
            created += 1
        else:
            duplicates += 1
    if created:
        flash(f'{created} recurring income invoices posted.' + (f' {duplicates} duplicates skipped.' if duplicates else ''))
    else:
        flash('No recurring income invoices were due yet.' if not duplicates else f'No new invoices were created. {duplicates} duplicates were skipped.')
    return redirect(url_for('recurring_income'))


@app.route('/recurring-income/<int:plan_id>/post-now', methods=['POST'])
@login_required
def recurring_income_post_now(plan_id):
    row = q("SELECT * FROM recurring_income WHERE id=?", (plan_id,), one=True)
    if not row or row['archived_at']:
        flash('Recurring income plan not found.')
        return redirect(url_for('recurring_income'))
    invoice_id, was_created = create_invoice_from_recurring_income(row, date.today(), manual=True)
    if was_created:
        next_due = next_due_date_for_frequency(date.today(), row['frequency']) or date.today()
        run("UPDATE recurring_income SET last_posted_at=CURRENT_TIMESTAMP, next_due_date=? WHERE id=?", (next_due.isoformat(), plan_id))
        flash('Recurring income invoice posted now.')
        return redirect(url_for('invoice_view', invoice_id=invoice_id))
    flash('A recurring invoice for today already exists, so a duplicate was not created.')
    return redirect(url_for('invoice_view', invoice_id=invoice_id))


@app.route('/recurring-income/<int:plan_id>/archive', methods=['POST'])
@login_required
def recurring_income_archive(plan_id):
    run("UPDATE recurring_income SET archived_at=CURRENT_TIMESTAMP, active=0 WHERE id=?", (plan_id,))
    flash('Recurring income plan archived.')
    return redirect(url_for('recurring_income'))


@app.route('/recurring-income/<int:plan_id>/restore', methods=['POST'])
@login_required
def recurring_income_restore(plan_id):
    run("UPDATE recurring_income SET archived_at=NULL, active=1 WHERE id=?", (plan_id,))
    flash('Recurring income plan restored.')
    return redirect(url_for('recurring_income', view='archived'))


@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    view = (request.args.get('view') or 'active').lower()
    edit_id = int(request.args.get('edit') or 0)
    if request.method == 'POST':
        expense_date = clean_str(request.form.get('expense_date')) or date.today().isoformat()
        category = clean_str(request.form.get('category')) or 'Other'
        supplier = clean_str(request.form.get('supplier'))
        description = clean_str(request.form.get('description'))
        notes = clean_str(request.form.get('notes'))
        amount = parse_money(request.form.get('amount'))
        vat_amount = parse_money(request.form.get('vat_amount'))
        if amount is None or amount < 0:
            flash('Please enter a valid expense amount.')
            return redirect(url_for('expenses', view=view))
        if vat_amount is None or vat_amount < 0:
            vat_amount = 0.0
        run("INSERT INTO expenses (expense_date, category, supplier, description, amount, vat_amount, notes) VALUES (?,?,?,?,?,?,?)",
            (expense_date, category, supplier, description, amount, vat_amount, notes))
        flash('Expense added.')
        return redirect(url_for('expenses', view=view))
    where_clause = list_scope_clause('expenses', view, archived_column='archived_at')
    rows = q(f"SELECT * FROM expenses WHERE {where_clause} ORDER BY COALESCE(expense_date, created_at) DESC, id DESC")
    totals = {
        'amount': round(sum(float(r['amount'] or 0) for r in rows), 2),
        'vat': round(sum(float(r['vat_amount'] or 0) for r in rows), 2),
    }
    totals['gross'] = round(totals['amount'] + totals['vat'], 2)
    supplier_summary = q("""SELECT COALESCE(NULLIF(TRIM(supplier),''), 'Unassigned') AS name,
                             ROUND(SUM(amount),2) AS total, COUNT(*) AS item_count
                          FROM expenses WHERE archived_at IS NULL
                          GROUP BY COALESCE(NULLIF(TRIM(supplier),''), 'Unassigned')
                          ORDER BY total DESC, item_count DESC LIMIT 8""")
    category_summary = q("""SELECT COALESCE(NULLIF(TRIM(category),''), 'Other') AS name,
                             ROUND(SUM(amount),2) AS total, COUNT(*) AS item_count
                          FROM expenses WHERE archived_at IS NULL
                          GROUP BY COALESCE(NULLIF(TRIM(category),''), 'Other')
                          ORDER BY total DESC, item_count DESC LIMIT 8""")
    recurring_rows = q("SELECT * FROM recurring_expenses ORDER BY archived_at IS NOT NULL, date(IFNULL(next_due_date,start_date)) ASC, id DESC")
    edit_expense = q("SELECT * FROM expenses WHERE id=?", (edit_id,), one=True) if edit_id else None
    return render_template('expenses.html', expenses=rows, expense_view=view, expense_categories=EXPENSE_CATEGORY_OPTIONS,
                           recurring_options=recurring_frequency_options(), totals=totals, supplier_summary=supplier_summary,
                           category_summary=category_summary, recurring_rows=recurring_rows, edit_expense=edit_expense)


@app.route('/expenses/<int:expense_id>/edit', methods=['POST'])
@login_required
def expense_edit(expense_id):
    expense = q("SELECT * FROM expenses WHERE id=?", (expense_id,), one=True)
    if not expense:
        flash('Expense not found.')
        return redirect(url_for('expenses'))
    expense_date = clean_str(request.form.get('expense_date')) or expense['expense_date'] or date.today().isoformat()
    category = clean_str(request.form.get('category')) or 'Other'
    supplier = clean_str(request.form.get('supplier'))
    description = clean_str(request.form.get('description'))
    notes = clean_str(request.form.get('notes'))
    amount = parse_money(request.form.get('amount'))
    vat_amount = parse_money(request.form.get('vat_amount'))
    if amount is None or amount < 0:
        flash('Please enter a valid expense amount.')
        return redirect(url_for('expenses', edit=expense_id))
    if vat_amount is None or vat_amount < 0:
        vat_amount = 0.0
    run("UPDATE expenses SET expense_date=?, category=?, supplier=?, description=?, amount=?, vat_amount=?, notes=? WHERE id=?",
        (expense_date, category, supplier, description, amount, vat_amount, notes, expense_id))
    flash('Expense updated.')
    return redirect(url_for('expenses'))


@app.route('/expenses/recurring/new', methods=['POST'])
@login_required
def recurring_expense_new():
    start_date = clean_str(request.form.get('start_date')) or date.today().isoformat()
    frequency = clean_str(request.form.get('frequency'))
    category = clean_str(request.form.get('category')) or 'Other'
    supplier = clean_str(request.form.get('supplier'))
    description = clean_str(request.form.get('description'))
    notes = clean_str(request.form.get('notes'))
    amount = parse_money(request.form.get('amount'))
    vat_amount = parse_money(request.form.get('vat_amount'))
    if amount is None or amount < 0:
        flash('Please enter a valid recurring expense amount.')
        return redirect(url_for('expenses'))
    if vat_amount is None or vat_amount < 0:
        vat_amount = 0.0
    start_obj = parse_iso_date(start_date) or date.today()
    next_due = next_due_date_for_frequency(start_obj, frequency) or start_obj
    run("INSERT INTO recurring_expenses (start_date, next_due_date, category, supplier, description, amount, vat_amount, notes, frequency, active) VALUES (?,?,?,?,?,?,?,?,?,1)",
        (start_obj.isoformat(), next_due.isoformat(), category, supplier, description, amount, vat_amount, notes, frequency))
    flash('Recurring expense saved.')
    return redirect(url_for('expenses'))


@app.route('/expenses/recurring/run', methods=['POST'])
@login_required
def recurring_expenses_run():
    today = date.today()
    rows = q("SELECT * FROM recurring_expenses WHERE archived_at IS NULL AND active=1 AND date(IFNULL(next_due_date,start_date)) <= date('now') ORDER BY date(IFNULL(next_due_date,start_date)) ASC, id ASC")
    created = 0
    for row in rows:
        due_date = parse_iso_date(row['next_due_date']) or parse_iso_date(row['start_date']) or today
        note_prefix = f"Auto posted from recurring expense #{row['id']} on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        notes = clean_str(row['notes'])
        full_notes = note_prefix if not notes else note_prefix + "\n\n" + notes
        run("INSERT INTO expenses (expense_date, category, supplier, description, amount, vat_amount, notes) VALUES (?,?,?,?,?,?,?)",
            (due_date.isoformat(), row['category'], row['supplier'], row['description'], row['amount'], row['vat_amount'], full_notes))
        next_due = next_due_date_for_frequency(due_date, row['frequency']) or due_date
        run("UPDATE recurring_expenses SET last_posted_at=CURRENT_TIMESTAMP, next_due_date=? WHERE id=?", (next_due.isoformat(), row['id']))
        created += 1
    flash(f'{created} recurring expense entries posted.' if created else 'No recurring expenses were due yet.')
    return redirect(url_for('expenses'))


@app.route('/recurring-expenses/<int:recurring_id>/archive', methods=['POST'])
@login_required
def recurring_expense_archive(recurring_id):
    run("UPDATE recurring_expenses SET archived_at=CURRENT_TIMESTAMP, active=0 WHERE id=?", (recurring_id,))
    flash('Recurring expense archived.')
    return redirect(url_for('expenses'))


@app.route('/recurring-expenses/<int:recurring_id>/restore', methods=['POST'])
@login_required
def recurring_expense_restore(recurring_id):
    run("UPDATE recurring_expenses SET archived_at=NULL, active=1 WHERE id=?", (recurring_id,))
    flash('Recurring expense restored.')
    return redirect(url_for('expenses'))


@app.route('/recurring-expenses/<int:recurring_id>/post-now', methods=['POST'])
@login_required
def recurring_expense_post_now(recurring_id):
    row = q("SELECT * FROM recurring_expenses WHERE id=?", (recurring_id,), one=True)
    if not row or row['archived_at']:
        flash('Recurring expense not found.')
        return redirect(url_for('expenses'))
    due_date = date.today()
    note_prefix = f"Manually posted from recurring expense #{row['id']} on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    notes = clean_str(row['notes'])
    full_notes = note_prefix if not notes else note_prefix + "\n\n" + notes
    run("INSERT INTO expenses (expense_date, category, supplier, description, amount, vat_amount, notes) VALUES (?,?,?,?,?,?,?)",
        (due_date.isoformat(), row['category'], row['supplier'], row['description'], row['amount'], row['vat_amount'], full_notes))
    next_due = next_due_date_for_frequency(due_date, row['frequency']) or due_date
    run("UPDATE recurring_expenses SET last_posted_at=CURRENT_TIMESTAMP, next_due_date=? WHERE id=?", (next_due.isoformat(), recurring_id))
    flash('Recurring expense posted now.')
    return redirect(url_for('expenses'))


@app.route('/expenses/<int:expense_id>/archive', methods=['POST'])
@login_required
def expense_archive(expense_id):
    run("UPDATE expenses SET archived_at=CURRENT_TIMESTAMP WHERE id=? AND archived_at IS NULL", (expense_id,))
    flash('Expense archived.')
    return redirect(url_for('expenses', view=request.args.get('view') or 'active'))


@app.route('/expenses/<int:expense_id>/restore', methods=['POST'])
@login_required
def expense_restore(expense_id):
    run("UPDATE expenses SET archived_at=NULL WHERE id=?", (expense_id,))
    flash('Expense restored.')
    return redirect(url_for('expenses', view=request.args.get('view') or 'archived'))




@app.route("/calendar")
@login_required
def full_calendar():
    view_mode = clean_str(request.args.get('view') or 'month').lower()
    if view_mode not in {'month', 'week', 'day'}:
        view_mode = 'month'
    year = request.args.get('year') or ''
    month = request.args.get('month') or ''
    day_text = request.args.get('day') or ''
    context = build_calendar_context(view_mode=view_mode, year=year or None, month=month or None, day_text=day_text)
    return render_template('calendar.html', **context)

@app.route("/scheduler")
@login_required
def scheduler():
    today = date.today()
    days = []
    for i in range(7):
        d = today + timedelta(days=i)
        jobs = q("""SELECT jobs.*, customers.first_name || ' ' || customers.last_name AS customer_name
                    FROM jobs LEFT JOIN customers ON customers.id = jobs.customer_id
                    WHERE jobs.job_date=? ORDER BY jobs.id DESC""", (d.isoformat(),))
        days.append({"date": d.isoformat(), "label": d.strftime("%a %d %b"), "jobs": jobs})
    unscheduled = q("""SELECT jobs.*, customers.first_name || ' ' || customers.last_name AS customer_name
                       FROM jobs LEFT JOIN customers ON customers.id = jobs.customer_id
                       WHERE IFNULL(jobs.status,'') <> 'Archived' AND (job_date IS NULL OR job_date='') ORDER BY jobs.id DESC""")
    return render_template("scheduler.html", days=days, unscheduled=unscheduled)

@app.route("/jobs/<int:job_id>/move_date", methods=["POST"])
@login_required
def move_job_date(job_id):
    run("UPDATE jobs SET job_date=? WHERE id=?", (request.form.get("job_date"), job_id))
    return ("", 204)




def month_calendar_matrix(year, month):
    cal = pycalendar.Calendar(firstweekday=0)
    return cal.monthdatescalendar(year, month)


def calendar_job_rows(start_date, end_date):
    return q("""SELECT jobs.*, customers.first_name || ' ' || customers.last_name AS customer_name
                FROM jobs
                LEFT JOIN customers ON customers.id = jobs.customer_id
                WHERE jobs.job_date IS NOT NULL AND jobs.job_date<>''
                  AND date(jobs.job_date) BETWEEN date(?) AND date(?)
                  AND IFNULL(jobs.status,'') <> 'Archived'
                ORDER BY date(jobs.job_date), jobs.id DESC""", (start_date, end_date))


def calendar_invoice_rows(start_date, end_date):
    return q("""SELECT invoices.*, customers.first_name || ' ' || customers.last_name AS customer_name
                FROM invoices
                LEFT JOIN customers ON customers.id = invoices.customer_id
                WHERE invoices.due_date IS NOT NULL AND invoices.due_date<>''
                  AND date(invoices.due_date) BETWEEN date(?) AND date(?)
                  AND lower(IFNULL(invoices.status,'')) NOT IN ('paid','archived')
                ORDER BY date(invoices.due_date), invoices.id DESC""", (start_date, end_date))


def build_calendar_context(view_mode='month', year=None, month=None, day_text=''):
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)
    selected_day = None
    if day_text:
        try:
            selected_day = datetime.strptime(day_text, '%Y-%m-%d').date()
            year = selected_day.year
            month = selected_day.month
        except Exception:
            selected_day = today
    else:
        selected_day = today

    month_matrix = month_calendar_matrix(year, month)
    month_start = month_matrix[0][0]
    month_end = month_matrix[-1][-1]

    jobs = calendar_job_rows(month_start.isoformat(), month_end.isoformat())
    invoices = calendar_invoice_rows(month_start.isoformat(), month_end.isoformat())

    jobs_by_day = {}
    for row in jobs:
        jobs_by_day.setdefault(clean_str(row['job_date']), []).append(row)
    invoices_by_day = {}
    for row in invoices:
        invoices_by_day.setdefault(clean_str(row['due_date']), []).append(row)

    weeks = []
    for week in month_matrix:
        days = []
        for d in week:
            key = d.isoformat()
            day_jobs = jobs_by_day.get(key, [])
            day_invoices = invoices_by_day.get(key, [])
            days.append({
                'date': key,
                'day_num': d.day,
                'is_current_month': d.month == month,
                'is_today': d == today,
                'jobs': day_jobs[:4],
                'jobs_more': max(0, len(day_jobs) - 4),
                'invoice_alerts': day_invoices[:2],
                'invoice_more': max(0, len(day_invoices) - 2),
            })
        weeks.append(days)

    first_of_month = date(year, month, 1)
    prev_month = (first_of_month - timedelta(days=1)).replace(day=1)
    next_month = (first_of_month + timedelta(days=32)).replace(day=1)

    week_start = selected_day - timedelta(days=selected_day.weekday())
    week_end = week_start + timedelta(days=6)
    week_days = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        key = d.isoformat()
        week_days.append({
            'date': key,
            'label': d.strftime('%a %d %b'),
            'is_today': d == today,
            'jobs': jobs_by_day.get(key, []),
            'invoice_alerts': invoices_by_day.get(key, []),
        })

    day_key = selected_day.isoformat()
    day_view = {
        'date': day_key,
        'label': selected_day.strftime('%A %d %B %Y'),
        'jobs': jobs_by_day.get(day_key, []),
        'invoice_alerts': invoices_by_day.get(day_key, []),
    }

    upcoming_jobs = q("""SELECT jobs.*, customers.first_name || ' ' || customers.last_name AS customer_name
                         FROM jobs
                         LEFT JOIN customers ON customers.id = jobs.customer_id
                         WHERE jobs.job_date IS NOT NULL AND jobs.job_date<>''
                           AND date(jobs.job_date) >= date(?)
                           AND IFNULL(jobs.status,'') <> 'Archived'
                         ORDER BY date(jobs.job_date), jobs.id
                         LIMIT 12""", (today.isoformat(),))

    return {
        'view_mode': view_mode or 'month',
        'month_name': first_of_month.strftime('%B %Y'),
        'year': year,
        'month': month,
        'today_iso': today.isoformat(),
        'weeks': weeks,
        'week_days': week_days,
        'day_view': day_view,
        'selected_day': selected_day.isoformat(),
        'prev_year': prev_month.year,
        'prev_month': prev_month.month,
        'next_year': next_month.year,
        'next_month': next_month.month,
        'upcoming_jobs': upcoming_jobs,
        'month_job_count': len(jobs),
        'month_invoice_alert_count': len(invoices),
    }

def safe_replace(text, replacements):
    result = str(text or "")
    for key, value in replacements.items():
        token = str(key or "")
        if token and token in result:
            result = result.replace(token, str(value or ""))
    return result

def comms_replacements(customer=None):
    s = settings()
    name = (customer["name"] if customer else "") if customer else ""
    first_name = name.split(" ")[0] if name else ""
    email = (customer["email"] if customer else "") if customer else ""
    phone = (customer["phone"] if customer else "") if customer else ""
    return {
        "{{name}}": name,
        "{{first_name}}": first_name,
        "{{business_name}}": s["business_name"] or "",
        "{{phone}}": s["phone"] or "",
        "{{review_link}}": s["review_link"] or "",
        "{{website}}": s["website"] or "",
        "{{email}}": email,
        "{{customer_email}}": email,
        "{{customer_phone}}": phone,
        "[[name]]": name,
        "[[first_name]]": first_name,
        "[[business_name]]": s["business_name"] or "",
        "[[phone]]": s["phone"] or "",
        "[[review_link]]": s["review_link"] or "",
        "[[website]]": s["website"] or "",
        "[[email]]": email,
        "[[customer_email]]": email,
        "[[customer_phone]]": phone,
    }



@app.route('/campaigns')
@login_required
def campaign_history():
    channel = (request.args.get('channel') or '').strip()
    status = (request.args.get('status') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

    where = []
    params = []
    if channel:
        where.append('b.channel = ?')
        params.append(channel)
    if status:
        where.append('b.status = ?')
        params.append(status)
    if date_from:
        where.append("date(b.created_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(b.created_at) <= date(?)")
        params.append(date_to)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    batches = q(f"""
        SELECT b.*,
               COALESCE((SELECT COUNT(*) FROM campaign_batch_items i WHERE i.campaign_batch_id=b.id),0) AS item_count,
               COALESCE((SELECT COUNT(*) FROM campaign_batch_items i WHERE i.campaign_batch_id=b.id AND i.item_status='Sent'),0) AS sent_items,
               COALESCE((SELECT COUNT(*) FROM campaign_batch_items i WHERE i.campaign_batch_id=b.id AND i.item_status='Prepared'),0) AS prepared_items,
               COALESCE((SELECT COUNT(*) FROM campaign_batch_items i WHERE i.campaign_batch_id=b.id AND i.item_status='Failed'),0) AS failed_items
        FROM campaign_batches b
        {where_sql}
        ORDER BY b.id DESC
        LIMIT 200
    """, tuple(params))

    item_where = []
    item_params = []
    if channel:
        item_where.append('b.channel = ?')
        item_params.append(channel)
    if status:
        item_where.append('b.status = ?')
        item_params.append(status)
    if date_from:
        item_where.append("date(i.created_at) >= date(?)")
        item_params.append(date_from)
    if date_to:
        item_where.append("date(i.created_at) <= date(?)")
        item_params.append(date_to)
    item_where_sql = ('WHERE ' + ' AND '.join(item_where)) if item_where else ''

    item_rows = q(f"""
        SELECT i.*, b.channel, b.segment, b.title, c.first_name || ' ' || c.last_name AS customer_name, c.town, c.phone AS customer_phone, c.email
        FROM campaign_batch_items i
        LEFT JOIN campaign_batches b ON b.id = i.campaign_batch_id
        LEFT JOIN customers c ON c.id = i.customer_id
        {item_where_sql}
        ORDER BY i.id DESC
        LIMIT 500
    """, tuple(item_params))

    enriched = evaluate_follow_up_results(item_rows)
    summary = follow_up_dashboard_summary(enriched, days=60)
    return render_template('campaigns.html', batches=batches, item_rows=enriched, follow_up_summary=summary, filters={'channel': channel, 'status': status, 'date_from': date_from, 'date_to': date_to})




@app.route('/sms-history')
@login_required
def sms_history():
    direction = clean_str(request.args.get('direction') or '')
    status = clean_str(request.args.get('status') or '')
    provider = clean_str(request.args.get('provider') or '')
    where=[]
    params=[]
    if direction:
        where.append('e.direction=?')
        params.append(direction)
    if status:
        where.append('e.status=?')
        params.append(status)
    if provider:
        where.append('e.provider=?')
        params.append(provider)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    rows = q(f"""SELECT e.*, c.first_name || ' ' || c.last_name AS customer_name, c.phone AS customer_phone
                 FROM sms_events e
                 LEFT JOIN customers c ON c.id = e.customer_id
                 {where_sql}
                 ORDER BY e.id DESC
                 LIMIT 500""", tuple(params))
    summary = q(f"""SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN lower(IFNULL(direction,''))='outbound' THEN 1 ELSE 0 END) AS outbound_count,
                    SUM(CASE WHEN lower(IFNULL(direction,''))='inbound' THEN 1 ELSE 0 END) AS inbound_count,
                    SUM(CASE WHEN lower(IFNULL(status,'')) IN ('delivered','sent','queued','accepted') THEN 1 ELSE 0 END) AS ok_count,
                    SUM(CASE WHEN lower(IFNULL(status,'')) IN ('failed','undelivered') OR IFNULL(error_text,'')<>'' THEN 1 ELSE 0 END) AS failed_count
                 FROM sms_events e
                 {where_sql}""", tuple(params), one=True)
    return render_template('sms_history.html', rows=rows, filters={'direction': direction, 'status': status, 'provider': provider}, summary=summary)


@app.route('/sms-history/<int:event_id>/retry', methods=['POST'])
@login_required
def sms_history_retry(event_id):
    row = q("SELECT * FROM sms_events WHERE id=?", (event_id,), one=True)
    if not row:
        flash('SMS event not found.')
        return redirect(url_for('sms_history'))
    if (row['direction'] or '').lower() != 'outbound':
        flash('Only outbound SMS items can be retried.')
        return redirect(url_for('sms_history'))
    customer = q('SELECT * FROM customers WHERE id=?', (row['customer_id'],), one=True) if row['customer_id'] else None
    ok, msg = send_sms_gateway(row['to_phone'] or '', row['body'] or '', customer=customer, communication_id=row['communication_id'])
    flash(msg)
    return redirect(url_for('sms_history'))


@app.route('/customers/<int:customer_id>/sms/<int:event_id>/retry', methods=['POST'])
@login_required
def customer_sms_retry(customer_id, event_id):
    row = q("SELECT * FROM sms_events WHERE id=? AND customer_id=?", (event_id, customer_id), one=True)
    if not row:
        flash('SMS event not found for this customer.')
        return redirect(url_for('customer_view', customer_id=customer_id))
    if (row['direction'] or '').lower() != 'outbound':
        flash('Only outbound SMS items can be retried.')
        return redirect(url_for('customer_view', customer_id=customer_id))
    customer = q('SELECT * FROM customers WHERE id=?', (customer_id,), one=True)
    ok, msg = send_sms_gateway(row['to_phone'] or '', row['body'] or '', customer=customer, communication_id=row['communication_id'])
    flash(msg)
    return redirect(url_for('customer_view', customer_id=customer_id))

@app.route("/communications")
@login_required
def communications():
    rows = q("""SELECT communications.*, customers.first_name || ' ' || customers.last_name AS customer_name
                FROM communications LEFT JOIN customers ON customers.id = communications.customer_id
                ORDER BY communications.id DESC""")
    customers = q("SELECT id, first_name || ' ' || last_name AS name, phone, email FROM customers WHERE archived_at IS NULL ORDER BY first_name, last_name")
    templates = q("SELECT * FROM communication_templates ORDER BY id DESC")
    prefill = {
        'customer_id': clean_str(request.args.get('customer_id')),
        'channel': clean_str(request.args.get('channel') or 'Email') or 'Email',
        'subject': request.args.get('subject') or '',
        'body': request.args.get('body') or '',
    }
    return render_template("communications.html", rows=rows, customers=customers, templates=templates, app_settings=settings(), prefill=prefill)

@app.route("/communications/new", methods=["POST"])
@login_required
def communications_new():
    customer_id = request.form.get("customer_id") or None
    channel = (request.form.get("channel") or "Email").strip()
    subject = request.form.get("subject") or ""
    body = request.form.get("body") or ""
    db().execute(
        "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
        (customer_id if customer_id else None, channel, subject, body)
    )
    db().commit()
    flash("Communication saved.")
    return redirect(url_for("communications"))


@app.route("/communications/templates/new", methods=["POST"])
@login_required
def communications_template_new():
    run("INSERT INTO communication_templates(name, channel, subject, body) VALUES (?,?,?,?)", (
        request.form.get("name"),
        request.form.get("channel"),
        request.form.get("subject"),
        request.form.get("body"),
    ))
    flash("Template saved.")
    return redirect(url_for("communications"))

@app.route("/communications/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def communications_template_delete(template_id):
    run("DELETE FROM communication_templates WHERE id=?", (template_id,))
    flash("Template deleted.")
    return redirect(url_for("communications"))

@app.route("/communications/send-test", methods=["POST"])
@login_required
def communications_send_test():
    test_email = (request.form.get("test_email") or "").strip()
    subject = request.form.get("subject") or ""
    body = request.form.get("body") or ""
    rendered_subject = safe_replace(subject, comms_replacements(None))
    rendered_body = safe_replace(body, comms_replacements(None))
    ok, msg = send_email_smtp(test_email, rendered_subject, rendered_body)
    flash(msg)
    return redirect(url_for("communications"))

@app.route("/communications/send-test-sms", methods=["POST"])
@login_required
def communications_send_test_sms():
    test_phone = normalize_phone(request.form.get("test_phone") or settings()["sms_test_number"] or "")
    body = request.form.get("body") or ""
    rendered_body = safe_replace(body, comms_replacements(None))
    cur = db().execute(
        "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
        (None, 'SMS', 'Test SMS', rendered_body)
    )
    communication_id = cur.lastrowid
    db().commit()
    ok, msg = send_sms_gateway(test_phone, rendered_body, communication_id=communication_id, message_category=request.form.get('message_category') or '')
    flash(msg)
    if not ok:
        db().execute("DELETE FROM communications WHERE id=?", (communication_id,))
        db().commit()
    return redirect(url_for("communications"))

@app.route("/communications/send-customer", methods=["POST"])
@login_required
def communications_send_customer():
    customer_id = int(request.form.get("customer_id") or 0)
    channel = (request.form.get("channel") or "Email").strip()
    subject = request.form.get("subject") or ""
    body = request.form.get("body") or ""
    customer = db().execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        flash("Customer not found.")
        return redirect(url_for("communications"))

    rendered_subject = safe_replace(subject, comms_replacements(customer))
    rendered_body = safe_replace(body, comms_replacements(customer))

    if channel == "Email":
        ok, msg = send_email_smtp(customer["email"] or "", rendered_subject, rendered_body, customer=customer)
        flash(msg)
    elif channel == "SMS":
        ok, msg = send_sms_gateway(customer["phone"] or "", rendered_body, customer=customer, message_category=request.form.get('message_category') or '')
        flash(msg)
    else:
        ok = True
        flash(f"{channel} quick send prepared.")

    if ok:
        db().execute(
            "INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))",
            (customer_id, channel, rendered_subject, rendered_body)
        )
        db().commit()
    return redirect(url_for("communications"))



@app.route("/webhooks/sms/status/twilio", methods=["POST"])
def sms_status_twilio():
    external_id = request.form.get("MessageSid") or request.form.get("SmsSid") or ""
    status = request.form.get("MessageStatus") or request.form.get("SmsStatus") or "Updated"
    payload = request.form.to_dict(flat=True)
    update_sms_status_by_external(external_id, status=status, payload=payload, error_text=payload.get('ErrorMessage') or '')
    return ("ok", 200)

@app.route("/webhooks/sms/inbound/twilio", methods=["POST"])
def sms_inbound_twilio():
    payload = request.form.to_dict(flat=True)
    from_phone = normalize_phone(payload.get("From") or "")
    to_phone = normalize_phone(payload.get("To") or "")
    body = payload.get("Body") or ""
    external_id = payload.get("MessageSid") or payload.get("SmsSid") or f"twilio-in-{uuid.uuid4().hex[:12]}"
    customer = q("SELECT * FROM customers WHERE replace(replace(replace(ifnull(phone,''),' ',''),'-',''),'+','') LIKE ? ORDER BY id DESC LIMIT 1", (f"%{from_phone.replace('+','')}%",), one=True) if from_phone else None
    customer_id = customer['id'] if customer else None
    log_sms_event(customer_id, None, 'Twilio', 'inbound', to_phone, from_phone, body, external_id, 'Received', 'inbound', payload)
    action = inbound_sms_keyword_action(body)
    if customer_id and action == 'stop':
        set_customer_sms_opt_out(customer_id, True, source='Inbound SMS')
    elif customer_id and action == 'start':
        set_customer_sms_opt_out(customer_id, False, source='Inbound SMS')
    db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, 'SMS', 'Inbound SMS', body))
    db().commit()
    return ("ok", 200)

@app.route("/webhooks/sms/status/clicksend", methods=["POST"])
def sms_status_clicksend():
    payload = request.get_json(silent=True) or request.form.to_dict(flat=True) or {}
    data = payload.get('data') if isinstance(payload, dict) else None
    if isinstance(data, list) and data:
        item = data[0] or {}
    elif isinstance(data, dict):
        item = data
    else:
        item = payload if isinstance(payload, dict) else {}
    external_id = str(item.get('message_id') or item.get('messageid') or item.get('id') or '')
    status = str(item.get('status') or item.get('status_text') or item.get('message_status') or 'Updated')
    update_sms_status_by_external(external_id, status=status, payload=payload, error_text=str(item.get('error') or item.get('error_text') or ''))
    return ("ok", 200)

@app.route("/webhooks/sms/inbound/clicksend", methods=["POST"])
def sms_inbound_clicksend():
    payload = request.get_json(silent=True) or request.form.to_dict(flat=True) or {}
    data = payload.get('data') if isinstance(payload, dict) else None
    if isinstance(data, list) and data:
        item = data[0] or {}
    elif isinstance(data, dict):
        item = data
    else:
        item = payload if isinstance(payload, dict) else {}
    from_phone = normalize_phone(item.get('from') or item.get('source') or '')
    to_phone = normalize_phone(item.get('to') or '')
    body = item.get('body') or item.get('message') or ''
    external_id = str(item.get('message_id') or item.get('id') or f"clicksend-in-{uuid.uuid4().hex[:12]}")
    customer = q("SELECT * FROM customers WHERE replace(replace(replace(ifnull(phone,''),' ',''),'-',''),'+','') LIKE ? ORDER BY id DESC LIMIT 1", (f"%{from_phone.replace('+','')}%",), one=True) if from_phone else None
    customer_id = customer['id'] if customer else None
    log_sms_event(customer_id, None, 'ClickSend', 'inbound', to_phone, from_phone, body, external_id, 'Received', 'inbound', payload)
    action = inbound_sms_keyword_action(body)
    if customer_id and action == 'stop':
        set_customer_sms_opt_out(customer_id, True, source='Inbound SMS')
    elif customer_id and action == 'start':
        set_customer_sms_opt_out(customer_id, False, source='Inbound SMS')
    db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (customer_id, 'SMS', 'Inbound SMS', body))
    db().commit()
    return ("ok", 200)

@app.route("/email-designer")
@login_required
def email_designer():
    customers = q("SELECT id, first_name || ' ' || last_name AS name, phone, email FROM customers WHERE archived_at IS NULL ORDER BY first_name, last_name")
    templates = q("SELECT * FROM communication_templates ORDER BY id DESC")
    return render_template("email_designer.html", customers=customers, templates=templates, app_settings=settings())

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    s = settings()
    if request.method == "POST":
        logo_filename = save_upload("logo_file") or s["logo_filename"]
        dashboard_carpet_image = save_upload("dashboard_carpet_file") or s["dashboard_carpet_image"]
        dashboard_upholstery_image = save_upload("dashboard_upholstery_file") or s["dashboard_upholstery_image"]
        bg_darkness = s["bg_darkness"] or 58
        bg_palette = s["bg_palette"] or "custom"
        bg_color = s["bg_color"] or "#c7d7ea"
        sidebar_color = s["sidebar_color"] or "#102744"
        new_username = (request.form.get("username") or s["username"] or "admin").strip()
        new_password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if new_password and new_password != confirm_password:
            flash("New password and confirm password did not match.")
            return redirect(url_for("settings_page"))
        final_password = normalize_password_for_storage(new_password) if new_password else normalize_password_for_storage(s["password"])
        run("""UPDATE settings SET business_name=?, phone=?, email=?, website=?, address=?, accent=?, review_link=?, username=?, password=?, minimum_charge=?, vat_rate=?, logo_filename=?, dashboard_carpet_image=?, dashboard_upholstery_image=?, email_footer_html=?, sms_footer_text=?, bg_darkness=?, bg_palette=?, bg_color=?, sidebar_color=?, gmail_address=?, gmail_app_password=?, smtp_from_name=?, test_email=?, sms_gateway_name=?, sms_sender_id=?, sms_api_key=?, sms_gateway_url=?, sms_test_number=?, sms_account_id=?, sms_api_secret=?, sms_opt_out_message=?, sms_stop_keywords=?, sms_start_keywords=?, sms_marketing_opt_out_notice=?, sms_append_opt_out_on_marketing=? WHERE id=1""", (
            request.form.get("business_name"), request.form.get("phone"), request.form.get("email"),
            request.form.get("website"), request.form.get("address"), request.form.get("accent"),
            request.form.get("review_link"), new_username, final_password,
            request.form.get("minimum_charge") or 100, request.form.get("vat_rate") or 0.20,
            logo_filename, dashboard_carpet_image, dashboard_upholstery_image,
            request.form.get("email_footer_html"), request.form.get("sms_footer_text"), bg_darkness, bg_palette, bg_color, sidebar_color,
            request.form.get("gmail_address"), request.form.get("gmail_app_password") or s["gmail_app_password"], request.form.get("smtp_from_name"), request.form.get("test_email"),
            request.form.get("sms_gateway_name"), request.form.get("sms_sender_id"), request.form.get("sms_api_key") or s["sms_api_key"], request.form.get("sms_gateway_url"), request.form.get("sms_test_number"), request.form.get("sms_account_id"), request.form.get("sms_api_secret") or s["sms_api_secret"], request.form.get("sms_opt_out_message") or s["sms_opt_out_message"],
            request.form.get("sms_stop_keywords") or s["sms_stop_keywords"], request.form.get("sms_start_keywords") or s["sms_start_keywords"], request.form.get("sms_marketing_opt_out_notice") or s["sms_marketing_opt_out_notice"], 1 if request.form.get("sms_append_opt_out_on_marketing") else 0
        ))
        flash("Settings saved.")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", app_settings=s)

@app.route("/quotes/<int:quote_id>/print")
@login_required
def quote_print(quote_id):
    quote = q("""SELECT quotes.*, customers.* FROM quotes
                 LEFT JOIN customers ON customers.id = quotes.customer_id
                 WHERE quotes.id=?""", (quote_id,), one=True)
    payload = json.loads(quote["payload_json"] or "{}") if quote["payload_json"] else {}
    calc = calc_from_payload(payload)
    return render_template("document_print.html", mode="quote", row=quote, calc=calc)

@app.route("/invoices/reminders")
@login_required
def invoice_reminders():
    alerts = invoice_alert_rows(limit=100)
    return render_template("invoice_reminders.html", alerts=alerts)


@app.route("/invoices/<int:invoice_id>/send_reminder", methods=["POST"])
@login_required
def send_invoice_reminder(invoice_id):
    invoice = q("""SELECT invoices.*, customers.first_name || ' ' || customers.last_name AS customer_name, customers.email AS customer_email
                   FROM invoices LEFT JOIN customers ON customers.id = invoices.customer_id
                   WHERE invoices.id=?""", (invoice_id,), one=True)
    if not invoice:
        flash("Invoice not found.")
        return redirect(url_for("invoices"))
    if clean_str(invoice['status']).lower() in {'paid', 'archived'}:
        flash("That invoice does not need a reminder.")
        return redirect(url_for("invoice_view", invoice_id=invoice_id))
    subject = request.form.get('subject') or build_invoice_reminder_subject(dict(invoice))
    body = request.form.get('body') or build_invoice_reminder_body(dict(invoice))
    customer_stub = {'first_name': clean_str((invoice['customer_name'] or '').split(' ')[0]), 'last_name': ' '.join(clean_str(invoice['customer_name']).split(' ')[1:]), 'email': invoice['customer_email'] or '', 'phone': ''}
    ok, msg = send_email_smtp(invoice['customer_email'] or '', subject, body, customer=customer_stub)
    if ok:
        current_status = clean_str(invoice['status']) or 'Sent'
        due = parse_iso_date(invoice['due_date'])
        if due and due < date.today() and current_status.lower() != 'paid':
            current_status = 'Overdue'
        notes = append_note(invoice['notes'], f"Reminder sent on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        run("UPDATE invoices SET status=?, notes=? WHERE id=?", (current_status, notes, invoice_id))
        db().execute("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", (invoice['customer_id'], 'Email', subject, body))
        db().commit()
    flash(msg)
    return redirect(request.form.get('next_url') or url_for("invoice_view", invoice_id=invoice_id))


@app.route("/invoices/<int:invoice_id>/print")
@login_required
def invoice_print(invoice_id):
    invoice = q("""SELECT invoices.*, customers.* FROM invoices
                   LEFT JOIN customers ON customers.id = invoices.customer_id
                   WHERE invoices.id=?""", (invoice_id,), one=True)
    payload = json.loads(invoice["payload_json"] or "{}") if invoice["payload_json"] else {}
    calc = calc_from_payload(payload) if payload else {"lines": [], "subtotal": invoice["subtotal"], "vat": invoice["vat"], "total": invoice["total"], "raw_total": invoice["total"], "minimum": 100}
    return render_template("document_print.html", mode="invoice", row=invoice, calc=calc)



@app.route("/quote-portal")
def public_quote_portal():
    return render_template("quote_portal.html", pricing=pricing(), area_options=AREA_OPTIONS)

@app.route("/quote-portal/submit", methods=["POST"])
def public_quote_portal_submit():
    payload = json.loads(request.form.get("payload_json") or "{}")
    calc = calc_from_payload(payload)
    customer_name = clean_str(request.form.get("customer_name"))
    email = clean_str(request.form.get("email"))
    if not customer_name:
        flash("Please enter your name before sending the quote request.")
        return redirect(url_for("public_quote_portal"))
    if email and not is_valid_email(email):
        flash("Please enter a valid email address.")
        return redirect(url_for("public_quote_portal"))
    request_id = run("""INSERT INTO quote_requests(customer_name, phone, email, address, town, postcode, notes, status, payload_json, estimate_total)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", (
        customer_name,
        clean_str(request.form.get("phone")),
        email,
        clean_str(request.form.get("address")),
        clean_str(request.form.get("town")),
        clean_str(request.form.get("postcode")),
        clean_str(request.form.get("notes")),
        "New",
        json.dumps(payload),
        calc["total"],
    ))
    try:
        _lead_id, customer_id = create_intake_from_website_payload({
            "customer_name": customer_name,
            "phone": clean_str(request.form.get("phone")),
            "email": email,
            "address": clean_str(request.form.get("address")),
            "town": clean_str(request.form.get("town")),
            "postcode": clean_str(request.form.get("postcode")),
            "what_cleaned": ", ".join([line.get("item_name", "") for line in calc.get("lines", []) if line.get("item_name")]),
            "notes": clean_str(request.form.get("notes")),
        }, source="Website quote form")
        run("UPDATE quote_requests SET status='Waiting for review' WHERE id=?", (request_id,))
        set_customer_workflow(customer_id, "waiting_for_review", "Website quote form completed. Review and approve for Xero.", "Website form completed")
    except Exception:
        logger.exception("Could not auto-create workflow customer from quote portal request %s", request_id)
    return render_template("quote_portal_thanks.html")


@app.route("/quote-requests")
@login_required
def quote_requests():
    rows = q("SELECT * FROM quote_requests ORDER BY id DESC")
    return render_template("quote_requests.html", rows=rows)

@app.route("/quote-requests/<int:request_id>")
@login_required
def quote_request_view(request_id):
    row = q("SELECT * FROM quote_requests WHERE id=?", (request_id,), one=True)
    payload = json.loads(row["payload_json"] or "{}") if row and row["payload_json"] else {}
    calc = calc_from_payload(payload) if payload else {"lines": [], "subtotal": 0, "vat": 0, "total": row["estimate_total"] if row else 0, "raw_total": row["estimate_total"] if row else 0, "minimum": 100}
    return render_template("quote_request_view.html", row=row, calc=calc)

@app.route("/quote-requests/<int:request_id>/approve", methods=["POST"])
@login_required
def quote_request_approve(request_id):
    row = q("SELECT * FROM quote_requests WHERE id=?", (request_id,), one=True)
    if not row:
        flash("Request not found.")
        return redirect(url_for("quote_requests"))
    if (row["status"] or "").strip().lower() == "approved":
        flash("That request has already been approved, so it was not added again.")
        return redirect(url_for("quote_requests"))
    name = (row["customer_name"] or "").strip()
    first_name = name.split(" ")[0] if name else "New"
    last_name = " ".join(name.split(" ")[1:]) if len(name.split(" ")) > 1 else "Customer"
    existing_customer_id = find_existing_customer_id(first_name=first_name, last_name=last_name, email=row["email"], phone=row["phone"], postcode=row["postcode"])
    customer_id = existing_customer_id
    if not customer_id:
        customer_id = run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,tags,notes)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            first_name, last_name, row["phone"], row["email"], row["address"], row["town"], row["postcode"],
            "Quote Portal", "portal", row["notes"]
        ))
        set_customer_workflow(customer_id, "customer_approved", "Website quote request approved.", "Customer approved")
    else:
        set_customer_workflow(customer_id, "customer_approved", "Website quote request approved and linked to existing customer.", "Customer approved")
    payload = json.loads(row["payload_json"] or "{}")
    calc = calc_from_payload(payload)
    quote_id = run("""INSERT INTO quotes(customer_id, quote_number, title, quote_date, valid_until, status, subtotal, vat, total, payload_json, notes)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
        customer_id, next_quote_number(), "Portal Quote Request", date.today().isoformat(), "", "Draft",
        calc["subtotal"], calc["vat"], calc["total"], json.dumps(payload), row["notes"] or ""
    ))
    for line in calc["lines"]:
        run("""INSERT INTO quote_lines(quote_id, item_name, method, quantity, unit_price, line_total, group_name)
               VALUES (?,?,?,?,?,?,?)""", (
            quote_id, line["item_name"], line["method"], line["quantity"], line["unit_price"], line["line_total"], line["group_name"]
        ))
    run("UPDATE quote_requests SET status='Approved' WHERE id=?", (request_id,))
    flash("Request approved and added into customers and quotes." if not existing_customer_id else "Request approved and linked to the existing customer.")
    return redirect(url_for("quote_view", quote_id=quote_id))


@app.route("/quote-requests/<int:request_id>/archive", methods=["POST"])
@login_required
def quote_request_archive(request_id):
    run("UPDATE quote_requests SET status='Archived' WHERE id=?", (request_id,))
    flash("Request archived.")
    return redirect(url_for("quote_requests"))

@app.route("/calculator-manager", methods=["GET", "POST"])
@login_required
def calculator_manager():
    data = pricing()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_domestic":
            data["domestic"].append({
                "id": request.form.get("item_id") or f"item_{len(data['domestic'])+1}",
                "name": request.form.get("name") or "New Item",
                "desc": request.form.get("desc") or "",
                "price": float(request.form.get("price") or 0),
                "group": request.form.get("group") or "Residential",
            })
        elif action == "update_domestic":
            for i, item in enumerate(data["domestic"]):
                item["name"] = request.form.get(f"name_{i}") or item["name"]
                item["desc"] = request.form.get(f"desc_{i}") or item["desc"]
                item["price"] = float(request.form.get(f"price_{i}") or item["price"])
                item["group"] = request.form.get(f"group_{i}") or item["group"]
        elif action == "delete_domestic":
            idx = int(request.form.get("index"))
            if 0 <= idx < len(data["domestic"]):
                data["domestic"].pop(idx)
        elif action == "update_hotels":
            data["hotelRooms"]["rotary"] = float(request.form.get("hotel_rotary") or data["hotelRooms"]["rotary"])
            data["hotelRooms"]["hybrid"] = float(request.form.get("hotel_hybrid") or data["hotelRooms"]["hybrid"])
            data["hotelRooms"]["hwe"] = float(request.form.get("hotel_hwe") or data["hotelRooms"]["hwe"])
        save_pricing(data)
        flash("Calculator settings saved.")
        return redirect(url_for("calculator_manager"))
    return render_template("calculator_manager.html", pricing=data)


@app.route("/seed")
@login_required
def seed():
    existing = q("SELECT COUNT(*) AS c FROM customers", one=True)["c"]
    if existing > 0:
        flash("Demo data was not loaded because customers already exist in this CRM.")
        return redirect(url_for("dashboard"))

    today = date.today()
    customer_rows = [
        ("Sarah", "James", "07800111222", "sarah@example.com", "12 High Street", "Ludlow", "SY8 1AA", "Website", "repeat,residential", "Repeat domestic customer. Prefers morning appointments."),
        ("Tom", "Baker", "07700999111", "tom@example.com", "8 Church Lane", "Shrewsbury", "SY1 2BB", "Google", "commercial,office", "Office maintenance contact. Evening access only."),
        ("Emma", "Clarke", "07700999112", "emma@example.com", "44 Mill Lane", "Hereford", "HR1 2AB", "Facebook", "upholstery", "Asked about sofa and rug bundle pricing."),
        ("David", "Morgan", "07700999113", "david@example.com", "2 Castle View", "Leominster", "HR6 8DD", "Referral", "landlord,void", "Landlord with regular changeover work."),
        ("Chloe", "Evans", "07700999114", "chloe@example.com", "17 Brook Street", "Worcester", "WR1 3DE", "Website", "review-candidate", "Good candidate for review follow up after next clean."),
        ("James", "Turner", "07700999115", "james@example.com", "Unit 4 Riverside Park", "Telford", "TF1 4ZZ", "Google Ads", "commercial,recurring", "Monthly recurring office clean enquiry."),
    ]
    customer_ids = []
    for row in customer_rows:
        customer_ids.append(run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,tags,notes)
                                   VALUES (?,?,?,?,?,?,?,?,?,?)""", row))

    # Quotes
    quote_specs = [
        (customer_ids[0], "Lounge, stairs and landing", 165.0, "Sent", [
            ("Living Room", "Living Room", 1, 79.0, 79.0, "Residential"),
            ("Stairs and Landing", "Stairs and Landing", 1, 75.0, 75.0, "Residential"),
            ("Spot treatment", "Manual extra", 1, 11.0, 11.0, "Residential"),
        ], "Customer asked to book next Friday if accepted."),
        (customer_ids[2], "Three seat sofa and rug", 150.0, "Draft", [
            ("3 Seat Sofa", "3 Seat Sofa", 1, 120.0, 120.0, "Upholstery"),
            ("Medium Rug", "Medium Rug", 1, 30.0, 30.0, "Rugs"),
        ], "Pending fabric photos from customer."),
        (customer_ids[3], "Void clean package", 280.0, "Accepted", [
            ("Bedroom carpets", "Bedroom", 4, 35.0, 140.0, "Residential"),
            ("Hall stairs landing", "Stairs and Landing", 1, 75.0, 75.0, "Residential"),
            ("Odour treatment", "Manual extra", 1, 65.0, 65.0, "Residential"),
        ], "Approved by landlord."),
    ]
    accepted_quote_id = None
    for customer_id, title, total, status, lines, notes in quote_specs:
        quote_id = run("""INSERT INTO quotes(customer_id, quote_number, title, quote_date, valid_until, status, subtotal, vat, total, payload_json, notes)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            customer_id, next_quote_number(), title, today.isoformat(), (today + timedelta(days=14)).isoformat(), status, total, 0, total,
            json.dumps({"lines": [{"item_name": a, "method": b, "quantity": c, "unit_price": d, "line_total": e, "group_name": f} for a,b,c,d,e,f in lines], "include_vat": False}),
            notes
        ))
        for line in lines:
            run("""INSERT INTO quote_lines(quote_id,item_name,method,quantity,unit_price,line_total,group_name)
                   VALUES (?,?,?,?,?,?,?)""", (quote_id, *line))
        if status == 'Accepted':
            accepted_quote_id = quote_id

    # Jobs
    job_specs = [
        (customer_ids[1], None, "Office maintenance visit", "Commercial Carpet Cleaning", today.isoformat(), "Booked", 240.0, "Paul", "Evening access confirmed."),
        (customer_ids[3], accepted_quote_id, "Void property clean", "Deep Clean", (today + timedelta(days=2)).isoformat(), "Booked", 280.0, "Team", "Keys to be collected from office."),
        (customer_ids[4], None, "Lounge and rug refresh", "Residential", (today + timedelta(days=5)).isoformat(), "Completed", 145.0, "Paul", "Customer very happy with result."),
        (customer_ids[5], None, "Monthly office clean", "Commercial Maintenance", (today + timedelta(days=9)).isoformat(), "Booked", 320.0, "Team", "First recurring maintenance visit."),
    ]
    completed_job_id = None
    for customer_id, quote_id, title, service_type, job_date, status, amount, assigned_to, notes in job_specs:
        job_id = run("""INSERT INTO jobs(customer_id, quote_id, title, service_type, job_date, status, amount, assigned_to, notes)
                        VALUES (?,?,?,?,?,?,?,?,?)""", (customer_id, quote_id, title, service_type, job_date, status, amount, assigned_to, notes))
        if status == 'Completed':
            completed_job_id = job_id

    # Invoices
    invoice_specs = [
        (customer_ids[4], completed_job_id, None, today.isoformat(), (today + timedelta(days=7)).isoformat(), "Sent", 145.0, "Sent after completed clean."),
        (customer_ids[1], None, None, (today - timedelta(days=10)).isoformat(), (today - timedelta(days=3)).isoformat(), "Overdue", 240.0, "Commercial invoice now overdue."),
        (customer_ids[5], None, None, today.isoformat(), (today + timedelta(days=14)).isoformat(), "Draft", 320.0, "Recurring invoice draft for office clean."),
    ]
    for customer_id, job_id, quote_id, invoice_date, due_date, status, total, notes in invoice_specs:
        run("""INSERT INTO invoices(customer_id, job_id, quote_id, invoice_number, invoice_date, due_date, status, subtotal, vat, total, payload_json, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            customer_id, job_id, quote_id, next_invoice_number(), invoice_date, due_date, status, total, 0, total,
            json.dumps({"lines": [{"item_name": "Service", "quantity": 1, "unit_price": total, "line_total": total}], "include_vat": False}), notes
        ))

    # Expenses
    expense_specs = [
        (today.isoformat(), "Fuel", "Shell", "Fuel top up", 65.0, 0.0, "Van fuel for the week."),
        ((today - timedelta(days=2)).isoformat(), "Chemicals", "RestoreMate", "Spotters and rinse", 92.0, 0.0, "Restocked van chemicals."),
        ((today - timedelta(days=7)).isoformat(), "Marketing", "Google Ads", "Lead generation", 120.0, 0.0, "Demo spend entry."),
    ]
    for row in expense_specs:
        run("""INSERT INTO expenses(expense_date, category, supplier, description, amount, vat_amount, notes)
               VALUES (?,?,?,?,?,?,?)""", row)

    # Recurring income plan
    run("""INSERT INTO recurring_income(customer_id, payer_name, start_date, next_due_date, description, amount, include_vat, frequency, collection_method, auto_payment_rule, notes, active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""", (
        customer_ids[5], "Turner Offices Ltd", today.isoformat(), (today + timedelta(days=30)).isoformat(), "Monthly office maintenance", 320.0, 0, "Monthly", "Invoice", "Auto by Method", "Starter recurring plan"
    ))

    # Communications
    comm_specs = [
        (customer_ids[0], "Email", "Quote sent", "Sent quote for lounge, stairs and landing."),
        (customer_ids[4], "SMS", "", "Thanks again for booking with us. Your invoice has been sent over."),
        (customer_ids[5], "Email", "Monthly maintenance proposal", "Discussed recurring monthly office cleaning arrangement."),
    ]
    for row in comm_specs:
        run("INSERT INTO communications (customer_id, channel, subject, body, created_at) VALUES (?,?,?,?,datetime('now'))", row)

    # Timeline items
    timeline_specs = [
        (customer_ids[0], "Customer asked for protector pricing at the next visit."),
        (customer_ids[3], "Landlord approved works and requested completion photos."),
        (customer_ids[4], "Customer mentioned possible sofa clean next month."),
    ]
    for customer_id, note in timeline_specs:
        run("INSERT INTO customer_timeline (customer_id, note_text, created_at) VALUES (?,?,datetime('now'))", (customer_id, note))

    flash("Demo customers and sample finance, jobs, invoices, communications and recurring items loaded.")
    return redirect(url_for("dashboard"))


def xero_config():
    client_id = os.environ.get("XERO_CLIENT_ID", "").strip()
    client_secret = os.environ.get("XERO_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("XERO_REDIRECT_URI", "").strip()
    if not redirect_uri:
        redirect_uri = url_for("xero_callback", _external=True)
    return client_id, client_secret, redirect_uri


def xero_is_configured():
    client_id, client_secret, _redirect_uri = xero_config()
    return bool(client_id and client_secret)


def xero_token_row():
    return q("SELECT * FROM xero_tokens WHERE id=1", one=True)


def save_xero_token(payload, tenant_id=None):
    expires_in = int(payload.get("expires_in") or 1800)
    expires_at = int(time.time()) + max(expires_in - 120, 60)
    run("""INSERT OR REPLACE INTO xero_tokens(id, access_token, refresh_token, expires_at, tenant_id, token_json, updated_at)
           VALUES (1,?,?,?,?,?,datetime('now'))""", (
        payload.get("access_token", ""),
        payload.get("refresh_token", ""),
        expires_at,
        tenant_id or payload.get("tenant_id") or "",
        json.dumps(payload),
    ))


def xero_token_request(data):
    client_id, client_secret, _redirect_uri = xero_config()
    if not client_id or not client_secret:
        raise RuntimeError("Xero is not configured. Set XERO_CLIENT_ID and XERO_CLIENT_SECRET as environment variables.")
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(XERO_TOKEN_URL, data=encoded, method="POST", headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.exception("Xero token request failed: %s", body)
        raise RuntimeError(f"Xero token request failed: {body}") from exc


def refresh_xero_token_if_needed():
    row = xero_token_row()
    if not row or not row["refresh_token"]:
        raise RuntimeError("Xero is not connected yet. Use Xero Connect first.")
    if row["access_token"] and int(row["expires_at"] or 0) > int(time.time()):
        return row["access_token"], row["tenant_id"]
    payload = xero_token_request({
        "grant_type": "refresh_token",
        "refresh_token": row["refresh_token"],
    })
    save_xero_token(payload, row["tenant_id"])
    refreshed = xero_token_row()
    return refreshed["access_token"], refreshed["tenant_id"]


def xero_api_request(url, method="GET", payload=None, idempotency_key=None):
    access_token, tenant_id = refresh_xero_token_if_needed()
    if not tenant_id:
        tenant_id = choose_xero_tenant(access_token)
    body = None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Xero-tenant-id": tenant_id,
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = str(idempotency_key)[:128]
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        logger.exception("Xero API request failed: %s", body_text)
        raise RuntimeError(f"Xero API request failed: {body_text}") from exc


def xero_sales_account_code():
    return clean_str(os.environ.get("XERO_SALES_ACCOUNT_CODE")) or "200"


def xero_tax_type():
    return clean_str(os.environ.get("XERO_TAX_TYPE")) or "OUTPUT2"


def xero_invoice_status_for_push():
    value = clean_str(os.environ.get("XERO_INVOICE_PUSH_STATUS")).upper()
    return value if value in {"DRAFT", "SUBMITTED", "AUTHORISED"} else "DRAFT"


def log_xero_sync(local_type, local_id, action, status, message="", payload=None):
    try:
        run("""INSERT INTO xero_sync_log(local_type, local_id, action, status, message, payload_json)
               VALUES (?,?,?,?,?,?)""", (
            local_type,
            local_id,
            action,
            status,
            clean_str(message),
            json.dumps(payload or {}),
        ))
    except Exception:
        logger.exception("Could not write Xero sync log")


def choose_xero_tenant(access_token):
    req = urllib.request.Request(XERO_CONNECTIONS_URL, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            connections = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.exception("Xero connections request failed: %s", body)
        raise RuntimeError(f"Xero connections request failed: {body}") from exc
    if not connections:
        raise RuntimeError("Xero connected, but no organisation was returned.")
    tenant_id = connections[0].get("tenantId", "")
    row = xero_token_row()
    if row:
        payload = json.loads(row["token_json"] or "{}")
        save_xero_token(payload, tenant_id)
    return tenant_id


def split_customer_name(name):
    parts = clean_str(name).split()
    if not parts:
        return "Customer", "Lead"
    if len(parts) == 1:
        return parts[0], "Customer"
    return parts[0], " ".join(parts[1:])


def create_customer_from_intake(lead):
    if lead["customer_id"]:
        set_customer_workflow(lead["customer_id"], "waiting_for_review", "Booking form attached to existing customer.", "Booking form completed")
        run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
            (lead["customer_id"], "Task: Review customer details from the submitted booking form.", row_get(lead, "photo_filename", "")))
        return lead["customer_id"]
    first_name, last_name = split_customer_name(lead["name"])
    existing_customer_id = find_existing_customer_id(
        first_name=first_name,
        last_name=last_name,
        email=lead["email"],
        phone=lead["phone"],
        postcode=lead["postcode"],
    )
    if existing_customer_id:
        run("""UPDATE intake_submissions SET customer_id=?, status='Reviewed', updated_at=datetime('now') WHERE id=?""",
            (existing_customer_id, lead["id"]))
        set_customer_workflow(existing_customer_id, "waiting_for_review", "Booking form attached to existing customer.", "Booking form completed")
        run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
            (existing_customer_id, "Task: Review customer details from the submitted booking form.", row_get(lead, "photo_filename", "")))
        return existing_customer_id
    notes = "\n".join([x for x in [
        "Created from customer intake form.",
        f"Google Maps: {lead['google_maps_link']}" if lead["google_maps_link"] else "",
        f"What3Words: {lead['what3words']}" if lead["what3words"] else "",
        f"Rooms/areas: {lead['rooms_areas']}" if lead["rooms_areas"] else "",
        f"What cleaned: {row_get(lead, 'what_cleaned')}" if row_get(lead, "what_cleaned") else "",
        f"Number of rooms: {row_get(lead, 'number_rooms')}" if row_get(lead, "number_rooms") else "",
        f"Upholstery: {row_get(lead, 'upholstery')}" if row_get(lead, "upholstery") else "",
        f"Rugs: {row_get(lead, 'rugs')}" if row_get(lead, "rugs") else "",
        f"Stains/problem areas: {row_get(lead, 'stains')}" if row_get(lead, "stains") else "",
        f"Pets: {row_get(lead, 'pets')}" if row_get(lead, "pets") else "",
        f"Parking: {row_get(lead, 'parking')}" if row_get(lead, "parking") else "",
        f"Preferred days/times: {row_get(lead, 'preferred_days_times')}" if row_get(lead, "preferred_days_times") else "",
        f"Additional notes: {row_get(lead, 'additional_notes')}" if row_get(lead, "additional_notes") else "",
        f"Preferred: {lead['preferred_date']} {lead['preferred_time']}".strip() if (lead["preferred_date"] or lead["preferred_time"]) else "",
        lead["job_notes"] or "",
    ] if x])
    customer_id = run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,tags,notes)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""", (
        first_name, last_name, lead["phone"], lead["email"], lead["full_address"], "", lead["postcode"],
        "Customer intake form", "Intake", notes,
    ))
    run("""UPDATE intake_submissions SET customer_id=?, status='Reviewed', updated_at=datetime('now') WHERE id=?""",
        (customer_id, lead["id"]))
    set_customer_workflow(customer_id, "waiting_for_review", "Booking form completed and waiting for review.", "Booking form completed")
    run("INSERT INTO customer_timeline(customer_id, note_text, photo_filename) VALUES (?,?,?)",
        (customer_id, "Task: Review customer details from the submitted booking form.", row_get(lead, "photo_filename", "")))
    return customer_id


def xero_contact_payload_from_lead(lead):
    name = clean_str(lead["name"]) or "Customer"
    first_name, last_name = split_customer_name(name)
    contact = {
        "Name": name,
        "FirstName": first_name,
        "LastName": last_name,
        "ContactNumber": f"FORM-{lead['id']}",
        "Phones": [{"PhoneType": "MOBILE", "PhoneNumber": clean_str(lead["phone"])}] if lead["phone"] else [],
        "Addresses": [{"AddressType": "STREET", "AddressLine1": clean_str(lead["full_address"]), "PostalCode": clean_str(lead["postcode"])}],
    }
    if lead["email"]:
        contact["EmailAddress"] = clean_str(lead["email"])
    return {"Contacts": [contact]}


def xero_contact_payload_from_customer(customer):
    full_name = clean_str(f"{customer['first_name'] or ''} {customer['last_name'] or ''}") or f"Customer {customer['id']}"
    contact = {
        "Name": full_name,
        "FirstName": clean_str(customer["first_name"]),
        "LastName": clean_str(customer["last_name"]),
        "ContactNumber": f"CRM-{customer['id']}",
        "Phones": [{"PhoneType": "MOBILE", "PhoneNumber": clean_str(customer["phone"])}] if customer["phone"] else [],
        "Addresses": [{"AddressType": "STREET", "AddressLine1": clean_str(customer["address"]), "City": clean_str(customer["town"]), "PostalCode": clean_str(customer["postcode"])}],
    }
    if customer["email"]:
        contact["EmailAddress"] = clean_str(customer["email"])
    if customer["xero_contact_id"]:
        contact["ContactID"] = customer["xero_contact_id"]
    return {"Contacts": [contact]}


def find_xero_contact_id_for_lead(lead):
    if lead["xero_contact_id"]:
        return lead["xero_contact_id"]
    if not lead["email"]:
        return ""
    email = clean_str(lead["email"]).replace('"', '\\"')
    where = urllib.parse.quote(f'EmailAddress=="{email}"')
    result = xero_api_request(f"{XERO_CONTACTS_URL}?where={where}")
    contacts = result.get("Contacts") or []
    return contacts[0].get("ContactID", "") if contacts else ""


def find_xero_contact_id_for_customer(customer):
    if customer["xero_contact_id"]:
        return customer["xero_contact_id"]
    if customer["email"]:
        email = clean_str(customer["email"]).replace('"', '\\"')
        where = urllib.parse.quote(f'EmailAddress=="{email}"')
        result = xero_api_request(f"{XERO_CONTACTS_URL}?where={where}")
        contacts = result.get("Contacts") or []
        if contacts:
            return contacts[0].get("ContactID", "")
    return ""


def xero_contact_phone(contact):
    for phone in contact.get("Phones") or []:
        number = clean_str(phone.get("PhoneNumber") or phone.get("PhoneAreaCode") or "")
        if number:
            return number
    return ""


def xero_contact_address(contact):
    for address in contact.get("Addresses") or []:
        line1 = clean_str(address.get("AddressLine1"))
        line2 = clean_str(address.get("AddressLine2"))
        city = clean_str(address.get("City"))
        postcode = clean_str(address.get("PostalCode"))
        if line1 or line2 or city or postcode:
            return {
                "address": "\n".join([x for x in [line1, line2] if x]),
                "town": city,
                "postcode": postcode,
            }
    return {"address": "", "town": "", "postcode": ""}


def xero_contact_name_parts(contact):
    first_name = clean_str(contact.get("FirstName"))
    last_name = clean_str(contact.get("LastName"))
    if first_name or last_name:
        return first_name or "Customer", last_name or "Xero"
    return split_customer_name(clean_str(contact.get("Name")) or "Xero Customer")


def pull_xero_contacts_into_crm(max_pages=20):
    created = 0
    updated = 0
    skipped = 0
    failed = 0
    seen = 0
    for page in range(1, max_pages + 1):
        result = xero_api_request(f"{XERO_CONTACTS_URL}?page={page}")
        contacts = result.get("Contacts") or []
        if not contacts:
            break
        for contact in contacts:
            seen += 1
            try:
                contact_id = clean_str(contact.get("ContactID"))
                status = clean_str(contact.get("ContactStatus")).upper()
                name = clean_str(contact.get("Name"))
                email = clean_str(contact.get("EmailAddress")).lower()
                if not contact_id or status == "ARCHIVED" or not name:
                    skipped += 1
                    continue
                first_name, last_name = xero_contact_name_parts(contact)
                phone = xero_contact_phone(contact)
                address = xero_contact_address(contact)
                existing = q("SELECT id FROM customers WHERE IFNULL(xero_contact_id,'')=? ORDER BY id DESC LIMIT 1", (contact_id,), one=True)
                customer_id = existing["id"] if existing else None
                if not customer_id:
                    customer_id = find_existing_customer_id(
                        first_name=first_name,
                        last_name=last_name,
                        email=email,
                        phone=phone,
                        postcode=address["postcode"],
                    )
                if customer_id:
                    run("""UPDATE customers
                           SET first_name=COALESCE(NULLIF(?,''), first_name),
                               last_name=COALESCE(NULLIF(?,''), last_name),
                               phone=COALESCE(NULLIF(?,''), phone),
                               email=COALESCE(NULLIF(?,''), email),
                               address=COALESCE(NULLIF(?,''), address),
                               town=COALESCE(NULLIF(?,''), town),
                               postcode=COALESCE(NULLIF(?,''), postcode),
                               source=CASE WHEN IFNULL(source,'')='' THEN 'Xero' ELSE source END,
                               tags=CASE WHEN IFNULL(tags,'')='' THEN 'Xero' WHEN tags NOT LIKE '%Xero%' THEN tags || ', Xero' ELSE tags END,
                               xero_contact_id=?,
                               xero_contact_synced_at=datetime('now'),
                               xero_contact_error=''
                           WHERE id=?""", (
                        first_name, last_name, phone, email, address["address"], address["town"], address["postcode"],
                        contact_id, customer_id,
                    ))
                    updated += 1
                else:
                    customer_id = run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,tags,notes,xero_contact_id,xero_contact_synced_at)
                                         VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""", (
                        first_name,
                        last_name,
                        phone,
                        email,
                        address["address"],
                        address["town"],
                        address["postcode"],
                        "Xero",
                        "Xero",
                        f"Imported from Xero contact: {name}",
                        contact_id,
                    ))
                    created += 1
                log_xero_sync("customer", customer_id, "pull_contact", "ok", f"Pulled Xero contact: {name}", {"ContactID": contact_id})
            except Exception as exc:
                failed += 1
                logger.exception("Xero pull contact failed")
                log_xero_sync("customer", 0, "pull_contact", "error", str(exc), contact)
    return {"seen": seen, "created": created, "updated": updated, "skipped": skipped, "failed": failed}


def ensure_xero_contact_for_customer(customer_id):
    customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not customer:
        raise RuntimeError("Customer not found.")
    contact_id = find_xero_contact_id_for_customer(customer)
    if contact_id:
        run("""UPDATE customers SET xero_contact_id=?, xero_contact_synced_at=datetime('now'), xero_contact_error='' WHERE id=?""", (contact_id, customer_id))
        log_xero_sync("customer", customer_id, "match_contact", "ok", "Matched existing Xero contact", {"ContactID": contact_id})
        return contact_id
    result = xero_api_request(
        XERO_CONTACTS_URL,
        method="POST",
        payload=xero_contact_payload_from_customer(customer),
        idempotency_key=f"crm-contact-{customer_id}",
    )
    contacts = result.get("Contacts") or []
    contact_id = contacts[0].get("ContactID", "") if contacts else ""
    if not contact_id:
        raise RuntimeError("Xero did not return a ContactID.")
    run("""UPDATE customers SET xero_contact_id=?, xero_contact_synced_at=datetime('now'), xero_contact_error='' WHERE id=?""", (contact_id, customer_id))
    log_xero_sync("customer", customer_id, "create_contact", "ok", "Created or updated Xero contact", result)
    return contact_id


def xero_line_items_for_invoice(invoice, calc):
    lines = calc.get("lines") or []
    account_code = xero_sales_account_code()
    tax_type = xero_tax_type()
    line_items = []
    for line in lines:
        desc = clean_str(line.get("item_name") or line.get("name") or invoice["invoice_number"] or "Carpet cleaning")
        quantity = float(line.get("quantity") or 1)
        unit_amount = float(line.get("unit_price") or line.get("line_total") or 0)
        if quantity <= 0:
            quantity = 1
        line_items.append({
            "Description": desc,
            "Quantity": quantity,
            "UnitAmount": unit_amount,
            "AccountCode": account_code,
            "TaxType": tax_type,
        })
    if not line_items:
        subtotal = float(invoice["subtotal"] or invoice["total"] or 0)
        line_items.append({
            "Description": clean_str(invoice["notes"])[:240] or "Carpet cleaning service",
            "Quantity": 1,
            "UnitAmount": subtotal,
            "AccountCode": account_code,
            "TaxType": tax_type,
        })
    return line_items


def xero_invoice_payload(invoice, contact_id):
    payload = json.loads(invoice["payload_json"] or "{}") if invoice["payload_json"] else {}
    calc = calc_from_payload(payload) if payload else {"lines": [], "subtotal": invoice["subtotal"], "vat": invoice["vat"], "total": invoice["total"]}
    return {
        "Invoices": [{
            "Type": "ACCREC",
            "Contact": {"ContactID": contact_id},
            "Date": invoice["invoice_date"] or date.today().isoformat(),
            "DueDate": invoice["due_date"] or invoice["invoice_date"] or date.today().isoformat(),
            "InvoiceNumber": invoice["invoice_number"] or f"CRM-{invoice['id']}",
            "Reference": f"CRM invoice {invoice['id']}",
            "Status": xero_invoice_status_for_push(),
            "LineAmountTypes": "Exclusive",
            "LineItems": xero_line_items_for_invoice(invoice, calc),
        }]
    }


def update_invoice_from_xero(invoice_id, xero_invoice):
    xero_status = clean_str(xero_invoice.get("Status"))
    amount_due = float(xero_invoice.get("AmountDue") or 0)
    amount_paid = float(xero_invoice.get("AmountPaid") or 0)
    status = "Paid" if xero_status == "PAID" or amount_due <= 0 < float(xero_invoice.get("Total") or 0) else None
    if status:
        run("""UPDATE invoices SET status=?, xero_status=?, xero_amount_due=?, xero_amount_paid=?, xero_synced_at=datetime('now'), xero_error='', xero_last_payload=? WHERE id=?""",
            (status, xero_status, amount_due, amount_paid, json.dumps(xero_invoice), invoice_id))
    else:
        run("""UPDATE invoices SET xero_status=?, xero_amount_due=?, xero_amount_paid=?, xero_synced_at=datetime('now'), xero_error='', xero_last_payload=? WHERE id=?""",
            (xero_status, amount_due, amount_paid, json.dumps(xero_invoice), invoice_id))


@app.route("/booking-form", methods=["GET", "POST"])
def booking_form():
    linked_customer_id = int(request.values.get("customer_id") or 0)
    linked_customer = q("SELECT * FROM customers WHERE id=?", (linked_customer_id,), one=True) if linked_customer_id else None
    if request.method == "POST":
        name = clean_str(request.form.get("name"))
        phone = clean_str(request.form.get("phone"))
        email = clean_str(request.form.get("email"))
        if not name or not phone:
            flash("Please enter your name and phone number.")
            return redirect(url_for("booking_form"))
        if email and not is_valid_email(email):
            flash("Please enter a valid email address.")
            return redirect(url_for("booking_form"))
        photo_filename = save_upload("photo")
        lead_id = run("""INSERT INTO intake_submissions
               (name, phone, email, full_address, postcode, google_maps_link, what3words, job_notes, rooms_areas,
                what_cleaned, number_rooms, upholstery, rugs, stains, pets, parking, preferred_days_times, additional_notes,
                preferred_date, preferred_time, photo_filename, customer_id, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            name, phone, email, clean_str(request.form.get("full_address")), clean_str(request.form.get("postcode")),
            clean_str(request.form.get("google_maps_link")), clean_str(request.form.get("what3words")),
            clean_str(request.form.get("job_notes")), clean_str(request.form.get("rooms_areas")),
            clean_str(request.form.get("what_cleaned")), clean_str(request.form.get("number_rooms")),
            clean_str(request.form.get("upholstery")), clean_str(request.form.get("rugs")),
            clean_str(request.form.get("stains")), clean_str(request.form.get("pets")),
            clean_str(request.form.get("parking")), clean_str(request.form.get("preferred_days_times")),
            clean_str(request.form.get("additional_notes")),
            clean_str(request.form.get("preferred_date")), clean_str(request.form.get("preferred_time")), photo_filename,
            linked_customer_id or None, "Waiting for review",
        ))
        lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        customer_id = create_customer_from_intake(lead)
        run("UPDATE intake_submissions SET customer_id=?, status='Waiting for review', updated_at=datetime('now') WHERE id=?", (customer_id, lead_id))
        return render_template("customer_intake_thanks.html", biz=settings(), public_mode=True)
    return render_template("customer_intake.html", biz=settings(), linked_customer=linked_customer, public_mode=True)


@app.route("/customer-intake", methods=["GET", "POST"])
def customer_intake():
    if request.method == "POST":
        return booking_form()
    return redirect(url_for("booking_form", **request.args))


@app.route("/website-form", methods=["GET", "POST", "OPTIONS"])
@app.route("/api/website-form", methods=["POST", "OPTIONS"])
def website_form_submit():
    if request.method == "OPTIONS":
        return ("", 204)
    if request.method == "GET":
        return render_template(
            "customer_intake_thanks.html",
            biz=settings(),
            public_mode=True,
            title="Website form endpoint",
            message="This page receives website forms. Please submit the quote form on the website so the enquiry can be saved to the CRM.",
        )
    data = request.get_json(silent=True) if request.is_json else request.form
    data = data or {}
    try:
        photo_filename = save_upload("photo")
        lead_id, customer_id = create_intake_from_website_payload(data, source=request_value(data, "source") or "Website form", photo_filename=photo_filename)
    except ValueError as exc:
        if request.is_json or request.path.startswith("/api/"):
            return {"ok": False, "error": str(exc)}, 400
        flash(str(exc))
        return redirect(url_for("booking_form"))
    automation_results = run_website_enquiry_automation(lead_id, customer_id, data)
    if request.is_json or request.path.startswith("/api/"):
        customer = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
        lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        return {
            "ok": True,
            "lead_id": lead_id,
            "customer_id": customer_id,
            "customer_url": url_for("customer_view", customer_id=customer_id, _external=True),
            "next_action": customer["next_action"] if customer else "Review website form and approve customer for Xero",
            "xero_status": lead["xero_sync_status"] if lead else "",
            "customer_email_status": lead["customer_email_status"] if lead else "",
            "customer_sms_status": lead["customer_sms_status"] if lead else "",
            "owner_email_status": lead["owner_email_status"] if lead else "",
            "owner_sms_status": lead["owner_sms_status"] if lead else "",
            "automation": {key: {"ok": value[0], "message": value[1]} for key, value in automation_results.items()},
        }
    return render_template(
        "customer_intake_thanks.html",
        biz=settings(),
        public_mode=True,
        message="Thank you. Your enquiry has been received and we will get back to you shortly.",
    )


@app.route("/intake-forms")
@login_required
def intake_forms():
    rows = q("SELECT * FROM intake_submissions ORDER BY id DESC")
    token = xero_token_row()
    return render_template("intake_forms.html", leads=rows, xero_connected=bool(token and token["access_token"]), xero_configured=xero_is_configured())


@app.route("/intake-forms/<int:lead_id>")
@login_required
def intake_form_view(lead_id):
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    if not lead:
        flash("Intake form not found.")
        return redirect(url_for("intake_forms"))
    return render_template("intake_form_view.html", lead=lead, xero_configured=xero_is_configured(), xero_connected=bool(xero_token_row()))


@app.route("/intake-forms/<int:lead_id>/review", methods=["POST"])
@login_required
def intake_form_review(lead_id):
    status = clean_str(request.form.get("status")) or "Reviewed"
    run("""UPDATE intake_submissions SET status=?, review_notes=?, updated_at=datetime('now') WHERE id=?""", (
        status,
        clean_str(request.form.get("review_notes")),
        lead_id,
    ))
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    if lead and lead["customer_id"]:
        if status.lower() in {"approved", "reviewed"}:
            set_customer_workflow(lead["customer_id"], "customer_approved", clean_str(request.form.get("review_notes")), "Customer approved")
        elif status.lower() in {"rejected", "more information needed", "information required"}:
            set_customer_workflow(lead["customer_id"], "booking_form_sent", clean_str(request.form.get("review_notes")), "Information required")
            run("UPDATE customers SET next_action='Request more information from customer' WHERE id=?", (lead["customer_id"],))
    flash("Intake form reviewed.")
    return redirect(url_for("intake_form_view", lead_id=lead_id))


@app.route("/intake-forms/<int:lead_id>/create-customer", methods=["POST"])
@login_required
def intake_create_customer(lead_id):
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    if not lead:
        flash("Intake form not found.")
        return redirect(url_for("intake_forms"))
    customer_id = create_customer_from_intake(lead)
    flash("Customer is ready from this intake form.")
    return redirect(url_for("customer_view", customer_id=customer_id))


@app.route("/intake-forms/<int:lead_id>/create-job", methods=["POST"])
@login_required
def intake_create_job(lead_id):
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    if not lead:
        flash("Intake form not found.")
        return redirect(url_for("intake_forms"))
    customer_id = create_customer_from_intake(lead)
    if lead["job_id"]:
        flash("This intake already has a job.")
        return redirect(url_for("job_view", job_id=lead["job_id"]))
    title = clean_str(request.form.get("title")) or f"Intake job - {lead['name']}"
    notes = "\n".join([x for x in [
        lead["job_notes"] or "",
        f"Rooms/areas: {lead['rooms_areas']}" if lead["rooms_areas"] else "",
        f"Address: {lead['full_address']}" if lead["full_address"] else "",
        f"Postcode: {lead['postcode']}" if lead["postcode"] else "",
        f"Google Maps: {lead['google_maps_link']}" if lead["google_maps_link"] else "",
        f"What3Words: {lead['what3words']}" if lead["what3words"] else "",
        f"Photo: {lead['photo_filename']}" if lead["photo_filename"] else "",
    ] if x])
    job_id = run("""INSERT INTO jobs(customer_id, title, service_type, job_date, status, amount, assigned_to, notes)
                    VALUES (?,?,?,?,?,?,?,?)""", (
        customer_id, title, "Customer intake", lead["preferred_date"], "Booked", 0, "", notes,
    ))
    run("""UPDATE intake_submissions SET job_id=?, status='Booked', updated_at=datetime('now') WHERE id=?""", (job_id, lead_id))
    flash("Job created from intake form.")
    return redirect(url_for("job_view", job_id=job_id))


@app.route("/xero/connect")
@login_required
def xero_connect():
    client_id, _client_secret, redirect_uri = xero_config()
    if not client_id:
        flash("Xero is not configured. Set XERO_CLIENT_ID and XERO_CLIENT_SECRET first.")
        return redirect(url_for("xero_dashboard"))
    state = secrets.token_urlsafe(24)
    session["xero_oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": XERO_SCOPES,
        "state": state,
    }
    return redirect(f"{XERO_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}")


@app.route("/xero")
@login_required
def xero_dashboard():
    token = xero_token_row()
    token_ready = bool(token and token["refresh_token"])
    counts = {
        "customers_linked": q("SELECT COUNT(*) AS c FROM customers WHERE IFNULL(xero_contact_id,'')<>''", one=True)["c"],
        "invoices_linked": q("SELECT COUNT(*) AS c FROM invoices WHERE IFNULL(xero_invoice_id,'')<>''", one=True)["c"],
        "invoices_unlinked": q("SELECT COUNT(*) AS c FROM invoices WHERE IFNULL(status,'')<>'Archived' AND IFNULL(xero_invoice_id,'')=''", one=True)["c"],
        "xero_paid": q("SELECT COUNT(*) AS c FROM invoices WHERE IFNULL(xero_status,'')='PAID' OR IFNULL(status,'')='Paid'", one=True)["c"],
    }
    recent_invoices = q("""SELECT invoices.*, customers.first_name || ' ' || customers.last_name AS customer_name
                           FROM invoices LEFT JOIN customers ON customers.id = invoices.customer_id
                           WHERE IFNULL(invoices.status,'')<>'Archived'
                           ORDER BY invoices.id DESC LIMIT 12""")
    recent_logs = q("SELECT * FROM xero_sync_log ORDER BY id DESC LIMIT 12")
    return render_template(
        "xero_dashboard.html",
        configured=xero_is_configured(),
        connected=token_ready,
        token=token,
        counts=counts,
        recent_invoices=recent_invoices,
        recent_logs=recent_logs,
        scopes=XERO_SCOPES,
        sales_account_code=xero_sales_account_code(),
        tax_type=xero_tax_type(),
        push_status=xero_invoice_status_for_push(),
    )


@app.route("/xero/callback")
def xero_callback():
    if request.args.get("error"):
        flash(f"Xero connection failed: {request.args.get('error_description') or request.args.get('error')}")
        return redirect(url_for("xero_dashboard"))
    if request.args.get("state") != session.get("xero_oauth_state"):
        flash("Xero connection failed because the security state did not match.")
        return redirect(url_for("xero_dashboard"))
    code = request.args.get("code", "")
    if not code:
        flash("Xero did not return an authorisation code.")
        return redirect(url_for("xero_dashboard"))
    try:
        _client_id, _client_secret, redirect_uri = xero_config()
        payload = xero_token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        })
        save_xero_token(payload)
        access_token = payload.get("access_token", "")
        if access_token:
            choose_xero_tenant(access_token)
        flash("Xero connected.")
    except Exception as exc:
        logger.exception("Xero callback failed")
        flash(str(exc))
    return redirect(url_for("xero_dashboard"))


@app.route("/xero/test")
@login_required
def xero_test():
    try:
        access_token, tenant_id = refresh_xero_token_if_needed()
        if not tenant_id:
            tenant_id = choose_xero_tenant(access_token)
        flash(f"Xero connection is working. Tenant ID: {tenant_id}")
    except Exception as exc:
        logger.exception("Xero test failed")
        flash(str(exc))
    return redirect(url_for("xero_dashboard"))


@app.route("/xero/sync-contact/<int:customer_id>", methods=["POST"])
@login_required
def xero_sync_contact(customer_id):
    try:
        contact_id = ensure_xero_contact_for_customer(customer_id)
        set_customer_workflow(customer_id, "xero_synced", f"Xero contact ready: {contact_id}", "Xero contact synced")
        flash(f"Customer linked to Xero ContactID {contact_id}.")
    except Exception as exc:
        logger.exception("Xero customer sync failed for customer %s", customer_id)
        run("UPDATE customers SET xero_contact_error=? WHERE id=?", (str(exc), customer_id))
        log_xero_sync("customer", customer_id, "sync_contact", "error", str(exc))
        flash(str(exc))
    return redirect(request.form.get("next_url") or url_for("customer_view", customer_id=customer_id))


@app.route("/xero/sync-invoice/<int:invoice_id>", methods=["POST"])
@login_required
def xero_sync_invoice(invoice_id):
    invoice = q("""SELECT invoices.*, customers.first_name, customers.last_name, customers.email, customers.phone, customers.address, customers.town, customers.postcode, customers.xero_contact_id
                   FROM invoices LEFT JOIN customers ON customers.id = invoices.customer_id
                   WHERE invoices.id=?""", (invoice_id,), one=True)
    if not invoice:
        flash("Invoice not found.")
        return redirect(url_for("invoices"))
    try:
        if invoice["xero_invoice_id"]:
            flash("This invoice is already linked to Xero. Use Refresh Xero Status instead.")
            return redirect(url_for("invoice_view", invoice_id=invoice_id))
        contact_id = ensure_xero_contact_for_customer(invoice["customer_id"])
        payload = xero_invoice_payload(invoice, contact_id)
        result = xero_api_request(XERO_INVOICES_URL, method="POST", payload=payload, idempotency_key=f"crm-invoice-{invoice_id}")
        invoices_result = result.get("Invoices") or []
        xero_invoice = invoices_result[0] if invoices_result else {}
        xero_invoice_id = xero_invoice.get("InvoiceID", "")
        if not xero_invoice_id:
            raise RuntimeError("Xero did not return an InvoiceID.")
        run("""UPDATE invoices
               SET xero_invoice_id=?, xero_invoice_number=?, xero_status=?, xero_amount_due=?, xero_amount_paid=?, xero_synced_at=datetime('now'), xero_error='', xero_last_payload=?
               WHERE id=?""", (
            xero_invoice_id,
            xero_invoice.get("InvoiceNumber", ""),
            xero_invoice.get("Status", ""),
            float(xero_invoice.get("AmountDue") or invoice["total"] or 0),
            float(xero_invoice.get("AmountPaid") or 0),
            json.dumps(xero_invoice),
            invoice_id,
        ))
        log_xero_sync("invoice", invoice_id, "create_invoice", "ok", "Invoice synced to Xero", result)
        flash("Invoice synced to Xero.")
    except Exception as exc:
        logger.exception("Xero invoice sync failed for invoice %s", invoice_id)
        run("UPDATE invoices SET xero_error=? WHERE id=?", (str(exc), invoice_id))
        log_xero_sync("invoice", invoice_id, "create_invoice", "error", str(exc))
        flash(str(exc))
    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/xero/sync-invoice-status/<int:invoice_id>", methods=["POST"])
@login_required
def xero_sync_invoice_status(invoice_id):
    invoice = q("SELECT * FROM invoices WHERE id=?", (invoice_id,), one=True)
    if not invoice:
        flash("Invoice not found.")
        return redirect(url_for("invoices"))
    if not invoice["xero_invoice_id"]:
        flash("This invoice has not been synced to Xero yet.")
        return redirect(url_for("invoice_view", invoice_id=invoice_id))
    try:
        result = xero_api_request(f"{XERO_INVOICES_URL}/{invoice['xero_invoice_id']}")
        invoices_result = result.get("Invoices") or []
        if not invoices_result:
            raise RuntimeError("Xero did not return the invoice.")
        update_invoice_from_xero(invoice_id, invoices_result[0])
        log_xero_sync("invoice", invoice_id, "refresh_status", "ok", "Invoice status refreshed from Xero", result)
        flash("Xero invoice status refreshed.")
    except Exception as exc:
        logger.exception("Xero invoice status sync failed for invoice %s", invoice_id)
        run("UPDATE invoices SET xero_error=? WHERE id=?", (str(exc), invoice_id))
        log_xero_sync("invoice", invoice_id, "refresh_status", "error", str(exc))
        flash(str(exc))
    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/xero/email-invoice/<int:invoice_id>", methods=["POST"])
@login_required
def xero_email_invoice(invoice_id):
    invoice = q("SELECT * FROM invoices WHERE id=?", (invoice_id,), one=True)
    if not invoice or not invoice["xero_invoice_id"]:
        flash("Sync the invoice to Xero before sending it from Xero.")
        return redirect(url_for("invoice_view", invoice_id=invoice_id))
    try:
        result = xero_api_request(f"{XERO_INVOICES_URL}/{invoice['xero_invoice_id']}/Email", method="POST")
        log_xero_sync("invoice", invoice_id, "email_invoice", "ok", "Xero invoice email triggered", result)
        flash("Xero invoice email triggered.")
    except Exception as exc:
        logger.exception("Xero invoice email failed for invoice %s", invoice_id)
        log_xero_sync("invoice", invoice_id, "email_invoice", "error", str(exc))
        flash(str(exc))
    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/xero/refresh-open-invoices", methods=["POST"])
@login_required
def xero_refresh_open_invoices():
    rows = q("SELECT * FROM invoices WHERE IFNULL(xero_invoice_id,'')<>'' AND IFNULL(status,'') NOT IN ('Paid','Archived') ORDER BY id DESC LIMIT 50")
    updated = 0
    failed = 0
    for invoice in rows:
        try:
            result = xero_api_request(f"{XERO_INVOICES_URL}/{invoice['xero_invoice_id']}")
            invoices_result = result.get("Invoices") or []
            if invoices_result:
                update_invoice_from_xero(invoice["id"], invoices_result[0])
                log_xero_sync("invoice", invoice["id"], "bulk_refresh_status", "ok", "Invoice status refreshed from Xero", result)
                updated += 1
        except Exception as exc:
            failed += 1
            logger.exception("Bulk Xero status refresh failed for invoice %s", invoice["id"])
            run("UPDATE invoices SET xero_error=? WHERE id=?", (str(exc), invoice["id"]))
            log_xero_sync("invoice", invoice["id"], "bulk_refresh_status", "error", str(exc))
    flash(f"Xero status refresh complete. Updated {updated}; failed {failed}.")
    return redirect(url_for("xero_dashboard"))


@app.route("/xero/pull-contacts", methods=["POST"])
@login_required
def xero_pull_contacts():
    try:
        if not xero_is_configured():
            raise RuntimeError("Xero cannot pull customers yet. Set XERO_CLIENT_ID, XERO_CLIENT_SECRET, and XERO_REDIRECT_URI in Render, then redeploy.")
        token = xero_token_row()
        if not token or not token["refresh_token"]:
            raise RuntimeError("Xero is not connected yet. Open Xero Sync and press Connect Xero before pulling customers.")
        result = pull_xero_contacts_into_crm()
        flash(
            "Xero customer pull complete. "
            f"Seen {result['seen']}; created {result['created']}; updated {result['updated']}; "
            f"skipped {result['skipped']}; failed {result['failed']}."
        )
    except Exception as exc:
        logger.exception("Xero pull all contacts failed")
        log_xero_sync("customer", 0, "pull_all_contacts", "error", str(exc))
        flash(f"Xero customer pull failed: {exc}")
    return redirect(url_for("xero_dashboard"))


@app.route("/xero/create-contact/<int:lead_id>", methods=["POST", "GET"])
@login_required
def xero_create_contact(lead_id):
    lead = q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
    if not lead:
        flash("Intake form not found.")
        return redirect(url_for("intake_forms"))
    try:
        contact_id = find_xero_contact_id_for_lead(lead)
        if not contact_id:
            result = xero_api_request(XERO_CONTACTS_URL, method="POST", payload=xero_contact_payload_from_lead(lead))
            contacts = result.get("Contacts") or []
            contact_id = contacts[0].get("ContactID", "") if contacts else ""
        if not contact_id:
            raise RuntimeError("Xero did not return a ContactID.")
        run("""UPDATE intake_submissions
               SET xero_contact_id=?, xero_sent_at=datetime('now'), xero_error='', status='Sent to Xero', updated_at=datetime('now')
               WHERE id=?""", (contact_id, lead_id))
        flash("Contact sent to Xero.")
    except Exception as exc:
        logger.exception("Xero contact creation failed for intake %s", lead_id)
        run("""UPDATE intake_submissions SET xero_error=?, updated_at=datetime('now') WHERE id=?""", (str(exc), lead_id))
        flash(str(exc))
    return redirect(url_for("intake_form_view", lead_id=lead_id))

init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=port, debug=debug)
