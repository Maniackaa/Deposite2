# Birpay API Депозита (для ASU)

Документация REST API модуля `deposit/views_birpay_api.py`. Этот API предоставляет проекту ASU (Payment) доступ к Birpay **только через Депозит**: ASU не обращается к Birpay напрямую.

- **Базовый URL:** `{DEPOSIT_HOST}/api/birpay/`
- **Аутентификация:** только JWT пользователя Депозита (staff/superuser). JWT получается через `POST {DEPOSIT_HOST}/api/token/` с логином и паролем из **SupportOptions на ASU** (Deposit API: логин, Deposit API: пароль). Учётные данные залогиненного пользователя не используются — только SupportOptions. Заголовок: `Authorization: Bearer <JWT>`.

---

## Классы и эндпоинты

### Permission: BirpayAPIPermission

- **Доступ разрешён**, если пользователь аутентифицирован по JWT (или сессии Депозита) и является **staff** или **superuser**.
- На Депозите в DRF включён `JWTAuthentication`: Bearer JWT проверяется (подпись, срок), из токена берётся пользователь и подставляется в `request.user`.
- Иначе доступ запрещён (403).

---

### Реквизиты

| Класс | Метод | URL | Описание |
|-------|--------|-----|----------|
| BirpayRequisitesListAPIView | GET | `/api/birpay/requisites/` | Список реквизитов Birpay с полями из БД: `card_number`, `raw_card_number`, `works_on_asu` |
| BirpayRequisiteUpdateAPIView | PUT | `/api/birpay/requisites/<id>/` | Обновить реквизит. Body: изменяемые поля + опционально `changed_by_user_id`, `changed_by_username` (агент ASU). Данные готовит сервис из модели RequsiteZajon; изменения пишутся в лог с данными агента |
| BirpayRequisiteSetActiveAPIView | POST | `/api/birpay/requisites/<id>/set-active/` | Включить/выключить реквизит. Body: `{"active": true\|false}`; опционально `changed_by_user_id`, `changed_by_username` (агент ASU) |

#### GET /api/birpay/requisites/

- **Ответ:** массив объектов реквизитов. Каждый объект содержит поля от Birpay плюс из БД Депозита:
  - `id` — ID реквизита в Birpay (и в RequsiteZajon)
  - `card_number` — нормализованный номер карты (из БД)
  - `raw_card_number` — сырой номер карты (из `payload` или БД)
  - `works_on_asu` — флаг «Работает на ASU» (из RequsiteZajon)
- При ошибке Birpay: `502 Bad Gateway`, тело `{"error": "..."}`.

#### PUT /api/birpay/requisites/<id>/

- **Body (JSON):** изменяемые поля и опционально данные агента ASU:
  - `card_number` — сырой номер карты (валидация: минимум 16 цифр, Luhn)
  - `name`, `agent_id`, `weight`, `active` — при необходимости
  - `changed_by_user_id` (опционально) — ID пользователя/агента на ASU, который внёс изменение (для лога)
  - `changed_by_username` (опционально) — логин пользователя/агента на ASU (для лога)
- Полный payload для Birpay собирается на Депозите из модели RequsiteZajon и переданных полей. `agent_id=0` или `None` не переопределяют значение — берётся из модели. После успеха Birpay обновляется локальная модель и пишется лог изменения с данными агента (если переданы).
- **Ответ при успехе:** `200 OK`, тело — ответ Birpay (например `{"success": true, ...}`).
- **Ошибки:**
  - `400 Bad Request` — ошибка валидации (например номер карты): `{"error": "..."}`.
  - `404 Not Found` — реквизит с таким `id` не найден в модели: `{"error": "Реквизит <id> не найден в модели."}`.
  - `502 Bad Gateway` — ошибка Birpay или сервера: `{"error": "...", ...}`.

#### POST /api/birpay/requisites/<id>/set-active/

- **Body (JSON):** `{"active": true}` или `{"active": false}`; опционально `changed_by_user_id`, `changed_by_username` (агент ASU — для лога изменений).
- **Ответ:** `200 OK` при успехе (тело — ответ сервиса/Birpay); при ошибке — `404` (реквизит не найден) или `502` (ошибка Birpay). После успеха обновляется локальная модель и пишется лог с данными агента.

---

### Refill (пополнение)

| Класс | Метод | URL | Описание |
|-------|--------|-----|----------|
| BirpayRefillOrdersListAPIView | GET | `/api/birpay/refill-orders/?limit=512` | Список заявок на пополнение |
| BirpayRefillOrderFindAPIView | GET | `/api/birpay/refill-orders/find/?merchant_transaction_id=` | Найти заявку по merchant_transaction_id |
| BirpayRefillOrderAmountAPIView | PUT | `/api/birpay/refill-orders/<id>/amount/` | Изменить сумму заявки. Body: `{"amount": 100.0}` |
| BirpayRefillOrderApproveAPIView | PUT | `/api/birpay/refill-orders/<id>/approve/` | Подтвердить заявку на пополнение |

- **GET refill-orders/find:** обязательный query-параметр `merchant_transaction_id`. При отсутствии — `400`. Если заявка не найдена — `404` с `{"detail": "Not found"}`.
- **PUT refill-orders/<id>/amount/:** обязательное поле `amount` (число). При отсутствии — `400`.
- При ошибках Birpay все refill-эндпоинты возвращают `502` с `{"error": "..."}`.

---

### Payout (выплаты)

| Класс | Метод | URL | Описание |
|-------|--------|-----|----------|
| BirpayPayoutOrdersListAPIView | GET | `/api/birpay/payout-orders/?limit=512&status=0` | Список заявок на выплату (status — через запятую) |
| BirpayPayoutOrderFindAPIView | GET | `/api/birpay/payout-orders/find/?merchant_transaction_id=` | Найти заявку выплаты по merchant_transaction_id |
| BirpayPayoutOrderApproveAPIView | PUT | `/api/birpay/payout-orders/<id>/approve/` | Подтвердить выплату. Body: `{"operator_transaction_id": "..."}` |
| BirpayPayoutOrderDeclineAPIView | PUT | `/api/birpay/payout-orders/<id>/decline/` | Отклонить выплату. Body: `{"reason": "err"}` (опционально) |

- **GET payout-orders/find:** обязательный query-параметр `merchant_transaction_id`. При отсутствии — `400`. Если не найдена — `404` с `{"detail": "Not found"}`.
- **PUT approve:** обязательное поле `operator_transaction_id`. При отсутствии — `400`.
- **PUT decline:** поле `reason` опционально (по умолчанию `"err"`).
- При ошибках Birpay — `502` с `{"error": "..."}`.

---

## Связь с другими компонентами

- **Обновление реквизита** (PUT requisites, POST set-active) выполняется через единый сервис `deposit/birpay_requisite_service.update_requisite_on_birpay(requisite_id, overrides)`. Форма Депозита, Z-ASU форма и этот API не дублируют логику — только передают ID и изменяемые поля.
- **BirpayClient** — низкоуровневый клиент к Birpay (`core/birpay_client.py`). Все запросы к Birpay идут через него.
- **Модель RequsiteZajon** — источник данных по реквизитам (card_number, works_on_asu, agent_id, refill_method_types, users и т.д.) при обновлении.

---

## Файлы

| Файл | Назначение |
|------|------------|
| `deposit/views_birpay_api.py` | Все классы API (permission, requisites, refill, payout) |
| `deposit/urls_birpay_api.py` | Маршруты с префиксом `/api/birpay/` |
| `deposit/birpay_requisite_service.py` | Единый сервис обновления реквизита в Birpay |

---

**Версия:** 2026-01-30
