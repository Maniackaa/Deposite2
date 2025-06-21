import datetime

import structlog
from django.apps import apps

logger = structlog.get_logger('deposit')

def find_possible_incomings(order_amount, target_time_value, delta_before=2, delta_after=2):
    # Ищет свободные смс с суммой и дельтой по времени
    min_time = target_time_value - datetime.timedelta(minutes=delta_before)
    max_time = target_time_value + datetime.timedelta(minutes=delta_after)
    logger.info(f'Ищем смс пришедшие {min_time} - {max_time}')

    Incoming = apps.get_model('deposit', 'Incoming')
    incomings = Incoming.objects.filter(
        pay=order_amount,
        register_date__gte=min_time, register_date__lte=max_time,
        birpay_id__isnull=True,
    )
    logger.info(f'Найдены смс: {incomings}')
    return incomings