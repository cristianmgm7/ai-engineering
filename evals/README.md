# Evals

Los tests unitarios cubren lógica determinista; los evals cubren el
**comportamiento del modelo**. El principio: cuando el bug es de
comportamiento, primero un caso que falle, luego el arreglo, y se mide antes y
después.

## Estructura

- **Harness (kernel)** en `src/agent/evaluation/`: `EvalCase`, `ScriptedTool`,
  graders deterministas, `Harness` (casos × reps, concurrencia, reporte, gate).
  Corre el loop, la frontera (`PolicyExecutor` + `ConfirmWrites`) y el prompt
  reales; solo el modelo es real **y** las salidas de las tools son fijas.
- **Casos (producto)** aquí, por suite. Hoy: `agent_loop/` (asistente de
  calendario de una app de mensajes de voz, 5 casos).

## Correr

Necesita `ANTHROPIC_API_KEY` en `.env` y **cuesta dinero** (≈ casos × reps × 2
llamadas al modelo).

```bash
uv run python evals/agent_loop/run.py --reps 3
uv run python evals/agent_loop/run.py --reps 1 --case create_is_gated
```

Imprime un reporte en markdown (pass rate por caso, costo, latencia p50/p95 y el
detalle de cada intento fallido), escribe el JSON en `evals/results/` (ignorado
por git) y sale con código ≠ 0 si algún caso queda por debajo de su
`min_pass_rate`.

## Agregar un caso

En `agent_loop/cases.py`: un `EvalCase` con el texto del usuario, las tools
guionadas que ve y los graders. Una tool `Effect.WRITE` se estaciona igual que en
producción. El reloj está fijo (martes 6 de octubre de 2026, 14:05 UTC) para que
las fechas se puedan verificar.
