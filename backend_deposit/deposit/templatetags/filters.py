import datetime
from decimal import Decimal

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def get_bound_field(form, field_name):
    try:
        return form[field_name]
    except Exception:
        return ''

@register.filter
def get_value_from_dict(dict_data, key):
    if not dict_data:
        return ''
    return dict_data.get(key, '')

@register.filter
def get_item(dict_data, key):
    try:
        return dict_data.get(key, '')
    except (AttributeError, TypeError):
        return ''

@register.filter(name='addstr')
def addstr(value, arg):
    return str(value) + str(arg)

@register.filter
def InList(value, list_):
  return value in list_.split(',')

@register.filter
def NotInList(value, list_):
  return value not in list_.split(',')

@register.filter
def thousandsep(value):
    """
    Преобразует число в формат "2 147 919.08".
    Если value = 0 или None, вернёт пустую строку.
    """
    if not value:
        # Если None или 0, возвращаем пустую строку
        return ""
    d = Decimal(value).quantize(Decimal("0.01"))
    # Преобразуем в строку с двумя знаками после запятой
    s = f"{d:.2f}"  # например, "2147919.08"
    int_part, dec_part = s.split(".")  # ["2147919", "08"]

    # Разворачиваем целую часть, чтобы группировать по 3 символа
    rev = int_part[::-1]
    chunks = [rev[i:i+3] for i in range(0, len(rev), 3)]
    # Соединяем chunks пробелами и разворачиваем обратно
    spaced_int_part = " ".join(chunks)[::-1]

    return f"{spaced_int_part}.{dec_part}"

@register.filter
def subtract(value, arg):
    """Вычитает arg из value."""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return ''  # Или какое-то значение по умолчанию

@register.filter(name='add_class')
def add_class(field, css_class):
    return field.as_widget(attrs={"class": css_class})

@register.filter
def ms_to_datetime(value):
    """
    Конвертирует миллисекунды (int или str) в datetime в текущей таймзоне.
    """
    try:
        ts = int(value) / 1000.0
        # из timestamp в datetime (aware)
        dt = datetime.datetime.fromtimestamp(ts, tz=timezone.get_current_timezone())
        return dt
    except Exception:
        return ''

@register.filter
def time_diff(ms, create_at):
    """
    Принимает два аргумента:
    - ms: timestamp в миллисекундах
    - create_at: datetime
    Возвращает разницу (create_at - converted ms) в секундах, округлённую до целого.
    """
    try:
        dt = ms_to_datetime(ms)
        if dt is None or create_at is None:
            return ''
        diff = create_at - dt
        return round(diff.total_seconds(), 1)

    except Exception:
        return ''


@register.filter
def as_bin(value, bits=5):
    try:
        value = format(int(value), f'0{bits}b')
        return value[::-1]
    except Exception:
        return ''


@register.filter
def join_newline(cards):
    """ Превращает список в многострочную строку с реальными переводами строки """
    return '\n'.join(cards)

@register.filter
def is_pdf(url):
    return str(url).lower().endswith('.pdf')