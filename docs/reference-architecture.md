# Reference architecture for an AI-agent project

A project-agnostic standard: the layers, names, domain classes and abstractions
every agent project should have, so each new project (a messaging-app agent,
restaurant ordering agent, anything else) starts from the same vocabulary and only
the parts that are really project-specific change.

The names follow what the ecosystem has converged on (Anthropic's *Building
effective agents*, the OpenAI Agents SDK, Pydantic AI, MCP), so they transfer to
any codebase or interview conversation.

---

## 1. The big idea: kernel vs. product

Every agent project has two halves:

| | **Agent kernel** (reusable) | **Product** (changes per project) |
|---|---|---|
| What | The loop, model access, tools machinery, permission boundary, memory, tracing, evals | The business domain, the concrete tools, the channels, the policies, the prompts |
| Where | `platform/`, `core/`, `evals/` harness | `domain/`, `adapters/`, `edges/`, agent specs, eval datasets |
| Test | If you rewrote it for a new project, you did it wrong | If it is in the kernel, the abstraction is in the wrong place |

**Litmus test:** a new project should mean a new domain package, new tools and
adapters, a new channel edge and new eval cases. It should never require editing the
loop.

---

## 2. The layers (dependencies point inward)

```
L4  edges/        Channels in/out, HTTP API, workers/queues          ← how the world talks to us
L3  adapters/     Tools, integrations, MCP clients, port impls       ← how we talk to the world
L2  core/         AgentRunner, ContextBuilder, ToolExecutor, Policy,
                  ApprovalGate, Memory, Guardrails                   ← the agent kernel
L1  domain/       Product entities + agent entities (pure, Pydantic) ← the nouns
L0  platform/     Settings, model vocabulary, ports (no vendor code) ← primitives and ports
─── cross-cutting: observability (traces, usage, cost) · evals
```

Rules:
- An outer layer may import an inner one, never the reverse. `core/` never imports
  `edges/` or `adapters/`. It sees them only through **Protocols** defined in inner
  layers (ports & adapters).
- `core/` is unit-testable with fakes: no network, no DB, no real model.
- **Ports inside, implementations outside.** `platform/` defines primitives and
  ports and imports no vendor SDK. Anything that calls a vendor (the Anthropic
  provider, a SQLite store, a Langfuse tracer) is an adapter in `adapters/`. Inner
  layers should be the most stable code, and vendor code is the least stable.
- **Kernel vs. product in folders.** Kernel code sits at the top of each layer;
  product code goes in a subpackage named after the product (`whatsapp/`). The
  kernel never imports it, so lifting the kernel means taking everything else.
  `core/` has no product subpackage at all.
- Cross-cutting concerns (tracing, metering, retries) **wrap** a port as a decorator
  (`TracedModelProvider(inner)`) instead of being edited into the loop.

---

## 3. The standard vocabulary

### L0 · Platform (primitives and ports)

| Name | Responsibility |
|---|---|
| `Settings` | Typed config, validated at boot (fail fast). Nobody reads `os.environ` directly. |
| `ModelProvider` | The only door to an LLM. `generate(ModelRequest) -> ModelResponse`. Vendor SDKs live behind it. |
| `ModelRequest` / `ModelResponse` | Provider-neutral request (system, messages, tools, limits) and response (content blocks, `stop_reason`, `Usage`). |
| `Usage` | Input/output/cache tokens per call. The raw material for cost. |
| `OpaqueBlock` | A provider block the kernel doesn't interpret (e.g. `thinking`), kept verbatim so it can be replayed unchanged. |
| `ModelProviderError` | The SDK's exceptions translated, with `retryable`, so the core never imports a vendor SDK. |
| `SessionStore` | Load/append the conversation of a session. |
| `Repository[T]` | Persistence for domain entities (one per aggregate). |
| `SecretStore` | Per-tenant/per-user credentials, decrypted just in time. |
| `Tracer` | Spans for run → step → model call → tool call. |
| `Clock` | Injected "now", so tests and prompts are deterministic. |

### L1 · Domain (pure models, no I/O)

**Agent entities. Same in every project:**

| Name | What it is |
|---|---|
| `Principal` | *Who* is acting: the end user, plus their roles. Every permission check starts here. |
| `Tenant` | *Whose* data and config this is (a workspace, a restaurant). Can equal the principal in single-user products. |
| `Session` | A conversation thread (a messaging channel, WhatsApp chat, phone call). |
| `InboundEvent` | A normalized input: `tenant_id, session_id, principal_id, event_id (idempotency), content`. Every channel produces this. |
| `Message` | One entry in history: `role` + list of `ContentBlock`. |
| `ContentBlock` | `TextBlock` · `ToolUseBlock` · `ToolResultBlock` (· `ImageBlock`, `AudioBlock`). |
| `AgentSpec` | An agent as **data**: `name, instructions, model, tool_names, limits`. Adding an agent shouldn't need code. |
| `RunLimits` | `max_steps, max_tokens, max_cost, timeout`. |
| `PendingAction` | A tool call parked waiting for a human decision. |
| `MemoryRecord` | A durable fact or summary that outlives the window (preferences, distilled history). |

**Product entities. Different per project.** For example, a messaging product has
`Channel`, `Connector` and `ConnectorAccount`; a restaurant has `Cart`, `Order`, `Catalog` and
`Reservation`. Rule: **business rules and maths live here, in deterministic
code.** The model never computes prices, permissions or state transitions.

### L2 · Core (the agent kernel)

| Name | Responsibility |
|---|---|
| `RunContext` | Everything a run needs, passed explicitly: `principal, tenant, session, spec`. No globals. Dependencies (stores, clock) go in constructors. |
| `AgentRunner` | The loop: `run(ctx, event, history) -> RunResult`. Calls the model, runs every tool call through `ToolExecutor`, stops on `end_turn`, `max_steps`, `max_tokens`, a refusal, or a parked call. Knows nothing about channels, vendors or storage, and never phrases text for the user. Model errors propagate to the caller. |
| `RunResult` | `output` (the model's last text), `new_messages`, `usage`, `steps`, `pending`, `stop: completed \| awaiting_approval \| limit_reached \| refused \| incomplete`. |
| `ContextBuilder` | Assembles the system prompt: a **stable** part that ends with a cache breakpoint + a **volatile** part after it (date/time, per-turn state). Kernel default `InstructionsContext`. The provider also caches the tool list and the conversation tail within a run. |
| `ToolRegistry` | Which tools exist, and `for_context(ctx) -> list[Tool]`: the per-turn catalog for this principal/tenant/agent. Kernel default `StaticToolRegistry`: an agent sees only the tools its `AgentSpec.tool_names` lists (none by default). |
| `ToolExecutor` | **The single permission boundary.** `execute(call, ctx) -> ToolResult`. Kernel `PolicyExecutor`, in order: re-check the tool is visible to this agent → validate args → ask `Policy` → park or run → turn exceptions into readable errors. Never raises for tool failures. |
| `Policy` | `authorize(call, spec, ctx) -> Allow \| Deny(reason) \| RequireApproval`. Kernel building blocks: `AllowAll`, `ConfirmWrites(auto_approved)`, `AllOf(...)` (any Deny wins, then RequireApproval). Products add their rules (sender-scoping, tenant isolation, order state) and compose them with `AllOf`. |
| `ApprovalGate` | Human-in-the-loop: parks the exact call as a `PendingAction` (with expiry), ends the turn. `decide(action_id, approved, ctx)` refuses anyone but the requester in the same session, claims the action once, and runs it unchanged through `execute_approved` (which re-checks everything but the approval). The model then narrates the real outcome. **The harness asks, never the model.** |
| `Memory` | Short-term: the last N turns **rebuilt as plain text** (user words + final answer). Earlier runs' thinking blocks are bound to a prompt that has since changed, and old tool payloads go stale, so neither is replayed. The `SessionStore` keeps everything verbatim. Long-term (`MemoryRecord`s, summaries) comes later. |
| `TurnService` | One event, start to finish: skip duplicates → load history → run → save. Also `decide()` → narrate. Serializes runs per session; saves nothing when a run fails, so a retry starts clean. |
| `Guardrail` | Input/output checks around a run (topic, PII, format). Pre/post hooks, not prompt instructions. |
| `Retriever` | `search(query, ctx) -> list[Chunk]` when knowledge doesn't fit in the prompt (RAG). Optional. |
| `Handoff` | Transfer to another agent or a human. Optional. |

### L3 · Adapters (how we touch the world)

| Name | Responsibility |
|---|---|
| `Tool` | `ToolSpec` + async handler. The only contract the model sees. |
| `ToolSpec` | `name, description, input_schema (from Pydantic), effect: READ \| WRITE`. The `effect` drives `Policy`. |
| `ToolCall` / `ToolResult` | What the model asked for / what came back (`content, is_error`, and `pending` when the call was parked for approval). Kept **compact**: trim raw API payloads before they reach the model. |
| `*Adapter` (port) | A product-defined interface implemented per vendor: `CalendarAdapter`, `PosAdapter`, `BillingAdapter`. Tools call domain/ports, never vendor SDKs directly. |
| `MCPClient` | Speaks MCP to an external server. Lives **inside** an adapter, never in the core. |
| Port implementations | `AnthropicModelProvider` (`adapters/models/`), SQLite/in-memory stores (`adapters/stores/`), Langfuse tracer (`adapters/tracing/`). |

### L4 · Edges

| Name | Responsibility |
|---|---|
| `ChannelAdapter` | `parse_inbound(InboundRequest) -> RoutedEvent \| None` (which agent + the `InboundEvent`; `None` = authentic but ignored, e.g. the agent's own echo) and `send(OutboundMessage)`. Raises `Unauthorized` / `BadRequest`. One per channel (CV, WhatsApp, voice, web). |
| `OutboundMessage` | Text plus channel-specific extras (buttons, cards, audio). |
| `Ingress` | `POST /webhooks/{channel}/{hook}`: the adapter verifies the raw body → known agent? → enqueue → 200. 401/400 on rejection, 404 unknown agent, 503 when the queue is down so the channel retries. Dedupe happens in `TurnService` (by `event_id`). |
| `Worker` | Consumes the queue: resolve the agent (`AgentDirectory`) → a waiting approval + a clear yes/no (`ReplyClassifier`) is a decision, anything else a turn (`TurnService`) → the `Responder` decides what to say → `ChannelAdapter.send`. |
| `Responder` | Product: turns a `RunResult` or a decision into what the channel says, including fallbacks (limits, refusals, failures) and how an approval is asked. The loop never phrases user text. |
| `AdminAPI` | REST to manage agent specs, tenants and tools. |

### Cross-cutting · Observability and evals

| Name | Responsibility |
|---|---|
| `Trace` / `Span` | One trace per run; spans per step, model call and tool call; tags `tenant, principal, session`. The `Tracer` port lives in L0; `TracedModelProvider` / `TracedToolExecutor` / `TracedAgentRunner` wrap the ports; implementations (in-memory, Langfuse) are adapters. |
| `CostMeter` | `Usage × price table → cost` per run/tenant (`platform/cost.py`). The tracing decorators put `cost_usd` on each span. |
| `EvalCase` | `input + context (scripted tools, history) + graders + min_pass_rate`. Scripted tools go through the real `ToolExecutor`, so writes park like production. |
| `Dataset` | A versioned list of `EvalCase`s, grown from real failures. |
| `Task` | The thing under test: usually `AgentRunner` with scripted tools. |
| `Grader` | Scores an output: deterministic assert (tool X called with Y) or LLM-as-judge with a rubric. |
| `Experiment` / `Report` | Runs a dataset × N reps, aggregates pass rate, cost and latency, and compares against a baseline. |
| `SimulatedUser` | An LLM that plays the user for multi-turn scenarios. Optional. |

---

## 4. Core interfaces

These are the real signatures in ai-engineering (`src/agent/`), not just a sketch.

```python
# L0 platform/model.py (implemented in adapters/models/anthropic.py)
class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


# L2 core/tools.py
class Tool(Protocol):
    spec: ToolSpec
    args_model: type[BaseModel]

    async def run(self, args: BaseModel, ctx: RunContext) -> ToolResult: ...


class ToolRegistry(Protocol):
    def for_context(self, ctx: RunContext) -> list[Tool]: ...


class ToolExecutor(Protocol):
    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult: ...


# L2 core/policy.py — Decision = Allow | Deny(reason) | RequireApproval
class Policy(Protocol):
    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision: ...


# L2 core/approval.py
class ApprovalGate(Protocol):
    async def park(self, call: ToolUseBlock, ctx: RunContext) -> PendingAction: ...

    async def decide(self, action_id: str, approved: bool) -> ToolResult | None: ...


# L2 core/context.py — Prompt has a stable and a volatile part
class ContextBuilder(Protocol):
    def build(self, ctx: RunContext) -> Prompt: ...


# L2 core/runner.py (implemented by ReasoningLoop) — RunContext and RunResult live in core/run.py
class AgentRunner(Protocol):
    async def run(
        self, ctx: RunContext, event: InboundEvent, history: list[Message]
    ) -> RunResult: ...


# L4 edges/channels.py
class ChannelAdapter(Protocol):
    def parse_inbound(
        self, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> InboundEvent: ...

    async def send(self, message: OutboundMessage) -> None: ...
```

**Where the shared types live.** `Message`, the content blocks and `ToolDefinition`
are the model's wire format, so they sit in L0 next to `ModelRequest` (L0 is the
innermost layer and can't import L1). `ToolUseBlock` doubles as `ToolCall`. L1
re-exports them and adds `ToolSpec` (a `ToolDefinition` plus `effect`), `InboundEvent`,
`AgentSpec`, `RunLimits` and `PendingAction`. `tests/test_architecture.py` fails the
build if any module imports an outer layer.

---

## 5. The request lifecycle in standard names

```
ChannelAdapter.parse_inbound → Ingress (verify, dedupe) → queue → Worker (session lock)
  → TurnService.handle (skip duplicate → Memory.history)
  → AgentRunner.run
      ContextBuilder.build → ModelProvider.generate
      ↳ tool_use → ToolExecutor.execute → Policy.authorize
                    ↳ Allow → Tool → Adapter → domain/vendor
                    ↳ RequireApproval → ApprovalGate.park → stop: awaiting_approval
      ↳ end_turn → RunResult
  → Memory.save (SessionStore.append, verbatim) → ChannelAdapter.send
Approval: ChannelAdapter (tap or voice) → TurnService.decide → ApprovalGate.decide
  → execute_approved → one more run where the model narrates the real result
(every step under one Trace; RunResult.usage → CostMeter)
```

---

## 6. Invariants (the rules that make it an architecture)

1. **One permission boundary.** Every side effect goes through `ToolExecutor`. If a
   guarantee matters, it is enforced there, not in the prompt.
2. **The harness owns control flow.** Approvals, retries, limits, handoffs and stop
   conditions are code. The model decides *what*; the harness decides *whether* and
   *when*.
3. **Deterministic things are code.** Prices, totals, dates, permissions and state
   machines live in `domain/`. The model only picks and explains.
4. **Agents are data.** `AgentSpec` rows, not classes. New agent = new config.
5. **Context has a stable part and a volatile part.** Per-turn data after the cache
   breakpoint, or caching breaks for everyone. Always inject today's date.
6. **Tool results are compact and model-readable.** Trim payloads; errors are
   sentences the model can act on.
7. **Everything is traced, and every behaviour bug becomes an eval case first.**
   Fix, then re-measure and record before/after.
8. **Idempotency at the edge, serialization per session.** Dedupe on `event_id`;
   one turn at a time per `Session`.

---

## 7. Same names, three projects

| Standard | Voice-messaging agent (the private reference) | ai-engineering | Restaurant platform |
|---|---|---|---|
| `Principal` | message sender | `InboundEvent.principal_id` | end customer (phone/WA number) |
| `Tenant` | sender (sender-scoped) | `InboundEvent.tenant_id` | restaurant |
| `Session` | Channel | `InboundEvent.session_id` | WhatsApp chat / call |
| `InboundEvent` | webhook payload | `domain/agent.py` ✓ | `InboundEvent` |
| `Message` | turn in channel memory | `platform/model.py` ✓ | message |
| `AgentSpec` | Agent + Skill (Mongo) | `domain/agent.py` ✓ | per-tenant agent config |
| `AgentRunner` | `runAgent` / agent loop | `ReasoningLoop` in `core/runner.py` ✓ | `AgentRuntime` |
| `ContextBuilder` | `buildSystemPrompt` | `InstructionsContext` + cache breakpoints ✓ | `PromptBuilder` |
| `ToolRegistry` | `assembleTools` | `StaticToolRegistry` in `core/tools.py` ✓ | `ToolRegistry` |
| `ToolExecutor` + `Policy` | `executeTool` (mapping + sender-scoping + writePolicy) | `PolicyExecutor` + `ConfirmWrites`/`AllOf` ✓ (sender-scoping rule pending) | executor + tenant/order-state policy |
| `ApprovalGate` | ConfirmFlow | `StoreApprovalGate` ✓ | order confirmation (domain state) |
| `*Adapter` | Connector adapters (MCP inside) | `adapters/` | `PosAdapter`, `BillingAdapter` |
| `ChannelAdapter` | webhook + outbound | first product adapter ✓ (HMAC webhook; removed in the WhatsApp pivot) | WhatsApp, voice |
| `Memory` | channel memory + distillation | `WindowMemory` + `TurnService` ✓ (distillation later) | session store |
| Evals | `evals/agent-loop`, reply-classifier | ✓ `src/agent/evaluation/` + `evals/agent_loop/` | per-tenant datasets + simulated customer |

**ai-engineering follows this standard** since the `refactor/standard-architecture`
branch: each channel's own names are mapped onto `session_id`/`principal_id` in
that channel's `ChannelAdapter`, so the kernel can lift into the next project
unchanged.

---

## 8. Recommended build order (any project)

1. `Settings` → `ModelProvider` (+ `Usage`) → one raw call works
2. `Message`/`ContentBlock` → `AgentRunner` with a fake tool → loop works
3. `Tool`/`ToolSpec` → `ToolRegistry` → `ToolExecutor` + `Policy` → boundary works
4. `Tracer` + first `EvalCase`/`Grader` → you can measure from here on
5. `ApprovalGate` → `ContextBuilder` (stable/volatile) → `Memory`
6. Edges: `ChannelAdapter` → `Ingress` → `Worker`
7. Product: domain models + adapters for the real systems
8. Optional: `Retriever`, `Guardrail`, `Handoff`, `SimulatedUser`, deploy
