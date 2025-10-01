# Тесты для BirpayPanelView

## Описание

Данный набор тестов проверяет функциональность формы вставки SMS ID в `BirpayPanelView`. Тесты покрывают:

- Успешную привязку SMS к заказу
- Валидацию формы
- Обработку ошибок
- Атомарность транзакций
- Интеграцию с внешним API BirPay

## Структура тестов

### 1. `test_birpay_panel_sms_form.py`
Основные unit-тесты для формы вставки SMS ID:

- `test_successful_sms_binding()` - успешная привязка SMS
- `test_sms_not_found_error()` - ошибка когда SMS не найден
- `test_sms_already_occupied_error()` - ошибка когда SMS уже занят
- `test_amount_mismatch_error()` - ошибка несовпадения сумм
- `test_birpay_api_error()` - ошибка API BirPay
- `test_empty_sms_id_error()` - ошибка пустого SMS ID
- `test_transaction_atomicity()` - атомарность транзакции
- `test_form_validation_with_invalid_data()` - валидация невалидных данных
- `test_multiple_orders_processing()` - обработка нескольких заказов

### 2. `test_birpay_panel_integration.py`
Интеграционные тесты:

- `test_birpay_panel_url_accessibility()` - доступность URL
- `test_birpay_panel_template_rendering()` - рендеринг шаблона
- `test_birpay_panel_with_filters()` - работа с фильтрами
- `test_full_sms_binding_workflow()` - полный рабочий процесс
- `test_birpay_panel_pagination()` - пагинация
- `test_birpay_panel_context_data()` - контекстные данные
- `test_birpay_panel_staff_permission()` - права доступа
- `test_birpay_panel_with_last_confirmed_order()` - последний подтвержденный заказ

### 3. `test_utils.py`
Утилиты для тестирования:

- `BirpayTestDataFactory` - фабрика тестовых данных
- `BirpayAPIMockHelper` - помощник для мокирования API
- `BirpayAssertionHelper` - помощник для проверок

## Запуск тестов

### Запуск всех тестов модуля deposit:
```bash
python manage.py test deposit
```

### Запуск конкретного тестового файла:
```bash
python manage.py test deposit.test_birpay_panel_sms_form
python manage.py test deposit.test_birpay_panel_integration
```

### Запуск конкретного теста:
```bash
python manage.py test deposit.test_birpay_panel_sms_form.BirpayPanelSMSFormTest.test_successful_sms_binding
```

### Запуск с подробным выводом:
```bash
python manage.py test deposit --verbosity=2
```

## Мокирование внешних API

Тесты используют моки для внешних вызовов к API BirPay:

- `approve_birpay_refill()` - подтверждение заявки
- `change_amount_birpay()` - изменение суммы заявки

Моки настраиваются в `BirpayAPIMockHelper` и возвращают предопределенные ответы.

## Тестовые данные

Тестовые данные создаются с помощью `BirpayTestDataFactory`:

- Пользователи с профилями
- Заказы BirPay
- Входящие SMS
- Полные тестовые сценарии

## Проверки

`BirpayAssertionHelper` предоставляет методы для проверки:

- Успешной привязки SMS
- Неудачной привязки SMS
- Сообщений об ошибках в ответе

## Требования

- Django 3.2+
- Python 3.8+
- Все зависимости из requirements.txt

## Примечания

- Тесты используют транзакции для изоляции
- Внешние API полностью замокированы
- Тесты проверяют как успешные, так и ошибочные сценарии
- Покрыты все основные пути выполнения кода
