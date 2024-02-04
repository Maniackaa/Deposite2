import datetime
import logging
import re

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request

from core.global_func import send_message_tg
from ocr.ocr_func import img_path_to_str, make_after_incoming_save
from deposit.models import BadScreen, Incoming, TrashIncoming, Setting
from ocr.screen_response import screen_text_to_pay
from deposit.serializers import IncomingSerializer
from ocr.text_response_func import response_sms1, response_sms2, response_sms3, response_sms4, response_sms5, \
    response_sms6, response_sms7

logger = logging.getLogger(__name__)
err_log = logging.getLogger('error_log')


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

        pay_status = pay.pop('status')
        errors = pay.pop('errors')

        if errors:
            logger.warning(f'errors: {errors}')
        sms_type = pay.get('type')

        if not sms_type:
            # Действие если скрин не по известному шаблону
            logger.debug('скрин не по известному шаблону')
            new_screen = BadScreen.objects.create(name=name, worker=worker, image=image)
            logger.debug(f'BadScreen сохранен')
            logger.debug(f'Возвращаем статус 200: not recognize')
            # path = f'{host}{MEDIA_ROOT}{new_screen.image.url}'
            path = f'{host}{new_screen.image.url}'
            msg = f'Пришел хреновый скрин с {worker}: {name}\n{path}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
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
                logger.debug(f'Найден дубликат {incoming_duplicate}')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='Incoming duplicate',
                                    charset='utf-8')
            # Если статус отличается от 'успешно'
            if pay_status.lower() != 'успешно':
                logger.debug(f'fПлохой статус: {pay}.')
                # Проверяем на дубликат в BadScreen
                is_duplicate = BadScreen.objects.filter(transaction=transaction_m10).exists()
                if not is_duplicate:
                    logger.debug('Сохраняем в BadScreen')
                    BadScreen.objects.create(name=name, worker=worker, image=image,
                                             transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='New BadScreen',
                                        charset='utf-8')
                else:
                    logger.debug('Дубликат в BadScreen')
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='duplicate in BadScreen',
                                        charset='utf-8')

            # Действия со статусом Успешно
            serializer = IncomingSerializer(data=pay)
            if serializer.is_valid():
                # Сохраянем Incoming
                logger.debug(f'Incoming serializer valid. Сохраняем транзакцию {transaction_m10}')
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
                logger.debug('Incoming serializer invalid')
                logger.debug(f'serializer errors: {serializer.errors}')
                transaction_error = serializer.errors.get('transaction')

                # Если просто дубликат:
                if transaction_error:
                    transaction_error_code = transaction_error[0].code
                    if transaction_error_code == 'unique':
                        # Такая транзакция уже есть. Дупликат.
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
        logger.debug(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                            reason=f'{err}',
                            charset='utf-8')


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
        text = post.get('message')
        sms_id = post.get('id')
        imei = post.get('imei')
        patterns = {
            'sms1': r'^Imtina:(.*)\nKart:(.*)\nTarix:(.*)\nMercant:(.*)\nMebleg:(.*) .+\nBalans:(.*) ',
            'sms2': r'.*Mebleg:(.+) AZN.*\nKart:(.*)\nTarix:(.*)\nMerchant:(.*)\nBalans:(.*) .*',
            'sms3': r'^.+[medaxil|mexaric] (.+?) AZN (.*)(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d).+Balance: (.+?) AZN.*',
            'sms4': r'^Amount:(.+?) AZN[\n]?.*\nCard:(.*)\nDate:(.*)\nMerchant:(.*)[\n]*Balance:(.*) .*',
            'sms5': r'.*Mebleg:(.+) AZN.*\n.*(\*\*\*.*)\nUnvan: (.*)\n(.*)\nBalans: (.*) AZN',
            'sms6': r'.*Mebleg:(.+) AZN.*\nHesaba medaxil: (.*)\nUnvan: (.*)\n(.*)\nBalans: (.*) AZN',
            'sms7': r'(.+) AZN.*\n(.+)\nBalans (.+) AZN\nKart:(.+)',
        }
        response_func = {
            'sms1': response_sms1,
            'sms2': response_sms2,
            'sms3': response_sms3,
            'sms4': response_sms4,
            'sms5': response_sms5,
            'sms6': response_sms6,
            'sms7': response_sms7,
        }
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

        # responsed_pay['message_url'] = message_url

        if text_sms_type:
            logger.info(f'Сохраняем в базу{responsed_pay}')
            is_duplicate = Incoming.objects.filter(
                response_date=responsed_pay.get('response_date'),
                sender=responsed_pay.get('sender'),
                pay=responsed_pay.get('pay'),
                balance=responsed_pay.get('balance')
            ).exists()
            if is_duplicate:
                logger.info('Дубликат sms:\n\n{text}')
                msg = f'Дубликат sms:\n\n{text}'
                send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
            else:
                created = Incoming.objects.create(**responsed_pay, worker=imei)
                logger.info(f'Создан: {created}')

        else:
            logger.info(f'Неизвестный шаблон\n{text}')
            new_trash = TrashIncoming.objects.create(text=text, worker=imei)
            logger.info(f'Добавлено в мусор: {new_trash}')
        return HttpResponse(sms_id)

    except Exception as err:
        logger.error(f'Неизвестная ошибка при распознавании сообщения: {err}\n', exc_info=False)
        err_log.error(f'Неизвестная ошибка при распознавании сообщения: {err}\n', exc_info=True)
        raise err
    finally:
        if errors:
            msg = f'Ошибки при распознавании sms:\n{errors}\n\n{text}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
