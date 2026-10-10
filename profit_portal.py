"""Isolated, password-protected profit workbook; no access to CRM records."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from functools import wraps
from flask import request, session, jsonify, render_template, redirect

PASSWORD_HASH = 'f12f1be6c9c555dd836c66cf732821c8:96baa9935471f9c95a77e223d41d9a53e85ea35b3315ba8642ce3fb52399900b'

def register_profit_portal(app, db_path, csrf_token):
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'profit-workbook.db')
    def connect():
        con = sqlite3.connect(path, timeout=15)
        con.execute('CREATE TABLE IF NOT EXISTS workbook (id INTEGER PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        con.execute('CREATE TABLE IF NOT EXISTS attempts (address TEXT, stamp REAL)')
        return con
    def protected(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('profit_user'):
                if request.path.startswith('/profit/api'):
                    return jsonify(error='Please sign in again.'), 401
                return redirect('/profit/login')
            return fn(*args, **kwargs)
        return wrapper
    @app.route('/profit/login', methods=['GET', 'POST'])
    def profit_login():
        error = None
        csrf_token()
        if request.method == 'POST':
            address = request.remote_addr or 'unknown'
            with connect() as con:
                con.execute('DELETE FROM attempts WHERE stamp < ?', (time.time()-900,))
                count = con.execute('SELECT COUNT(*) FROM attempts WHERE address=?', (address,)).fetchone()[0]
                if count >= 10:
                    return render_template('profit_login.html', error='Too many attempts. Try again in 15 minutes.'), 429
                salt, expected = PASSWORD_HASH.split(':')
                password = request.form.get('password', '')[:200]
                actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
                if request.form.get('username', '').strip().lower() == 'pedro' and hmac.compare_digest(actual, expected):
                    session['profit_user'] = 'Pedro'
                    session.permanent = True
                    con.execute('DELETE FROM attempts WHERE address=?', (address,))
                    return redirect('/profit')
                con.execute('INSERT INTO attempts VALUES (?,?)', (address, time.time()))
                error = 'Username or password is incorrect.'
        return render_template('profit_login.html', error=error)
    @app.post('/profit/logout')
    def profit_logout():
        session.pop('profit_user', None)
        return redirect('/profit/login')
    @app.get('/profit')
    @protected
    def profit_home():
        csrf_token()
        return render_template('profit.html')
    @app.route('/profit/api/workbook', methods=['GET', 'POST'])
    @protected
    def profit_workbook():
        with connect() as con:
            if request.method == 'GET':
                row = con.execute('SELECT data,revision FROM workbook WHERE id=1').fetchone()
                return jsonify(data=json.loads(row[0]) if row else None, revision=row[1] if row else 0)
            if request.content_length and request.content_length > 1000000:
                return jsonify(error='Workbook is too large.'), 413
            payload = request.get_json(silent=True) or {}
            data = payload.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('periods'), list) or len(data['periods']) > 500:
                return jsonify(error='Invalid workbook.'), 400
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT revision FROM workbook WHERE id=1').fetchone()
            revision = row[0] if row else 0
            if payload.get('revision') != revision:
                return jsonify(error='Figures changed on another device. Reload before saving.'), 409
            con.execute('INSERT INTO workbook VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=excluded.revision', (json.dumps(data), revision+1))
            return jsonify(revision=revision+1)
    @app.after_request
    def profit_headers(response):
        if request.path.startswith('/profit'):
            response.headers['Cache-Control'] = 'no-store, private'
        return response
