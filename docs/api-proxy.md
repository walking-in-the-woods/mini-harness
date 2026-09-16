# Внешние API

`ApiProxy` — единственный сетевой инструмент агента, кроме локального
сервера инференса. Модель обращается к нему через tool `api_call`.

## Маршруты по умолчанию

| Route | Upstream | Назначение |
|---|---|---|
| `weather` | `api.open-meteo.com` | Погода (CC BY 4.0) |
| `translate` | `api.mymemory.translated.net` | Перевод (1000 слов/день) |
| `fake-data` | `jsonplaceholder.typicode.com` | Тестовые JSON |
| `countries` | `restcountries.com` | Информация о странах |

## Как модель вызывает

```
>>> вызови api_call с route="weather" и params={"latitude": 55.75,
... "longitude": 37.62, "current": "temperature_2m"}
```

Модель передаёт `route`, `method`, `params`, `body`. Хост берётся из
конфига.

## Добавить свой маршрут

В `config.yaml`:

```yaml
proxy:
  routes:
    weather:    "https://api.open-meteo.com/v1/forecast"
    my-api:     "https://api.real-service.com/v1/endpoint"
```

Перезапуск REPL — маршрут доступен.

## Почему это безопасно

- **Хост берётся из `routes`**, не из аргументов модели. Модель не
  может подставить `api.allowed.com@internal-host` или другой
  обходной URL.
- **`follow_redirects=False`.** Upstream не уведёт трафик через 30x.
- **Лимиты.** `HARNESS_PROXY_MAX_REQUEST_BYTES` (дефолт 100 000)
  и `HARNESS_PROXY_MAX_RESPONSE_BYTES` (дефолт 200 000).
- **Таймаут** из `HARNESS_PROXY_TIMEOUT` (дефолт 30 секунд).

## Ограничения

- **Внешний сервис может быть недоступен.** Прокси вернёт
  `ERROR: upstream request failed: ...`.
- **Ответ не проходит через ACL.** Если upstream вернёт инъекцию в
  теле — `neutralize_data_block` её обезвредит, но содержимое
  ответа всё равно попадёт в контекст в экранированном виде.
- **API без ключей.** Если нужен API с ключом — добавьте его в
  `routes` как часть URL или передайте через `params` в tool_call.
  Первый вариант хуже (ключ попадёт в audit через
  `_redact_args`? Нет — `params` редактируется, URL маршрута
  печатается в audit только через `source="api:route"`, без URL).
  Второй вариант хуже — ключ окажется в `params` и в логе
  `tool_call` в виде `<dict, N keys>`; значения не печатаются.
  Оба безопасны для audit, но ключ в URL может утечь в логи
  upstream.
