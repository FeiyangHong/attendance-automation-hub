const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
const state = { year: new Date().getFullYear(), month: new Date().getMonth() + 1, selectedDate: '', activeJobId: '', showCalendarTimes: false };

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrf;
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  if (response.status === 401) { location.href = '/login'; throw new Error('请重新登录'); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `请求失败 (${response.status})`);
  return payload;
}

function toast(message, bad = false) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.style.background = bad ? '#743243' : '#28466e';
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 5000);
}

function setStatus(id, text, level = '') {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = text;
  element.className = level;
  if (id === 'today-arrangement') element.classList.add('multiline');
}

function formatPlan(plan) {
  if (!plan || (!plan.clock_in && !plan.clock_out)) return '未设置';
  return [plan.clock_in ? `上 ${plan.clock_in}` : '', plan.clock_out ? `下 ${plan.clock_out}` : ''].filter(Boolean).join(' / ');
}

function formatTaskResult(value) {
  if (value === undefined || value === null || value === '') return { text: '--', level: '' };
  const code = Number(value);
  const labels = {
    0: '成功 (0)',
    267009: '正在运行 (267009)',
    267010: '已停用 (267010)',
    267011: '尚未运行 (267011)',
  };
  return {
    text: labels[code] || String(value),
    level: code === 0 ? 'good' : code >= 267008 && code <= 267015 ? 'warn' : 'bad',
  };
}

function renderMorningProgress(plan, todayDate) {
  const container = document.getElementById('daily-task-progress');
  if (!plan?.date) {
    container.textContent = '尚无每日任务执行记录。';
    container.className = 'empty-state';
    return;
  }

  const attempts = Number.isFinite(Number(plan.attempts)) ? Number(plan.attempts) : 0;
  const isToday = plan.date === todayDate;
  const description = plan.message || '暂无执行说明。';
  const updatedAt = plan.updated_at ? new Date(plan.updated_at).toLocaleString() : '--';
  const values = [
    isToday ? '今日自动上班' : `${plan.date} 自动上班`,
    plan.status || '--',
    `尝试 ${attempts} 次 · ${description}`,
    updatedAt,
  ];
  container.className = 'job-row';
  container.replaceChildren();
  values.forEach((value, index) => {
    const span = document.createElement('span');
    span.textContent = value;
    if (index === 2) span.className = 'job-message';
    container.append(span);
  });
}

async function refreshStatus() {
  try {
    const data = await api('/api/status');
    const device = data.device || {};
    setStatus('device-state', device.state === 'device' ? `${device.udid} · 已连接` : device.state || '未知', device.state === 'device' ? 'good' : device.state === 'disabled' ? 'warn' : 'bad');
    setStatus('appium-state', data.appium?.listening ? '运行中' : '未运行', data.appium?.listening ? 'good' : 'warn');
    const task = data.task || {};
    setStatus('task-state', task.Exists ? `${task.State} · ${task.NextRunTime || '无下次时间'}` : '未安装', task.Exists && task.Enabled ? 'good' : 'warn');
    setStatus('next-wake', task.NextRunTime || '无');
    const nextExecution = data.next_execution || {};
    setStatus('next-execution', nextExecution.date ? `${nextExecution.date} · ${nextExecution.label || ''}` : '无');
    const morningPlan = data.morning_plan || {};
    const planIsToday = morningPlan.date === data.today?.date;
    const planText = planIsToday
      ? (morningPlan.target_time || (morningPlan.status === 'skipped' ? '今日跳过' : '尚未生成'))
      : '尚未生成';
    setStatus('morning-plan', planText, planIsToday && morningPlan.target_time ? 'good' : '');
    const taskResult = formatTaskResult(task.LastTaskResult);
    setStatus('last-task-result', taskResult.text, taskResult.level);
    const tailscale = data.tailscale || {};
    setStatus('tailscale-state', tailscale.online ? (tailscale.dns_name || '在线') : tailscale.installed ? '离线' : '未安装', tailscale.online ? 'good' : 'warn');
    const calendar = data.today?.calendar || {};
    const calendarText = `${calendar.label || '--'} · ${calendar.reason || ''}`;
    setStatus('today-arrangement', `${calendarText}\n单日计划：${formatPlan(data.today?.plan)}`, calendar.should_run ? 'good' : 'warn');
    document.getElementById('updated-at').textContent = new Date(data.timestamp).toLocaleString();
    renderHistory(data.recent_attendance || []);
    renderMorningProgress(morningPlan, data.today?.date || '');
    renderActiveJob(data.active_job);
  } catch (error) {
    ['device-state', 'appium-state', 'tailscale-state', 'task-state', 'next-wake', 'next-execution', 'morning-plan', 'last-task-result', 'today-arrangement']
      .forEach(id => setStatus(id, '读取失败', 'bad'));
    const dailyProgress = document.getElementById('daily-task-progress');
    dailyProgress.className = 'empty-state';
    dailyProgress.textContent = `状态读取失败：${error.message}`;
    toast(error.message, true);
  }
}

function renderHistory(records) {
  const body = document.getElementById('history-body');
  body.replaceChildren();
  if (!records.length) {
    const row = body.insertRow(); const cell = row.insertCell(); cell.colSpan = 4; cell.textContent = '还没有历史记录。'; cell.className = 'muted'; return;
  }
  records.forEach(record => {
    const row = body.insertRow();
    [record.attendance_date, record.clock_in_time || '--', record.clock_out_time || '--', `${record.clock_in_status || '--'} / ${record.clock_out_status || '--'}`].forEach(value => { const cell = row.insertCell(); cell.textContent = value; });
  });
}

function renderActiveJob(job) {
  const container = document.getElementById('active-job');
  const cancel = document.getElementById('cancel-job');
  if (!job) { container.textContent = '当前没有执行中的任务。'; container.className = 'empty-state'; cancel.hidden = true; state.activeJobId = ''; return; }
  state.activeJobId = job.id;
  container.className = 'job-row';
  container.replaceChildren();
  [job.kind, job.status, job.message || '等待执行'].forEach((value, index) => { const span = document.createElement('span'); span.textContent = value; if (index === 2) span.className = 'job-message'; container.append(span); });
  cancel.hidden = false;
}

async function refreshJobs() {
  try {
    const data = await api('/api/jobs?limit=8');
    const list = document.getElementById('job-history'); list.replaceChildren();
    data.jobs.forEach(job => {
      const row = document.createElement('div'); row.className = 'job-row';
      const detail = job.failure_category ? `[${job.failure_category}] ${job.message || ''}` : job.message || '--';
      const values = [job.kind, job.status, detail, job.created_at.replace('T', ' ')];
      values.forEach((value, index) => { const span = document.createElement('span'); span.textContent = value; if (index === 2) span.className = 'job-message'; row.append(span); });
      list.append(row);
    });
  } catch (error) { toast(error.message, true); }
}

async function submitJob(kind) {
  const real = ['clock_in', 'clock_out', 'task_enable', 'task_disable'].includes(kind);
  const labels = { clock_in: '立即执行真实上班打卡？', clock_out: '立即执行真实下班或更新打卡？', dry_run_clock_in: '开始上班安全测试？极速打卡仍可能被飞书触发。', dry_run_clock_out: '开始下班安全测试？极速打卡仍可能被飞书触发。', diagnostic: '运行只读环境诊断？', scrcpy_start: '在打卡电脑上启动手机画面？', task_enable: '启用替代仓库的每日上班任务？', task_disable: '暂停替代仓库的每日上班任务？' };
  if (!confirm(labels[kind] || `执行 ${kind}？`)) return;
  try {
    const result = await api(`/api/jobs/${kind}`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ confirm: real, parameters: {} }) });
    toast(result.duplicate ? '相同请求已存在，未重复创建。' : '任务已加入队列。');
    await refreshStatus(); await refreshJobs();
  } catch (error) { toast(error.message, true); }
}

async function loadCalendar() {
  try {
    const data = await api(`/api/calendar/${state.year}/${state.month}`);
    document.getElementById('calendar-title').textContent = `${state.year} 年 ${state.month} 月`;
    const grid = document.getElementById('calendar-grid'); grid.replaceChildren(); grid.classList.toggle('show-times', state.showCalendarTimes);
    const firstOffset = data.days.length ? data.days[0].weekday : 0;
    for (let index = 0; index < firstOffset; index += 1) { const blank = document.createElement('div'); blank.className = 'calendar-blank'; grid.append(blank); }
    data.days.forEach(day => {
      const button = document.createElement('button');
      button.className = `calendar-day ${day.should_run ? 'work' : 'rest'} ${day.code}${Object.keys(day.attendance || {}).length ? ' has-record' : ''}`;
      const number = document.createElement('span'); number.className = 'number'; number.textContent = day.day;
      const label = document.createElement('span'); label.className = 'day-label'; label.textContent = day.label;
      button.append(number, label);
      if (state.showCalendarTimes && day.plan?.clock_in) { const line = document.createElement('span'); line.className = 'day-plan'; line.textContent = `计上 ${day.plan.clock_in}`; button.append(line); }
      if (state.showCalendarTimes && day.plan?.clock_out) { const line = document.createElement('span'); line.className = 'day-plan'; line.textContent = `计下 ${day.plan.clock_out}`; button.append(line); }
      if (state.showCalendarTimes && day.attendance?.clock_in_time) { const line = document.createElement('span'); line.className = 'day-plan actual'; line.textContent = `实上 ${day.attendance.clock_in_time}`; button.append(line); }
      if (state.showCalendarTimes && day.attendance?.clock_out_time) { const line = document.createElement('span'); line.className = 'day-plan actual'; line.textContent = `实下 ${day.attendance.clock_out_time}`; button.append(line); }
      button.addEventListener('click', () => openDay(day)); grid.append(button);
    });
  } catch (error) { toast(error.message, true); }
}

function openDay(day) {
  state.selectedDate = day.date;
  document.getElementById('day-title').textContent = day.date;
  document.getElementById('clock-in').value = day.plan?.clock_in || '';
  document.getElementById('clock-out').value = day.plan?.clock_out || '';
  document.getElementById('override-mode').value = day.code === 'manual_workday' ? 'workday' : day.code === 'manual_holiday' ? 'holiday' : 'default';
  const attendance = day.attendance || {};
  document.getElementById('day-actual').textContent = `实际记录：上 ${attendance.clock_in_time || '--'} / 下 ${attendance.clock_out_time || '--'}`;
  document.getElementById('day-dialog').showModal();
}

async function saveDay(event) {
  event.preventDefault();
  try {
    const mode = document.getElementById('override-mode').value;
    const clockIn = document.getElementById('clock-in').value;
    const clockOut = document.getElementById('clock-out').value;
    await api(`/api/calendar/day/${state.selectedDate}/override`, { method: 'PUT', body: JSON.stringify({ mode }) });
    const saved = await api(`/api/plans/${state.selectedDate}`, { method: 'PUT', body: JSON.stringify({ clock_in: clockIn, clock_out: clockOut }) });
    document.getElementById('day-dialog').close();
    const sync = saved.task_sync || {};
    toast(sync.status === 'failed' ? `计划已保存，但系统任务同步失败：${sync.message}` : '当天设置已保存。', sync.status === 'failed');
    await loadCalendar(); await refreshStatus();
  } catch (error) { toast(error.message, true); }
}

async function loadArtifacts() {
  try {
    const data = await api('/api/artifacts'); const list = document.getElementById('artifact-list'); list.replaceChildren();
    data.files.slice(0, 12).forEach(file => { const link = document.createElement('a'); link.href = `/api/artifacts/${encodeURIComponent(file.name)}`; link.target = '_blank'; link.rel = 'noopener'; link.textContent = file.name; list.append(link); });
  } catch (error) { /* optional panel */ }
}

document.querySelectorAll('[data-job]').forEach(button => button.addEventListener('click', () => submitJob(button.dataset.job)));
document.getElementById('refresh-button').addEventListener('click', async () => { await Promise.all([refreshStatus(), refreshJobs(), loadCalendar(), loadArtifacts()]); toast('状态已刷新。'); });
document.getElementById('logout-button').addEventListener('click', async () => { await api('/api/logout', { method: 'POST', body: '{}' }); location.href = '/login'; });
document.getElementById('cancel-job').addEventListener('click', async () => { if (!state.activeJobId || !confirm('停止当前任务？')) return; try { await api(`/api/jobs/${state.activeJobId}/cancel`, { method: 'POST', body: '{}' }); toast('已请求停止任务。'); } catch (error) { toast(error.message, true); } });
document.getElementById('previous-month').addEventListener('click', () => { state.month -= 1; if (state.month < 1) { state.month = 12; state.year -= 1; } loadCalendar(); });
document.getElementById('next-month').addEventListener('click', () => { state.month += 1; if (state.month > 12) { state.month = 1; state.year += 1; } loadCalendar(); });
document.getElementById('toggle-calendar-times').addEventListener('click', event => { state.showCalendarTimes = !state.showCalendarTimes; event.currentTarget.textContent = state.showCalendarTimes ? '隐藏时间' : '显示时间'; event.currentTarget.setAttribute('aria-pressed', String(state.showCalendarTimes)); loadCalendar(); });
document.getElementById('sync-calendar').addEventListener('click', async () => { if (!confirm(`联网同步 ${state.year} 年国务院放假安排？`)) return; try { await api(`/api/calendar/sync/${state.year}`, { method: 'POST', body: '{}' }); toast('日历同步完成。'); await loadCalendar(); } catch (error) { toast(error.message, true); } });
document.getElementById('close-dialog').addEventListener('click', () => document.getElementById('day-dialog').close());
document.getElementById('day-form').addEventListener('submit', saveDay);
document.getElementById('load-log').addEventListener('click', async () => { const value = document.getElementById('log-date').value; if (!value) return; try { const data = await api(`/api/logs/${value}`); document.getElementById('log-output').textContent = data.text || '日志为空。'; } catch (error) { document.getElementById('log-output').textContent = error.message; } });
document.getElementById('log-date').value = new Date().toISOString().slice(0, 10);

Promise.all([refreshStatus(), refreshJobs(), loadCalendar(), loadArtifacts()]);
setInterval(() => { refreshStatus(); refreshJobs(); }, 5000);
