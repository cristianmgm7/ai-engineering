# 06 · La frontera de permisos (`core/tools.py`, `core/policy.py`)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si toda llamada a una tool pasa por un único punto que no confía en nada de lo
> que dice el modelo (ni el nombre de la tool, ni los argumentos), y ese punto
> decide con reglas en código y no con el prompt, entonces la seguridad del agente
> se puede probar con tests negativos. Las reglas de cada producto se enchufan
> como `Policy` sin tocar ese punto.

## Qué hace

- **`StaticToolRegistry`**: un conjunto fijo de tools; cada agente ve solo las que
  su `AgentSpec.tool_names` nombra, en ese orden. Sin `tool_names`, cero tools.
- **`@tool` / `FunctionTool`**: convierte una función async tipada en un `Tool`.
  El modelo de argumentos se lee de la anotación del primer parámetro y de ahí sale
  el JSON Schema. Si la función devuelve un `str`, se envuelve en `ToolResult`.
- **`PolicyExecutor`**, la frontera, en este orden:
  1. ¿Existe la tool **y este agente puede verla**? Se vuelve a chequear: el modelo
     podría nombrar una tool que nunca se le ofreció.
  2. ¿Los argumentos validan contra el modelo Pydantic?
  3. `Policy`: `Allow`, `Deny(reason)` o `RequireApproval`.
  4. Si hay que aprobar: `park` y se devuelve `parked_result` (no corre).
  5. Si se permite: corre el handler; una excepción se vuelve un error legible.
- **Policies del kernel**: `AllowAll`, `ConfirmWrites(auto_approved)` (lecturas
  pasan, escrituras piden aprobación salvo las "siempre"), y `AllOf(...)` para
  componer (cualquier `Deny` gana, luego `RequireApproval`, si no `Allow`).

## Doc de tb-agent

`15-tooling-and-executetool.md` (assembleTools + executeTool) y
`27-tenant-isolation.md` (sender-scoping).

## En qué difiere de la versión TS

- En la referencia, `executeTool` hace todo en un solo método: resolver el mapping
  canal→conector, chequear sender-scoping, descifrar el token, aplicar
  `writePolicy`, estacionar y llamar al adapter. Acá eso se separa en tres
  piezas: el registro decide qué se ve, `Policy` decide si se permite, y el
  executor solo orquesta. Las reglas del producto serán una `Policy` más.
- `parked_result` cumple el mismo papel que `confirmationParkResult`: el texto que
  recibe el modelo le dice que pare, y los evals lo reutilizan para estacionar
  igual que producción.
- El sender-scoping real necesita cuentas de conector, que son producto. Llega con
  el primer conector real, en su subpaquete de `adapters/`. Acá está probado con una
  regla de ejemplo (`OwnerOnly`) en los tests.

## Decisiones y tradeoffs

- **Validar antes de autorizar.** Una llamada con argumentos inválidos nunca se
  estaciona: no tiene sentido pedirle a un humano que apruebe algo que no puede
  correr. Y una `Policy` puede leer argumentos ya válidos.
- **Errores legibles, sin detalles internos.** Una excepción del handler se vuelve
  `"<tool> failed (RuntimeError). Tell the user it didn't work."` y el detalle va
  al log. El mensaje de la excepción puede traer datos internos y el modelo podría
  repetirlos en voz alta.
- **"Unknown tool" también para tools que existen pero este agente no tiene.** No
  se le confirma al modelo que existen.
- **`ApprovalParker` en vez de importar `ApprovalGate`.** `approval.py` importa
  `ToolResult` de `tools.py`, así que el executor declara solo la parte que
  necesita (`park`). Cualquier `ApprovalGate` la cumple.
- **Tests de mutación a mano.** Saqué la llamada a `Policy` a propósito: fallaron
  4 tests. Si un cambio así pasara en verde, los tests no estarían protegiendo la
  frontera.

## Preguntas abiertas

- ¿El registro debería fallar al guardar un `AgentSpec` que nombra tools que no
  existen? Hoy se ignoran en silencio en `for_context`.
- ¿Namespacing (`calendar__list`) lo pone el registro o el adapter del conector?
  En tb-agent lo hace `assembleTools`.
- ¿`Policy` necesita saber qué se aprobó antes en la misma sesión? Hoy
  `auto_approved` es fijo por construcción; en la referencia viene de la DB.
