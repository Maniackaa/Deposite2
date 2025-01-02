import logging
import time

import requests
import structlog
from celery import shared_task, group, chunks
from celery.utils.log import get_task_logger
from django.contrib.auth import get_user_model

from backend_deposit.settings import REMOTE_SERVER
from ocr.models import ScreenResponse, ScreenResponsePart


User = get_user_model()
# logger = get_task_logger('celery')
logger = structlog.get_logger(__name__)


@shared_task(priority=2)
def response_parts(screen_id: int, pairs: list):
    """Задача для отправки пар в очередь на распознавание"""
    try:
        logger.info(f'Для добавления в очередь передано {len(pairs)} пар для скрина {screen_id}')
        start = time.perf_counter()
        # Передадим имя, изображение и создадим его на удаленном сервере если его нет. Получим id ScreenResponse

        # Создание или получение скрина распознавания на удаленном сервере
        screen = ScreenResponse.objects.get(id=screen_id)
        image = screen.image.read()
        files = {'image': image}
        logger.info(f'Отправляем запрос Имя {screen.name}, источник {screen.source} картинка {screen.image}')
        response = requests.post(REMOTE_SERVER + '/ocr/create_screen/',
                                 data={'name': screen.name, 'source': screen.source},
                                 files=files,
                                 timeout=10)
        logger.info(response.status_code)
        data = response.json()
        logger.info(f'response data: {data}')
        remote_screen_id = data.get('id')
        logger.info(f'Удаленный screen_id: {remote_screen_id}')

        # Создадим задачи для распознавания
        for i, pair in enumerate(pairs):
            remote_response_pair.delay(screen_id, remote_screen_id, pair)
            time.sleep(0.01)

            # if i > 1000:
            #     break
        logger.info(f'Отправлено {i} пар в очередь за {time.perf_counter() - start}')
    except Exception as err:
        logger.error(err)


@shared_task(priority=3)
def remote_response_pair(screen_id: int, remote_screen_id: int, pair):
    try:
        black = pair[0]
        white = pair[1]
        screen = ScreenResponse.objects.get(id=screen_id)
        part = ScreenResponsePart.objects.filter(screen=screen, black=black, white=white).exists()
        if part:
            return f'pair {pair} is present'
        ENDPOINT = REMOTE_SERVER + '/ocr/reponse_screen/'
        logger.info(f'Отправляем на {ENDPOINT} {screen_id} {pair}')
        response = requests.post(ENDPOINT, data={'id': remote_screen_id, 'black': black, 'white': white}, timeout=10)
        logger.info(f'response: {response}')
        data = response.json()
        if data:

            part, _ = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white)
            for name, values in data.items():
                try:
                    setattr(part, name, values)
                except KeyError:
                    pass
            part.save()
            print(part)
            return f'pair  {pair}  or screen {screen_id} created'
    except Exception as err:
        logger.error(err)

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
