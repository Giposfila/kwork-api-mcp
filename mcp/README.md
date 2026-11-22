# MCP-сервер поиска проектов Kwork

Инструмент `kwork_search_projects` использует фильтры из `../kwork_projects.py` и возвращает структурированный JSON с полями `count` и `projects`. Он читает публичную биржу Kwork через интернет. Для использования достаточно Python 3.11+ и клиента с поддержкой MCP через stdio.

## Подключение

Добавьте запись `kwork-projects` из [mcp.example.json](mcp.example.json) в раздел `mcpServers` вашего клиента и перезапустите клиент. Если перенесёте проект, обновите оба абсолютных пути в записи. Клиент сам запускает сервер.

Виртуальное окружение на этой машине находится в `mcp/.venv`. Для установки с нуля выполните из корня проекта:

```bash
python3 -m venv mcp/.venv
mcp/.venv/bin/python -m pip install -r mcp/requirements.txt
```

## Поиск

Пример запроса к нейросети: «Найди на Kwork до 10 проектов про Python или FastAPI, от 5000 ₽, с максимум 15 откликами. Исключи WordPress».

Пример аргументов инструмента:

```json
{
  "category": 41,
  "keywords": ["Python", "FastAPI"],
  "exclude_keywords": ["WordPress"],
  "min_budget": 5000,
  "max_offers": 15,
  "pages": 3,
  "limit": 10
}
```

Все параметры необязательны. По умолчанию сервер просматривает до трёх страниц и возвращает до 20 проектов после фильтрации. `category` — число из параметра `c` в URL биржи. Границы `min_budget` и `max_budget` включительные и относятся к желаемому бюджету. При отсутствии совпадений результат содержит `count: 0` и пустой список `projects`.

[Подключение локального stdio-сервера к MCP-клиенту](https://py.sdk.modelcontextprotocol.io/get-started/real-host/)
