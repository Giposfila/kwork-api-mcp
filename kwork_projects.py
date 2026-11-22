"""Выгрузка публичных проектов Kwork в JSON (Python 3.11+)."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import webbrowser
from argparse import Namespace
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECTS_URL = "https://kwork.ru/projects"
STATE = re.compile(r"window\.stateData\s*=\s*(?=\{)")
MAX_HTML_BYTES = 8 * 1024 * 1024


def integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def amount(value):
    if type(value) not in (int, float, str):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number) if number.is_integer() else number


def parse_page(html):
    for match in STATE.finditer(html):
        try:
            state, _ = json.JSONDecoder().raw_decode(html, match.end())
        except json.JSONDecodeError:
            continue
        listing = state.get("wantsListData") or {}
        if not isinstance(listing, dict):
            continue
        pagination = listing.get("pagination") or {}
        if not isinstance(pagination, dict):
            continue
        for rows in (pagination.get("data"), listing.get("wants"), state.get("wants")):
            if isinstance(rows, list):
                return rows, integer(pagination.get("current_page")), integer(pagination.get("last_page"))
    raise ValueError("В HTML нет списка проектов Kwork. Возможно, сайт вернул другую страницу или изменил формат.")


def normalize(row):
    project_id = integer(row.get("id"))
    title = row.get("name")
    if project_id is None or project_id <= 0 or not isinstance(title, str) or not title.strip():
        raise ValueError("Kwork вернул карточку без ID или названия.")
    description = row.get("description")
    return {
        "id": project_id,
        "title": unescape(title).strip(),
        "description": unescape(description).strip() if isinstance(description, str) else "",
        "url": f"{PROJECTS_URL}/{project_id}/view",
        "category_id": integer(row.get("category_id")),
        "desired_budget_rub": amount(row.get("priceLimit")),
        "max_budget_rub": amount(row.get("possiblePriceLimit")),
        "offers_count": integer(row.get("kwork_count")),
        "created_at": row.get("date_create"),
        "active_until": row.get("date_active"),
    }


def matches(project, args):
    text = f"{project['title']}\n{project['description']}".casefold()
    if args.keyword and not any(word in text for word in args.keyword):
        return False
    if any(word in text for word in args.exclude):
        return False
    budget = project["desired_budget_rub"]
    if args.min_budget is not None and (budget is None or budget < args.min_budget):
        return False
    if args.max_budget is not None and (budget is None or budget > args.max_budget):
        return False
    offers = project["offers_count"]
    return args.max_offers is None or (offers is not None and offers <= args.max_offers)


def fetch_page(page, category):
    query = {}
    if category is not None:
        query["c"] = category
    if page > 1:
        query["page"] = page
    url = f"{PROJECTS_URL}?{urlencode(query)}" if query else PROJECTS_URL
    request = Request(url, headers={
        "Accept": "text/html",
        "Accept-Language": "ru",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    })
    with urlopen(request, timeout=25) as response:
        body = response.read(MAX_HTML_BYTES + 1)
        if len(body) > MAX_HTML_BYTES:
            raise ValueError("Размер ответа Kwork превышает 8 МиБ.")
        return body.decode(response.headers.get_content_charset() or "utf-8")


def collect(args):
    results = []
    seen = set()
    for page in range(1, args.pages + 1):
        if page > 1:
            time.sleep(2)
        html = args.html.read_text(encoding="utf-8") if args.html else fetch_page(page, args.category)
        rows, current_page, last_page = parse_page(html)
        if not args.html and current_page is not None and current_page != page:
            raise ValueError(f"Kwork вернул страницу {current_page} вместо {page}.")
        if not rows:
            break

        seen_before = len(seen)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Kwork вернул карточку неизвестного формата.")
            project = normalize(row)
            project_id = project["id"]
            if project_id in seen:
                continue
            seen.add(project_id)
            if matches(project, args):
                results.append(project)
                if len(results) == args.limit:
                    return results

        if last_page is not None and page >= last_page:
            break
        if len(seen) == seen_before:
            raise ValueError(f"Kwork вернул уже просмотренные карточки на странице {page}.")
    return results


def validate_options(args):
    for name in ("category", "pages", "limit", "min_budget", "max_budget", "max_offers"):
        value = getattr(args, name)
        minimum = 1 if name in ("category", "pages", "limit") else 0
        if value is not None and value < minimum:
            raise ValueError(f"--{name.replace('_', '-')} должен быть не меньше {minimum}")
    if args.min_budget is not None and args.max_budget is not None and args.min_budget > args.max_budget:
        raise ValueError("--min-budget не может превышать --max-budget")
    if args.html and (args.pages != 1 or args.category is not None):
        raise ValueError("--html читает одну страницу и не принимает --category или --pages больше 1")
    for name in ("keyword", "exclude"):
        words = [word.strip().casefold() for word in getattr(args, name)]
        if "" in words:
            raise ValueError(f"--{name} требует непустую строку")
        setattr(args, name, words)


def error_message(error):
    if isinstance(error, HTTPError):
        return f"Kwork вернул HTTP {error.code}."
    if isinstance(error, URLError):
        return f"Не удалось подключиться к Kwork: {error.reason}"
    return f"Ошибка выгрузки: {error}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--visual", action="store_true", help="Открыть форму поиска в браузере")
    parser.add_argument("--category", type=int, help="ID рубрики из URL биржи (параметр c)")
    parser.add_argument("--keyword", action="append", default=[], help="Подстрока в названии или описании. Подойдёт любая из указанных")
    parser.add_argument("--exclude", action="append", default=[], help="Исключить совпадение. Можно передать несколько раз")
    parser.add_argument("--min-budget", type=int, help="Минимальный желаемый бюджет в рублях")
    parser.add_argument("--max-budget", type=int, help="Максимальный желаемый бюджет в рублях")
    parser.add_argument("--max-offers", type=int, help="Максимум откликов на проект")
    parser.add_argument("--pages", type=int, default=1, help="Максимум страниц для просмотра (по умолчанию 1)")
    parser.add_argument("--limit", type=int, help="Максимум подходящих проектов")
    parser.add_argument("--output", "-o", type=Path, help="Записать в файл вместо вывода в stdout")
    parser.add_argument("--html", type=Path, help="Прочитать одну сохранённую страницу биржи из HTML-файла")
    args = parser.parse_args()

    try:
        validate_options(args)
    except ValueError as error:
        parser.error(str(error))

    if args.visual:
        return serve(args)

    try:
        output = json.dumps(collect(args), ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (OSError, ValueError, LookupError) as error:
        print(error_message(error), file=sys.stderr)
        return 1
    return 0


NUMBER_FIELDS = ("category", "min_budget", "max_budget", "max_offers", "pages", "limit")
TEXT_FIELDS = ("keyword", "exclude")
MAX_FORM_BYTES = 16 * 1024


def search_options(data, defaults):
    if not isinstance(data, dict):
        raise ValueError("Некорректные параметры поиска.")
    options = {"html": defaults.html}
    for field in NUMBER_FIELDS:
        value = data.get(field)
        if value is not None and type(value) is not int:
            raise ValueError("Числовые поля должны содержать целые числа.")
        options[field] = value
    if options["pages"] is None:
        options["pages"] = 1
    for field in TEXT_FIELDS:
        values = data.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("Ключевые слова должны быть текстом.")
        options[field] = values
    return Namespace(**options)


def serve(defaults):
    search_lock = Lock()
    config = {field: getattr(defaults, field) for field in NUMBER_FIELDS + TEXT_FIELDS}
    config["filename"] = defaults.output.name if defaults.output else "projects.json"
    config["offline"] = defaults.html.name if defaults.html else None

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json_reply(self, status, data):
            self.reply(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            if self.headers.get("Host") != host:
                self.send_error(403)
            elif self.path == "/":
                self.reply(200, UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/config":
                self.json_reply(200, config)
            else:
                self.send_error(404)

        def do_POST(self):
            if (self.headers.get("Host") != host or self.headers.get("Origin") != url
                    or self.headers.get("X-Kwork-UI") != "search"):
                self.send_error(403)
                return
            if self.path != "/search":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_FORM_BYTES:
                    raise ValueError("Размер формы превышает лимит или форма пуста.")
                options = search_options(json.loads(self.rfile.read(size)), defaults)
                validate_options(options)
            except (ValueError, RecursionError) as error:
                self.json_reply(400, {"error": str(error)})
                return
            if not search_lock.acquire(blocking=False):
                self.json_reply(409, {"error": "Подождите завершения текущего поиска."})
                return
            try:
                self.json_reply(200, {"projects": collect(options)})
            except (OSError, ValueError, LookupError) as error:
                self.json_reply(502, {"error": error_message(error)})
            finally:
                search_lock.release()

        def log_message(self, format, *args):
            pass

    try:
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            host = f"127.0.0.1:{server.server_port}"
            url = f"http://{host}"
            print(f"Интерфейс поиска Kwork: {url}  (Ctrl+C для остановки)", flush=True)
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        print(f"Не удалось запустить локальный интерфейс: {error}", file=sys.stderr)
        return 1


UI_HTML = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Поиск проектов Kwork</title>
<style>
  :root {
    color-scheme: light;
    --ink: #152d29;
    --muted: #536760;
    --line: #d1dcd5;
    --surface: #fafcf9;
    --accent: #176448;
    --accent-dark: #114e38;
    font: 16px/1.5 "Noto Sans", "Segoe UI", sans-serif;
    color: var(--ink);
    background: #eaf0ea;
  }
  * { box-sizing: border-box; }
  body { margin: 0; }
  header { background: #122f29; color: #f4faf3; border-bottom: 4px solid #9fd3a9; }
  header div, main { max-width: 1180px; margin: auto; padding: 24px 30px; }
  h1, h2, h3, p { margin: 0; }
  h1 { font: 700 clamp(1.65rem, 4vw, 2.35rem)/1.15 Georgia, serif; }
  h2 { font: 700 1.38rem/1.2 Georgia, serif; }
  main { display: grid; grid-template-columns: minmax(280px, 360px) minmax(0, 1fr); gap: 22px; align-items: start; }
  section, form { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 22px; min-width: 0; }
  form { display: grid; gap: 14px; }
  label { display: grid; gap: 4px; font-size: .88rem; font-weight: 650; min-width: 0; }
  input, textarea, select, button { font: inherit; }
  input, select, textarea {
    width: 100%; padding: 8px 10px; border: 1px solid #a4b7ab;
    border-radius: 6px; color: var(--ink); background: white;
  }
  textarea { resize: vertical; min-height: 3rem; }
  input::placeholder, textarea::placeholder { color: var(--muted); opacity: 1; }
  :focus-visible { outline: 3px solid #327a53; outline-offset: 3px; }
  .pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  small { font-weight: 400; color: var(--muted); }
  button {
    cursor: pointer; border-radius: 6px; padding: 8px 12px; border: 1px solid var(--accent);
    background: var(--accent); color: white; font-weight: 700;
  }
  button:not(:disabled):hover { background: var(--accent-dark); color: white; }
  button:disabled { cursor: default; opacity: .55; }
  #download { background: transparent; color: var(--accent-dark); }
  .toolbar { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 10px; }
  #status { margin-top: 16px; color: var(--muted); }
  #status.error { color: #942b28; }
  #projects { list-style: none; padding: 0; margin: 0; }
  #projects li { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 14px; }
  h3 { font-size: 1.02rem; overflow-wrap: anywhere; }
  a { color: var(--accent-dark); text-underline-offset: .18em; }
  .stats { color: var(--muted); font-size: .88rem; margin-top: 4px; }
  details { margin-top: 6px; }
  summary { cursor: pointer; font-size: .88rem; color: var(--accent-dark); }
  details p { margin-top: 6px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .92rem; }
  [hidden] { display: none !important; }
  @media (max-width: 720px) {
    main { grid-template-columns: 1fr; gap: 14px; }
    header div, main { padding: 17px; }
    section, form { padding: 16px; }
  }
</style>
</head>
<body>
<header><div><h1>Поиск проектов Kwork</h1></div></header>
<main>
  <form id="filters" aria-labelledby="filter-title">
    <h2 id="filter-title">Фильтры</h2>
    <p id="offline" hidden></p>
    <label>Рубрика
      <select name="category_choice">
        <option value="">Все рубрики</option>
        <option value="15">Дизайн</option>
        <option value="11">Разработка и IT</option>
        <option value="5">Тексты и переводы</option>
        <option value="17">SEO и трафик</option>
        <option value="45">Соцсети и маркетинг</option>
        <option value="7">Аудио, видео, съёмка</option>
        <option value="83">Бизнес и жизнь</option>
        <option value="custom">Указать ID рубрики</option>
      </select>
    </label>
    <label id="category-id" hidden>ID рубрики
      <input type="number" name="category" min="1" placeholder="Например, 41">
      <small>Берётся из параметра c в адресе биржи.</small>
    </label>
    <label>Ключевые слова
      <textarea name="keyword" rows="2" placeholder="Python&#10;FastAPI"></textarea>
      <small>По одному на строку. Достаточно любого совпадения.</small>
    </label>
    <label>Исключить слова
      <textarea name="exclude" rows="1" placeholder="WordPress"></textarea>
    </label>
    <div class="pair" role="group" aria-label="Желаемый бюджет">
      <label>Бюджет от, ₽<input type="number" name="min_budget" min="0" placeholder="Любой"></label>
      <label>Бюджет до, ₽<input type="number" name="max_budget" min="0" placeholder="Любой"></label>
    </div>
    <label>Максимум откликов<input type="number" name="max_offers" min="0" placeholder="Без ограничения"></label>
    <div class="pair">
      <label>Страниц<input type="number" name="pages" min="1" value="1" required></label>
      <label>Лимит проектов<input type="number" name="limit" min="1" placeholder="Без лимита"></label>
    </div>
    <button id="search" type="submit" disabled>Найти проекты</button>
  </form>
  <section aria-labelledby="result-title">
    <div class="toolbar">
      <h2 id="result-title">Результаты</h2>
      <button id="download" type="button" disabled>Скачать JSON</button>
    </div>
    <p id="status" role="status">Задайте параметры и запустите поиск.</p>
    <ul id="projects"></ul>
  </section>
</main>
<script>
  const form = document.querySelector('#filters');
  const fields = form.elements;
  const status = document.querySelector('#status');
  const projects = document.querySelector('#projects');
  const search = document.querySelector('#search');
  const download = document.querySelector('#download');
  const numberFields = ['min_budget', 'max_budget', 'max_offers', 'pages', 'limit'];
  const money = new Intl.NumberFormat('ru-RU', {style: 'currency', currency: 'RUB', maximumFractionDigits: 2, minimumFractionDigits: 0});
  let rows = [];
  let filename = 'projects.json';

  function showStatus(text, error = false) {
    status.textContent = text;
    status.classList.toggle('error', error);
  }

  function showCategoryId() {
    const custom = fields.category_choice.value === 'custom';
    document.querySelector('#category-id').hidden = !custom;
    fields.category.disabled = !custom;
    fields.category.required = custom;
  }
  fields.category_choice.addEventListener('change', showCategoryId);
  showCategoryId();

  function renderProject(project) {
    const card = document.createElement('li');
    const title = document.createElement('h3');
    const link = document.createElement('a');
    link.textContent = project.title;
    link.href = project.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    title.append(link);
    const stats = document.createElement('p');
    stats.className = 'stats';
    const values = [];
    if (project.desired_budget_rub !== null) values.push('Бюджет до ' + money.format(project.desired_budget_rub));
    if (project.max_budget_rub !== null && project.max_budget_rub !== project.desired_budget_rub) {
      values.push('Допустимый до ' + money.format(project.max_budget_rub));
    }
    if (project.offers_count !== null) values.push('Отклики · ' + project.offers_count);
    stats.textContent = values.join('  /  ');
    card.append(title, stats);
    if (project.description) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = 'Описание';
      const description = document.createElement('p');
      description.textContent = project.description;
      details.append(summary, description);
      card.append(details);
    }
    return card;
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const params = Object.fromEntries(numberFields.map(name => [name, fields[name].valueAsNumber]));
    const category = fields.category_choice.value;
    params.category = category === 'custom' ? fields.category.valueAsNumber : Number(category) || null;
    for (const name of ['keyword', 'exclude']) {
      params[name] = fields[name].value.split('\n').map(line => line.trim()).filter(Boolean);
    }
    search.disabled = true;
    download.disabled = true;
    projects.replaceChildren();
    showStatus('Загружаю проекты…');
    projects.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/search', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Kwork-UI': 'search'},
        body: JSON.stringify(params),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      rows = result.projects;
      projects.replaceChildren(...rows.map(renderProject));
      showStatus(rows.length ? 'Найдено проектов: ' + rows.length : 'По заданным параметрам ничего не найдено.');
      download.disabled = false;
    } catch (error) {
      showStatus(error.message || 'Не удалось выполнить поиск.', true);
    } finally {
      search.disabled = false;
      projects.removeAttribute('aria-busy');
    }
  });

  download.addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(rows, null, 2) + '\n'], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  async function init() {
    try {
      const response = await fetch('/config');
      if (!response.ok) throw new Error();
      const config = await response.json();
      for (const name of numberFields) fields[name].value = config[name] ?? '';
      for (const name of ['keyword', 'exclude']) fields[name].value = config[name].join('\n');
      const category = String(config.category ?? '');
      fields.category_choice.value = category;
      if (fields.category_choice.selectedIndex === -1) {
        fields.category_choice.value = 'custom';
        fields.category.value = category;
      }
      showCategoryId();
      filename = config.filename;
      if (config.offline) {
        fields.category_choice.disabled = true;
        fields.pages.readOnly = true;
        const offline = document.querySelector('#offline');
        offline.textContent = 'Поиск по файлу ' + config.offline;
        offline.hidden = false;
      }
      search.disabled = false;
    } catch {
      showStatus('Не удалось загрузить параметры. Проверьте, запущен ли скрипт.', true);
    }
  }
  init();
</script>
</body>
</html>
'''

if __name__ == "__main__":
    sys.exit(main())
