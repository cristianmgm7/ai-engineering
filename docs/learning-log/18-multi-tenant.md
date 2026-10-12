# 18 · Multi-tenant: varias tiendas (y otros negocios) sobre el mismo kernel

Estado: **diseño, sin código.** La sección "Qué aprendí" se llena al terminar.
Revisión 2: hilo vs. sesión, atribución de anuncios y el vertical
`productos_digitales` como segundo caso real.

## Vocabulario (tres niveles que el código hoy confunde)

| Nivel | Qué es | Vive | Ejemplo |
|---|---|---|---|
| **Tenant** | el negocio al que le vendemos el agente: su número, su config, sus credenciales, sus datos | permanente | Chicken Express; el negocio de productos digitales |
| **Principal** | la persona que escribe | permanente | un `wa_id` |
| **Hilo** (`thread`) | la relación `tenant + principal`: el log durable de mensajes | permanente | `"<phone_number_id>:<wa_id>"` |
| **Sesión** | un episodio temporal de conversación dentro del hilo | nace con un mensaje, muere por inactividad | `"<hilo>:<n>"` |

Hoy `InboundEvent.session_id` es en realidad un **hilo** (nunca expira) y
`tenant_id` es el **principal**. Esta slice los separa.

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si la **tienda** es de primera clase (`tenant_id`, separada del cliente), si el
> **hilo** (durable) se separa de la **sesión** (temporal, expira por
> inactividad), si todo estado lleva la tienda en su clave y lo específico de
> cada negocio sale de un directorio de configuración, entonces:
>
> 1. agregar una tienda es **insertar una fila**, no escribir código;
> 2. agregar un negocio de otro tipo (productos digitales, clínica) es **un
>    subpaquete nuevo** (`domain/<x>/`, `adapters/<x>/`), con cero ediciones en
>    `core/` y `platform/` *después* de esta slice;
> 3. el mismo cliente escribiendo a dos tiendas **no comparte nada**: ni
>    historial, ni aprobaciones pendientes, ni pedidos;
> 4. una conversación inactiva **no arrastra** intención ni aprobaciones viejas
>    a la siguiente, y cada sesión sabe de qué anuncio vino.
>
> Se falsa si algo de eso exige tocar el kernel más allá de lo listado en
> "Cambios al kernel", o si un test negativo logra cruzar datos entre tiendas.

## El problema hoy (leído del código)

| Qué | Dónde | Por qué rompe |
|---|---|---|
| `tenant_id = sender` (el `wa_id`) | `edges/whatsapp/adapter.py:95` | "tenant" significa *cliente*; no hay concepto de tienda |
| `session_id = sender`, para siempre | `adapter.py:97` | Mismo cliente + dos números = **mismo hilo**; y nada lo expira |
| `to = message.session_id` en `send` | `adapter.py` (outbound) | Al cambiar la clave de sesión, el destinatario deja de ser el `session_id` |
| `INSTRUCTIONS` fijo "Chicken Express" | `edges/whatsapp/app.py:51` | El prompt es de una sola tienda |
| Un solo `whatsapp_access_token`, `pos_*` | `platform/config.py`, `app.py` | Credenciales globales |
| `pedidos` y `PedidoStore` por `customer_id` | `adapters/restaurante/sqlite.py` | Sin tienda: pedidos de A visibles desde B |
| `agents` con una sola entrada | `app.py:103` | El directorio ya soporta N, solo falta llenarlo |

Lo que **ya sirve**: el kernel no sabe de restaurantes ni de WhatsApp; el
directorio ya se indexa por `phone_number_id`; `AgentSpec.tool_names` filtra
tools por agente; el adapter exige que el `phone_number_id` del path coincida
con el del payload firmado; las aprobaciones ya expiran (`StoreApprovalGate`,
`ttl=24h`); `WindowMemory` ya limita el contexto a las últimas 10 vueltas.

## Diseño

### 1. Identidad

```
tenant_id    = phone_number_id               # la tienda (v1: 1 número = 1 tienda)
principal_id = wa_id                         # el cliente
thread_id    = f"{tenant_id}:{wa_id}"        # durable; lo emite el adapter
session_id   = f"{thread_id}:{n}"            # temporal; lo resuelve el Worker
```

- La tienda se decide por el `phone_number_id` **del payload firmado**, nunca por
  un argumento del modelo ni por lo que el cliente escriba.
- **v1: tenant = número.** Una tienda con dos números es YAGNI; si llega, se
  agrega un mapa `número → tenant`.
- `OutboundMessage` gana `recipient` (el `wa_id`); `send` deja de usar
  `session_id` como destino.

### 2. Sesión: temporal, expira por inactividad

La sesión no tiene timer ni proceso de limpieza: expira **de forma perezosa**.
Al llegar un mensaje, el `Worker` pregunta a un `SessionResolver`:

```python
class SessionResolver(Protocol):                       # core/sessions.py (L2)
    async def resolve(self, event: InboundEvent, ttl: timedelta) -> Session: ...
```

Reglas (todas con `FakeClock` en tests):

1. No hay sesión previa para el hilo → se abre la `n=1`.
2. `now - last_seen < ttl` → misma sesión; se actualiza `last_seen`.
3. `now - last_seen ≥ ttl` → se abre `n+1`. La anterior **no se cierra**: simplemente
   nadie la retoma (queda para auditoría y trazas).
4. Un mensaje con `attribution` de **otro anuncio** distinto al de la sesión viva
   fuerza una sesión nueva (otro clic = otro embudo).

Tabla `sessions` (`adapters/stores/`, memoria + SQLite): `session_id`, `thread_id`,
`tenant_id`, `principal_id`, `started_at`, `last_seen_at`, `attribution` (JSON).

**Qué cambia y qué no:**

- `SessionStore` y `PendingActionStore` **no cambian**: `session_id` sigue siendo
  un string opaco; una sesión nueva empieza con historial vacío por construcción.
- **Aprobaciones**: una acción parqueada en la sesión 3 es invisible desde la 4
  (`waiting()` filtra por sesión). Un "sí" tardío ya no ejecuta nada viejo: se
  trata como mensaje normal. Esto es deseado, y el TTL de 24 h de `PendingAction`
  queda como tope.
- **Qué NO es el beneficio**: no es principalmente costo, porque `WindowMemory` ya
  topa el contexto. El valor es **semántico** (no arrastrar intención vieja),
  de **seguridad** (aprobaciones atadas a un episodio) y de **medición**
  (sesión = unidad del embudo y de las trazas).
- **TTL por tenant** (`session_ttl_minutes`). Los valores por defecto son
  suposiciones a ajustar con datos: ~2 h para el restaurante; hasta 24 h para
  productos digitales, donde una donación puede tardar horas. Referencia: la
  **ventana de servicio de 24 h** de WhatsApp (pasada esa ventana solo se pueden
  enviar plantillas aprobadas) es el techo natural.
- **Costo de la decisión**: una sesión nueva no recuerda nombre ni dirección. El
  restaurante lo recupera con `pedidos__listar`. La memoria de largo plazo
  (hechos destilados que sobreviven a la sesión, ya anotada como "componente
  posterior" en `core/memory.py`) queda **fuera de alcance**.

### 3. Atribución de anuncios (Click-to-WhatsApp)

Los mensajes que llegan por un anuncio Click-to-WhatsApp traen en el webhook un
objeto `referral` (origen, id del anuncio, titular, `ctwa_clid`…). **Verificar los
campos exactos contra la documentación vigente de Meta antes de implementar.**

- El kernel gana un tipo genérico y sin vendor, `Attribution(source, source_id,
  click_id, extra)`, y `InboundEvent.attribution: Attribution | None`. Nada de
  "Meta" en `domain/`.
- El adapter mapea `referral → Attribution`. El `SessionResolver` lo guarda en la
  sesión (solo llega en el primer mensaje del clic).
- Las trazas llevan `tenant_id`, `session_id` y `attribution.source_id`: de ahí
  sale el embudo **anuncio → chat → donación → entrega** sin infraestructura nueva.

### 4. Configuración por tienda

`domain/tenant.py` (L1, sin vendors):

```python
class TenantConfig(BaseModel):
    tenant_id: str              # = phone_number_id
    nombre: str
    vertical: str               # "restaurante" | "productos_digitales" | ...
    params: dict[str, str]      # ciudad, horario, nombre del creador...
    instructions: str | None    # override; None = plantilla del vertical + params
    model: str | None
    limits: RunLimits | None
    session_ttl_minutes: int
    tools: list[str]            # qué tools del vertical se habilitan
    secret_refs: dict[str, str] # nombre → clave de entorno/secret, NUNCA el valor
    enabled: bool = True
```

- Puerto `TenantDirectory` (L1) + `SqliteTenantDirectory` (L3) que **carga todo en
  memoria al arrancar** (el `AgentDirectory` actual es síncrono). Cambios de
  config = reinicio en v1.
- Un `AgentDirectory` derivado arma el `AgentSpec` de cada tienda desde su config:
  el `Worker` sigue resolviendo por `agent_key`.
- Los prompts son **plantillas por vertical** (`adapters/<vertical>/prompts.py`)
  rellenadas con `params`: la tienda cambia datos, no estructura.
- Alta: `scripts/tenants.py add|list|disable` (sin UI en esta slice).

### 5. Tools y conectores por tienda

- Las tools se registran **una vez**, globales, con nombre por vertical
  (`pedidos__crear`, `catalogo__listar`). `AgentSpec.tool_names` decide cuáles ve
  cada tienda.
- Cada tool resuelve su backend por `ctx.tenant_id` con un `ConnectorResolver`
  (L3): `tenant_id → PedidoStore` (SQLite local o `PosPedidoStore` con el
  `PosClient` de esa tienda; caché de menú también por tienda).
- `PedidoStore` pasa a `(tenant_id, customer_id)`; `pedidos` gana `tenant_id` e
  índice `(tenant_id, customer_id, id)`.
- **Policy**: `TenantScoped` (hermana de `CustomerScoped`) niega cualquier
  `tenant_id`/`tienda_id` en el input crudo distinto de `ctx.tenant_id`.
  Composición: `AllOf(TenantScoped(), CustomerScoped(), <reglas del vertical>, ConfirmWrites())`.

### 6. Credenciales y salida

- `access_token` por tienda: un `TokenProvider` en el borde (`agent_key → token`)
  vía `secret_refs`. En SQLite solo viven las **referencias**.
- **Un solo `app_secret`** de Meta firma todos los webhooks (modelo BSP / Embedded
  Signup). Si una tienda trae su propia app de Meta hace falta un secreto por
  hook: fuera de alcance.
- Hook desconocido o tienda `enabled=False` → el evento se descarta con log
  (ya es el comportamiento del `Worker`).

### 7. Segundo vertical real: `productos_digitales`

Reemplaza al `clinica` falso como prueba de la hipótesis 2. Negocio: anuncios en
Meta → chat de WhatsApp → productos digitales a cambio de una **donación**.

| Tool | Efecto | Qué hace |
|---|---|---|
| `catalogo__listar` | READ | productos y donación mínima sugerida |
| `donacion__crear_link` | ver preguntas abiertas | genera un enlace de pago con referencia `(tenant, principal, producto, nonce)` |
| `entrega__enviar` | WRITE | entrega el enlace de descarga |

La regla que importa es **determinista y vive en código, no en el prompt**:
`PagoVerificado` (una `Policy` del vertical) niega `entrega__enviar` salvo que el
**store de pagos** tenga una donación verificada para ese `(tenant, principal,
producto)`. Ese store lo escribe **el webhook de la pasarela**, nunca el modelo: que
el cliente diga "ya doné" no cuenta.

En esta slice el vertical se prueba con una `FakePaymentProvider` en tests. La
pasarela real, su webhook firmado y el mensaje proactivo posterior (plantilla
aprobada si pasaron más de 24 h) son la **slice 19**.

### 8. Cambios al kernel (la lista completa)

1. `InboundEvent.session_id` → `thread_id`; nuevo `InboundEvent.attribution`.
2. `core/sessions.py`: puerto `SessionResolver` (+ implementaciones en `adapters/`).
3. `RunContext.session_id` ahora lo deriva el `Worker`, no el adapter.
4. `OutboundMessage.recipient` (`edges/channels.py`).
5. Etiquetas de tracing (`tenant_id`, `session_id`, `attribution`).

Todo lo demás es adapters, productos y configuración. Si el diff toca más del
kernel, la hipótesis 2 está en duda.

### 9. Plano de control (después de esta slice)

El front futuro es un CRUD sobre `TenantConfig` más la gestión de secretos: qué
tools tiene cada número y con qué conector y credencial. Para que un cliente pueda
editarlo sin riesgo:

- solo elige tools de un **catálogo** que definimos nosotros;
- el **piso de seguridad es fijo**: las escrituras siempre necesitan aprobación y
  `TenantScoped`/`CustomerScoped` no se desactivan desde el front;
- los secretos son de solo escritura.

Por eso `TenantConfig` es config como datos: el front se agrega sin tocar el kernel.

## Plan de construcción (cada paso con su test primero)

0. **Test rojo ancla**: el mismo `wa_id` escribiendo a dos `phone_number_id` hoy
   comparte historial.
1. Adapter: `thread_id`, `tenant_id`, `OutboundMessage.recipient`. El test 0 pasa a verde.
2. Sesiones: `SessionResolver` + `sessions` (memoria y SQLite). Tests con `FakeClock`:
   dentro del TTL misma sesión, fuera del TTL sesión nueva, aprobación vieja no
   decidible, otro anuncio fuerza sesión nueva.
3. `TenantConfig`, `TenantDirectory`, `SqliteTenantDirectory`, `scripts/tenants.py`;
   `app.build` arma un `AgentSpec` por tienda (adiós `INSTRUCTIONS` global).
4. `PedidoStore` con `tenant_id` + `ConnectorResolver` + `TenantScoped` con tests
   negativos (input crudo con otra tienda → `Deny`).
5. `TokenProvider` por tienda para el envío.
6. Atribución: el adapter parsea `referral`; trazas con `tenant_id`, `session_id`,
   `source_id`; script de costo y conteo por tienda y por anuncio.
7. Vertical `productos_digitales` con fakes: `PagoVerificado` y sus tests negativos.

## Criterios de aceptación

- Tests negativos entre tiendas: historial, aprobaciones y pedidos disjuntos; un
  "sí" en la tienda B no ejecuta una acción parqueada en la A.
- Sesiones: expiran por inactividad, una aprobación de una sesión expirada no se
  ejecuta, y la atribución queda en la sesión.
- `PagoVerificado`: "ya doné" sin pago registrado → `Deny`; un pago de la tienda A no
  desbloquea una entrega en la B; una entrega no se repite sin un pago nuevo.
- Alta de la tienda C = una fila vía script, sin editar código.
- `productos_digitales` convive con `restaurante` en la misma app; el diff fuera de
  la lista de "Cambios al kernel" no toca `core/` ni `platform/`.
- `tests/test_architecture.py`, `uv run pytest` y `ruff` limpios.

## Fuera de alcance

Pasarela de pago real y su webhook, mensajes proactivos y plantillas, **agente de
campañas** (generar anuncios con la Marketing API: otro agente, otro canal y otro
aprobador, porque hoy `ApprovalGate` solo deja decidir al solicitante), Embedded
Signup, UI de admin, cuotas y facturación, secretos cifrados, Postgres, hot-reload,
tienda con varios números, memoria de largo plazo.

## Riesgos y preguntas abiertas

- **¿Directorio en SQLite o en un YAML versionado?** SQLite permite
  `scripts/tenants.py` y altas sin deploy; YAML es más auditable. Recomiendo SQLite.
- **¿`donacion__crear_link` es READ o WRITE?** Con WRITE, `ConfirmWrites` le pediría
  al cliente aprobar un simple enlace de pago, lo que es mala UX. Probablemente
  necesita su propio efecto o una excepción en la policy del vertical.
- **Renombrar `session_id` → `thread_id` en `InboundEvent`** toca tipos del kernel
  y todos sus tests. Alternativa más barata: dejar el campo como está y tratarlo
  como hilo en el borde; sacrifica claridad. Recomiendo el rename.
- **Aislamiento por disciplina del borde**: si un adapter futuro olvida namespacear
  `thread_id`, se mezclan datos. Mitigación: un test de contrato que todo
  `ChannelAdapter` debe pasar.
- **TTL de sesión**: los valores por defecto son conjeturas; conviene medir la
  distribución real de pausas entre mensajes antes de fijarlos.
- **Cuenta publicitaria y políticas de Meta** sobre donaciones y productos digitales:
  un baneo de la cuenta bloquea el embudo entero. Revisar antes de invertir en el
  agente de campañas.
- **Impuestos**: una donación que da acceso a un producto se parece a una venta;
  revisar el tratamiento fiscal. No es asesoría legal.
- Una tienda con mucho tráfico comparte la `InProcessQueue` con las demás: aceptable en v1.

## Doc de tb-agent

27 (tenant isolation) para el aislamiento; 23 (config) para el directorio; 18
(memoria) para la frontera hilo/sesión. Releer 27 antes del paso 3 y anotar aquí
en qué difiere.

## Qué aprendí

_(pendiente: se escribe al cerrar la slice.)_
