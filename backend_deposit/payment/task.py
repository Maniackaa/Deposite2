import requests
import structlog

from payment.models import Payment, Shop

logger = structlog.get_logger(__name__)


async def send_payment_confirm(payment: Payment):
    shop: Shop = payment.shop
    url = shop.pay_success_endpoint
    logger.info(f'Requests to url: {url}')
    data = {
        "order_id": payment.order_id,
        "user_login": payment.user_login,
        "amount": payment.amount,
        "create_at": payment.create_at,
        "status": payment.status,
        "confirmed_time": payment.confirmed_time.timestamp(),
        "confirmed_amount": payment.confirmed_amount,
        "secret": payment.shop.secret
    }
    try:
        result = requests.post(url, data=data)
        logger.info(result.status_code)
    except Exception as err:
        logger.error(err)
