
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
    """Создание сркрина пл имени если его нет и возврат id"""
    logger.debug('create_screen')
    name = request.data.get('name')
    image = request.data.get('image')
    source = request.data.get('source')
    logger.debug(f'{name} {image} {source}')
    screen, _ = ScreenResponse.objects.get_or_create(name=name, image=image, source=source)
    return JsonResponse(data={'id': screen.id})




@api_view(['POST'])
def response_screen(request: Request):
    """
    Прием скриншота
    """
    try:
        host = request.META["HTTP_HOST"]  # получаем адрес сервера
        user_agent = request.META.get("HTTP_USER_AGENT")  # получаем данные бразера
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        path = request.path
        logger.debug(f'request.data: {request.data},'
                     f' host: {host},'
                     f' user_agent: {user_agent},'
                     f' path: {path},'
                     f' forwarded: {forwarded}')

        # params_example {'name': '/DCIM/Screen.jpg', 'worker': 'Station 1}
        image = request.data.get('image')
        worker = request.data.get('worker')
        name = request.data.get('name')

        if not image or not image.file:
            logger.info(f'Запрос без изображения')
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                                reason='no screen',
                                charset='utf-8')

        file_bytes = image.file.read()
        text = img_path_to_str(file_bytes)
        logger.debug(f'Распознан текст: {text}')
        pay = screen_text_to_pay(text)
        logger.debug(f'Распознан pay: {pay}')


    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                            reason=f'{err}',
                            charset='utf-8')

