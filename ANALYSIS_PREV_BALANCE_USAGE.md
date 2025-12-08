# Анализ использования `prev_balance` в коде

## ⚠️ ВАЖНО: Использование только для отображения

**`prev_balance` НЕ используется в бизнес-логике!**

Поле `prev_balance` используется **ТОЛЬКО для визуального отображения** в шаблонах. Оно:
- ❌ НЕ используется в валидации данных
- ❌ НЕ используется в бизнес-логике
- ❌ НЕ используется в API
- ❌ НЕ используется в задачах Celery
- ❌ НЕ используется в сигналах Django
- ✅ Используется ТОЛЬКО для отображения в шаблонах (визуальная индикация)

Единственное использование `check_balance` (который вычисляется из `prev_balance`) - это сравнение с фактическим балансом в шаблоне для изменения цвета текста (красный цвет при расхождении).

---

## Места вычисления `prev_balance`

### 1. **`incoming_list()`** - `backend_deposit/deposit/views.py` (строки 347-452)

**Функция:** Основной список всех платежей

**Вычисление:** Через Raw SQL с оконными функциями

#### Вариант для операторов base2 (строки 390-401):
```python
incoming_q = Incoming.objects.raw("""
    SELECT *,
    LAG(balance, -1) OVER (
        PARTITION BY deposit_incoming.recipient 
        ORDER BY response_date DESC, balance DESC, deposit_incoming.id DESC
    ) as prev_balance,
    LAG(balance, -1) OVER (...) + pay as check_balance
    FROM deposit_incoming 
    LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    WHERE worker = 'base2'
    ORDER BY deposit_incoming.id DESC LIMIT 5000;
""")
```

#### Вариант для операторов не-base2 (строки 403-413):
```python
incoming_q = Incoming.objects.raw("""
    SELECT *,
    LAG(balance, -1) OVER (...) as prev_balance,
    LAG(balance, -1) OVER (...) + pay as check_balance
    FROM deposit_incoming 
    LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    WHERE worker != 'base2' or worker is NULL
    ORDER BY deposit_incoming.id DESC LIMIT 5000;
""")
```

#### Вариант для support/superuser (строки 415-432):
```python
incoming_q = Incoming.objects.raw("""
    WITH short_table AS (
        SELECT * FROM deposit_incoming ORDER BY id DESC LIMIT 5000
    )
    SELECT *,
    LAG(balance, -1) OVER (
        PARTITION BY short_table.recipient 
        ORDER BY response_date DESC, balance DESC, short_table.id DESC
    ) as prev_balance,
    LAG(balance, -1) OVER (...) + pay as check_balance
    FROM short_table 
    LEFT JOIN deposit_colorbank ON deposit_colorbank.name = short_table.sender
    ORDER BY short_table.id DESC LIMIT 5000;
""")
```

**Шаблон:** `deposit/incomings_list.html`

---

### 2. **`IncomingEmpty.get_queryset()`** - `backend_deposit/deposit/views.py` (строки 455-474)

**Класс:** Не подтвержденные платежи

**Вычисление:** Через Django ORM с Window функциями

```python
empty_incoming = Incoming.objects.filter(
    Q(birpay_id__isnull=True) | Q(birpay_id='')
).order_by('-response_date', '-id').annotate(
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
).order_by('-id').all()
```

**Шаблон:** `deposit/incomings_list.html`

---

### 3. **`IncomingFiltered.get_queryset()`** - `backend_deposit/deposit/views.py` (строки 512-548)

**Класс:** Отфильтрованные платежи по получателям из профиля пользователя

**Вычисление:** Через Raw SQL с оконными функциями

#### Вариант для операторов base2 (строки 526-535):
```python
filtered_incoming = Incoming.objects.raw("""
    SELECT *,
    LAG(balance, -1) OVER (
        PARTITION BY deposit_incoming.recipient 
        ORDER BY response_date DESC, balance DESC, deposit_incoming.id DESC
    ) as prev_balance,
    LAG(balance, -1) OVER (...) + pay as check_balance
    FROM deposit_incoming 
    LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    WHERE deposit_incoming.recipient = ANY(%s) 
        AND deposit_incoming.worker = 'base2'
    ORDER BY deposit_incoming.id DESC
""", [user_filter])
```

#### Вариант для операторов не-base2 (строки 536-545):
```python
filtered_incoming = Incoming.objects.raw("""
    SELECT *,
    LAG(balance, -1) OVER (...) as prev_balance,
    LAG(balance, -1) OVER (...) + pay as check_balance
    FROM deposit_incoming 
    LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    WHERE deposit_incoming.recipient = ANY(%s) 
        AND (deposit_incoming.worker != 'base2' OR deposit_incoming.worker IS NULL)
    ORDER BY deposit_incoming.id DESC
""", [user_filter])
```

**Шаблон:** `deposit/incomings_list.html`

---

## Места использования `prev_balance` в шаблонах

### 1. **`incomings_list.html`** - `backend_deposit/templates/deposit/incomings_list.html` (строка 102)

**Код:**
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

**Используется в представлениях:**
- `incoming_list()` ✅ (вычисляет `prev_balance`)
- `IncomingEmpty` ✅ (вычисляет `prev_balance`)
- `IncomingFiltered` ✅ (вычисляет `prev_balance`)
- `IncomingSearch` ❌ (НЕ вычисляет `prev_balance` - **потенциальная ошибка**)
- `IncomingMyCardsView` ❌ (НЕ вычисляет `prev_balance` - **потенциальная ошибка**)

---

### 2. **`incomings_list_stat.html`** - `backend_deposit/templates/deposit/incomings_list_stat.html` (строка 91)

**Код:**
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

**Используется в представлениях:**
- `IncomingStatSearchView` ❌ (НЕ вычисляет `prev_balance` - **потенциальная ошибка**)

---

## Проблемные места (НЕ вычисляют `prev_balance`, но используют шаблоны)

### 1. **`IncomingSearch`** - `backend_deposit/deposit/views.py` (строки 628-722)

**Проблема:** Использует шаблон `incomings_list.html`, который ожидает `prev_balance`, но не вычисляет его.

**Код get_queryset:**
```python
def get_queryset(self):
    # ... фильтрация ...
    return all_incoming  # Обычный queryset без аннотаций
```

**Риск:** В шаблоне будет ошибка при попытке доступа к `incoming.prev_balance`, если баланс есть.

---

### 2. **`IncomingMyCardsView`** - `backend_deposit/deposit/views.py` (строки 568-625)

**Проблема:** Использует шаблон `incomings_list.html`, который ожидает `prev_balance`, но не вычисляет его.

**Код get_queryset:**
```python
def get_queryset(self, *args, **kwargs):
    # ... фильтрация по картам ...
    return Incoming.objects.filter(id__in=matched_ids).order_by('-response_date')
    # Обычный queryset без аннотаций
```

**Риск:** В шаблоне будет ошибка при попытке доступа к `incoming.prev_balance`, если баланс есть.

---

### 3. **`IncomingStatSearchView`** - `backend_deposit/deposit/views.py` (строки 1325-1346)

**Проблема:** Использует шаблон `incomings_list_stat.html`, который ожидает `prev_balance`, но не вычисляет его.

**Код get_queryset:**
```python
def get_queryset(self):
    qs = super().get_queryset().order_by('-register_date')
    self.filterset = IncomingStatSearch(self.request.GET, queryset=qs)
    return self.filterset.qs  # Обычный queryset без аннотаций
```

**Риск:** В шаблоне будет ошибка при попытке доступа к `incoming.prev_balance`, если баланс есть.

---

## Сводная таблица использования

| Представление | Шаблон | Вычисляет `prev_balance`? | Статус |
|:--------------|:-------|:-------------------------:|:-------|
| `incoming_list()` | `incomings_list.html` | ✅ Да (Raw SQL) | ✅ OK |
| `IncomingEmpty` | `incomings_list.html` | ✅ Да (Django ORM) | ✅ OK |
| `IncomingFiltered` | `incomings_list.html` | ✅ Да (Raw SQL) | ✅ OK |
| `IncomingSearch` | `incomings_list.html` | ❌ Нет | ⚠️ **ПРОБЛЕМА** |
| `IncomingMyCardsView` | `incomings_list.html` | ❌ Нет | ⚠️ **ПРОБЛЕМА** |
| `IncomingStatSearchView` | `incomings_list_stat.html` | ❌ Нет | ⚠️ **ПРОБЛЕМА** |

---

## Рекомендации

### 1. **Исправить проблемные представления**

Добавить вычисление `prev_balance` и `check_balance` в:

#### `IncomingSearch.get_queryset()`:
```python
def get_queryset(self):
    # ... существующая логика фильтрации ...
    
    # Добавить аннотации перед возвратом
    all_incoming = all_incoming.annotate(
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
    
    return all_incoming
```

#### `IncomingMyCardsView.get_queryset()`:
```python
def get_queryset(self, *args, **kwargs):
    # ... существующая логика фильтрации ...
    
    matched_incoming = Incoming.objects.filter(id__in=matched_ids)
    
    # Добавить аннотации
    return matched_incoming.annotate(
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
    ).order_by('-response_date')
```

#### `IncomingStatSearchView.get_queryset()`:
```python
def get_queryset(self):
    qs = super().get_queryset().order_by('-register_date')
    self.filterset = IncomingStatSearch(self.request.GET, queryset=qs)
    
    # Добавить аннотации к отфильтрованному queryset
    return self.filterset.qs.annotate(
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

### 2. **Альтернативное решение: защита в шаблоне**

Добавить проверку на существование `prev_balance` в шаблонах:

```django
<td>
    {% if incoming.balance %}
        {{ incoming.balance|floatformat:0  }}
        {% if incoming.balance and incoming.prev_balance is not None %} 
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

### 3. **Создать базовый метод для вычисления**

Вынести логику вычисления в отдельный метод:

```python
@staticmethod
def annotate_balance_fields(queryset):
    """Добавляет prev_balance и check_balance к queryset"""
    return queryset.annotate(
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

Использовать во всех представлениях:
```python
def get_queryset(self):
    qs = Incoming.objects.filter(...)
    return IncomingEmpty.annotate_balance_fields(qs)
```

---

## Импорты для Window функций

Для использования Django ORM Window функций нужны импорты:

```python
from django.db.models import F, Window
from django.db.models.functions import Lag
```

---

## Использование в логике

### ❌ НЕ используется в бизнес-логике

Проверка показала, что `prev_balance` и `check_balance` **НЕ используются** в:

1. **Валидации данных:**
   - Нет проверок при сохранении `Incoming`
   - Нет валидации баланса на основе `prev_balance`
   - Нет ошибок при расхождении `check_balance` и `balance`

2. **Бизнес-логике:**
   - Нет автоматических действий при расхождении балансов
   - Нет уведомлений в систему при ошибках баланса
   - Нет блокировок операций при несоответствии

3. **API:**
   - Нет endpoints, которые используют `prev_balance`
   - Нет сериализаторов с этими полями

4. **Задачах Celery:**
   - `check_incoming()` проверяет только сумму платежа (`pay`), но не баланс
   - Нет задач для проверки корректности балансов

5. **Сигналах Django:**
   - `after_save_incoming()` не проверяет баланс
   - Нет обработки расхождений балансов

### ✅ Используется только для визуализации

**Единственное использование** - в шаблонах для визуальной индикации:

```django
{% if incoming.check_balance|floatformat:0 != incoming.balance|floatformat:0 %}
    style="color: #C20000"  <!-- Красный цвет при расхождении -->
{% endif %}
```

Это означает:
- Оператор видит красным цветом предыдущий баланс, если текущий баланс не соответствует расчетному
- Но система **НЕ блокирует** операции и **НЕ валидирует** данные автоматически
- Это только **визуальная подсказка** для оператора

---

## Примечания

1. **Raw SQL vs Django ORM:**
   - Raw SQL используется в `incoming_list()` и `IncomingFiltered` для производительности
   - Django ORM используется в `IncomingEmpty` для совместимости с фильтрами

2. **Сортировка:**
   - Все варианты используют одинаковую сортировку: `response_date DESC, balance DESC, id DESC`
   - Это важно для корректного вычисления предыдущего баланса

3. **Производительность:**
   - Оконные функции могут быть медленными на больших объемах данных
   - В `incoming_list()` используется `LIMIT 5000` для оптимизации

4. **Отсутствие валидации:**
   - Система вычисляет `prev_balance` и `check_balance`, но не использует их для валидации
   - Расхождения балансов обнаруживаются только визуально оператором
   - Нет автоматической обработки ошибок баланса

5. **Потенциальные улучшения:**
   - Можно добавить валидацию при сохранении `Incoming`
   - Можно добавить уведомления при расхождении балансов
   - Можно добавить логирование расхождений для анализа

