// SECURITY: The server injects a new API token for every process; never persist it.
const API_TOKEN = document.querySelector('meta[name="taptap-api-token"]').content;
const IS_DESKTOP = document.querySelector('meta[name="taptap-desktop"]').content === '1';
// INVARIANT (cache): This query must follow app.py's _ASSET_VERSION.
const ASSET_VERSION = document.querySelector('meta[name="taptap-asset-version"]').content;
const ASSET_QUERY = '?v=' + encodeURIComponent(ASSET_VERSION);
// PACKAGING: Every filename here must exist under static/icons and in asset tests.
const CATEGORY_ICON_FILES = Object.freeze({
  work: 'briefcase.svg',
  personal: 'user.svg',
  health: 'heart.svg',
  other: 'tag.svg',
  custom: 'star.svg'
});

// ── Keyboard shortcuts ───────────────────────────────────
const SHORTCUT_PLATFORM = (
  (navigator.userAgentData && navigator.userAgentData.platform)
  || navigator.platform
  || ''
);
const SHORTCUT_IS_MAC = /Mac|iPhone|iPad|iPod/i.test(SHORTCUT_PLATFORM);
const PRIMARY_SHORTCUT_LABEL = SHORTCUT_IS_MAC ? 'Cmd' : 'Ctrl';
const ALT_SHORTCUT_LABEL = SHORTCUT_IS_MAC ? 'Option' : 'Alt';
const FORM_FIELD_IDS = Object.freeze([
  'ev-name', 'ev-desc', 'ev-date', 'ev-time', 'ev-category',
  'custom-cat-name', 'ev-reminder', 'ev-recurrence', 'custom-n', 'custom-unit'
]);

let _formBaseline = null;
let _shortcutReturnFocus = null;
let _undoState = null;

function isEditableTarget(target) {
  if (!target || typeof target.closest !== 'function') return false;
  return Boolean(target.closest(
    'input, textarea, select, [contenteditable=""], [contenteditable="true"]'
  ));
}

function choiceMenuIsOpen() {
  const categoryButton = document.getElementById('category-select-button');
  return Boolean(
    (categoryButton && categoryButton.getAttribute('aria-expanded') === 'true')
    || document.querySelector('.personalized-select-wrap.is-open')
  );
}

function closeOpenChoiceMenus(returnFocus = false) {
  const categoryButton = document.getElementById('category-select-button');
  if (categoryButton && categoryButton.getAttribute('aria-expanded') === 'true') {
    closeCategoryDropdown(returnFocus);
  }
  document.querySelectorAll('.personalized-select-wrap.is-open').forEach(wrap => {
    closePersonalizedSelect(wrap, returnFocus);
  });
}

function isPrimaryShortcut(event, key) {
  const primaryPressed = SHORTCUT_IS_MAC ? event.metaKey : event.ctrlKey;
  const otherPrimaryPressed = SHORTCUT_IS_MAC ? event.ctrlKey : event.metaKey;
  return primaryPressed
    && !otherPrimaryPressed
    && !event.altKey
    && !event.shiftKey
    && event.key.toLowerCase() === key;
}

function isKnownAppShortcut(event) {
  return ['n', 's', 'r', 'z'].some(key => isPrimaryShortcut(event, key))
    || (
      event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
      && event.key.toLowerCase() === 'p'
    );
}

function isShortcutHintKey(event) {
  return event.shiftKey
    && !event.ctrlKey
    && !event.metaKey
    && !event.altKey
    && (event.key === '?' || event.code === 'Slash');
}

function shortcutDialogIsOpen() {
  const dialog = document.getElementById('shortcuts-dialog');
  return Boolean(dialog && !dialog.hidden);
}

function updateShortcutLabels() {
  document.querySelectorAll('[data-primary-key]').forEach(element => {
    element.textContent = PRIMARY_SHORTCUT_LABEL + '+' + element.dataset.primaryKey;
  });
  document.querySelectorAll('[data-alt-key]').forEach(element => {
    element.textContent = ALT_SHORTCUT_LABEL + '+' + element.dataset.altKey;
  });
  document.querySelectorAll('[data-shortcut-title]').forEach(element => {
    element.title = element.dataset.shortcutTitle
      .replaceAll('{primary}', PRIMARY_SHORTCUT_LABEL)
      .replaceAll('{alt}', ALT_SHORTCUT_LABEL);
  });
}

function shortcutDialogFocusableElements() {
  const dialog = document.getElementById('shortcuts-dialog');
  if (!dialog) return [];
  return Array.from(dialog.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), '
      + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  ));
}

function trapShortcutDialogFocus(event) {
  const dialog = document.getElementById('shortcuts-dialog');
  const focusable = shortcutDialogFocusableElements();
  if (!dialog || !focusable.length) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}

function openShortcuts() {
  const dialog = document.getElementById('shortcuts-dialog');
  const button = document.getElementById('shortcuts-button');
  const closeButton = document.getElementById('shortcuts-close');
  if (!dialog || !dialog.hidden) return;

  _shortcutReturnFocus = document.activeElement;
  if (_shortcutReturnFocus && typeof _shortcutReturnFocus.closest === 'function') {
    const categoryWrap = _shortcutReturnFocus.closest('.category-select-wrap');
    const personalizedWrap = _shortcutReturnFocus.closest('.personalized-select-wrap');
    if (categoryWrap) {
      _shortcutReturnFocus = categoryWrap.querySelector('.category-select-button');
    } else if (personalizedWrap) {
      _shortcutReturnFocus = personalizedWrap.querySelector('.personalized-select-button');
    }
  }
  closeOpenChoiceMenus(false);
  dialog.hidden = false;
  document.body.classList.add('shortcuts-open');
  if (button) button.setAttribute('aria-expanded', 'true');
  if (closeButton) closeButton.focus();
}

function closeShortcuts(restoreFocus = true) {
  const dialog = document.getElementById('shortcuts-dialog');
  const button = document.getElementById('shortcuts-button');
  if (!dialog || dialog.hidden) return;

  dialog.hidden = true;
  document.body.classList.remove('shortcuts-open');
  if (button) button.setAttribute('aria-expanded', 'false');
  if (
    restoreFocus
    && _shortcutReturnFocus
    && document.documentElement.contains(_shortcutReturnFocus)
    && typeof _shortcutReturnFocus.focus === 'function'
  ) {
    _shortcutReturnFocus.focus();
  }
  _shortcutReturnFocus = null;
}

function toggleShortcuts() {
  if (shortcutDialogIsOpen()) closeShortcuts();
  else openShortcuts();
}

function initShortcutUI() {
  const dialog = document.getElementById('shortcuts-dialog');
  const openButton = document.getElementById('shortcuts-button');
  const closeButton = document.getElementById('shortcuts-close');
  updateShortcutLabels();
  if (!dialog || !openButton || !closeButton) return;

  openButton.addEventListener('click', openShortcuts);
  closeButton.addEventListener('click', () => closeShortcuts());
  dialog.addEventListener('click', event => {
    if (event.target === dialog) closeShortcuts();
  });
}

function formSnapshot() {
  const values = Object.fromEntries(FORM_FIELD_IDS.map(id => {
    const field = document.getElementById(id);
    return [id, field ? field.value : ''];
  }));
  if (values['ev-category'] !== 'custom') values['custom-cat-name'] = '';
  if (values['ev-recurrence'] !== 'custom') {
    values['custom-n'] = '';
    values['custom-unit'] = '';
  }
  return JSON.stringify(values);
}

function rememberFormBaseline() {
  _formBaseline = formSnapshot();
}

function formHasUnsavedChanges() {
  return _formBaseline !== null && formSnapshot() !== _formBaseline;
}

function formIsActive() {
  return Boolean(document.getElementById('edit-id').value) || formHasUnsavedChanges();
}

function confirmDiscardFormChanges() {
  return !formHasUnsavedChanges() || confirm('Discard unsaved changes?');
}

function startNewEvent() {
  if (!confirmDiscardFormChanges()) return;
  cancelEdit();
  document.getElementById('ev-name').focus();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function cancelFormFromShortcut() {
  if (!formIsActive()) return false;
  if (!confirmDiscardFormChanges()) return true;
  cancelEdit();
  return true;
}

async function refreshEventData(announce = true) {
  try {
    const refreshes = [loadEvents(), refreshCategoryOptions()];
    if (typeof _historyOpen !== 'undefined' && _historyOpen) {
      refreshes.push(_renderHistory());
    }
    await Promise.all(refreshes);
    if (announce) showToast('Refreshed', 2000);
  } catch(e) {
    if (announce) showToast('Could not refresh event data.');
  }
}

function handleAppShortcut(event) {
  if (event.defaultPrevented || event.isComposing) return;

  if (isShortcutHintKey(event) && !isEditableTarget(event.target)) {
    event.preventDefault();
    if (!event.repeat) toggleShortcuts();
    return;
  }

  if (shortcutDialogIsOpen()) {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (!event.repeat) closeShortcuts();
    } else if (event.key === 'Tab') {
      trapShortcutDialogFocus(event);
    } else if (isKnownAppShortcut(event)) {
      event.preventDefault();
    }
    return;
  }

  if (event.key === 'Escape') {
    if (choiceMenuIsOpen()) {
      event.preventDefault();
      if (!event.repeat) closeOpenChoiceMenus(true);
    } else if (formIsActive()) {
      event.preventDefault();
      if (!event.repeat) cancelFormFromShortcut();
    }
    return;
  }

  if (isPrimaryShortcut(event, 'n')) {
    event.preventDefault();
    if (!event.repeat) startNewEvent();
    return;
  }
  if (isPrimaryShortcut(event, 's')) {
    event.preventDefault();
    if (!event.repeat) saveEvent();
    return;
  }
  if (isPrimaryShortcut(event, 'r')) {
    event.preventDefault();
    if (!event.repeat) refreshEventData();
    return;
  }
  if (
    event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
    && event.key.toLowerCase() === 'p'
    && !isEditableTarget(event.target)
  ) {
    event.preventDefault();
    if (!event.repeat) toggleHistory();
    return;
  }
  if (
    isPrimaryShortcut(event, 'z')
    && !isEditableTarget(event.target)
    && _undoState
  ) {
    event.preventDefault();
    if (!event.repeat) undoLatestDeletion();
  }
}

document.addEventListener('keydown', handleAppShortcut);

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

// ── Windows sign-in startup ──────────────────────────────
let _autostartUpdateInFlight = false;

async function loadAutostartSetting(announce = false) {
  const control = document.getElementById('autostart-control');
  const toggle = document.getElementById('autostart-toggle');
  const liveStatus = document.getElementById('autostart-status');
  if (!control || !toggle || !IS_DESKTOP) return;

  try {
    const status = await api('GET', '/api/settings/autostart');
    if (!status.supported) {
      control.hidden = true;
      return;
    }
    control.hidden = false;
    toggle.checked = status.enabled === true;
    toggle.dataset.enabled = toggle.checked ? 'true' : 'false';
    const message = status.reason || (toggle.checked
      ? 'Start with Windows is enabled.'
      : 'Start with Windows is disabled.');
    if (liveStatus) {
      liveStatus.textContent = message;
    }
    control.title = status.reason
      || 'Launch TapTap in the notification area when you sign in to Windows';
    if (announce) showToast(message);
  } catch (error) {
    if (liveStatus) liveStatus.textContent = 'Could not read Windows startup settings.';
    control.title = String(error.message || error);
  }
}

async function setAutostartSetting() {
  const toggle = document.getElementById('autostart-toggle');
  const liveStatus = document.getElementById('autostart-status');
  if (!toggle || _autostartUpdateInFlight) return;

  const requested = toggle.checked;
  const previous = toggle.dataset.enabled === 'true';
  _autostartUpdateInFlight = true;
  toggle.disabled = true;
  try {
    const status = await api('PUT', '/api/settings/autostart', {enabled: requested});
    toggle.checked = status.enabled === true;
    toggle.dataset.enabled = toggle.checked ? 'true' : 'false';
    const message = toggle.checked
      ? 'TapTap will start in the background when you sign in.'
      : 'Start with Windows disabled.';
    if (liveStatus) liveStatus.textContent = message;
    showToast(message, 4500);
  } catch (error) {
    toggle.checked = previous;
    if (liveStatus) liveStatus.textContent = 'Could not change Windows startup settings.';
    showToast(String(error.message || error), 6500);
  } finally {
    toggle.disabled = false;
    _autostartUpdateInFlight = false;
  }
}

function initAutostartUI() {
  const toggle = document.getElementById('autostart-toggle');
  if (!toggle) return;
  toggle.addEventListener('change', setAutostartSetting);
  loadAutostartSetting();
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
const CLOCK_MS = 1000;                  // visibly tick once per second
const CLOCK_CALIBRATION_MS = 5 * 60_000;
const CLOCK_RESUME_GAP_MS = 5000;       // a delayed tick can indicate sleep/wake
let _clockOffset = 0;
let _clockCalibration = null;
let _lastClockTick = Date.now();
let _clockTimer = null;

function calibrateClock() {
  // Visibility, focus, and wake signals can arrive together. Share one local
  // request so restoring the window never creates a burst of calibrations.
  if (_clockCalibration) return _clockCalibration;
  _clockCalibration = (async () => {
    try {
      const t0 = Date.now();
      const data = await api('GET', '/api/time');
      const t1 = Date.now();
      // Estimate server time at the midpoint of the request round-trip.
      const serverNow = data.timestamp * 1000;
      if (!Number.isFinite(serverNow)) return;
      const rtt = t1 - t0;
      const estimatedServerAtT1 = serverNow + rtt / 2;
      _clockOffset = estimatedServerAtT1 - t1;
    } catch(e) {
      // Keep the current offset on failure; the local clock can still tick.
    }
  })();
  _clockCalibration.finally(() => { _clockCalibration = null; });
  return _clockCalibration;
}

function calibratedNow() {
  return new Date(Date.now() + _clockOffset);
}

// COMPATIBILITY: <input type="date"> needs local calendar fields, not UTC ISO fields.
function localDateValue(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return year + '-' + month + '-' + day;
}

function recurrenceLabel(value, noneLabel) {
  // INVARIANT (display): Stored "N:units" rules are shown as natural text.
  const recurrence = String(value || 'none');
  if (recurrence === 'none') return noneLabel || '';
  if (['daily', 'weekly', 'monthly', 'yearly'].includes(recurrence)) return recurrence;

  const custom = recurrence.match(/^([1-9]\d*):(days|weeks|months|years)$/);
  if (!custom) return recurrence;
  const count = Number(custom[1]);
  const unit = count === 1 ? custom[2].slice(0, -1) : custom[2];
  return 'every ' + count + ' ' + unit;
}

function recalibrateAndAlignClock() {
  return calibrateClock().then(() => {
    updateClock();
    scheduleClockTick();
  });
}

recalibrateAndAlignClock();
setInterval(recalibrateAndAlignClock, CLOCK_CALIBRATION_MS);

// ── Lightweight polling — adaptive: 5 s normally, 1 s when reminder is imminent ──
const POLL_SLOW = 5000;      // normal: every 5 s
const POLL_FAST = 1000;      // imminent: every 1 s (second-accurate firing)
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
scheduleClockTick();
setInterval(updateCountdown, 1000);  // countdown ticks every second (local, no server hit)

// UI polling can pause while hidden because Python delivers reminders in the background.
document.addEventListener('visibilitychange', () => {
  _pollActive = !document.hidden;
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
  if (_pollActive) {
    resumeClock();
    pollReminders().then(() => _scheduleNextPoll());
  }
});
window.addEventListener('focus', resumeClock);
window.addEventListener('pageshow', resumeClock);
window.addEventListener('focus', () => loadAutostartSetting());

// Set form defaults to current local time (after calibratedNow is defined).
const _initialNow = calibratedNow();
document.getElementById('ev-date').value = localDateValue(_initialNow);
document.getElementById('ev-time').value =
  String(_initialNow.getHours()).padStart(2, '0') + ':' +
  String(_initialNow.getMinutes()).padStart(2, '0');
updateClock();
loadEvents();
updateCountdown();
initCategoryDropdown();
initPersonalizedSelects();
refreshCategoryOptions();
initShortcutUI();
initAutostartUI();
rememberFormBaseline();

function updateClock() {
  document.getElementById('clock').textContent =
    calibratedNow().toLocaleTimeString();
}

function millisecondsToNextClockTick(now) {
  const remainder = ((now % CLOCK_MS) + CLOCK_MS) % CLOCK_MS;
  return remainder < 1 ? CLOCK_MS : CLOCK_MS - remainder;
}

function scheduleClockTick() {
  if (_clockTimer) clearTimeout(_clockTimer);
  const delay = millisecondsToNextClockTick(calibratedNow().getTime());
  _clockTimer = setTimeout(() => {
    _clockTimer = null;
    tickClock();
    scheduleClockTick();
  }, delay);
}

function tickClock() {
  const tickAt = Date.now();
  const resumed = tickAt - _lastClockTick > CLOCK_RESUME_GAP_MS;
  _lastClockTick = tickAt;
  updateClock();
  // A visible page whose timer was delayed likely crossed sleep/wake. Recheck
  // the server offset, but keep repainting from local time while that completes.
  if (resumed && !document.hidden) recalibrateAndAlignClock();
}

function resumeClock() {
  _lastClockTick = Date.now();
  updateClock();
  scheduleClockTick();
  recalibrateAndAlignClock();
}

async function updateCountdown() {
  // Driven by pollReminders — no separate fetch needed
  const el = document.getElementById('countdown');
  if (!el._nextAt) { el.textContent = ''; return; }
  const seconds = Math.round((new Date(el._nextAt) - calibratedNow()) / 1000);
  if (seconds <= 0) { el.textContent = '· now'; return; }
  el.textContent = formatCountdown(seconds);
}

function formatCountdown(seconds) {
  if (seconds < 60) return '· in ' + seconds + 's';

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return '· in ' + minutes + 'm';

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return '· in ' + hours + 'h ' + (minutes % 60) + 'm';

  const days = Math.floor(hours / 24);
  if (days < 30) return '· in ' + days + 'd';

  const months = Math.floor(days / 30);
  if (months < 12) {
    return '· in ' + months + 'mo' + (days % 30 ? ' ' + (days % 30) + 'd' : '');
  }

  const years = Math.floor(days / 365);
  const daysAfterYears = days % 365;
  const remainingMonths = Math.floor(daysAfterYears / 30);
  const remainingDays = daysAfterYears % 30;
  return '· in ' + years + 'y'
    + (remainingMonths ? ' ' + remainingMonths + 'mo' : '')
    + (remainingDays ? ' ' + remainingDays + 'd' : '');
}

// ── Toast ────────────────────────────────────────────────
function showToast(msg, duration) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = '<img src="/static/icons/info.svg" class="icon"> ';
  t.appendChild(document.createTextNode(String(msg)));
  if (duration > 5000) {
    t.title = 'Click to dismiss';
    t.onclick = () => t.remove();
  }
  document.body.appendChild(t);
  setTimeout(() => { if (t.parentNode) t.remove(); }, duration || 3000);
}

// ── Notifications ────────────────────────────────────────
function browserNotificationsAvailable() {
  return 'Notification' in window;
}

function requestNotifyPermission() {
  if (browserNotificationsAvailable() && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}
// Browser mode still needs permission; the desktop shell uses native notifications.
if (!IS_DESKTOP && browserNotificationsAvailable()) {
  requestNotifyPermission();
  document.addEventListener('click', function ask() {
    requestNotifyPermission();
    if (Notification.permission !== 'default') document.removeEventListener('click', ask);
  });
}

function fireReminder(title, body, eventId, nativeNotified) {
  playBeep();
  // Fall back to the browser API only if native Python delivery failed.
  if (!nativeNotified && browserNotificationsAvailable() && Notification.permission === 'granted') {
    new Notification(title, {
      body: body, icon: '/static/app-icon.png' + ASSET_QUERY, requireInteraction: true, tag: 'reminder',
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
          return '<button onclick="api(\'POST\',\'/api/events/' + eventId + '/snooze?minutes=' + mins + '\');'
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
  // SECURITY: All frontend API requests pass through this token-bearing helper.
  const opts = { method, headers: {'X-TapTap-Token': API_TOKEN} };
  if (data !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(data);
  }
  const res = await fetch(path, opts);
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || ('Request failed: ' + res.status));
  return payload;
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
    const recDisplay = recurrenceLabel(ev.recurrence, '');
    const recLabel = recDisplay
      ? `<span class="event-tag tag-recur">${esc(recDisplay)}</span>` : '';
    const remindLabel = `<span class="event-tag tag-reminder"><img src="/static/icons/clock-white.svg" class="icon" style="width:12px;height:12px;"> ${esc(ev.reminder_min)}m</span>`;
    const knownCats = ['work','personal','health','other'];
    const catCSS = knownCats.includes(ev.category) ? ev.category : (ev.category ? 'custom' : '');
    const catClass = catCSS ? ` cat-${catCSS}` : '';
    const catDot = catCSS
      ? `<span class="cat-dot cat-dot-${catCSS}" title="${esc(ev.category)}"></span>` : '';
    return `<div class="event-card${isPast ? ' past' : ''}${catClass}">
      <div class="event-icon"><img src="/static/icons/${isPast ? 'check-white' : 'bell'}.svg" style="width:22px;height:22px;"></div>
      <div class="event-info">
        <div class="event-name">${esc(ev.name)}${catDot}${recLabel}${remindLabel}</div>
        <div class="event-meta">
          <img src="/static/icons/calendar.svg" class="icon" style="width:12px;height:12px;"> ${esc(ev.event_date)} at ${esc(ev.event_time)}
          ${ev.description ? ' — ' + esc(ev.description) : ''}
        </div>
      </div>
      <div class="event-actions">
        <button class="btn btn-secondary btn-sm" onclick="startEdit(${ev.id})" title="Edit this event"><img src="/static/icons/edit-white.svg" class="icon"></button>
        <div class="snooze-group">
          <button class="btn btn-warning btn-sm" onclick="snoozeEventFromControls(${ev.id}, this)" title="Snooze reminders"><img src="/static/icons/bell-white.svg" class="icon"></button>
          <div class="personalized-select-wrap snooze-select-wrap" data-select-id="snooze-select-${ev.id}">
            <button type="button" class="personalized-select-button" aria-haspopup="listbox" aria-expanded="false" aria-label="Snooze: 5m">
              <span class="personalized-select-value">5m</span><span class="personalized-select-chevron" aria-hidden="true"></span>
            </button>
            <div class="personalized-select-menu" role="listbox" aria-label="Snooze" hidden></div>
            <select id="snooze-select-${ev.id}" class="personalized-native-select snooze-select" hidden aria-hidden="true" tabindex="-1">
            <option value="1">1m</option>
            <option value="5" selected>5m</option>
            <option value="10">10m</option>
            <option value="15">15m</option>
            <option value="30">30m</option>
            <option value="other">other…</option>
            </select>
          </div>
          <input type="number" class="snooze-custom" value="60" min="1" max="1440" style="display:none">
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteEvent(${ev.id})" title="Delete this event permanently"><img src="/static/icons/trash-white.svg" class="icon"></button>
      </div>
    </div>`;
  }).join('');
  initPersonalizedSelects(list);
  }

function snoozeEventFromControls(id, button) {
  const group = button.closest('.snooze-group');
  const select = group.querySelector('.snooze-select');
  const custom = group.querySelector('.snooze-custom');
  const value = parseInt(select.value === 'other' ? custom.value : select.value, 10) || 5;
  snoozeEvent(id, Math.max(1, Math.min(1440, value)));
}

function esc(s) {
  // SECURITY: Escape every database-derived value before using HTML templates.
  const el = document.createElement('span');
  el.textContent = s == null ? '' : String(s);
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
    } else {
      document.getElementById('ev-category').value = 'custom';
      document.getElementById('custom-cat-name').value = cat;
      document.getElementById('custom-cat').style.display = 'block';
    }
    updateCategoryIcon();
    syncPersonalizedSelectById('ev-recurrence');
    syncPersonalizedSelectById('custom-unit');
    document.getElementById('form-title').innerHTML = '<img src="/static/icons/edit.svg" class="icon"> Edit Event';
    document.getElementById('btn-save').innerHTML = '<img src="/static/icons/edit.svg" class="icon"> Save Changes';
    document.getElementById('btn-cancel').style.display = '';
    rememberFormBaseline();
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
  updateCategoryIcon();
}

// Native selects remain the value source. These controls give every other
// choice field the same accessible, app-styled menu as Category.
function initPersonalizedSelects(root = document) {
  root.querySelectorAll('.personalized-select-wrap').forEach(wrap => {
    if (wrap.dataset.initialized === 'true') return;
    const select = document.getElementById(wrap.dataset.selectId);
    const button = wrap.querySelector('.personalized-select-button');
    const menu = wrap.querySelector('.personalized-select-menu');
    if (!select || !button || !menu) return;

    wrap.dataset.initialized = 'true';
    rebuildPersonalizedSelect(wrap);
    button.addEventListener('click', () => togglePersonalizedSelect(wrap));
    button.addEventListener('keydown', event => {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        openPersonalizedSelect(wrap);
      } else if (
        event.key === 'Escape'
        && button.getAttribute('aria-expanded') === 'true'
      ) {
        event.preventDefault();
        closePersonalizedSelect(wrap);
      }
    });
    select.addEventListener('change', () => {
      syncPersonalizedSelect(wrap);
      if (select.classList.contains('snooze-select')) {
        const custom = wrap.parentElement.querySelector('.snooze-custom');
        custom.style.display = select.value === 'other' ? '' : 'none';
        if (select.value === 'other') custom.focus();
      }
    });
    document.addEventListener('click', event => {
      if (!wrap.contains(event.target)) closePersonalizedSelect(wrap, false);
    });
  });
}

function rebuildPersonalizedSelect(wrap) {
  const select = document.getElementById(wrap.dataset.selectId);
  const menu = wrap.querySelector('.personalized-select-menu');
  if (!select || !menu) return;
  menu.replaceChildren();
  for (const option of select.options) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'personalized-select-option';
    item.dataset.value = option.value;
    item.setAttribute('role', 'option');
    item.tabIndex = -1;
    item.textContent = option.textContent;
    item.addEventListener('click', () => {
      select.value = option.value;
      select.dispatchEvent(new Event('change', {bubbles: true}));
      closePersonalizedSelect(wrap);
    });
    item.addEventListener('keydown', event => handlePersonalizedOptionKeydown(event, wrap));
    menu.appendChild(item);
  }
  syncPersonalizedSelect(wrap);
}

function syncPersonalizedSelect(wrap) {
  const select = document.getElementById(wrap.dataset.selectId);
  const button = wrap.querySelector('.personalized-select-button');
  const value = wrap.querySelector('.personalized-select-value');
  if (!select || !button || !value) return;
  const option = select.options[select.selectedIndex];
  const label = option ? option.textContent : '';
  value.textContent = label;
  button.setAttribute('aria-label', (wrap.querySelector('.personalized-select-menu').getAttribute('aria-label') || 'Choice') + ': ' + label);
  wrap.querySelectorAll('.personalized-select-option').forEach(item => {
    item.setAttribute('aria-selected', String(item.dataset.value === select.value));
  });
}

function syncPersonalizedSelectById(id) {
  const wrap = document.querySelector(`.personalized-select-wrap[data-select-id="${id}"]`);
  if (wrap) syncPersonalizedSelect(wrap);
}

function openPersonalizedSelect(wrap) {
  const menu = wrap.querySelector('.personalized-select-menu');
  const button = wrap.querySelector('.personalized-select-button');
  menu.hidden = false;
  wrap.classList.add('is-open');
  button.setAttribute('aria-expanded', 'true');
  const selected = menu.querySelector('[aria-selected="true"]') || menu.firstElementChild;
  if (selected) selected.focus({preventScroll: true});
}

function closePersonalizedSelect(wrap, returnFocus = true) {
  const menu = wrap.querySelector('.personalized-select-menu');
  const button = wrap.querySelector('.personalized-select-button');
  if (menu.hidden) return;
  menu.hidden = true;
  wrap.classList.remove('is-open');
  button.setAttribute('aria-expanded', 'false');
  if (returnFocus) button.focus();
}

function togglePersonalizedSelect(wrap) {
  if (wrap.querySelector('.personalized-select-button').getAttribute('aria-expanded') === 'true') {
    closePersonalizedSelect(wrap);
  } else {
    openPersonalizedSelect(wrap);
  }
}

function handlePersonalizedOptionKeydown(event, wrap) {
  const options = Array.from(wrap.querySelectorAll('.personalized-select-option'));
  const index = options.indexOf(event.currentTarget);
  let next = index;
  if (event.key === 'ArrowDown') next = Math.min(index + 1, options.length - 1);
  else if (event.key === 'ArrowUp') next = Math.max(index - 1, 0);
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = options.length - 1;
  else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault(); event.currentTarget.click(); return;
  } else if (event.key === 'Escape') {
    event.preventDefault(); closePersonalizedSelect(wrap); return;
  } else if (event.key === 'Tab') {
    closePersonalizedSelect(wrap, false); return;
  } else return;
  event.preventDefault();
  options[next].focus();
}

// The native select remains the category value source, while this listbox
// provides consistent icon-rich options across the desktop webview backends.
function categoryIconFile(value) {
  return CATEGORY_ICON_FILES[value]
    || (value ? 'star.svg' : 'circle-outline.svg');
}

function categoryIconSource(value) {
  return '/static/icons/' + categoryIconFile(value) + ASSET_QUERY;
}

function categoryOptionElements() {
  return Array.from(
    document.querySelectorAll('#category-select-menu .category-select-option')
  );
}

function initCategoryDropdown() {
  const wrap = document.querySelector('.category-select-wrap');
  const button = document.getElementById('category-select-button');
  if (!wrap || !button || wrap.dataset.initialized === 'true') return;
  wrap.dataset.initialized = 'true';

  button.addEventListener('click', toggleCategoryDropdown);
  button.addEventListener('keydown', handleCategoryButtonKeydown);
  document.addEventListener('click', event => {
    if (!wrap.contains(event.target)) closeCategoryDropdown(false);
  });

  rebuildCategoryMenu();
}

function rebuildCategoryMenu() {
  const select = document.getElementById('ev-category');
  const menu = document.getElementById('category-select-menu');
  if (!select || !menu) return;

  menu.replaceChildren();
  for (const option of select.options) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'category-select-option';
    item.dataset.value = option.value;
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(option.value === select.value));
    item.tabIndex = -1;

    const icon = document.createElement('img');
    icon.className = 'category-option-icon';
    icon.src = categoryIconSource(option.value);
    icon.alt = '';
    icon.setAttribute('aria-hidden', 'true');

    const label = document.createElement('span');
    label.textContent = option.textContent;
    item.append(icon, label);
    item.addEventListener('click', () => selectCategory(option.value));
    item.addEventListener('keydown', handleCategoryOptionKeydown);
    menu.appendChild(item);
  }
  updateCategoryIcon();
}

function openCategoryDropdown() {
  const wrap = document.querySelector('.category-select-wrap');
  const button = document.getElementById('category-select-button');
  const menu = document.getElementById('category-select-menu');
  if (!wrap || !button || !menu) return;

  menu.hidden = false;
  wrap.classList.add('is-open');
  button.setAttribute('aria-expanded', 'true');

  const options = categoryOptionElements();
  const selected = options.find(
    option => option.getAttribute('aria-selected') === 'true'
  );
  const target = selected || options[0];
  if (target) {
    target.focus({preventScroll: true});
    target.scrollIntoView({block: 'nearest'});
  }
}

function closeCategoryDropdown(returnFocus = true) {
  const wrap = document.querySelector('.category-select-wrap');
  const button = document.getElementById('category-select-button');
  const menu = document.getElementById('category-select-menu');
  if (!wrap || !button || !menu) return;

  menu.hidden = true;
  wrap.classList.remove('is-open');
  button.setAttribute('aria-expanded', 'false');
  if (returnFocus) button.focus();
}

function toggleCategoryDropdown() {
  const button = document.getElementById('category-select-button');
  if (!button) return;
  if (button.getAttribute('aria-expanded') === 'true') {
    closeCategoryDropdown();
  } else {
    openCategoryDropdown();
  }
}

function selectCategory(value) {
  const select = document.getElementById('ev-category');
  if (!select) return;
  select.value = value;
  select.dispatchEvent(new Event('change', {bubbles: true}));
  closeCategoryDropdown();
}

function handleCategoryButtonKeydown(event) {
  if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
    event.preventDefault();
    openCategoryDropdown();
  } else if (
    event.key === 'Escape'
    && event.currentTarget.getAttribute('aria-expanded') === 'true'
  ) {
    event.preventDefault();
    closeCategoryDropdown();
  }
}

function handleCategoryOptionKeydown(event) {
  const options = categoryOptionElements();
  const index = options.indexOf(event.currentTarget);
  let nextIndex = index;

  if (event.key === 'ArrowDown') nextIndex = Math.min(index + 1, options.length - 1);
  else if (event.key === 'ArrowUp') nextIndex = Math.max(index - 1, 0);
  else if (event.key === 'Home') nextIndex = 0;
  else if (event.key === 'End') nextIndex = options.length - 1;
  else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    selectCategory(event.currentTarget.dataset.value);
    return;
  } else if (event.key === 'Escape') {
    event.preventDefault();
    closeCategoryDropdown();
    return;
  } else if (event.key === 'Tab') {
    closeCategoryDropdown(false);
    return;
  } else {
    return;
  }

  event.preventDefault();
  if (options[nextIndex]) options[nextIndex].focus();
}

function updateCategoryIcon() {
  const select = document.getElementById('ev-category');
  const button = document.getElementById('category-select-button');
  const icon = button ? button.querySelector('.category-select-icon') : null;
  const label = document.getElementById('category-select-value');
  if (!select || !button || !icon || !label) return;

  const selectedOption = Array.from(select.options).find(
    option => option.value === select.value
  );
  const selectedLabel = selectedOption ? selectedOption.textContent : 'None';
  icon.src = categoryIconSource(select.value);
  label.textContent = selectedLabel;
  button.setAttribute('aria-label', 'Category: ' + selectedLabel);

  for (const option of categoryOptionElements()) {
    option.setAttribute(
      'aria-selected', String(option.dataset.value === select.value)
    );
  }
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
      opt.textContent = cat;
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
    rebuildCategoryMenu();
  } catch(e) { /* ignore */ }
}
function cancelEdit() {
  document.getElementById('edit-id').value = '';
  document.getElementById('ev-name').value = '';
  const now = calibratedNow();
  document.getElementById('ev-date').value = localDateValue(now);
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
  updateCategoryIcon();
  syncPersonalizedSelectById('ev-recurrence');
  syncPersonalizedSelectById('custom-unit');
  document.getElementById('form-title').innerHTML = '<img src="/static/icons/plus.svg" class="icon"> Add New Event';
  document.getElementById('btn-save').innerHTML = '<img src="/static/icons/plus-white.svg" class="icon"> Add Event';
  document.getElementById('btn-cancel').style.display = 'none';
  rememberFormBaseline();
}

// ── Delete ──────────────────────────────────────────────
function dismissUndoToast() {
  if (!_undoState) return;
  clearTimeout(_undoState.timer);
  if (_undoState.toast.parentNode) _undoState.toast.remove();
  _undoState = null;
}

function scheduleUndoDismiss(delay) {
  if (!_undoState) return;
  const state = _undoState;
  clearTimeout(state.timer);
  state.timer = setTimeout(() => {
    if (_undoState === state) dismissUndoToast();
  }, delay);
}

function showUndoToast(id) {
  dismissUndoToast();
  const toast = document.createElement('div');
  toast.className = 'toast undo-toast';
  toast.setAttribute('role', 'status');
  toast.setAttribute('aria-live', 'polite');
  toast.title = 'Hover to keep — click to dismiss';

  const message = document.createElement('span');
  message.textContent = 'Deleted.';
  const undoButton = document.createElement('button');
  undoButton.type = 'button';
  undoButton.className = 'undo-button';
  undoButton.textContent = 'Undo';
  undoButton.title = 'Undo deletion (' + PRIMARY_SHORTCUT_LABEL + '+Z)';
  undoButton.setAttribute('aria-keyshortcuts', 'Control+Z Meta+Z');
  undoButton.addEventListener('click', event => {
    event.stopPropagation();
    undoLatestDeletion();
  });
  toast.append(message, undoButton);

  _undoState = {id, toast, timer: null, restoring: false};
  toast.addEventListener('click', dismissUndoToast);
  toast.addEventListener('mouseenter', () => {
    if (_undoState && _undoState.toast === toast) clearTimeout(_undoState.timer);
  });
  toast.addEventListener('mouseleave', () => {
    if (_undoState && _undoState.toast === toast) scheduleUndoDismiss(2000);
  });
  document.body.appendChild(toast);
  scheduleUndoDismiss(3000);
}

async function undoLatestDeletion() {
  const state = _undoState;
  if (!state || state.restoring) return;
  state.restoring = true;
  clearTimeout(state.timer);
  const button = state.toast.querySelector('.undo-button');
  if (button) {
    button.disabled = true;
    button.textContent = 'Restoring…';
  }
  try {
    await api('POST', '/api/events/' + state.id + '/restore');
    if (_undoState === state) dismissUndoToast();
    await refreshEventData(false);
    showToast('Restored!');
  } catch(e) {
    state.restoring = false;
    if (button) {
      button.disabled = false;
      button.textContent = 'Undo';
    }
    if (_undoState === state) scheduleUndoDismiss(3000);
    showToast('Could not restore the event.');
  }
}

async function deleteEvent(id) {
  const events = await api('GET', '/api/events');
  const ev = events.find(e => e.id === id);
  if (!confirm('Delete "' + (ev ? ev.name : 'event') + '"?')) return;
  await api('DELETE', '/api/events/' + id);
  showUndoToast(id);
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
    const data = await api('GET', '/api/pending');
    const status = document.getElementById('status');
    status.innerHTML = '<span class="dot"></span> ' + data.status;
    if (data.notification && data.notification.message) {
      status.appendChild(document.createTextNode(' · ' + data.notification.message));
    }
    // Feed countdown from the same response (no separate fetch)
    const cd = document.getElementById('countdown');
    cd._nextAt = data.next_at || null;
    updateCountdown();
    if (data.reminders && data.reminders.length) {
      let deliveredWhileHidden = 0;
      data.reminders.forEach(r => {
        const deliveredAt = Date.parse(r.delivered_at || '');
        const staleNativeDelivery = r.native_notified === true
          && Number.isFinite(deliveredAt)
          && Date.now() - deliveredAt > 30_000;
        if (staleNativeDelivery) {
          deliveredWhileHidden += 1;
          return;
        }
        fireReminder(r.title, r.message, r.id, r.native_notified === true);
      });
      if (deliveredWhileHidden) {
        const noun = deliveredWhileHidden === 1 ? 'reminder was' : 'reminders were';
        showToast(deliveredWhileHidden + ' ' + noun + ' delivered while TapTap was hidden.', 6000);
      }
    }
  } catch(e) {
    // server not ready yet — ignore
  }
}
