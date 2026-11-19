"""Simplified CRM entrypoint. Original app.py stays unchanged."""
import os
from flask import render_template, request
from app import app, login_required, q, clean_str, uk_today
from simple_package import register_simple_package
app.config['SIMPLE_PACKAGE_ENABLED']=True
app.config['SIMPLE_CRM_HOME']=os.environ.get('SIMPLE_CRM_HOME','1').lower() in {'1','true','yes'}

@app.route("/simple")
@login_required
def simple_workspace():
    return render_template("simple_workspace.html")


@app.route("/simple/customers")
@login_required
def simple_customer_library():
    search = clean_str(request.args.get("q"))[:200]
    scope = "all" if request.args.get("scope") == "all" else "active"
    conditions, params = [], []
    if scope == "active":
        conditions.append("COALESCE(archived_at,'')=''")
    if search:
        conditions.append("(first_name || ' ' || last_name || ' ' || COALESCE(email,'') || ' ' || COALESCE(phone,'') || ' ' || COALESCE(postcode,'') || ' ' || COALESCE(town,'') || ' ' || COALESCE(tags,'')) LIKE ? ESCAPE '\\'")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append("%" + escaped + "%")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = q("SELECT id,first_name,last_name,email,phone,town,postcode,tags,notes,archived_at,sms_opt_out FROM customers" + where + " ORDER BY first_name COLLATE NOCASE,last_name COLLATE NOCASE,id LIMIT 500", tuple(params))
    return render_template("simple_customers.html", customers=rows, search=search, scope=scope)



register_simple_package(app, login_required, q, clean_str, uk_today)
