# 03 · Arquitectura estándar (refactor)

## Qué hace

Renombra y reorganiza el proyecto según
[`reference-architecture.md`](../reference-architecture.md): un **kernel** reusable
más un **producto** (el canal de mensajería). Fija todos los tipos y las interfaces
(`typing.Protocol`) de cada capa antes de implementar nada. Cada componente que
sigue ya tiene su contrato escrito, solo falta llenarlo.

- `connectors/` → `adapters/` (L3: tools concretos e integraciones).
- `InboundMessage(channel_id, sender_id, message_id)` →
  `InboundEvent(session_id, principal_id, tenant_id, event_id)`.
- `Turn(role, text)` → `Message(role, content: list[ContentBlock])`, con
  `TextBlock` / `ToolUseBlock` / `ToolResultBlock`.
- Nuevos: `AgentSpec`, `RunLimits`, `ToolSpec` (+ `Effect`), `PendingAction`,
  `ModelRequest`/`ModelResponse`/`Usage`, `Clock`, y los Protocols `ModelProvider`,
  `AgentRunner`, `ContextBuilder`, `Tool`, `ToolRegistry`, `ToolExecutor`, `Policy`,
  `ApprovalGate`, `ChannelAdapter`.
- `AGENT_MAX_TURNS` → `AGENT_MAX_STEPS` (son pasos del loop, no turnos de chat).

## Doc de tb-agent

Ninguno en particular: es la traducción de los nombres de tb-agent al estándar.
channel → `Session`, sender → `Principal`, executeTool → `ToolExecutor` + `Policy`,
confirm-flow → `ApprovalGate`, Agent + Skill → `AgentSpec`.

## En qué difiere de la versión TS

la referencia usa tokens de DI de Nest (`MODEL_PROVIDER`, `TOOL_EXECUTOR`,
`AGENT_RUNNER`) como puertos. Acá los puertos son `Protocol`s de Python (tipado
estructural): una clase cumple la interfaz si tiene los métodos, sin heredar de
nada. Los fakes de los tests no necesitan importar la interfaz.

## Decisiones y tradeoffs

- **`Message` vive en L0, no en L1.** Es el formato de cable del modelo y
  `ModelRequest` (L0) lo necesita; L0 no puede importar L1. El dominio lo
  re-exporta. Por la misma razón `ToolDefinition` (lo que ve el modelo) está en L0
  y `ToolSpec` (L1) la extiende con `effect`, que el modelo nunca ve.
- **`ToolUseBlock` es el `ToolCall`.** No hay un tipo aparte que traducir.
- **`tenant_id` es obligatorio.** En CV el tenant es el propio sender
  (sender-scoped), pero obligar al edge a decirlo explícitamente evita que un
  producto multi-tenant olvide la frontera.
- **La regla de capas es un test** (`tests/test_architecture.py`, con `ast`) y no
  una convención: falla si un módulo importa una capa exterior, o si `domain/` o
  `core/` importan `anthropic`, `fastapi`, `httpx` o `uvicorn`.
- **El historial persistido "solo texto"** (doc 18) ya no es una propiedad del tipo
  `Message`. Será una política de `Memory` (componente 7).

## Preguntas abiertas

- ¿`RunContext` debería llevar `deps` (stores, clock) o se inyectan en el
  constructor de cada implementación? Por ahora, constructor.
- ¿`Policy.authorize` necesita el historial (p. ej. "ya aprobó esto en este turno")
  o eso es estado del `ApprovalGate`?
