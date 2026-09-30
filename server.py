#!/usr/bin/env python3
"""YD attendance reporting application server (Python standard library + SQLite)."""
from __future__ import annotations
import argparse, csv, hashlib, hmac, io, json, os, secrets, smtplib, sqlite3, ssl, time
from email.message import EmailMessage
from datetime import date, datetime, timezone, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
DB_PATH=Path(os.environ.get('YD_DATABASE_PATH',ROOT/'instance'/'yd.sqlite3'))
SESSION_SECONDS=int(os.environ.get('YD_SESSION_SECONDS','1800'))
COOKIE='yd_session'
ROLE_NAMES={'system_administrator':'System Administrator','department_user':'Department User','department_supervisor':'Department Supervisor','yd_hr_coordinator':'YD HR Coordinator','hr_manager':'HR Manager','canteen_user':'Canteen User'}
HR={'system_administrator','yd_hr_coordinator'}
DEPT={'department_user','department_supervisor'}
READ_ALL={'system_administrator','yd_hr_coordinator','hr_manager'}

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def hash_password(password,salt=None):
    salt=salt or secrets.token_bytes(16)
    return hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310_000).hex(),salt.hex()
def db_open():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB_PATH,timeout=15);db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=WAL')
    return db
def audit(db,uid,action,kind='',record='',old=None,new=None,reason=''):
    db.execute('''INSERT INTO audit_log(user_id,action,affected_record_type,affected_record_id,previous_value_json,new_value_json,authorized_reason)
      VALUES(?,?,?,?,?,?,?)''',(uid,action,kind,str(record),json.dumps(old) if old is not None else None,json.dumps(new) if new is not None else None,reason))
def create_user(username,name,password,role,department=None,email=''):
    if role not in ROLE_NAMES:raise ValueError('Unknown role key')
    if len(password)<12:raise ValueError('Password must be at least 12 characters')
    db=db_open();dept_id=None
    if department:
        row=db.execute('SELECT department_id FROM departments WHERE name=? COLLATE NOCASE',(department,)).fetchone()
        if not row:db.close();raise ValueError('Unknown department')
        dept_id=row['department_id']
    if role in DEPT and not dept_id:db.close();raise ValueError('Department roles require --department')
    digest,salt=hash_password(password)
    db.execute('INSERT INTO users(username,display_name,email,password_hash,password_salt,role_key,assigned_department_id,must_reset_password) VALUES(?,?,?,?,?,?,?,0)',(username,name,email,digest,salt,role,dept_id));db.commit();db.close()
def init_db():
    db=db_open();db.executescript((ROOT/'database'/'schema.sql').read_text());db.executescript((ROOT/'database'/'seed.sql').read_text())
    db.executescript('''CREATE TABLE IF NOT EXISTS sessions(session_id INTEGER PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,csrf_hash TEXT NOT NULL,user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,expires_at INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);''')
    db.execute('DELETE FROM sessions WHERE expires_at < ?',(int(time.time()),))
    day=date.today().isoformat();shift=db.execute('SELECT shift_id FROM shifts WHERE is_active=1 ORDER BY shift_id LIMIT 1').fetchone()
    if shift:db.execute("INSERT OR IGNORE INTO attendance_submissions(report_date,department_id,shift_id,status,is_current) SELECT ?,department_id,?,'draft',1 FROM departments WHERE is_active=1",(day,shift['shift_id']))
    for typ,key in [('daily_absent','YD_DAILY_ABSENT_TO'),('canteen','YD_CANTEEN_TO')]:
        addresses={v.strip().lower() for v in os.environ.get(key,'').split(',') if v.strip()}
        db.execute("UPDATE email_recipient_settings SET is_active=0 WHERE report_type=? AND recipient_kind='to'",(typ,))
        for address in addresses:
            db.execute('INSERT OR IGNORE INTO email_recipient_settings(report_type,recipient_kind,email_address) VALUES(?,?,?)',(typ,'to',address))
            db.execute("UPDATE email_recipient_settings SET is_active=1 WHERE report_type=? AND recipient_kind='to' AND lower(email_address)=?",(typ,address))
    cc_addresses={v.strip().lower() for v in os.environ.get('YD_REPORT_CC','').split(',') if v.strip()}
    for typ in ('daily_absent','canteen'):
        db.execute("UPDATE email_recipient_settings SET is_active=0 WHERE report_type=? AND recipient_kind='cc'",(typ,))
        for address in cc_addresses:
            db.execute('INSERT OR IGNORE INTO email_recipient_settings(report_type,recipient_kind,email_address) VALUES(?,?,?)',(typ,'cc',address))
            db.execute("UPDATE email_recipient_settings SET is_active=1 WHERE report_type=? AND recipient_kind='cc' AND lower(email_address)=?",(typ,address))
    db.commit();db.close()
def ensure_admin():
    db=db_open();exists=db.execute("SELECT 1 FROM users WHERE role_key='system_administrator' LIMIT 1").fetchone();db.close()
    if exists:return
    username=os.environ.get('YD_ADMIN_USERNAME','admin');password=os.environ.get('YD_BOOTSTRAP_ADMIN_PASSWORD') or (secrets.token_urlsafe(18)+'!A9')
    create_user(username,'YD System Administrator',password,'system_administrator')
    print('\n=== Initial YD administrator account ===\nUsername:',username,'\nPassword:',password,'\nSave this password now; it is not written to disk.\n=======================================\n',flush=True)
def status_label(s):return {'draft':'Pending','submitted':'Submitted by Department','supervisor_review':'Under Supervisor Review','returned_for_correction':'Returned for Correction','supervisor_approved':'Supervisor Approved','submitted_to_hr':'Submitted to YD HR','hr_review':'Under YD HR Review','finalized':'Finalized','included_in_report':'Included in Final Report'}.get(s,s)

class Handler(BaseHTTPRequestHandler):
    server_version='YDSystem/1.0'
    def log_message(self,fmt,*args):print('%s - %s'%(self.address_string(),fmt%args))
    def send_json(self,status,data,extra=None):
        body=json.dumps(data,ensure_ascii=False).encode();headers={'Content-Type':'application/json; charset=utf-8','Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN','Referrer-Policy':'strict-origin-when-cross-origin','Content-Security-Policy':"default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"}
        if extra:headers.update(extra)
        self.send_response(status)
        for k,v in headers.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def fail(self,status,msg):return self.send_json(status,{'error':msg})
    def read_json(self):
        length=int(self.headers.get('Content-Length','0'))
        if length>1_000_000:raise ValueError('Request too large')
        return json.loads(self.rfile.read(length) or b'{}')
    def cookie_token(self):
        cookie=SimpleCookie(self.headers.get('Cookie',''));m=cookie.get(COOKIE);return m.value if m else None
    def session(self):
        token=self.cookie_token()
        if not token:return None
        th=hashlib.sha256(token.encode()).hexdigest();db=db_open();u=db.execute('SELECT s.csrf_hash,s.expires_at,u.* FROM sessions s JOIN users u USING(user_id) WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1',(th,int(time.time()))).fetchone();db.close()
        return u
    def require(self):
        u=self.session()
        if not u:self.fail(401,'Please sign in again.');return None
        if self.command not in ('GET','HEAD'):
            csrf=self.headers.get('X-CSRF-Token','')
            if not csrf or not hmac.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(),u['csrf_hash']):self.fail(403,'Invalid CSRF token.');return None
        return u
    def allowed(self,u,*roles):
        if u['role_key'] not in roles:self.fail(403,'Your role is not authorized for this action.');return False
        return True
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/healthz':return self.send_json(200,{'status':'ok'})
        if path=='/api/session':
            u=self.session()
            if not u:return self.send_json(200,{'user':None})
            token=self.cookie_token();th=hashlib.sha256(token.encode()).hexdigest();csrf=self.server.csrf.get(th)
            if not csrf:
                csrf=secrets.token_urlsafe(32);self.server.csrf[th]=csrf;db=db_open();db.execute('UPDATE sessions SET csrf_hash=? WHERE token_hash=?',(hashlib.sha256(csrf.encode()).hexdigest(),th));db.commit();db.close()
            return self.send_json(200,{'user':self.user_json(u),'csrfToken':csrf})
        if path.startswith('/api/'):
            u=self.require()
            if not u:return
            if path=='/api/bootstrap':return self.send_json(200,self.bootstrap(u))
            if path=='/api/submissions':return self.send_json(200,{'submissions':self.bootstrap(u)['submissions']})
            if path=='/api/employees':
                if u['role_key']=='canteen_user':return self.fail(403,'Employee information is restricted.')
                return self.send_json(200,{'employees':self.bootstrap(u)['employees']})
            if path=='/api/absences':
                if u['role_key']=='canteen_user':return self.fail(403,'Absence information is restricted.')
                return self.send_json(200,{'absences':self.bootstrap(u)['absences']})
            if path=='/api/canteen':return self.send_json(200,{'requirements':self.bootstrap(u)['canteen']})
            if path=='/api/email-history':
                if u['role_key'] not in READ_ALL:return self.fail(403,'Email history is restricted.')
                return self.send_json(200,{'emails':self.bootstrap(u)['emails']})
            if path=='/api/audit':
                if not self.allowed(u,'system_administrator','yd_hr_coordinator'):return
                db=db_open();rows=db.execute('SELECT audit_id,action,affected_record_type,affected_record_id,created_at,user_id FROM audit_log ORDER BY audit_id DESC LIMIT 500').fetchall();db.close();return self.send_json(200,{'items':[dict(r) for r in rows]})
            return self.fail(404,'API route not found')
        return self.static(path)
    def do_POST(self):
        path=urlparse(self.path).path
        try:data=self.read_json()
        except (ValueError,json.JSONDecodeError):return self.fail(400,'Invalid JSON or request too large.')
        if path=='/api/login':return self.login(data)
        u=self.require()
        if not u:return
        if path=='/api/logout':return self.logout(u)
        if path=='/api/submissions':return self.create_submission(u,data)
        if path.startswith('/api/submissions/') and path.endswith('/workflow'):return self.workflow(u,path.split('/')[-2],data)
        if path=='/api/absences':return self.create_absence(u,data)
        if path=='/api/employees':return self.create_employee(u,data)
        if path=='/api/canteen/adjustments':return self.create_adjustment(u,data)
        if path=='/api/canteen/plan':return self.save_plan(u,data)
        if path=='/api/canteen/finalize':return self.finalize_canteen(u,data)
        if path=='/api/canteen/received':return self.canteen_received(u,data)
        if path=='/api/reports/preview':return self.report_preview(u,data)
        if path=='/api/reports/download':return self.download_report(u,data)
        if path=='/api/reports/send':return self.send_report(u,data)
        if path=='/api/users':return self.create_account(u,data)
        return self.fail(404,'API route not found')
    def static(self,path):
        if path=='/':path='/index.html'
        files={'/index.html','/app.js','/auth.js','/styles.css','/auth.css','/print.css'}
        if path not in files:return self.fail(404,'Not found')
        f=ROOT/path.lstrip('/')
        if not f.is_file():return self.fail(404,'Not found')
        mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[f.suffix]
        b=f.read_bytes();self.send_response(200)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(b)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN','Referrer-Policy':'strict-origin-when-cross-origin','Content-Security-Policy':"default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(b)
    def user_json(self,u):return {'id':u['user_id'],'username':u['username'],'displayName':u['display_name'],'role':u['role_key'],'roleName':ROLE_NAMES[u['role_key']],'departmentId':u['assigned_department_id']}
    def login(self,data):
        name=str(data.get('username','')).strip();password=str(data.get('password',''));db=db_open();u=db.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(name,)).fetchone()
        if not u or not u['is_active'] or (u['locked_until'] and u['locked_until']>now()):
            if u:
                failures=u['failed_login_attempts']+1;locked=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat() if failures>=5 else None
                db.execute('UPDATE users SET failed_login_attempts=?,locked_until=? WHERE user_id=?',(failures,locked,u['user_id']));audit(db,u['user_id'],'Failed login','user',u['user_id'])
            db.commit();db.close();return self.fail(401,'Invalid username or password.')
        digest,_=hash_password(password,bytes.fromhex(u['password_salt']))
        if not hmac.compare_digest(digest,u['password_hash']):
            failures=u['failed_login_attempts']+1;locked=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat() if failures>=5 else None
            db.execute('UPDATE users SET failed_login_attempts=?,locked_until=? WHERE user_id=?',(failures,locked,u['user_id']));audit(db,u['user_id'],'Failed login','user',u['user_id']);db.commit();db.close();return self.fail(401,'Invalid username or password.')
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);th=hashlib.sha256(token.encode()).hexdigest();db.execute('UPDATE users SET failed_login_attempts=0,locked_until=NULL,last_login_at=? WHERE user_id=?',(now(),u['user_id']));db.execute('INSERT INTO sessions(token_hash,csrf_hash,user_id,expires_at) VALUES(?,?,?,?)',(th,hashlib.sha256(csrf.encode()).hexdigest(),u['user_id'],int(time.time())+SESSION_SECONDS));audit(db,u['user_id'],'Login','user',u['user_id']);db.commit();db.close();self.server.csrf[th]=csrf
        secure='; Secure' if os.environ.get('YD_COOKIE_SECURE')=='1' else '';cookie=f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}{secure}'
        return self.send_json(200,{'user':self.user_json(u),'csrfToken':csrf},{'Set-Cookie':cookie})
    def logout(self,u):
        token=self.cookie_token();th=hashlib.sha256(token.encode()).hexdigest() if token else '';db=db_open();db.execute('DELETE FROM sessions WHERE token_hash=?',(th,));audit(db,u['user_id'],'Logout','user',u['user_id']);db.commit();db.close();self.server.csrf.pop(th,None)
        return self.send_json(200,{'ok':True},{'Set-Cookie':f'{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
    def bootstrap(self,u):
        db=db_open();role=u['role_key'];dept=u['assigned_department_id'];dept_filter=' AND department_id=?' if role in DEPT else '';params=(dept,) if role in DEPT else ()
        departments=[dict(r) for r in db.execute('SELECT department_id,name,department_type,is_active FROM departments WHERE is_active=1'+dept_filter+' ORDER BY name',params)]
        shifts=[dict(r) for r in db.execute('SELECT shift_id,name,starts_at,ends_at,crosses_midnight FROM shifts WHERE is_active=1 ORDER BY shift_id')]
        subs=[]
        if role!='canteen_user':
            q='''SELECT a.*,d.name department,s.name shift,u.display_name submitted_name FROM attendance_submissions a JOIN departments d USING(department_id) JOIN shifts s USING(shift_id) LEFT JOIN users u ON u.user_id=a.prepared_by WHERE a.is_current=1'''
            par=()
            if role in DEPT:q+=' AND a.department_id=?';par=(dept,)
            for r in db.execute(q+' ORDER BY a.report_date DESC,d.name,s.shift_id',par):subs.append({'id':f"s{r['submission_id']}",'dbId':r['submission_id'],'date':r['report_date'],'department':r['department'],'shift':r['shift'],'status':status_label(r['status']),'strength':r['total_strength'],'present':r['present_count'],'absent':r['absent_count'],'ot':r['overtime_count']+r['lunch_overtime_count']+r['tea_overtime_count']+r['dinner_overtime_count'],'time':r['submitted_at'] or '—','submittedBy':r['submitted_name'] or '—','supervisor':'Approved' if r['supervisor_approved_at'] else ('Pending' if r['status'] in ('submitted','supervisor_review','returned_for_correction') else '—'),'hr':'Finalized' if r['status']=='finalized' else '—'})
        employees=[]
        if role!='canteen_user':
            q='''SELECT e.employee_no,e.employee_name,d.name department,e.section_name,s.name shift,e.employment_category,e.meal_preference,e.is_active FROM employees e JOIN departments d USING(department_id) LEFT JOIN shifts s ON s.shift_id=e.current_shift_id''';par=()
            if role in DEPT:q+=' WHERE e.department_id=?';par=(dept,)
            employees=[{'id':r['employee_no'],'name':r['employee_name'],'department':r['department'],'section':r['section_name'],'shift':r['shift'] or '','category':r['employment_category'],'meal':'Vegetarian' if r['meal_preference']=='vegetarian' else 'Non-vegetarian','active':bool(r['is_active'])} for r in db.execute(q+' ORDER BY e.employee_name',par)]
        absences=[]
        if role in READ_ALL|DEPT:
            q='''SELECT a.absence_id,a.employee_no,e.employee_name,d.name department,s.name shift,a.absence_start_date,a.absent_days,a.informed_status,a.absence_reason,a.follow_up_status FROM absence_records a JOIN employees e USING(employee_no) JOIN departments d USING(department_id) LEFT JOIN shifts s USING(shift_id) WHERE a.record_status='active' ''';par=()
            if role in DEPT:q+=' AND a.department_id=?';par=(dept,)
            for r in db.execute(q+' ORDER BY a.absent_days DESC',par):
                x={'id':f"a{r['absence_id']}",'employee':r['employee_no'],'name':r['employee_name'],'department':r['department'],'shift':r['shift'] or '','start':r['absence_start_date'],'days':r['absent_days'],'informed':'Informed' if r['informed_status']=='informed' else 'Not informed','status':r['follow_up_status'].replace('_',' ').title()}
                if role in READ_ALL:x['reason']=r['absence_reason']
                absences.append(x)
        adjustments=[]
        if role in HR:adjustments=[dict(r) for r in db.execute('''SELECT c.category_name category,a.adjustment_amount amount,a.adjustment_reason reason,u.display_name by,a.adjusted_at time FROM canteen_adjustments a JOIN canteen_requirements r USING(requirement_id) JOIN canteen_categories c USING(category_id) JOIN users u ON u.user_id=a.adjusted_by ORDER BY a.adjustment_id DESC LIMIT 100''')]
        emails=[]
        if role in READ_ALL:emails=[dict(r) for r in db.execute('SELECT email_id,report_type,report_date,version_no,subject,to_recipients,sent_at,delivery_status FROM email_history ORDER BY email_id DESC LIMIT 100')]
        canteen=[dict(r) for r in db.execute('''SELECT r.report_date,r.plan_type,c.category_name,r.system_calculated_quantity,r.manual_adjustment_quantity,r.final_approved_quantity,r.status,r.version_no FROM canteen_requirements r JOIN canteen_categories c USING(category_id) ORDER BY r.report_date DESC,c.sort_order LIMIT 300''')]
        public=self.user_json(u);public['departmentName']=departments[0]['name'] if role in DEPT and departments else None
        result={'user':public,'date':date.today().isoformat(),'planDate':(date.today()+timedelta(days=1)).isoformat(),'departments':departments,'shifts':shifts,'submissions':subs,'employees':employees,'absences':absences,'adjustments':adjustments,'emails':emails,'canteen':canteen,'reportVersion':1,'attendance':[]}
        db.close();return result
    def create_submission(self,u,data):
        role=u['role_key']
        if role not in HR|DEPT:return self.fail(403,'Your role cannot submit attendance.')
        try:
            day=date.fromisoformat(str(data['date'])).isoformat()
            if day>date.today().isoformat() and os.environ.get('YD_ALLOW_FUTURE_DATES')!='1':return self.fail(400,'Future attendance dates are not allowed.')
            db=db_open();d=db.execute('SELECT department_id FROM departments WHERE name=? COLLATE NOCASE AND is_active=1',(data['department'],)).fetchone()
            if not d:db.close();return self.fail(400,'Unknown department.')
            if role in DEPT and d['department_id']!=u['assigned_department_id']:db.close();return self.fail(403,'Only assigned department data is allowed.')
            sh=db.execute('SELECT shift_id FROM shifts WHERE name=? COLLATE NOCASE AND is_active=1',(data['shift'],)).fetchone()
            if not sh:db.close();return self.fail(400,'Unknown shift.')
            vals={k:int(data.get(k,0)) for k in ('strength','present','absent','ot','leave')}
            absent_ids=list(dict.fromkeys(data.get('absentEmployees') or []));overtime_ids=list(dict.fromkeys(data.get('overtimeEmployees') or []))
            if any(v<0 for v in vals.values()) or vals['present']+vals['absent']+vals['leave']>vals['strength']:db.close();return self.fail(400,'Attendance totals are invalid or exceed strength.')
            if absent_ids and len(absent_ids)!=vals['absent']:db.close();return self.fail(400,'Absent employee selection must match the absent total.')
            if overtime_ids and len(overtime_ids)!=vals['ot']:db.close();return self.fail(400,'Overtime employee selection must match the overtime total.')
            if set(absent_ids)&set(overtime_ids):db.close();return self.fail(400,'An employee cannot be both absent and assigned overtime.')
            for empno in absent_ids+overtime_ids:
                employee=db.execute('SELECT 1 FROM employees WHERE employee_no=? AND department_id=? AND is_active=1',(empno,d['department_id'])).fetchone()
                if not employee:db.close();return self.fail(400,'Selected employee is inactive or outside this department.')
            dup=db.execute('SELECT submission_id,status FROM attendance_submissions WHERE report_date=? AND department_id=? AND shift_id=? AND is_current=1',(day,d['department_id'],sh['shift_id'])).fetchone()
            status='draft' if data.get('status')=='Draft' else 'submitted'
            values=(status,vals['strength'],vals['present'],vals['absent'],vals['ot'],vals['leave'],int(data.get('other',0)),int(data.get('lunchOT',0)),int(data.get('teaOT',0)),int(data.get('dinnerOT',0)),str(data.get('remarks',''))[:4000],u['user_id'],now() if status=='submitted' else None)
            if dup and dup['status'] in ('draft','returned_for_correction'):
                sid=dup['submission_id'];db.execute('''UPDATE attendance_submissions SET status=?,total_strength=?,present_count=?,absent_count=?,overtime_count=?,approved_leave_count=?,other_attendance_count=?,lunch_overtime_count=?,tea_overtime_count=?,dinner_overtime_count=?,remarks=?,prepared_by=?,submitted_at=?,updated_at=CURRENT_TIMESTAMP WHERE submission_id=?''',values+(sid,));old=dup['status']
            else:
                revision=1;revision_of=None
                if dup:
                    why=str(data.get('revisionReason','')).strip()
                    if role not in HR or not why:db.close();return self.fail(409,'This daily shift already has a submission. An authorized revision reason is required.')
                    db.execute('UPDATE attendance_submissions SET is_current=0 WHERE submission_id=?',(dup['submission_id'],));revision=db.execute('SELECT COALESCE(MAX(revision_no),0)+1 n FROM attendance_submissions WHERE report_date=? AND department_id=? AND shift_id=?',(day,d['department_id'],sh['shift_id'])).fetchone()['n'];revision_of=dup['submission_id'];old=dup['status']
                else:old=None
                cur=db.execute('''INSERT INTO attendance_submissions(report_date,department_id,shift_id,revision_no,revision_of,status,total_strength,present_count,absent_count,overtime_count,approved_leave_count,other_attendance_count,lunch_overtime_count,tea_overtime_count,dinner_overtime_count,remarks,prepared_by,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(day,d['department_id'],sh['shift_id'],revision,revision_of,*values));sid=cur.lastrowid
            db.execute('DELETE FROM attendance_entries WHERE submission_id=?',(sid,));db.execute('DELETE FROM overtime_entries WHERE submission_id=?',(sid,))
            for empno in absent_ids:db.execute("INSERT INTO attendance_entries(submission_id,employee_no,attendance_status) VALUES(?,?,'absent')",(sid,empno))
            for empno in overtime_ids:db.execute("INSERT INTO overtime_entries(submission_id,employee_no,overtime_type,overtime_hours,supervisor_status) VALUES(?,?,'other',0,'pending')",(sid,empno))
            audit(db,u['user_id'],'Saved attendance submission','attendance_submission',sid,{'status':old} if old else None,dict(data),str(data.get('revisionReason','')))
            db.execute('INSERT INTO workflow_history(submission_id,actor_user_id,action,to_status,reason) VALUES(?,?,?,?,?)',(sid,u['user_id'],'saved_draft' if status=='draft' else 'submitted',status,str(data.get('revisionReason',''))))
            self.recalculate_canteen(db,day);db.commit();db.close();return self.send_json(201,{'ok':True,'submissionId':sid,'status':status_label(status)})
        except (KeyError,ValueError,TypeError,sqlite3.IntegrityError):return self.fail(400,'Required fields, calendar date, or attendance counts are invalid.')
    def recalculate_canteen(self,db,day):
        rows=db.execute('''SELECT a.present_count,a.overtime_count,a.lunch_overtime_count,a.tea_overtime_count,a.dinner_overtime_count,s.name shift FROM attendance_submissions a JOIN shifts s USING(shift_id) WHERE a.report_date=? AND a.is_current=1 AND a.status IN ('submitted','supervisor_review','supervisor_approved','submitted_to_hr','hr_review','finalized','included_in_report')''',(day,)).fetchall()
        present=sum(r['present_count'] for r in rows);lunch_ot=sum(r['lunch_overtime_count'] for r in rows);tea_ot=sum(r['tea_overtime_count'] for r in rows);dinner_ot=sum(r['dinner_overtime_count'] for r in rows);night=sum(r['present_count'] for r in rows if r['shift'] in ('Evening','Night'))
        emp=db.execute("SELECT COUNT(*) total,SUM(CASE WHEN meal_preference='vegetarian' THEN 1 ELSE 0 END) veg FROM employees WHERE is_active=1").fetchone();ratio=(emp['veg'] or 0)/emp['total'] if emp and emp['total'] else 0
        calculations={'TEA_0700':present,'TEA_1000':present,'TEA_1500':present,'TEA_1600':present+tea_ot,'LUNCH':present+lunch_ot,'DINNER':night+dinner_ot,'SNACK_TEA':tea_ot,'VEGETARIAN':round(present*ratio),'SPECIAL_MEAL':0}
        for code,quantity in calculations.items():
            cat=db.execute('SELECT category_id FROM canteen_categories WHERE category_code=?',(code,)).fetchone()
            if not cat:continue
            old=db.execute("SELECT * FROM canteen_requirements WHERE report_date=? AND plan_type='actual' AND category_id=? AND shift_id IS NULL ORDER BY version_no DESC LIMIT 1",(day,cat['category_id'])).fetchone()
            if old and old['status']!='finalized':db.execute('UPDATE canteen_requirements SET system_calculated_quantity=?,final_approved_quantity=?+manual_adjustment_quantity WHERE requirement_id=?',(quantity,quantity,old['requirement_id']))
            else:
                version=(old['version_no']+1) if old else 1
                db.execute("INSERT INTO canteen_requirements(report_date,plan_type,category_id,system_calculated_quantity,final_approved_quantity,status,version_no,generated_at) VALUES(?,'actual',?,?,?,'under_review',?,?)",(day,cat['category_id'],quantity,quantity,version,now()))
    def workflow(self,u,sid,data):
        if u['role_key'] not in ('department_supervisor','yd_hr_coordinator','system_administrator'):return self.fail(403,'Supervisor permissions are required.')
        try:sid=int(sid)
        except ValueError:return self.fail(400,'Invalid submission id.')
        action=data.get('action');reason=str(data.get('reason','')).strip();db=db_open();row=db.execute('SELECT * FROM attendance_submissions WHERE submission_id=? AND is_current=1',(sid,)).fetchone()
        if not row:db.close();return self.fail(404,'Submission not found.')
        if u['role_key']=='department_supervisor' and row['department_id']!=u['assigned_department_id']:db.close();return self.fail(403,'This submission is outside your assigned department.')
        if action=='return' and not reason:db.close();return self.fail(400,'A correction reason is required.')
        if action in ('approve','return'):
            new='supervisor_approved' if action=='approve' else 'returned_for_correction'
            db.execute('UPDATE attendance_submissions SET status=?,supervisor_approved_by=?,supervisor_approved_at=?,updated_at=CURRENT_TIMESTAMP WHERE submission_id=?',(new,u['user_id'],now() if action=='approve' else None,sid))
        elif action=='finalize':
            if u['role_key'] not in HR:return db.close() or self.fail(403,'YD HR permissions are required.')
            if row['status']!='supervisor_approved':db.close();return self.fail(409,'Supervisor approval is required first.')
            new='finalized';db.execute('UPDATE attendance_submissions SET status=?,hr_finalized_by=?,hr_finalized_at=?,updated_at=CURRENT_TIMESTAMP WHERE submission_id=?',(new,u['user_id'],now(),sid))
        else:db.close();return self.fail(400,'Unsupported workflow action.')
        audit(db,u['user_id'],action,'attendance_submission',sid,{'status':row['status']},{'status':new},reason);db.execute('INSERT INTO workflow_history(submission_id,actor_user_id,action,from_status,to_status,reason) VALUES(?,?,?,?,?,?)',(sid,u['user_id'],action,row['status'],new,reason));db.commit();db.close();return self.send_json(200,{'ok':True,'status':status_label(new)})
    def create_absence(self,u,data):
        if u['role_key'] not in HR|DEPT:return self.fail(403,'Absence reporting is not permitted for this role.')
        try:
            report=date.fromisoformat(data['reportDate']).isoformat();start=date.fromisoformat(data['startDate']).isoformat();empno=str(data['employeeNo'])
            db=db_open();emp=db.execute('SELECT employee_no,department_id FROM employees WHERE employee_no=? AND is_active=1',(empno,)).fetchone()
            if not emp:db.close();return self.fail(400,'Employee is unknown or inactive.')
            if u['role_key'] in DEPT and emp['department_id']!=u['assigned_department_id']:db.close();return self.fail(403,'Only assigned department employees are allowed.')
            days=max(1,(date.fromisoformat(report)-date.fromisoformat(start)).days+1);reason=str(data.get('reason','')) if u['role_key'] in READ_ALL else ''
            cur=db.execute('''INSERT INTO absence_records(employee_no,department_id,report_date,absence_start_date,expected_return_date,absent_days,informed_status,absence_reason,comment,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)''',(empno,emp['department_id'],report,start,data.get('expectedReturnDate'),days,'informed' if data.get('informed')=='Informed' else 'not_informed',reason,str(data.get('comment',''))[:2000],u['user_id']))
            audit(db,u['user_id'],'Created absence record','absence',cur.lastrowid);db.commit();db.close();return self.send_json(201,{'ok':True,'absenceId':cur.lastrowid,'absentDays':days})
        except (KeyError,ValueError,TypeError,sqlite3.IntegrityError):return self.fail(400,'Invalid absence fields or calendar date.')
    def create_employee(self,u,data):
        if u['role_key']!='system_administrator':return self.fail(403,'Administrator permissions are required.')
        try:
            db=db_open();dept=db.execute('SELECT department_id FROM departments WHERE name=? COLLATE NOCASE',(data['department'],)).fetchone();shift=db.execute('SELECT shift_id FROM shifts WHERE name=? COLLATE NOCASE',(data.get('shift',''),)).fetchone()
            if not dept:db.close();return self.fail(400,'Unknown department.')
            pref='vegetarian' if data.get('meal')=='Vegetarian' else 'non_vegetarian'
            db.execute('INSERT INTO employees(employee_no,employee_name,department_id,production_line,section_name,current_shift_id,employment_category,meal_preference) VALUES(?,?,?,?,?,?,?,?)',(data['id'].strip(),data['name'].strip(),dept['department_id'],data.get('line',''),data.get('section',''),shift['shift_id'] if shift else None,data.get('category','permanent'),pref));audit(db,u['user_id'],'Created employee','employee',data['id']);db.commit();db.close();return self.send_json(201,{'ok':True})
        except (KeyError,ValueError,sqlite3.IntegrityError):return self.fail(409,'Employee number is duplicated or data is invalid.')
    def create_adjustment(self,u,data):
        if u['role_key'] not in HR:return self.fail(403,'YD HR permissions are required.')
        try:amount=int(data['amount']);category=str(data['category']);reason=str(data['reason']).strip();day=date.fromisoformat(data.get('date',date.today().isoformat())).isoformat()
        except (KeyError,ValueError,TypeError):return self.fail(400,'Invalid adjustment fields.')
        if amount==0 or not reason:return self.fail(400,'A non-zero adjustment and reason are required.')
        db=db_open();cat=db.execute('SELECT category_id FROM canteen_categories WHERE category_name=?',(category,)).fetchone()
        if not cat:db.close();return self.fail(400,'Unknown canteen category.')
        r=db.execute("SELECT * FROM canteen_requirements WHERE report_date=? AND plan_type='actual' AND category_id=? AND shift_id IS NULL ORDER BY version_no DESC LIMIT 1",(day,cat['category_id'])).fetchone()
        if not r:
            cur=db.execute("INSERT INTO canteen_requirements(report_date,plan_type,category_id,status) VALUES(?,'actual',?,'under_review')",(day,cat['category_id']));rid=cur.lastrowid;system=0;previous=0
        elif r['status']=='finalized':
            version=r['version_no']+1;system=r['system_calculated_quantity'];previous=0
            cur=db.execute("INSERT INTO canteen_requirements(report_date,plan_type,category_id,system_calculated_quantity,status,version_no) VALUES(?,'actual',?,?,'under_review',?)",(day,cat['category_id'],system,version));rid=cur.lastrowid
        else:rid=r['requirement_id'];system=r['system_calculated_quantity'];previous=r['manual_adjustment_quantity']
        if system+previous+amount<0:db.close();return self.fail(400,'Final quantity cannot be negative.')
        db.execute('INSERT INTO canteen_adjustments(requirement_id,adjustment_amount,adjustment_reason,reason_category,adjusted_by) VALUES(?,?,?,?,?)',(rid,amount,reason,data.get('reasonCategory','other'),u['user_id']));db.execute('UPDATE canteen_requirements SET manual_adjustment_quantity=manual_adjustment_quantity+?,final_approved_quantity=system_calculated_quantity+manual_adjustment_quantity+? WHERE requirement_id=?',(amount,amount,rid));audit(db,u['user_id'],'Manual canteen adjustment','canteen_requirement',rid,None,{'amount':amount,'reason':reason},reason);db.commit();db.close();return self.send_json(201,{'ok':True})
    def save_plan(self,u,data):
        if u['role_key'] not in HR:return self.fail(403,'YD HR permissions are required.')
        try:day=date.fromisoformat(data.get('date',date.today().isoformat())).isoformat();quantities={str(k):int(v) for k,v in data['quantities'].items()}
        except (KeyError,ValueError,TypeError,AttributeError):return self.fail(400,'Invalid plan date or quantities.')
        if not quantities or any(v<0 for v in quantities.values()):return self.fail(400,'Plan quantities must be zero or more.')
        db=db_open()
        for name,q in quantities.items():
            cat=db.execute('SELECT category_id FROM canteen_categories WHERE category_name=?',(name,)).fetchone()
            if not cat:db.close();return self.fail(400,'Unknown canteen category: '+name)
            cur=db.execute("SELECT requirement_id,status,version_no FROM canteen_requirements WHERE report_date=? AND plan_type='planned' AND category_id=? AND shift_id IS NULL ORDER BY version_no DESC LIMIT 1",(day,cat['category_id'])).fetchone()
            if cur and cur['status']!='finalized':db.execute('UPDATE canteen_requirements SET system_calculated_quantity=?,final_approved_quantity=? WHERE requirement_id=?',(q,q,cur['requirement_id']))
            else:
                ver=(cur['version_no']+1) if cur else 1
                db.execute("INSERT INTO canteen_requirements(report_date,plan_type,category_id,system_calculated_quantity,final_approved_quantity,status,version_no,generated_by,generated_at) VALUES(?,'planned',?,?,?,'draft',?,?,?)",(day,cat['category_id'],q,q,ver,u['user_id'],now()))
        audit(db,u['user_id'],'Updated canteen plan','canteen_plan',day,None,quantities);db.commit();db.close();return self.send_json(200,{'ok':True})
    def finalize_canteen(self,u,data):
        if u['role_key'] not in HR:return self.fail(403,'YD HR permissions are required.')
        try:day=date.fromisoformat(data.get('date',date.today().isoformat())).isoformat()
        except ValueError:return self.fail(400,'Invalid report date.')
        plan_day=(date.fromisoformat(day)+timedelta(days=1)).isoformat();db=db_open();categories=db.execute('SELECT COUNT(*) n FROM canteen_categories WHERE is_active=1').fetchone()['n']
        for plan_type,required_day in (('actual',day),('planned',plan_day)):
            count=db.execute('SELECT COUNT(DISTINCT category_id) n FROM canteen_requirements WHERE report_date=? AND plan_type=?',(required_day,plan_type)).fetchone()['n']
            if count<categories:db.close();return self.fail(409,f'{plan_type.title()} canteen requirements for {required_day} are incomplete.')
            db.execute("UPDATE canteen_requirements SET status='finalized',approved_by=?,approved_at=? WHERE report_date=? AND plan_type=? AND status IN ('draft','under_review')",(u['user_id'],now(),required_day,plan_type))
        audit(db,u['user_id'],'Finalized canteen report','canteen_report',day);db.commit();db.close();return self.send_json(200,{'ok':True})
    def canteen_received(self,u,data):
        if u['role_key']!='canteen_user':return self.fail(403,'Canteen receipt confirmation requires the Canteen role.')
        try:day=date.fromisoformat(data.get('date',date.today().isoformat())).isoformat()
        except ValueError:return self.fail(400,'Invalid report date.')
        plan_day=(date.fromisoformat(day)+timedelta(days=1)).isoformat();db=db_open();cur=db.execute("UPDATE canteen_requirements SET status='received' WHERE ((report_date=? AND plan_type='actual') OR (report_date=? AND plan_type='planned')) AND status='finalized'",(day,plan_day))
        if not cur.rowcount:db.close();return self.fail(404,'No finalized canteen report is available to receive.')
        audit(db,u['user_id'],'Canteen confirmed receipt','canteen_report',day,None,{'status':'received'});db.commit();db.close();return self.send_json(200,{'ok':True})
    def create_account(self,u,data):
        if u['role_key']!='system_administrator':return self.fail(403,'Administrator permissions are required.')
        try:create_user(str(data['username']),str(data['displayName']),str(data['password']),data['role'],data.get('department'),data.get('email',''));return self.send_json(201,{'ok':True})
        except (KeyError,ValueError,sqlite3.IntegrityError) as e:return self.fail(400,str(e))
    def report_csv(self,db,kind,day,version,provisional):
        plan_day=(date.fromisoformat(day)+timedelta(days=1)).isoformat();stream=io.StringIO();writer=csv.writer(stream)
        if kind=='daily_absent':
            writer.writerow(['Employee number','Employee name','Department','Shift','Absence start','Absent days','Informed status','Reason'])
            rows=db.execute("SELECT e.employee_no,e.employee_name,d.name,s.name,a.absence_start_date,a.absent_days,a.informed_status,a.absence_reason FROM absence_records a JOIN employees e USING(employee_no) JOIN departments d USING(department_id) LEFT JOIN shifts s USING(shift_id) WHERE a.report_date=? AND a.record_status='active' ORDER BY d.name,e.employee_name",(day,))
            writer.writerows([tuple(r) for r in rows]);prefix='YD_Daily_Absent_Summary'
        else:
            writer.writerow(['Report date','Plan type','Category','Shift','System calculated','Manual adjustment','Final quantity','Status','Version'])
            rows=db.execute("""WITH ranked AS (SELECT r.*,c.category_name,c.sort_order,COALESCE(s.name,'') shift_name,ROW_NUMBER() OVER(PARTITION BY r.report_date,r.plan_type,r.category_id,r.shift_id ORDER BY r.version_no DESC,r.requirement_id DESC) rn FROM canteen_requirements r JOIN canteen_categories c USING(category_id) LEFT JOIN shifts s USING(shift_id) WHERE (r.report_date=? AND r.plan_type='actual') OR (r.report_date=? AND r.plan_type='planned')) SELECT report_date,plan_type,category_name,shift_name,system_calculated_quantity,manual_adjustment_quantity,final_approved_quantity,status,version_no FROM ranked WHERE rn=1 ORDER BY plan_type,sort_order,shift_name""",(day,plan_day))
            writer.writerows([tuple(r) for r in rows]);prefix='YD_Canteen_Requirements'
        filename=f"{prefix}_{day}_v{version}{'_PROVISIONAL' if provisional else ''}.csv"
        return filename,stream.getvalue()
    def download_report(self,u,data):
        if u['role_key'] not in HR:return self.fail(403,'Report downloads are restricted to YD HR.')
        kind=data.get('type');provisional=bool(data.get('provisional'))
        if kind not in ('daily_absent','canteen'):return self.fail(400,'Unsupported report type.')
        try:day=date.fromisoformat(data.get('date',date.today().isoformat())).isoformat()
        except (ValueError,TypeError):return self.fail(400,'Invalid report date.')
        db=db_open();version=db.execute('SELECT COALESCE(MAX(version_no),0)+1 n FROM report_versions WHERE report_type=? AND report_date=?',(kind,day)).fetchone()['n'];filename,content=self.report_csv(db,kind,day,version,provisional);db.close()
        return self.send_json(200,{'filename':filename,'csv':content,'version':version})
    def canteen_missing(self,db,day,plan_day):
        categories={r['category_id']:r['category_name'] for r in db.execute('SELECT category_id,category_name FROM canteen_categories WHERE is_active=1')};missing=[]
        for typ,req_day in (('actual',day),('planned',plan_day)):
            rows=db.execute("""WITH ranked AS (SELECT category_id,status,ROW_NUMBER() OVER(PARTITION BY category_id,shift_id ORDER BY version_no DESC,requirement_id DESC) rn FROM canteen_requirements WHERE report_date=? AND plan_type=?) SELECT category_id,MIN(CASE WHEN status IN ('finalized','sent','received') THEN 1 ELSE 0 END) complete FROM ranked WHERE rn=1 GROUP BY category_id""",(req_day,typ)).fetchall()
            complete={r['category_id'] for r in rows if r['complete']==1}
            missing.extend([f'{typ.title()} {name}' for cid,name in categories.items() if cid not in complete])
        return missing
    def report_preview(self,u,data):
        if u['role_key'] not in HR:return self.fail(403,'YD HR permissions are required.')
        kind=data.get('type');day=data.get('date',date.today().isoformat())
        if kind not in ('daily_absent','canteen'):return self.fail(400,'Unsupported report type.')
        try:day=date.fromisoformat(day).isoformat()
        except ValueError:return self.fail(400,'Invalid report date.')
        db=db_open();missing=[];missing_quantities=[]
        if kind=='daily_absent':
            expected={r['name'] for r in db.execute('SELECT name FROM departments WHERE is_active=1')};done={r['department'] for r in db.execute("SELECT d.name department FROM attendance_submissions a JOIN departments d USING(department_id) WHERE a.report_date=? AND a.is_current=1 AND a.status IN ('supervisor_approved','submitted_to_hr','hr_review','finalized','included_in_report')",(day,))};missing=sorted(expected-done)
        else:
            next_day=(date.fromisoformat(day)+timedelta(days=1)).isoformat();missing_quantities=self.canteen_missing(db,day,next_day)
        rec=db.execute('SELECT recipient_kind,email_address FROM email_recipient_settings WHERE report_type=? AND is_active=1',(kind,)).fetchall();db.close()
        return self.send_json(200,{'type':kind,'date':day,'missingDepartments':missing,'missingQuantities':missing_quantities,'recipients':{key:[r['email_address'] for r in rec if r['recipient_kind']==key] for key in ('to','cc','bcc')},'ready':not missing and not missing_quantities})
    def send_report(self,u,data):
        if u['role_key'] not in HR:return self.fail(403,'YD HR permissions are required.')
        kind=data.get('type');subject=str(data.get('subject','')).strip();body=str(data.get('body','')).strip();provisional=bool(data.get('provisional'));reason=str(data.get('reason','')).strip()
        try:day=date.fromisoformat(data.get('date',date.today().isoformat())).isoformat()
        except (ValueError,TypeError):return self.fail(400,'Invalid report date.')
        if kind not in ('daily_absent','canteen') or not subject or not body:return self.fail(400,'Report type, subject, and body are required.')
        if provisional and not reason:return self.fail(400,'Provisional reports require an authorized reason.')
        db=db_open()
        missing=[];missing_quantities=[];plan_day=(date.fromisoformat(day)+timedelta(days=1)).isoformat()
        if kind=='daily_absent':
            required={x['name'] for x in db.execute('SELECT name FROM departments WHERE is_active=1')};approved={x['name'] for x in db.execute("SELECT d.name FROM attendance_submissions a JOIN departments d USING(department_id) WHERE a.report_date=? AND a.is_current=1 AND a.status IN ('supervisor_approved','submitted_to_hr','hr_review','finalized','included_in_report')",(day,))};missing=sorted(required-approved)
        else:missing_quantities=self.canteen_missing(db,day,plan_day)
        if (missing or missing_quantities) and not provisional:db.close();return self.send_json(409,{'error':'Required departments or finalized canteen quantities are still missing. Use provisional delivery only with an authorized reason, or wait.','missingDepartments':missing,'missingQuantities':missing_quantities})
        allowed={r['email_address'].lower() for r in db.execute('SELECT email_address FROM email_recipient_settings WHERE report_type=? AND is_active=1',(kind,))}
        to=[x.strip() for x in str(data.get('to','')).split(',') if x.strip()];cc=[x.strip() for x in str(data.get('cc','')).split(',') if x.strip()]
        if not to or any(x.lower() not in allowed for x in to+cc):db.close();return self.fail(400,'Recipients must be configured and authorized in the recipient settings.')
        if provisional and not subject.upper().startswith('PROVISIONAL:'):subject='PROVISIONAL: '+subject
        version=db.execute('SELECT COALESCE(MAX(version_no),0)+1 n FROM report_versions WHERE report_type=? AND report_date=?',(kind,day)).fetchone()['n']
        label='Provisional' if provisional else ('Corrected' if version>1 else 'Final')
        cur=db.execute("INSERT INTO report_versions(report_type,report_date,version_no,version_label,is_provisional,status,generated_by) VALUES(?,?,?,?,?,'generated',?)",(kind,day,version,label,int(provisional),u['user_id']));version_id=cur.lastrowid
        filename,csv_content=self.report_csv(db,kind,day,version,provisional)
        email=EmailMessage();email['Subject']=subject;email['From']=os.environ.get('YD_SMTP_FROM','');email['To']=', '.join(to)
        if cc:email['Cc']=', '.join(cc)
        email.set_content(body+'\n\nReport version: '+str(version)+(' · PROVISIONAL' if provisional else ''))
        email.add_attachment(csv_content.encode('utf-8-sig'),maintype='text',subtype='csv',filename=filename)
        status='sent';failure='';smtp_host=os.environ.get('YD_SMTP_HOST','');smtp_from=os.environ.get('YD_SMTP_FROM','')
        try:
            if not smtp_host or not smtp_from:raise RuntimeError('SMTP is not configured (set YD_SMTP_HOST and YD_SMTP_FROM).')
            with smtplib.SMTP(smtp_host,int(os.environ.get('YD_SMTP_PORT','587')),timeout=20) as client:
                client.ehlo();client.starttls(context=ssl.create_default_context());client.ehlo()
                if os.environ.get('YD_SMTP_USER'):client.login(os.environ['YD_SMTP_USER'],os.environ.get('YD_SMTP_PASSWORD',''))
                client.send_message(email,to_addrs=to+cc)
        except Exception as exc:status='not_configured' if not smtp_host else 'failed';failure=str(exc)[:500]
        cur=db.execute('''INSERT INTO email_history(report_version_id,report_type,report_date,version_no,subject,body,to_recipients,cc_recipients,attachment_name,sender_user_id,delivery_status,failure_reason,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(version_id,kind,day,version,subject,body,','.join(to),','.join(cc),filename,u['user_id'],status,failure,now() if status=='sent' else None))
        db.execute("UPDATE report_versions SET status=? WHERE report_version_id=?",('sent' if status=='sent' else 'generated',version_id));audit(db,u['user_id'],'Sent report email' if status=='sent' else 'Email delivery failed',kind,cur.lastrowid,None,{'version':version,'recipients':to+cc,'status':status},reason);db.commit();db.close()
        if status!='sent':return self.send_json(502,{'error':'Email delivery failed and was recorded in history. '+failure,'emailId':cur.lastrowid})
        return self.send_json(200,{'ok':True,'emailId':cur.lastrowid,'version':version,'attachment':filename,'status':'sent'})


def main():
    import getpass
    p=argparse.ArgumentParser(description='YD Attendance Reporting System')
    p.add_argument('--create-user',action='store_true',help='Create a user interactively')
    p.add_argument('--reset-password',action='store_true',help='Reset a local account password')
    p.add_argument('--username');p.add_argument('--display-name');p.add_argument('--role',choices=ROLE_NAMES);p.add_argument('--department');p.add_argument('--email',default='')
    a=p.parse_args();init_db()
    if a.create_user:
        create_user(a.username or input('Username: ').strip(),a.display_name or input('Display name: ').strip(),getpass.getpass('Password (12+ characters): '),a.role or input('Role key: ').strip(),a.department or input('Department (blank if not applicable): ').strip() or None,a.email)
        print('Account created.');return
    if a.reset_password:
        username=a.username or input('Username to reset: ').strip();password=getpass.getpass('New password (12+ characters): ')
        if len(password)<12:raise SystemExit('Password must be at least 12 characters.')
        digest,salt=hash_password(password);db=db_open();row=db.execute('SELECT user_id FROM users WHERE username=? COLLATE NOCASE',(username,)).fetchone()
        if not row:raise SystemExit('Account not found.')
        db.execute('UPDATE users SET password_hash=?,password_salt=?,failed_login_attempts=0,locked_until=NULL WHERE user_id=?',(digest,salt,row['user_id']));db.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],));db.commit();db.close()
        print('Password reset and existing sessions revoked.');return
    ensure_admin()
    class Server(ThreadingHTTPServer):pass
    server=Server((os.environ.get('HOST','0.0.0.0'),int(os.environ.get('PORT','4173'))),Handler);server.csrf={}
    print(f'YD Attendance System running at http://{server.server_address[0]}:{server.server_address[1]}',flush=True);print('SQLite DB:',DB_PATH,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nShutting down.')
    finally:server.server_close()
if __name__=='__main__':main()
