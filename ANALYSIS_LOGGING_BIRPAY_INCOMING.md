# Анализ и улучшение логирования моделей Incoming и BirpayOrder

## Проблема

При работе с моделями `Incoming` и `BirpayOrder` не было достаточного контекстного логирования для отслеживания истории работы с заказами. В частности:

1. В `process_birpay_order` устанавливался только `birpay_id`, но не `merchant_transaction_id` и `order.id`
2. В `birpay_panel` устанавливался только `merchant_transaction_id`, но не `birpay_id` и `order.id`
3. Контекст не очищался после завершения работы с заказом, что приводило к попаданию контекста в другие задачи (например, в `check_cards_activity`)
4. При связывании `Incoming` с `BirpayOrder` не устанавливался контекст для отслеживания операций

## Решение

Добавлено контекстное логирование со всеми тремя идентификаторами:
- `birpay_id` - первичный id в birpay
- `merchant_transaction_id` - идентификатор транзакции мерчанта
- `birpay_order_id` - внутренний id записи в БД (order.id)

### Изменения в `process_birpay_order` (tasks.py)

**До:**
```python
def process_birpay_order(data):
    birpay_id = data['id']
    bind_contextvars(birpay_id=birpay_id)
    # ...
    order, created = BirpayOrder.objects.get_or_create(birpay_id=birpay_id, defaults=order_data)
```

**После:**
```python
def process_birpay_order(data):
    birpay_id = data['id']
    merchant_transaction_id = data['merchantTransactionId']
    # Устанавливаем контекст с birpay_id и merchant_transaction_id до получения/создания заказа
    bind_contextvars(birpay_id=birpay_id, merchant_transaction_id=merchant_transaction_id)
    # ...
    order, created = BirpayOrder.objects.get_or_create(birpay_id=birpay_id, defaults=order_data)
    # Обновляем контекст с order.id после получения/создания заказа
    bind_contextvars(birpay_id=birpay_id, merchant_transaction_id=merchant_transaction_id, birpay_order_id=order.id)
```

### Изменения в `birpay_panel` (views.py)

**До:**
```python
def post(self, request, *args, **kwargs):
    # ...
    for name, value in post_data.items():
        if name.startswith('orderconfirm'):
            order = BirpayOrder.objects.get(pk=order_id)
            bind_contextvars(merchant_transaction_id=order.merchant_transaction_id)
    # ...
    return HttpResponseRedirect(f"{request.path}?{query_string}")
```

**После:**
```python
def post(self, request, *args, **kwargs):
    # Очищаем контекст в начале обработки POST запроса
    clear_contextvars()
    # ...
    for name, value in post_data.items():
        if name.startswith('orderconfirm'):
            order = BirpayOrder.objects.get(pk=order_id)
            # Устанавливаем контекст со всеми идентификаторами BirpayOrder
            bind_contextvars(
                birpay_id=order.birpay_id,
                merchant_transaction_id=order.merchant_transaction_id,
                birpay_order_id=order.id
            )
    # ...
    # Очищаем контекст после завершения обработки заказа
    clear_contextvars()
    return HttpResponseRedirect(f"{request.path}?{query_string}")
except Exception as e:
    # Очищаем контекст при ошибке
    clear_contextvars()
```

### Изменения в `send_image_to_gpt_task` (tasks.py)

**До:**
```python
@shared_task(bind=True, max_retries=2)
def send_image_to_gpt_task(self, birpay_id):
    bind_contextvars(birpay_id=birpay_id)
    # ...
    order = BirpayOrder.objects.get(birpay_id=birpay_id)
    bind_contextvars(merchant_transaction_id=order.merchant_transaction_id, birpay_id=birpay_id)
```

**После:**
```python
@shared_task(bind=True, max_retries=2)
def send_image_to_gpt_task(self, birpay_id):
    # Устанавливаем контекст с birpay_id до получения заказа
    bind_contextvars(birpay_id=birpay_id)
    # ...
    order = BirpayOrder.objects.get(birpay_id=birpay_id)
    # Обновляем контекст со всеми идентификаторами после получения заказа
    bind_contextvars(
        birpay_id=birpay_id,
        merchant_transaction_id=order.merchant_transaction_id,
        birpay_order_id=order.id
    )
    # ...
    finally:
        # Очищаем контекст после завершения обработки заказа
        clear_contextvars()
```

### Изменения в `check_cards_activity` (tasks.py)

**До:**
```python
@shared_task(priority=2, time_limit=30)
def check_cards_activity():
    """Периодическая задача для проверки активности карт"""
    try:
        logger.info('Запуск проверки активности карт')
        clear_contextvars()
```

**После:**
```python
@shared_task(priority=2, time_limit=30)
def check_cards_activity():
    """Периодическая задача для проверки активности карт"""
    try:
        # Очищаем контекст в начале задачи, чтобы не попадали данные из предыдущих задач
        clear_contextvars()
        logger.info('Запуск проверки активности карт')
```

### Изменения в `check_incoming` (tasks.py)

**До:**
```python
@shared_task(bind=True, priority=1, time_limit=15, max_retries=5)
def check_incoming(self, pk, count=0):
    """Функция проверки incoming в birpay"""
    check = {}
    try:
        logger.info(f'Проверка опера. Попытка {count + 1}')
        IncomingCheck = apps.get_model('deposit', 'IncomingCheck')
        incoming_check = IncomingCheck.objects.get(pk=pk)
        check = find_birpay_from_id(birpay_id=incoming_check.birpay_id)
```

**После:**
```python
@shared_task(bind=True, priority=1, time_limit=15, max_retries=5)
def check_incoming(self, pk, count=0):
    """Функция проверки incoming в birpay"""
    check = {}
    try:
        # Очищаем контекст в начале задачи
        clear_contextvars()
        logger.info(f'Проверка опера. Попытка {count + 1}')
        IncomingCheck = apps.get_model('deposit', 'IncomingCheck')
        incoming_check = IncomingCheck.objects.get(pk=pk)
        # Устанавливаем контекст с birpay_id из incoming_check
        bind_contextvars(birpay_id=incoming_check.birpay_id)
        # Если есть связанный BirpayOrder, добавляем его идентификаторы в контекст
        if incoming_check.incoming and incoming_check.incoming.birpay_id:
            BirpayOrder = apps.get_model('deposit', 'BirpayOrder')
            try:
                order = BirpayOrder.objects.filter(merchant_transaction_id=incoming_check.incoming.birpay_id).first()
                if order:
                    bind_contextvars(
                        birpay_id=order.birpay_id,
                        merchant_transaction_id=order.merchant_transaction_id,
                        birpay_order_id=order.id
                    )
            except Exception:
                pass
        check = find_birpay_from_id(birpay_id=incoming_check.birpay_id)
```

### Изменения в `download_birpay_check_file` (tasks.py)

**До:**
```python
@shared_task(bind=True, max_retries=3, default_retry_delay=1, priority=2, soft_time_limit=20)
def download_birpay_check_file(self, order_id, check_file_url):
    from deposit.models import BirpayOrder
    try:
        order = BirpayOrder.objects.get(id=order_id)
```

**После:**
```python
@shared_task(bind=True, max_retries=3, default_retry_delay=1, priority=2, soft_time_limit=20)
def download_birpay_check_file(self, order_id, check_file_url):
    from deposit.models import BirpayOrder
    try:
        # Очищаем контекст в начале задачи
        clear_contextvars()
        order = BirpayOrder.objects.get(id=order_id)
        # Устанавливаем контекст со всеми идентификаторами BirpayOrder
        bind_contextvars(
            birpay_id=order.birpay_id,
            merchant_transaction_id=order.merchant_transaction_id,
            birpay_order_id=order.id
        )
```

### Изменения в местах связывания Incoming с BirpayOrder

#### В `incoming_list` (views.py)

**До:**
```python
if value and value.strip():
    order = BirpayOrder.objects.filter(merchant_transaction_id=value.strip()).first()
    if order:
        order.incoming = incoming
        order.save(update_fields=['incoming', 'confirmed_time'])
```

**После:**
```python
if value and value.strip():
    order = BirpayOrder.objects.filter(merchant_transaction_id=value.strip()).first()
    if order:
        # Устанавливаем контекст со всеми идентификаторами BirpayOrder при связывании
        bind_contextvars(
            birpay_id=order.birpay_id,
            merchant_transaction_id=order.merchant_transaction_id,
            birpay_order_id=order.id
        )
        order.incoming = incoming
        order.save(update_fields=['incoming', 'confirmed_time'])
        logger.info(f'Привязан BirpayOrder {order.merchant_transaction_id} к Incoming {incoming.id} в incoming_list')
        # Очищаем контекст после связывания
        clear_contextvars()
```

#### В `IncomingEdit.form_valid` (views.py)

**До:**
```python
if old_birpay_id:
    old_order = BirpayOrder.objects.filter(merchant_transaction_id=old_birpay_id).first()
    if old_order and old_order.incoming and old_order.incoming.pk == incoming.pk:
        old_order.incoming = None
        old_order.save(update_fields=['incoming'])
        logger.info(f'Отвязан старый BirpayOrder {old_birpay_id} от Incoming {incoming.id}')

if new_birpay_id:
    new_order = BirpayOrder.objects.filter(merchant_transaction_id=new_birpay_id).first()
    if new_order:
        new_order.incoming = incoming
        new_order.save(update_fields=['incoming', 'confirmed_time'])
        logger.info(f'Привязан новый BirpayOrder {new_birpay_id} к Incoming {incoming.id}')
```

**После:**
```python
if old_birpay_id:
    old_order = BirpayOrder.objects.filter(merchant_transaction_id=old_birpay_id).first()
    if old_order and old_order.incoming and old_order.incoming.pk == incoming.pk:
        # Устанавливаем контекст со всеми идентификаторами BirpayOrder при отвязывании
        bind_contextvars(
            birpay_id=old_order.birpay_id,
            merchant_transaction_id=old_order.merchant_transaction_id,
            birpay_order_id=old_order.id
        )
        old_order.incoming = None
        old_order.save(update_fields=['incoming'])
        logger.info(f'Отвязан старый BirpayOrder {old_birpay_id} от Incoming {incoming.id}')
        # Очищаем контекст после отвязывания
        clear_contextvars()

if new_birpay_id:
    new_order = BirpayOrder.objects.filter(merchant_transaction_id=new_birpay_id).first()
    if new_order:
        # Устанавливаем контекст со всеми идентификаторами BirpayOrder при связывании
        bind_contextvars(
            birpay_id=new_order.birpay_id,
            merchant_transaction_id=new_order.merchant_transaction_id,
            birpay_order_id=new_order.id
        )
        new_order.incoming = incoming
        new_order.save(update_fields=['incoming', 'confirmed_time'])
        logger.info(f'Привязан новый BirpayOrder {new_birpay_id} к Incoming {incoming.id}')
        # Очищаем контекст после связывания
        clear_contextvars()
```

## Результат

Теперь при каждом взаимодействии с моделью `BirpayOrder` в логах будет присутствовать полный контекст:

- `birpay_id` - первичный id в birpay
- `merchant_transaction_id` - идентификатор транзакции мерчанта
- `birpay_order_id` - внутренний id записи в БД

Это позволяет:

1. **Отслеживать полную историю работы с заказом** - зная любой из идентификаторов (`birpay_id`, `merchant_transaction_id`, `birpay_order_id`), можно найти все логи, связанные с этим заказом

2. **Избежать попадания контекста в другие задачи** - контекст очищается после завершения работы с заказом, что предотвращает попадание идентификаторов в задачи, которые не связаны с этим заказом (например, `check_cards_activity`)

3. **Отслеживать связывание Incoming с BirpayOrder** - при каждой операции связывания/отвязывания устанавливается контекст, что позволяет видеть полную историю связей

## Примеры использования

### Поиск логов по birpay_id
```bash
grep "birpay_id=90094021" logs/*.log
```

### Поиск логов по merchant_transaction_id
```bash
grep "merchant_transaction_id=1016809298" logs/*.log
```

### Поиск логов по birpay_order_id
```bash
grep "birpay_order_id=12345" logs/*.log
```

Все три идентификатора будут присутствовать в логах одновременно, что позволяет использовать любой из них для поиска полной истории работы с заказом.
