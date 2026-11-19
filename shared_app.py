"""Both CRM views on the existing service, database and automation runner."""
from flask import request, session, redirect, url_for
from unified_app import app, login_required

# The shared deployment keeps the existing dashboard and full CRM as default.
app.config['SIMPLE_CRM_HOME'] = False
app.config['SIMPLE_PACKAGE_ENABLED'] = False
app.config['SIMPLE_SHARED_ENDPOINTS'] = ['full_crm_mode']

@app.before_request
def remember_crm_view():
    if session.get('logged_in') and request.path.startswith('/simple'):
        session['simple_mode'] = True

@app.route('/full')
@login_required
def full_crm_mode():
    session.pop('simple_mode', None)
    return redirect(url_for('dashboard'))
