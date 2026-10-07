# CLAUDE.md

Guidance for Claude Code (and me, Cristian) working in this repo.

## What this is

**ai-engineering** is a personal, from-scratch **Python** implementation of a
channel-scoped agent, built to *learn the full AI-engineering stack by hand*:
consuming a foundational-model API, a hand-rolled reasoning loop (the "harness"),
tool calling with a permission boundary, evals, and deploy.

**Product pivot (Oct 2026):** the target product channel is **WhatsApp**, via
the **Meta WhatsApp Business Cloud API** — the chatbot every business asks for.
The first slice was built against a different messaging product; that edge was
removed when the repo went product-agnostic. Only the product around the kernel
changed — the kernel carried over whole.

It is a **learning project, not a product.** Optimize for understanding, not for
matching production. Go slice by slice; write the hypothesis first, then the code,
then a short note in `docs/learning-log/` on what was learned.

## The standard: `docs/reference-architecture.md`

The code follows a **project-agnostic reference architecture**: an *agent kernel*
(reusable) plus a *product* (WhatsApp here). Class names come from it
(`InboundEvent`, `Message`/`ContentBlock`, `AgentSpec`, `RunContext`, `AgentRunner`,
`ContextBuilder`, `ToolRegistry`, `ToolExecutor`, `Policy`, `ApprovalGate`, ...).
Visual map: https://claude.ai/artifact/TVGHrXUuhY4U5houC8kJ5s. Product names
always translate onto the standard ones at the edge: chat/channel → `Session`,
sender → `Principal`, the permission boundary → `ToolExecutor` + `Policy`,
confirm-flow → `ApprovalGate`.

## The design docs behind the citations

Docstrings and this file cite "(tb-agent doc N)": a private set of layered
architecture design docs (L0–L4) consulted locally, alongside a private
reference implementation in TypeScript. Neither is required to build or
understand this repo — the citations just record where a design came from.
When a component is built, read its matching design doc first; consult the
reference for "how was X actually solved", never to port wholesale.

## The one rule: dependencies point inward

Edges know the core; the core never imports an edge. `tests/test_architecture.py`
enforces three rules: imports point inward; `platform/`, `domain/` and `core/` import
no vendor SDK or web framework; kernel code never imports product code.

```
L4  edges/     channels.py (ChannelAdapter) · ingress · worker · whatsapp/ next
L3  adapters/  models/anthropic.py · stores/, tracing/ (in-memory; SQLite + Langfuse later) · business tools later
L2  core/      runner · context · tools · policy · approval   (kernel only, never product)
L1  domain/    agent.py (InboundEvent, AgentSpec, ToolSpec, PendingAction) · product subpackages later
L0  platform/  config · model (Message, ModelRequest/Response, Usage, ModelProvider port) · clock · tracing port · cost
×   evaluation/  eval harness (outermost: imports anything, nothing imports it)
```

**Kernel vs. product in folders:** kernel code sits at the top of each layer;
product code goes in a product subpackage (`whatsapp/`). **Ports vs. implementations:**
`platform/` holds only primitives and ports (no vendor imports); anything that calls
a vendor (Anthropic, SQLite, Langfuse) is an adapter in `adapters/`. `tests/` mirrors
`src/agent/`.

`core/` (L2) must stay testable with no HTTP, no DB and no real model. Each layer
defines its contracts as `typing.Protocol`s; implementations arrive one component
at a time.

## Decisions carried over from the original design (don't silently reverse)

- **Hand-rolled loop, no LLM framework.** No LangChain / LangGraph. Building the
  harness ourselves *is the point of this repo*.
- **`ToolExecutor` is the single permission boundary** (tb-agent's `executeTool`).
  Every tool call routes through it; it asks `Policy` before running anything.
- **Model access behind a thin `ModelProvider`** (Anthropic first; keep it swappable).
- **Default sender-scoped isolation** — a user only ever touches their own
  connectors/records. Prove it with negative tests. (WhatsApp: a customer's
  `wa_id` only ever reaches that customer's own data.)

## Learning goals

This project targets, in order: APIs/SDK · harness/agents/MCP ·
security/permissions · evals · deploy.

## Stack

- **Python 3.12** (pinned in `.python-version`), managed by **uv**.
- **anthropic** SDK in `adapters/models/anthropic.py`, behind the `ModelProvider` port (`platform/model.py`).
- **pydantic** / **pydantic-settings** for models, tool schemas, and config.
- **FastAPI** + **uvicorn** for L4 edges.
- **pytest** (+ pytest-asyncio) for tests and evals; **ruff** for lint/format.
- Storage: **SQLite** first (in-memory for tests); Postgres later if needed. No
  Mongo / Keycloak / Helm / Argo CD — production infra we deliberately skip.

## First vertical slice (done)

Goal: `inbound event → reason (real Anthropic call) → call one fake tool →
approve → reply`, text only, state in memory. Build order (each got a learning-log
note); ✓ = done:

1. ✓ `Settings` — `platform/config.py` (tb-agent 23)
2. ✓ Standard types and interfaces — `platform/model.py`, `domain/agent.py`, `core/*`,
   `edges/channels.py` (tb-agent 01, reference architecture)
3. ✓ `AnthropicModelProvider` — `adapters/models/anthropic.py`: first real call, with `Usage`
4. ✓ `AgentRunner` — `ReasoningLoop` in `core/runner.py`, tested with fakes (tb-agent 14)
5. ✓ `ToolRegistry` + `ToolExecutor` + `Policy` — `StaticToolRegistry`, `PolicyExecutor`, `ConfirmWrites`/`AllOf` in `core/tools.py`, `core/policy.py` (tb-agent 15, 27). The sender-scoping rule lands in the WhatsApp slice (station 5).
6. ✓ Tracing + evals — `Tracer` port, tracing decorators (`core/observability.py`), `InMemoryTracer`, cost, `InstructionsContext`, eval harness (`src/agent/evaluation/`) + first suite (`evals/agent_loop/`). Langfuse adapter next.
7. ✓ `ApprovalGate` (`StoreApprovalGate`), prompt caching (`Prompt.blocks`, cache breakpoints), `Memory` (`WindowMemory`, text-only window) and `TurnService` (tb-agent 22, 18)
8. ✓ Edge: `Ingress`, `Worker`, `InProcessQueue` (kernel, `edges/`) + a first product edge — webhook adapter, spoken yes/no approvals, voice responder, composition root (tb-agent 10, 17). Removed in the Oct 2026 pivot (see learning-log note 09).

## Second slice: the WhatsApp product edge (current)

Goal: a real business chatbot on WhatsApp — `customer message on WhatsApp →
reason → call a business tool → approve via reply → answer in the chat`. The
kernel is untouched; this slice is a new product around it. Build order (each
gets a learning-log note):

1. ✓ **Webhook verification** — Meta's subscribe handshake is a `GET` with
   `hub.mode` / `hub.verify_token` / `hub.challenge`; the ingress only has POST
   today. Small kernel extension: optional `verify` hook on `ChannelAdapter` +
   `GET /webhooks/{channel}/{hook}` route.
2. ✓ **`Settings`** — `whatsapp_verify_token`, `whatsapp_app_secret` (signs
   webhooks), `whatsapp_access_token`, `whatsapp_phone_number_id`, Graph API
   version.
3. ✓ **`edges/whatsapp/adapter.py`** — `WhatsAppAdapter`: validate
   `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with the app secret);
   parse `entry[].changes[].value.messages[]` (ignore `statuses` and echoes);
   map `wa_id` → `principal_id` = `tenant_id`, chat → `session_id`; outbound via
   `POST graph.facebook.com/<ver>/{phone_number_id}/messages`.
4. ✓ **`edges/whatsapp/responder.py` + replies + composition root** — text
   responder (WhatsApp formatting, 4096-char limit); approvals as text yes/no
   first, interactive reply buttons later.
5. ✓ **The business domain + first real tools** — the pedidos domain (a
   restaurant: `domain/whatsapp/pedidos.py`), the connector
   (`adapters/whatsapp/pedidos.py`: `pedidos__listar` READ + `pedidos__crear`
   WRITE, in-memory store) and the **customer-scoped `Policy`**
   (`AllOf(CustomerScoped(), ConfirmWrites())`) with negative tests. The
   sender-scoped isolation goal, landed.
6. ✓ **SQLite stores** — `SessionStore`, `PendingActions` and the `PedidoStore`
   survive restarts (a parked approval must outlive the process). `aiosqlite`
   in `adapters/stores/sqlite.py` + `adapters/whatsapp/sqlite.py`; set
   `DATABASE_PATH` to turn it on, unset = in-memory.
7. ✓ **Langfuse tracing adapter** — `adapters/tracing/langfuse.py` behind the
   existing `Tracer` port (SDK v4, names → observation types, metadata
   accumulated per span). `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` turn it on; the
   app now always wires the `Traced*` decorators (Noop when off).
8. **Run it for real** — Meta developer app + WhatsApp test number (free, up to
   5 recipients), tunnel (ngrok/cloudflared), register the webhook. Step by
   step: `docs/run-whatsapp.md`. Mind the 24-hour customer-service window
   (irrelevant while the bot only replies).

## How we work

- One component at a time: read the tb-agent doc → state the hypothesis → implement
  → test → write the learning-log note (what it does, how it differs from the TS
  version, open questions).
- Use AI to move faster, but I write the hypothesis and review every line — this
  repo exists so I *understand* it, not just run it.
- Small, focused commits. Branch off `main`; **never push to `main` without asking.**
- Secrets: never commit them. `.env` is git-ignored; keep `.env.example` current.

## Commands

```bash
uv sync                                        # install deps + this package (editable)
uv run pytest                                  # tests + evals
uv run ruff check . && uv run ruff format .    # lint + format
uv run pytest -m live                          # real API, costs money
uv run python evals/agent_loop/run.py --reps 3 # agent-loop evals, real API, costs money
uv run uvicorn agent.edges.whatsapp.app:create_app --factory --reload  # webhook server
```
