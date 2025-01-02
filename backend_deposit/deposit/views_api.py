import datetime
import logging
import re

import pytz
import structlog
from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request

from backend_deposit.settings import TIME_ZONE
from core.global_func import send_message_tg
from deposit import tasks
from ocr.ocr_func import bytes_to_str, make_after_incoming_save, response_text_from_image
from deposit.models import BadScreen, Incoming, TrashIncoming, Setting
from ocr.screen_response import screen_text_to_pay
from deposit.serializers import IncomingSerializer
from ocr.text_response_func import response_sms1, response_sms2, response_sms3, response_sms4, response_sms5, \
    response_sms6, response_sms7, response_sms8, response_sms9, response_sms10, response_sms11, response_sms12, \
    response_sms13, response_sms14, response_sms15, response_sms16
from ocr.views_api import convert_atb_value

logger = structlog.get_logger(__name__)
TZ = pytz.timezone(TIME_ZONE)


@api_view(['POST'])
def screen_new(request: Request):
    """
    Прием скриншота новая версия
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
        image = request.data.get('image')
        worker = request.data.get('worker')
        name = request.data.get('name')
        black = int(request.data.get('black', 182))
        white = int(request.data.get('white', 255))
        logger.info(request.data.get('lang'))
        lang = request.data.get('lang', 'eng')
        oem = int(request.data.get('oem', 0))
        psm = int(request.data.get('psm', 6))
        image = request.data.get('image')
        image_bytes = image.file.read()

        if not image_bytes:
            logger.info(f'Запрос без изображения')
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                                reason='no screen',
                                charset='utf-8')
        logger.info(f'Параметры response_screen_m10: {black}-{white} {lang} {oem} {psm} {len(image_bytes)}b')
        char_whitelist = '+- :;*•0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
        first_stoke = response_text_from_image(image_bytes, y_start=5, y_end=10, x_start=10, x_end=100,
                                               black=black, white=white,
                                               oem=oem, psm=psm, lang=lang, strip=False,
                                               char_whitelist=char_whitelist).strip()
        amount = response_text_from_image(image_bytes, y_start=12, y_end=28,
                                          black=black, white=white,
                                          oem=oem, psm=psm, lang=lang, strip=False,
                                          char_whitelist=char_whitelist).strip()
        info = response_text_from_image(image_bytes, y_start=28, y_end=70,
                                        black=black, white=white,
                                        oem=oem, psm=4, lang=lang, strip=False,
                                        char_whitelist=char_whitelist).strip()
        logger.debug(f'convert_atb_value: {convert_atb_value(amount)}')
        text = f'first: {first_stoke}\namount: {amount}\n{info}'
        logger.debug(f'Распознан текст: {text}')
        pay = screen_text_to_pay(text)
        logger.debug(f'Распознан pay: {pay}')
        pay_status = pay.pop('status')
        errors = pay.pop('errors')

        if errors:
            logger.warning(f'errors: {errors}')
        sms_type = pay.get('type')

        if not sms_type:
            # Действие если скрин не по известному шаблону
            logger.info('скрин не по известному шаблону')
            new_screen = BadScreen.objects.create(name=name, worker=worker, image=image)
            logger.debug(f'BadScreen сохранен')
            logger.debug(f'Возвращаем статус 200: not recognize')
            # path = f'{host}{MEDIA_ROOT}{new_screen.image.url}'
            path = f'{host}{new_screen.image.url}'
            # msg = f'Пришел хреновый скрин с {worker}: {name}\n{path}'
            # send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
            return HttpResponse(status=status.HTTP_200_OK,
                                reason='not recognize',
                                charset='utf-8')

        # Если шаблон найден:
        if sms_type:
            last_good_screen_time, _ = Setting.objects.get_or_create(name='last_good_screen_time')
            last_good_screen_time.value = datetime.datetime.now().isoformat()
            last_good_screen_time.save()

            transaction_m10 = pay.get('transaction')
            incoming_duplicate = Incoming.objects.filter(transaction=transaction_m10).all()
            # Если дубликат:
            if incoming_duplicate:
                logger.info(f'Найден дубликат {incoming_duplicate}')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='Incoming duplicate',
                                    charset='utf-8')
            # Если статус отличается от 'успешно'
            if pay_status.lower().replace(' ', '') not in ['успешно', 'success']:
                logger.warning(f'Плохой статус: {pay}.')
                # Проверяем на дубликат в BadScreen
                is_duplicate = BadScreen.objects.filter(transaction=transaction_m10).exists()
                if not is_duplicate:
                    logger.info('Сохраняем в BadScreen')
                    BadScreen.objects.create(name=name, worker=worker, image=image,
                                             transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='New BadScreen',
                                        charset='utf-8')
                else:
                    logger.info('Дубликат в BadScreen')
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='duplicate in BadScreen',
                                        charset='utf-8')

            # Действия со статусом Успешно
            serializer = IncomingSerializer(data=pay)
            if serializer.is_valid():
                # Сохраянем Incoming
                logger.info(f'Incoming serializer valid. Сохраняем транзакцию {transaction_m10}')
                new_incoming = serializer.save(worker=worker, image=image)

                # Логика после сохранения
                make_after_incoming_save(new_incoming)

                # ОТправляем копию в Payment
                logger.debug(f'Задача копию в Payment: {new_incoming.id}')
                tasks.send_screen_to_payment.delay(new_incoming.id)

                # Сохраняем в базу-бота телеграм:
                # logger.debug(f'Пробуем сохранить в базу бота: {new_incoming}')
                # add_incoming_from_asu_to_bot_db(new_incoming)


                return HttpResponse(status=status.HTTP_201_CREATED,
                                    reason='created',
                                    charset='utf-8')
            else:
                # Если не сохранилось в Incoming
                logger.error('Incoming serializer invalid')
                logger.error(f'serializer errors: {serializer.errors}')
                transaction_error = serializer.errors.get('transaction')

                # Если просто дубликат:
                if transaction_error:
                    transaction_error_code = transaction_error[0].code
                    if transaction_error_code == 'unique':
                        logger.info('Такая транзакция уже есть. Дупликат.')
                        return HttpResponse(status=status.HTTP_201_CREATED,
                                            reason='Incoming duplicate',
                                            charset='utf-8')

                # Обработа неизвестных ошибок при сохранении
                logger.warning('Неизестная ошибка')
                if not BadScreen.objects.filter(transaction=transaction_m10).exists():
                    BadScreen.objects.create(name=name, worker=worker, transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='invalid serializer. Add to trash',
                                        charset='utf-8')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='invalid serializer. Duplicate in trash',
                                    charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                            reason=f'{err}',
                            charset='utf-8')


@api_view(['POST'])
def screen(request: Request):
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
        lang = request.data.get('lang', 'rus')

        if not image or not image.file:
            logger.info(f'Запрос без изображения')
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                                reason='no screen',
                                charset='utf-8')

        file_bytes = image.file.read()
        text = bytes_to_str(file_bytes, lang=lang)
        logger.debug(f'Распознан текст: {text}')
        pay = screen_text_to_pay(text)
        logger.debug(f'Распознан pay: {pay}')

        pay_status = pay.pop('status')
        errors = pay.pop('errors')

        if errors:
            logger.warning(f'errors: {errors}')
        sms_type = pay.get('type')

        if not sms_type:
            # Действие если скрин не по известному шаблону
            logger.info('скрин не по известному шаблону')
            new_screen = BadScreen.objects.create(name=name, worker=worker, image=image)
            logger.debug(f'BadScreen сохранен')
            logger.debug(f'Возвращаем статус 200: not recognize')
            # path = f'{host}{MEDIA_ROOT}{new_screen.image.url}'
            path = f'{host}{new_screen.image.url}'
            # msg = f'Пришел хреновый скрин с {worker}: {name}\n{path}'
            # send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
            return HttpResponse(status=status.HTTP_200_OK,
                                reason='not recognize',
                                charset='utf-8')

        # Если шаблон найден:
        if sms_type:
            last_good_screen_time, _ = Setting.objects.get_or_create(name='last_good_screen_time')
            last_good_screen_time.value = datetime.datetime.now().isoformat()
            last_good_screen_time.save()

            transaction_m10 = pay.get('transaction')
            incoming_duplicate = Incoming.objects.filter(transaction=transaction_m10).all()
            # Если дубликат:
            if incoming_duplicate:
                logger.info(f'Найден дубликат {incoming_duplicate}')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='Incoming duplicate',
                                    charset='utf-8')
            # Если статус отличается от 'успешно'
            if pay_status.lower().replace(' ', '') != 'успешно':
                logger.warning(f'Плохой статус: {pay}.')
                # Проверяем на дубликат в BadScreen
                is_duplicate = BadScreen.objects.filter(transaction=transaction_m10).exists()
                if not is_duplicate:
                    logger.info('Сохраняем в BadScreen')
                    BadScreen.objects.create(name=name, worker=worker, image=image,
                                             transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='New BadScreen',
                                        charset='utf-8')
                else:
                    logger.info('Дубликат в BadScreen')
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='duplicate in BadScreen',
                                        charset='utf-8')

            # Действия со статусом Успешно
            serializer = IncomingSerializer(data=pay)
            if serializer.is_valid():
                # Сохраянем Incoming
                logger.info(f'Incoming serializer valid. Сохраняем транзакцию {transaction_m10}')
                new_incoming = serializer.save(worker=worker, image=image)

                # Логика после сохранения
                make_after_incoming_save(new_incoming)

                # Сохраняем в базу-бота телеграм:
                # logger.debug(f'Пробуем сохранить в базу бота: {new_incoming}')
                # add_incoming_from_asu_to_bot_db(new_incoming)

                return HttpResponse(status=status.HTTP_201_CREATED,
                                    reason='created',
                                    charset='utf-8')
            else:
                # Если не сохранилось в Incoming
                logger.error('Incoming serializer invalid')
                logger.error(f'serializer errors: {serializer.errors}')
                transaction_error = serializer.errors.get('transaction')

                # Если просто дубликат:
                if transaction_error:
                    transaction_error_code = transaction_error[0].code
                    if transaction_error_code == 'unique':
                        logger.info('Такая транзакция уже есть. Дупликат.')
                        return HttpResponse(status=status.HTTP_201_CREATED,
                                            reason='Incoming duplicate',
                                            charset='utf-8')

                # Обработа неизвестных ошибок при сохранении
                logger.warning('Неизестная ошибка')
                if not BadScreen.objects.filter(transaction=transaction_m10).exists():
                    BadScreen.objects.create(name=name, worker=worker, transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='invalid serializer. Add to trash',
                                        charset='utf-8')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='invalid serializer. Duplicate in trash',
                                    charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.info(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                            reason=f'{err}',
                            charset='utf-8')


patterns = {
    'sms1': r'^Imtina:(.*)\nKart:(.*)\nTarix:(.*)\nMercant:(.*)\nMebleg:(.*) .+\nBalans:(.*) ',
    'sms2': r'.*Mebleg:\s*(.*?) AZN.*\n*.*\n*.*\nKart:(.*)\n*Tarix:(.*)\n*Merchant:(.*)\n*Balans:(.*) .*',
    'sms3': r'^.+[medaxil|mexaric] (.+?) AZN (.*)(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d).+Balance: (.+?) AZN.*',
    'sms4': r'^Amount:(.+?) AZN[\n]?.*\nCard:(.*)\nDate:(.*)\nMerchant:(.*)[\n]*Balance:(.*) .*',
    'sms5': r'.*Mebleg:(.+) AZN.*\n.*(\*\*\*.*)\nUnvan: (.*)\n(.*)\nBalans: (.*) AZN',
    'sms6': r'.*Mebleg:(.+) AZN.*\nHesaba medaxil: (.*)\nUnvan: (.*)\n(.*)\nBalans: (.*) AZN',
    'sms7': r'(.+) AZN.*\n(.+)\nBalans (.+) AZN\nKart:(.+)',
    'sms8': r'.*Mebleg: (.+) AZN.*Merchant: (.*)\sBalans: (.*) AZN',
    'sms9': r'(.*)\n(\d\d\d\d\*\*\d\d\d\d)\nMedaxil\n(.*) AZN\n(\d\d:\d\d \d\d\.\d\d.\d\d)\nBALANCE\n(.*)AZN',
    'sms10': r'(.*)\n(\d\d\d\d\*\*\d\d\d\d)\nMedaxil (.*) AZN\nBALANCE\n(.*) AZN\n(\d\d:\d\d \d\d\.\d\d.\d\d)',
    'sms11': r'Odenis\n(.*) AZN \n(.*\n.*)\n(\d\d\d\d\*\*\d\d\d\d).*\n(\d\d:\d\d \d\d\.\d\d.\d\d)\nBALANCE\n(.*) AZN',
    'sms12': r'(\d\d\.\d\d\.\d\d \d\d:\d\d)(.*)AZ Card: (.*) amount:(.*)AZN.*Balance:(.*)AZN',
    'sms13': r'Odenis: (.*) AZN\n(.*)\n(\d\d\d\d\*\*\d\d\d\d).*\n(\d\d:\d\d \d\d\.\d\d.\d\d)\nBALANCE\n(.*) AZN',
    'sms14': r'^.+[medaxil|mexaric]: (.+?) AZN\n(.*)\n(\d\d:\d\d \d\d\.\d\d\.\d\d)\nBALANCE\n(.+?) AZN.*',
    'sms15': r'Medaxil C2C: (.+?) AZN\n(.*)\n(.*)\n(\d\d:\d\d \d\d\.\d\d\.\d\d)\nBALANCE\n(.+?) AZN.*',
    'sms16': r'.*Summa:\s*(.*?) AZN.*\n*.*\n*.*\nKarta:(.*)\n*Data:(.*)\n*Merchant:(.*)\n*Balans:(.*) .*'

}
response_func = {
    'sms1': response_sms1,
    'sms2': response_sms2,
    'sms3': response_sms3,
    'sms4': response_sms4,
    'sms5': response_sms5,
    'sms6': response_sms6,
    'sms7': response_sms7,
    'sms8': response_sms8,
    'sms9': response_sms9,
    'sms10': response_sms10,
    'sms11': response_sms11,
    'sms12': response_sms12,
    'sms13': response_sms13,
    'sms14': response_sms14,
    'sms15': response_sms15,
    'sms16': response_sms16,
}


def response_sms_template(text):
    fields = ['response_date', 'recipient', 'sender', 'pay', 'balance',
              'transaction', 'type']
    text_sms_type = ''
    responsed_pay = {}
    for sms_type, pattern in patterns.items():
        search_result = re.findall(pattern, text)
        if search_result:
            logger.debug(f'Найдено: {sms_type}: {search_result}')
            text_sms_type = sms_type
            responsed_pay: dict = response_func[text_sms_type](fields, search_result[0])
            # errors = responsed_pay.pop('errors')
            break
    return responsed_pay


def analyse_sms_text_and_save(text, imei, sms_id, worker, *args, **kwargs):
    errors = []
    fields = ['response_date', 'recipient', 'sender', 'pay', 'balance',
              'transaction', 'type']
    text_sms_type = ''
    responsed_pay = {}

    for sms_type, pattern in patterns.items():
        search_result = re.findall(pattern, text)
        if search_result:
            logger.debug(f'Найдено: {sms_type}: {search_result}')
            text_sms_type = sms_type
            responsed_pay: dict = response_func[text_sms_type](fields, search_result[0])
            errors = responsed_pay.pop('errors')
            break

    # Добавим получателя если его нет
    if not responsed_pay.get('recipient'):
        responsed_pay['recipient'] = imei
    if text_sms_type:
        logger.info(f'Сохраняем в базу{responsed_pay}')
        if text_sms_type in ['sms8', 'sms7']:
            # Шаблоны без времени
            threshold = datetime.datetime.now(tz=TZ) - datetime.timedelta(hours=12)
            is_duplicate = Incoming.objects.filter(
                sender=responsed_pay.get('sender'),
                pay=responsed_pay.get('pay'),
                balance=responsed_pay.get('balance'),
                register_date__gte=threshold
            ).exists()
        else:
            is_duplicate = Incoming.objects.filter(
                response_date=responsed_pay.get('response_date'),
                sender=responsed_pay.get('sender'),
                pay=responsed_pay.get('pay'),
                balance=responsed_pay.get('balance')
            ).exists()

        if is_duplicate:
            logger.info(f'Дубликат sms:\n\n{text}')
            msg = f'Дубликат sms:\n\n{text}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
        else:
            created = Incoming.objects.create(**responsed_pay, worker=worker or imei)
            logger.info(f'Создан: {created}')

    else:
        logger.info(f'Неизвестный шаблон\n{text}')
        new_trash = TrashIncoming.objects.create(text=text, worker=worker or imei)
        logger.info(f'Добавлено в мусор: {new_trash}')
    return {'response': HttpResponse(sms_id), 'errors': errors}


@api_view(['POST'])
def sms(request: Request):
    """
    Прием sms
    {'id': ['b1899338-2314-400c-a4ff-a9ef3d890c79'], 'from': ['icard'], 'to': [''], 'message': ['Mebleg:+50.00 AZN '], 'res_sn': ['111'], 'imsi': ['400055555555555'], 'imei': ['123456789000000'], 'com': ['COM39'], 'simno': [''], 'sendstat': ['0']}>, host: asu-payme.com, user_agent: None, path: /sms/, forwarded: 91.201.000.000
    """
    errors = []
    text = ''
    try:
        host = request.META["HTTP_HOST"]  # получаем адрес сервера
        user_agent = request.META.get("HTTP_USER_AGENT")  # получаем данные бразера
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        path = request.path
        logger.info(f'request.data: {request.data},'
                     f' host: {host},'
                     f' user_agent: {user_agent},'
                     f' path: {path},'
                     f' forwarded: {forwarded}')

        post = request.POST
        text = post.get('message').replace('\r\n', '\n')
        print(repr(text))
        sms_id = post.get('id')
        imei = post.get('imei')
        worker = request.data.get('worker')
        result = analyse_sms_text_and_save(text, imei, sms_id, worker)
        response = result.get('response')
        errors = result.get('errors')
        return response

    except Exception as err:
        logger.info(f'Неизвестная ошибка при распознавании сообщения: {err}')
        logger.error(f'Неизвестная ошибка при распознавании сообщения: {err}\n', exc_info=True)
        raise err
    finally:
        if errors:
            msg = f'Ошибки при распознавании sms:\n{errors}\n\n{text}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)


@api_view(['POST'])
def sms_forwarder(request: Request):
    """
    Прием sms_forwarder
    """
    errors = []
    text = ''
    try:
        host = request.META["HTTP_HOST"]  # получаем адрес сервера
        user_agent = request.META.get("HTTP_USER_AGENT")  # получаем данные бразера
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        path = request.path
        logger.info(f'request.data: {request.data},'
                     f' host: {host},'
                     f' user_agent: {user_agent},'
                     f' path: {path},'
                     f' forwarded: {forwarded}')
        post = request.POST
        text = post.get('message').replace('\r\n', '\n')
        sms_id = post.get('id')
        imei = post.get('imei')
        worker = post.get('worker')
        result = analyse_sms_text_and_save(text, imei, sms_id, worker)
        response = result.get('response')
        errors = result.get('errors')
        return response

    except Exception as err:
        logger.info(f'Неизвестная ошибка при распознавании сообщения: {err}')
        logger.error(f'Неизвестная ошибка при распознавании сообщения: {err}\n', exc_info=True)
        raise err
    finally:
        if errors:
            msg = f'Ошибки при распознавании sms:\n{errors}\n\n{text}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)