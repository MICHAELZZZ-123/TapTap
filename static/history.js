// ── History ─────────────────────────────────────────────
let _historyOpen = false;
async function toggleHistory() {
  _historyOpen = !_historyOpen;
  const panel = document.getElementById('history-panel');
  const list = document.getElementById('history-list');
  const actions = document.getElementById('history-actions');
  const btn = document.getElementById('history-toggle');
  if (!_historyOpen) {
    panel.style.display = 'none';
    btn.innerHTML = '<img src=\"/static/icons/clock.svg\" class=\"icon\"> Past Events';
    _selectMode = false;
    document.getElementById('btn-select-mode').textContent = 'Select';
    document.getElementById('btn-select-all').style.display = 'none';
    document.getElementById('btn-delete-sel').style.display = 'none';
    return;
  }
  btn.innerHTML = '<img src=\"/static/icons/clock.svg\" class=\"icon\"> Past Events ▾';
  const events = await api('GET', '/api/history');
  if (!events.length) {
    list.innerHTML = '<p style=\"color:var(--text-dim);font-size:0.85rem;text-align:center;\">No past events.</p>';
    actions.style.display = 'none';
  } else {
    actions.style.display = 'flex';
    list.innerHTML = events.map(ev => {
      const recur = ev.recurrence;
      const recDisplay = recur === 'none' ? 'once' :
        ['daily','weekly','monthly','yearly'].includes(recur) ? recur :
        recur.replace(':', ' every ') + 's'.replace('ss','s');
      return '<div class=\"event-card\" style=\"opacity:0.7;\">'
        + '<input type=\"checkbox\" class=\"hist-cb\" value=\"' + ev.id + '\" onchange=\"updateSelected()\" style=\"display:none;width:auto;flex-shrink:0;\">'
        + '<div class=\"event-info\">'
        + '<div class=\"event-name\">' + esc(ev.name) + '</div>'
        + '<div class=\"event-meta\"><img src=\"/static/icons/calendar.svg\" class=\"icon\" style=\"width:12px;height:12px;\"> '
        + ev.event_date + ' at ' + ev.event_time
        + ' · ⏰ ' + ev.reminder_min + 'm · ' + recDisplay
        + '</div></div>'
        + '<button class=\"btn btn-danger btn-sm\" onclick=\"deleteOneHist(' + ev.id + ')\" title=\"Delete permanently\">'
        + '<img src=\"/static/icons/trash-white.svg\" class=\"icon\"></button>'
        + '<button class=\"btn btn-primary btn-sm\" onclick=\"reuseEvent(' + ev.id + ')\" title=\"Use as template for a new event\">'
        + '<img src=\"/static/icons/plus-white.svg\" class=\"icon\"> Reuse</button>'
        + '</div>';
    }).join('');
    updateSelected();
  }
  panel.style.display = 'block';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function updateSelected() {
  const cbs = document.querySelectorAll('.hist-cb:checked');
  document.getElementById('selected-count').textContent = cbs.length ? cbs.length + ' selected' : '';
}
let _selectMode = false;
function toggleSelectMode() {
  _selectMode = !_selectMode;
  const btn = document.getElementById('btn-select-mode');
  const btnAll = document.getElementById('btn-select-all');
  const btnDel = document.getElementById('btn-delete-sel');
  const cbs = document.querySelectorAll('.hist-cb');
  if (_selectMode) {
    btn.textContent = 'Cancel';
    btnAll.style.display = '';
    btnDel.style.display = '';
    cbs.forEach(c => c.style.display = '');
  } else {
    btn.textContent = 'Select';
    btnAll.style.display = 'none';
    btnDel.style.display = 'none';
    cbs.forEach(c => { c.checked = false; c.style.display = 'none'; });
    document.getElementById('selected-count').textContent = '';
  }
}
function toggleSelectAll() {
  const cbs = document.querySelectorAll('.hist-cb');
  const allChecked = [...cbs].every(c => c.checked);
  cbs.forEach(c => c.checked = !allChecked);
  document.getElementById('btn-select-all').textContent = allChecked ? 'Select All' : 'Deselect All';
  updateSelected();
}
async function deleteSelected() {
  const cbs = document.querySelectorAll('.hist-cb:checked');
  if (!cbs.length) { showToast('Nothing selected.'); return; }
  if (!confirm('Delete ' + cbs.length + ' event(s) permanently?')) return;
  for (const cb of cbs) {
    await api('DELETE', '/api/events/' + cb.value + '?permanent=1');
  }
  showToast('Deleted ' + cbs.length + ' event(s).');
  toggleHistory(); toggleHistory(); // refresh
}
async function deleteOneHist(id) {
  if (!confirm('Delete this event permanently?')) return;
  await api('DELETE', '/api/events/' + id + '?permanent=1');
  showToast('Deleted.');
  toggleHistory(); toggleHistory();
}

async function reuseEvent(id) {
  const events = await api('GET', '/api/history');
  const ev = events.find(e => e.id === id);
  if (!ev) return;
  document.getElementById('ev-name').value = ev.name;
  document.getElementById('ev-desc').value = ev.description || '';
  document.getElementById('ev-date').value = new Date().toISOString().slice(0, 10);
  document.getElementById('ev-time').value = ev.event_time;
  document.getElementById('ev-reminder').value = ev.reminder_min;
  // Handle custom recurrence like "3:days" or standard like "weekly"
  const rec = ev.recurrence;
  if (['none','daily','weekly','monthly','yearly'].includes(rec)) {
    document.getElementById('ev-recurrence').value = rec;
    document.getElementById('custom-recur').style.display = 'none';
  } else {
    document.getElementById('ev-recurrence').value = 'custom';
    const parts = rec.split(':');
    document.getElementById('custom-n').value = parts[0] || '2';
    document.getElementById('custom-unit').value = parts[1] || 'days';
    document.getElementById('custom-recur').style.display = 'block';
  }
  document.getElementById('ev-name').focus();
  window.scrollTo({top:0, behavior:'smooth'});
  showToast('Template loaded from history — edit and save.');
}