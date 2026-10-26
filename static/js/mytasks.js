/* 我的任务页：当前登录用户创建的执行计划（信息同执行总览 + 删除按钮） */
mountSidebar('mytasks');

const state = { kw: '', tasks: [] };

function taskStatus(t) {
  if (t.running) return 'running';
  if (!t.enabled) return 'paused';
  return t.last_status || 'never';
}

function bars(hist) {
  if (!hist || !hist.length) return '<span class="cell muted">无记录</span>';
  const okc = hist.filter(v => v === 'success').length;
  const rate = Math.round(okc / hist.length * 100);
  return `<div class="bars">${hist.map(v => `<i class="${v}"></i>`).join('')}<span class="rate">${rate}%</span></div>`;
}

function filtered() {
  if (!state.kw) return state.tasks;
  const k = state.kw.toLowerCase();
  return state.tasks.filter(t => [t.name, t.owner, t.grp].some(x => (x || '').toLowerCase().includes(k)));
}

function colorLog(text) {
  return esc(text || '(无日志)').split('\n').map(l => {
    if (l.includes('[ERROR]') || l.includes('Traceback')) return `<span class="ln-err">${l}</span>`;
    if (l.includes('[WARN]')) return `<span class="ln-warn">${l}</span>`;
    return l;
  }).join('\n');
}

function renderRows() {
  const list = filtered();
  const box = document.getElementById('rows');
  if (!list.length) {
    box.innerHTML = `<div class="empty"><b>还没有你创建的执行计划</b>点击右上角「新建执行计划」创建一个；历史存量任务归属管理员</div>`;
  } else {
    box.innerHTML = list.map(t => {
      const st = taskStatus(t);
      const stInfo = STATUS[st === 'paused' ? 'never' : st] || { cls: 'paused', txt: '已停用' };
      const stTxt = st === 'paused' ? '已停用' : stInfo.txt;
      return `
      <div class="trow" style="grid-template-columns:minmax(200px,1.5fr) 120px 130px 120px 76px 96px 104px 216px" data-id="${t.id}">
        <div class="tname"><b>${esc(t.name)}</b><span>#${String(t.id).padStart(4,'0')} · 负责人 ${esc(t.owner || '—')}</span></div>
        <div class="cell">${esc(t.grp || '—')}</div>
        <div class="cell">${esc(cronText(t.cron))}</div>
        <div class="cell">${fmtTime(t.last_run_at)}</div>
        <div class="cell mono ${t.last_duration == null ? 'muted' : ''}">${fmtDuration(t.last_duration)}</div>
        <div class="cell"><span class="pill ${st === 'paused' ? 'paused' : stInfo.cls}"><span class="d"></span>${stTxt}</span></div>
        <div class="cell">${bars(t.hist)}</div>
        <div class="ops">
          <button class="op" data-op="edit" data-id="${t.id}">${ICO.edit}编辑</button>
          <button class="op" data-op="run" data-id="${t.id}" ${t.running ? 'disabled' : ''}>${ICO.play}运行</button>
          <button class="op" data-op="log" data-id="${t.id}">${ICO.log}日志</button>
          <button class="op ${t.enabled ? '' : 'toggle off'}" data-op="toggle" data-id="${t.id}" title="启用/停用">${t.enabled ? '停用' : '启用'}</button>
          <button class="op" data-op="del" data-id="${t.id}" data-name="${esc(t.name)}" style="color:var(--err)">${ICO.trash}删除</button>
        </div>
      </div>`;
    }).join('');
  }
  document.getElementById('listSub').textContent = `共 ${list.length} 个我创建的计划`;
}

async function load() {
  try {
    const data = await api('/api/overview?mine=1');
    state.tasks = data.tasks;
    renderRows();
  } catch (e) { toast(e.message, 'err'); }
}

async function runNow(id) {
  try {
    await api(`/api/tasks/${id}/run`, { method: 'POST' });
    toast('已触发手动运行，可在「运行记录」查看实时日志');
    setTimeout(load, 600);
  } catch (e) { toast(e.message, 'err'); }
}

async function toggle(id) {
  const t = state.tasks.find(x => x.id === id);
  try {
    await api(`/api/tasks/${id}/enabled`, { method: 'POST', body: { enabled: !t.enabled } });
    toast(t.enabled ? '已停用' : '已启用');
    load();
  } catch (e) { toast(e.message, 'err'); }
}

async function showLog(id) {
  const r = await api('/api/runs/' + id);
  const st = STATUS[r.status] || { cls: 'never', txt: r.status };
  document.getElementById('logTitle').textContent = `#${r.id} ${r.task_name}`;
  document.getElementById('logPill').className = 'pill ' + st.cls;
  document.getElementById('logPill').innerHTML = `<span class="d"></span>${st.txt} · ${esc(r.start_at || '')}`;
  document.getElementById('logOutputs').innerHTML = (r.outputs || []).length
    ? '<b style="color:var(--text)">产出文件：</b>' + r.outputs.map(p => `<div class="mono" style="word-break:break-all">${esc(p)}</div>`).join('')
    : '';
  document.getElementById('logText').innerHTML = colorLog(r.log);
  document.getElementById('logModal').classList.add('show');
}

async function del(id, name) {
  if (!confirm(`确定删除任务「${name}」？\n删除后不可恢复，其运行记录仍保留在「运行记录」页。`)) return;
  try {
    await api(`/api/tasks/${id}`, { method: 'DELETE' });
    toast('已删除');
    load();
  } catch (e) { toast(e.message, 'err'); }
}

document.getElementById('rows').addEventListener('click', e => {
  const b = e.target.closest('[data-op]'); if (!b) return;
  const id = +b.dataset.id;
  const op = b.dataset.op;
  if (op === 'edit') { location.href = `/console.html?id=${id}`; return; }
  ({ run: runNow, log: showLog, toggle, del }[op])(id, b.dataset.name);
});
document.getElementById('search').addEventListener('input', e => { state.kw = e.target.value.trim(); renderRows(); });
document.getElementById('logClose').onclick = () => document.getElementById('logModal').classList.remove('show');
document.getElementById('logModal').addEventListener('click', e => { if (e.target.id === 'logModal') e.target.classList.remove('show'); });

load();
setInterval(load, 15000);
