import asyncio
import datetime
import time

import requests
import structlog
from asgiref.sync import async_to_sync
from celery import shared_task

from django.conf import settings
from django.http import JsonResponse
from urllib3 import Retry, PoolManager

from core.asu_pay_func import create_payment, send_card_data, send_sms_code, create_asu_withdraw
from core.birpay_func import get_birpay_withdraw
from core.birpay_new_func import get_um_transactions, create_payment_data_from_new_transaction, send_transaction_action
from core.global_func import send_message_tg, TZ
from deposit.models import *
from django.apps import apps

User = get_user_model()

logger = structlog.get_logger('tasks')


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


@shared_task(priority=1, time_limit=30)
def check_incoming(pk, count=0):
    """Функция проверки incoming в birpay"""
    try:
        logger.info(f'Проверка опера. Попытка {count + 1}')
        check = None
        IncomingCheck = apps.get_model('deposit', 'IncomingCheck')
        incoming_check = IncomingCheck.objects.get(pk=pk)
        logger.info(f'incoming_check: {incoming_check}')
        try:
            check = find_birpay_from_id(birpay_id=incoming_check.birpay_id)
        except Exception as err:
            count += 1
            logger.debug(f'Неудачных попыток: {count}')
            if count < 5:
                check_incoming.apply_async(kwargs={'pk': pk, 'count': count}, countdown=count * 15)
                return

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


@shared_task(priority=2, time_limit=60)
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
    withdraw_list = async_to_sync(get_birpay_withdraw)(limit=512)
    total_amount = 0
    results = []
    limit = 5
    count = 0
    WithdrawTransaction = apps.get_model('deposit.WithdrawTransaction')
    logger.info(f'Всего транзакций бирпай: {len(withdraw_list)}')
    for withdraw in withdraw_list:
        if count >= limit:
            break
        is_exists = WithdrawTransaction.objects.filter(withdraw_id=withdraw['id']).exists()
        logger.info(f'{withdraw["id"]} is_exists: {is_exists}')
        if not is_exists:
            # Если еще не брали в работу создадим на асупэй
            expired_month = expired_year = target_phone = card_data = None
            amount = float(withdraw.get('amount'))
            total_amount += amount
            wallet_id = withdraw.get('customerWalletId', '')
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
            }

            result = create_asu_withdraw(**withdraw_data)
            if result.get('status') == 'success':
                # Успешно создана
                try:
                    WithdrawTransaction.objects.create(
                        withdraw_id=withdraw['id'],
                        status=1,
                    )
                except Exception as err:
                    logger.warning(err)

                results.append(result)
                count += 1
    return results

