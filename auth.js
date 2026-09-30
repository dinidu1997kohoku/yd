(() => {
  const STATE_KEY = 'yd-operations-v1';
  const SNAPSHOT_KEY = 'yd-server-bootstrap-v1';
  const ROLE_LABELS = {
    system_administrator: 'System Administrator', department_user: 'Department User',
    department_supervisor: 'Department Supervisor', yd_hr_coordinator: 'YD HR Coordinator',
    hr_manager: 'HR Manager', canteen_user: 'Canteen User'
  };
  const shell = document.querySelector('.app-shell');
  shell?.classList.add('hidden');

  function showLogin(message = '') {
    let root = document.getElementById('authRoot');
    if (!root) {
      root = document.createElement('div'); root.id = 'authRoot'; document.body.appendChild(root);
    }
    root.innerHTML = `<main class="auth-page"><section class="auth-card"><div class="brand-mark auth-mark">YD</div><p class="eyebrow">YD MANUFACTURING · SECURE WORKSPACE</p><h1>Welcome back</h1><p class="auth-description">Sign in to the Daily Attendance, Absence and Canteen Reporting System.</p><form id="loginForm" autocomplete="on"><label for="loginUsername">Username</label><input id="loginUsername" name="username" autocomplete="username" required autofocus><label for="loginPassword">Password</label><input id="loginPassword" name="password" type="password" autocomplete="current-password" required><div id="loginError" class="auth-error">${message}</div><button class="button primary auth-submit" type="submit">Sign in securely <span>→</span></button></form><small class="auth-footnote">Access is limited by your assigned role and department.</small></section></main>`;
    root.querySelector('#loginForm').addEventListener('submit', async (event) => {
      event.preventDefault(); const form = new FormData(event.currentTarget); const button = event.currentTarget.querySelector('button'); button.disabled = true; button.textContent = 'Signing in…';
      try {
        const response = await fetch('/api/login', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.get('username'),password:form.get('password')})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Sign in failed.');
        window.YD_API_CSRF = data.csrfToken;
        await hydrate(true);
      } catch (error) { button.disabled = false; button.textContent = 'Sign in securely →'; root.querySelector('#loginError').textContent = error.message; }
    });
  }

  function convert(bootstrap) {
    const planned = {'7:00 AM tea':49,'10:00 AM tea':56,'3:00 PM tea':41,'4:00 PM tea':48,'Lunch':420,'Dinner':91,'Snack & tea':80,'Vegetarian meals':130,'Special meals':8};
    let planDate=''; for (const row of bootstrap.canteen || []) if (row.plan_type === 'planned') { planned[row.category_name] = row.final_approved_quantity; planDate=planDate||row.report_date; }
    const emails = (bootstrap.emails || []).map(e => ({
      id:`em${e.email_id}`, type:e.report_type==='daily_absent'?'Daily Absent Summary Report':'Canteen Requirement Report',
      date:e.report_date, version:e.version_no, subject:e.subject, to:e.to_recipients, by:'YD user',
      time:e.sent_at || '', status:e.delivery_status, corrected:false
    }));
    return {
      date:bootstrap.date, role:ROLE_LABELS[bootstrap.user.role] || bootstrap.user.roleName,
      assignedDepartment:bootstrap.user.departmentName || '', deptNames:(bootstrap.departments||[]).map(d=>d.name),
      departments:bootstrap.departments||[], shifts:bootstrap.shifts||[], submissions:bootstrap.submissions||[],
      employees:bootstrap.employees||[], absences:bootstrap.absences||[], adjustments:bootstrap.adjustments||[],
      emails, emailDrafts:[], planned, planDate:planDate||bootstrap.planDate||bootstrap.date, canteen:bootstrap.canteen||[], attendance:[], reportVersion:bootstrap.reportVersion||1, serverManaged:true
    };
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
    if (window.YD_API_CSRF && !['GET','HEAD'].includes((options.method||'GET').toUpperCase())) headers.set('X-CSRF-Token',window.YD_API_CSRF);
    const response = await fetch(path,{...options,headers,credentials:'same-origin'});
    const body = await response.json().catch(()=>({error:'Unexpected server response.'}));
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
    return body;
  }
  window.ydApi = api;
  window.ydSync = async () => {
    const data = await api('/api/bootstrap'); const mapped = convert(data); const serialized=JSON.stringify(mapped);
    localStorage.setItem('yd-server-mode','1');sessionStorage.setItem(STATE_KEY,serialized);sessionStorage.setItem(SNAPSHOT_KEY,serialized);
    location.reload();
  };

  async function hydrate(fromLogin = false) {
    try {
      const sessionResponse = await fetch('/api/session',{credentials:'same-origin',cache:'no-store'});
      if (!sessionResponse.ok) throw new Error('Could not verify session.');
      const session = await sessionResponse.json();
      if (!session.user) { sessionStorage.removeItem(STATE_KEY);sessionStorage.removeItem(SNAPSHOT_KEY);showLogin(); return; }
      window.YD_API_CSRF = session.csrfToken; window.YD_AUTHENTICATED = true;
      const response=await fetch('/api/bootstrap',{credentials:'same-origin',cache:'no-store'});
      if (!response.ok) { if(response.status===401){showLogin('Your session expired. Please sign in again.');return;} throw new Error('Could not load your workspace.'); }
      const data=await response.json(),mapped=convert(data),snapshot=JSON.stringify(mapped);
      const previous=sessionStorage.getItem(SNAPSHOT_KEY);
      if (fromLogin || !previous || previous!==snapshot) {
        localStorage.setItem('yd-server-mode','1');sessionStorage.setItem(STATE_KEY,snapshot);sessionStorage.setItem(SNAPSHOT_KEY,snapshot);
        if (!sessionStorage.getItem('yd-auth-reloaded')) { sessionStorage.setItem('yd-auth-reloaded','1'); location.reload(); return; }
      }
      sessionStorage.removeItem('yd-auth-reloaded');
      const roleSelect=document.getElementById('roleSelect');
      if(roleSelect){roleSelect.value=mapped.role;roleSelect.disabled=true;roleSelect.title='Your role is assigned by an administrator';}
      const name=document.querySelector('.top-user > div:nth-child(2) strong');if(name)name.textContent=session.user.displayName;
      const caption=document.getElementById('roleCaption');if(caption)caption.textContent=mapped.role;
      shell?.classList.remove('hidden');document.getElementById('authRoot')?.remove();
      document.getElementById('logoutButton')?.addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});}catch{}localStorage.removeItem('yd-server-mode');sessionStorage.removeItem(STATE_KEY);sessionStorage.removeItem(SNAPSHOT_KEY);sessionStorage.removeItem('yd-auth-reloaded');location.reload();});
    } catch (error) { showLogin(error.message || 'The application server could not be reached. Start it with python3 server.py.'); }
  }
  hydrate();
})();
