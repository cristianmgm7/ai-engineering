# 15 · El adapter de Langfuse (estación 7)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el puerto `Tracer` es tan chico (un context manager que da spans con
> `set()`), entonces el adapter de Langfuse es pura traducción: nombres del
> kernel → tipos de observación, atributos → campos del SDK — y los
> decoradores de `core/observability.py` no cambian ni una línea.

## Qué hace

- **`adapters/tracing/langfuse.py`**: `LangfuseTracer` sobre el SDK **v4**
  (ojo: yo recordaba v3; la doc viva mandó — regla de la skill de Langfuse:
  nunca implementar de memoria). Mapea nombres a tipos de observación:
  `agent.run` → `agent`, `model.generate` → `generation` (enciende analítica
  de tokens/costo), `tool.execute` → `tool`, resto → `span`.
- **Ruteo de atributos**: `input`/`output` pasan directo; `model` solo en
  generations; todo lo demás va a `metadata` — así el adapter no depende de
  kwargs del SDK que puedan cambiar. La metadata se **acumula en el span** y
  viaja completa en cada update, para no depender de si el SDK fusiona o
  reemplaza.
- **Anidación por `ContextVar`** (igual que `InMemoryTracer`): cada task
  concurrente lleva su propia cadena de padres.
- **`app.py`**: siempre cablea los decoradores `Traced*`; el tracer es
  `LangfuseTracer` con keys configuradas, `NoopTracer` sin ellas. Detalle de
  cableado que importa: el `gate` queda bindeado al `PolicyExecutor` crudo
  (necesita `execute_approved`, que el decorador no expone); el loop usa el
  executor decorado.

## Doc de tb-agent

— (el puerto venía de la estación 6 del primer slice). Referencia real: la
doc de Langfuse v4 (instrumentación manual: `start_observation`, `update`,
`end`, `flush`).

## En qué difiere de la versión TS

La referencia usa los decoradores de DI de Nest sobre tokens y reporta evals a
Langfuse con entorno propio; acá el mismo efecto con wrappers explícitos de una
línea en el composition root.

## Decisiones y tradeoffs

- **Tests contra un cliente fake** que imita la rebanada exacta del SDK que
  tocamos (`start_observation`/`update`/`end`). Fijan el mapeo, no el SDK: el
  primer run real contra Langfuse es la verificación que falta (estación 8) —
  auditar la traza en la UI como pide la skill.
- **Sin `propagate_attributes` todavía**: en v4, session/user a nivel traza
  van por ese context manager; por ahora `session_id`/`principal_id` viajan en
  metadata. Mapearlos bien (vista Sessions/Users de Langfuse) queda para
  después del primer run real.
- **Usage a metadata, no a `usage_details`**: sin confirmar los nombres
  exactos del kwarg en v4, metadata es visible y seguro; el costo ya viaja
  calculado por nosotros (`cost_usd`, de `platform/cost.py`).

## Preguntas abiertas

- `flush()`/`shutdown()` al apagar uvicorn (¿lifespan de FastAPI?). El SDK
  tiene su propio batching; verificar que nada se pierda en un deploy.
- `usage_details`/`cost_details` nativos para que Langfuse calcule costo solo.
- `propagate_attributes` para Sessions/Users de verdad.
