# 07 · Tracing y evals (`core/observability.py`, `src/agent/evaluation/`)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> No puedo mejorar lo que no mido. Si cada run deja una traza (qué vio el modelo,
> qué tools llamó, cuánto tardó y cuánto costó) y tengo casos que corren el loop
> real contra el modelo real con resultados verificables, entonces cada cambio de
> prompt o de código se puede comparar con números, no con impresiones. Y si el
> tracing envuelve los puertos en vez de vivir dentro del loop, agregarlo o
> cambiar de proveedor no toca el loop.

## Qué hace

**Tracing**
- `platform/tracing.py`: el puerto `Tracer` (`span(name, **attrs)` como context
  manager) y `NoopTracer`.
- `core/observability.py`: tres decoradores con la misma interfaz que lo que
  envuelven: `TracedModelProvider` (modelo, stop reason, tokens, costo),
  `TracedToolExecutor` (tool, error, estacionada; input/output solo con
  `capture_content`) y `TracedAgentRunner` (agente, sesión, stop, pasos, tokens,
  costo). Una traza por run: `agent.run` → `model.generate` / `tool.execute`.
- `adapters/tracing/memory.py`: `InMemoryTracer`. El anidamiento sigue a la tarea
  con un `ContextVar`, así que runs concurrentes no se mezclan.
- `platform/cost.py`: tabla de precios por modelo y `cost_of(model, usage)`.

**Evals**
- `src/agent/evaluation/` (kernel): `EvalCase`, `ScriptedTool`, graders
  deterministas (`CalledTool`, `NotCalledTool`, `Stopped`, `OutputContains`,
  `OutputExcludes`, `MaxWords`, `MaxSteps`) y `Harness`, que corre casos × reps en
  paralelo con el loop, la frontera y el prompt reales, y arma un `Report`.
- `evals/agent_loop/` (producto): 5 casos de un asistente de calendario de CV y el
  CLI `run.py` (markdown, JSON, gate con exit code).
- `core/context.py`: `InstructionsContext`, el `ContextBuilder` mínimo
  (instrucciones estables + fecha y hora volátiles). Los evals necesitaban un
  prompt real.

## Doc de tb-agent

No hay doc de evals en tb-agent. El modelo es `evals/agent-loop` de la referencia:
tools guionadas que se estacionan igual que producción, reps, gate.

## En qué difiere de la versión TS

- la referencia reporta los evals a Langfuse (entorno `sdk-experiment`). Acá el
  reporte es local (markdown + JSON) por ahora; el adapter de Langfuse va sobre el
  mismo puerto `Tracer`.
- Igual que la referencia, los decoradores envuelven los tokens de DI
  (`MODEL_PROVIDER`, `TOOL_EXECUTOR`, `AGENT_RUNNER`). Acá son clases que
  implementan el mismo `Protocol`.
- El harness es kernel y los casos son producto: mañana el restaurante reusa el
  harness con otros casos.

## Decisiones y tradeoffs

- **`evaluation/` es la capa más externa.** Puede importar todo y nadie la
  importa. El test de arquitectura lo hace cumplir (rank 5).
- **Graders deterministas primero.** Son baratos, exactos y explican la falla.
  LLM-as-judge solo para lo que una regla no puede ver (tono, si "respondió la
  pregunta").
- **Reloj fijo en los evals** (martes 6 de octubre de 2026). Así "mañana a la 1pm"
  tiene una respuesta correcta verificable: `2026-10-07T13:00`.
- **`min_pass_rate` por caso, 100 % por defecto.** El modelo no es determinista;
  un caso conocido como inestable puede bajar su umbral explícitamente, a la vista.
- **Contenido solo con `capture_content=True`.** Producción guarda metadata, como
  la referencia (`LANGFUSE_FULL_CAPTURE_USER_IDS` para el contenido completo).
- **Los precios son una tabla escrita a mano.** Los tokens son exactos; los dólares
  dependen de que la tabla esté al día.

## Preguntas abiertas

- ¿Cuánta variación hay entre reps con el modelo real? Correr con `--reps 5` y
  ver si algún caso necesita `min_pass_rate` menor o un prompt mejor.
- ¿Cómo mando los evals a Langfuse (datasets + experimentos) sin acoplar el
  harness a Langfuse?
- ¿Agregar un grader LLM-as-judge para "la respuesta suena natural en voz"?
- ¿Un check en CI que corra los evals en PRs que tocan `core/` o los prompts,
  como `agent-eval.yml` en la referencia?
