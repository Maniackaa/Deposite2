
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
    """Создание сркрина по имени если его нет и возврат id"""
    try:
        logger.debug('create_screen')
        logger.info(f'Приняли: {request.POST}')
        name = request.data.get('name')
        image = request.data.get('image')
        file_bytes = image.file.read()
        source = request.data.get('source')
        logger.debug(f'{name} {image} {source}')
        screen, _ = ScreenResponse.objects.get_or_create(name=name)
        if not screen.image:
            screen.image = file_bytes
            screen.image.name = name
            screen.source = source
            screen.save()
        return JsonResponse(data={'id': screen.id})
    except Exception as err:
        logger.error(err)


@api_view(['POST'])
def response_screen(request: Request):
    """
    Прием данных
    id, black, white
    """
    try:
        logger.debug('response_screen')
        # params_example {{'id': screen_id, 'black': 100, 'white': 100}
        screen_id = request.data.get('id')
        logger.debug(f'Передан screen_id: {screen_id}')
        screen = ScreenResponse.objects.get(id=screen_id)
        file_bytes = screen.image.file.read()
        text = img_path_to_str(file_bytes)
        logger.debug(f'Распознан текст: {text}')
        pay = screen_text_to_pay(text)
        logger.debug(f'Распознан pay: {pay}')
        return JsonResponse(data=pay)


    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                            reason=f'{err}',
                            charset='utf-8')

