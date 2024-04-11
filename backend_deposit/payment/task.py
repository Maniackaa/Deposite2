import json

import aiohttp
import structlog

from payment.models import Payment, Shop

logger = structlog.get_logger(__name__)


async def send_payment_confirm(payment: Payment):
    try:
        shop: Shop = payment.shop
        url = shop.pay_success_endpoint
        logger.info(f'Подтверждение платежа {payment.id} на {url}')
        data = {
            "id": payment.id,
            "order_id": payment.order_id,
            "user_login": payment.user_login,
            "amount": payment.amount,
            "create_at": payment.create_at.timestamp(),
            "status": payment.status,
            "confirmed_time": payment.confirmed_time.timestamp(),
            "confirmed_amount": payment.confirmed_amount,
            "secret": payment.shop.secret
        }
        logger.debug(json.dumps(data))
        headers = {"Content-Type": "application/json"}
        params = {}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, params=params, ssl=False,
                                    data=json.dumps(data)) as response:
                status = response.status
                logger.info(f'Статус ответа {payment.id}: {status}')
                payment.response_status_code = status
                payment.save()

    except Exception as err:
        logger.error(err)
        raise err
