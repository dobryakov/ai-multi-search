Главная идея: сделать гибридную систему из бесплатных API и open-source инструментов. Тогда даже если один источник перестанет работать или исчерпает лимит, остальные продолжат обслуживать запросы.

## 1. Какие бесплатные инструменты использовать

Я бы разделил их на три категории.

### Поиск в интернете (Search API)

File:SearXNG-wordmark.svg - Wikimedia Commons

1. SearXNG

Бесплатно, open-source- Можно самостоятельно развернуть на VPS или компьютере.
- Агрегирует результаты множества поисковых систем.
- Есть JSON API.
- Не требует API-ключей от самого SearXNG.

Ограничение: поисковые системы могут блокировать запросы твоего инстанса.

[Документация](<https://docs.searxng.org/?utm_source=chatgpt.com>)

Brave Search | Search Engines | Essentials | Awesome Privacy

2. Brave Search API

Бесплатные кредиты- Собственный поисковый индекс.
- Хорошо подходит для поиска URL и сниппетов.
- $5 бесплатных кредитов ежемесячно.
- Базовая цена — $5 за 1000 запросов.

[Brave Search API](<https://brave.com/search/api/?utm_source=chatgpt.com>)

Brave

+1

InnovaHack — India's Largest Hiring & Startup Hackathon | By Elite Forums

3. Tavily

Бесплатный тариф- Поиск, ориентированный на AI-агентов.
- Возвращает результаты в удобном структурированном формате.
- Бесплатный тариф — около 1000 кредитов в месяц.

[Официальный сайт](<https://tavily.com/?utm_source=chatgpt.com>)

cors.dev

+1

How to Use Firecrawl - Getting Started Guide (2026) | Delv

4. Firecrawl

Бесплатный тариф- 1000 кредитов ежемесячно.
- Примерно 500 поисковых запросов или 1000 простых операций scrape.
- Также существует open-source версия для самостоятельного развёртывания.

[Firecrawl](<https://www.firecrawl.dev/?utm_source=chatgpt.com>)

Firecrawl

+1

### Скачивание и парсинг страниц (Scraping)

Здесь я бы вообще не платил сторонним сервисам.

Crawl4AI: Easy Website Scraping for LLM Data | Siddharth Trivedi posted on the topic | LinkedIn

1. Crawl4AI

Open-sourceНаверное, мой первый выбор для твоей задачи.

- Скачивает страницы.
- Поддерживает JavaScript через браузер.
- Превращает HTML в чистый Markdown.
- Поддерживает структурированное извлечение JSON.
- Можно развернуть через Docker.

[Документация](<https://docs.crawl4ai.com/?utm_source=chatgpt.com>) · [GitHub](<https://github.com/unclecode/crawl4ai?utm_source=chatgpt.com>)

Crawl4AI Documentation (v0.9.x)

+1

Trafilatura | Haystack

2. Trafilatura

Open-sourceОтличный лёгкий парсер для обычных веб-страниц.

- Извлекает основной текст.
- Убирает навигацию, рекламу и лишний HTML.
- Поддерживает Markdown и другие форматы.
- Не требует запуска полноценного браузера.

[Документация](<https://trafilatura.readthedocs.io/?utm_source=chatgpt.com>)

Playwright Logo PNG Vector (SVG) Free Download

3. Playwright

Open-sourceДля сайтов, где контент появляется только после выполнения JavaScript.

- Chromium, Firefox, WebKit.
- Рендеринг динамических страниц.
- Управление переходами и ожиданием загрузки.

[Документация](<https://playwright.dev/?utm_source=chatgpt.com>)

Отдельно: Crawl4AI Cloud сейчас также предлагает 1000 бесплатных кредитов ежемесячно, но это уже hosted-сервис, а не самостоятельный парсер.

Crawl4AI Documentation (v0.9.x)

## 2. Какую архитектуру я бы построил

Я бы сделал единый API, который абстрагируется от конкретных провайдеров.

Твой Search API

POST /search · POST /scrape · POST /crawl

Provider Manager

Выбор провайдера, fallback, лимиты, кэширование

Search providers

- SearXNG
- Brave API
- Tavily
- Firecrawl

Scrape providers

- Crawl4AI
- Trafilatura
- Playwright
- Firecrawl

Result Normalizer

Единый JSON / Markdown формат

Redis + PostgreSQL

Кэш, история запросов, лимиты, статистика

### Предлагаемый API

Я бы предусмотрел три основных endpoint.

1\. Поиск

`POST /v1/search`

Вход:

json

```json
{
  "query": "best open source vector databases",
  "limit": 10,
  "language": "en",
  "country": "us"
}
```

Выход:

json

```json
{
  "results": [
    {
      "title": "Example",
      "url": "https://example.com",
      "snippet": "Description...",
      "source": "searxng"
    }
  ]
}
```

2\. Скачивание одной страницы

`POST /v1/scrape`

json

```json
{
  "url": "https://example.com",
  "formats": ["markdown", "html"]
}
```

3\. Поиск с извлечением контента

`POST /v1/search-and-scrape`

json

```json
{
  "query": "latest developments in AI agents",
  "limit": 5,
  "scrape": true,
  "format": "markdown"
}
```

Последний endpoint особенно полезен для LLM и AI-агентов.

## 3\. Как сделать это максимально бесплатным

Здесь есть несколько важных оптимизаций.

| Компонент | Решение |
| --- | --- |
| Search | SearXNG |
| Дополнительный Search | Brave API |
| HTML scraping | Trafilatura |
| JavaScript scraping | Crawl4AI |
| Browser automation | Playwright |
| Cache | Redis |
| Database | PostgreSQL |
| Backend | Python + FastAPI |
| Deployment | Docker Compose |

Я бы добавил следующие механизмы:

1. Smart caching. Один и тот же поисковый запрос не должен отправляться провайдеру повторно.
2. Deduplication. Убирать одинаковые URL из разных поисковых систем.
3. Provider fallback. Если Brave исчерпал лимит, переключаться на SearXNG.
4. Scrape on demand. Не скачивать все найденные страницы автоматически. Сначала получать поисковую выдачу, а затем скачивать только нужные страницы.
5. Browser only when needed. Сначала пробовать обычный HTTP-запрос + Trafilatura. Playwright запускать только при необходимости.
6. Per-domain throttling. Не создавать чрезмерную нагрузку на отдельные сайты.

## 4\. Важный нюанс: почему полностью бесплатный поиск сложен

Нужно понимать одну вещь: SearXNG не является самостоятельной поисковой системой с собственным индексом. Он агрегирует сторонние поисковые системы.

Если запустить его на своём сервере, Google, Bing и другие источники могут ограничивать или блокировать запросы.

Поэтому я бы не строил систему исключительно на SearXNG. Нужны независимые источники и кэширование. Также следует соблюдать условия использования сайтов, robots.txt и ограничения на частоту запросов.

## 5\. Моя конкретная рекомендация

Я бы начал именно такДля первой версии проекта:

| Задача | Инструмент |
| --- | --- |
| Backend | FastAPI |
| Search | SearXNG + Brave API |
| Scraping | Crawl4AI |
| Быстрое извлечение | Trafilatura |
| JS-страницы | Playwright |
| Cache | Redis |
| Storage | PostgreSQL |
| Deployment | Docker Compose |

И ещё одна интересная возможность: можно использовать бесплатные облачные лимиты как дополнительный резерв.

Например:

- Firecrawl — 1000 кредитов/месяц.
- Crawl4AI Cloud — 1000 кредитов/месяц.
- Brave Search — $5 кредитов/месяц.
- Собственный SearXNG и Crawl4AI — без платы за API.

Это не означает, что все квоты можно бесконечно суммировать: у каждого сервиса свои ограничения и условия.

