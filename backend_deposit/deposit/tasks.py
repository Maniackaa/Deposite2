import hashlib
import time
from enum import Enum, Flag, auto
from sys import exc_info

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

from core.asu_pay_func import create_payment, send_card_data, send_sms_code, create_asu_withdraw
from core.birpay_func import get_birpay_withdraw, find_birpay_from_id, get_birpays
from core.birpay_new_func import get_um_transactions, create_payment_data_from_new_transaction, send_transaction_action
from core.global_func import send_message_tg, TZ, Timer, mask_compare
from deposit.func import find_possible_incomings
from deposit.models import *
from django.apps import apps


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
def send_new_transactions_from_um_to_asu():
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
            um_logger.info(f'Обработка транзакции №{transaction_id}. Статус: "{status}". action_values: {action_values}')
            um_logger.debug(f'actions {transaction_id}: {actions}')
            # if ('agent_sms' not in action_values and 'agent_push' not in action_values) and 'agent_decline' in action_values:
            #     um_logger.info('Нет нужных действий - отклоняем')
            #     response_json = send_transaction_action(um_transaction['id'], 'agent_decline')
            #     um_logger.debug(f'{response_json}')
            data_for_payment = create_payment_data_from_new_transaction(um_transaction)
            um_logger.debug(f'payment_data: {data_for_payment}')
            payment_data = data_for_payment['payment_data']
            card_data = data_for_payment['card_data']

            # Ждет готовность работы
            if not base_um_transaction.payment_id and ('agent_sms' in action_values or 'agent_push' in action_values):
                # Если еще нет в базе payment_id отправляем на asu и добавляем созданный payment_id в базу
                # Передаем данные карты и передаем agent_sms agent_push чз 20 сек
                # base_um_transaction.status = 4
                try:
                    # Создаем новый Payment
                    um_logger.info(f'Создаем новый Payment: {payment_data}')
                    payment_id = create_payment(payment_data)
                    if payment_id:
                        um_logger.debug(f'Payment создан: {payment_id}')
                        base_um_transaction.payment_id = payment_id
                        base_um_transaction.save()
                        um_logger.debug(base_um_transaction)
                        # Отправляем данные карты
                        um_logger.info('Отправляем данные карты')
                        json_response = send_card_data(payment_id, card_data)
                        um_logger.info(f'json_response: {json_response}')
                        if json_response:
                            sms_required = json_response.get('sms_required')
                            # Отправяем действие ждем смс чз 20 сек
                            um_logger.debug(f'sms_required: {sms_required}')
                            if 'agent_sms' in action_values:
                                # send_transaction_action(transaction_id, 'agent_sms')
                                um_logger.info('Отправляем agent_sms чеоез 20 сек')
                                send_transaction_action_task.apply_async(
                                    kwargs={'transaction_id': transaction_id, 'action': 'agent_sms'},
                                    countdown=countdown)
                            elif 'agent_push' in action_values:
                                # send_transaction_action(transaction_id, 'agent_push')
                                um_logger.info('Отправляем agent_push чеоез 20 сек')
                                send_transaction_action_task.apply_async(
                                    kwargs={'transaction_id': transaction_id, 'action': 'agent_push'},
                                    countdown=countdown)
                            else:
                                um_logger.warning('Нет известных действий')
                            base_um_transaction.status = 4
                            base_um_transaction.save()
                    else:
                        um_logger.debug(f'Payment по транзакции {transaction_id} НЕ создан!')

                except Exception as err:
                    um_logger.error(err)

            # Пришел смс-код и ждет подтверждения. передаем смс-код
            elif status == 'pending' and card_data.get('sms_code') and base_um_transaction.status != 6:
                um_logger.info(f'Получен смс-код {transaction_id}')
                um_logger.info(f'Передаем sms_code {transaction_id}')
                send_sms_code(base_um_transaction.payment_id, card_data['sms_code'])
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
                    'merchant_transaction_id': merchant_transaction_id,
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


@shared_task(prority=2, timeout=20)
def download_birpay_check_file(order_id, check_file_url):
    bind_contextvars(birpay_order_id=order_id)
    from deposit.models import BirpayOrder
    order = BirpayOrder.objects.get(id=order_id)
    logger.info(f'Скачивание чека {order}')
    try:
        response = requests.get(check_file_url)
        logger.info(f'response: {response.status_code}')
        if response.ok:
            file_content = response.content

            filename = check_file_url.split('/')[-1]
            order.check_file.save(filename, ContentFile(file_content), save=True)
            order.check_file_failed = False

            # Проверка на дубль
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
                # Запускаем GPT только если НЕ дубль!
                order.gpt_processing = True
                update_fields.append('gpt_processing')
                send_image_to_gpt_task.delay(order.birpay_id)
            order.save(update_fields=update_fields)
            return f"OK: {filename}"
        else:
            order.check_file_failed = True
            order.save(update_fields=['check_file_failed'])
            return f"Failed: status={response.status_code}"
    except Exception as e:
        order.check_file_failed = True
        order.save(update_fields=['check_file_failed'])
        return f"Error: {e}"


def process_birpay_order(data):
    birpay_id = data['id']
    bind_contextvars(birpay_id=birpay_id)
    check_file_url = data.get('payload', {}).get('check_file')

    order_data = {
        'created_at': parse_datetime(data['createdAt']),
        'updated_at': parse_datetime(data['updatedAt']),
        'birpay_id': birpay_id,
        'merchant_transaction_id': data['merchantTransactionId'],
        'merchant_user_id': data['merchantUserId'],
        'merchant_name': data['merchant']['name'] if 'merchant' in data and data['merchant'] else None,
        'customer_name': data.get('customerName'),
        'card_number': data.get('paymentRequisite', {}).get('payload', {}).get('card_number'),
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
    logger.info(f' send_image_to_gpt_task {birpay_id}')
    BirpayOrder = apps.get_model('deposit', 'BirpayOrder')
    try:
        order = BirpayOrder.objects.get(birpay_id=birpay_id)
        logger.debug(f'Найден BirpayOrder: {order}')
        bind_contextvars(birpay_order_id=order.id)
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
                order.gpt_data = json.loads(result)
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
                logger.info(f'Найдена однозначная СМС: {incomings_with_correct_card_and_order_amount[0]}')
                gpt_imho_result |= BirpayOrder.GPTIMHO.sms
            else:
                logger.info(f'Однозначная смс не найдена')

            result_str = ", ".join(
                f"{flag.name}: {'✅ ' if flag in gpt_imho_result else '❌ '}"
                for flag in BirpayOrder.GPTIMHO)
            logger.info(f'gpt_imho_result: {result_str}')

            update_fields = ["gpt_processing", "gpt_data", "gpt_flags"]
            # Сохранение данных
            order.gpt_processing = False
            order.gpt_flags = gpt_imho_result.value
            if order.gpt_flags == 31:
                # Автоматическое подтверждение
                incoming_sms = incomings_with_correct_card_and_order_amount[0]
                logger.info(
                    f'Автоматическое подтверждение {order} {order.merchant_transaction_id}: смс{incoming_sms.id}')
                order.incomingsms_id = incoming_sms.id
                update_fields.append("incomingsms_id")
                incoming_sms.birpay_id = order.merchant_transaction_id
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
    return birpay_data
