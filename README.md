# ai-engineering

A from-scratch **Python** implementation of a channel-scoped agent — built to
learn the AI-engineering stack by hand: foundational-model APIs, a hand-rolled
reasoning loop (the harness), tool calling with a permission boundary, evals,
and deploy.

**Learning project, not a product.** The current product slice: a **WhatsApp
business chatbot** on the Meta WhatsApp Business Cloud API, built as a thin
product edge around a reusable agent kernel.

- Full context: [`docs/00-why.md`](./docs/00-why.md)

## Quickstart

```bash
uv sync                    # Python 3.12 env + deps + this package (editable)
cp .env.example .env       # then fill ANTHROPIC_API_KEY
uv run pytest              # should pass the smoke test
```

## Layout

```
src/agent/
  edges/      L4  ChannelAdapter interface · ingress, worker · whatsapp/ webhook next
  adapters/   L3  models/anthropic.py · stores/, tracing/ · product tools later
  core/       L2  AgentRunner, ContextBuilder, ToolExecutor + Policy, ApprovalGate
  domain/     L1  InboundEvent, AgentSpec, ToolSpec, PendingAction
  platform/   L0  Settings, Message + ModelProvider port, Clock, Tracer port, cost
  evaluation/     eval harness: cases, graders, reps, report (cross-cutting, outermost)
docs/          why · reference architecture · a learning-log note per component
evals/         eval suites (product cases) · run with evals/<suite>/run.py
tests/         mirrors src/agent/ · test_architecture.py enforces the layering

Kernel code sits at the top of each layer; product code lives in a product
subpackage (`whatsapp/`), so the kernel can be lifted into another project.
```

Names follow [`docs/reference-architecture.md`](./docs/reference-architecture.md),
a project-agnostic standard (agent kernel vs. product).
Dependencies point inward: edges know the core; **the core never imports an edge.**

## Commands

```bash
uv run pytest                                  # tests + evals
uv run ruff check . && uv run ruff format .    # lint + format
uv run pytest -m live                          # real API, costs money
uv run python evals/agent_loop/run.py --reps 3 # agent-loop evals, real API, costs money
```
