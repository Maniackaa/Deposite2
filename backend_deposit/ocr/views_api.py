import datetime
import json
import logging
import pickle
from http import HTTPStatus

import pytz
from django.conf import settings
from django.http import HttpResponse, JsonResponse

from rest_framework.decorators import api_view
from rest_framework.request import Request

from deposit.models import Incoming
from ocr.models import ScreenResponse
from ocr.ocr_func import bytes_to_str, response_text_from_image, date_m10_response

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
def response_text(request: Request):
    """
    УДАЛЕННОЕ Распознование изображения и возврат распозанного pay
    id, black, white
    """
    try:
        logger.debug('response_text')
        black = int(request.data.get('black'))
        white = int(request.data.get('white'))
        image = request.data.get('image')
        file_bytes = image.file.read()
        text = bytes_to_str(file_bytes, black=black, white=white)
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
        text = bytes_to_str(file_bytes, black=black, white=white)
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


def convert_date_atb(text_date):
    """Преобразует текст с платежа в дату
    5 December 2023 14:51
    Today 13:31
    """
    tz = pytz.timezone(settings.TIME_ZONE)
    text_date = text_date.replace('Today', datetime.datetime.now(tz=tz).date().strftime('%d %B %Y'))
    response_date = datetime.datetime.strptime(text_date, '%d %B %Y %H:%M')
    return response_date


def convert_atb_value(string) -> float:
    """Преобразовывает +1,0m 2.4m в число"""
    try:
        result = ''.join([c if c in ['.', ',', '-'] or c.isdigit() else '' for c in string]).replace(',', '.')
        return float(result)
    except ValueError:
        logger.debug('Не нашлось число')


@api_view(['POST'])
def response_screen_atb(request: Request):
    """
    Распознавание байтов картинки с параметрами
    black, white
    """
    try:
        black = int(request.data.get('black', 175))
        white = int(request.data.get('white', 255))
        logger.info(request.data.get('lang'))
        lang = request.data.get('lang', 'eng')
        oem = int(request.data.get('oem', 0))
        psm = int(request.data.get('psm', 6))
        image = request.data.get('image')
        image_bytes = image.file.read()
        logger.info(f'Параметры response_screen_atb: {black}-{white} {lang} {oem} {psm} {len(image_bytes)}b')
        char_whitelist = '+- :;*0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.'
        date = response_text_from_image(image_bytes, 16, 20, black=black, white=white, oem=1, psm=psm)
        pay = response_text_from_image(image_bytes, 37, 45, black=black, white=white, oem=oem, psm=psm)
        balance1 = response_text_from_image(image_bytes, 49, 52, black=black, white=white, oem=oem, psm=8)
        balance2 = response_text_from_image(image_bytes, 51, 55, black=black, white=white, oem=oem, psm=8)
        bank_card = response_text_from_image(image_bytes, 28, 32, black=black, white=white, oem=oem, psm=psm).strip()
        text = f'{date} {bank_card} {pay} ({balance1}|{balance2})'
        logger.info(f'Распозанано со скрина atb:text: {text}')
        date = convert_date_atb(date).timestamp()
        result = {
            'response_date': date,
            'bank_card': bank_card,
            'pay': convert_atb_value(pay),
            'balance': convert_atb_value(balance1) or convert_atb_value(balance2)
        }
        # return HttpResponse(status=HTTPStatus.OK, reason=text.replace('\n', ' '), charset='utf-8')
        return HttpResponse(status=HTTPStatus.OK, reason=json.dumps(result), charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке response_screen_atb: {err}')
        logger.error(err, exc_info=True)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=err, charset='utf-8')


@api_view(['POST'])
def response_bank1(request: Request):
    """
    Новое Тестовое Распознавание банка вместо atb
    """
    try:
        black = int(request.data.get('black', 175))
        white = int(request.data.get('white', 255))
        logger.info(request.data.get('lang'))
        lang = request.data.get('lang', 'eng')
        oem = int(request.data.get('oem', 0))
        psm = int(request.data.get('psm', 6))
        # oem = 0
        # psm = 7
        image = request.data.get('image')
        image_bytes = image.file.read()
        logger.info(f'Параметры response_screen_m10: {black}-{white} {lang} {oem} {psm} {len(image_bytes)}b')
        char_whitelist = '+- :;*0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
        char_whitelist = '+- :;*0123456789'
        # responsed_text = response_text_from_image(image_bytes, y_start=20, y_end=35, black=black, white=white,
        #                                           oem=oem, psm=psm, lang=lang, char_whitelist=char_whitelist).strip()
        # text = f'({black}-{white}) {responsed_text}'
        # logger.info(f'Распозанано со скрина:\n{text}')
        # bank_card, date, pay, balance = responsed_text.split('\n')
        bank_card = response_text_from_image(image_bytes, y_start=21, y_end=24, black=black, white=white,
                                                  oem=oem, psm=psm, lang=lang, strip=True).strip()
        date = response_text_from_image(image_bytes, y_start=24, y_end=26, black=black, white=white,
                                                  oem=oem, psm=psm, lang=lang, strip=True, char_whitelist=' :0123456789').strip()
        date = date_m10_response(date)
        pay = response_text_from_image(image_bytes, y_start=26, y_end=29, black=black, white=white,
                                                  oem=oem, psm=psm, lang=lang, strip=True).strip()
        balance = response_text_from_image(image_bytes, y_start=29, y_end=32, black=black, white=white,
                                       oem=oem, psm=psm, lang=lang, strip=True).strip()
        result = {
            'response_date': date.timestamp(),
            'bank_card': bank_card,
            'pay': convert_atb_value(pay),
            'balance': convert_atb_value(balance)
        }

        # return HttpResponse(status=HTTPStatus.OK, reason=json.dumps(result, ensure_ascii=False), charset='utf-8')
        return HttpResponse(status=HTTPStatus.OK, reason=json.dumps(result, ensure_ascii=True), charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке response_screen_atb: {err}')
        logger.error(err, exc_info=True)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=json.dumps({'error': str(err)}, ensure_ascii=True), charset='utf-8')


@api_view(['POST'])
def receive_pay(request: Request):
    """Прием распознанного платежа
    pay: {'response_date': datetime.datetime(2023, 12, 1, 15, 59), 'bank_card': 'IBA MOBILE', 'pay': 25.0, 'balance': 25.0}
    """
    try:
        pay_dict = request.data.get('pay')
        pay_dict = json.loads(pay_dict)
        pay = pay_dict.get('pay')
        bank_card = pay_dict.get('bank_card')
        balance = pay_dict.get('balance')
        response_date = pay_dict.get('response_date')
        response_date = datetime.datetime.fromtimestamp(response_date)
        worker = request.data.get('worker')
        sms_type = request.data.get('type')
        phone_name = request.data.get('phone_name')
        image = request.data.get('image')
        name = request.data.get('name')
        logger.info(f'Принят pay: {pay_dict} от телефона {phone_name} со станции {worker}.')
        new_pay, status = Incoming.objects.get_or_create(
            response_date=response_date,
            sender=bank_card,
            pay=pay,
            balance=balance,
            recipient=phone_name,
            type=sms_type,
            worker=worker,
        )
        logger.info(f'{status} {new_pay}')
        if status:
            new_pay.image.save(name=name, content=image)
        return HttpResponse(status=HTTPStatus.OK)
    except Exception as err:
        logger.error(err, exc_info=True)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=err)


@api_view(['POST'])
def response_only_text(request: Request):
    """
    """
    try:
        black = int(request.data.get('black', 175))
        white = int(request.data.get('white', 255))
        logger.info(request.data.get('lang'))
        lang = request.data.get('lang', 'eng')
        oem = int(request.data.get('oem', 0))
        psm = int(request.data.get('psm', 6))
        char_whitelist = request.data.get('char_whitelist', '+- :;*0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя')
        # oem = 0
        # psm = 7
        image = request.data.get('image')
        image_bytes = image.file.read()
        logger.info(f'Параметры response_screen_m10: {black}-{white} {lang} {oem} {psm} {len(image_bytes)}b')
        # char_whitelist = '+- :;*0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
        # char_whitelist = '+- :;*0123456789'
        text = response_text_from_image(image_bytes, y_start=0, y_end=100, black=black, white=white,
                                        oem=oem, psm=psm, lang=lang, strip=True,
                                        char_whitelist=char_whitelist).strip()
        return HttpResponse(status=HTTPStatus.OK, reason=json.dumps(text, ensure_ascii=True), charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке response_screen_atb: {err}')
        logger.error(err, exc_info=True)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST,
                            reason=json.dumps({'error': str(err)}, ensure_ascii=True), charset='utf-8')


@api_view(['POST'])
def response_m10new(request: Request):
    """
    Новое Распознавание банка м10 - возвращает текст
    """
    try:
        black = int(request.data.get('black', 182))
        white = int(request.data.get('white', 255))
        logger.info(request.data.get('lang'))
        lang = request.data.get('lang', 'eng')
        oem = int(request.data.get('oem', 0))
        psm = int(request.data.get('psm', 6))
        image = request.data.get('image')
        image_bytes = image.file.read()
        logger.info(f'Параметры response_screen_m10: {black}-{white} {lang} {oem} {psm} {len(image_bytes)}b')
        char_whitelist = '+- :;*•0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
        first_stoke = response_text_from_image(image_bytes, y_start=4, y_end=10, x_start=10, x_end=100,
                                               black=black, white=white,
                                               oem=oem, psm=psm, lang=lang, strip=False,
                                               char_whitelist=char_whitelist).strip()
        amount = response_text_from_image(image_bytes, y_start=12, y_end=29,
                                               black=black, white=white,
                                               oem=oem, psm=psm, lang=lang, strip=False,
                                               char_whitelist=char_whitelist).strip()
        info = response_text_from_image(image_bytes, y_start=29, y_end=62,
                                               black=black, white=white,
                                               oem=oem, psm=4, lang=lang, strip=False,
                                               char_whitelist=char_whitelist).strip()
        text = f'first: {first_stoke}\namount: {amount}\n{info}'

        logger.debug(f'text: {text}')
        result = [first_stoke, convert_atb_value(amount), info, text]
        x = screen_text_to_pay(text)
        return HttpResponse(status=HTTPStatus.OK, reason=json.dumps([str(x), result], ensure_ascii=True), charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке response_screen_atb: {err}')
        logger.error(err, exc_info=True)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=json.dumps({'error': str(err)}, ensure_ascii=True), charset='utf-8')