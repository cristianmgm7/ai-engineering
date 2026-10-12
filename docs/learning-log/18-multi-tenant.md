# 18 · Multi-tenant: varias tiendas (y otros negocios) sobre el mismo kernel

Estado: **diseño, sin código.** La sección "Qué aprendí" se llena al terminar.

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si la **tienda** pasa a ser ciudadana de primera clase (`tenant_id`, separada
> del cliente `principal_id`), si todo estado persistente lleva la tienda en su
> clave, y si lo específico de cada tienda (instrucciones, tools, credenciales,
> límites) se resuelve desde un directorio de configuración, entonces:
>
> 1. agregar una tienda es **insertar una fila**, no escribir código;
> 2. agregar un negocio de otro tipo (clínica, inmobiliaria) es **un subpaquete
>    nuevo** (`domain/<x>/`, `adapters/<x>/`), con cero ediciones en `core/` y
>    `platform/`;
> 3. el mismo cliente (`wa_id`) escribiendo a dos tiendas **no comparte nada**:
>    ni historial, ni aprobaciones pendientes, ni pedidos.
>
> Se falsa si algo de eso exige tocar el kernel más allá de una etiqueta de
> tracing, o si un test negativo logra cruzar datos entre tiendas.

## El problema hoy (leído del código)

| Qué | Dónde | Por qué rompe con 2 tiendas |
|---|---|---|
| `tenant_id = sender` (el `wa_id`) | `edges/whatsapp/adapter.py:95` | "tenant" significa *cliente*; no hay concepto de tienda |
| `session_id = sender` | `adapter.py:97` | Mismo cliente + dos números = **misma sesión** (historial compartido) |
| `to = message.session_id` en `send` | `adapter.py` (outbound) | Al namespacear la sesión, el destinatario deja de ser el `session_id` |
| `INSTRUCTIONS` fijo "Chicken Express" | `edges/whatsapp/app.py:51` | El prompt es de una sola tienda |
| Un solo `whatsapp_access_token`, `pos_*` | `platform/config.py`, `app.py` | Credenciales globales |
| `pedidos` y el `PedidoStore` keyed por `customer_id` | `adapters/restaurante/sqlite.py` | Sin tienda: pedidos de A visibles desde B |
| `agents` con una sola entrada | `app.py:103` | El `StaticAgentDirectory` ya soporta N, solo falta llenarlo |

Lo que **ya sirve**: el kernel no sabe de restaurantes ni de WhatsApp; el
directorio ya se indexa por `phone_number_id`; `AgentSpec.tool_names` ya filtra
tools por agente; el adapter ya exige que el `phone_number_id` del path coincida
con el del payload firmado.

## Diseño

### 1. Identidad: tienda ≠ cliente

```
tenant_id    = phone_number_id        # la tienda (v1: 1 número = 1 tienda)
principal_id = wa_id                  # el cliente
session_id   = f"{tenant_id}:{wa_id}" # opaco para el kernel
```

- `session_id` sigue siendo un string opaco: **`SessionStore` y
  `PendingActionStore` no cambian**; quedan aislados por la clave que reciben.
- **v1: tenant = número.** Una tienda con dos números es YAGNI; si llega, se
  agrega un mapa `número → tenant` en el directorio (ver preguntas abiertas).
- `OutboundMessage` gana `recipient` (el `wa_id`); `send` deja de usar
  `session_id` como destino. Es el único cambio en tipos del borde (`edges/channels.py`).
- La tienda se decide por el `phone_number_id` **del payload firmado**, nunca
  por un argumento del modelo ni por algo que el cliente escriba.

### 2. Configuración por tienda

`domain/tenant.py` (L1, sin vendors):

```python
class TenantConfig(BaseModel):
    tenant_id: str              # = phone_number_id
    nombre: str
    vertical: str               # "restaurante" | "clinica" | ...
    instructions: str           # prompt ya renderizado; plantilla en el vertical
    params: dict[str, str]      # ciudad, horario, zona de entrega...
    model: str | None           # override; None = el global
    limits: RunLimits | None
    tools: list[str]            # qué tools del vertical se habilitan
    secret_refs: dict[str, str] # nombre → clave de entorno/secret, NUNCA el valor
    enabled: bool = True
```

- Puerto `TenantDirectory` (L1) con `get(tenant_id)`; implementación
  `SqliteTenantDirectory` (L3, `adapters/stores/`) que **carga todo en memoria al
  arrancar** (el `AgentDirectory` actual es síncrono). Cambios de config = reinicio en v1.
- Un `AgentDirectory` derivado construye el `AgentSpec` de cada tienda desde su
  config, así el `Worker` no cambia.
- Los prompts son plantillas **por vertical** (`adapters/restaurante/prompts.py`)
  rellenadas con `params`: la tienda cambia datos, no estructura.
- Alta de tiendas: `scripts/tenants.py add|list|disable` (sin UI de admin).

### 3. Tools y conectores por tienda

- Las tools se registran **una vez**, globales, con nombre por vertical
  (`pedidos__crear`, `citas__agendar`). `AgentSpec.tool_names` decide cuáles ve
  cada tienda.
- Cada tool resuelve su backend por `ctx.tenant_id` mediante un
  `ConnectorResolver` (L3): `tenant_id → PedidoStore` (SQLite local, o
  `PosPedidoStore` con el `PosClient` de esa tienda, con su caché de menú
  también por tienda).
- `PedidoStore` pasa a `(tenant_id, customer_id)`; la tabla `pedidos` gana
  `tenant_id` + índice `(tenant_id, customer_id, id)`.
- **Policy**: se agrega `TenantScoped` (hermana de `CustomerScoped`) que niega
  cualquier campo `tenant_id`/`tienda_id` en el input crudo distinto de
  `ctx.tenant_id`. Composición: `AllOf(TenantScoped(), CustomerScoped(), ConfirmWrites())`.

### 4. Credenciales y salida

- `access_token` por tienda: `TokenProvider` (puerto en el borde) que resuelve
  `agent_key → token` vía `secret_refs`. En SQLite solo viven las **referencias**.
- **Un solo `app_secret`** de Meta firma todos los webhooks (modelo BSP / Embedded
  Signup). Si una tienda trae su propia app de Meta, se necesita un secreto por
  hook: queda fuera de esta slice.
- Hook desconocido o tienda `enabled=False` → el evento se descarta con log
  (comportamiento que el `Worker` ya tiene para agentes ausentes).

### 5. Observabilidad y costos

- `core/observability.py:106` ya manda `tenant_id`; pasa a significar la tienda
  sin tocar código. Se agrega `principal_id` como metadata para depurar.
- Reporte de costo por tienda desde `Usage` ya agregado por run: un script
  sobre las trazas. **Presupuesto/cuota por tienda: fuera de alcance** (es la
  siguiente slice, necesita un contador persistente).

### 6. Datos existentes

El deploy sigue *fail-closed* hasta tener credenciales de Meta, así que
**probablemente no hay datos reales**: la migración sería recrear el esquema.
Confirmar antes de borrar nada. Si hubiera datos: `UPDATE messages/pending_actions`
reescribiendo `session_id` a `f"{phone}:{wa_id}"` y backfill de `tenant_id` en `pedidos`.

## Plan de construcción (cada paso con su test primero)

0. **Test rojo que prueba el problema**: mismo `wa_id` a dos `phone_number_id`
   → hoy comparten sesión. Es el test negativo ancla de toda la slice.
1. Adapter: identidad nueva (`tenant_id`, `session_id` namespaced) +
   `OutboundMessage.recipient`. El test 0 pasa a verde.
2. `TenantConfig`, `TenantDirectory`, `SqliteTenantDirectory` + `scripts/tenants.py`;
   `app.build` arma un `AgentSpec` por tienda (adiós `INSTRUCTIONS` global).
3. `PedidoStore` con `tenant_id` + `ConnectorResolver`; POS por tienda.
4. `TenantScoped` + tests negativos (input crudo con otra tienda → `Deny`).
5. `TokenProvider` por tienda para el envío.
6. Etiquetas de tracing y script de costo por tienda.
7. **Prueba de la hipótesis 2**: un vertical falso `clinica` (una tool) en
   tests, corriendo en la misma app junto a `restaurante`.

## Criterios de aceptación

- Tests negativos entre tiendas: historial, aprobaciones y pedidos disjuntos;
  un "sí" en la tienda B no ejecuta una acción parqueada en la A.
- Alta de la tienda C = una fila vía script, sin editar código.
- El vertical `clinica` de prueba convive sin cambios en `core/` ni `platform/`.
- `tests/test_architecture.py` verde; `git diff --stat src/agent/core src/agent/platform`
  sin cambios funcionales (a lo sumo la etiqueta de tracing).
- `uv run pytest` y `ruff` limpios.

## Fuera de alcance

Embedded Signup / onboarding automático, UI de admin, facturación y cuotas,
secretos cifrados (KMS/Vault), Postgres, hot-reload de config, tienda con
varios números, y un segundo vertical real.

## Riesgos y preguntas abiertas

- **¿Directorio en SQLite o en un archivo versionado (YAML)?** SQLite permite
  `scripts/tenants.py` y altas sin deploy; YAML es más auditable. Recomiendo
  SQLite; la decisión es tuya.
- **¿Una tienda con dos números?** Obligaría a separar `tenant_id` de
  `phone_number_id` y mapear. Se pospone a propósito.
- **Aislamiento por disciplina del borde**: `SessionStore` es ciego a la tienda;
  si un adapter futuro olvida namespacear `session_id`, se mezclan datos.
  Mitigación: un test de contrato que todo `ChannelAdapter` debe pasar.
- **Aislamiento de costo/ruido**: una tienda con mucho tráfico comparte la
  cola en proceso (`InProcessQueue`) con las demás. Aceptable en v1.
- **Prompt injection cross-tenant**: el modelo nunca ve datos de otra tienda
  porque los stores no se lo permiten, pero conviene un eval que lo intente.

## Doc de tb-agent

27 (tenant isolation) para el modelo de aislamiento; 23 (config) para el
directorio. Releer 27 antes del paso 2 y anotar aquí en qué difiere.

## Qué aprendí

_(pendiente: se escribe al cerrar la slice.)_
