"""Signed, draft-only gateway for Conversation Desk; no CRM record access."""
import base64
from contextlib import closing
import json
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from flask import jsonify, request

PATH = '/api/sales-portal/draft'

def register_sales_portal(app, db_path, model_getter, usage_recorder=None):
    public_key = serialization.load_pem_public_key(Path(__file__).with_name('sales_portal_public.pem').read_bytes())

    @app.post(PATH, endpoint='sales_portal_draft')
    def draft():
        if request.content_length is None or request.content_length > 40000:
            return jsonify(error='Request too large'), 413
        raw = request.get_data(cache=False)
        if len(raw) > 40000:
            return jsonify(error='Request too large'), 413
        stamp = request.headers.get('X-Portal-Time', '')
        nonce = request.headers.get('X-Portal-Nonce', '')
        try:
            if abs(time.time() - int(stamp)) > 120 or not re.fullmatch(r'[a-zA-Z0-9-]{20,64}', nonce):
                raise ValueError()
            signed = ('POST\n' + PATH + '\n' + stamp + '\n' + nonce + '\n').encode() + raw
            public_key.verify(base64.b64decode(request.headers.get('X-Portal-Signature', ''), validate=True), signed, padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            return jsonify(error='Connection authentication failed'), 401
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or not isinstance(body.get('instructions'), str) or not isinstance(body.get('input'), str):
                raise ValueError()
            fmt = body.get('text', {}).get('format', {})
            if fmt.get('type') != 'json_schema' or fmt.get('name') != 'sales_reply':
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return jsonify(error='Invalid draft request'), 400
        key = os.environ.get('OPENAI_API_KEY', '').strip()
        if not key:
            return jsonify(error='CRM AI is not configured'), 503
        # Persistent nonce claims prevent replays across worker restarts/processes.
        now = int(time.time())
        with closing(sqlite3.connect(db_path, timeout=5)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS sales_portal_requests (nonce TEXT PRIMARY KEY, created INTEGER NOT NULL)')
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM sales_portal_requests WHERE created < ?', (now - 3600,))
            if conn.execute('SELECT COUNT(*) FROM sales_portal_requests').fetchone()[0] >= 120:
                return jsonify(error='Draft limit reached; try again later'), 429
            try:
                conn.execute('INSERT INTO sales_portal_requests VALUES (?, ?)', (nonce, now))
            except sqlite3.IntegrityError:
                return jsonify(error='Duplicate request'), 409
        # Explicit fields ensure callers cannot enable tools, storage or unbounded output.
        model = model_getter()
        payload = {'model': model, 'store': False, 'instructions': body['instructions'], 'input': body['input'], 'text': body['text'], 'max_output_tokens': 1800}
        req = urllib.request.Request('https://api.openai.com/v1/responses', data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=40) as result:
                data = json.loads(result.read(1000000))
            if data.get('status') != 'completed':
                return jsonify(error='AI did not finish the draft'), 502
            if usage_recorder:
                usage_recorder(model, data.get('usage') or {}, int((time.time() - started) * 1000))
            return jsonify(status=data['status'], output=data.get('output', []))
        except Exception:
            app.logger.warning('Sales portal AI request failed; customer content omitted.')
            return jsonify(error='CRM AI is temporarily unavailable'), 502
