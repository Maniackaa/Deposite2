import datetime
import logging
import time
from copy import copy
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from celery import shared_task, group
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
    send_message_tg('Макрос не активен более 10 секунд', settings.ALARM_IDS)


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
    if last_message_time < last_good_screen_time and delta > 10:
        logger.info(f'Время больше 10')
        do_if_macros_broken()
        last_message_time_obj.value = datetime.datetime.now().isoformat()
        last_message_time_obj.save()
        return True


# @shared_task
# def add_response_part_to_queue(screen_id: int, pairs: list):
#     """Задача для отправки пар в очередь на распознавание"""
#     logger.info(f'add_response_part_to_queue {screen_id}')
#     for pair in pairs:
#         create_response_part.delay(screen_id, pair[0], pair[1])
#     logger.info(f'add_response_part_to_queue END')


@shared_task(priority=2)
def add_response_part_to_queue(screen_id: int, pairs: list):
    """Задача для отправки пар в очередь на распознавание"""
    logger.info(f'Для добавления в очередь передано {len(pairs)} пар для скрина {screen_id}')
    parts_group = group([create_response_part.s(screen_id, pair[0], pair[1]) for pair in pairs]).apply()
    logger.info(f'add_response_part_to_queue END')
    logger.debug(str(parts_group))


@shared_task(priority=3)
def create_response_part(screen_id, black, white) -> str:
    """Создает новое распознавание скрина с заданными параметрами"""
    logger.info(f'Создана задача распознавания скрина {screen_id} с параметрами ({black}, {white})')
    screen, _ = ScreenResponse.objects.get_or_create(id=screen_id)
    try:
        part_is_exist = ScreenResponsePart.objects.filter(screen=screen, black=black, white=white).exists()
        if part_is_exist:
            return f'Cкрин {screen_id} с параметрами ({black}, {white}) уже есть'
        # pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'
        img = cv2.imdecode(np.fromfile(screen.image.path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        # img = cv2.imdecode(np.fromfile(screen.image.path, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
        # img = cv2.imdecode(np.frombuffer(screen.image.file.read(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        _, binary = cv2.threshold(img, black, white, cv2.THRESH_BINARY)
        path = Path(screen.image.path)
        new_file_path = path.parent
        new_file_name = new_file_path / f'{path.stem}({black}-{white}).jpg'
        cv2.imwrite(new_file_name.as_posix(), binary)
        string = pytesseract.image_to_string(binary, lang='rus')
        text = string.replace('\n', ' ')
        pay = screen_text_to_pay(text)
        fields = ('response_date', 'recipient', 'sender', 'pay', 'transaction')
        cut_pay = copy(pay)
        for key, value in pay.items():
            if key not in fields:
                cut_pay.pop(key)
        new_response_part, status = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white, **cut_pay)
        return f'Создан  ScreenResponsePart {new_response_part.id} для скрина {screen_id} с параметрами ({black}, {white})'
    except Exception as err:
        logger.warning(f'Ошибка при создании ScreenResponsePart: {err}')
        new_response_part, status = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white)
        return f'Создан пустой ScreenResponsePart с параметрами ({black}, {white})'
