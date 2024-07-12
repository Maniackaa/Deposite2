import asyncio
import datetime
import time

import structlog
from celery import shared_task

from django.conf import settings


from core.global_func import send_message_tg
from deposit.models import *
from django.apps import apps
User = get_user_model()

logger = structlog.get_logger('tasks')


def find_time_between_good_screen(last_good_screen_time) -> int:
    """Находит время сколько прошло с момента прихода распознанного скрина в секундах"""
    now = datetime.datetime.now()
    delta = int((now - last_good_screen_time).total_seconds())
    logger.debug(f'Последний хороший скрин приходил {delta} секунд назад')
    return delta


def do_if_macros_broken():
    """Действие если макрос сдох"""
    try:
        send_message_tg('Макрос не активен более 15 секунд', settings.ALARM_IDS)
        Message = apps.get_model('deposit', 'Message')
        Message.objects.create(title='Макрос не активен',
                               text=f'Макрос не активен',
                               type='macros',
                               author=User.objects.get(username='Admin'))
    except Exception as err:
        logger.error(f'Ошибка если макрос сдох: {err}')


@shared_task(priority=1)
def check_macros():
    """Функция проверки работоспособности макроса"""
    Setting = apps.get_model('deposit', 'Setting')
    logger.info('Проверка макроса')
    now = datetime.datetime.now()
    last_good_screen_time_obj, _ = Setting.objects.get_or_create(name='last_good_screen_time')
    if last_good_screen_time_obj.value:
        last_good_screen_time = datetime.datetime.fromisoformat(last_good_screen_time_obj.value)
    else:
        last_good_screen_time = now
        last_good_screen_time_obj.value = now.isoformat()
        last_good_screen_time_obj.save()

    last_message_time_obj, _ = Setting.objects.get_or_create(name='last_message_time')
    if last_message_time_obj.value:
        last_message_time = datetime.datetime.fromisoformat(last_message_time_obj.value)
    else:
        last_message_time = datetime.datetime(2000, 1, 1)
    delta = find_time_between_good_screen(last_good_screen_time)
    logger.debug(f'last_message_time: {last_message_time}\nlast_good_screen_time: {last_good_screen_time}\ndelta:{delta}')
    if last_message_time < last_good_screen_time and delta > 15:
        logger.info(f'Время больше 10')
        do_if_macros_broken()
        last_message_time_obj.value = datetime.datetime.now().isoformat()
        last_message_time_obj.save()
        return True


@shared_task(priority=1)
def check_incoming(pk, count=0):
    """Функция проверки incoming в birpay"""
    try:
        logger.info(f'Проверка опера. Попытка {count+1}')
        check = None
        IncomingCheck = apps.get_model('deposit', 'IncomingCheck')
        incoming_check = IncomingCheck.objects.get(pk=pk)
        logger.info(f'incoming_check: {incoming_check}')
        try:
            check = find_birpay_from_id(birpay_id=incoming_check.birpay_id)
        except Exception as err:
            count += 1
            logger.debug(f'Неудачных попыток: {count}')
            if count < 5:
                check_incoming.apply_async(kwargs={'pk': pk, 'count': count}, countdown=count * 15)
                return

        logger.info(f'check result {count}: {check}')
        msg = ''
        if check:
            pay_birpay = check.get('pay')
            operator = check.get('operator')
            if operator:
                operator = operator.get('username')
            status = check.get('status')

            incoming_check.pay_birpay = pay_birpay
            incoming_check.operator = operator
            incoming_check.status = status
            incoming_check.save()
            pay_incoming = incoming_check.incoming.pay
            text_incoming = f'Проверка платежа {incoming_check.incoming.id} на сумму {pay_incoming} azn.\nCheck №{pk} birpay_id: {incoming_check.birpay_id}:\n'

            if status == 0:
                if pay_incoming == pay_birpay:
                    # Не подтвержден. Сумма равна
                    msg = f'{text_incoming}<b>Статус 0</b>'
                elif pay_birpay < pay_incoming:
                    # Не подтвержден. Пришло больше чем нужно
                    msg = f'{text_incoming}<b>Статус 0. Пришло {pay_incoming} azn вместо {pay_birpay} azn (на {pay_incoming - pay_birpay} больше)</b>'
                else:
                    # Не подтвержден. Пришло меньше чем нужно
                    msg = f'{text_incoming}<b>Статус 0. Пришло {pay_incoming} azn вместо {pay_birpay} azn (на {pay_birpay - pay_incoming} меньше)</b>'
            elif status == -1:
                # Пришло больше
                if pay_incoming > pay_birpay:
                    msg = f'{text_incoming}<b>Статус -1. Лишние {pay_incoming - pay_birpay} azn<>'
            elif status == 1:
                # Подтвержден
                if pay_birpay > pay_incoming:
                    # Пришло меньше
                    msg = f'{text_incoming}<b>Статус 1. Не хватает {pay_birpay - pay_incoming} azn {operator}</b>'
                elif pay_birpay < pay_incoming:
                    # Пришло больше
                    msg = f'{text_incoming}<b>Статус 1. Лишние {pay_incoming - pay_birpay} azn {operator}</b>'
            else:
                msg = f'{text_incoming}<b>Неизвестный статус {status}</b>'

        else:
            msg = (
                f'<b>Ничего не найдено</b> при проверке birpay {pk}\n'
                f'({incoming_check.birpay_id})\n'
                f'Платеж {incoming_check.incoming.id} на сумму {incoming_check.incoming.pay} azn'
            )
        send_message_tg(msg, settings.ALARM_IDS)
        return check
    except Exception as err:
        logger.error(f'Ошибка при проверке birpay {pk}: {err}')
        send_message_tg(f'Ошибка при проверке birpay {pk}: {err}', settings.ALARM_IDS)


@shared_task(priority=1)
def test_task(pk, count=0):
    try:
        logger.info('test_task2')
        send_message_tg(f'test_task2: {pk} попытка {count+1}')
        raise ValueError('xxx')

    except Exception as err:
        logger.error(f'test_task erorr {count}: {err}')
        if count < 5:
            count += 1
            test_task.apply_async(kwargs={'pk': 100, 'count': count}, countdown=3)