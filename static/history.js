// INVARIANT: index.html loads app.js first; this file intentionally reuses its helpers.
// ── History ─────────────────────────────────────────────
let _historyOpen = false;

async function _renderHistory() {
  const list = document.getElementById('history-list');
  const actions = document.getElementById('history-actions');
  const events = await api('GET', '/api/history');
  if (!events.length) {
    list.innerHTML = '<p style=\"color:var(--text-dim);font-size:0.85rem;text-align:center;\">No past events.</p>';
    actions.style.display = 'none';
    return;
  }
  actions.style.display = 'flex';
  list.innerHTML = events.map(ev => {
    const recDisplay = recurrenceLabel(ev.recurrence, 'once');
    const category = String(ev.category || '');
    const knownCats = ['work','personal','health','other'];
    const categoryDot = category
      ? '<span class=\"cat-dot cat-dot-' + (knownCats.includes(category) ? category : 'custom')
        + '\" title=\"' + esc(category) + '\"></span>'
      : '';
    return '<div class=\"event-card\" style=\"opacity:0.7;\">'
      + '<input type=\"checkbox\" class=\"hist-cb\" value=\"' + ev.id + '\" onchange=\"updateSelected()\" style=\"display:none;width:auto;flex-shrink:0;\">'
      + '<div class=\"event-info\">'
      + '<div class=\"event-name\">' + esc(ev.name) + categoryDot + '</div>'
      + '<div class=\"event-meta\"><img src=\"/static/icons/calendar.svg\" class=\"icon\" style=\"width:12px;height:12px;\"> '
      + esc(ev.event_date) + ' at ' + esc(ev.event_time)
      + ' · <img src=\"/static/icons/clock.svg\" class=\"icon\" style=\"width:12px;height:12px;\"> ' + esc(ev.reminder_min) + 'm · ' + esc(recDisplay)
      + '</div></div>'
      + '<button class=\"btn btn-danger btn-sm\" onclick=\"deleteOneHist(' + ev.id + ')\" title=\"Delete permanently\">'
      + '<img src=\"/static/icons/trash-white.svg\" class=\"icon\"></button>'
      + '<button class=\"btn btn-primary btn-sm\" onclick=\"reuseEvent(' + ev.id + ')\" title=\"Use as template for a new event\">'
      + '<img src=\"/static/icons/plus-white.svg\" class=\"icon\"> Reuse</button>'
      + '</div>';
  }).join('');
  // INVARIANT (UX): A selection-mode refresh must not hide new checkboxes.
  if (_selectMode) {
    document.querySelectorAll('.hist-cb').forEach(cb => { cb.style.display = ''; });
  }
  updateSelected();
}

async function toggleHistory() {
  _historyOpen = !_historyOpen;
  const panel = document.getElementById('history-panel');
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
  await _renderHistory();
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
  let deleted = 0;
  for (const cb of cbs) {
    try { await api('DELETE', '/api/events/' + cb.value + '?permanent=1'); deleted++; }
    catch(e) { /* skip failed deletes */ }
  }
  showToast('Deleted ' + deleted + ' event(s).');
  _renderHistory();
}
async function deleteOneHist(id) {
  if (!confirm('Delete this event permanently?')) return;
  await api('DELETE', '/api/events/' + id + '?permanent=1');
  showToast('Deleted.');
  _renderHistory();
}

async function reuseEvent(id) {
  const events = await api('GET', '/api/history');
  const ev = events.find(e => e.id === id);
  if (!ev) return;
  // INVARIANT: Reuse creates a new event and must never retain a previous edit ID.
  cancelEdit();
  document.getElementById('ev-name').value = ev.name;
  document.getElementById('ev-desc').value = ev.description || '';
  document.getElementById('ev-date').value = localDateValue(calibratedNow());
  document.getElementById('ev-time').value = ev.event_time;
  document.getElementById('ev-reminder').value = ev.reminder_min;
  // Handle custom recurrence like "3:days" or standard like "weekly"
  const rec = String(ev.recurrence || 'none');
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
  const presets = ['', 'work', 'personal', 'health', 'other'];
  const cat = ev.category || '';
  if (presets.includes(cat)) {
    document.getElementById('ev-category').value = cat;
    document.getElementById('custom-cat').style.display = 'none';
  } else if (cat) {
    document.getElementById('ev-category').value = 'custom';
    document.getElementById('custom-cat-name').value = cat;
    document.getElementById('custom-cat').style.display = 'block';
  } else {
    document.getElementById('ev-category').value = '';
    document.getElementById('custom-cat').style.display = 'none';
  }
  updateCategoryIcon();
  document.getElementById('ev-name').focus();
  window.scrollTo({top:0, behavior:'smooth'});
  showToast('Template loaded from history — edit and save.');
}
