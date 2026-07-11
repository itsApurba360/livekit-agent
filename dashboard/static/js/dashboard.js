const $ = (id) => document.getElementById(id);

const TOKEN_KEY = 'callDashboardToken';
const loginShell = $('loginShell');
const appShell = $('appShell');
const loginForm = $('loginForm');
const loginToken = $('loginToken');
const loginError = $('loginError');
const loginSubmit = $('loginSubmit');

let activeCall = null;
let allCalls = [];
let refreshTimer = null;
let clockTimer = null;
let serverClockBase = null; // { serverMs, localMs } captured at last sync

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fmtTime = (value) => value ? new Date(value).toLocaleString() : '—';
const purposeOf = (call) => call.metadata?.call_purpose || call.metadata?.purpose || '—';
const statusClass = (status) => {
  if (!status) return 'status-idle';
  if (status.startsWith('failed') || status === 'dispatch_failed') return 'status-fail';
  if (['completed', 'answered', 'active'].includes(status)) return 'status-ok';
  if (['dialing', 'dispatching', 'dispatched', 'agent_ready'].includes(status)) return 'status-live';
  return 'status-idle';
};
const statusGroup = (status) => {
  if (!status) return 'idle';
  if (status.startsWith('failed') || status === 'dispatch_failed') return 'failed';
  if (['completed', 'answered', 'active'].includes(status)) return 'connected';
  if (['dialing', 'dispatching', 'dispatched', 'agent_ready'].includes(status)) return 'live';
  return 'idle';
};

function setError(message) {
  const box = $('errorBox');
  box.textContent = message || '';
  box.style.display = message ? 'block' : 'none';
}

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || '';
}

async function api(path, options = {}, tokenOverride) {
  const token = tokenOverride || getToken();
  if (!token) throw new Error('Not authenticated.');
  const headers = { Authorization: `Bearer ${token}`, Accept: 'application/json', ...options.headers };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return response.json();
}

// ---------- Auth / protected route ----------

function showLogin(message) {
  stopPolling();
  appShell.classList.add('hidden');
  loginShell.classList.remove('hidden');
  loginError.style.display = message ? 'block' : 'none';
  loginError.textContent = message || '';
  loginToken.focus();
}

function showApp() {
  loginShell.classList.add('hidden');
  appShell.classList.remove('hidden');
  startPolling();
}

async function attemptLogin(token) {
  loginSubmit.disabled = true;
  loginSubmit.textContent = 'Signing in…';
  try {
    await api('/auth/verify', {}, token);
    sessionStorage.setItem(TOKEN_KEY, token);
    showApp();
    await loadDashboard();
  } catch (err) {
    showLogin('Invalid token. Please check CALL_API_TOKEN and try again.');
  } finally {
    loginSubmit.disabled = false;
    loginSubmit.textContent = 'Sign in';
  }
}

loginForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const token = loginToken.value.trim();
  if (!token) return;
  attemptLogin(token);
});

$('logoutBtn').addEventListener('click', () => {
  sessionStorage.removeItem(TOKEN_KEY);
  loginToken.value = '';
  showLogin();
});

function startPolling() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => { if ($('auto').checked) loadDashboard(); }, 5000);
  if (clockTimer) clearInterval(clockTimer);
  clockTimer = setInterval(tickServerClock, 1000);
}

function stopPolling() {
  if (refreshTimer) clearInterval(refreshTimer);
  if (clockTimer) clearInterval(clockTimer);
  refreshTimer = null;
  clockTimer = null;
}

async function bootstrap() {
  const token = getToken();
  if (!token) {
    showLogin();
    return;
  }
  try {
    await api('/auth/verify', {}, token);
    showApp();
    await loadDashboard();
  } catch (err) {
    sessionStorage.removeItem(TOKEN_KEY);
    showLogin();
  }
}

// ---------- Server clock ----------

function tickServerClock() {
  if (!serverClockBase) return;
  const elapsed = Date.now() - serverClockBase.localMs;
  const serverNow = new Date(serverClockBase.serverMs + elapsed);
  const ist = new Date(serverNow.getTime() + (5 * 60 + 30) * 60 * 1000);
  const hh = String(ist.getUTCHours()).padStart(2, '0');
  const mm = String(ist.getUTCMinutes()).padStart(2, '0');
  const ss = String(ist.getUTCSeconds()).padStart(2, '0');
  $('serverClock').textContent = `${hh}:${mm}:${ss}`;
}

function syncServerClock(generatedAt) {
  if (!generatedAt) return;
  serverClockBase = { serverMs: new Date(generatedAt).getTime(), localMs: Date.now() };
  tickServerClock();
}

// ---------- Dashboard data ----------

function renderSummary(summary) {
  $('mTotal').textContent = summary.total ?? 0;
  $('mLive').textContent = summary.live ?? 0;
  $('mConnected').textContent = summary.connected ?? 0;
  $('mFailures').textContent = summary.failures ?? 0;
  $('mBusy').textContent = summary.busy ?? 0;
  $('lastUpdated').textContent = `Last updated ${fmtTime(summary.generated_at)}`;
  syncServerClock(summary.generated_at);
}

function applyFilters(calls) {
  const statusFilter = $('filterStatus').value;
  const search = $('filterSearch').value.trim().toLowerCase();
  return calls.filter((call) => {
    if (statusFilter && statusGroup(call.status) !== statusFilter) return false;
    if (!search) return true;
    const haystack = [call.phone_number, purposeOf(call), call.call_id].join(' ').toLowerCase();
    return haystack.includes(search);
  });
}

function renderCalls(calls) {
  const body = $('callsBody');
  if (!calls.length) {
    body.innerHTML = '<tr><td colspan="7" class="muted">No calls match the current filters.</td></tr>';
    return;
  }
  body.innerHTML = calls.map((call) => `
    <tr data-call-id="${escapeHtml(call.call_id)}">
      <td><span class="badge ${statusClass(call.status)}">${escapeHtml(call.status || 'unknown')}</span></td>
      <td class="mono">${escapeHtml(call.phone_number)}</td>
      <td>${escapeHtml(purposeOf(call))}</td>
      <td class="mono">${escapeHtml([call.sip_status_code, call.sip_status].filter(Boolean).join(' ') || '—')}</td>
      <td>${escapeHtml(call.reason || '—')}</td>
      <td>${escapeHtml(fmtTime(call.updated_at))}</td>
      <td class="mono muted">${escapeHtml(call.call_id)}</td>
    </tr>`).join('');
}

function updateAgentToggleBtn(running) {
  const btn = $('agentToggleBtn');
  if (running) {
    btn.textContent = 'Kill Switch';
    btn.style.background = 'linear-gradient(135deg, #ef4444, #f43f5e)';
    btn.style.color = '#ffffff';
  } else {
    btn.textContent = 'Start Agent';
    btn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
    btn.style.color = '#ffffff';
  }
}

async function loadDashboard() {
  try {
    setError('');
    const limit = $('limit').value;
    const data = await api(`/dashboard/data?limit=${encodeURIComponent(limit)}`);
    renderSummary(data.summary || {});
    allCalls = data.calls || [];
    renderCalls(applyFilters(allCalls));
    updateAgentToggleBtn(data.agent_running);
    if (data.agent_error) {
      setError(data.agent_error);
    }
  } catch (error) {
    setError(error.message);
  }
}

$('filterStatus').addEventListener('change', () => renderCalls(applyFilters(allCalls)));
$('filterSearch').addEventListener('input', () => renderCalls(applyFilters(allCalls)));

function renderDetail(call) {
  activeCall = call;
  const events = call.events || [];
  $('detailPane').innerHTML = `
    <div class="detail-head">
      <span class="badge ${statusClass(call.status)}">${escapeHtml(call.status || 'unknown')}</span>
      <h3>${escapeHtml(call.phone_number || 'Unknown number')}</h3>
      <div class="mono muted">${escapeHtml(call.call_id)}</div>
    </div>
    <div class="kv"><span>Purpose</span><strong>${escapeHtml(purposeOf(call))}</strong></div>
    <div class="kv"><span>Reason</span><strong>${escapeHtml(call.reason || '—')}</strong></div>
    <div class="kv"><span>SIP</span><strong class="mono">${escapeHtml([call.sip_status_code, call.sip_status].filter(Boolean).join(' ') || '—')}</strong></div>
    <div class="kv-block">
      <span>Recording</span>
      <div style="display: flex; align-items: center; gap: 10px; width: 100%;">
        ${call.recording_url ? `
          <div class="recording-player" style="flex: 1;">
            <audio controls preload="metadata" src="/calls/${encodeURIComponent(call.call_id)}/recording">Audio not supported.</audio>
            <div class="muted" style="font-size:12px">${escapeHtml(call.recording_source || 'recording')}${call.recording_duration_ms ? ` · ${Math.round(Number(call.recording_duration_ms) / 1000)}s` : ''}</div>
          </div>
        ` : `
          <span class="muted" style="flex: 1;">Not available yet</span>
          <button class="btn-action" id="btnFetchRecording" data-call-id="${escapeHtml(call.call_id)}">Fetch</button>
        `}
      </div>
    </div>
    <div class="kv">
      <span>Transcript</span>
      <strong style="display: flex; align-items: center; gap: 10px;">
        ${escapeHtml(call.transcript_source || '—')}
        ${call.transcript_text ? `
          <button class="btn-secondary" id="btnViewTranscript">View Full Transcript</button>
        ` : ''}
      </strong>
    </div>
    <div class="kv"><span>Error</span><strong>${escapeHtml(call.error || '—')}</strong></div>
    <div class="kv"><span>Updated</span><strong>${escapeHtml(fmtTime(call.updated_at))}</strong></div>
    <div class="timeline">
      ${events.map((event) => `
        <div class="event">
          <div class="dot"></div>
          <div class="event-body">
            <div class="event-title">${escapeHtml(event.status)} ${event.reason ? `· ${escapeHtml(event.reason)}` : ''}</div>
            <div class="event-meta">${escapeHtml(event.message || 'Status updated')} · ${escapeHtml(fmtTime(event.created_at))}</div>
            ${event.sip_status_code || event.sip_status ? `<div class="event-meta mono">SIP ${escapeHtml([event.sip_status_code, event.sip_status].filter(Boolean).join(' '))}</div>` : ''}
          </div>
        </div>`).join('') || '<div class="muted">No events recorded.</div>'}
    </div>`;
}

async function loadCall(callId) {
  try {
    setError('');
    const call = await api(`/calls/${encodeURIComponent(callId)}`);
    renderDetail(call);
  } catch (error) {
    setError(error.message);
  }
}

$('detailPane').addEventListener('click', async (event) => {
  const btnFetch = event.target.closest('#btnFetchRecording');
  if (btnFetch) {
    const callId = btnFetch.dataset.callId;
    const originalText = btnFetch.textContent;
    try {
      btnFetch.disabled = true;
      btnFetch.textContent = 'Fetching...';
      const result = await api(`/calls/${encodeURIComponent(callId)}/refresh-recording`, { method: 'POST' });
      if (result.ok || (result.recording && result.recording.recording_url)) {
        await loadCall(callId);
      } else {
        alert('Recording not available yet. Please try again later.');
      }
    } catch (error) {
      alert('Failed to fetch recording: ' + error.message);
    } finally {
      btnFetch.disabled = false;
      btnFetch.textContent = originalText;
    }
  }

  const btnViewTranscript = event.target.closest('#btnViewTranscript');
  if (btnViewTranscript && activeCall && activeCall.transcript_text) {
    $('modalTranscriptText').textContent = activeCall.transcript_text;
    $('transcriptModal').showModal();
  }
});

$('agentToggleBtn').addEventListener('click', async () => {
  const btn = $('agentToggleBtn');
  const isRunning = btn.textContent === 'Kill Switch';

  if (isRunning) {
    if (!confirm('Are you sure you want to trigger the Kill Switch? This will stop the calling process and cut all active calls immediately.')) {
      return;
    }
    try {
      btn.disabled = true;
      btn.textContent = 'Stopping...';
      const res = await api('/agent/kill', { method: 'POST' });
      alert(res.message || 'Kill Switch triggered.');
      await loadDashboard();
    } catch (err) {
      alert('Failed to kill agent: ' + err.message);
    } finally {
      btn.disabled = false;
    }
  } else {
    try {
      btn.disabled = true;
      btn.textContent = 'Starting...';
      const res = await api('/agent/start', { method: 'POST' });
      alert(res.message || 'Agent started successfully.');
      await loadDashboard();
    } catch (err) {
      alert('Failed to start agent: ' + err.message);
    } finally {
      btn.disabled = false;
    }
  }
});

$('refresh').addEventListener('click', loadDashboard);
$('callsBody').addEventListener('click', (event) => {
  const row = event.target.closest('tr[data-call-id]');
  if (row) loadCall(row.dataset.callId);
});

// ---------- Settings modal ----------

function showFieldMessage(el, message, kind) {
  el.className = kind === 'error' ? 'field-warning' : kind === 'warning' ? 'field-warning' : 'field-success';
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

async function loadSettingsModal() {
  const spreadsheetResult = $('spreadsheetResult');
  const windowResult = $('windowResult');
  spreadsheetResult.style.display = 'none';
  windowResult.style.display = 'none';

  try {
    const data = await api('/settings/spreadsheet');
    $('spreadsheetUrl').value = data.spreadsheet_url || '';
    if (data.warnings && data.warnings.length) {
      showFieldMessage(spreadsheetResult, `Configured, with warnings: ${data.warnings.join('; ')}`, 'warning');
    } else if (data.spreadsheet_id) {
      showFieldMessage(spreadsheetResult, `Configured (validated ${fmtTime(data.validated_at)}).`, 'success');
    }
  } catch (err) {
    // no spreadsheet configured yet, leave field blank
  }

  try {
    const data = await api('/settings/calling-window');
    $('windowEnabled').checked = !!data.enabled;
    if (data.start) $('windowStart').value = data.start;
    if (data.end) $('windowEnd').value = data.end;
  } catch (err) {
    // defaults stay as-is
  }
}

$('settingsBtn').addEventListener('click', () => {
  loadSettingsModal();
  $('settingsModal').showModal();
});

$('spreadsheetSave').addEventListener('click', async () => {
  const btn = $('spreadsheetSave');
  const resultEl = $('spreadsheetResult');
  const url = $('spreadsheetUrl').value.trim();
  if (!url) return;
  btn.disabled = true;
  btn.textContent = 'Validating…';
  try {
    const data = await api('/settings/spreadsheet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spreadsheet_url: url }),
    });
    if (data.warnings && data.warnings.length) {
      showFieldMessage(resultEl, `Saved, with warnings: ${data.warnings.join('; ')}`, 'warning');
    } else {
      showFieldMessage(resultEl, 'Spreadsheet validated and saved.', 'success');
    }
  } catch (err) {
    showFieldMessage(resultEl, `Validation failed: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Validate & Save';
  }
});

$('windowSave').addEventListener('click', async () => {
  const btn = $('windowSave');
  const resultEl = $('windowResult');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    await api('/settings/calling-window', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: $('windowEnabled').checked,
        start: $('windowStart').value,
        end: $('windowEnd').value,
      }),
    });
    showFieldMessage(resultEl, 'Calling window saved.', 'success');
  } catch (err) {
    showFieldMessage(resultEl, `Failed to save: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save window';
  }
});

bootstrap();
