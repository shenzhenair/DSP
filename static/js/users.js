/* 权限管理页：用户列表 + 角色调整（仅管理员可访问，后端同样校验） */
mountSidebar('users');

const ROLE_LBL = { admin: '管理员', senior: '高级用户', member: '普通成员', pending: '无权限' };
const ROLE_HINT = {
  admin: '全部模块 + 权限管理，可操作/删除所有任务与数据源',
  senior: '普通用户权限 + 运行/启停所有任务、编辑所有数据源',
  member: '工作台各模块；仅能运行/启停/编辑/删除自己创建的任务与数据源',
  pending: '不可见任何模块'
};

function roleBadge(r) {
  const cls = r === 'admin' ? 'pill ok' : r === 'senior' ? 'pill ok' : r === 'member' ? 'pill run' : 'pill wait';
  if (r === 'senior') return `<span class="${cls}" style="font-size:11px;background:#fff7e6;color:#b26a00">${ROLE_LBL[r] || r}</span>`;
  return `<span class="${cls}" style="font-size:11px">${ROLE_LBL[r] || r}</span>`;
}

function render(users, selfOpenId) {
  const holder = document.getElementById('userList');
  if (!users.length) {
    holder.innerHTML = '<div class="hint" style="color:var(--sub);font-size:12px">暂无用户。有人扫码登录后会出现在这里。</div>';
    return;
  }
  holder.innerHTML = users.map(u => {
    const isSelf = u.open_id === selfOpenId;
    const av = u.avatar_url
      ? `<img src="${esc(u.avatar_url)}" alt="" style="width:26px;height:26px;border-radius:8px;object-fit:cover">`
      : `<span class="user-av" style="width:26px;height:26px;font-size:12px">${esc((u.name || '用')[0])}</span>`;
    return `
    <div style="display:grid;grid-template-columns:minmax(200px,1.4fr) minmax(0,1fr) 150px 110px 110px;gap:12px;align-items:center;padding:11px 0;border-bottom:1px solid var(--line-soft)">
      <div style="display:flex;align-items:center;gap:10px;min-width:0">
        ${av}
        <div style="min-width:0">
          <div style="font-size:12.8px;font-weight:600;display:flex;gap:6px;align-items:center">
            ${esc(u.name || '未知')} ${isSelf ? '<span class="pill" style="font-size:10px">我</span>' : ''}
          </div>
          <div style="font-size:10.8px;color:var(--sub)" title="${esc(u.open_id)}">${esc(u.user_id || u.open_id)}</div>
        </div>
      </div>
      <div style="font-size:11.6px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis" title="${esc(u.email || '')}">${esc(u.email || '—')}</div>
      <div style="font-size:11.4px;color:var(--text-2)">${esc(u.last_login_at || '—')}</div>
      <div style="font-size:11.4px;color:var(--text-2)">${u.login_count || 0} 次</div>
      <div style="display:flex;align-items:center;gap:8px">
        <select class="inp sel" data-open="${esc(u.open_id)}" ${isSelf ? 'disabled title="不能修改自己的角色"' : ''} style="padding:5px 8px;font-size:11.8px">
          ${['admin', 'senior', 'member', 'pending'].map(r =>
            `<option value="${r}" ${u.role === r ? 'selected' : ''}>${ROLE_LBL[r]}</option>`).join('')}
        </select>
        ${roleBadge(u.role)}
      </div>
    </div>`;
  }).join('');
  holder.insertAdjacentHTML('afterbegin',
    `<div style="display:grid;grid-template-columns:minmax(200px,1.4fr) minmax(0,1fr) 150px 110px 110px;gap:12px;padding:10px 0;border-bottom:1.5px solid var(--line);font-size:11px;color:var(--sub)">
       <span>用户</span><span>邮箱</span><span>最近登录</span><span>登录次数</span><span>角色</span>
     </div>`);

  holder.querySelectorAll('select[data-open]').forEach(sel => {
    sel.onchange = async () => {
      const open_id = sel.dataset.open, role = sel.value;
      try {
        await api('/api/users/role', { method: 'POST', body: { open_id, role } });
        toast(`已更新为「${ROLE_LBL[role]}」`);
        load();
      } catch (e) {
        toast(`保存失败：${e.message}`, 'err');
        load();
      }
    };
  });
}

async function load() {
  try {
    const [r, me] = await Promise.all([
      api('/api/users'),
      fetch('/api/auth/me', { cache: 'no-store' }).then(x => x.json())
    ]);
    render(r.users || [], (me.user || {}).open_id);
  } catch (e) {
    document.getElementById('userList').innerHTML =
      `<div class="hint" style="color:#dc2626;font-size:12px">${esc(e.message)}</div>`;
  }
}

document.getElementById('btnRefresh').onclick = load;
load();
