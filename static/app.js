// ── Keyboard shortcuts ───────────────────────────────────
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      saveEvent();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelEdit();
    }
  }
});

// ── Init ─────────────────────────────────────────────────

// Mode switch (light / dark)
(function initMode() {
  const saved = localStorage.getItem('reminder-mode') || 'light';
  document.body.className = saved;
  document.getElementById('mode-light').classList.toggle('active', saved === 'light');
  document.getElementById('mode-dark').classList.toggle('active', saved === 'dark');
})();
function setMode(mode) {
  document.body.className = mode;
  document.getElementById('mode-light').classList.toggle('active', mode === 'light');
  document.getElementById('mode-dark').classList.toggle('active', mode === 'dark');
  localStorage.setItem('reminder-mode', mode);
}

// ── Sound alert ───────────────────────────────────────────
function playBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = 'sine';
    // Two-tone chirp: 880 Hz → 660 Hz
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(660, ctx.currentTime + 0.1);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.4);
  } catch(e) { /* audio not available */ }
}

// Clock calibration — offset between server time and local time (in ms)
let _clockOffset = 0;

async function calibrateClock() {
  try {
    const t0 = Date.now();
    const res = await fetch('/api/time');
    const data = await res.json();
    const t1 = Date.now();
    // Estimate server time at the midpoint of the request round-trip
    const serverNow = data.timestamp * 1000;
    const rtt = t1 - t0;
    const estimatedServerAtT1 = serverNow + rtt / 2;
    _clockOffset = estimatedServerAtT1 - t1;
  } catch(e) {
    // keep current offset on failure
  }
}

function calibratedNow() {
  return new Date(Date.now() + _clockOffset);
}

calibrateClock();                         // calibrate immediately
setInterval(calibrateClock, 5 * 60_000);  // recalibrate every 5 minutes

// ── Lightweight polling — adaptive: 5 s normally, 1 s when reminder is imminent ──
const POLL_SLOW = 5000;      // normal: every 5 s
const POLL_FAST = 1000;      // imminent: every 1 s (second-accurate firing)
const CLOCK_MS = 30000;      // clock display every 30 s (was 1 s)
let _pollActive = true;      // throttled when tab is hidden
let _pollTimer = null;

function _scheduleNextPoll() {
  if (!_pollActive) return;  // tab hidden — stop the chain
  if (_pollTimer) clearTimeout(_pollTimer);
  const cd = document.getElementById('countdown');
  // If a reminder is within 15 seconds, poll every second for precision
  const imminent = cd._nextAt && (new Date(cd._nextAt) - calibratedNow()) < 15000;
  const delay = imminent ? POLL_FAST : POLL_SLOW;
  _pollTimer = setTimeout(() => { pollReminders().then(() => _scheduleNextPoll()); }, delay);
}

pollReminders().then(() => _scheduleNextPoll());
setInterval(updateClock, CLOCK_MS);
setInterval(updateCountdown, 1000);  // countdown ticks every second (local, no server hit)

// Pause polling when the tab is hidden; resume immediately when visible
document.addEventListener('visibilitychange', () => {
  _pollActive = !document.hidden;
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
  if (_pollActive) {
    pollReminders().then(() => _scheduleNextPoll());
  }
});

// Set form defaults to current time (after calibratedNow is defined)
document.getElementById('ev-date').value =
  calibratedNow().toISOString().slice(0, 10);
document.getElementById('ev-time').value =
  String(calibratedNow().getHours()).padStart(2, '0') + ':' +
  String(calibratedNow().getMinutes()).padStart(2, '0');
updateClock();
loadEvents();
updateCountdown();
refreshCategoryOptions();

function updateClock() {
  document.getElementById('clock').textContent =
    calibratedNow().toLocaleTimeString();
}

async function updateCountdown() {
  // Driven by pollReminders — no separate fetch needed
  const el = document.getElementById('countdown');
  if (!el._nextAt) { el.textContent = ''; return; }
  const seconds = Math.round((new Date(el._nextAt) - calibratedNow()) / 1000);
  if (seconds <= 0) { el.textContent = '· now'; return; }
  if (seconds < 60) { el.textContent = '· in ' + seconds + 's'; return; }
  const mins = Math.floor(seconds / 60);
  el.textContent = mins < 60 ? '· in ' + mins + 'm' :
    '· in ' + Math.floor(mins/60) + 'h ' + (mins%60) + 'm';
}

// ── Toast ────────────────────────────────────────────────
function showToast(msg, duration) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = '<img src="/static/icons/info.svg" class="icon"> ' + msg;
  if (duration > 5000) {
    t.title = 'Click to dismiss';
    t.onclick = () => t.remove();
  }
  document.body.appendChild(t);
  setTimeout(() => { if (t.parentNode) t.remove(); }, duration || 3000);
}

// ── Notifications ────────────────────────────────────────
function requestNotifyPermission() {
  if (Notification.permission === 'default') {
    Notification.requestPermission();
  }
}
// Aggressively request permission — try on first click too
document.addEventListener('click', function ask() {
  if (Notification.permission === 'default') Notification.requestPermission();
  if (Notification.permission !== 'default') document.removeEventListener('click', ask);
}, {once: false});
requestNotifyPermission();

function fireReminder(title, body, eventId) {
  playBeep();
  // OS desktop notification
  if (Notification.permission === 'granted') {
    new Notification(title, {
      body: body, icon: '/favicon.ico', requireInteraction: true, tag: 'reminder',
    });
  }
  // Bottom-right popup with ✕ and quick-snooze
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;'
    + 'background:var(--surface);color:var(--text);padding:24px 28px;'
    + 'border-radius:12px;border:3px solid var(--primary);max-width:480px;'
    + 'box-shadow:0 12px 40px rgba(0,0,0,0.35);animation:slideIn 0.3s ease;';
  const snoozeBtn = eventId
    ? '<div style="display:flex;gap:6px;margin-top:10px;">'
      + ['2m','5m','15m'].map(function(m) {
          var mins = parseInt(m);
          return '<button onclick="fetch(\'/api/events/' + eventId + '/snooze?minutes=' + mins + '\',{method:\'POST\'});'
            + 'let p=this.parentElement.parentElement.parentElement.parentElement;p.remove();'
            + 'showToast(\'Snoozed for ' + mins + ' minutes\',3000);" '
            + 'style="background:var(--warning);color:#fff;border:none;padding:5px 12px;'
            + 'border-radius:6px;cursor:pointer;font-weight:600;font-size:0.8rem;">'
            + m + '</button>';
        }).join('')
      + '</div>'
    : '';
  box.innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:start;gap:16px;">'
    + '<div style="flex:1;"><strong style="font-size:1.15rem;">' + esc(title) + '</strong>'
    + '<p style="color:var(--text-dim);margin-top:8px;font-size:1rem;white-space:pre-line;">' + esc(body) + '</p>'
    + snoozeBtn + '</div>'
    + '<button onclick="let p=this.parentElement.parentElement;p.remove();" '
    + 'style="background:none;border:none;color:var(--text-dim);font-size:1.5rem;'
    + 'cursor:pointer;line-height:1;padding:0 0 0 12px;" title="Dismiss"><img src="/static/icons/x.svg" class="icon" style="width:22px;height:22px;"></button>'
    + '</div>';
  document.body.appendChild(box);
  // Flash tab title briefly
  document.title = '🔔 ' + title;
  setTimeout(() => { document.title = 'TapTap'; }, 3000);
}

// ── API helpers ──────────────────────────────────────────
async function api(method, path, data) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (data) opts.body = JSON.stringify(data);
  const res = await fetch(path, opts);
  return res.json();
}

// ── Load events ──────────────────────────────────────────
async function loadEvents() {
  const events = await api('GET', '/api/events');
  const list = document.getElementById('event-list');
  const empty = document.getElementById('empty');
  if (!events.length) {
    list.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  const now = calibratedNow();
  list.innerHTML = events.map(ev => {
    const dt = new Date(ev.event_date + 'T' + ev.event_time);
    const isPast = dt < now;
    const recur = ev.recurrence;
    const recDisplay = recur === 'none' ? '' : ['daily','weekly','monthly','yearly'].includes(recur)
      ? recur : recur.replace(':', ' every ') + 's'.replace('ss','s');
    const recLabel = recDisplay
      ? `<span class="event-tag tag-recur">${recDisplay}</span>` : '';
    const remindLabel = `<span class="event-tag tag-reminder"><img src="/static/icons/clock-white.svg" class="icon" style="width:12px;height:12px;"> ${ev.reminder_min}m</span>`;
    const knownCats = ['work','personal','health','other'];
    const catCSS = knownCats.includes(ev.category) ? ev.category : (ev.category ? 'custom' : '');
    const catClass = catCSS ? ` cat-${catCSS}` : '';
    const catDot = catCSS
      ? `<span class="cat-dot cat-dot-${catCSS}" title="${ev.category}"></span>` : '';
    return `<div class="event-card${isPast ? ' past' : ''}${catClass}">
      <div class="event-icon"><img src="/static/icons/${isPast ? 'check-white' : 'bell'}.svg" style="width:22px;height:22px;"></div>
      <div class="event-info">
        <div class="event-name">${esc(ev.name)}${catDot}${recLabel}${remindLabel}</div>
        <div class="event-meta">
          <img src="/static/icons/calendar.svg" class="icon" style="width:12px;height:12px;"> ${ev.event_date} at ${ev.event_time}
          ${ev.description ? ' — ' + esc(ev.description) : ''}
        </div>
      </div>
      <div class="event-actions">
        <button class="btn btn-secondary btn-sm" onclick="startEdit(${ev.id})" title="Edit this event"><img src="/static/icons/edit-white.svg" class="icon"></button>
        <div class="snooze-group">
          <button class="btn btn-warning btn-sm" onclick="var s=this.nextElementSibling;var v=parseInt(s.value==='other'?s.nextElementSibling.value:s.value)||5;snoozeEvent(${ev.id},Math.max(1,Math.min(1440,v)))" title="Snooze reminders"><img src="/static/icons/bell-white.svg" class="icon"></button>
          <select class="snooze-select" onchange="this.nextElementSibling.style.display=this.value==='other'?'':'none';if(this.value==='other')this.nextElementSibling.focus()">
            <option value="1">1m</option>
            <option value="5" selected>5m</option>
            <option value="10">10m</option>
            <option value="15">15m</option>
            <option value="30">30m</option>
            <option value="other">other…</option>
          </select>
          <input type="number" class="snooze-custom" value="60" min="1" max="1440" style="display:none">
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteEvent(${ev.id})" title="Delete this event permanently"><img src="/static/icons/trash-white.svg" class="icon"></button>
      </div>
    </div>`;
  }).join('');
  }

function esc(s) {
  const el = document.createElement('span');
  el.textContent = s;
  return el.innerHTML;
}

// ── Save (add or edit) ───────────────────────────────────
async function saveEvent() {
  const name = document.getElementById('ev-name').value.trim();
  const date = document.getElementById('ev-date').value;
  const time = document.getElementById('ev-time').value;
  const desc = document.getElementById('ev-desc').value.trim();
  const reminder = document.getElementById('ev-reminder').value.trim() || '15';
  let recurrence = document.getElementById('ev-recurrence').value;
  if (recurrence === 'custom') {
    const n = document.getElementById('custom-n').value || '2';
    const u = document.getElementById('custom-unit').value;
    recurrence = n + ':' + u;
  }
  const editId = document.getElementById('edit-id').value;

  if (!name) { showToast('Please enter an event name.'); return; }

  // Reject events in the past
  const eventDt = new Date(date + 'T' + time);
  if (eventDt < calibratedNow()) {
    const gone = document.createElement('div');
    gone.className = 'toast gone-toast';
    gone.textContent = 'Gone is gone, my friend';
    gone.title = 'Click to dismiss';
    gone.onclick = () => gone.remove();
    document.body.appendChild(gone);
    setTimeout(() => { if (gone.parentNode) gone.remove(); }, 5000);
    return;
  }

  let category = document.getElementById('ev-category').value;
  if (category === 'custom') {
    category = document.getElementById('custom-cat-name').value.trim().toLowerCase() || 'other';
  }
  const data = { name, event_date: date, event_time: time, description: desc, reminder_min: reminder, recurrence, category };

  if (editId) {
    await api('PUT', '/api/events/' + editId, data);
    showToast('Event updated!');
  } else {
    await api('POST', '/api/events', data);
    showToast('Event added!');
  }
  cancelEdit();
  loadEvents();
  refreshCategoryOptions();
}

function startEdit(id) {
  api('GET', '/api/events').then(events => {
    const ev = events.find(e => e.id === id);
    if (!ev) return;
    document.getElementById('edit-id').value = ev.id;
    document.getElementById('ev-name').value = ev.name;
    document.getElementById('ev-date').value = ev.event_date;
    document.getElementById('ev-time').value = ev.event_time;
    document.getElementById('ev-desc').value = ev.description || '';
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
    const presets = ['', 'work', 'personal', 'health', 'other'];
    const cat = ev.category || '';
    if (presets.includes(cat)) {
      document.getElementById('ev-category').value = cat;
      document.getElementById('custom-cat').style.display = 'none';
    } else {
      document.getElementById('ev-category').value = 'custom';
      document.getElementById('custom-cat-name').value = cat;
      document.getElementById('custom-cat').style.display = 'block';
    }
    document.getElementById('form-title').innerHTML = '<img src="/static/icons/edit.svg" class="icon"> Edit Event';
    document.getElementById('btn-save').innerHTML = '<img src="/static/icons/edit.svg" class="icon"> Save Changes';
    document.getElementById('btn-cancel').style.display = '';
    document.getElementById('ev-name').focus();
    window.scrollTo({top:0, behavior:'smooth'});
  });
}

function toggleCustomRecur() {
  const val = document.getElementById('ev-recurrence').value;
  document.getElementById('custom-recur').style.display = val === 'custom' ? 'block' : 'none';
}
function toggleCustomCat() {
  const val = document.getElementById('ev-category').value;
  document.getElementById('custom-cat').style.display = val === 'custom' ? 'block' : 'none';
}

async function refreshCategoryOptions() {
  try {
    const events = await api('GET', '/api/events');
    const presets = ['work','personal','health','other'];
    const seen = new Set(presets);
    const custom = [];
    for (const ev of events) {
      const cat = ev.category;
      if (cat && !seen.has(cat)) {
        seen.add(cat);
        custom.push(cat);
      }
    }
    const sel = document.getElementById('ev-category');
    const currentVal = sel.value === 'custom'
      ? document.getElementById('custom-cat-name').value.trim().toLowerCase() : sel.value;
    // Remove old custom options (keep presets + custom separator)
    while (sel.options.length > 6) sel.remove(sel.options.length - 1);
    // Add custom categories found in events
    for (const cat of custom) {
      const opt = document.createElement('option');
      opt.value = cat;
      opt.textContent = '✨ ' + cat;
      sel.appendChild(opt);
    }
    // Restore selection
    if (custom.includes(currentVal)) {
      sel.value = currentVal;
    } else if (!presets.includes(currentVal) && currentVal && currentVal !== '') {
      // It's a truly new custom category — keep "custom" selected
    } else {
      sel.value = currentVal;
    }
  } catch(e) { /* ignore */ }
}
function cancelEdit() {
  document.getElementById('edit-id').value = '';
  document.getElementById('ev-name').value = '';
  const now = calibratedNow();
  document.getElementById('ev-date').value = now.toISOString().slice(0,10);
  document.getElementById('ev-time').value =
    String(now.getHours()).padStart(2,'0') + ':' +
    String(now.getMinutes()).padStart(2,'0');
  document.getElementById('ev-desc').value = '';
  document.getElementById('ev-reminder').value = '15';
  document.getElementById('ev-recurrence').value = 'none';
  document.getElementById('ev-category').value = '';
  document.getElementById('custom-cat-name').value = '';
  document.getElementById('custom-cat').style.display = 'none';
  document.getElementById('custom-recur').style.display = 'none';
  document.getElementById('form-title').innerHTML = '<img src="/static/icons/plus.svg" class="icon"> Add New Event';
  document.getElementById('btn-save').innerHTML = '<img src="/static/icons/plus-white.svg" class="icon"> Add Event';
  document.getElementById('btn-cancel').style.display = 'none';
}

// ── Delete ──────────────────────────────────────────────
async function deleteEvent(id) {
  const events = await api('GET', '/api/events');
  const ev = events.find(e => e.id === id);
  if (!confirm('Delete "' + (ev ? ev.name : 'event') + '"?')) return;
  await api('DELETE', '/api/events/' + id);
  // Undo toast — 3 s auto-dismiss, stays while hovered
  const toast = document.createElement('div');
  toast.className = 'toast undo-toast';
  toast.innerHTML = 'Deleted. <button onclick="'
    + "fetch('/api/events/" + id + "/restore',{method:'POST'}).then(()=>{showToast('Restored!');loadEvents();});"
    + "this.parentElement.remove();"
    + '" style="background:var(--success);color:#fff;border:none;padding:4px 14px;border-radius:4px;cursor:pointer;font-weight:600;margin-left:8px;">Undo</button>';
  let undoTimer = setTimeout(() => { if (toast.parentNode) toast.remove(); }, 3000);
  toast.title = 'Hover to keep — click to dismiss';
  toast.onclick = () => { clearTimeout(undoTimer); toast.remove(); };
  toast.onmouseenter = () => clearTimeout(undoTimer);
  toast.onmouseleave = () => { undoTimer = setTimeout(() => { if (toast.parentNode) toast.remove(); }, 2000); };
  document.body.appendChild(toast);
  loadEvents();
}

// ── Snooze ──────────────────────────────────────────────
async function snoozeEvent(id, minutes) {
  minutes = Math.max(1, Math.min(1440, parseInt(minutes) || 5));
  await api('POST', '/api/events/' + id + '/snooze?minutes=' + minutes);
  showToast('Snoozed for ' + minutes + ' minutes.');
  loadEvents();
}

// ── Poll for reminders ──────────────────────────────────
async function pollReminders() {
  if (!_pollActive) return;  // tab hidden — skip
  try {
    const res = await fetch('/api/pending');
    const data = await res.json();
    const status = document.getElementById('status');
    status.innerHTML = '<span class="dot"></span> ' + data.status;
    // Feed countdown from the same response (no separate fetch)
    const cd = document.getElementById('countdown');
    cd._nextAt = data.next_at || null;
    updateCountdown();
    if (data.reminders && data.reminders.length) {
      data.reminders.forEach(r => fireReminder(r.title, r.message, r.id));
    }
  } catch(e) {
    // server not ready yet — ignore
  }
}