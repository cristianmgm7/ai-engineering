# 02 · Domain models (`domain/models.py`)

## Qué hace

Los modelos Pydantic mínimos que el loop de slice 1 mueve: `InboundMessage` (lo que
el webhook entrega al core) y `Turn` (una entrada del historial, con `Role`). Todos
`frozen` (inmutables) y con `created_at` timezone-aware en UTC.

## Doc de tb-agent

`01-data-model.md`. La idea central del doc — que `ChannelConnector` es la frontera
de permisos — no se implementa aquí; llega con `executeTool` (comp. 5). De doc 01
solo tomé la forma de `turns` (role + texto audible, sin bloques de tool, según
doc 18).

## En qué difiere de la versión TS

- la referencia modela ~5 colecciones Mongo (Agent, Channel, ConnectorAccount,
  ChannelConnector, ChannelMemory) con Mongoose. Acá **solo 2 modelos en memoria**;
  el resto se difiere a su componente.
- `Turn` aquí no persiste; es una estructura en memoria. La persistencia (y el
  equivalente de `ChannelMemory`) llega cuando metamos storage.

## Decisiones y tradeoffs

- **`frozen=True`.** El historial es append-only; congelar los modelos convierte esa
  invariante en algo que el tipo garantiza (mutar lanza `ValidationError`).
- **`StrEnum` para `Role`.** Los miembros son strings → serializan a `"user"` /
  `"assistant"` sin conversión, y `Role.USER == "user"` es verdadero.
- **`datetime.now(UTC)`** (aware, no naive) vía `default_factory`. Evita el bug de
  comparar naive vs aware más adelante.
- **`message_id` como clave de idempotencia** — mismo rol que en la referencia
  (`lastMessageId` guarda el doble-append en reintentos del worker).

## Preguntas abiertas

- ¿`InboundMessage` debería `forbid` campos extra para cazar typos del payload, o
  `ignore` porque el webhook trae campos que no usamos? (por ahora: default = ignore).
- ¿Cuándo introducir `Channel`/membresía? Lo necesita `executeTool` (comp. 5) para
  el sender-scoping; lo modelo ahí.
