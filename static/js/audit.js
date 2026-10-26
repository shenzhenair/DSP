/* 操作记录页：人为操作审计日志（仅管理员可访问，后端同样校验） */
mountSidebar('audit');

let page = 1;
const SIZE = 50;

function resBadge(r) {
  const ok = r === '成功';
  return `<span class="pill ${ok ? 'ok' : 'err'}" style="font-size:10.5px">${esc(r || '-')}</span>`;
}

function render(d) {
  const holder = document.getElementById('auditList');
  const rows = d.rows || [];
  if (!rows.length) {
    holder.innerHTML = '<div class="hint" style="color:var(--sub);font-size:12px">暂无操作记录。任何写操作（含登录）发生后会出现在这里。</div>';
  } else {
    const head = `<div style="display:grid;grid-template-columns:150px 110px 130px minmax(0,1fr) 76px 90px;gap:12px;padding:10px 0;border-bottom:1.5px solid var(--line);font-size:11px;color:var(--sub)">
      <span>时间</span><span>操作人</span><span>操作</span><span>对象 / 详情</span><span>结果</span><span>来源 IP</span></div>`;
    holder.innerHTML = head + rows.map(r => `
      <div style="display:grid;grid-template-columns:150px 110px 130px minmax(0,1fr) 76px 90px;gap:12px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line-soft)">
        <span style="font-size:11.6px;color:var(--text-2);white-space:nowrap">${esc(r.at)}</span>
        <span style="font-size:12px;font-weight:600">${esc(r.user_name || '-')}</span>
        <span style="font-size:12px">${esc(r.action)}</span>
        <span style="font-size:11.4px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc([r.target, r.detail].filter(Boolean).join(' · '))}">${esc([r.target, r.detail].filter(Boolean).join(' · ') || '—')}</span>
        ${resBadge(r.result)}
        <span style="font-size:11px;color:var(--sub)">${esc(r.ip || '—')}</span>
      </div>`).join('');
  }
  const total = d.total || 0, maxPage = Math.max(1, Math.ceil(total / SIZE));
  document.getElementById('pageInfo').textContent = `共 ${total} 条 · 第 ${page}/${maxPage} 页`;
  document.getElementById('btnPrev').disabled = page <= 1;
  document.getElementById('btnNext').disabled = page >= maxPage;
}

async function load() {
  try {
    const u = document.getElementById('userFilter').value;
    const q = `?page=${page}&size=${SIZE}` + (u ? `&user=${encodeURIComponent(u)}` : '');
    render(await api('/api/audit' + q));
  } catch (e) {
    document.getElementById('auditList').innerHTML =
      `<div class="hint" style="color:#dc2626;font-size:12px">${esc(e.message)}</div>`;
  }
}

async function initFilter() {
  try {
    const r = await api('/api/users');
    const sel = document.getElementById('userFilter');
    const names = [...new Set((r.users || []).map(u => u.name).filter(Boolean))];
    sel.innerHTML = '<option value="">全部操作人</option>' +
      names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  } catch (e) { /* 非管理员看不了用户列表，忽略 */ }
}

document.getElementById('userFilter').onchange = () => { page = 1; load(); };
document.getElementById('btnRefresh').onclick = load;
document.getElementById('btnPrev').onclick = () => { if (page > 1) { page--; load(); } };
document.getElementById('btnNext').onclick = () => { page++; load(); };
initFilter();
load();
