# Выгрузка статей базы знаний Dodo IS (dodopizza.info) через её внутренний JSON API.
#
# Зачем: нужен точный ответ на вопрос, что Dodo IS считает «ключевым ингредиентом»
# и «Категорией А» — от него зависит метрика «Стопы ключевых ингредиентов», которая
# сходится с их выгрузкой лишь на 35% (см. docs/stops-lost-revenue-reconciliation.md).
# Формулу расчёта мы в своё время взяли из KB-статьи f878a355-b8b8-4263-9db1-73e3c7b9cc2e;
# определение «ключевого» лежит там же, если лежит вообще.
#
# Способ подсмотрен в open-source проекте 2mozg-oss (Apache-2.0,
# src/assistant/knowledge/crawler.py): у dodopizza.info есть внутренний JSON API,
# который отдаёт статьи целиком — надёжнее и полнее, чем скрапинг DOM.
#     GET /api/feed/getFeed/{page}   лента, по 10 статей на страницу
#     GET /api/feed/navigation/      дерево навигации (закреплённые статьи)
#     GET /api/articles/{uuid}       {title, content(HTML)}
# Запросы идут fetch()'ем из контекста авторизованной страницы Playwright — куки
# сессии подставляются браузером сами, отдельный токен не нужен.
#
# Это РУЧНОЙ скрипт: он не в расписании, playwright намеренно НЕ добавлен в
# requirements.txt (иначе каждый CI-job тянул бы браузер). Поставить локально:
#     pip install playwright && python -m playwright install chromium
#
# Логин интерактивный, в видимом окне браузера: у Dodo IS SSO с MFA, и надёжнее
# дать человеку ввести пароль и код самому, чем плодить обработку форм. Сессия
# сохраняется в kb_cache/state.json, так что crawl потом идёт без окна.
#
#     python fetch_kb_articles.py login     — окно браузера, войти руками
#     python fetch_kb_articles.py crawl     — выгрузить все статьи в kb_cache/
#     python fetch_kb_articles.py search ключев ингредиент "категория а"
#
# Содержимое kb_cache/ — внутренние документы компании, каталог в .gitignore.
import argparse
import asyncio
import html as _html
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

KB_BASE = "https://dodopizza.info"
LOGIN_URL = f"{KB_BASE}/auth/ExternalLogin?ReturnUrl=%2Flogin&provider=kb-oidc"
PROBE_URL = f"{KB_BASE}/search?q=%D0%BC%D0%B0%D1%80%D0%BA%D0%B5%D1%82%D0%B8%D0%BD%D0%B3"  # /search?q=маркетинг

CACHE = Path("kb_cache")
STATE = CACHE / "state.json"
ARTICLES = CACHE / "articles"
FEED_MAX_PAGES = 200
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
BLOCK_RE = re.compile(r"</(p|div|li|tr|h[1-6]|table|ul|ol|section|article)\s*>|<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\xa0]+")
NL_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    s = SCRIPT_RE.sub(" ", raw)
    s = BLOCK_RE.sub("\n", s)
    s = TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    s = WS_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return NL_RE.sub("\n\n", s).strip()


def editorjs_to_text(raw: str) -> str:
    """Статьи из пространств приходят документом EditorJS, а не HTML.

    Структура: `{"blocks": [{"id","type","data",...}, ...]}` — плоский список.
    Таблицы (`type: table`) НЕ содержат текста: их `data.content` — матрица
    ссылок вида `{"blocks": ["paragraph-4"]}` на другие блоки того же списка,
    а сами эти блоки лежат рядом на верхнем уровне (с полем `parent`). Поэтому
    достаточно пройти плоский список и собрать текстовые поля из `data`,
    а матрицу ссылок пропустить — иначе в текст попадут id блоков.
    """
    try:
        doc = json.loads(raw)
    except Exception:
        return html_to_text(raw)  # вдруг всё-таки HTML
    blocks = doc.get("blocks") if isinstance(doc, dict) else doc
    if not isinstance(blocks, list):
        return html_to_text(raw)

    out: list[str] = []

    def add(value):
        if isinstance(value, str):
            t = html_to_text(value)
            if t:
                out.append(t)
        elif isinstance(value, list):
            for v in value:
                add(v)
        elif isinstance(value, dict):
            for k in ("text", "content", "caption", "title", "label"):
                if isinstance(value.get(k), (str, list, dict)):
                    add(value[k])
                    break

    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        data = blk.get("data")
        if not isinstance(data, dict):
            continue
        for key in ("text", "caption", "title", "message", "alignment_text"):
            add(data.get(key))
        add(data.get("items"))  # списки и чек-листы
        if blk.get("type") in ("link", "linkTool"):
            add((data.get("meta") or {}).get("title") if isinstance(data.get("meta"), dict) else None)

    return NL_RE.sub(2 * chr(10), chr(10).join(out)).strip()


async def api_get(page, path: str):
    """GET JSON через fetch() в контексте авторизованной страницы."""
    url = path if path.startswith("http") else f"{KB_BASE}{path}"
    try:
        res = await page.evaluate(
            """async (u) => {
                try {
                    const r = await fetch(u, {credentials:'include', headers:{'Accept':'application/json'}});
                    return {status: r.status, body: await r.text()};
                } catch (e) { return {status: -1, body: String(e)}; }
            }""",
            url,
        )
    except Exception:
        # Во время входа страница ходит по редиректам SSO, и evaluate падает с
        # «Execution context was destroyed». Для опроса это просто «пока не готово».
        return None
    if not res or res.get("status") != 200:
        return None
    try:
        return json.loads(res["body"])
    except Exception:
        return None


async def is_authenticated(page) -> bool:
    """Спрашиваем сам API, а не текст страницы.

    Проверка по DOM (ищем «Sign in» в innerText) ненадёжна: без авторизации
    /search отвечает 200 и лишь потом редиректит на /Auth/Login, так что в
    момент проверки body бывает ещё пустым — и пустой body читался как «мы
    вошли». API же отвечает однозначно: 401 без сессии, JSON-массив с ней.
    """
    if "dodopizza.info" not in page.url:  # нужен same-origin контекст для fetch
        await page.goto(KB_BASE, timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    return isinstance(await api_get(page, "/api/feed/getFeed/1"), list)


async def cmd_login(args):
    from playwright.async_api import async_playwright

    CACHE.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(storage_state=str(STATE) if STATE.exists() else None)
        page = await ctx.new_page()

        if await is_authenticated(page):
            print("Сохранённая сессия ещё жива — вход не нужен.")
            await ctx.storage_state(path=str(STATE))
            await browser.close()
            return

        await page.goto(LOGIN_URL, timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)

        # Логин/пароль подставим, если они есть в .env — код MFA всё равно вводит человек.
        user, pwd = os.getenv("DODOPIZZA_USERNAME"), os.getenv("DODOPIZZA_PASSWORD")
        if user and pwd:
            try:
                login_input = page.locator(
                    "#login-form-input-login, input[name='Login'], input[autocomplete='username']"
                ).first
                if await login_input.is_visible(timeout=5000):
                    await login_input.fill(user)
                    await page.locator(
                        "#login-form-input-password, input[name='Password'], input[type='password']"
                    ).first.fill(pwd)
                    print("Логин и пароль подставлены из .env — нажмите «Войти» и введите код.")
            except Exception as e:
                print(f"Не удалось подставить креды ({e}) — введите вручную.")
        else:
            print("DODOPIZZA_USERNAME/PASSWORD в .env нет — войдите в окне браузера вручную.")

        # Опрашиваем API, а не URL: маршруты SSO меняются, а 401/JSON — нет.
        print(f"Жду успешного входа (до {args.timeout} с)...")
        for _ in range(args.timeout // 5):
            await page.wait_for_timeout(5000)
            if "dodopizza.info" not in page.url:
                continue  # мы ещё на стороне провайдера SSO, fetch туда не отправить
            if isinstance(await api_get(page, "/api/feed/getFeed/1"), list):
                await ctx.storage_state(path=str(STATE))
                print(f"Готово, сессия сохранена в {STATE}")
                await browser.close()
                return
        print("Не дождался входа. Запустите ещё раз.", file=sys.stderr)
        await browser.close()
        raise SystemExit(1)


def _first_list_of_dicts(data):
    """Ответы KB непоследовательны: где-то голый список, где-то {items:[...]}."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "content", "articles", "data", "results"):
            v = data.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


async def collect_space_article_ids(page) -> dict[str, str]:
    """article_id -> название пространства.

    Лента (`getFeed`) отдаёт только новости — справочные разделы в неё не попадают
    (проверено: методичка по стопам продуктов в ленте отсутствует). Реальный
    справочник лежит в «пространствах»:
        GET /api/v2/user/me/spaces                         список пространств
        GET /api/spaces/{id}/content?limit&page&spaceLanguage=ru-RU
    Маршрут подсмотрен в сетевых запросах самого сайта при переходе в раздел.
    """
    out: dict[str, str] = {}
    spaces = _first_list_of_dicts(await api_get(page, "/api/v2/user/me/spaces"))
    print(f"Пространств: {len(spaces)}", flush=True)
    for sp in spaces:
        sid, stitle = sp.get("id"), str(sp.get("title") or sp.get("name") or "?")
        if not sid:
            continue
        got, expected = 0, None
        for pg_no in range(1, FEED_MAX_PAGES + 1):
            data = await api_get(page, f"/api/spaces/{sid}/content?limit=100&page={pg_no}&spaceLanguage=ru-RU")
            if not isinstance(data, dict):
                break
            # Только ключ `content` — рядом лежат `pinnedThemes`/`unpinnedThemes`
            # (это теги с counts, не статьи). Раньше здесь стоял «найди любой
            # список словарей», и на пустой второй странице он подхватывал темы:
            # список никогда не пустел, цикл крутил все 200 страниц на каждое
            # пространство и выгрузка не доходила до самих статей.
            chunk = [x for x in (data.get("content") or []) if isinstance(x, dict)]
            if expected is None:
                expected = data.get("articlesCount")
            if not chunk:
                break
            for item in chunk:
                aid = str(item.get("id") or "")
                if UUID_RE.fullmatch(aid):
                    out.setdefault(aid, stitle)
                    got += 1
            if len(chunk) < 100 or (isinstance(expected, int) and got >= expected):
                break
        print(f"  {stitle}: {got}" + (f" из {expected}" if expected is not None else ""), flush=True)
    return out


async def collect_article_ids(page) -> list[str]:
    ids: dict[str, None] = {}
    for pg in range(1, FEED_MAX_PAGES + 1):
        feed = await api_get(page, f"/api/feed/getFeed/{pg}")
        if not isinstance(feed, list) or not feed:
            break
        for item in feed:
            if not isinstance(item, dict) or item.get("itemType", "article") != "article":
                continue
            iid = item.get("instanceId") or item.get("id")
            if iid and UUID_RE.fullmatch(str(iid)):
                ids.setdefault(str(iid))
        print(f"  лента, страница {pg}: всего id {len(ids)}", flush=True)
        if len(feed) < 10:
            break

    nav = await api_get(page, "/api/feed/navigation/")
    if isinstance(nav, list):
        for item in nav:
            if not isinstance(item, dict):
                continue
            if item.get("itemType") == "article" and item.get("itemId"):
                m = UUID_RE.search(str(item["itemId"]))
                if m:
                    ids.setdefault(m.group(0))
            url = str(item.get("url", ""))
            m = UUID_RE.search(url)
            if m and "/article/" in url:
                ids.setdefault(m.group(0))
        print(f"  навигация: всего id {len(ids)}")
    return list(ids)


async def cmd_crawl(args):
    from playwright.async_api import async_playwright

    if not STATE.exists():
        raise SystemExit(f"Нет {STATE} — сначала `python {Path(__file__).name} login`")
    ARTICLES.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=str(STATE))
        page = await ctx.new_page()

        if not await is_authenticated(page):
            await browser.close()
            raise SystemExit("Сессия протухла — перезапустите `login`")

        print("Собираю список статей из пространств...")
        space_of = await collect_space_article_ids(page)
        print("Собираю список статей из ленты...")
        feed_ids = await collect_article_ids(page)
        ids = list(dict.fromkeys(list(space_of) + feed_ids))
        print(f"Уникальных статей: {len(ids)} (пространства {len(space_of)}, лента {len(feed_ids)})")

        index, fetched, skipped = [], 0, 0
        for i, aid in enumerate(ids, 1):
            dest = ARTICLES / f"{aid}.json"
            if dest.exists() and not args.force:
                skipped += 1
                index.append(json.loads(dest.read_text(encoding="utf-8"))["title"])  # из кэша, без раздела
                continue
            # Два разных источника: статьи из новостной ленты отдаёт
            # /api/articles/{id} (контент — HTML), статьи из пространств —
            # /api/content/{id} (контент — документ EditorJS). Первый на
            # «пространственных» id отвечает 400, поэтому пробуем по очереди.
            data = await api_get(page, f"/api/articles/{aid}")
            is_editorjs = False
            if not isinstance(data, dict):
                data = await api_get(page, f"/api/content/{aid}")
                is_editorjs = True
            if not isinstance(data, dict):
                continue
            title = str(data.get("title") or "").strip() or "Без названия"
            raw = str(data.get("content") or data.get("contentJson") or data.get("contentValue") or "")
            text = editorjs_to_text(raw) if is_editorjs else html_to_text(raw)
            if len(text) < 20:
                continue
            dest.write_text(
                json.dumps(
                    {"id": aid, "title": title, "space": data.get("spaceTitle") or space_of.get(aid, ""),
                     "text": text, "raw": raw},  # raw — чтобы правки парсера не требовали перекачки
                    ensure_ascii=False, indent=1,
                ),
                encoding="utf-8",
            )
            index.append(f"[{space_of.get(aid, 'лента')}] {title}")
            fetched += 1
            if i % 25 == 0:
                print(f"  {i}/{len(ids)} (скачано {fetched}, из кэша {skipped})", flush=True)
            await page.wait_for_timeout(50)  # вежливость к API

        (CACHE / "index.txt").write_text("\n".join(sorted(index)), encoding="utf-8")
        print(f"Готово: скачано {fetched}, из кэша {skipped}, всего файлов {len(list(ARTICLES.glob('*.json')))}")
        print(f"Список заголовков: {CACHE / 'index.txt'}")
        await browser.close()


def cmd_search(args):
    files = sorted(ARTICLES.glob("*.json"))
    if not files:
        raise SystemExit(f"Пусто в {ARTICLES} — сначала `crawl`")
    terms = [t.lower() for t in args.terms]
    hits = 0
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        hay = (doc["title"] + "\n" + doc["text"]).lower()
        matched = [t for t in terms if t in hay]
        if len(matched) < args.min_terms:
            continue
        hits += 1
        print(f"\n{'=' * 78}\n{doc['title']}\n{KB_BASE}/article/{doc['id']}  [совпало: {', '.join(matched)}]")
        text = doc["text"]
        low = text.lower()
        shown = 0
        for t in matched:
            pos = 0
            while shown < args.context_per_doc:
                idx = low.find(t, pos)
                if idx < 0:
                    break
                a, b = max(0, idx - 250), min(len(text), idx + 350)
                print(f"  …{text[a:b].strip()}…")
                shown += 1
                pos = idx + len(t)
    print(f"\nНайдено статей: {hits} из {len(files)}")


def main():
    # Консоль Windows по умолчанию cp1251 — статьи КБ содержат стрелки, тире и
    # прочие символы вне неё, и печать падала бы с UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="интерактивный вход, сохраняет сессию")
    p_login.add_argument("--timeout", type=int, default=300, help="сколько секунд ждать входа")

    p_crawl = sub.add_parser("crawl", help="выгрузить статьи в kb_cache/articles")
    p_crawl.add_argument("--force", action="store_true", help="перекачать даже то, что в кэше")

    p_search = sub.add_parser("search", help="поиск по выгруженным статьям")
    p_search.add_argument("terms", nargs="+", help="искомые подстроки (регистр не важен)")
    p_search.add_argument("--min-terms", type=int, default=1, help="сколько терминов должно совпасть")
    p_search.add_argument("--context-per-doc", type=int, default=3, help="сколько фрагментов показывать")

    args = ap.parse_args()
    if args.cmd == "search":
        cmd_search(args)
    else:
        asyncio.run({"login": cmd_login, "crawl": cmd_crawl}[args.cmd](args))


if __name__ == "__main__":
    main()
