
import logging

from django.http import HttpResponse, JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request

from ocr.models import ScreenResponse
from ocr.ocr_func import img_path_to_str

from ocr.screen_response import screen_text_to_pay

logger = logging.getLogger(__name__)


@api_view(['POST'])
def create_screen(request: Request):
    """УДАЛЕННОЕ Создание сркрина по имени если его нет и возврат id"""
    try:
        logger.debug('create_screen')
        logger.info(f'Приняли: {request.POST}')
        name = request.data.get('name')
        image = request.data.get('image')
        source = request.data.get('source')
        logger.debug(f'{name} {image} {source}')
        screen = ScreenResponse.objects.filter(name=name).first()
        if not screen:
            screen = ScreenResponse(name=name, source=source)
            screen.image.save(name=f'{name}.jpg', content=image.file, save=False)
            # screen = ScreenResponse.objects.create(name=name, source=source, image=image)
            screen.save()
        return JsonResponse(data={'id': screen.id})
    except Exception as err:
        logger.error(err, exc_info=True)


@api_view(['POST'])
def response_screen(request: Request):
    """
    УДАЛЕННОЕ Распознование изображения и возврат распозанного pay
    id, black, white
    """
    try:
        logger.debug('response_screen')
        # params_example {'id': screen_id, 'black': 100, 'white': 100}
        screen_id = request.data.get('id')
        black = int(request.data.get('black'))
        white = int(request.data.get('white'))
        logger.debug(f'Передан screen_id: {screen_id}')
        screen = ScreenResponse.objects.get(id=screen_id)
        file_bytes = screen.image.file.read()
        text = img_path_to_str(file_bytes, black=black, white=white)
        if text:
            logger.debug(f'Распознан текст: {text}')
            pay = screen_text_to_pay(text)
            logger.debug(f'Распознан pay: {pay}')
            return JsonResponse(data=pay)
        else:
            return JsonResponse(data={})

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return JsonResponse(data={'error': err})

