import hashlib
import time
from enum import Enum, Flag, auto
from pathlib import PurePosixPath
from urllib.parse import urlparse

import requests
import structlog
import json
import datetime
import pytz
from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from urllib3 import Retry, PoolManager

from core.asu_pay_func import create_asu_withdraw, create_payment_v2, \
    send_sms_code_v2
from core.birpay_func import get_birpay_withdraw, find_birpay_from_id, get_birpays, approve_birpay_refill
from core.birpay_new_func import get_um_transactions, create_payment_data_from_new_transaction, send_transaction_action
from core.global_func import send_message_tg, TZ, Timer, mask_compare
from deposit.func import find_possible_incomings
from deposit.models import *
from django.apps import apps
from users.models import Options


User = get_user_model()

logger = structlog.get_logger('deposit')


def find_time_between_good_screen(last_good_screen_time) -> int:
    """Находит время сколько прошло с момента прихода распознанного скрина в секундах"""
    now = datetime.datetime.now()
    delta = int((now - last_good_screen_time).total_seconds())
    logger.debug(f'Последний хороший скрин приходил {delta} секунд назад')
    return delta


def do_if_macros_broken():
    """Действие если макрос сдох"""
    try:
        send_message_tg('Макрос не активен более 15 секунд', settings.ALARM_IDS)
        Message = apps.get_model('deposit', 'Message')
        Message.objects.create(title='Макрос не активен',
                               text=f'Макрос не активен',
                               type='macros',
                               author=User.objects.get(username='Admin'))
    except Exception as err:
        logger.error(f'Ошибка если макрос сдох: {err}')


@shared_task(priority=1, time_limit=20)
def check_macros():
    """Функция проверки работоспособности макроса"""
    Setting = apps.get_model('deposit', 'Setting')
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
    logger.debug(
        f'last_message_time: {last_message_time}\nlast_good_screen_time: {last_good_screen_time}\ndelta:{delta}')
    if last_message_time < last_good_screen_time and delta > 15:
        logger.info(f'Время больше 10')
        do_if_macros_broken()
        last_message_time_obj.value = datetime.datetime.now().isoformat()
        last_message_time_obj.save()
        return True


@shared_task(bind=True, priority=1, time_limit=15, max_retries=5)
def check_incoming(self, pk, count=0):
    """Функция проверки incoming в birpay"""
    check = {}
    try:
        logger.info(f'Проверка опера. Попытка {count + 1}')
        IncomingCheck = apps.get_model('deposit', 'IncomingCheck')
        incoming_check = IncomingCheck.objects.get(pk=pk)
        check = find_birpay_from_id(birpay_id=incoming_check.birpay_id)
    except Exception as err:
        logger.error(f'Ошибка проверки incoming в birpay: {err}')
        try:
            self.retry(exc=err, countdown=(count + 1) * 15)
        except self.MaxRetriesExceededError:
            logger.error(f'Превышено количество попыток для pk={pk}')
            return f'Превышено количество попыток для pk={pk}'

    try:
        logger.info(f'check result {count}: {check}')
        msg = ''
        if check:
            pay_birpay = check.get('pay')
            operator = check.get('operator')
            if operator:
                operator = operator.get('username')
            status = check.get('status')

            incoming_check.pay_birpay = pay_birpay
            incoming_check.operator = operator
            incoming_check.status = status
            incoming_check.save()
            pay_incoming = incoming_check.incoming.pay
            text_incoming = f'Проверка платежа {incoming_check.incoming.id} на сумму {pay_incoming} azn.\nCheck №{pk} birpay_id: {incoming_check.birpay_id}:\n'
            delta = round(pay_incoming - pay_birpay, 2)
            if status == 0:
                if pay_incoming == pay_birpay:
                    # Не подтвержден. Сумма равна
                    msg = f'{text_incoming}<b>Статус 0</b>'
                elif pay_birpay < pay_incoming:
                    # Не подтвержден. Пришло больше чем нужно
                    msg = f'{text_incoming}<b>Статус 0. Пришло {pay_incoming} azn вместо {pay_birpay} azn (на {delta} больше)</b>'
                else:
                    # Не подтвержден. Пришло меньше чем нужно
                    msg = f'{text_incoming}<b>Статус 0. Пришло {pay_incoming} azn вместо {pay_birpay} azn (на {-delta} меньше)</b>'
            elif status == -1:
                # Пришло больше
                if pay_incoming > pay_birpay:
                    msg = f'{text_incoming}<b>Статус -1. Лишние {delta} azn<>'
            elif status == 1:
                # Подтвержден
                if pay_birpay > pay_incoming:
                    # Пришло меньше
                    msg = f'{text_incoming}<b>Статус 1. Не хватает {delta} azn {operator}</b>'
                elif pay_birpay < pay_incoming:
                    # Пришло больше
                    msg = f'{text_incoming}<b>Статус 1. Лишние {delta} azn {operator}</b>'
            else:
                msg = f'{text_incoming}<b>Неизвестный статус {status}</b>'

        else:
            msg = (
                f'<b>Ничего не найдено</b> при проверке birpay {pk}\n'
                f'({incoming_check.birpay_id})\n'
                f'Платеж {incoming_check.incoming.id} на сумму {incoming_check.incoming.pay} azn'
            )
        if msg:
            send_message_tg(msg, settings.ALARM_IDS)
        return check
    except Exception as err:
        logger.error(f'Ошибка при проверке birpay {pk}: {err}')
        send_message_tg(f'Ошибка при проверке birpay {pk}: {err}', settings.ALARM_IDS)


@shared_task(priority=1)
def test_task(pk, count=0):
    try:
        logger.info('test_task2')
        send_message_tg(f'test_task2: {pk} попытка {count + 1}')
        raise ValueError('xxx')

    except Exception as err:
        logger.error(f'test_task erorr {count}: {err}')
        if count < 5:
            count += 1
            test_task.apply_async(kwargs={'pk': 100, 'count': count}, countdown=3)


@shared_task(priority=1, time_limit=20)
def send_screen_to_payment(incoming_id):
    # Отправка копии скрина в смс Payment
    Incoming = apps.get_model('deposit', 'Incoming')
    logger.debug(f'Отправка копии скрина в смс Payment: {incoming_id}')
    incoming: Incoming = Incoming.objects.get(pk=incoming_id)
    data = {
        'recipient': incoming.recipient,
        'sender': incoming.sender,
        'pay': incoming.pay,
        'transaction': incoming.transaction,
        'response_date': str(incoming.response_date),
        'type': incoming.type,
        'worker': 'copy from Deposite2'
    }
    try:
        retries = Retry(total=5, backoff_factor=3, status_forcelist=[500, 502, 503, 504])
        http = PoolManager(retries=retries)
        response = http.request('POST', url=f'{settings.ASU_HOST}/create_copy_screen/', json=data
                                )
        logger.debug(f'response: {response}')
    except Exception as err:
        logger.error(err)


@shared_task(priority=1, time_limit=20)
def send_transaction_action_task(transaction_id, action):
    # Отправка Actiom
    logger.info(f'20 сек прошло. Отправляем {action} для {transaction_id}')
    json_data = send_transaction_action(transaction_id, action)
    return json_data


@shared_task(priority=1, time_limit=60)
def send_new_transactions_from_um_to_asu_v2():
    countdown = 7
    # Получение новых um транзакций и их обработка
    #  {'title': 'Waiting sms', 'action': 'agent_sms'},
    #  {'title': 'Waiting push', 'action': 'agent_push'},
    #  {'title': 'Decline', 'action': 'agent_decline'}
    start = time.perf_counter()
    UmTransaction = apps.get_model('deposit', 'UmTransaction')
    logger.debug('Поиск новых транзакций')
    new_transactions = get_um_transactions(search_filter={'status': ['new', 'pending']})
    logger.info(f'новых транзакций: {len(new_transactions)}')

    for um_transaction in new_transactions:
        try:
            create_at = datetime.datetime.fromisoformat(um_transaction['createdAt'])
            create_delta = datetime.datetime.now(tz=TZ) - create_at
            if create_delta > datetime.timedelta(days=1):
                continue

            transaction_id = um_transaction['id']
            um_logger = logger.bind(transaction_id=transaction_id)
            base_um_transaction, is_create = UmTransaction.objects.get_or_create(order_id=transaction_id)
            um_logger.info(f'base_um_transaction: {base_um_transaction}, is_create: {is_create}')

            status = um_transaction.get('status')
            actions = um_transaction.get('actions', [])
            action_values = [action['action'] for action in actions]
            um_logger.info(
                f'Обработка транзакции №{transaction_id}. Статус: "{status}". action_values: {action_values}')
            um_logger.debug(f'actions {transaction_id}: {actions}')

            data_for_payment = create_payment_data_from_new_transaction(um_transaction)
            um_logger.debug(f'payment_data: {data_for_payment}')
            payment_data = data_for_payment['payment_data']
            card_data = data_for_payment['card_data']

            # Ждет готовность работы
            if not base_um_transaction.payment_id and ('agent_sms' in action_values or 'agent_push' in action_values):
                # Если еще нет в базе payment_id отправляем на asu и добавляем созданный payment_id в базу
                # Передаем данные карты и передаем agent_sms agent_push чз 20 сек
                try:
                    # Создаем новый Payment через API v2 (с данными карты)
                    um_logger.info(f'Создаем новый Payment v2: {payment_data}')
                    payment_result = create_payment_v2(payment_data, card_data)

                    if payment_result and payment_result.get('payment_id'):
                        payment_id = payment_result['payment_id']
                        sms_required = payment_result.get('sms_required', False)
                        instruction = payment_result.get('instruction', '')

                        um_logger.debug(f'Payment создан: {payment_id}, sms_required: {sms_required}')
                        base_um_transaction.payment_id = payment_id
                        base_um_transaction.save()
                        um_logger.debug(base_um_transaction)

                        # Отправляем действие ждем смс чз 20 сек
                        um_logger.debug(f'sms_required: {sms_required}')
                        if 'agent_sms' in action_values:
                            um_logger.info('Отправляем agent_sms через 20 сек')
                            send_transaction_action_task.apply_async(
                                kwargs={'transaction_id': transaction_id, 'action': 'agent_sms'},
                                countdown=countdown)
                        elif 'agent_push' in action_values:
                            um_logger.info('Отправляем agent_push через 20 сек')
                            send_transaction_action_task.apply_async(
                                kwargs={'transaction_id': transaction_id, 'action': 'agent_push'},
                                countdown=countdown)
                        else:
                            um_logger.warning('Нет известных действий')

                        base_um_transaction.status = 4
                        base_um_transaction.save()
                    else:
                        text = f'Payment по транзакции UM {transaction_id} НЕ создан!'
                        um_logger.debug(text)
                        send_message_tg(message=text, chat_ids=settings.ALARM_IDS)

                except Exception as err:
                    um_logger.error(err)

            # Пришел смс-код и ждет подтверждения. передаем смс-код
            elif status == 'pending' and card_data.get('sms_code') and base_um_transaction.status != 6:
                um_logger.info(f'Получен смс-код {transaction_id}')
                um_logger.info(f'Передаем sms_code {transaction_id}')
                send_sms_code_v2(base_um_transaction.payment_id, card_data['sms_code'], transaction_id)
                um_logger.info(f'Меняем status {transaction_id}')
                base_um_transaction.status = 6
                base_um_transaction.save()
            else:
                um_logger.debug('Доступных действий нет')

        except Exception as err:
            logger.error(err)
            raise err

    logger.debug(f'Обработка новых транзакций закончена за {time.perf_counter() - start}')
    return f'Новых: {len(new_transactions)}'


@shared_task(priority=2, time_limit=30)
def send_new_transactions_from_birpay_to_asu():
    # Задача по запросу выплат с бирпая со статусом pending (0).
    logger = structlog.get_logger('deposit')
    withdraw_list = async_to_sync(get_birpay_withdraw)(limit=512)
    total_amount = 0
    results = []
    limit = 10
    count = 0
    WithdrawTransaction = apps.get_model('deposit.WithdrawTransaction')
    logger.info(f'Всего транзакций бирпай: {len(withdraw_list)}')
    for withdraw in withdraw_list:
        logger = logger.bind(birpay_withdraw_id=withdraw['id'])
        if count >= limit:
            logger.debug(f'break: {count} > {limit}')
            break
        is_exists = WithdrawTransaction.objects.filter(withdraw_id=withdraw['id']).exists()
        logger.debug(f'WithdrawTransaction is_exists: {is_exists}')
        if not is_exists:
            logger.info(f'{withdraw["id"]} not_exists')
            try:
                # Если еще не брали в работу создадим на асупэй
                expired_month = expired_year = target_phone = card_data = None
                amount = round(float(withdraw.get('amount')), 2)
                amount = int(amount)
                create_at = withdraw.get('createdAt', '')
                total_amount += amount
                wallet_id = withdraw.get('customerWalletId', '')
                merchant_transaction_id = withdraw.get('merchantTransactionId', '')
                if wallet_id.startswith('994'):
                    target_phone = f'+{wallet_id}'
                elif len(wallet_id) == 9:
                    target_phone = f'+994{wallet_id}'
                else:
                    payload = withdraw.get('payload', {})
                    if payload:
                        card_date = payload.get('card_date')
                        if card_date:
                            expired_month, expired_year = card_date.split('/')
                            if expired_year:
                                expired_year = expired_year[-2:]
                    card_data = {
                        "card_number": wallet_id,
                    }
                    if expired_month and expired_year:
                        card_data['expired_month'] = expired_month
                        card_data['expired_year'] = expired_year
                withdraw_data = {
                    'withdraw_id': withdraw['id'],
                    'amount': amount,
                    'card_data': card_data,
                    'target_phone': target_phone,
                    # 'merchant_transaction_id': merchant_transaction_id,
                    'payload': {
                        'merchant_transaction_id': merchant_transaction_id,
                        'create_at': create_at
                    },
                }
                logger.info(f'Передача на асупэй: {withdraw_data}')
                result = create_asu_withdraw(**withdraw_data)
                logger.debug(f'result: {result}')
                if result.get('status') == 'success':
                    logger.debug(f'success')
                    # Успешно создана
                    try:
                        birpay_withdraw = WithdrawTransaction.objects.create(
                            withdraw_id=withdraw['id'],
                            status=1,
                        )
                        logger.info(f'Создан WithdrawTransaction: {birpay_withdraw}')
                        count += 1
                    except Exception as err:
                        logger.warning(f'Ошибка при создании WithdrawTransactio: {err}')

                    results.append(result)

                    logger.info(f'count: {count}. Добавлено: {result}')
                else:
                    logger.debug('unseccess!')

            except Exception as e:
                logger.error(f'Неизвестная ошибка при обработке birpay_withdraw: {type(e): {e}}')
    return results


@shared_task(bind=True, max_retries=3, default_retry_delay=1, priority=2, soft_time_limit=20)
def download_birpay_check_file(self, order_id, check_file_url):
    from deposit.models import BirpayOrder
    try:
        order = BirpayOrder.objects.get(id=order_id)
        response = requests.get(check_file_url)
        if response.ok:
            file_content = response.content
            suffix_path = urlparse(check_file_url).path
            ext = PurePosixPath(suffix_path).suffix.lower()
            suffix =  ext if ext else ".jpg"
            filename = f'{order.merchant_transaction_id}_{order.amount}_azn.{suffix}'
            order.check_file.save(filename, ContentFile(file_content), save=True)
            order.check_file_failed = False
            md5_hash = hashlib.md5(file_content).hexdigest()
            is_double = False
            if md5_hash:
                threshold = timezone.now() - datetime.timedelta(days=1)
                is_double = BirpayOrder.objects.filter(created_at__gte=threshold, check_hash=md5_hash).exists()
            order.check_hash = md5_hash
            update_fields = ['check_file', 'check_file_failed', 'check_hash']
            if is_double:
                order.check_is_double = is_double
                update_fields.append('check_is_double')
            else:
                if not order.is_painter():
                    order.gpt_processing = True
                    update_fields.append('gpt_processing')
                    send_image_to_gpt_task.delay(order.birpay_id)
            order.save(update_fields=update_fields)
            return f"OK: {filename}"
        else:
            order.check_file_failed = True
            order.save(update_fields=['check_file_failed'])
            raise Exception(f"Failed: status={response.status_code}")
    except Exception as exc:
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            order = BirpayOrder.objects.get(id=order_id)
            order.check_file_failed = True
            order.save(update_fields=['check_file_failed'])
            return f"Error after max retries: {exc}"


def process_birpay_order(data):
    birpay_id = data['id']
    bind_contextvars(birpay_id=birpay_id)
    check_file_url = data.get('payload', {}).get('check_file')

    card_number = None
    paymentRequisite = data.get('paymentRequisite')
    if paymentRequisite:
        payload = paymentRequisite.get('payload', {})
        if payload:
            card_number = payload.get('card_number')

    order_data = {
        'created_at': parse_datetime(data['createdAt']),
        'updated_at': parse_datetime(data['updatedAt']),
        'birpay_id': birpay_id,
        'merchant_transaction_id': data['merchantTransactionId'],
        'merchant_user_id': data['merchantUserId'],
        'merchant_name': data['merchant']['name'] if 'merchant' in data and data['merchant'] else None,
        'customer_name': data.get('customerName'),
        'card_number': card_number,
        'status': data['status'],
        'amount': float(data['amount']),
        'operator': None,
        'raw_data': data,
        'check_file_url': check_file_url,
    }

    if 'operator' in data and data['operator'] and 'username' in data['operator']:
        order_data['operator'] = data['operator']['username']
    elif 'user' in data and data['user'] and 'username' in data['user']:
        order_data['operator'] = data['user']['username']

    BirpayOrder = apps.get_model('deposit', 'BirpayOrder')
    order, created = BirpayOrder.objects.get_or_create(birpay_id=birpay_id, defaults=order_data)

    updated = False

    if not created:
        for field, value in order_data.items():
            if getattr(order, field) != value:
                logger.info(f"Поле '{field}' изменено: {getattr(order, field)} → {value}")
                setattr(order, field, value)
                updated = True
        if updated:
            order.save()
    else:
        logger.info(f"Создан новый {order} birpay_id={birpay_id}")

    if check_file_url and not order.check_file and not order.check_file_failed:
        order.check_file_failed = True   # Резервируем скачивание — повторно не поставим
        order.save(update_fields=['check_file_failed'])
        download_birpay_check_file.delay(order.id, check_file_url)
        logger.info(f"Задача на скачивание файла для заказа {birpay_id} отправлена в celery.")
    return order, created, updated


@shared_task(bind=True, max_retries=2)
def send_image_to_gpt_task(self, birpay_id):
    logger = structlog.get_logger('deposit')
    bind_contextvars(birpay_id=birpay_id)
    logger.info(f'send_image_to_gpt_task {birpay_id}')
    BirpayOrder = apps.get_model('deposit', 'BirpayOrder')
    try:
        order = BirpayOrder.objects.get(birpay_id=birpay_id)
        logger.debug(f'Найден BirpayOrder: {order}')
        bind_contextvars(merchant_transaction_id=order.merchant_transaction_id, birpay_id=birpay_id)
    except BirpayOrder.DoesNotExist:
        logger.error(f"BirpayOrder {birpay_id} не найден")
        return f"BirpayOrder {birpay_id} не найден"

    try:
        if not order.check_file:
            logger.error(f"BirpayOrder {birpay_id}: Нет файла чека")
            return f"BirpayOrder {birpay_id}: Нет файла чека"
        logger.info(f"BirpayOrder {birpay_id}: отправка файла {order.check_file.name} в GPT")
        with order.check_file.open("rb") as f:
            files = {'file': (order.check_file.name, f, 'image/jpeg')}
            response = requests.post("http://45.14.247.139:9000/recognize/", files=files, timeout=15)
            logger.info(f"BirpayOrder {birpay_id}: ответ GPT code={response.status_code}")
            if response.ok:
                result = response.json().get("result")
                if result is None:
                    logger.error(
                        f"BirpayOrder {birpay_id}: GPT ответ не содержит result или он пустой! response={response.text}")
                    order.gpt_data = {}  # или '', или None — что у вас по логике
                else:
                    order.gpt_data = json.loads(result)
                order.save(update_fields=['gpt_data'])
                order.save(update_fields=['gpt_data'])
                logger.info(f"BirpayOrder {birpay_id}: gpt_data успешно записано: {result}")
            else:
                order.gpt_data = {"error": f"HTTP {response.status_code}", "text": response.text}
                logger.error(f"BirpayOrder {birpay_id}: ошибка HTTP {response.status_code}")
    except Exception as e:
        order.gpt_data = {"error": str(e)}
        logger.exception(f"BirpayOrder {birpay_id}: исключение: {e}")
        return f"BirpayOrder {birpay_id}: исключение: {e}"
    finally:
        result_str = ''
        # Автоматическое подтверждение.
        try:
            order.refresh_from_db()
            gpt_data = order.gpt_data
            if isinstance(gpt_data, str):
                gpt_data = json.loads(gpt_data)

            order_amount = order.amount
            gpt_amount = float(gpt_data.get('amount', 0))
            gpt_status = gpt_data.get('status', 0)
            gpt_recipient = gpt_data.get('recipient', '')
            gpt_sender = gpt_data.get('gpt_sender', '')
            gpt_time_str = gpt_data.get('create_at')
            if not gpt_time_str:
                gpt_time_str = '2000-01-01T00:00:00'
            gpt_time_naive = datetime.datetime.fromisoformat(gpt_time_str)
            gpt_time_naive_msk = gpt_time_naive - datetime.timedelta(hours=1)
            gpt_time_aware = TZ.localize(gpt_time_naive_msk)

            now = timezone.now()
            gpt_imho_result = BirpayOrder.GPTIMHO(0)
            # Мнение GPT
            if gpt_status:
                gpt_imho_result |= BirpayOrder.GPTIMHO.gpt_status

            # Проверка суммы в чеке
            logger.info(f'gpt_amount == order_amount: {gpt_amount} == {order_amount}: {float(gpt_amount) == order_amount}')
            if float(gpt_amount) == order_amount:
                gpt_imho_result |= BirpayOrder.GPTIMHO.amount

            # Проверка на получателя
            recipient_is_correct = mask_compare(order.card_number, gpt_recipient)
            logger.info(f'Сравниваем маски {order.card_number} {gpt_recipient}: {recipient_is_correct}')
            if recipient_is_correct:
                gpt_imho_result |= BirpayOrder.GPTIMHO.recipient

            # Проверка времени
            logger.info(
                f'GPTIMHO.time: {now - datetime.timedelta(hours=1)} < {gpt_time_aware} < {gpt_time_aware <= now + datetime.timedelta(hours=1)}: {now - datetime.timedelta(hours=1) < gpt_time_aware  <= now + datetime.timedelta(hours=1)}')
            if now - datetime.timedelta(hours=1) < gpt_time_aware  <= now + datetime.timedelta(hours=1):
                gpt_imho_result |= BirpayOrder.GPTIMHO.time

            # Найдем подходящие смс:
            incomings = find_possible_incomings(order_amount, gpt_time_aware)
            logger.info(f'Найдено смс с суммой: {len(incomings)}')
            incomings_with_correct_card_and_order_amount = []
            for incoming in incomings:
                logger.info(f'Проверка СМС {incoming}')
                sms_recipient = incoming.recipient
                recipient_is_correct = mask_compare(sms_recipient, gpt_recipient)
                logger.info(f'маски равны? {recipient_is_correct}. {sms_recipient} и {gpt_recipient} ')
                logger.info(f'Cумма подходит {incoming.pay}: {order_amount == incoming.pay}:{order_amount} и {incoming.pay}')
                if recipient_is_correct and order_amount == incoming.pay:
                    logger.info(f'СМС подходит: {incoming}')
                    incomings_with_correct_card_and_order_amount.append(incoming)
                else:
                    logger.info(f'Смс не подходит: {incoming}')
            if len(incomings_with_correct_card_and_order_amount) == 0:
                logger.info(f'Ни одна смс не подходит')
            if len(incomings_with_correct_card_and_order_amount) == 1:
                # Найдена однозначная СМС
                incoming_sms = incomings_with_correct_card_and_order_amount[0]
                logger.info(f'Найдена однозначная СМС: {incoming_sms}')
                gpt_imho_result |= BirpayOrder.GPTIMHO.sms
                
                # Проверка баланса: расчетный баланс должен соответствовать фактическому балансу из SMS
                # Используем уже рассчитанные значения из БД (check_balance вычисляется только при создании Incoming)
                if incoming_sms.check_balance is not None and incoming_sms.balance is not None:
                    # Округляем до 0.1 перед сравнением для учета погрешностей округления
                    check_balance_rounded = round(incoming_sms.check_balance * 10) / 10
                    balance_rounded = round(incoming_sms.balance * 10) / 10
                    balance_match = check_balance_rounded == balance_rounded
                    logger.info(f'Проверка баланса: check_balance={incoming_sms.check_balance} (округлено {check_balance_rounded}), balance={incoming_sms.balance} (округлено {balance_rounded}), совпадают={balance_match}')
                    if balance_match:
                        gpt_imho_result |= BirpayOrder.GPTIMHO.balance_match
                        logger.info(f'balance_match: ✅')
                    else:
                        logger.info(f'balance_match: ❌ (расчетный {incoming_sms.check_balance} != фактический {incoming_sms.balance})')
                else:
                    logger.info(f'balance_match: ❌ (нет данных: check_balance={incoming_sms.check_balance}, balance={incoming_sms.balance})')
            else:
                logger.info(f'Однозначная смс не найдена')
            
            # Проверка репутации пользователя
            user_orders = BirpayOrder.objects.filter(merchant_user_id=order.merchant_user_id)
            total_user_orders = user_orders.count()
            logger.info(f'total_user_orders: {total_user_orders}')
            if total_user_orders >= 5:
                gpt_imho_result |= BirpayOrder.GPTIMHO.min_orders
                logger.info(f'min_orders: ✅')
                
                user_orders_1 = user_orders.filter(status=1).count()
                user_order_percent = round(user_orders_1 / total_user_orders * 100, 0)
                logger.info(f'user_order_percent: {user_order_percent}')
                if user_order_percent >= 40:
                    gpt_imho_result |= BirpayOrder.GPTIMHO.user_reputation
                    logger.info(f'user_reputation: ✅')
            else:
                logger.info(f'min_orders: ❌ (всего {total_user_orders} < 5)')

            result_str = ", ".join(
                f"{flag.name}: {'✅ ' if flag in gpt_imho_result else '❌ '}"
                for flag in BirpayOrder.GPTIMHO)
            logger.info(f'gpt_imho_result: {result_str}')

            update_fields = ["gpt_processing", "gpt_data", "gpt_flags", "sender"]
            # Сохранение данных
            order.gpt_processing = False
            order.sender = gpt_sender
            order.gpt_flags = gpt_imho_result.value
            Options = apps.get_model('users', 'Options')
            gpt_auto_approve = Options.load().gpt_auto_approve
            # Автоматическое подтверждение только если ВСЕ 8 флагов установлены (255 = 0b11111111)
            if not order.is_moshennik() and not order.is_painter() and gpt_auto_approve and order.gpt_flags == 255:
                # Автоматическое подтверждение
                incoming_sms = incomings_with_correct_card_and_order_amount[0]
                logger.info(
                    f'Автоматическое подтверждение {order} {order.merchant_transaction_id}: смс{incoming_sms.id}')
                order.incomingsms_id = incoming_sms.id
                update_fields.append("incomingsms_id")
                order.incoming = incoming_sms
                order.confirmed_time = timezone.now()
                update_fields.append("incoming")
                update_fields.append("confirmed_time")
                incoming_sms.birpay_id = order.merchant_transaction_id
                incoming_sms.save()
                # Апрувнем заявку
                response = approve_birpay_refill(pk=order.birpay_id)
                if response.status_code != 200:
                    text = f"ОШИБКА пдтверждения {order} mtx_id {order.merchant_transaction_id}: {response.text}"
                    logger.warning(text)
                    send_message_tg(message=text, chat_ids=settings.ALARM_IDS)
            if order.is_moshennik():
                logger.info(f'Обработка мошенника')
                if len(incomings_with_correct_card_and_order_amount) == 1:
                    incoming_sms = incomings_with_correct_card_and_order_amount[0]
                    incoming_sms.comment = f'Смс мошенника. birpay_id {order.birpay_id} Tx ID {order.merchant_transaction_id} UserID {order.merchant_user_id}'
                    incoming_sms.birpay_id = ''
                    incoming_sms.save()

            order.save(update_fields=update_fields)

        except ValueError as e:
            logger.warning(e)
        except Exception as e:
            logger.error(f'Не смог обработать авто подтверждение BirpayOrder {birpay_id}: {e}', exc_info=True)

        return result_str


@shared_task(priority=1, time_limit=5)
def refresh_birpay_data():

    birpay_data = get_birpays()
    if birpay_data:
        if settings.DEBUG:
            birpay_data = birpay_data[:10]
            logger.info(f'birpay_data: {birpay_data}')
        with Timer(f'Обработка birpay_data'):
            for row in birpay_data:
                b_id = row.get('id')
                result = process_birpay_order(row)
                # logger.info(f'Обработка birpay_id {b_id}: {result}')
    return len(birpay_data)


@shared_task(priority=2, time_limit=30)
def check_cards_activity():
    """Периодическая задача для проверки активности карт"""
    try:
        logger.info('Запуск проверки активности карт')
        
        # Получаем настройки
        options = Options.load()
        monitoring_minutes = options.card_monitoring_minutes
        
        # Получаем все активные карты из assigned_cards всех операторов
        staff_users = User.objects.filter(is_staff=True, is_active=True).select_related('profile')
        all_cards = set()
        
        for user in staff_users:
            profile = getattr(user, 'profile', None)
            if profile and profile.assigned_card_numbers:
                cards = profile.assigned_card_numbers
                if isinstance(cards, str):
                    cards = [x.strip() for x in cards.split(',') if x.strip()]
                all_cards.update(cards)
        
        if not all_cards:
            logger.info('Нет активных карт для мониторинга')
            return "Нет активных карт"
        
        logger.info(f'Мониторинг {len(all_cards)} карт')
        
        # Получаем текущее время
        now = timezone.now()
        threshold_time = now - datetime.timedelta(minutes=monitoring_minutes)
        
        inactive_cards = []
        cards_to_notify = []
        
        for card_number in all_cards:
            # Проверяем, есть ли поступления на эту карту за последние X минут
            Incoming = apps.get_model('deposit', 'Incoming')
            recent_incomings = Incoming.objects.filter(
                response_date__gte=threshold_time
            ).exclude(recipient__isnull=True).exclude(recipient='')
            
            # Проверяем каждое поступление на соответствие маске карты
            has_recent_activity = False
            for incoming in recent_incomings:
                if mask_compare(card_number, incoming.recipient):
                    has_recent_activity = True
                    break
            
            if not has_recent_activity:
                inactive_cards.append(card_number)
                logger.warning(f'Карта {card_number} неактивна более {monitoring_minutes} минут')
                
                # Проверяем, отправляли ли уже уведомление для этой карты
                CardMonitoringStatus = apps.get_model('deposit', 'CardMonitoringStatus')
                monitoring_status, created = CardMonitoringStatus.objects.get_or_create(
                    card_number=card_number,
                    defaults={'is_active': False, 'last_activity': now}
                )
                
                # Если это новая запись или карта была активна, но стала неактивной
                if created or monitoring_status.is_active:
                    cards_to_notify.append(card_number)
                    monitoring_status.is_active = False
                    monitoring_status.last_activity = now
                    monitoring_status.save()
            else:
                # Если карта активна, обновляем статус
                CardMonitoringStatus = apps.get_model('deposit', 'CardMonitoringStatus')
                monitoring_status, created = CardMonitoringStatus.objects.get_or_create(
                    card_number=card_number,
                    defaults={'is_active': True, 'last_activity': now}
                )
                if not created:
                    monitoring_status.is_active = True
                    monitoring_status.last_activity = now
                    monitoring_status.save()
        
        # Отправляем уведомления только для новых неактивных карт
        if cards_to_notify:
            message = f'На карты № {", ".join(cards_to_notify)} не было поступлений {monitoring_minutes} минут'
            send_message_tg(message, settings.ALARM_IDS)
            logger.warning(f'Отправлено уведомление о {len(cards_to_notify)} новых неактивных картах')
        
        return f'Проверено {len(all_cards)} карт, неактивных: {len(inactive_cards)}'
        
    except Exception as err:
        logger.error(f'Ошибка при проверке активности карт: {err}')
        send_message_tg(f'Ошибка при проверке активности карт: {err}', settings.ALARM_IDS)
        return f"Ошибка: {err}"
