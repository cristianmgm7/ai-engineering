# 05 · AgentRunner (`core/runner.py`)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> El loop es pequeño si hace una sola cosa: llamar al modelo, pasar cada tool call
> por el `ToolExecutor` y repetir hasta que el modelo responda o algo lo detenga.
> Todo lo demás (el texto para el usuario, pedir aprobación, reintentos, guardar
> historial) le pertenece a otra pieza. Si eso es cierto, el loop se testea entero
> con fakes y nunca necesita saber qué canal ni qué proveedor hay detrás.

## Qué hace

`ReasoningLoop.run(ctx, event, history) → RunResult`:

1. Arma el system prompt una vez (`ContextBuilder`), y la lista de tools que ve el
   modelo (`ToolRegistry.for_context`, sin `effect`).
2. Agrega el mensaje del usuario y llama al modelo con `history + nuevos`.
3. Si el modelo pide tools: corre **todas** las llamadas de la ronda por el
   `ToolExecutor` y devuelve todos los resultados en **un solo** mensaje de usuario.
4. Repite hasta `max_steps`.

Para por cinco razones, cada una con su `RunStop`: `completed` (el modelo terminó),
`awaiting_approval` (una llamada quedó en espera), `limit_reached` (`max_steps`, o
`max_tokens` cortó la respuesta), `refused` (clasificadores de seguridad),
`incomplete` (cualquier otro stop).

## Doc de tb-agent

`14-agent-reasoning-loop.md`: el loop de `runAgent`, `MAX_TURNS`, y "cada tool
call pasa por `executeTool`".

## En qué difiere de la versión TS

- la referencia (`agent-runner.service.ts`) también **redacta la respuesta**: un
  fallback cuando llega a `MAX_TURNS`, el texto de aprobación
  (`renderApprovalText`), la pregunta de `ask_user`. Acá el loop solo devuelve el
  texto del modelo y el motivo de la parada. Elegir las palabras para el usuario es
  producto (voz), así que le toca al edge.
- la referencia atrapa errores y responde con un fallback. Acá un `ModelProviderError`
  se propaga: el worker decide si reintentar (sabe si es `retryable`).
- Igual que la referencia: todas las llamadas de una ronda se ejecutan aunque una quede
  en espera, y una llamada en espera termina el turno.

## Decisiones y tradeoffs

- **`RunContext`/`RunResult` se mudaron a `core/run.py`.** `tools.py`, `policy.py`,
  `approval.py` y `context.py` necesitan `RunContext`, y el loop los importa a
  ellos. Con los tipos en `runner.py` había un ciclo de imports.
- **`ToolResult.pending`.** El executor marca una llamada en espera devolviendo un
  resultado con `pending`. Su `content` le dice al modelo que pare, sin redactar la
  pregunta. Ese resultado entra al historial, así que cada `tool_use` tiene su
  `tool_result` y el transcript sigue siendo válido para la siguiente llamada.
- **Todos los resultados en un mensaje.** Separarlos en varios mensajes le enseña
  al modelo a dejar de llamar tools en paralelo.
- **Las llamadas se ejecutan en orden, no en paralelo.** Más simple y
  determinista, y dos escrituras no se pisan. Si la latencia lo pide, se puede
  paralelizar las lecturas más adelante.
- **Una respuesta cortada por `max_tokens` no ejecuta sus tools.** El input de la
  última llamada puede venir incompleto.
- **El system prompt se arma una vez por run**, no por paso: es igual en todos los
  pasos, y así no rompe la caché a mitad del turno.
- **`new_messages` empieza con el mensaje del usuario.** Quien llama persiste
  exactamente eso; el loop no sabe de storage.

## Preguntas abiertas

- ¿Qué dice el edge cuando `stop` es `limit_reached` o `refused` y `output` está
  vacío? Va con el `ChannelAdapter` del canal (componente 8).
- ¿Un tope de costo por run (`max_cost`) además de `max_steps`? Llega con
  `CostMeter` (componente 6).
- ¿Paralelizar las lecturas de una misma ronda (`asyncio.gather`)?
