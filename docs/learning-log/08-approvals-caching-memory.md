# 08 · Aprobaciones, caché y memoria (`core/approval.py`, `core/memory.py`, `core/turns.py`)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Una conversación con un agente es una serie de turnos independientes que solo
> comparten lo que se guardó. Si el harness decide qué se guarda, qué ve el modelo
> en el siguiente turno y quién puede aprobar qué, entonces el agente es seguro y
> barato sin que el modelo tenga que "acordarse" de nada ni pedir permiso por sí
> mismo.

## Qué hace

**ApprovalGate** (`StoreApprovalGate`)
- `park`: guarda la llamada exacta como `PendingAction`, con vencimiento (24 h por
  defecto).
- `decide(action_id, approved, ctx)`: primero verifica que quien decide es quien
  pidió, en la misma sesión (si no, `NOT_ALLOWED`, sin consumir la acción). Luego
  la reclama una sola vez, rechaza las vencidas, y si se aprobó la corre **sin
  cambios** por `PolicyExecutor.execute_approved`.
- `execute_approved` es la misma frontera: vuelve a chequear visibilidad,
  argumentos y `Deny`. Solo la aprobación se da por cumplida.
- `decision_turn_text`: el mensaje del harness para el turno siguiente, para que
  el modelo cuente el resultado real (port de `buildApprovedTurn`).

**Caché del prompt**
- `SystemBlock(text, cache)` y `ModelRequest.cache`. `Prompt.blocks()` pone el
  breakpoint al final de la parte estable; la fecha va después.
- El adapter de Anthropic pone `cache_control` en el bloque estable, en la última
  tool y en la cola de la conversación. Así el segundo paso de una ronda de tools
  reusa todo lo anterior.

**Memoria y turnos**
- `SessionStore` guarda todo, append-only y textual. `InMemorySessionStore` por
  ahora.
- `WindowMemory`: el siguiente run ve los últimos N turnos **como texto plano**
  (lo que dijo el usuario + la respuesta final del agente).
- `TurnService`: `handle` (evento duplicado → nada; si no: historia → run →
  guardar) y `decide` (decidir → un turno más donde el modelo narra). Serializa los
  runs de una misma sesión.

## Doc de tb-agent

`22-confirm-flow-state-machine.md` y `18-memory-distillation.md`.

## En qué difiere de la versión TS

- En la referencia `ConfirmFlow` vive en el worker (edge) y hace todo: tarjeta, texto
  hablado, decisión, narración. Acá se separa en tres: el gate (decidir y correr,
  kernel), `TurnService` (orquestar, kernel) y la tarjeta o el texto hablado
  (producto, componente 8).
- la referencia re-chequea al aprobar porque `decide()` vuelve a pasar por el
  executor. Acá es explícito: `execute_approved` es la misma frontera, y hay un
  test donde se revoca el permiso mientras la llamada espera.
- El chequeo de "quién decide" cubre una brecha señalada en un audit de la
  referencia (respuestas de tarjetas HITL de otros miembros del canal).

## Decisiones y tradeoffs

- **Historia como texto plano entre runs.** Un bloque de thinking queda atado al
  prompt exacto que lo produjo (system, tools y mensajes anteriores). Nuestro
  system cambia en cada run porque lleva la fecha, así que reenviar thinking de un
  run anterior daría 400 en cuentas con el chequeo activo. Además los payloads
  viejos de tools gastan tokens y quedan desactualizados. Dentro de un run el
  transcript sí va completo y append-only.
- **Verificar al que decide antes de reclamar.** Si se reclamara primero, un
  extraño podría "gastar" la aprobación aunque luego se le negara.
- **`bind()` en el gate.** El executor necesita al gate para estacionar, y el gate
  necesita al executor para correr lo aprobado: se apuntan mutuamente. Se rompe el
  ciclo construyendo uno y enlazando el otro después, y `decide` falla fuerte si
  falta el enlace.
- **Si el run falla, no se guarda nada.** El evento no queda como "visto" y un
  reintento empieza limpio.
- **El lock por sesión es de un solo proceso.** Con varios procesos, la cola del
  worker tiene que serializar por sesión.
- **Mutación a mano otra vez:** sin el lock, el test de serialización falla.

## Preguntas abiertas

- ¿La respuesta "siempre" (auto-aprobar esta tool de ahora en adelante)? En
  la referencia es `autoApprovedTools` en la DB; acá `ConfirmWrites` lo recibe fijo.
- ¿Cuándo un `SessionStore` en SQLite, y cómo hacer `claim` atómico ahí?
- ¿Memoria de largo plazo: destilar hechos de la conversación (doc 18) y ponerlos
  en la parte estable o en la volátil del prompt?
- ¿Un eval que cubra el turno de narración después de aprobar?
