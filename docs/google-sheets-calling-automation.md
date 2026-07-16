# Google Sheets Calling Automation

This document covers the Google Sheets campaign loop added in `sheet_calling_automation.py`. It supports separate GST and ITR document-collection campaigns where Sheet 1 is the source queue and Sheet 2 is the call log / next-action tracker.

## Where it fits

```text
Google Sheet 1 pending rows
  → sheet_calling_automation.py
      → POST /calls on call_api.py
          → LiveKit room + SIP participant + worker dispatch
              → agent.py support persona campaign rules
                  → schedule_human_callback tool when a human follow-up is needed
      → PostgreSQL call status store
  → sync_completed_calls_to_sheets()
      → append Sheet 2 call log
      → update Sheet 1 Last Comment + Count
```

The automation does **not** replace the worker or Call API. The Call API still owns SIP dialing, status, recordings, and dashboard data. The worker still owns the live conversation.

API, worker, and Sheets sync must point at the same Postgres-backed call status store (`CALL_API_DATABASE_URL` / `CALL_STATUS_DATABASE_URL`).

## Environment

Add these to `.env` and run `uv sync` after pulling the new dependencies:

```env
GOOGLE_SHEETS_SPREADSHEET_ID=<spreadsheet-id>
GOOGLE_SHEETS_CREDS_PATH=.google_sheets_creds.json
LIVEKIT_CALL_API_URL=https://api.yourdomain.com
CALL_API_TOKEN=<same token used by call_api.py>
```

The credentials file is a Google service-account JSON file and must stay uncommitted. `.gitignore` excludes `.google_sheets_creds.json`.

## Expected sheet layout

### Sheet 1: call queue

The automation reads rows with `get_all_records()`, so these headers must exist:

| Header | Usage |
| --- | --- |
| `CID` | Unique client identifier used for duplicate detection and Sheet 2 correlation. |
| `Mobile Number` | Destination phone number passed to `POST /calls`. |
| `Data Received Status` | Only rows whose value is `Pending` are dialed. |
| `Campaign Type` | `GST` or `ITR`. Missing values default to `GST` for existing sheets. |
| `Purpose/Prompt` | Optional call purpose. Defaults according to `Campaign Type`. |
| `Owner name` | Optional `customer_name` for call metadata. |
| `Company Name` | Optional `company_name` for call metadata. |
| `Count` | Existing attempt count; incremented after syncing a completed call. |
| `Workflow Status` | Current workflow state: `Pending AI`, `AI Scheduled`, `Calling`, `Human Help Needed`, `Documents Received`, `Closed`, or `Do Not Call`. |
| `AI Enabled` | `Yes` allows automation to dial; `No` prevents AI follow-up. |
| `Assigned To` | Human owner when help is needed. |
| `Human Status` | Human workflow state such as `Open`, `In Progress`, `Resolved`, or `Closed`. |
| `Help Needed Notes` | AI-generated free-text summary for human handoff. |
| `Last Call Outcome` | Latest call outcome/reason from the Call API. |
| `Next AI Call Date` | Date for the next AI follow-up. |
| `Next AI Call Time` | Time for the next AI follow-up. |
| `AI Attempt Count` | AI attempt counter used with `Max AI Attempts`. |
| `Max AI Attempts` | Maximum AI attempts before the row stops for human review; default `3`. |

The sync step updates Sheet 1 by header name. Keep the header names exact.

### Sheet 2: call log / next action

The automation appends rows in this exact order:

1. `Client Comment`
2. `Next Action`
3. `Next Action Date (DD/MMYYYY)`
4. `Next Action Time (IST)`
5. `CID`
6. `Contact person`
7. `Datetime`
8. `Recording`
9. `Trasncript`
10. `Actor`
11. `Call ID`
12. `Call Outcome`
13. `Help Needed Notes`
14. `Assigned To`
15. `WhatsApp Received`
16. `Promised Date`
17. `Delivery Mode`
18. `Help/Issue`
19. `Callback Time`

The misspellings `DD/MMYYYY` and `Trasncript` are currently part of the sheet contract because the script reads/writes by those labels/positions.

`Next Action` controls future dialing:

- `AI Call` with due date/time at or before now: the automation calls again.
- `AI Call` with future date/time: skip until due.
- `Human`: set Sheet 1 to `Human Help Needed`, disable AI, and expect a human to work the row.
- anything else: skip.

Sheet 1 also controls future dialing. The automation skips rows when `AI Enabled` is `No`, when `Workflow Status` is `Human Help Needed`, `Documents Received`, `Closed`, or `Do Not Call`, or when `AI Attempt Count` has reached `Max AI Attempts`.

## Operational execution

The Google Sheets automation loop runs as a background task inside the Call API container on Dokploy/VPS.

To control the campaign loop:
- **Start Agent**: Call `POST /agent/start` (via the API Dashboard) to begin the background loop. It will scan Sheet 1 and execute a call cycle roughly every 15 seconds.
- **Stop/Kill**: Call `POST /agent/kill` (via the API Dashboard) to stop the background loop, clear the running flag, delete active LiveKit call rooms, and terminate active dials.

## Dashboard controls and API endpoints

The dashboard at `/dashboard` now includes:

- **Start Agent**: calls `POST /agent/start` to begin the Google Sheets automation loop.
- **Kill Switch**: calls `POST /agent/kill`, writes `agent_stop.flag`, stops the loop, deletes active LiveKit rooms, and marks active calls as killed.
- Per-call recording fetch/playback controls.

Related endpoints:

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /agent/status` | Bearer | Return automation running state, active calls, and `agent_error.log` contents. |
| `POST /agent/start` | Bearer | Start the background Sheets automation loop in the Call API process. |
| `POST /agent/kill` | Bearer | Stop the loop and terminate all active LiveKit rooms known to the call status store. |
| `POST /calls/{call_id}/kill` | Bearer | Terminate one call room and mark it killed. |

The loop uses runtime flag/log files in the repo root:

- `agent_running.flag` — created while the background loop is active.
- `agent_stop.flag` — stop request; consumed by the loop.
- `agent_error.log` — last Sheets connection/data-fetch error shown in the dashboard.

These files are operational artifacts and should not be committed.

## Conversation behavior for spreadsheet campaigns

When the outbound call was requested by Google Sheets automation (`requested_by=sheets_automation`), `agent.py` selects the `GST` or `ITR` prompt from `agent_config.json` using the Sheet 1 `Campaign Type` value.

The ITR campaign tells the customer that the required checklist was already sent to their registered WhatsApp number, confirms receipt, records the promised date and delivery mode, and routes missing-message/help cases to the human team. `record_itr_collection_outcome(...)` stores the structured fields in PostgreSQL metadata. A promised date schedules a conditional AI follow-up for 11:00 AM IST on the next working day; the automation places that call only while `Data Received Status` remains `Pending`.

Shared campaign behavior:

- do not collect document details directly;
- do not ask for OTP/WhatsApp verification;
- record an exact promised date before scheduling an AI follow-up;
- if the customer reports blockers or asks for help, call `schedule_human_callback` immediately;
- adapt to the customer's language, confirm the outcome, and end the call.

`schedule_ai_followup(date_str, time_str, client_notes)` writes an `AI Call` next action. `schedule_human_callback(date_str, time_str, client_notes)` writes these fields into the call record metadata:

```json
{
  "next_action": "Human",
  "next_action_date": "30/06/2026",
  "next_action_time": "15:00",
  "callback_time": "30/06/2026 15:00 IST",
  "client_comment": "Customer requested a human callback.",
  "help_needed_notes": "Customer requested a human callback."
}
```

`sync_completed_calls_to_sheets()` later reads that metadata and writes Sheet 2. Calls without a human callback are logged as `AI Call` unless they failed, in which case `Client Comment` includes the failure reason.

## Operational notes

- Do not run multiple automation loops against the same sheet unless there is an external lock; the local flag files are not distributed locks.
- The Call API process must stay alive for the dashboard-started background loop to continue.
- The automation currently posts to `/calls` with a 90-second client timeout. Keep it long enough for the call API's blocking answer/failure behavior.
- Keep `CALL_API_ALLOWED_COUNTRY_PREFIXES` tight before enabling a sheet-driven campaign.
- Confirm the sheet data and approved destination scope before starting a real dialing campaign.
