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




@shared_task(priority=2)
def add_response_part_to_queue(screen_id: int, pairs: list):
    """Задача для отправки пар в очередь на распознавание"""
    ENDPOINT = 'http://45.67.228.39/ocr/create_screen/'
    logger.info(f'Для добавления в очередь передано {len(pairs)} пар для скрина {screen_id}')
    # Передадим имя, изображение и создадим его на удаленном сервере если его нет. Получим id ScreenResponse
    screen, _ = ScreenResponse.objects.get_or_create(id=screen_id)
    logger.debug(screen)
    image = screen.image.read()
    files = {'image': image}
    logger.debug(f'Отправляем запрос {screen.name} {screen.source}')
    response = requests.post(ENDPOINT, data={'name': screen.name, 'source': screen.source}, files=files, timeout=10)
    data = response.json()
    logger.debug(f'response: {data}')
    remote_screen_id = data.get('id')
    # Создадим задачи для распознавания
    for i, pair in enumerate(pairs):
        remote_response_pair.delay(remote_screen_id, pair)
        logger.debug(f'Отправлено {pair}')
        if i > 10:
            break


@shared_task(priority=3)
def remote_response_pair(screen_id: int, pair):
    ENDPOINT = 'http://45.67.228.39/ocr/reponse_screen/'
    logger.debug(f'Отправляем на {ENDPOINT} {screen_id} {pair}')

    response = requests.post(ENDPOINT, data={'id': screen_id, 'black': pair[0], 'white': pair[1]}, timeout=10)
    logger.debug(f'reaponse: {response}')
    data = response.json()
    logger.debug(f'data: {data}')


# @shared_task(priority=3)
# def create_response_part(screen_id, black, white) -> str:
#     """Создает новое распознавание скрина с заданными параметрами"""
#     logger.info(f'Создана задача распознавания скрина {screen_id} с параметрами ({black}, {white})')
#     screen, _ = ScreenResponse.objects.get_or_create(id=screen_id)
#     try:
#         part_is_exist = ScreenResponsePart.objects.filter(screen=screen, black=black, white=white).exists()
#         if part_is_exist:
#             return f'Cкрин {screen_id} с параметрами ({black}, {white}) уже есть'
#         # pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'
#         img = cv2.imdecode(np.fromfile(screen.image.path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
#         # img = cv2.imdecode(np.fromfile(screen.image.path, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
#         # img = cv2.imdecode(np.frombuffer(screen.image.file.read(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
#         _, binary = cv2.threshold(img, black, white, cv2.THRESH_BINARY)
#         path = Path(screen.image.path)
#         new_file_path = path.parent
#         new_file_name = new_file_path / f'{path.stem}({black}-{white}).jpg'
#         cv2.imwrite(new_file_name.as_posix(), binary)
#         string = pytesseract.image_to_string(binary, lang='rus')
#         text = string.replace('\n', ' ')
#         pay = screen_text_to_pay(text)
#         fields = ('response_date', 'recipient', 'sender', 'pay', 'transaction')
#         cut_pay = copy(pay)
#         for key, value in pay.items():
#             if key not in fields:
#                 cut_pay.pop(key)
#         new_response_part, status = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white, **cut_pay)
#         return f'Создан  ScreenResponsePart {new_response_part.id} для скрина {screen_id} с параметрами ({black}, {white})'
#     except Exception as err:
#         logger.error(f'Ошибка при создании ScreenResponsePart: {err}')
#         # new_response_part, status = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white)
#         # return f'Создан пустой ScreenResponsePart с параметрами ({black}, {white})'
