# Glosario: de Clean Architecture (mobile/Node) a este repo

Tabla de traducción personal. Vengo de Flutter/Dart (data/domain/presentation,
UseCase, Repository, DataSource) y de Node (services, repositories). Este repo
usa arquitectura hexagonal con otros nombres; este archivo es el mapa.

## 1. Traducción de conceptos

| Lo que conozco (mobile/Node)        | Aquí se llama                          | Dónde vive                                                        |
| ----------------------------------- | -------------------------------------- | ----------------------------------------------------------------- |
| Entity / model                      | modelo pydantic `frozen=True`          | `domain/` (`Pedido`, `InboundEvent`) y `platform/` (`Message`)     |
| Repository (interfaz abstracta)     | **Store** (un `Protocol`)              | `domain/restaurante/pedidos.py` (`PedidoStore`), `core/memory.py` (`SessionStore`) |
| Repository impl + DataSource        | `InMemory*Store` / `Sqlite*Store`      | `adapters/stores/`, `adapters/restaurante/`                        |
| Cliente de API externa              | **Provider** / **Client**              | `adapters/models/anthropic.py` (`AnthropicModelProvider`)          |
| UseCase                             | métodos de un **Service**              | `core/turns.py` (`TurnService.handle`, `.decide`)                  |
| Service (Node)                      | `ReasoningLoop`, `PolicyExecutor`      | `core/runner.py`, `core/tools.py`                                  |
| Controller / route + DTO            | **Ingress** + **ChannelAdapter**       | `edges/ingress.py`, `edges/whatsapp/adapter.py`                    |
| Presenter / ViewModel               | **Responder**                          | `edges/whatsapp/responder.py`                                      |
| Guard / middleware de permisos      | **Policy**                             | `core/policy.py`, `adapters/restaurante/policy.py`                 |
| Contenedor de DI (get_it, Nest)     | la función `build()` (composition root)| `edges/whatsapp/app.py`                                            |
| interface (TS) / abstract class     | `typing.Protocol` (estructural)        | en la capa que lo *consume*, no junto a la implementación          |
| implements / extends                | no existe: duck typing verificado por mypy/pyright | la implementación ni importa el puerto             |
| Mock (mocktail, jest.fn)            | **fake escrito a mano**                | `tests/` (`ScriptedModel`, `RecordingParker`)                      |

## 2. Equivalencia de capas

| Mobile              | Este repo                   | Regla                                        |
| ------------------- | --------------------------- | -------------------------------------------- |
| presentation        | L4 `edges/`                 | único sitio con FastAPI/httpx                |
| data (impls)        | L3 `adapters/`              | único sitio con vendors (anthropic, sqlite)  |
| domain (use cases)  | L2 `core/`                  | orquestación pura: sin HTTP, sin DB, sin SDK |
| domain (entities)   | L1 `domain/`                | entidades + puertos del negocio              |
| (no hay equivalente)| L0 `platform/`              | primitivas: config, clock, tipos del modelo  |

Diferencias con mi mundo: Repository y DataSource están fusionados (un solo
origen de datos no necesita dos capas); no hay una clase por UseCase sino un
Service con pocos métodos; y parte del "use case" lo decide el LLM en runtime
(qué tool llamar), por eso existen piezas sin equivalente mobile: `Policy`,
`ApprovalGate`, `ToolExecutor` y los evals.

## 3. Las 4 preguntas para orientarme en cualquier backend

Los nombres cambian de repo a repo (Store/Repository/Gateway/DAO son el mismo
rol); los roles no. Buscar las respuestas, no las palabras:

1. **¿Por dónde entra el mundo?** (HTTP, webhook, cola, CLI) → la "presentation".
   Aquí: `edges/ingress.py`.
2. **¿Dónde está la decisión de negocio sin tecnología?** → el "domain".
   Aquí: `domain/` (p. ej. `rechazo_para`).
3. **¿Dónde se habla con el exterior?** → los "data sources". Pista: dónde se
   importan los vendors. Aquí: `adapters/`.
4. **¿Dónde se arma todo?** → el DI. Buscar `build` / `create_app` / `main`.
   Aquí: `edges/whatsapp/app.py:build`.

## 4. Convención de nombres de este repo

- **Puerto** = el rol a secas, sin prefijo: `Clock`, `PedidoStore`,
  `ModelProvider`. Nunca `IClock`, `AbstractClock` ni `*Impl`.
- **Implementación** = tecnología o variante + rol: `SqlitePedidoStore`,
  `AnthropicModelProvider`, `SystemClock`, `NoopTracer`, `StaticToolRegistry`.
- **Decorador** = `Traced*` envuelve un puerto y delega (`TracedModelProvider`).
- **Valores/resultados** sin sufijo de patrón: `RunResult`, `ToolResult`,
  `ApprovalOutcome`; decisiones como verbos: `Allow`, `Deny`, `RequireApproval`.
- **Args de tools** = `<Acción>Args`: `CrearArgs`, `ListarArgs`.
- **Archivos**: `snake_case`; varios tipos relacionados conviven en un archivo
  (no hay "una clase por archivo"); la capa va en la primera línea del
  docstring (`"""L2 · Tools — ..."""`); `tests/` espeja `src/agent/`.
- **Producto vs. kernel**: el producto va en la *carpeta* (`whatsapp/`,
  `restaurante/`), nunca en el kernel; `tests/test_architecture.py` vigila los
  imports.

## 5. Cómo encontrar "quién implementa este puerto"

Python no tiene `implements`, así que:

1. Por nombre: `grep -rn "PedidoStore"` → `SqlitePedidoStore`, `InMemoryPedidoStore`.
2. Por ubicación: puertos en `domain/`/`core`/`platform`, impls en `adapters/`.
3. Por el composition root: `build()` muestra qué impl se inyecta en qué puerto.
4. Con el type checker: `_: PedidoStore = SqlitePedidoStore(...)` falla si deja
   de cumplirlo.
