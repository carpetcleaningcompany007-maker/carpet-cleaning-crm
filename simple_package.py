"""Read-only enquiry reporting and navigation for the simplified CRM.
No provider clients, message dispatches or background jobs are registered here.
"""
from datetime import datetime, timedelta
from flask import request, render_template, redirect, url_for, abort

def register_simple_package(app, login_required, q, clean_str, uk_today):
    @app.before_request
    def simple_start_page():
        if app.config.get('SIMPLE_CRM_HOME') and request.path == '/' and request.method == 'GET':
            return redirect(url_for('simple_workspace'))

    @app.route('/simple/customers/<int:customer_id>')
    @login_required
    def simple_customer_profile(customer_id):
        customer=q('SELECT * FROM customers WHERE id=?',(customer_id,),one=True)
        if not customer: abort(404)
        jobs=q("SELECT * FROM jobs WHERE customer_id=? ORDER BY job_date DESC,id DESC LIMIT 6",(customer_id,))
        reminders=q("SELECT * FROM future_reminders WHERE customer_id=? AND COALESCE(status,'Open')='Open' ORDER BY reminder_date,id LIMIT 6",(customer_id,))
        return render_template('simple_customer_profile.html',customer=customer,jobs=jobs,reminders=reminders)

    @app.route('/simple/enquiries')
    @login_required
    def simple_enquiry_tracker():
        period = request.args.get('period', '30')
        if period not in {'30','90','all'}: period='30'
        search=clean_str(request.args.get('q'))[:200]
        source=clean_str(request.args.get('source'))[:150]
        since=(uk_today()-timedelta(days=int(period)-1)).isoformat() if period!='all' else None
        where=['COALESCE(i.is_test,0)=0']
        params=[]
        if since: where.append('date(i.created_at)>=?');params.append(since)
        if search:
            where.append("(COALESCE(i.name,'') || ' ' || COALESCE(i.email,'') || ' ' || COALESCE(i.phone,'') || ' ' || COALESCE(i.utm_campaign,'') || ' ' || COALESCE(i.utm_term,'')) LIKE ? ESCAPE '\\'")
            params.append('%'+search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        # One original enquiry owns the job's values. A repeat contact cannot
        # duplicate the same quote or invoice revenue in attribution reports.
        raw=q("""SELECT i.*,
            CASE WHEN COALESCE(i.gclid,'')<>'' OR COALESCE(i.gbraid,'')<>'' OR COALESCE(i.wbraid,'')<>'' THEN 'Google Ads'
                 WHEN COALESCE(i.utm_source,'')<>'' THEN i.utm_source ELSE 'Unknown' END AS traffic_source,
            CASE WHEN i.job_id IS NOT NULL AND i.id=(SELECT MIN(o.id) FROM intake_submissions o WHERE o.job_id=i.job_id AND COALESCE(o.is_test,0)=0) THEN 1 ELSE 0 END AS value_owner,
            quotes.total AS linked_quote,
            (SELECT SUM(inv.total) FROM invoices inv WHERE inv.job_id=i.job_id AND lower(COALESCE(inv.status,''))='paid') AS paid_total
            FROM intake_submissions i LEFT JOIN jobs ON jobs.id=i.job_id
            LEFT JOIN quotes ON quotes.id=jobs.quote_id
            WHERE """+' AND '.join(where)+' ORDER BY i.created_at DESC,i.id DESC',tuple(params))
        source_options=sorted({r['traffic_source'] for r in raw})
        rows=[]
        for record in raw:
            r=dict(record)
            if source and r['traffic_source']!=source: continue
            r['quote_value']=r['linked_quote'] if r['value_owner'] else None
            if r['quote_value'] is None and not r['job_id'] and float(r.get('agreed_quote_price') or 0)>0:
                r['quote_value']=float(r['agreed_quote_price'])
            r['sales_value']=r['paid_total'] if r['value_owner'] else None
            rows.append(r)
        groups={}
        for r in rows:
            g=groups.setdefault(r['traffic_source'],{'count':0,'sales':0,'known_sales':0})
            g['count']+=1
            if r['sales_value'] is not None:g['sales']+=float(r['sales_value']);g['known_sales']+=1
        totals={'leads':len(rows),'customers':len({r['customer_id'] for r in rows if r['customer_id']}),
                'quotes':sum(float(r['quote_value'] or 0) for r in rows),'sales':sum(float(r['sales_value'] or 0) for r in rows),
                'quotes_known':sum(r['quote_value'] is not None for r in rows),'sales_known':sum(r['sales_value'] is not None for r in rows)}
        return render_template('simple_enquiries.html',leads=rows[:500],total_rows=len(rows),totals=totals,groups=groups,
                               period=period,search=search,source=source,source_options=source_options)
