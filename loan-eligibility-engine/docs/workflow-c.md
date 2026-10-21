# Workflow C — User Notification (README & Guide)

> **Goal:** After Workflow B finishes matching a batch, email each matched user a
> personalized list of the loan products they may qualify for — via **AWS SES** —
> then mark those matches `notified` so no one is emailed twice.
> **Runs locally via Docker Compose** (n8n + Postgres). SES is the only AWS touch
> point, and sends are redirected to a safe test inbox by default.

---

## 1. Trigger & contract

- **Trigger:** webhook `POST /webhook/notify` with `{ "batch_id": "..." }`.
  Workflow B calls this automatically via its `Trigger Workflow C` node once a
  batch's matches are written. A manual "Run Now (demo)" trigger uses the seed
  batch `00000000-0000-0000-0000-000000000001`.
- **Reads:** `matches WHERE batch_id = ? AND notified = FALSE AND is_active`,
  joined to `users` (name, email) and `loan_products` (name, lender, rates).
- **Writes:** flips `matches.notified = TRUE` per user after a send.

This matches the handoff rule in `workflow-b-architecture.md`: *"B writes a
match_runs summary and triggers Workflow C with the batch_id. C then reads
matches WHERE batch_id = ? AND notified = false."*

---

## 2. Demo safety — never email the sample CSV addresses

The assignment is explicit: **do not email the sample CSV addresses**; use
test-inbox aliases (e.g. `abcd+one@gmail.com`) to prove the workflow.

Workflow C enforces this in the **Build Emails** node:
- By default (`DEMO_REDIRECT` unset or `on`) **every** recipient is rewritten to
  a safe test inbox from `DEMO_INBOX`, tagged per user via Gmail `+aliasing`
  (e.g. `abcd+loan@gmail.com` → `abcd+loan-<userId>@gmail.com`). The user's real
  email is carried along only as `original_email` for logging — it is never the
  `To:` address.
- Set `DEMO_REDIRECT=off` to send to real addresses. **Do not** do this with the
  sample data.

Relevant n8n environment variables:
| Var | Default | Meaning |
|---|---|---|
| `DEMO_REDIRECT` | `on` | `on` = redirect all mail to the test inbox |
| `DEMO_INBOX` | `abcd+loan@gmail.com` | the safe test inbox (use YOUR gmail+alias) |
| `SES_SENDER` | `loan-engine@example.com` | verified SES sender/From |

---

## 3. Setup & running

### 1. Import the workflows
1. n8n → Workflows → **Import from File**:
   - `n8n/workflow_c_user_notification.json`
   - `n8n/workflow_c_error_handler.json`
2. In `C - User Notification`, Settings → **Error Workflow** = `C - Error Handler`.
3. Attach credentials:
   - **`Postgres - LoanDB`** on both Postgres nodes.
   - **`AWS`** (access key with `ses:SendEmail`) on the **Send via SES** node.
4. Set the env vars above (docker-compose `n8n.environment` or n8n settings),
   especially `DEMO_INBOX` = your own `you+alias@gmail.com`, and `SES_SENDER` to
   an address verified in SES. **Activate** the workflow so `/webhook/notify` is live.

> SES sandbox: until you request production access, SES only sends to *verified*
> recipients. Verify your test alias (or the whole address) in the SES console.

### 2. Trigger it
- **Automatic:** run Workflow B; its `Trigger Workflow C` node fires `/webhook/notify`.
- **Manual (curl):**
  ```bash
  curl -X POST http://localhost:5678/webhook/notify \
       -H "Content-Type: application/json" \
       -d '{"batch_id": "00000000-0000-0000-0000-000000000001"}'
  ```
- **Canvas:** open the workflow, click **Execute Workflow** (uses the demo batch).

---

## 4. Node-by-node

| # | Node | Type | Purpose |
|---|---|---|---|
| 1 | Webhook | Webhook (POST) | `/webhook/notify`, receives `{batch_id}` from B |
| 2 | Run Now (demo) | Manual Trigger | Canvas test |
| 3 | Set Demo Batch | Set | Injects the demo batch UUID |
| 4 | Validate Input | IF | Requires `batch_id`; else Respond 400 |
| 5 | Respond 400 | Respond to Webhook | `400` on missing/invalid batch |
| 6 | Fetch Matches Per User | Postgres | One row per user; products aggregated as JSON, score DESC; only unnotified active matches |
| 7 | Has Recipients? | IF | If no rows, Respond 200 (nothing to send) |
| 8 | Build Emails | Code | Personalized subject/text/HTML; **redirects To → safe test alias** |
| 9 | Send via SES | AWS SES | Sends the email (continues on per-address error) |
| 10 | Mark Notified | Postgres | `notified=TRUE` per `(batch_id, user_id)` — idempotent |
| 11 | Respond 200 | Respond to Webhook | `{status, batch_id, emails_sent}` |

---

## 5. Verification performed

- **SQL (against a real Postgres 16 with the project schema + seed):**
  - *Fetch Matches Per User* correctly produced one row per user with products
    aggregated as JSON ordered by score (u1 → 2 products [0.82, 0.60], u2 → 1).
  - *Mark Notified* flipped exactly u1's two matches (`UPDATE 2`); a re-run then
    showed only u2 unnotified — proving **no double-emailing** on re-trigger.
- **Build Emails logic (executed with a fake n8n shim):** real addresses
  (`dhruv.iyengar@example.com`, `tara.khan@example.com`) were **never** used as
  `To:` — all redirected to tagged aliases (`abcd+loan-<id>@gmail.com`).
  Personalization (name vs "Hello,"), pluralization, and skipping users with 0
  products all behaved correctly.
- **Not performed:** a live SES send (no AWS account in this environment). The
  SES node is configured per the assignment (AWS SES node in n8n); provide an
  `AWS` credential and a verified sender to send for real.
