import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import server as app


class BackendApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(cls.temp.name) / 'test.sqlite3'
        app.init_db()
        app.create_user('line-user', 'Line User', 'A-secure-demo-Password-1', 'department_user', 'Appearance')
        app.create_user('line-supervisor', 'Line Supervisor', 'A-secure-demo-Password-2', 'department_supervisor', 'Appearance')
        app.create_user('store-user', 'Store User', 'A-secure-demo-Password-4', 'department_user', 'Stores')
        app.create_user('hr-user', 'HR User', 'A-secure-demo-Password-5', 'yd_hr_coordinator')
        app.create_user('canteen-user', 'Canteen User', 'A-secure-demo-Password-3', 'canteen_user')
        db=app.db_open();db.execute("INSERT INTO email_recipient_settings(report_type,recipient_kind,email_address) VALUES('daily_absent','to','reports@example.test')");db.commit();db.close()
        cls.httpd = app.ThreadHTTPServer if hasattr(app, 'ThreadHTTPServer') else None
        from http.server import ThreadingHTTPServer
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.httpd.csrf = {}
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(timeout=2); cls.temp.cleanup()

    def client(self):
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(self, client, path, data=None, csrf=None):
        headers = {'Content-Type': 'application/json'}
        if csrf: headers['X-CSRF-Token'] = csrf
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with client.open(req) as response: return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def login(self, username, password):
        client=self.client();code,data=self.request(client,'/api/login',{'username':username,'password':password})
        self.assertEqual(code,200);return client,data['csrfToken']

    def test_auth_scoped_bootstrap_and_attendance_submission(self):
        client,csrf=self.login('line-user','A-secure-demo-Password-1')
        status,boot=self.request(client,'/api/bootstrap');self.assertEqual(status,200)
        self.assertEqual([x['name'] for x in boot['departments']],['Appearance'])
        self.assertTrue(all(x['department']=='Appearance' for x in boot['submissions']))
        record={'date':date.today().isoformat(),'department':'Appearance','shift':'Day 1','strength':5,'present':4,'absent':1,'leave':0,'ot':0,'status':'Submitted'}
        status,_=self.request(client,'/api/submissions',record,csrf);self.assertEqual(status,201)
        bad={**record,'department':'Stores'}
        status,_=self.request(client,'/api/submissions',bad,csrf);self.assertEqual(status,403)
        over={**record,'strength':2,'present':4,'absent':1}
        status,_=self.request(client,'/api/submissions',over,csrf);self.assertEqual(status,400)

    def test_supervisor_cannot_approve_another_department_submission(self):
        store_client,store_csrf=self.login('store-user','A-secure-demo-Password-4')
        record={'date':date.today().isoformat(),'department':'Stores','shift':'Day 1','strength':3,'present':3,'absent':0,'leave':0,'ot':0,'status':'Submitted'}
        status,created=self.request(store_client,'/api/submissions',record,store_csrf);self.assertEqual(status,201)
        supervisor,csrf=self.login('line-supervisor','A-secure-demo-Password-2')
        status,_=self.request(supervisor,f"/api/submissions/{created['submissionId']}/workflow",{'action':'approve'},csrf)
        self.assertEqual(status,403)

    def test_report_download_and_smtp_send_enforce_recipient_and_provisional_rules(self):
        client,csrf=self.login('hr-user','A-secure-demo-Password-5')
        status,preview=self.request(client,'/api/reports/preview',{'type':'daily_absent','date':date.today().isoformat()},csrf)
        self.assertEqual(status,200);self.assertFalse(preview['ready']);self.assertIn('reports@example.test',preview['recipients']['to'])
        status,file=self.request(client,'/api/reports/download',{'type':'daily_absent','date':date.today().isoformat()},csrf)
        self.assertEqual(status,200);self.assertTrue(file['filename'].endswith('.csv'));self.assertIn('Employee number',file['csv'])
        payload={'type':'daily_absent','date':date.today().isoformat(),'to':'not-authorized@example.test','subject':'Daily absence','body':'Report','provisional':True,'reason':'Test'}
        status,_=self.request(client,'/api/reports/send',payload,csrf);self.assertEqual(status,400)
        payload['to']='reports@example.test';payload['provisional']=False;payload['reason']=''
        status,_=self.request(client,'/api/reports/send',payload,csrf);self.assertEqual(status,409)
        payload.update(provisional=True,reason='Authorized test of provisional mail flow')
        with patch.dict(os.environ,{'YD_SMTP_HOST':'smtp.example.test','YD_SMTP_PORT':'587','YD_SMTP_FROM':'reports@example.test'}):
            with patch.object(app.smtplib,'SMTP') as smtp:
                smtp.return_value.__enter__.return_value.send_message.return_value={}
                status,result=self.request(client,'/api/reports/send',payload,csrf)
        self.assertEqual(status,200);self.assertEqual(result['status'],'sent');self.assertIn('PROVISIONAL',result['attachment'])
        smtp.assert_called_once()

    def test_canteen_plan_finalization_adjustment_versions_and_receipt(self):
        hr,csrf=self.login('hr-user','A-secure-demo-Password-5')
        db=app.db_open();categories=[r['category_name'] for r in db.execute('SELECT category_name FROM canteen_categories WHERE is_active=1 ORDER BY sort_order')];db.close()
        quantities={name:25 for name in categories};tomorrow=(date.today().fromordinal(date.today().toordinal()+1)).isoformat()
        status,_=self.request(hr,'/api/canteen/plan',{'date':tomorrow,'quantities':quantities},csrf);self.assertEqual(status,200)
        status,_=self.request(hr,'/api/canteen/finalize',{'date':date.today().isoformat()},csrf);self.assertEqual(status,200)
        status,preview=self.request(hr,'/api/reports/preview',{'type':'canteen','date':date.today().isoformat()},csrf)
        self.assertEqual(status,200);self.assertTrue(preview['ready'])
        status,_=self.request(hr,'/api/canteen/adjustments',{'date':date.today().isoformat(),'category':categories[0],'amount':2,'reason':'Test versioned change'},csrf);self.assertEqual(status,201)
        status,preview=self.request(hr,'/api/reports/preview',{'type':'canteen','date':date.today().isoformat()},csrf)
        self.assertEqual(status,200);self.assertFalse(preview['ready']);self.assertTrue(any('Actual '+categories[0] in x for x in preview['missingQuantities']))
        db=app.db_open();history=db.execute("SELECT status,version_no,final_approved_quantity FROM canteen_requirements r JOIN canteen_categories c USING(category_id) WHERE c.category_name=? AND r.report_date=? AND r.plan_type='actual' ORDER BY version_no",(categories[0],date.today().isoformat())).fetchall();db.close()
        self.assertEqual([row['version_no'] for row in history],[1,2]);self.assertEqual(history[0]['status'],'finalized');self.assertEqual(history[1]['final_approved_quantity'],history[0]['final_approved_quantity']+2)
        status,_=self.request(hr,'/api/canteen/finalize',{'date':date.today().isoformat()},csrf);self.assertEqual(status,200)
        canteen,can_csrf=self.login('canteen-user','A-secure-demo-Password-3')
        status,_=self.request(canteen,'/api/canteen/received',{'date':date.today().isoformat()},can_csrf);self.assertEqual(status,200)

    def test_canteen_role_receives_no_employee_or_absence_data(self):
        client,csrf=self.login('canteen-user','A-secure-demo-Password-3')
        status,boot=self.request(client,'/api/bootstrap');self.assertEqual(status,200)
        self.assertEqual(boot['employees'],[]);self.assertEqual(boot['absences'],[]);self.assertEqual(boot['submissions'],[])
        status,_=self.request(client,'/api/employees');self.assertEqual(status,403)
        status,_=self.request(client,'/api/absences');self.assertEqual(status,403)

    def test_state_changes_require_csrf(self):
        client,_=self.login('line-user','A-secure-demo-Password-1')
        status,_=self.request(client,'/api/submissions',{'date':date.today().isoformat()})
        self.assertEqual(status,403)

    def test_server_protects_non_public_source_files(self):
        client=self.client();status,_=self.request(client,'/server.py');self.assertEqual(status,404)
        status,_=self.request(client,'/database/schema.sql');self.assertEqual(status,404)


if __name__=='__main__': unittest.main()
