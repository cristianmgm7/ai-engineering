# 01 · Config (`platform/config.py`)

## Qué hace

Un único objeto `Settings` tipado (pydantic-settings `BaseSettings`) que se valida
al instanciarse. Lee de variables de entorno y de `.env` (solo local). Se consume
vía `get_settings()` (cacheado con `@lru_cache`), nunca leyendo `os.environ` directo.
Slice 1: `anthropic_api_key`, `anthropic_model`, `agent_max_turns`,
`agent_max_tokens`, `log_level`.

## Doc de tb-agent

`23-config-and-env.md`. Principio central: **un esquema tipado validado en boot**,
fail-fast ante requeridas faltantes; `.env` solo local; secretos desde el entorno.

## En qué difiere de la versión TS

- la referencia usa **Joi** (`environment.ts`, ~40 vars); acá **pydantic-settings**.
  Equivalencias: `.required()` → campo sin default · `.default(x)` → `campo: T = x`
  · validación en boot → `ValidationError` al instanciar · `ConfigService` →
  `get_settings()`.
- la referencia dejó `ANTHROPIC_API_KEY` **opcional** (`.optional().allow('')`) — el
  servicio arranca aunque no haya key. Acá la hice **requerida**: para un proyecto
  cuyo fin es llamar al modelo, prefiero fallar en el arranque.
- Usé `SecretStr` para la key (pydantic no la muestra en `repr`/`str`) — la referencia
  la trata como string plano. El doc 20 prohíbe loguear secretos, así que esto lo
  hace cumplir por construcción.
- Salté ~35 variables de la referencia (Mongo, Redis, Keycloak, OAuth, CORS,
  attachments, memoria): infra que decidimos no clonar.

## Decisiones y tradeoffs

- **Plana, no agrupada.** El "group by concern" del spec paga con muchas vars; con
  5 es ruido. Se refactoriza a sub-modelos anidados cuando lleguen más.
- **`@lru_cache`** → se parsea/valida una vez por proceso. Los tests que tocan el
  entorno llaman `get_settings.cache_clear()`.
- **Tests herméticos:** todos pasan `_env_file=None` para que un `.env` local no
  altere el resultado, y controlan el entorno con `monkeypatch`.

## Preguntas abiertas

- ¿Confirmar el id de modelo por defecto (`claude-sonnet-5`) al construir el
  ModelProvider? (componente 3, con la skill claude-api).
- ¿Cuándo introducir un `env` (development/test/production)? Aún nada se ramifica
  por él; lo añado cuando algo lo necesite.
