# Plan: one notification API for Deligo

## Context

Two documents were handed to Deligo:

| Doc | What it asked of Deligo |
|---|---|
| `Deligo-Notification-API-Playbook.pdf` | Learn ~12 endpoints (subscribe, templates, rules, log, send, …) |
| `Deligo-Notification-Receiver-Spec.pdf` | Build a receiver `POST /api/sales/notification` on their side |

Deligo's answer: they want **one endpoint they call** to send a customer
notification — either pick a template (`event_type`) or pass their own
`title`/`body`, "like sending our own notification". They do not want to build
a receiver, and they do not want the rest of the surface documented.

Everything else stays implemented and keeps working (admin panel, automatic
status-change notifications, push subscribe flow). It just stops being the
thing we hand Deligo.

## Decisions

1. **New route `POST /api/notifications/send`, auth `X-API-Key`.**
   Not `/api/push/send` — that name says "push", but the call goes SSE + push.
   `/api/push/send` stays as a thin alias so the admin panel (`lib/api.ts:911`)
   keeps working untouched.
2. **Two modes in one body, mutually exclusive:**
   - template mode: `event_type` (+ optional `params`)
   - custom mode: `title` + `body`
   Both present → 422. Neither → 422.
3. **Response echoes the rendered copy** (`title`, `body`, `urgency`,
   `tracking_url`, `event_id`, `push_devices`). This is what makes the receiver
   unnecessary for Deligo-initiated sends: they get "what the customer saw"
   synchronously and can write their own timeline row.
4. **Outbound report to Deligo turned off by default** (`DELIGO_NOTIFY_PATH`
   default `""`). Code stays; flipping the env var re-enables it if they ever
   build the receiver.
5. **Explicit sends bypass mute rules.** Mute is keyed on `wfm_status_id`,
   which a manual send does not carry. An operator/Deligo asking to send is
   intent, not a status-change side effect.
6. **Missing placeholders → 422, not a blank sentence.** Template mode
   validates that every `{placeholder}` in the resolved template is satisfiable
   from `params` (after derivation, below).

## Endpoint contract

```
POST /api/notifications/send
X-API-Key: <api key>
Content-Type: application/json
```

### Request

```jsonc
{
  "sales_id": "1773113766311954",        // required
  "sales_number": "ORD-77",               // optional; tracking link + {sales_number}; defaults to sales_id

  // ── mode A: template ──
  "event_type": "delivery_no_answer",     // one of the known event types
  "params": {                             // optional; only needed if the template has placeholders
    "status_description": "Хаалга нээгээгүй"
  },

  // ── mode B: custom copy ──
  "title": "Хүргэлт хойшиллоо",           // 1..120
  "body":  "Жолооч 30 минутын дараа очно.", // 1..400

  // ── both modes, optional ──
  "icon": "campaign",                     // Material Symbols name; template default in mode A, "campaign" in mode B
  "urgency": "high"                       // low | normal | high; template default in mode A, "high" in mode B
}
```

`params` accepted keys (template mode) and what we derive from them:

| param | derived |
|---|---|
| `status_description` | `status_description_line` (" Тайлбар: …" or "") |
| `wfm_status_id` | `status_label` |
| `queue_position` | `queue_position_text` |
| `distance_text`, `formatted_address`, `changed_by_name` | passed through |

Unknown keys in `params` → 422 (same reason as the template editor: typo protection).

### Response `200`

```json
{
  "status": "ok",
  "data": {
    "event_id": "9f2c1ab34de84f0197c5b1d0a6e2f8ce",
    "sales_id": "1773113766311954",
    "sales_number": "ORD-77",
    "event_type": "delivery_no_answer",
    "title": "Утсаа аваагүй",
    "body": "Жолооч тантай холбогдохыг оролдсон боловч утсаа аваагүй байна.",
    "icon": "phone_missed",
    "urgency": "high",
    "tracking_url": "https://map.deligoalpha.mn/track/ORD-77",
    "push_devices": 2,
    "push_enabled": true,
    "sent_at": "2026-09-11T03:18:16.129678+00:00"
  }
}
```

`push_devices: 0` is normal — open tab still gets it over SSE.
Custom mode reports `event_type: "admin_message"`.

### Errors

| code | when |
|---|---|
| 403 | bad / missing `X-API-Key` |
| 404 | `event_type` unknown — `detail` lists the known ones |
| 422 | both or neither mode; missing placeholder; unknown `params` key; bad icon/urgency; length limits |

### Event types (template mode)

`driver_accepted`, `delivery_started`, `delivery_queue_near`, `driver_nearby`,
`delivery_completed`, `delivery_no_answer`, `delivery_unreachable`,
`delivery_tomorrow`, `delivery_later`, `delivery_failed`, `order_cancelled`,
`address_updated`, `status_changed`. (`admin_message` and `driver_location`
are not choosable — one is custom mode itself, the other has no copy.)

## Implementation steps

### 1. Service helper — `src/services/notifications.py`
- `CHOOSABLE_EVENT_TYPES` = `default_templates()` minus `admin_message`, `driver_location`.
- `template_placeholders(event_type) -> set[str]` — parse resolved title+body with `string.Formatter`.
- `enrich_params(params) -> dict` — apply the derivation table above (`describe_status`, `describe_queue_position`, `STATUS_LABEL_BY_WFM_ID`).
- `missing_placeholders(event_type, params) -> list[str]`.
- Move `KNOWN_PLACEHOLDERS` here from `push.py` (push.py imports it) so both the template editor and the new endpoint share one list.

### 2. New router — `src/api/endpoints/notifications.py`
- `router = APIRouter(prefix="/api/notifications", tags=["notifications"])`.
- `SendNotificationRequest` (pydantic): fields above; `model_validator` enforces mode exclusivity.
- `POST /send`, `dependencies=[Depends(require_api_key)]`:
  1. resolve mode → `event_type` + payload dict
  2. template mode: 404 if not choosable; 422 on missing placeholders / unknown params
  3. custom mode: `event_type="admin_message"`, payload `admin_title`/`admin_body`
  4. `event_id = uuid4().hex`; call `publish_order_event(sales_id, event_type, payload, event_id=event_id)` — **needs a new optional `event_id` kwarg** on `publish_order_event` (`src/services/events.py:233`) so the response can return the id the log row is keyed on
  5. render via `build_notification(event_type, sales_id, payload)` for the echo (same function the log uses → identical copy)
  6. `push_devices` from `PushSubscriptionRepository.count_for_sales_id`
- Register in `src/api/api.py` next to `push_router`.

### 3. Alias — `src/api/endpoints/push.py`
- `send_admin_message` body → one-line delegate to the new handler (custom mode). Route and response shape unchanged for the admin panel.

### 4. Default the outbound report off — `src/services/deligo_integration.py:482`
- `DELIGO_NOTIFY_PATH = os.getenv("DELIGO_NOTIFY_PATH", "")`.
- Update the module docstring (line ~21) and `root.env` / `.env` comment: set to `/api/sales/notification` to re-enable.

### 5. Tests — `tests/test_notifications_send.py`
- template mode, no params needed → 200, echo matches template
- template mode with `status_description` → body has "Тайлбар:" clause
- template needing `{queue_position_text}` without params → 422 listing it
- unknown `event_type` → 404 with list
- unknown `params` key → 422
- custom mode → 200, `event_type == "admin_message"`, copy echoed verbatim
- both modes / neither → 422
- missing key → 403
- log row written with returned `event_id` (`NotificationLogRepository.recent`)
- `/api/push/send` still returns old shape

### 6. Docs — the one document
- `docs/Deligo-Notify-API.html` (same style as `docs/notifications.html`) → export `Deligo-Notify-API.pdf` to the repo root. Sections: Auth · Request (both modes side by side) · Params table · Response · Errors · Event type table · curl for each mode · "0 devices is normal".
- Add one request per mode to `docs/deligo-postman.json` under a new folder "Notifications".
- Mark the two old PDFs superseded: move to `docs/archive/` with a one-line README pointing at the new doc. Do **not** delete — they still describe what is deployed.
- `docs/openapi.json` regenerates from the app; no manual edit.

### 7. Not in scope (keep as-is, undocumented for Deligo)
- `/api/push/subscribe|unsubscribe|public-key|test` — browser tracking page
- `/api/push/templates|rules|log|overview` — admin panel
- automatic notifications from `changestatus` / `delivery/start` / queue alerts
- receiver `POST /api/sales/notification` sender code — off by env var

## Open questions for Deligo (ask before writing the doc, not after)

1. Should the reply include `push_devices`, or is `event_id` + copy enough?
2. Idempotency: do they want to send their own `event_id` to make retries safe? Cheap to add (`event_id` optional in request; reuse if present in log within 24h) — but only if they'll actually retry.
3. Do they want a `GET /api/notifications/{event_id}` to read back delivery outcome later? Not in "one API" — say no unless they push.

## Order of work

1 → 2 → 3 → 5 (get green) → 4 → 6. Doc last, from the running endpoint, so the examples are real responses.
