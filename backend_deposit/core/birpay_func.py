import asyncio
import datetime
import os
from pprint import pprint

import requests
import structlog

from backend_deposit.settings import BASE_DIR


logger = structlog.get_logger('deposit')

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    'Content-Type': 'application/json;charset=utf-8',
    'Origin': 'https://birpay-gate.com',
    'Connection': 'keep-alive',
    'Referer': 'https://birpay-gate.com/login',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
}


def get_new_token(username=os.getenv('BIRPAY_LOGIN'), password=os.getenv('BIRPAY_PASSWORD')):

    json_data = {
        'username': username,
        'password': password,
    }

    response = requests.post('https://birpay-gate.com/api/login_check', headers=headers, json=json_data)
    if response.status_code == 200:
        token = response.json().get('token')
        with open(BASE_DIR / 'token.txt', 'w') as file:
            file.write(token)
        return token


def read_token():
    token_file = BASE_DIR / 'token.txt'
    if not token_file.exists():
        get_new_token()
    with open(BASE_DIR / 'token.txt', 'r') as file:
        token = file.read()
        return token


def find_birpay_from_id(birpay_id, results=1):
    """Поиск заявки пополнения по merchant_transaction_id. Обёртка над BirpayClient.find_refill_order()."""
    from core.birpay_client import BirpayClient
    client = BirpayClient()
    row = client.find_refill_order(str(birpay_id).strip(), results=results)
    if not row:
        return None
    return {
        'transaction_id': row.get('merchantTransactionId'),
        'status': row.get('status'),
        'sender': row.get('customerWalletId'),
        'requisite': row.get('paymentRequisite'),
        'created_time': datetime.datetime.fromisoformat(row.get('createdAt')) if row.get('createdAt') else None,
        'pay': float(row.get('amount', 0)),
        'operator': row.get('operator'),
    }


def get_refill_order_raw(merchant_tx_id, results=1):
    """Поиск заявки пополнения по Merchant Transaction ID. Обёртка над BirpayClient.find_refill_order()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().find_refill_order(merchant_tx_id, results=results)


def get_payout_order_raw(merchant_tx_id, results=1):
    """Поиск заявки выплаты по Merchant Transaction ID. Обёртка над BirpayClient.find_payout_order()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().find_payout_order(merchant_tx_id, results=results)


def get_birpays(results=512) -> list:
    """Получить список заявок на пополнение. Обёртка над BirpayClient.get_refill_orders()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().get_refill_orders(limit=results)


def find_birpay_from_merch_transaction_id(merch_transaction_id, results=1):
    # это выплаты payout
    merch_transaction_id = str(merch_transaction_id).strip()
    try:
        token = read_token()
        cookies = None
        headers['Authorization'] = f'Bearer {token}'

        json_data = {
            'filter': {
                'merchantTransactionId': merch_transaction_id
            },
            'sort': {},
            'limit': {
                'lastId': 0,
                'maxResults': results,
                'descending': True,
            },
        }

        response = requests.post(
            'https://birpay-gate.com/api/operator/payout_order/find',
            cookies=cookies,
            headers=headers,
            json=json_data,
        )
        logger.debug(f'find_birpay_from_id: {response.status_code}')
        if response.status_code == 401:
            # Обновление токена
            token = get_new_token()
            headers['Authorization'] = f'Bearer {token}'
            response = requests.post(
                'https://birpay-gate.com/api/operator/payout_order/find',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )

        if response.status_code == 200:
            data = response.json()
            # for key, val in data[0].items():
            #     print(f'{key}: {val}')
            #     print('-------------------\n')
            logger.info(f'Получено по merch_transaction_id {merch_transaction_id}: {data}')
            print(f'Получено по merch_transaction_id {merch_transaction_id}: {data}')
            row = data[0]
            transaction_id = row.get('merchantTransactionId')
            status = row.get('status')
            sender = row.get('customerWalletId')
            requisite = row.get('paymentRequisite')
            pay = float(row.get('amount'))
            operator = row.get('operator')
            created_time = datetime.datetime.fromisoformat(row.get('createdAt'))
            result = {
                    'transaction_id': transaction_id,
                    'status': status,
                    'sender': sender,
                    'requisite': requisite,
                    'created_time': created_time,
                    'pay': pay,
                    'operator': operator,
                }
            return data
    except Exception as err:
        logger.error(err)
        raise err


def send_request_birpay(url, method='POST', json_data=None) -> requests.Response:
    """Низкоуровневый запрос (для обратной совместимости). Предпочтительно использовать BirpayClient."""
    token = read_token()
    headers['Authorization'] = f'Bearer {token}'
    if method == "POST":
        response = requests.post(url, headers=headers, json=json_data)
    else:
        response = requests.put(url, headers=headers, json=json_data)
    if response.status_code == 401:
        token = get_new_token()
        headers['Authorization'] = f'Bearer {token}'
        return send_request_birpay(url, method=method, json_data=json_data)
    logger.info(f'response.status_code: {response.status_code}')
    return response


def change_amount_birpay(pk: int, amount: float) -> requests.Response:
    """Изменить сумму заявки на пополнение. Обёртка над BirpayClient.change_refill_amount()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().change_refill_amount(pk, amount)


def approve_birpay_refill(pk: int) -> requests.Response:
    """Подтвердить заявку на пополнение. Обёртка над BirpayClient.approve_refill()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().approve_refill(pk)

# ------------------- Payout (выплаты), через BirpayClient ------------------
async def get_birpay_withdraw(limit=512):
    """Список заявок на выплату (status=0). Обёртка над BirpayClient.get_payout_orders()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().get_payout_orders(limit=limit)


def approve_birpay_withdraw(withdraw_id, transaction_id):
    """Подтвердить заявку на выплату. Обёртка над BirpayClient.approve_payout()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().approve_payout(withdraw_id, transaction_id)


def decline_birpay_withdraw(withdraw_id, transaction_id):
    """Отклонить заявку на выплату. Обёртка над BirpayClient.decline_payout()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().decline_payout(withdraw_id, reason='err')

#### Запросы для получения реквизитов (через BirpayClient) #############
def get_payment_requisite_data():
    """Получить все реквизиты Birpay. Обёртка над BirpayClient.get_requisites()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().get_requisites()


def update_payment_requisite_data(
    requisite_id: int,
    *,
    name: str,
    agent_id: int,
    weight: int,
    card_number: str,
    active: bool | None = None,
    refill_method_types: list | None = None,
    users: list | None = None,
    payment_requisite_filter_id: int | None = None,
    full_payload: dict | None = None,
):
    """Обновление реквизита (номер карты и др.). Обёртка над BirpayClient.update_requisite()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().update_requisite(
        requisite_id,
        name=name,
        agent_id=agent_id,
        weight=weight,
        card_number=card_number,
        active=active,
        refill_method_types=refill_method_types,
        users=users,
        payment_requisite_filter_id=payment_requisite_filter_id,
        full_payload=full_payload,
    )


def set_payment_requisite_active(
    requisite_id: int,
    active: bool,
    *,
    name: str,
    agent_id: int,
    weight: int,
    card_number: str,
    refill_method_types: list | None = None,
    users: list | None = None,
    payment_requisite_filter_id: int | None = None,
):
    """Изменение статуса активности реквизита. Обёртка над BirpayClient.set_requisite_active()."""
    from core.birpay_client import BirpayClient
    return BirpayClient().set_requisite_active(
        requisite_id,
        active,
        name=name,
        agent_id=agent_id,
        weight=weight,
        card_number=card_number,
        refill_method_types=refill_method_types,
        users=users,
        payment_requisite_filter_id=payment_requisite_filter_id,
    )


async def main():
    token = get_new_token()
    print(token)
    # result = get_payment_requisite_data()
    # pprint(result)
    # res = update_payment_requisite_data(
    #     1480,
    #     name='Agent_Zajon_AZN_azcashier5',
    #     agent_id=1184,
    #     weight=200,
    #     card_number='5261633335656873',
    #     refill_method_types=[{'id': 127, 'name': 'AZN_azcashier_5_birpay'}],
    #     users=[{'id': 1254}],
    #     payment_requisite_filter_id=468,
    # )
    # print(res)

    #13691648
    birpay = find_birpay_from_id('1029930386')
    print(birpay)
    # t = find_birpay_from_merch_transaction_id('1029928944')
    # pprint(t)
    # t = find_birpay_from_merch_transaction_id('889136356')
    # pprint(t)
    # withdraw_list = await get_birpay_withdraw(limit=10)
    # pprint(withdraw_list)
    # r = change_amount_birpay(pk=888308930, amount=1)
    # print(r.status_code)
    # print(r.text)
    # r = approve_birpay_refill(pk=888308930)
    # print(r.status_code)
    # print(r.text)
    # print(len(withdraw_list))
    # ids = "VALUES "
    # wlist = []
    # for w in withdraw_list:
    #     ids += f"('{w['id']}'), "
    #     wlist.append(w['id'])

    # print(ids)
    # withdraw_id = 13864000
    # transaction_id = 755119964
    # res = approve_birpay_withdraw(withdraw_id, transaction_id)
    # print(res)
    # print(wlist)

    # birpays = get_birpays()
    # pprint(birpays)
    # row = birpays[0]
    # print(row)
    # for row in birpays:
    #     print(row.get('paymentRequisite').get('payload'))
    # esult = process_birpay_order(row)

if __name__ == '__main__':
    asyncio.run(main())