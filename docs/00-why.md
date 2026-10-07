# Por qué existe este repo

## La idea

Implementar, **desde cero y en Python**, un agente scoped a un canal de
mensajería: recibe un mensaje, razona con un LLM, llama herramientas en nombre
del usuario y responde. El objetivo no es el producto — es **aprender el stack
de AI engineering con las manos**: consumir la API de un modelo fundacional,
construir el harness (el loop de razonamiento) a mano, tool calling con frontera de
permisos, evals, y despliegue. El producto actual: un **chatbot de negocio en
WhatsApp** (Meta WhatsApp Business Cloud API).

Motivación concreta: la mayoría de vacantes de AI engineer piden **Python**, y es
justo — mucho del ecosistema se hace ahí. Este repo cierra esa brecha con un
proyecto real en vez de tutoriales sueltos.

## El setup poco común que tengo a favor

No parto de una página en blanco. Tengo las dos piezas más difíciles ya resueltas,
en material privado que consulto localmente:

- **El plano — los docs "tb-agent".** Una arquitectura limpia de agentes: 28 docs
  en capas L0–L4, con las decisiones ya tomadas y justificadas. Es el *spec* que
  sigo (las citas "(tb-agent doc N)" en el código apuntan ahí).
- **El edificio — una implementación de referencia.** Un agente real en
  TypeScript/NestJS (~39k líneas). La consulto para ver *cómo lo resolvieron de
  verdad*, nunca para portarla línea por línea.

Reimplementar un diseño conocido-bueno me quita la parte más difícil (qué construir)
y me deja justo la que quiero aprender: **el cómo y el porqué**.

## La regla de oro: las dependencias apuntan hacia adentro

Los edges conocen el core; el core nunca importa un edge. `tests/test_architecture.py`
lo hace cumplir, junto con otras dos reglas: las capas internas no importan SDKs de
vendors, y el código del kernel nunca importa código de producto.

| Capa | Carpeta | Qué vive ahí |
|---|---|---|
| L4 edges | `src/agent/edges/` | `ChannelAdapter` · `Ingress` y `Worker` · `whatsapp/` (el webhook del producto) |
| L3 adapters | `src/agent/adapters/` | todo lo que habla con un vendor: `models/anthropic.py`, stores y tracing · tools del producto en su subpaquete (MCP vive *dentro* del adapter) |
| L2 core | `src/agent/core/` | `AgentRunner` · `ContextBuilder` · `ToolExecutor` + `Policy` (frontera de permisos) · `ApprovalGate` |
| L1 domain | `src/agent/domain/` | `InboundEvent` · `AgentSpec` · `ToolSpec` · `PendingAction` (Pydantic) |
| L0 platform | `src/agent/platform/` | `Settings` · `Message`/`Usage` + el puerto `ModelProvider` · `Clock` (sin SDKs de vendors) |

`core/` (L2) debe poder testearse sin HTTP, sin base de datos y sin modelo real. El
código del producto va en subpaquetes (`whatsapp/`) dentro de cada capa; lo que
queda fuera de ellos es el kernel reusable.

## El estándar: kernel vs. producto

Los nombres siguen una **arquitectura de referencia agnóstica**
([`reference-architecture.md`](./reference-architecture.md), mapa
visual: https://claude.ai/artifact/TVGHrXUuhY4U5houC8kJ5s). El **kernel** (loop,
acceso al modelo, frontera de permisos, aprobaciones, tracing, evals) se escribe una
vez y se reusa; el **producto** (dominio, tools, canales, reglas, prompts) cambia por
proyecto. Los nombres del canal se traducen en el edge: chat → `Session`,
sender → `Principal`.

## El currículo: cada componente → un módulo Python

| # | Componente (estándar) | Módulo | Doc tb-agent |
|---|---|---|---|
| 1 ✓ | `Settings` | `platform/config.py` | 23 |
| 2 ✓ | Tipos e interfaces estándar | `platform/model.py`, `domain/agent.py`, `core/*` | 01 |
| 3 ✓ | `AnthropicModelProvider` | `adapters/models/anthropic.py` | — |
| 4 ✓ | `AgentRunner` (`ReasoningLoop`) | `core/runner.py`, `core/run.py` | 14 |
| 5 ✓ | `ToolRegistry` + `ToolExecutor` + `Policy` | `core/tools.py`, `core/policy.py` | 15, 27 |
| 6 ✓ | Tracing + primeros evals | `core/observability.py`, `src/agent/evaluation/`, `evals/agent_loop/` | — |
| 7 ✓ | `ApprovalGate`, caché del prompt, `Memory`, `TurnService` | `core/approval.py`, `core/context.py`, `core/memory.py`, `core/turns.py` | 22, 18 |
| 8 ✓ | `Ingress` + `Worker` + el primer adapter de producto | `edges/` | 10, 17 |

**Meta de la rebanada 1 (hecha):** `evento entra → razona (llamada real a
Anthropic) → llama 1 tool falso → aprobación → responde`, solo texto, estado en
memoria. **Rebanada 2 (en curso):** el edge de WhatsApp — ver el CLAUDE.md.

## Cómo trabajo

Un componente a la vez: leo el doc de diseño → escribo la hipótesis → implemento →
testeo → dejo una nota en `docs/learning-log/`. Uso IA para ir más rápido, pero
escribo la hipótesis y reviso cada línea — este repo existe para que yo *entienda*,
no solo para que corra.

## Lo que NO es

No es producción, no es un clon 1:1 de la implementación de referencia, y no
reproduce su infraestructura (Mongo, Keycloak, Helm, Argo CD, AWS Secrets). Esa
complejidad enseña *infra*, no *AI engineering* — y arrastra el desorden del
original.
