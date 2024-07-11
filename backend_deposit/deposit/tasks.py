import datetime

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
def check_incoming(pk):
    """Функция проверки incoming в birpay"""
    try:
        logger.info('Проверка опера')
        IncomingCheck = apps.get_model('deposit', 'IncomingCheck')
        incoming_check = IncomingCheck.objects.get(pk=pk)
        logger.info(f'incoming_check: {incoming_check}')
        check = find_birpay_from_id(birpay_id=incoming_check.birpay_id)
        logger.info(f'check result: {check}')
        if check:
            pay_birpay = check.get('pay')
            operator = check.get('operator').get('username')
            incoming_check.pay_birpay = pay_birpay
            incoming_check.operator = operator
            incoming_check.save()
            if pay_birpay != incoming_check.incoming.pay:
                msg = (
                    f'Заявка {incoming_check.incoming.id} ({incoming_check.birpay_id})\n'
                    f'{incoming_check.incoming.pay} azn\n'
                    f'Платеж: {pay_birpay} azn\n'
                    f'Разница: {incoming_check.incoming.pay - pay_birpay} azn'
                )
                send_message_tg(msg, settings.ALARM_IDS)
        else:
            send_message_tg(f'Ошибка при проверке birpay {pk}: ошибка при получении данных', settings.ALARM_IDS)

    except Exception as err:
        logger.error(f'Ошибка при проверке birpay {pk}: {err}')
        send_message_tg(f'Ошибка при проверке birpay {pk}: {err}', settings.ALARM_IDS)


# @shared_task(priority=1)
# def test_task():
#     try:
#         logger.info('test_task2')
#         send_message_tg('test_task2')
#         do_if_macros_broken()
#     except Exception as err:
#         logger.error(f'test_task erorr: {err}')