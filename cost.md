# Vobiz Pricing

## 1. Voice Call Rates (India)

### A. Voice API / WebSocket / Real-time Streaming

- **Inbound calls:** ₹0.65 / minute
- **Outbound calls:** ₹0.65 / minute

### B. SIP Trunking

- **Inbound calls:** ₹0.45 / minute
- **Outbound calls:** ₹0.45 / minute

---

## 2. Phone Numbers (DID)

- **Setup Fee:** Variable one-time purchase fee depending on category (DID Purchase).
- **Monthly Rental:** ₹500 / month per active Vobiz number.
- **Auto-renewals:** Billed automatically on `next_billing_date`. Calls hard-stop if account balance drops to ₹0 (prepaid accounts).

---

## 3. Call & Conference Recording

Storage costs are calculated based on duration and retention time in USD.

- **Billing Increment:** Rounded up to the nearest 60-second interval (minimum 60 seconds).
- **Monthly Storage Rate:** $0.005 USD per minute per month (tracked via `recording_storage_rate`).
- **Retention Policy:**
  - Vobiz console retention window: **30 days**.
  - Use the completion webhook to export files to external storage (e.g. AWS S3) for long-term retention.

---

## 4. AI Transcription / Speech Recognition (ASR)

Billed on Gather or stream execution when speech input is analyzed.

- **Pulse Interval:** **15-second pulses**. Durations are rounded up to the nearest 15 seconds.
  - _Example:_ 35 seconds of speech is billed as 45 seconds (3 pulses).
- **Rate:** **$0.02 USD per 15 seconds** ($0.08 USD per minute).

---

## 5. Capacity Limits (Concurrency & CPS)

- **Base Concurrency:** 3 concurrent calls free for standard accounts.
- **Limit Upgrades:** Higher concurrency and CPS limits require a nominal recurring subscription or a one-time provisioning fee depending on the requested volume.
- **Enterprise Scaling:** Unlimited concurrency and CPS limits are available for high-volume enterprise accounts.

---

## 6. LiveKit Cloud Pricing

### A. Core Infrastructure & Agent (Worker) Hosting

| Plan | Price | Agent Hosting (Worker) | Core WebRTC / SIP Infrastructure |
| :--- | :--- | :--- | :--- |
| **Build** (Free) | $0/mo | - 1,000 agent minutes/mo included<br>- Max 5 concurrent sessions<br>- No cold start prevention | - 5,000 WebRTC minutes/mo included<br>- 1,000 third-party SIP minutes/mo included<br>- Max 100 concurrent connections |
| **Ship** | $50/mo | - 5,000 agent minutes/mo included (then $0.01/min)<br>- Max 20 concurrent sessions<br>- Cold start prevention included | - 150,000 WebRTC minutes/mo included (then $0.0005/min)<br>- 5,000 third-party SIP minutes/mo included (then $0.004/min)<br>- Max 1,000 concurrent connections |
| **Scale** | $500/mo | - 50,000 agent minutes/mo included (then $0.01/min)<br>- Up to 600 concurrent sessions<br>- Cold start prevention included | - 1.5M WebRTC minutes/mo included (then $0.0004/min)<br>- 50,000 third-party SIP minutes/mo included (then $0.003/min)<br>- Max 5,000 concurrent connections |

### B. AI Noise Suppression & Voice Isolation

- **Background Noise Suppression:** (e.g. Krisp NC, ai-coustics QUAIL_L)
  - **Included free** across all plans (Build, Ship, Scale, Enterprise).
- **Voice Isolation:** (e.g. Krisp BVC, Krisp BVCTelephony, ai-coustics Voice Focus 2.1)
  - **Build (Free):** 100 minutes/mo included (**hard cap**; new requests fail once exceeded).
  - **Ship / Scale:** 1,000 minutes/mo (Ship) or 10,000 minutes/mo (Scale) included, then **$0.0012 / minute** overage.


