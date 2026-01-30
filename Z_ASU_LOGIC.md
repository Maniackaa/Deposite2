# Документация логики Z-ASU

## Обзор

Z-ASU - это специальная логика для взаимодействия между двумя проектами:
- **Deposit** (Django, порт 8002) - депозитная система
- **Payment** (FastAPI/ASGI, порт 8000) - платежная система

Логика Z-ASU обеспечивает:
1. Автоматическое создание Payment в Payment системе при создании BirpayOrder в Deposit системе (при определенных условиях)
2. Пересылку SMS от агентов с опцией "Работает на Zajon" в систему депозит без автоматического подтверждения платежей

---

## Архитектура

### Проекты и сервисы

```
┌─────────────────┐         HTTP + JWT          ┌─────────────────┐
│   Deposit       │ ──────────────────────────> │    Payment      │
│   (Django)      │                              │   (FastAPI)     │
│   Port: 8002    │ <────────────────────────── │   Port: 8000    │
└─────────────────┘      SMS forwarding         └─────────────────┘
```

### Компоненты

1. **Deposit проект** (`H:\Dev\Freelance\Project-Deposite2\backend_deposit\`)
   - Создание BirpayOrder
   - Отправка запросов на Payment API
   - Прием пересылаемых SMS

2. **Payment проект** (`H:\Dev\Payment\backend_payment\`)
   - API endpoint для создания Payment (`/api/v2/z-asu/create-payment/`)
   - Обработка SMS от APK агентов
   - Пересылка SMS в Deposit систему

---

## 1. Создание Payment от BirpayOrder

### Условие активации

Логика Z-ASU активируется когда создается `BirpayOrder` с номером карты **`4111 1111 1111 1111`**.

### Процесс

1. **Создание BirpayOrder** (Deposit проект)
   - Через Celery задачу `process_birpay_order` (`deposit/tasks.py`)
   - Через ручное создание в админке `BirpayOrderCreateView` (`deposit/views.py`)

2. **Проверка условия** (`core/asu_pay_func.py`)
   ```python
   def should_send_to_z_asu(card_number: str) -> bool:
       """Проверяет что номер карты равен 4111 1111 1111 1111"""
       cleaned_card = card_number.replace(' ', '').replace('-', '')
       return cleaned_card == '4111111111111111'
   ```

3. **Отправка на Payment API** (`core/asu_pay_func.py`)
   ```python
   def send_birpay_order_to_z_asu(birpay_order) -> dict:
       """Отправляет данные BirpayOrder на /api/v2/z-asu/create-payment/"""
   ```

### Данные запроса

**Endpoint:** `POST {ASU_HOST}/api/v2/z-asu/create-payment/`

**Headers:**
```
Authorization: Bearer {JWT_TOKEN}
Content-Type: application/json
```

**Body:**
```json
{
    "birpay_order_id": 123,
    "birpay_id": 456,
    "merchant_transaction_id": "txn_12345",
    "merchant_user_id": "user_789",
    "amount": 100.00,
    "card_number": "4111111111111111",
    "currency_code": "AZN"
}
```

### Аутентификация

Используется JWT токен, полученный через `/api/v2/token/` с учетными данными Z-ASU:
- Логин: `Options.z_asu_login`
- Пароль: `Options.z_asu_password`

Токен хранится в файле `token_z_asu.txt` в корне проекта Deposit.

**Важно:** Доступ к Z-ASU API разрешен только для пользователя с `username='Z-ASU'`. Проверка выполняется через permission класс `IsZASUUser`.

---

## 2. Создание Payment в Payment системе

### API Endpoint

**URL:** `/api/v2/z-asu/create-payment/`  
**Method:** `POST`  
**Authentication:** JWT Token (required)

### Логика обработки

1. **Валидация данных** (`api/views.py`)
   - Проверка обязательных полей через `ZASUCreatePaymentSerializer`

2. **Поиск CreditCard**
   ```python
   credit_card = CreditCard.objects.filter(card_number=card_number).first()
   ```

3. **Поиск PayRequisite**
   ```python
   pay_requisite = PayRequisite.objects.filter(
       pay_type='p2p',  # Для card-to-card используются P2P реквизиты
       card=credit_card,
       is_active=True
   ).first()
   ```

4. **Поиск Wallet**
   ```python
   wallet = Wallet.objects.filter(
       pay_requisite=pay_requisite,
       is_active=True,
       is_archieve=False
   ).first()
   ```

5. **Поиск Merchant**
   ```python
   merchant = Merchant.objects.filter(name='Z-ASU').first()
   ```

6. **Создание Payment**
   ```python
   payment = Payment.objects.create(
       merchant=merchant,
       order_id=merchant_transaction_id,
       amount=amount,
       currency_code='AZN',
       pay_type='card-to-card',
       pay_requisite=pay_requisite,
       source='z_asu',  # Помечаем что это из Z-ASU API
       status=0  # Статус Created (без work_wallet)
   )
   ```

7. **Назначение Wallet и уменьшение баланса агента**
   ```python
   from payment.wallet_assignment_service import WalletAssignmentResult
   
   result = WalletAssignmentResult(selected_wallet=wallet)
   result.apply_to_payment(payment, skip_work_wallet=False)
   ```
   
   Метод `apply_to_payment` выполняет:
   - Назначает `work_wallet` на Payment
   - Обновляет статус Payment на `4` (Assigned)
   - Вызывает `deduct_manual_agent_balance` для уменьшения баланса агента (для manual/p2p агентов)
   - Обеспечивает единообразную логику с `wallet_assignment_service`

### WebSocket уведомления

После создания Payment отправляются уведомления через Celery:
- `notify_apk_new_payment.delay(payment_id)` - для APK/APK_MANUAL клиентов
- `notify_p2p_deposite_mode_update.delay(apk_p2p_user_id)` - для P2P кошельков

### Ответы API

**Успех (201 Created):**
```json
{
    "payment_id": "4792fc56-6ab9-4b6e-ad8a-f0ca23fc2a63",
    "status": "success"
}
```

**Ошибка (400 Bad Request):**
```json
{
    "errors": {
        "card_number": ["CreditCard с номером 4111111111111111 не найден"]
    }
}
```

---

## 3. Подтверждение и отклонение Payment

### Подтверждение Payment по payment_id

**Endpoint:** `POST /api/v2/z-asu/confirm-payment/`

**Body:**
```json
{
    "payment_id": "4792fc56-6ab9-4b6e-ad8a-f0ca23fc2a63"
}
```

**Ответ (200 OK):**
```json
{
    "payment_id": "4792fc56-6ab9-4b6e-ad8a-f0ca23fc2a63",
    "status": "confirmed",
    "message": "Payment успешно подтвержден"
}
```

### Отклонение Payment по payment_id

**Endpoint:** `POST /api/v2/z-asu/decline-payment/`

**Body:**
```json
{
    "payment_id": "4792fc56-6ab9-4b6e-ad8a-f0ca23fc2a63"
}
```

**Ответ (200 OK):**
```json
{
    "payment_id": "4792fc56-6ab9-4b6e-ad8a-f0ca23fc2a63",
    "status": "declined",
    "message": "Payment успешно отклонен"
}
```

### Подтверждение транзакции по merchant_transaction_id

**Endpoint:** `POST /api/v2/z-asu/confirm-transaction/`

**Body:**
```json
{
    "merchant_transaction_id": "txn_12345"
}
```

**Логика:**
- Endpoint ищет Payment через ORM по `order_id=merchant_transaction_id` и `source='z_asu'`
- Если найден, переводит в статус 9 (Confirmed)
- Сохраняет без `update_fields` для срабатывания всех сигналов

**Ответ (200 OK):**
```json
{
    "payment_id": "4792fc56-6ab9-4b6e-ad8a-f0ca23fc2a63",
    "status": "confirmed",
    "message": "Транзакция успешно подтверждена"
}
```

**Ошибка (404 Not Found):**
```json
{
    "errors": {
        "merchant_transaction_id": ["Payment с order_id=txn_12345 и source=z_asu не найден"]
    }
}
```

---

## 4. Подтверждение заявки в birpay_panel с апрувом на ASU

### Логика подтверждения

При подтверждении заявки с привязкой к Incoming на странице `birpay_panel`:

1. **Проверка DEBUG режима:**
   - Если `DEBUG=True`, запрос на `approve_birpay_refill` не отправляется
   - Считается успешным автоматически

2. **Подтверждение в birpay:**
   - Вызывается `approve_birpay_refill(pk=order.birpay_id)`
   - Если успешно (HTTP 200), заявка подтверждается

3. **Логика Z-ASU - подтверждение на ASU:**
   - После успешного подтверждения в birpay проверяется `should_send_to_z_asu(order.card_number)`
   - Если это Z-ASU заказ (карта `4111 1111 1111 1111`), вызывается `confirm_z_asu_transaction(order.merchant_transaction_id)`
   - Endpoint `/api/v2/z-asu/confirm-transaction/` ищет Payment через ORM и подтверждает его

4. **Сообщение пользователю:**
   - Добавляется информация об успешности апрува на ASU:
     - " успешно апрувнуто на ASU (Payment {payment_id})" - если успешно
     - " ошибка апрува на ASU: {error}" - если ошибка

### Функция подтверждения транзакции

**Файл:** `core/asu_pay_func.py`  
**Функция:** `confirm_z_asu_transaction(merchant_transaction_id: str) -> dict`

**Процесс:**
1. Отправка POST запроса на `/api/v2/z-asu/confirm-transaction/`
2. Endpoint ищет Payment через ORM по `order_id` и `source='z_asu'`
3. Подтверждает Payment (статус 9)

---

## 5. Пересылка SMS для агентов "Работает на Zajon"

### Опция агента

В профиле агента (`Profile`) есть опция в `agent_data['works_on_zajon']`:
- Если `True` - все распознанные SMS от этого агента пересылаются в систему депозит
- Если `False` - обычная логика подтверждения платежей по SMS

### Настройка опции

1. **В админке Django** (`users/admin.py`)
   - Поле `works_on_zajon` в форме `ProfileForm`
   - Отображается в секции "Опции агента"

2. **На странице агента** (`payment/agent_detail.html`)
   - Чекбокс "Работает на Zajon"
   - Сохраняется через `handle_settings_update` в `AgentDetail` view

### Логика обработки SMS

**Файл:** `deposit/models.py`  
**Функция:** `process_card_to_card_sms_from_apk_agent`

1. **Получение профиля агента**
   ```python
   agent_profiles = Profile.objects.filter(
       apk_accounts=sms_user,
       user__role='agent'
   )
   agent_profile = agent_profiles.first()
   ```

2. **Проверка опции**
   ```python
   works_on_zajon = False
   if agent_profile and isinstance(agent_profile.agent_data, dict):
       works_on_zajon = agent_profile.agent_data.get('works_on_zajon', False)
   ```

3. **Пересылка SMS** (если опция включена)
   ```python
   if works_on_zajon:
       sms_text = instance.sms_message.raw_text
       duplicate_sms_to_deposit(
           sms_text=sms_text,
           sms_message_instance=instance.sms_message,
           payment_id=None
       )
       return  # Не подтверждаем платеж автоматически
   ```

### Функция пересылки SMS

**Файл:** `deposit/models.py`  
**Функция:** `duplicate_sms_to_deposit`

**Параметры:**
- `sms_text` - Raw текст SMS (без обработки)
- `sms_message_instance` - Экземпляр SmsMessage
- `payment_id` - ID Payment (опционально, для логирования)

**Процесс:**
1. Получение `DEPOSIT_HOST` из settings
2. Подготовка данных для отправки:
   ```python
   sms_data = {
       'message': sms_text,  # Raw текст без обработки
       'id': str(sms_message_instance.id),
       'imei': sms_message_instance.device_id,
       'worker': 'zajon_agent'
   }
   ```
3. HTTP POST запрос на `{DEPOSIT_HOST}/sms/`
4. Retry механизм (3 попытки, backoff_factor=1)

**Важно:** SMS пересылается **независимо** от того, найден платеж для подтверждения или нет.

---

## 6. Конфигурация

### Переменные окружения

**Deposit проект:**
- `ASU_HOST` - URL Payment системы (например, `http://localhost:8000`)

**Payment проект:**
- `DEPOSIT_HOST` - URL Deposit системы (например, `http://localhost:8002`)

### Настройки в базе данных

**Deposit проект - Options (Singleton):**
- `z_asu_login` - Логин для Z-ASU аккаунта
- `z_asu_password` - Пароль для Z-ASU аккаунта

**Payment проект - Profile.agent_data:**
- `works_on_zajon` - Опция для агентов (boolean)

**Payment проект - Merchant:**
- Должен существовать Merchant с `name='Z-ASU'`

---

## 7. API Документация

### Swagger документация

Z-ASU API имеет отдельную Swagger документацию:

- **Schema:** `/api/v2/z-asu/schema/`
- **Swagger UI:** `/api/v2/z-asu/docs/`
- **ReDoc:** `/api/v2/z-asu/redoc/`

### Endpoints

| Endpoint | Method | Описание |
|----------|--------|----------|
| `/api/v2/z-asu/create-payment/` | POST | Создание Payment для Z-ASU |
| `/api/v2/z-asu/confirm-payment/` | POST | Подтверждение Payment по payment_id (статус 9) |
| `/api/v2/z-asu/decline-payment/` | POST | Отклонение Payment по payment_id (статус -1) |
| `/api/v2/z-asu/confirm-transaction/` | POST | Подтверждение транзакции по merchant_transaction_id (ищет Payment через ORM) |

---

## 8. Файлы и компоненты

### Deposit проект

**Файлы:**
- `core/asu_pay_func.py` - Функции для работы с Z-ASU API
  - `should_send_to_z_asu()` - Проверка условия
  - `send_birpay_order_to_z_asu()` - Отправка на Payment API
  - `confirm_z_asu_transaction()` - Подтверждение транзакции по merchant_transaction_id
  - `ASUAccountManager` - Менеджер аккаунтов (ASU и Z-ASU)

- `deposit/tasks.py` - Celery задачи
  - `process_birpay_order()` - Обработка BirpayOrder (вызывает Z-ASU логику)

- `deposit/views.py` - Django views
  - `BirpayOrderCreateView` - Ручное создание BirpayOrder (вызывает Z-ASU логику)
  - `BirpayPanelView.post()` - Подтверждение заявки с апрувом на ASU (Z-ASU логика)

- `users/models.py` - Модели
  - `Options` - Singleton модель с `z_asu_login` и `z_asu_password`

- `users/admin.py` - Админка
  - `OptionsAdmin` - Админка для Options с полями Z-ASU

### Payment проект

**Файлы:**
- `api/views.py` - API views
  - `z_asu_create_payment()` - Endpoint для создания Payment
  - `z_asu_confirm_payment()` - Endpoint для подтверждения Payment по payment_id
  - `z_asu_decline_payment()` - Endpoint для отклонения Payment по payment_id
  - `z_asu_confirm_transaction()` - Endpoint для подтверждения транзакции по merchant_transaction_id
  - `ZASUCreatePaymentSerializer` - Сериализатор запроса создания Payment
  - `ZASUPaymentActionSerializer` - Сериализатор для подтверждения/отклонения по payment_id
  - `ZASUConfirmTransactionSerializer` - Сериализатор для подтверждения по merchant_transaction_id

- `api/permissions.py` - Permission классы
  - `IsZASUUser` - Проверка доступа для пользователя с username='Z-ASU'

- `payment/wallet_assignment_service.py` - Сервис назначения кошельков
  - `WalletAssignmentResult.apply_to_payment()` - Централизованная логика назначения кошелька и уменьшения баланса агента

- `api/urls_z_asu.py` - URL конфигурация для Z-ASU API

- `deposit/models.py` - Модели и сигналы
  - `duplicate_sms_to_deposit()` - Функция пересылки SMS
  - `process_card_to_card_sms_from_apk_agent()` - Обработка SMS от APK агентов

- `users/models.py` - Модели
  - `Profile.agent_data` - JSONField с опцией `works_on_zajon`

- `users/admin.py` - Админка
  - `ProfileAdmin` - Админка для Profile с полем `works_on_zajon`

- `payment/views.py` - Views
  - `AgentDetail.handle_settings_update()` - Обработка сохранения опции агента

- `templates/payment/agent_detail.html` - Шаблон страницы агента
  - Чекбокс "Работает на Zajon"

- `backend_payment/urls.py` - Главная URL конфигурация
  - Включение `api.urls_z_asu`
  - Swagger документация для Z-ASU

- `backend_payment/excluded_path.py` - Preprocessing hooks для Swagger
  - `custom_preprocessing_hook_z_asu()` - Фильтрация Z-ASU endpoints

---

## 9. Логирование

Все операции логируются с пометкой `z_asu` или `zajon_agent`:

**Deposit проект:**
- `log.bind(account_type='z_asu')` - для операций с Z-ASU аккаунтом
- `logger.bind(birpay_order_id=..., z_asu=True)` - для отправки BirpayOrder

**Payment проект:**
- `logger.bind(z_asu_api=True)` - для API операций
- `logger.bind(works_on_zajon=True)` - для пересылки SMS

---

## 10. Обработка ошибок

### Ошибки при создании Payment

1. **CreditCard не найден**
   - HTTP 400: `{"errors": {"card_number": ["CreditCard с номером ... не найден"]}}`

2. **PayRequisite не найден**
   - HTTP 400: `{"errors": {"card_number": ["PayRequisite с pay_type=p2p ... не найден"]}}`

3. **Wallet не найден**
   - HTTP 400: `{"errors": {"card_number": ["Wallet с card_number=... не найден"]}}`

4. **Merchant не найден**
   - HTTP 400: `{"errors": {"merchant": ["Не найден Merchant с именем 'Z-ASU'"]}}`

### Ошибки при пересылке SMS

1. **DEPOSIT_HOST не настроен**
   - Логируется ошибка, SMS не пересылается

2. **HTTP ошибка при отправке**
   - Retry механизм (3 попытки)
   - Логируется ошибка с деталями

---

## 11. Тестирование

### Тестовая карта

Для активации логики Z-ASU используется тестовая карта:
- **Номер:** `4111 1111 1111 1111` (с пробелами или без)

### Проверка работы

1. **Создание BirpayOrder с тестовой картой**
   - В админке Deposit проекта
   - Проверить логи на отправку запроса на Payment API

2. **Проверка создания Payment**
   - В админке Payment проекта
   - Payment должен иметь:
     - `merchant.name = 'Z-ASU'`
     - `source = 'z_asu'`
     - `pay_type = 'card-to-card'`
     - `work_wallet` назначен принудительно

3. **Проверка пересылки SMS**
   - Включить опцию "Работает на Zajon" для агента
   - Отправить SMS от APK агента
   - Проверить логи на пересылку в Deposit систему
   - Проверить `/trash/` в Deposit системе на наличие пересланной SMS

---

## 12. Важные замечания

1. **Токены JWT**
   - Токены для ASU и Z-ASU хранятся отдельно (`token_asu.txt` и `token_z_asu.txt`)
   - Токены автоматически обновляются при истечении

2. **Принудительное назначение Wallet**
   - Для Z-ASU Payment пропускается обычный поиск `work_wallet`
   - Wallet назначается напрямую через `PayRequisite` и `CreditCard`
   - Используется `WalletAssignmentResult.apply_to_payment()` для единообразной логики с `wallet_assignment_service`
   - Это обеспечивает автоматическое уменьшение баланса агента при назначении Payment

3. **Raw текст SMS**
   - При пересылке SMS используется **raw текст** без обработки
   - Это просто дублирование исходного SMS текста

4. **Отдельная Swagger документация**
   - Z-ASU API имеет свою документацию, не пересекающуюся с основной

5. **Опция агента**
   - Опция `works_on_zajon` хранится в `agent_data` (JSONField), а не как отдельное поле модели
   - Это позволяет расширять настройки агента без миграций

6. **Уменьшение баланса агента**
   - При назначении Payment через Z-ASU API баланс агента уменьшается автоматически
   - Используется та же логика, что и в `wallet_assignment_service` через `WalletAssignmentResult.apply_to_payment()`
   - Это обеспечивает консистентность между ручным назначением и назначением через Z-ASU API

---

---

## 13. Permission классы

### IsZASUUser

**Файл:** `api/permissions.py`

Проверяет, что пользователь имеет `username='Z-ASU'`. Используется во всех Z-ASU API endpoints для ограничения доступа.

**Использование:**
```python
@permission_classes([IsAuthenticated, IsZASUUser])
def z_asu_create_payment(request):
    ...
```

При попытке доступа от пользователя с другим username возвращается HTTP 403 Forbidden.

---

## Версия документа

**Дата создания:** 2026-01-30  
**Последнее обновление:** 2026-01-30

**Изменения:**
- Добавлена проверка доступа через permission класс `IsZASUUser`
- Добавлены endpoints для подтверждения и отклонения Payment
- Добавлен endpoint для подтверждения транзакции по merchant_transaction_id
- Добавлена логика подтверждения на ASU после успешного подтверждения в birpay_panel
- Добавлена проверка DEBUG режима для `approve_birpay_refill`
- Интегрирован `WalletAssignmentResult.apply_to_payment()` для единообразной логики назначения кошелька и уменьшения баланса агента
