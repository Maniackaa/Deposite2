import json
import requests
import structlog

from django.conf import settings
from core.global_func import hash_gen, send_message_tg
from users.models import Options

logger = structlog.get_logger('deposit')


class ASUAccountManager:
    """
    Менеджер для работы с разными аккаунтами ASU (ASU и Z-ASU).
    Управляет токенами для каждого аккаунта отдельно.
    """
    
    # Типы аккаунтов
    ACCOUNT_ASU = 'asu'
    ACCOUNT_Z_ASU = 'z_asu'
    
    def __init__(self, account_type: str = ACCOUNT_ASU):
        """
        Инициализация менеджера аккаунта.
        
        Args:
            account_type: Тип аккаунта ('asu' или 'z_asu'). По умолчанию 'asu'.
        """
        if account_type not in [self.ACCOUNT_ASU, self.ACCOUNT_Z_ASU]:
            raise ValueError(f"Неизвестный тип аккаунта: {account_type}. Используйте '{self.ACCOUNT_ASU}' или '{self.ACCOUNT_Z_ASU}'")
        
        self.account_type = account_type
        self.token_file = settings.BASE_DIR / f'token_{account_type}.txt'
        self.logger = logger.bind(account_type=account_type)
    
    def _get_credentials(self):
        """Получает логин и пароль для текущего аккаунта."""
        options = Options.load()
        if self.account_type == self.ACCOUNT_ASU:
            return options.asu_login, options.asu_password
        elif self.account_type == self.ACCOUNT_Z_ASU:
            return options.z_asu_login, options.z_asu_password
    
    def get_new_token(self) -> str:
        """
        Получение нового токена по логину и паролю.
        
        Returns:
            str: Access токен
        """
        self.logger.info('Получение первичного токена по логину')
        try:
            login, password = self._get_credentials()
            url = f"{settings.ASU_HOST}/api/v1/token/"
            payload = json.dumps({
                "username": login,
                "password": password
            })
            headers = {'Content-Type': 'application/json'}
            response = requests.request("POST", url, headers=headers, data=payload, timeout=5)
            self.logger.info(f'response.status_code: {response.status_code}')
            
            if response.status_code != 200:
                raise Exception(f"Ошибка получения токена: {response.status_code} {response.text}")
            
            token_dict = response.json()
            token_data = {
                'refresh': token_dict.get('refresh', ''),
                'access': token_dict.get('access', '')
            }
            token = token_dict.get('access')
            
            with open(self.token_file, 'w') as file:
                file.write(json.dumps(token_data))
            
            self.logger.info(f'Токен успешно получен и сохранен')
            return token
        except Exception as err:
            self.logger.error(f'Ошибка получения токена по логину/паролю: {err}')
            raise err
    
    def get_token(self) -> str:
        """
        Получение токена. Если токен не существует, получает новый.
        
        Returns:
            str: Access токен
        """
        if not self.token_file.exists():
            return self.get_new_token()
        
        try:
            with open(self.token_file, 'r') as file:
                token_data = json.loads(file.read())
                token = token_data.get('access', '')
                if not token:
                    return self.get_new_token()
                return token
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            return self.get_new_token()
    
    def get_headers(self) -> dict:
        """
        Получает заголовки с токеном для запросов.
        
        Returns:
            dict: Заголовки с Authorization Bearer токеном
        """
        token = self.get_token()
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
    
    def make_request(self, method: str, url: str, json_data: dict = None, **kwargs) -> requests.Response:
        """
        Выполняет HTTP запрос с автоматическим обновлением токена при 401 ошибке.
        
        Args:
            method: HTTP метод ('GET', 'POST', 'PUT', 'DELETE')
            url: URL для запроса
            json_data: JSON данные для отправки
            **kwargs: Дополнительные параметры для requests
            
        Returns:
            requests.Response: Ответ от сервера
        """
        headers = self.get_headers()
        
        if json_data:
            kwargs['json'] = json_data
        
        response = requests.request(method, url, headers=headers, timeout=kwargs.get('timeout', 10), **kwargs)
        
        # Если получили 401, обновляем токен и повторяем запрос
        if response.status_code == 401:
            self.logger.warning('Получен 401, обновляем токен и повторяем запрос')
            headers = {
                'Authorization': f'Bearer {self.get_new_token()}',
                'Content-Type': 'application/json'
            }
            response = requests.request(method, url, headers=headers, timeout=kwargs.get('timeout', 10), **kwargs)
        
        return response


# Глобальные экземпляры менеджеров для обратной совместимости
_default_manager = ASUAccountManager(ASUAccountManager.ACCOUNT_ASU)
_z_asu_manager = ASUAccountManager(ASUAccountManager.ACCOUNT_Z_ASU)


# ============================================================================
# Функции для обратной совместимости (используют ASU аккаунт по умолчанию)
# ============================================================================

def get_new_asu_token():
    """
    Получение нового токена для ASU аккаунта.
    Сохранена для обратной совместимости.
    """
    return _default_manager.get_new_token()


def get_asu_token() -> str:
    """
    Получение токена для ASU аккаунта.
    Сохранена для обратной совместимости.
    """
    return _default_manager.get_token()


def send_sms_code(payment_id, sms_code, transaction_id=None, account_type: str = None) -> dict:
    """
    Отправка SMS-кода через API v1.
    
    Args:
        payment_id: ID платежа
        sms_code: SMS код
        transaction_id: ID транзакции (опционально)
        account_type: Тип аккаунта ('asu' или 'z_asu'). По умолчанию 'asu'.
    
    Returns:
        dict: Ответ от сервера или None при ошибке
    """
    manager = _default_manager if account_type is None else ASUAccountManager(account_type)
    
    if transaction_id:
        logger = manager.logger.bind(transaction_id=transaction_id, payment_id=payment_id)
    else:
        logger = manager.logger.bind(payment_id=payment_id)
    
    try:
        logger.debug(f'Передача sms_code {payment_id} на asu-pay')
        url = f'{settings.ASU_HOST}/api/v1/payment/{payment_id}/send_sms_code/'
        json_data = {'sms_code': sms_code}
        
        response = manager.make_request('PUT', url, json_data=json_data)
        
        logger.debug(f'response {payment_id}: {response.status_code} {response.reason} {response.text}')
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f'Ошибка отправки SMS-кода: {response.status_code} {response.text}')
            return None
    except Exception as err:
        logger.debug(f'Ошибка при передачи sms_code {payment_id}: {err}')
        return None


def create_payment_v2(payment_data, card_data=None, account_type: str = None):
    """
    Создание платежа через API v2 с данными карты.
    Объединяет функциональность create_payment и send_card_data из v1.
    
    Args:
        payment_data: Данные платежа
        card_data: Данные карты (опционально)
        account_type: Тип аккаунта ('asu' или 'z_asu'). По умолчанию 'asu'.
    
    Returns:
        dict: Результат создания платежа или None при ошибке
    """
    manager = _default_manager if account_type is None else ASUAccountManager(account_type)
    logger = manager.logger
    
    try:
        logger.debug(f'Создание заявки Payment на asu-pay v2: {payment_data}')

        # Подготавливаем данные для API v2
        v2_payment_data = {
            'merchant': payment_data['merchant'],
            'order_id': payment_data['order_id'],
            'amount': payment_data['amount'],
            'pay_type': payment_data['pay_type'],
            'currency_code': payment_data.get('currency_code', 'AZN'),
            'user_login': payment_data.get('user_login', ''),
        }

        # Добавляем данные карты если они есть
        if card_data:
            v2_payment_data['card_data'] = card_data

        url = f'{settings.ASU_HOST}/api/v2/payment/'
        response = manager.make_request('POST', url, json_data=v2_payment_data)

        logger.debug(f'response: {response.status_code} {response.reason} {response.text}')

        if response.status_code == 201:
            response_data = response.json()
            return {
                'payment_id': response_data['id'],
                'sms_required': response_data.get('sms_required', False),
                'instruction': response_data.get('instruction', '')
            }
        else:
            text = f'Ошибка создания платежа на АСУ v2. response: {response.status_code} {response.reason} {response.text}'
            logger.error(text)
            send_message_tg(message=text, chat_ids=settings.ALARM_IDS)
            return None

    except Exception as err:
        logger.debug(f'Ошибка при создании payment v2: {err}')
        return None


def send_sms_code_v2(payment_id, sms_code, transaction_id=None, account_type: str = None):
    """
    Отправка SMS-кода через API v2.
    
    Args:
        payment_id: ID платежа
        sms_code: SMS код
        transaction_id: ID транзакции (опционально)
        account_type: Тип аккаунта ('asu' или 'z_asu'). По умолчанию 'asu'.
    
    Returns:
        dict: Ответ от сервера или None при ошибке
    """
    manager = _default_manager if account_type is None else ASUAccountManager(account_type)
    
    if transaction_id:
        logger = manager.logger.bind(transaction_id=transaction_id, payment_id=payment_id)
    else:
        logger = manager.logger.bind(payment_id=payment_id)

    try:
        logger.debug(f'Передача sms_code {payment_id} на asu-pay v2')

        url = f'{settings.ASU_HOST}/api/v2/payment/{payment_id}/send_sms_code/'
        json_data = {'sms_code': sms_code}
        
        response = manager.make_request('PUT', url, json_data=json_data)

        logger.debug(f'response {payment_id}: {response.status_code} {response.reason} {response.text}')

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f'Ошибка отправки SMS-кода: {response.status_code} {response.text}')
            return None

    except Exception as err:
        logger.debug(f'Ошибка при передачи sms_code {payment_id}: {err}')
        return None


def create_asu_withdraw(withdraw_id, amount, card_data, target_phone, payload: dict, account_type: str = None):
    """
    Создание выплаты через ASU API.
    
    Args:
        withdraw_id: ID выплаты
        amount: Сумма
        card_data: Данные карты
        target_phone: Телефон получателя
        payload: Дополнительные данные
        account_type: Тип аккаунта ('asu' или 'z_asu'). По умолчанию 'asu'.
    
    Returns:
        dict: Результат создания выплаты
    """
    manager = _default_manager if account_type is None else ASUAccountManager(account_type)
    logger = manager.logger.bind(birpay_withdraw_id=withdraw_id)
    
    result = {}
    
    try:
        options = Options.load()
        
        # Для Z-ASU может потребоваться отдельный merchant_id и secret
        # Пока используем те же, что и для ASU
        merchant_id = options.asu_merchant_id
        text = f'{merchant_id}{target_phone or card_data.get("card_number")}{int(round(amount, 0))}'
        secret = options.asu_secret
        signature = hash_gen(text, secret)
        
        withdraw_data = {
            "merchant": f'{merchant_id}',
            "withdraw_id": withdraw_id,
            "payload": payload,
            "amount": f'{amount}',
            "currency_code": "AZN",
            "signature": signature,
        }
        
        if card_data:
            withdraw_data['card_data'] = card_data
        if target_phone:
            withdraw_data['target_phone'] = target_phone
        
        url = f'{settings.ASU_HOST}/api/v1/withdraw/'
        logger.info(f'Отправка на асупэй birpay_withdraw_data: {withdraw_data}')
        
        response = manager.make_request('POST', url, json_data=withdraw_data)
        
        logger.debug(f'response: {response.status_code} {response.reason} {response.text}')
        
        if response.status_code == 201:
            result = response.json()
            logger.debug(f'Успешно создан на Asupay')
        else:
            logger.warning(f'response: {response.status_code} {response.reason} {response.text}')
        
        logger.info(f'Результат: {result}')
        return result
        
    except Exception as err:
        result = {'withdraw_id': withdraw_id, 'status': 'error', 'error': str(err)}
        logger.debug(f'Ошибка при создании withdraw: {err}')
        return result


# ============================================================================
# Функции для работы с Z-ASU аккаунтом
# ============================================================================

def get_z_asu_token() -> str:
    """
    Получение токена для Z-ASU аккаунта.
    
    Returns:
        str: Access токен
    """
    return _z_asu_manager.get_token()


def get_new_z_asu_token() -> str:
    """
    Получение нового токена для Z-ASU аккаунта.
    
    Returns:
        str: Access токен
    """
    return _z_asu_manager.get_new_token()


def create_z_asu_payment_v2(payment_data, card_data=None):
    """
    Создание платежа через API v2 для Z-ASU аккаунта.
    
    Args:
        payment_data: Данные платежа
        card_data: Данные карты (опционально)
    
    Returns:
        dict: Результат создания платежа или None при ошибке
    """
    return create_payment_v2(payment_data, card_data, account_type=ASUAccountManager.ACCOUNT_Z_ASU)


def send_z_asu_sms_code_v2(payment_id, sms_code, transaction_id=None):
    """
    Отправка SMS-кода через API v2 для Z-ASU аккаунта.
    
    Args:
        payment_id: ID платежа
        sms_code: SMS код
        transaction_id: ID транзакции (опционально)
    
    Returns:
        dict: Ответ от сервера или None при ошибке
    """
    return send_sms_code_v2(payment_id, sms_code, transaction_id, account_type=ASUAccountManager.ACCOUNT_Z_ASU)


def create_z_asu_withdraw(withdraw_id, amount, card_data, target_phone, payload: dict):
    """
    Создание выплаты через ASU API для Z-ASU аккаунта.
    
    Args:
        withdraw_id: ID выплаты
        amount: Сумма
        card_data: Данные карты
        target_phone: Телефон получателя
        payload: Дополнительные данные
    
    Returns:
        dict: Результат создания выплаты
    """
    return create_asu_withdraw(withdraw_id, amount, card_data, target_phone, payload, account_type=ASUAccountManager.ACCOUNT_Z_ASU)


# ============================================================================
# Функции для логики Z-ASU
# ============================================================================

def should_send_to_z_asu(card_number: str) -> bool:
    """
    Проверка условия для отправки BirpayOrder на Z-ASU.
    Логика Z-ASU: проверяет, есть ли карта в реквизитах Zajon с опцией "Работает на ASU".
    
    Args:
        card_number: Номер карты (может содержать пробелы)
    
    Returns:
        bool: True если нужно отправить на Z-ASU, False иначе
    """
    if not card_number:
        return False
    
    # Убираем пробелы и дефисы для сравнения
    cleaned_card = card_number.replace(' ', '').replace('-', '')
    
    # Проверяем, есть ли реквизит с этой картой и опцией works_on_asu=True
    from deposit.models import RequsiteZajon
    
    # Получаем все реквизиты с works_on_asu=True и проверяем карту в Python
    # Это позволяет нормализовать карты независимо от формата хранения в базе
    all_z_asu_requisites = RequsiteZajon.objects.filter(works_on_asu=True).values('id', 'name', 'card_number')
    
    for req in all_z_asu_requisites:
        req_card = req['card_number']
        if req_card:
            # Нормализуем карту из базы для сравнения (убираем пробелы и дефисы)
            normalized_req_card = req_card.replace(' ', '').replace('-', '')
            if normalized_req_card == cleaned_card:
                logger.info(f'Логика Z-ASU: найдена карта {cleaned_card} в реквизите {req["id"]} ({req["name"]}) с works_on_asu=True')
                return True
    
    # Логируем для отладки
    logger.debug(f'Логика Z-ASU: не найдено реквизитов с картой {cleaned_card} и works_on_asu=True. Всего реквизитов с works_on_asu=True: {all_z_asu_requisites.count()}')
    
    return False


def send_birpay_order_to_z_asu(birpay_order) -> dict:
    """
    Отправка BirpayOrder на ASU для Z-ASU.
    Логика Z-ASU: отправляет данные BirpayOrder на специальный API endpoint ASU.
    
    Args:
        birpay_order: Объект BirpayOrder
    
    Returns:
        dict: Результат отправки с ключами:
            - success: bool - успешность операции
            - payment_id: int - ID созданного Payment (если успешно)
            - error: str - описание ошибки (если неуспешно)
    """
    manager = _z_asu_manager
    logger = manager.logger.bind(
        birpay_order_id=birpay_order.id,
        birpay_id=birpay_order.birpay_id,
        card_number=birpay_order.card_number
    )
    
    try:
        logger.info('Отправка BirpayOrder на Z-ASU API')
        
        # Подготавливаем данные для отправки
        request_data = {
            'birpay_order_id': birpay_order.id,
            'birpay_id': birpay_order.birpay_id,
            'merchant_transaction_id': birpay_order.merchant_transaction_id,
            'merchant_user_id': birpay_order.merchant_user_id,
            'amount': float(birpay_order.amount),
            'card_number': birpay_order.card_number,
            'currency_code': 'AZN',
        }
        
        # Отправляем на специальный endpoint для Z-ASU
        url = f'{settings.ASU_HOST}/api/v2/z-asu/create-payment/'
        response = manager.make_request('POST', url, json_data=request_data)
        
        logger.debug(f'Z-ASU API response: {response.status_code} {response.reason} {response.text}')
        
        # Успех: 201 Created или 200 OK (на случай прокси/вариаций)
        if response.status_code in (200, 201):
            response_data = response.json()
            payment_id = response_data.get('payment_id') or response_data.get('id')
            if payment_id is not None:
                payment_id = str(payment_id)
            logger.info(f'Успешно создан Payment на Z-ASU: payment_id={payment_id}')
            return {
                'success': True,
                'payment_id': payment_id,
            }
        else:
            error_text = response.text or response.reason or 'Unknown error'
            logger.error(f'Ошибка создания Payment на Z-ASU: {response.status_code} {error_text}')
            return {
                'success': False,
                'error': f'HTTP {response.status_code}: {error_text}',
            }
            
    except Exception as err:
        logger.error(f'Исключение при отправке BirpayOrder на Z-ASU: {err}', exc_info=True)
        return {
            'success': False,
            'error': str(err),
        }


def confirm_z_asu_payment(payment_id: str) -> dict:
    """
    Подтверждение Payment на Z-ASU API по payment_id (точный поиск).
    Логика Z-ASU: подтверждает Payment через endpoint confirm-payment.
    
    Args:
        payment_id: UUID Payment на ASU
    
    Returns:
        dict: success, payment_id или error
    """
    manager = _z_asu_manager
    logger = manager.logger.bind(payment_id=payment_id, z_asu_api=True)
    try:
        logger.info(f'Подтверждение Payment {payment_id} на Z-ASU API (confirm-payment)')
        request_data = {'payment_id': str(payment_id)}
        url = f'{settings.ASU_HOST}/api/v2/z-asu/confirm-payment/'
        response = manager.make_request('POST', url, json_data=request_data)
        logger.debug(f'Z-ASU API confirm-payment response: {response.status_code} {response.reason} {response.text}')
        if response.status_code == 200:
            response_data = response.json()
            pid = response_data.get('payment_id')
            logger.info(f'Успешно подтвержден Payment на Z-ASU: payment_id={pid}')
            return {'success': True, 'payment_id': pid}
        error_text = response.text or response.reason or 'Unknown error'
        logger.error(f'Ошибка подтверждения Payment на Z-ASU: {response.status_code} {error_text}')
        return {'success': False, 'error': f'HTTP {response.status_code}: {error_text}'}
    except Exception as err:
        logger.error(f'Исключение при подтверждении Payment на Z-ASU: {err}', exc_info=True)
        return {'success': False, 'error': str(err)}


def decline_z_asu_payment(payment_id: str) -> dict:
    """
    Отклонение Payment на Z-ASU API по payment_id (точный поиск).
    Логика Z-ASU: отклоняет Payment через endpoint decline-payment (статус -1).
    
    Args:
        payment_id: UUID Payment на ASU
    
    Returns:
        dict: success, payment_id или error
    """
    manager = _z_asu_manager
    logger = manager.logger.bind(payment_id=payment_id, z_asu_api=True)
    try:
        logger.info(f'Отклонение Payment {payment_id} на Z-ASU API (decline-payment)')
        request_data = {'payment_id': str(payment_id)}
        url = f'{settings.ASU_HOST}/api/v2/z-asu/decline-payment/'
        response = manager.make_request('POST', url, json_data=request_data)
        logger.debug(f'Z-ASU API decline-payment response: {response.status_code} {response.reason} {response.text}')
        if response.status_code == 200:
            response_data = response.json()
            pid = response_data.get('payment_id')
            logger.info(f'Успешно отклонен Payment на Z-ASU: payment_id={pid}')
            return {'success': True, 'payment_id': pid}
        error_text = response.text or response.reason or 'Unknown error'
        logger.error(f'Ошибка отклонения Payment на Z-ASU: {response.status_code} {error_text}')
        return {'success': False, 'error': f'HTTP {response.status_code}: {error_text}'}
    except Exception as err:
        logger.error(f'Исключение при отклонении Payment на Z-ASU: {err}', exc_info=True)
        return {'success': False, 'error': str(err)}


def confirm_z_asu_transaction(merchant_transaction_id: str) -> dict:
    """
    Подтверждение транзакции на Z-ASU API по merchant_transaction_id (fallback).
    Логика Z-ASU: подтверждает транзакцию по merchant_transaction_id через Z-ASU API endpoint.
    Endpoint сам ищет Payment через ORM и подтверждает его.
    
    Args:
        merchant_transaction_id: merchant_transaction_id (order_id) из BirpayOrder
    
    Returns:
        dict: Результат подтверждения с ключами:
            - success: bool - успешность операции
            - payment_id: str - UUID Payment (если успешно)
            - error: str - описание ошибки (если неуспешно)
    """
    manager = _z_asu_manager
    logger = manager.logger.bind(
        merchant_transaction_id=merchant_transaction_id,
        z_asu_api=True
    )
    
    try:
        logger.info(f'Подтверждение транзакции {merchant_transaction_id} на Z-ASU API')
        
        # Подготавливаем данные для подтверждения транзакции
        request_data = {
            'merchant_transaction_id': merchant_transaction_id,
        }
        
        # Отправляем на endpoint для подтверждения транзакции
        # Endpoint сам найдет Payment через ORM по order_id и source='z_asu'
        url = f'{settings.ASU_HOST}/api/v2/z-asu/confirm-transaction/'
        response = manager.make_request('POST', url, json_data=request_data)
        
        logger.debug(f'Z-ASU API confirm-transaction response: {response.status_code} {response.reason} {response.text}')
        
        if response.status_code == 200:
            response_data = response.json()
            payment_id = response_data.get('payment_id')
            logger.info(f'Успешно подтверждена транзакция на Z-ASU: merchant_transaction_id={merchant_transaction_id}, payment_id={payment_id}')
            return {
                'success': True,
                'payment_id': payment_id,
            }
        else:
            error_text = response.text or response.reason or 'Unknown error'
            logger.error(f'Ошибка подтверждения транзакции на Z-ASU: {response.status_code} {error_text}')
            return {
                'success': False,
                'error': f'HTTP {response.status_code}: {error_text}',
            }
            
    except Exception as err:
        logger.error(f'Исключение при подтверждении транзакции на Z-ASU: {err}', exc_info=True)
        return {
            'success': False,
            'error': str(err),
        }


def decline_z_asu_transaction(merchant_transaction_id: str) -> dict:
    """
    Отклонение транзакции на Z-ASU API.
    Логика Z-ASU: отклоняет транзакцию по merchant_transaction_id через Z-ASU API endpoint.
    Endpoint ищет Payment через ORM и переводит в статус -1 (Declined).
    
    Args:
        merchant_transaction_id: merchant_transaction_id (order_id) из BirpayOrder
    
    Returns:
        dict: Результат с ключами:
            - success: bool - успешность операции
            - payment_id: str - UUID Payment (если успешно)
            - error: str - описание ошибки (если неуспешно)
    """
    manager = _z_asu_manager
    logger = manager.logger.bind(
        merchant_transaction_id=merchant_transaction_id,
        z_asu_api=True
    )
    
    try:
        logger.info(f'Отклонение транзакции {merchant_transaction_id} на Z-ASU API')
        
        request_data = {'merchant_transaction_id': merchant_transaction_id}
        url = f'{settings.ASU_HOST}/api/v2/z-asu/decline-transaction/'
        response = manager.make_request('POST', url, json_data=request_data)
        
        logger.debug(f'Z-ASU API decline-transaction response: {response.status_code} {response.reason} {response.text}')
        
        if response.status_code == 200:
            response_data = response.json()
            payment_id = response_data.get('payment_id')
            logger.info(f'Успешно отклонена транзакция на Z-ASU: merchant_transaction_id={merchant_transaction_id}, payment_id={payment_id}')
            return {
                'success': True,
                'payment_id': payment_id,
            }
        else:
            error_text = response.text or response.reason or 'Unknown error'
            logger.error(f'Ошибка отклонения транзакции на Z-ASU: {response.status_code} {error_text}')
            return {
                'success': False,
                'error': f'HTTP {response.status_code}: {error_text}',
            }
            
    except Exception as err:
        logger.error(f'Исключение при отклонении транзакции на Z-ASU: {err}', exc_info=True)
        return {
            'success': False,
            'error': str(err),
        }


if __name__ == '__main__':
    pass
