# -*- coding: utf-8 -*-
"""Self-contained HTML dashboard for LiveKit call-control status."""


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LiveKit Call Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #09111f;
      --panel: rgba(15, 23, 42, 0.78);
      --panel-strong: rgba(15, 23, 42, 0.96);
      --text: #e5edf9;
      --muted: #93a4ba;
      --line: rgba(148, 163, 184, 0.18);
      --blue: #60a5fa;
      --cyan: #22d3ee;
      --green: #34d399;
      --amber: #f59e0b;
      --red: #fb7185;
      --violet: #a78bfa;
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.38);
      --radius: 24px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 20% 12%, rgba(34, 211, 238, 0.18), transparent 32rem),
        radial-gradient(circle at 86% 2%, rgba(167, 139, 250, 0.16), transparent 28rem),
        radial-gradient(circle at 72% 92%, rgba(52, 211, 153, 0.10), transparent 26rem),
        var(--bg);
      color: var(--text);
    }

    .shell { width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 40px; }

    .hero {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 20px;
      align-items: stretch;
      margin-bottom: 20px;
    }

    .card {
      background: linear-gradient(180deg, rgba(15, 23, 42, 0.86), rgba(15, 23, 42, 0.64));
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px);
    }

    .intro { padding: 30px; position: relative; overflow: hidden; }
    .intro::after {
      content: "";
      position: absolute;
      inset: auto -70px -120px auto;
      width: 260px;
      height: 260px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(96, 165, 250, 0.28), transparent 65%);
    }

    .eyebrow { color: var(--cyan); font-size: 13px; letter-spacing: .16em; text-transform: uppercase; font-weight: 800; }
    h1 { margin: 10px 0 8px; font-size: clamp(34px, 5vw, 58px); line-height: 0.95; letter-spacing: -0.055em; }
    .subtitle { max-width: 760px; margin: 0; color: var(--muted); font-size: 16px; line-height: 1.65; }

    .controls { padding: 22px; display: grid; gap: 14px; }
    label { display: block; color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 7px; }
    input, select, button {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 15px;
      background: rgba(2, 6, 23, 0.58);
      color: var(--text);
      padding: 12px 14px;
      outline: none;
      font: inherit;
    }
    input:focus, select:focus { border-color: rgba(96, 165, 250, .7); box-shadow: 0 0 0 4px rgba(96, 165, 250, .13); }
    button { cursor: pointer; border: 0; background: linear-gradient(135deg, var(--blue), var(--cyan)); color: #031323; font-weight: 900; }
    button:hover { filter: brightness(1.06); }
    .control-row { display: grid; grid-template-columns: 1fr 130px; gap: 10px; }
    .check-row { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 14px; }
    .check-row input { width: auto; }

    .metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 20px; }
    .metric { padding: 18px; position: relative; overflow: hidden; }
    .metric .value { font-size: 34px; font-weight: 900; letter-spacing: -.04em; margin-top: 8px; }
    .metric .label { color: var(--muted); font-size: 13px; font-weight: 750; }
    .metric::after { content: ""; position: absolute; width: 80px; height: 80px; border-radius: 999px; right: -24px; bottom: -30px; opacity: .22; background: var(--accent, var(--blue)); }

    .grid { display: grid; grid-template-columns: 1.5fr .8fr; gap: 20px; }
    .section-title { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px 0; }
    .section-title h2 { margin: 0; font-size: 18px; letter-spacing: -.02em; }
    .hint { color: var(--muted); font-size: 13px; }

    .table-wrap { overflow: auto; padding: 12px 14px 16px; }
    table { width: 100%; border-collapse: collapse; min-width: 900px; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; text-align: left; padding: 12px 10px; border-bottom: 1px solid var(--line); }
    td { padding: 14px 10px; border-bottom: 1px solid rgba(148, 163, 184, .11); font-size: 14px; vertical-align: middle; }
    tr { cursor: pointer; }
    tbody tr:hover { background: rgba(96, 165, 250, .08); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    .muted { color: var(--muted); }

    .badge { display: inline-flex; align-items: center; gap: 7px; padding: 7px 10px; border-radius: 999px; font-weight: 850; font-size: 12px; border: 1px solid currentColor; }
    .badge::before { content: ""; width: 7px; height: 7px; border-radius: 999px; background: currentColor; box-shadow: 0 0 16px currentColor; }
    .status-live { color: var(--cyan); background: rgba(34, 211, 238, .10); }
    .status-ok { color: var(--green); background: rgba(52, 211, 153, .10); }
    .status-warn { color: var(--amber); background: rgba(245, 158, 11, .10); }
    .status-fail { color: var(--red); background: rgba(251, 113, 133, .10); }
    .status-idle { color: var(--violet); background: rgba(167, 139, 250, .10); }

    .detail { padding: 18px 20px 20px; min-height: 520px; }
    .empty { min-height: 430px; display: grid; place-items: center; color: var(--muted); text-align: center; border: 1px dashed var(--line); border-radius: 20px; }
    .detail-head { display: grid; gap: 8px; margin-bottom: 18px; }
    .detail-head h3 { margin: 0; font-size: 22px; letter-spacing: -.03em; }
    .kv { display: grid; grid-template-columns: 110px 1fr; gap: 10px; padding: 9px 0; border-bottom: 1px solid rgba(148, 163, 184, .11); }
    .kv span:first-child { color: var(--muted); }
    .kv-block { display: flex; flex-direction: column; gap: 6px; padding: 9px 0; border-bottom: 1px solid rgba(148, 163, 184, .11); }
    .kv-block span:first-child { color: var(--muted); }
    .recording-player { display: grid; gap: 6px; }
    .recording-player audio { width: 100%; height: 36px; }

    .timeline { margin-top: 18px; display: grid; gap: 12px; }
    .event { display: grid; grid-template-columns: 20px 1fr; gap: 10px; }
    .dot { width: 12px; height: 12px; margin-top: 5px; border-radius: 999px; background: var(--blue); box-shadow: 0 0 18px var(--blue); }
    .event-body { padding-bottom: 12px; border-bottom: 1px solid rgba(148, 163, 184, .10); }
    .event-title { font-weight: 850; }
    .event-meta { color: var(--muted); font-size: 12px; margin-top: 2px; }

    .error { display: none; margin: 0 0 14px; padding: 12px 14px; border-radius: 16px; color: #fecdd3; background: rgba(127, 29, 29, .36); border: 1px solid rgba(251, 113, 133, .32); }

    @media (max-width: 980px) {
      .hero, .grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 600px) {
      .shell { width: min(100vw - 20px, 1440px); padding-top: 12px; }
      .metrics { grid-template-columns: 1fr; }
      .control-row { grid-template-columns: 1fr; }
    }
    dialog::backdrop {
      background: rgba(2, 6, 23, 0.75);
      backdrop-filter: blur(8px);
    }
    dialog {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel-strong);
      color: var(--text);
      padding: 30px;
      max-width: 600px;
      width: calc(100vw - 32px);
      box-shadow: var(--shadow);
      backdrop-filter: blur(25px);
      outline: none;
    }
    .modal-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 12px;
    }
    .modal-head h3 { margin: 0; font-size: 20px; }
    .modal-close {
      width: auto;
      padding: 6px 12px;
      font-size: 14px;
      border-radius: 8px;
      margin: 0;
      background: rgba(255, 255, 255, 0.1);
      color: var(--text);
      border: 0;
      cursor: pointer;
      font-weight: bold;
    }
    .modal-close:hover {
      background: rgba(255, 255, 255, 0.2);
    }
    .btn-action {
      width: auto;
      padding: 6px 12px;
      font-size: 12px;
      border-radius: 8px;
      margin: 0;
      background: linear-gradient(135deg, var(--blue), var(--cyan));
      color: #031323;
      font-weight: bold;
      border: 0;
      cursor: pointer;
    }
    .btn-action:hover {
      filter: brightness(1.08);
    }
    .btn-secondary {
      width: auto;
      padding: 4px 10px;
      font-size: 11px;
      border-radius: 6px;
      margin: 0;
      background: rgba(96, 165, 250, 0.15);
      border: 1px solid rgba(96, 165, 250, 0.3);
      color: var(--blue);
      cursor: pointer;
      font-weight: bold;
    }
    .btn-secondary:hover {
      background: rgba(96, 165, 250, 0.25);
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="card intro">
        <div class="eyebrow">LiveKit outbound telemetry</div>
        <h1>Call Control Dashboard</h1>
        <p class="subtitle">Monitor Hermes-triggered PSTN calls from the local SQLite status store. Track dispatch, dialing, answered calls, carrier failures, SIP codes, and the exact event timeline for each call.</p>
      </div>
      <div class="card controls">
        <div>
          <label for="token">Call API token</label>
          <input id="token" type="password" placeholder="Paste CALL_API_TOKEN" autocomplete="off" />
        </div>
        <div class="control-row">
          <div>
            <label for="limit">Rows</label>
            <select id="limit"><option>25</option><option selected>100</option><option>250</option><option>500</option></select>
          </div>
          <div>
            <label>&nbsp;</label>
            <button id="refresh">Refresh</button>
          </div>
        </div>
        <label class="check-row"><input id="auto" type="checkbox" checked /> Auto-refresh every 5s</label>
        <div class="hint" id="lastUpdated">Waiting for data…</div>
      </div>
    </section>

    <p class="error" id="errorBox"></p>

    <section class="metrics">
      <div class="card metric" style="--accent: var(--blue)"><div class="label">Total calls</div><div class="value" id="mTotal">—</div></div>
      <div class="card metric" style="--accent: var(--cyan)"><div class="label">Live / dialing</div><div class="value" id="mLive">—</div></div>
      <div class="card metric" style="--accent: var(--green)"><div class="label">Connected</div><div class="value" id="mConnected">—</div></div>
      <div class="card metric" style="--accent: var(--red)"><div class="label">Failures</div><div class="value" id="mFailures">—</div></div>
      <div class="card metric" style="--accent: var(--amber)"><div class="label">Busy</div><div class="value" id="mBusy">—</div></div>
    </section>

    <section class="grid">
      <div class="card">
        <div class="section-title"><h2>Recent calls</h2><span class="hint">Click a row for event history</span></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Status</th><th>Phone</th><th>Purpose</th><th>SIP</th><th>Reason</th><th>Updated</th><th>Call ID</th></tr></thead>
            <tbody id="callsBody"><tr><td colspan="7" class="muted">Enter token and refresh.</td></tr></tbody>
          </table>
        </div>
      </div>
      <aside class="card detail" id="detailPane">
        <div class="empty">Select a call to inspect its SIP timeline.</div>
      </aside>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const tokenInput = $('token');
    const savedToken = sessionStorage.getItem('callDashboardToken');
    if (savedToken) tokenInput.value = savedToken;

    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const fmtTime = (value) => value ? new Date(value).toLocaleString() : '—';
    const purposeOf = (call) => call.metadata?.call_purpose || call.metadata?.purpose || '—';
    const statusClass = (status) => {
      if (!status) return 'status-idle';
      if (status.startsWith('failed') || status === 'dispatch_failed') return 'status-fail';
      if (['completed', 'answered', 'active'].includes(status)) return 'status-ok';
      if (['dialing', 'dispatching', 'dispatched'].includes(status)) return 'status-live';
      return 'status-idle';
    };

    function setError(message) {
      const box = $('errorBox');
      box.textContent = message || '';
      box.style.display = message ? 'block' : 'none';
    }

    async function api(path, options = {}) {
      const token = tokenInput.value.trim();
      if (!token) throw new Error('Paste CALL_API_TOKEN to load dashboard data.');
      sessionStorage.setItem('callDashboardToken', token);
      const headers = { Authorization: `Bearer ${token}`, Accept: 'application/json', ...options.headers };
      const response = await fetch(path, { ...options, headers });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`${response.status} ${response.statusText}: ${text}`);
      }
      return response.json();
    }

    function renderSummary(summary) {
      $('mTotal').textContent = summary.total ?? 0;
      $('mLive').textContent = summary.live ?? 0;
      $('mConnected').textContent = summary.connected ?? 0;
      $('mFailures').textContent = summary.failures ?? 0;
      $('mBusy').textContent = summary.busy ?? 0;
      $('lastUpdated').textContent = `Last updated ${fmtTime(summary.generated_at)}`;
    }

    function renderCalls(calls) {
      const body = $('callsBody');
      if (!calls.length) {
        body.innerHTML = '<tr><td colspan="7" class="muted">No calls found in the SQLite store.</td></tr>';
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

    async function loadDashboard() {
      try {
        setError('');
        const limit = $('limit').value;
        const data = await api(`/dashboard/data?limit=${encodeURIComponent(limit)}`);
        renderSummary(data.summary || {});
        renderCalls(data.calls || []);
      } catch (error) {
        setError(error.message);
      }
    }

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

    let activeCall = null;

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

    $('refresh').addEventListener('click', loadDashboard);
    $('callsBody').addEventListener('click', (event) => {
      const row = event.target.closest('tr[data-call-id]');
      if (row) loadCall(row.dataset.callId);
    });
    setInterval(() => { if ($('auto').checked && tokenInput.value.trim()) loadDashboard(); }, 5000);
    if (tokenInput.value.trim()) loadDashboard();
  </script>
  <dialog id="transcriptModal">
    <div class="modal-head">
      <h3>Conversation Transcript</h3>
      <button class="modal-close" onclick="document.getElementById('transcriptModal').close()">Close</button>
    </div>
    <div id="modalTranscriptText" style="white-space: pre-wrap; max-height: 450px; overflow-y: auto; font-family: inherit; line-height: 1.65; font-size: 15px; color: var(--text);"></div>
  </dialog>
</body>
</html>
"""


def dashboard_html() -> str:
    return DASHBOARD_HTML
