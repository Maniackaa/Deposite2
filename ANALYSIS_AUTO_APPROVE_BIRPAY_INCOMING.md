# Анализ связи автоматического подтверждения BirpayOrder с Incoming

## Обзор

Система автоматического подтверждения `BirpayOrder` использует модель `Incoming` для поиска и привязки SMS-уведомлений о поступлении средств. Процесс происходит в задаче `send_image_to_gpt_task`, которая обрабатывает чеки через GPT и автоматически подтверждает заказы при выполнении всех условий.

## Архитектура связи

### Модели и их связи

#### BirpayOrder
- **Поле `incoming`**: `OneToOneField('Incoming', on_delete=SET_NULL, null=True, blank=True, related_name='birpay')`
  - Связь один-к-одному с `Incoming`
  - При удалении `Incoming` поле устанавливается в `NULL`
  - Обратная связь: `incoming.birpay` → `BirpayOrder`
  
- **Поле `incomingsms_id`**: `CharField(max_length=10, null=True, blank=True, unique=True)`
  - Хранит ID связанного `Incoming` в виде строки
  - Уникальное поле (один `Incoming` может быть привязан только к одному `BirpayOrder`)

- **Поле `confirmed_time`**: `DateTimeField(null=True, blank=True)`
  - Время подтверждения заказа
  - Устанавливается при автоматическом подтверждении

#### Incoming
- **Поле `birpay_id`**: `CharField(max_length=15, null=True, blank=True)`
  - Хранит `merchant_transaction_id` из `BirpayOrder`
  - Используется для обратной связи: найти `BirpayOrder` по `merchant_transaction_id`

- **Поле `recipient`**: `CharField(max_length=50, null=True, blank=True)`
  - Номер карты получателя (может быть в виде маски, например: `1234****5678`)
  - Используется для поиска подходящих SMS

- **Поле `pay`**: `FloatField(db_index=True)`
  - Сумма платежа
  - Используется для фильтрации подходящих SMS

- **Поле `register_date`**: `DateTimeField(auto_now_add=True)`
  - Время добавления записи в базу
  - Используется для фильтрации по времени

## Процесс автоматического подтверждения

### Задача: `send_image_to_gpt_task`

**Расположение**: `backend_deposit/deposit/tasks.py:496`

**Триггер**: Запускается после скачивания чека из Birpay (в задаче `download_birpay_check_file`)

**Процесс**:

1. **Распознавание чека через GPT**
   - Отправка изображения чека на сервер распознавания (`http://45.14.247.139:9000/recognize/`)
   - Получение данных: сумма, получатель, отправитель, время, статус

2. **Проверка условий через флаги GPT (GPTIMHO)**
   
   Система проверяет 7 условий (флагов):
   
   | Флаг | Условие | Описание |
   |------|---------|----------|
   | `gpt_status` | `gpt_status == 1` | Статус из GPT положительный |
   | `amount` | `gpt_amount == order_amount` | Сумма в чеке совпадает с суммой заказа |
   | `recipient` | `mask_compare(order.card_number, gpt_recipient)` | Маска карты получателя совпадает |
   | `time` | `now - 1h < gpt_time < now + 1h` | Время в чеке в пределах ±1 часа от текущего |
   | `sms` | Найдена ровно одна подходящая SMS | Однозначное соответствие SMS |
   | `min_orders` | `total_user_orders >= 5` | У пользователя минимум 5 заказов |
   | `user_reputation` | `user_order_percent >= 40%` | Процент подтвержденных заказов ≥ 40% |

3. **Поиск подходящих Incoming**

   **Функция**: `find_possible_incomings(order_amount, gpt_time_aware)`
   
   **Расположение**: `backend_deposit/deposit/func.py:8`
   
   **Логика поиска**:
   ```python
   Incoming.objects.filter(
       pay=order_amount,                    # Точное совпадение суммы
       register_date__gte=min_time,         # Не раньше чем за 2 минуты до времени чека
       register_date__lte=max_time,         # Не позже чем через 2 минуты после времени чека
       birpay_id__isnull=True,              # SMS еще не привязана к другому заказу
   )
   ```
   
   **Параметры времени**:
   - `delta_before = 2` минуты (по умолчанию)
   - `delta_after = 2` минуты (по умолчанию)
   - Окно поиска: ±2 минуты от времени, распознанного GPT из чека

4. **Фильтрация по маске карты**

   После получения списка SMS по сумме и времени, выполняется дополнительная фильтрация:
   
   ```python
   for incoming in incomings:
       sms_recipient = incoming.recipient
       recipient_is_correct = mask_compare(sms_recipient, gpt_recipient)
       if recipient_is_correct and order_amount == incoming.pay:
           incomings_with_correct_card_and_order_amount.append(incoming)
   ```
   
   **Функция `mask_compare`**: `backend_deposit/core/global_func.py:57`
   
   Сравнивает маски карт по видимым частям:
   - Берет цифры с начала до первого символа `*`, `•` или `.`
   - Берет цифры с конца до первого символа `*`, `•` или `.`
   - Сравнивает эти части
   
   **Пример**: 
   - `1234****5678` и `1234****5678` → совпадают
   - `1234****5678` и `1234****9999` → не совпадают (разные последние 4 цифры)

5. **Условие однозначности**

   Флаг `sms` устанавливается только если найдена **ровно одна** подходящая SMS:
   ```python
   if len(incomings_with_correct_card_and_order_amount) == 1:
       gpt_imho_result |= BirpayOrder.GPTIMHO.sms
   ```

6. **Автоматическое подтверждение**

   **Условия**:
   ```python
   if (not order.is_moshennik() and           # Не мошенник
       not order.is_painter() and              # Не художник
       gpt_auto_approve and                    # Включено автоматическое подтверждение
       order.gpt_flags == 127):                # Все 7 флагов установлены (127 = 0b1111111)
   ```
   
   **Действия при подтверждении**:
   1. Привязка `Incoming` к `BirpayOrder`:
      ```python
      incoming_sms = incomings_with_correct_card_and_order_amount[0]
      order.incomingsms_id = incoming_sms.id
      order.incoming = incoming_sms
      order.confirmed_time = timezone.now()
      ```
   
   2. Установка обратной связи в `Incoming`:
      ```python
      incoming_sms.birpay_id = order.merchant_transaction_id
      incoming_sms.save()
      ```
   
   3. Вызов API Birpay для подтверждения:
      ```python
      response = approve_birpay_refill(pk=order.birpay_id)
      ```
   
   4. Обработка ошибок:
      - Если статус ответа ≠ 200, отправляется уведомление в Telegram

## Критические моменты

### 1. Уникальность связи

- **Проблема**: Поле `incomingsms_id` имеет `unique=True`, что означает, что один `Incoming` может быть привязан только к одному `BirpayOrder`
- **Защита**: В функции `find_possible_incomings` используется фильтр `birpay_id__isnull=True`, который исключает уже привязанные SMS

### 2. Обратная связь через birpay_id

- **Прямая связь**: `BirpayOrder.incoming` → `Incoming` (OneToOneField)
- **Обратная связь**: `Incoming.birpay_id` → `BirpayOrder.merchant_transaction_id`
- **Использование**: Позволяет найти `BirpayOrder` по `merchant_transaction_id` из `Incoming`

### 3. Временное окно поиска

- **Окно**: ±2 минуты от времени, распознанного GPT из чека
- **Проблема**: Если SMS пришла вне этого окна, она не будет найдена автоматически
- **Решение**: Можно увеличить `delta_before` и `delta_after` в функции `find_possible_incomings`

### 4. Проверка маски карты

- **Важно**: Используется функция `mask_compare`, которая сравнивает только видимые части маски
- **Примеры**:
  - `1234****5678` и `1234****5678` → совпадают ✅
  - `1234****5678` и `1234****9999` → не совпадают ❌
  - `1234****5678` и `9999****5678` → не совпадают ❌

### 5. Однозначность соответствия

- **Требование**: Для автоматического подтверждения должна быть найдена **ровно одна** подходящая SMS
- **Если найдено 0**: Автоматическое подтверждение не происходит, требуется ручное подтверждение
- **Если найдено > 1**: Автоматическое подтверждение не происходит (неоднозначность)

### 6. Проверка мошенников и художников

- **Мошенники**: Если `order.is_moshennik() == True`, автоматическое подтверждение не происходит
  - Дополнительно: SMS помечается комментарием и `birpay_id` очищается
- **Художники**: Если `order.is_painter() == True`, автоматическое подтверждение не происходит

## Схема потока данных

```
BirpayOrder (создан)
    ↓
download_birpay_check_file (скачивание чека)
    ↓
send_image_to_gpt_task (распознавание через GPT)
    ↓
Проверка 7 флагов GPT
    ↓
find_possible_incomings (поиск SMS по сумме и времени)
    ↓
Фильтрация по маске карты (mask_compare)
    ↓
Проверка однозначности (ровно 1 SMS)
    ↓
Проверка условий (не мошенник, не художник, gpt_auto_approve, все флаги = 127)
    ↓
Автоматическое подтверждение:
    - order.incoming = incoming_sms
    - order.incomingsms_id = incoming_sms.id
    - incoming_sms.birpay_id = order.merchant_transaction_id
    - approve_birpay_refill(order.birpay_id)
```

## Проблемы и рекомендации

### Проблема 1: Отсутствие обновления статуса после автоматического подтверждения

**Описание**: После успешного автоматического подтверждения статусы `status` и `status_internal` в `BirpayOrder` не обновляются локально.

**Текущее поведение**: 
- Вызывается `approve_birpay_refill(pk=order.birpay_id)` для подтверждения на сервере Birpay
- Но локальные статусы не обновляются

**Рекомендация**: 
```python
if response.status_code == 200:
    order.status = 1  # approved
    order.status_internal = 1  # approved
    update_fields.append("status")
    update_fields.append("status_internal")
```

### Проблема 2: Нет проверки на дубликаты при автоматическом подтверждении

**Описание**: Если несколько `BirpayOrder` имеют одинаковую сумму и время, они могут найти одну и ту же SMS.

**Текущая защита**: 
- Фильтр `birpay_id__isnull=True` в `find_possible_incomings`
- Но между проверкой и привязкой может произойти race condition

**Рекомендация**: 
- Использовать `select_for_update()` при поиске SMS
- Или проверять `birpay_id` непосредственно перед привязкой

### Проблема 3: Временное окно поиска может быть слишком узким

**Описание**: Окно ±2 минуты может быть недостаточным, если SMS приходит с задержкой.

**Рекомендация**: 
- Сделать параметры `delta_before` и `delta_after` настраиваемыми через `Options`
- Или увеличить окно до ±5 минут

### Проблема 4: Нет логирования при неудачном поиске SMS

**Описание**: Если SMS не найдена или найдено несколько, это логируется, но не отправляется уведомление.

**Рекомендация**: 
- Отправлять уведомление в Telegram, если автоматическое подтверждение не произошло из-за отсутствия подходящей SMS
- Особенно важно, если все остальные флаги установлены

### Проблема 5: Нет проверки на уже подтвержденный заказ

**Описание**: Задача может быть запущена повторно для уже подтвержденного заказа.

**Рекомендация**: 
- Проверять `order.incoming` перед началом обработки
- Если `order.incoming` уже установлен, пропускать автоматическое подтверждение

## Использование связи в других местах

### 1. Ручное подтверждение (`BirpayPanelView.post`)

**Расположение**: `backend_deposit/deposit/views.py:1621`

При ручном подтверждении оператором:
```python
order.incomingsms_id = incoming_id
order.incoming = incoming_to_approve
incoming_to_approve.birpay_id = order.merchant_transaction_id
```

### 2. Отображение в списке заказов (`BirpayOrderView`)

**Расположение**: `backend_deposit/deposit/views.py:1361`

Используется аннотация для отображения данных из связанного `Incoming`:
```python
qs = qs.annotate(
    incoming_pay=F('incoming__pay'),
    delta=ExpressionWrapper(F('incoming__pay') - F('amount'), output_field=FloatField()),
)
```

### 3. Команда синхронизации (`sms_to_birp`)

**Расположение**: `backend_deposit/deposit/management/commands/sms_to_birp.py`

Заполняет поле `incoming` в `BirpayOrder` на основе `incomingsms_id` или `birpay_id`:
```python
# По incomingsms_id
incoming = Incoming.objects.get(pk=order.incomingsms_id)
order.incoming = incoming

# По birpay_id
order = BirpayOrder.objects.get(merchant_transaction_id=incoming.birpay_id)
order.incoming = incoming
```

## Выводы

1. **Связь реализована через OneToOneField** между `BirpayOrder` и `Incoming`, что обеспечивает однозначное соответствие.

2. **Обратная связь** через `birpay_id` в `Incoming` позволяет находить `BirpayOrder` по `merchant_transaction_id`.

3. **Автоматическое подтверждение** происходит только при выполнении всех 7 условий (флагов GPT) и наличии ровно одной подходящей SMS.

4. **Поиск SMS** выполняется по сумме, времени (±2 минуты) и маске карты получателя.

5. **Критическая проблема**: После автоматического подтверждения локальные статусы не обновляются, что может привести к рассинхронизации данных.

6. **Рекомендация**: Добавить обновление статусов после успешного подтверждения и улучшить обработку ошибок.

