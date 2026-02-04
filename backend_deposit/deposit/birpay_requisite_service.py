"""
Единая точка обновления реквизита в Birpay.
Принимает requisite_id и словарь переопределений (overrides).
Все данные готовятся из модели RequsiteZajon; overrides задают только изменяемые поля.
Валидация номера карты (Луна, минимум 16 цифр) — в этом модуле; формы и API вызывают её через сервис.
Используется: форма /requisite-zajon/<id>/, API PUT /api/birpay/requisites/<id>/, Z-ASU форма.
"""
import re
import structlog
from django.shortcuts import get_object_or_404

from core.birpay_client import BirpayClient
from deposit.models import RequsiteZajon

logger = structlog.get_logger('deposit')


def luhn_check(card_number: str) -> bool:
    """
    Проверка номера карты по алгоритму Луна (Luhn).
    card_number — строка из 16 цифр (или число). Возвращает True, если номер валиден.
    """
    def digits_of(n):
        return [int(d) for d in str(n)]

    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d * 2))
    return checksum % 10 == 0


def validate_card_number_raw(raw_value: str, allow_empty: bool = True) -> str:
    """
    Валидация сырого значения номера карты (минимум 16 цифр, проверка Луна).
    Возвращает приведённую строку (strip); при ошибке валидации — ValueError с текстом.
    allow_empty: если True, пустая строка допустима (возвращается '').
    """
    raw = (raw_value or '').strip()
    if not raw:
        if allow_empty:
            return ''
        raise ValueError('Введите номер карты.')

    digits = re.sub(r'\D', '', raw)
    if len(digits) < 16:
        raise ValueError(
            f'В значении должно быть минимум 16 цифр для номера карты. Найдено: {len(digits)}.'
        )
    card_16 = digits[:16]
    if not luhn_check(card_16):
        raise ValueError(
            f'Номер карты {card_16} не прошёл проверку по алгоритму Луна. Проверьте правильность номера.'
        )
    return raw


def _merge_overrides(requisite: RequsiteZajon, overrides: dict) -> dict:
    """
    Собрать данные для Birpay из модели и overrides.
    overrides переопределяют только переданные ключи; agent_id=0 или None не переопределяют (берём из модели).
    """
    name = requisite.name
    if 'name' in overrides and overrides.get('name') not in (None, ''):
        name = str(overrides['name'])

    agent_id = requisite.agent_id
    if 'agent_id' in overrides and overrides.get('agent_id') not in (None, 0):
        agent_id = int(overrides['agent_id'])

    weight = requisite.weight
    if 'weight' in overrides:
        weight = int(overrides['weight'])

    card_number = (requisite.payload or {}).get('card_number', '') or requisite.card_number or ''
    if 'card_number' in overrides:
        raw_from_override = overrides['card_number']
        card_number = validate_card_number_raw(
            raw_from_override if raw_from_override is not None else '',
            allow_empty=True,
        )

    full_payload = dict(requisite.payload or {})
    full_payload['card_number'] = card_number

    active = overrides.get('active') if 'active' in overrides else None

    return {
        'name': name,
        'agent_id': agent_id,
        'weight': weight,
        'card_number': card_number,
        'full_payload': full_payload,
        'active': active,
        'refill_method_types': requisite.refill_method_types or [],
        'users': requisite.users or [],
        'payment_requisite_filter_id': requisite.payment_requisite_filter_id,
    }


def update_requisite_on_birpay(requisite_id: int, overrides: dict) -> dict:
    """
    Обновить реквизит в Birpay по ID и переопределениям.
    Данные (name, agent_id, weight, refill_method_types, users, payload) берутся из RequsiteZajon;
    overrides задают только изменяемые поля (например card_number, active).
    Возвращает результат BirpayClient (dict: success, status_code, data, error).
    """
    requisite = get_object_or_404(RequsiteZajon, pk=requisite_id)
    overrides = overrides or {}
    params = _merge_overrides(requisite, overrides)

    log = logger.bind(
        requisite_id=requisite_id,
        birpay_requisite_service='update_requisite_on_birpay',
        agent_id=params['agent_id'],
        users_count=len(params['users']),
    )
    log.info(
        'Birpay requisite service: подготовка данных из модели',
        name=params['name'],
        card_number_len=len(params['card_number']),
    )

    client = BirpayClient()
    if 'active' in overrides and set(overrides.keys()) <= {'active'}:
        result = client.set_requisite_active(
            requisite_id,
            bool(overrides['active']),
            name=params['name'],
            agent_id=params['agent_id'],
            weight=params['weight'],
            card_number=params['card_number'],
            refill_method_types=params['refill_method_types'],
            users=params['users'],
            payment_requisite_filter_id=params['payment_requisite_filter_id'],
        )
    else:
        result = client.update_requisite(
            requisite_id,
            name=params['name'],
            agent_id=params['agent_id'],
            weight=params['weight'],
            card_number=params['card_number'],
            active=params['active'],
            refill_method_types=params['refill_method_types'],
            users=params['users'],
            payment_requisite_filter_id=params['payment_requisite_filter_id'],
            full_payload=params['full_payload'],
        )
    return result
