# Dialogue architecture

This is the durable architecture for the DUDU Car support dialogue. The business rules remain in
the [requirements baseline](requirements-summary.md); release state and operational evidence belong
in [release validation](release-validation.md). The [documentation index](README.md) maps the
remaining policy, operations and evidence records.

## Runtime path

```text
POST /api/chat
  -> ChatbotService.handle directly (prompt/version controls and receipt replay)

verified /webhooks/meta
  -> durable inbound row, deduplication, rate limits and sender ordering
  -> process_inbox claim/lease

Both paths
  -> redact, assess, load typed state and build field references
  -> approved retrieval + minimized context
  -> one LangChain create_agent turn with staged tools and structured DialogueAnswer
  -> local validation and a short locked transaction
  -> conversation, ticket/case, audit, notification and reply rows committed together
  -> API response or WhatsApp outbox worker
```

FastAPI, PostgreSQL, WhatsApp transport, administration, media processing, notifications, retention,
and deployment infrastructure remain the surrounding application. The agent is the only ordinary
dialogue owner: it interprets the current question, answers and clarifies, chooses tools, handles
interruptions and corrections, and proposes the next missing field. Local code remains authoritative
for safety, permissions, exact control text, validation, state changes and receipts.

## Agent and runtime authority

`app/services/dialogue.py` creates one LangChain `create_agent` using the configured hosted
`glm-5.3-flash` model and `ToolStrategy` for the structured `DialogueAnswer` (`answer`, supplied
`citation_ids`, and an optional `next_prompt`). Tool availability is narrowed from the turn-local
state on every model iteration. Tools prepare work in a private working copy; they do not commit SQL,
send messages, enqueue notifications, or claim that an uncommitted operation succeeded.

There is no second dialogue owner, separate graph, persistent LangChain checkpointer, vector database,
second model, or generic tool registry. LangGraph dependencies installed by LangChain are framework
internals; they are not a separately authored workflow.

The seven scoped tools are:

| Tool | Staged responsibility |
| --- | --- |
| `search_knowledge` | Search the governed approved corpus and return excerpt IDs, versions and content. |
| `update_ticket_draft` | Start or update a draft from validated opaque customer-field references. |
| `set_draft_status` | Stage an explicit pause, resume or cancellation. |
| `prepare_ticket_review` | Validate a complete consented draft and prepare the local review prompt. |
| `request_ticket_submission` | Stage one submission authorized by the current prompt and customer reply. |
| `get_owned_case` | Resolve an owned case to a minimized status/update view. |
| `request_case_update` | Prepare or confirm a bounded update or reopening for that owned case. |

The runtime supplies channel, customer identity, current input, prompt/version context, database
access and private field references. `FieldReferences` exposes only opaque IDs to the model; values
resolve locally to the current customer input or an already validated draft. The runtime and domain
validators enforce consent evidence, field bounds, ownership, priority, safety and legal transitions.

## Typed dialogue state and prompt authority

`Conversation.dialogue_data` stores Pydantic `DialogueData` (`schema_version=1`) and
`Conversation.dialogue_revision` is incremented for committed dialogue or evidence changes. A draft
has its own ID/version, `active`/`paused`/`cancelled`/`submitted` status, expiry (60 minutes by
default, configurable from 5–1,440), contact and issue fields, issue/priority proposals, consent
evidence, review flag and evidence group. `DialogueData` also carries a pending offer, unassigned
evidence group, the last case reference, a pending case update and a bounded operation receipt.

`PendingPrompt` records what was actually sent: offer, consent, field, details, review or
case-confirmation purpose, optional field, draft/case reference, version, operation ID and
originating turn. A prompt is authorization context, not a fixed state-machine instruction.

- An offer leaves intake idle; accepting it creates a draft and asks for separate consent.
- Consent is recorded only from the current reply to the matching prompt (or a prompt-bound API
  control). A model argument alone is never evidence.
- Name, valid email, contact phone and issue are required. Fields may arrive in any order; a
  WhatsApp sender number is only a default, and an explicit customer number wins.
- Corrections invalidate review. Done/Skip details and Submit review paths remain available only
  when the current version and evidence authorize them. “No” to optional details does not withdraw
  consent; explicit withdrawal cancels the draft.
- Case updates and reopenings require an owned case and confirmation tied to its case/version.
  Replays return the recorded receipt; unrelated greetings never reopen a case.

## Transactions, inbox and fallback

Provider calls run outside database transactions. The webhook persists a verified, unique inbound
event before acknowledgement. `process_inbox` claims work for up to four concurrent senders, keeps
each sender ordered, and gives a turn a 90-second lease. Immediately before the first model dispatch,
the inbound row records an agent-attempt fence. A known pre-dispatch timeout clears only that owned
marker; after dispatch, recovery does not start another agent loop.

After the bounded turn, `ChatbotService` locks and reloads the conversation and (for WhatsApp) the
inbound claim, then rechecks the dialogue revision, lease, ordering, prompt/version, ownership and
cited-source status. Stale or conflicting proposals are discarded. One short transaction applies the
validated ticket or case operation and conversation state, renders the definitive receipt from the
applied database result, writes audit/notification/transcript/reply rows, and completes the inbound
row when present. The response is exposed only after that transaction commits. API prompt/version
controls and recorded receipts protect retries; arbitrary unkeyed HTTP requests do not receive an
exactly-once guarantee. The outbound worker records uncertain delivery before network I/O.
Meta callbacks reconcile using durable IDs; uncertain SMTP sends require audited manual
reconciliation in the [operations runbook](production-platform.md#uncertain-outbound-delivery).
Unresolved sends hold later messages for the same recipient.

Local controls, provider outage/timeout, invalid or unsafe output, failed grounding, stale state and
the post-dispatch recovery path use the deterministic fallback in `chatbot.py` and
`answer_generation.py`. It calls the same draft validators, preserves useful drafts, and never
performs a hidden mutation.

## Provider bounds and data boundary

Each turn permits at most five model requests and ten native tool calls, including the structured
final response, within 60 seconds. Each model request is capped at 30 seconds, 15,000 input
characters including tools/history/results, and 300 output tokens. SDK retries are disabled; explicit
repair attempts count against the same limits. Runtime metrics record usage, attempts, tools,
fallbacks and latency without prompts, raw values or hidden reasoning. The evaluation runner
accounts for cost with conservative reserves for unknown usage and projects beta spend per inbound
message. The [beta contract](launch-contract.md) governs runtime traffic and spend limits.

The hosted agent path requires both `LLM_ENABLED` and `LLM_CUSTOMER_CONTEXT_ENABLED`;
the local fallback remains available when either is disabled.

The provider receives a minimized current question, at most 12 sanitized recent messages, scalar safe
state and field-presence metadata, opaque references and approved excerpts. The current question,
authoritative draft/control metadata and field references take priority. The input cap includes the
system prompt, tool schemas, history, tool results and excerpts; oldest history is trimmed first,
then whole excerpts, and a confirmation is never truncated into a different meaning. `pii.py`
redacts known secrets, contacts and identifiers; unsafe identifying prose is omitted or kept local.
Raw contacts, ticket or case bodies, attachments, media, secrets and full history never go to the
hosted model. Media is scanned and stored privately outside PostgreSQL and is never sent to the model.
The model proposes text, citations and a next-prompt shape; it cannot set private values, consent,
identifiers, revisions, ownership or commit authority.

## Approved knowledge and citations

Only the active, effective CCO-approved knowledge versions are retrievable. PostgreSQL loads the
complete eligible corpus up to 500 chunks; local Python scoring ranks those rows, including titles and
tags. A larger corpus fails closed into clarification. Website imports are an explicit HTTPS allowlist
and remain inactive until named CCO review and activation. Conflicts are clarified rather than inferred.

The agent may cite only excerpt IDs supplied in the current turn. `dialogue.py` and
`answer_generation.py` reject unknown citations, unsupported numbers/URLs or stronger qualifiers,
unsafe claims, and control text that was not locally authorized. Before commit, cited document keys,
versions and languages must still be active and effective. If retrieval, provider output or source
validation fails, the deterministic approved-knowledge response is used.

## Migration and compatible recovery

Migration `e5c1a2b3d4f6` preflights every legacy `intake_state`/`intake_data` row before converting it
to typed dialogue, preserving draft fields, consent evidence, prompts, case references and media
evidence groups. Dormant legacy columns remain for this release and are not read by the new dialogue
runtime. A failed preflight changes no rows.

After new-format writes, recover with the same runtime and `LLM_ENABLED=false`, or with a compatible
forward fix. Preserve the durable inbox and queued events. An old image cannot safely interpret new
dialogue rows; an old-image rollback requires no new writes or a separately tested reverse conversion.
Never restore an old database snapshot over newer customer records.

Deployment source, live flags and the distinction between repository defaults/local changes and the
last recorded production state are maintained in [release validation](release-validation.md), rather
than inferred from examples in this working tree.

## Key source paths

| Path | Role |
| --- | --- |
| `app/routers/chat.py`, `app/services/chatbot.py` | API entry point, local controls, staged turn and atomic commit. |
| `app/routers/webhooks_meta.py`, `app/services/inbound.py` | Signature validation, durable inbox, media evidence and claims. |
| `app/services/dialogue.py`, `app/services/ticket_drafts.py` | Agent, context, seven tools, typed dialogue and pure validators. |
| `app/services/answer_generation.py`, `app/services/retrieval.py`, `app/services/pii.py` | Local rendering/fallback, approved retrieval and provider minimization. |
| `app/services/tickets.py`, `app/services/ticket_operations.py` | Durable ticket creation, ownership, updates, audit and notifications. |
| `app/services/whatsapp.py`, `app/services/notifications.py`, `app/services/media.py`, `app/services/retention.py` | Outbound reconciliation, admin notifications, private media and lifecycle deletion. |
| `app/models.py`, `migrations/versions/e5c1a2b3d4f6_add_dialogue_state.py` | Durable schema and legacy conversion. |
| `docs/requirements-summary.md`, `docs/launch-contract.md`, `docs/release-validation.md` | Product policy, traffic/rollback limits and current release evidence. |
