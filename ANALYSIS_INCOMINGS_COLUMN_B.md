# Анализ столбца "Б" в таблице incomings

## Описание столбца "Б"

Столбец "Б" (Баланс) отображает информацию о балансе карты получателя и проверяет корректность данных.

---

## Логика отображения

### Шаблоны

**Файлы:**
- `backend_deposit/templates/deposit/incomings_list.html` (строки 100-106)
- `backend_deposit/templates/deposit/incomings_list_stat.html` (строки 89-95)

**Код шаблона:**
```django
<td>
    {% if incoming.balance %}
        {{ incoming.balance|floatformat:0  }}
        {% if incoming.balance %} 
            (<span {% if incoming.check_balance|floatformat:0 != incoming.balance|floatformat:0 %}
                style="color: #C20000" 
            {% endif %}>
                {{ incoming.prev_balance|floatformat:0 }}
            </span>)
        {% endif %}
    {% elif incoming.image %}
        <a target="_blank" href="/media/{{ incoming.image }}">Чек</a>
    {% endif %}
</td>
```

### Логика отображения:

1. **Если есть `balance`:**
   - Показывает текущий баланс (`incoming.balance`) без дробной части
   - В скобках показывает предыдущий баланс (`incoming.prev_balance`)
   - Если текущий баланс **НЕ совпадает** с расчетным (`check_balance`), то предыдущий баланс отображается **красным цветом** (#C20000)

2. **Если нет `balance`, но есть `image`:**
   - Показывает ссылку "Чек" на изображение

3. **Если нет ни `balance`, ни `image`:**
   - Столбец пустой

---

## Вычисление данных

### Источник данных

Данные вычисляются в представлениях (`views.py`) через SQL оконные функции (Window Functions) с использованием `LAG()`.

### Файлы с вычислениями:

1. **`incoming_list()`** (строки 347-452)
2. **`IncomingEmpty.get_queryset()`** (строки 455-474)
3. **`IncomingFiltered.get_queryset()`** (строки 512-548)

### SQL запросы

#### Вариант 1: Raw SQL (для операторов base2 и не-base2)

```sql
SELECT *,
LAG(balance, -1) OVER (
    PARTITION BY deposit_incoming.recipient 
    ORDER BY response_date DESC, balance DESC, deposit_incoming.id DESC
) as prev_balance,
LAG(balance, -1) OVER (
    PARTITION BY deposit_incoming.recipient 
    ORDER BY response_date DESC, balance DESC, deposit_incoming.id DESC
) + pay as check_balance
FROM deposit_incoming 
LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
WHERE worker = 'base2'  -- или worker != 'base2' or worker is NULL
ORDER BY deposit_incoming.id DESC 
LIMIT 5000;
```

#### Вариант 2: Django ORM (для IncomingEmpty)

```python
empty_incoming = Incoming.objects.filter(...).annotate(
    prev_balance=Window(
        expression=Lag('balance', 1), 
        partition_by=[F('recipient')], 
        order_by=['response_date', 'balance', 'id']
    ),
    check_balance=F('pay') + Window(
        expression=Lag('balance', 1), 
        partition_by=[F('recipient')], 
        order_by=['response_date', 'balance', 'id']
    ),
)
```

---

## Вычисляемые поля

### 1. `prev_balance` (Предыдущий баланс)

**Формула:**
```sql
LAG(balance, -1) OVER (
    PARTITION BY recipient 
    ORDER BY response_date DESC, balance DESC, id DESC
)
```

**Логика:**
- Берет баланс из **предыдущей записи** для того же получателя (`recipient`)
- Группировка: по `recipient` (PARTITION BY)
- Сортировка: `response_date DESC`, `balance DESC`, `id DESC`
- `LAG(balance, -1)` означает: взять значение `balance` из строки, которая идет **перед** текущей в отсортированном порядке

**Пример:**
Если есть записи для получателя "1234":
- Запись 1: id=100, response_date=2024-01-03, balance=5000
- Запись 2: id=99, response_date=2024-01-02, balance=3000
- Запись 3: id=98, response_date=2024-01-01, balance=1000

Для записи 1: `prev_balance` = 3000 (из записи 2)
Для записи 2: `prev_balance` = 1000 (из записи 3)
Для записи 3: `prev_balance` = NULL (нет предыдущей записи)

### 2. `check_balance` (Расчетный баланс)

**Формула:**
```sql
prev_balance + pay
```

**Логика:**
- Расчетный баланс = предыдущий баланс + текущий платеж
- Используется для проверки корректности текущего баланса

**Пример:**
- `prev_balance` = 3000
- `pay` = 500
- `check_balance` = 3000 + 500 = 3500

### 3. Проверка корректности

**Условие для красного цвета:**
```django
{% if incoming.check_balance|floatformat:0 != incoming.balance|floatformat:0 %}
```

**Логика:**
- Если расчетный баланс (`check_balance`) **НЕ равен** фактическому балансу (`balance`), то предыдущий баланс отображается красным цветом
- Это указывает на **расхождение** между ожидаемым и фактическим балансом

---

## Примеры отображения

### Пример 1: Корректный баланс
- Текущий баланс: 5000
- Предыдущий баланс: 3000
- Платеж: 2000
- Расчетный баланс: 3000 + 2000 = 5000 ✅

**Отображение:** `5000 (3000)` - черным цветом

### Пример 2: Некорректный баланс
- Текущий баланс: 5100
- Предыдущий баланс: 3000
- Платеж: 2000
- Расчетный баланс: 3000 + 2000 = 5000 ❌

**Отображение:** `5100 (<span style="color: #C20000">3000</span>)` - предыдущий баланс красным цветом

### Пример 3: Нет баланса, но есть чек
**Отображение:** `<a href="/media/...">Чек</a>`

### Пример 4: Нет баланса и чека
**Отображение:** пусто

---

## Особенности реализации

### 1. Сортировка для LAG()

Сортировка по трем полям:
- `response_date DESC` - сначала более поздние даты
- `balance DESC` - при одинаковой дате, больше баланс
- `id DESC` - при одинаковой дате и балансе, больше ID

**Важно:** `LAG(balance, -1)` с offset `-1` означает, что берется значение из **предыдущей** строки в отсортированном порядке.

### 2. Группировка по получателю

`PARTITION BY recipient` означает, что расчеты выполняются **отдельно для каждого получателя** (карты).

### 3. Ограничение выборки

В `incoming_list()` используется `LIMIT 5000` для оптимизации производительности.

### 4. Разные запросы для разных пользователей

- **Операторы base2:** `WHERE worker = 'base2'`
- **Операторы не-base2:** `WHERE worker != 'base2' or worker is NULL`
- **Support/Superuser:** все записи (с CTE для оптимизации)

---

## Поля модели Incoming

### Используемые поля:

- `balance` (FloatField) - текущий баланс карты получателя
- `pay` (FloatField) - сумма платежа
- `recipient` (CharField) - получатель (номер карты)
- `response_date` (DateTimeField) - распознанное время платежа
- `image` (ImageField) - изображение чека
- `id` (IntegerField) - первичный ключ

### Вычисляемые поля (не сохраняются в БД):

- `prev_balance` - предыдущий баланс (вычисляется через LAG)
- `check_balance` - расчетный баланс (prev_balance + pay)

---

## Назначение столбца "Б"

1. **Отображение баланса:** показывает текущий баланс карты получателя
2. **История баланса:** показывает предыдущий баланс в скобках
3. **Валидация данных:** красным цветом выделяет случаи, когда текущий баланс не соответствует расчетному (предыдущий баланс + платеж)
4. **Альтернативный контент:** если баланса нет, показывает ссылку на чек (если есть изображение)

---

## Потенциальные проблемы

### 1. Порядок сортировки

Сортировка по `response_date DESC` может не соответствовать реальному порядку поступлений, если:
- Время распознавания (`response_date`) отличается от времени фактического поступления
- Есть задержки в обработке SMS

### 2. NULL значения

Если `prev_balance` = NULL (первая запись для получателя), то:
- `check_balance` = NULL + pay = NULL
- Проверка `check_balance != balance` не сработает корректно

### 3. Округление

Использование `floatformat:0` может скрывать небольшие расхождения из-за округления.

### 4. Производительность

Оконные функции с `PARTITION BY` и `ORDER BY` могут быть медленными на больших объемах данных.

---

## Рекомендации

1. **Обработка NULL:** добавить проверку на NULL перед сравнением `check_balance` и `balance`
2. **Логирование расхождений:** логировать случаи, когда `check_balance != balance` для анализа
3. **Индексы:** убедиться, что есть индексы на `recipient`, `response_date`, `balance` для оптимизации оконных функций
4. **Документация:** добавить комментарии в код о логике вычисления `prev_balance` и `check_balance`

