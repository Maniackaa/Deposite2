import datetime
import logging
import time
from copy import copy
from pathlib import Path

import cv2
import numpy as np
import pytesseract
import requests
from celery import shared_task, group, chunks
from celery.utils.log import get_task_logger
from django.conf import settings
from django.contrib.auth import get_user_model

from backend_deposit.settings import LOGGING
from core.global_func import send_message_tg
from deposit.models import Message, Setting
from ocr.models import ScreenResponse, ScreenResponsePart
from ocr.screen_response import screen_text_to_pay

User = get_user_model()
logger = get_task_logger(__name__)

# logger = logging.getLogger('celery')


def find_time_between_good_screen(last_good_screen_time) -> int:
    """Находит время сколько прошло с момента прихода распознанного скрина в секундах"""
    now = datetime.datetime.now()
    delta = int((now - last_good_screen_time).total_seconds())
    logger.info(f'Последний хороший скрин приходил {delta} секунд назад')
    return delta


def do_if_macros_broken():
    """Действие если макрос сдох"""
    Message.objects.create(title='Макрос не активен',
                           text=f'Макрос не активен',
                           type='macros',
                           author=User.objects.get(username='Admin'))
    send_message_tg('Макрос не активен более 15 секунд', settings.ALARM_IDS)


@shared_task(priority=1)
def check_macros():
    """Функция проверки работоспособности макроса"""
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
    if last_message_time < last_good_screen_time and delta > 15:
        logger.info(f'Время больше 10')
        do_if_macros_broken()
        last_message_time_obj.value = datetime.datetime.now().isoformat()
        last_message_time_obj.save()
        return True
