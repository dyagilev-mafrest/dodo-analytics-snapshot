# Dodo IS Public API — OpenAPI specs

Официальные Swagger/OpenAPI-спецификации Dodo IS Public API (получены из Telegram,
2026-08-28). Дают полный документированный список эндпоинтов, параметров и схем
ответов — точнее и полнее, чем ручной перебор через `discovery.py`.

## Файлы

- `dodo-is-api.yaml` — основной API (доставка, производство, учёт, заказы, финансы,
  команда, заведения). База: `https://api.dodois.io/dodopizza/ru/`.
- `accounting-api.yaml` — расширенный учётный API (приходы, списания, поставщики,
  остатки, себестоимость).
- `controlling-api.yaml` — рейтинги клиентского опыта и стандартов (`Проверки`,
  `/ratings/customer-experience/*`, `/ratings/standards/*`) — для блока «Качество».
- `ratings-api.yaml` — отзывы клиентов + **официальный LFL** (`/lfl/by-units`,
  `/lfl/by-countries`).
- `auth-api.yaml` — роли, OAuth-скоупы, фиды удалений/обновлений.
- `staff-api.yaml`, `franchisee-api.yaml`, `inventory-api.yaml`, `iot-api.yaml`,
  `label-printer-api.yaml`, `marketplace-api.yaml`, `pospayments.yaml` — периферийные
  домены (найм, франшиза, ревизии склада, IoT-устройства, принтер этикеток, магазин
  приложений, POS-платежи) — низкий приоритет для аналитики.

## Точки роста, найденные при первом просмотре (2026-08-28)

- **`GET /delivery/delivery-sectors`** (`dodo-is-api.yaml`) — до сих пор не
  использовался. Отдаёт `sectorId` (настоящий UUID, не только имя), `isDeleted`,
  `isStopped`, `isSubSector`, `geometry` по каждому сектору доставки. Решает
  проблему хрупкого джойна `stop_events.entity_name` ↔ `hourly_revenue_by_sector`
  по строке имени — см. [[project_dodo_stops_lost_revenue]] в памяти.
- **`GET /lfl/by-units`, `/lfl/by-countries`** (`ratings-api.yaml`) — официальный LFL
  от Dodo IS, можно сверить/заменить наш самостоятельный расчёт.
- **`/ratings/customer-experience/*`, `/ratings/standards/*`** (`controlling-api.yaml`)
  — полный набор для недоделанного блока «Качество» (детализация, история,
  нарушения, апелляции) — см. [[project_dodo_quality_api_endpoints]].
- **`GET/PATCH /units/month-goals`** (`dodo-is-api.yaml`) — официальные месячные
  цели по пиццерии, потенциально может заменить ручной ввод из Google-таблицы плана
  руководства.
- **`/production/unit-workload-by-orders`, `/production/unit-workload-by-products`,
  `/staff/schedules/forecast`** — под ещё не сделанные разделы «Производительность
  кухни» / штатное расписание.
