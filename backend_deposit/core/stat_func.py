from django.db.models import Sum, Count, Max

from deposit.models import Incoming


def bad_ids():
    """
    Функция аходит id которые не надо учитывать:
    m10 с отправителем картой
    m10 sender с полным номером телефона кроме 00 000 00 00
    :return:
    """

    # m10 с отправителем картой
    finded_sender_as_card = Incoming.objects.filter(
        type__in=('m10', 'm10_short'),
        sender__iregex=r'\d\d\d\d \d\d.*\d\d\d\d',
    ).all()

    # m10 sender с полным номером телефона кроме 00 000 00 00
    finded_sender_full_phone = Incoming.objects.filter(
        type__in=('m10', 'm10_short'),
        sender__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d',
    ).exclude(
        sender__iregex=r'00 000 00 00',
    ).all()

    result = finded_sender_as_card | finded_sender_full_phone
    return result


def cards_report() -> dict:
    # Возвращает словарь со статистикой по картам
    cards = Incoming.objects.filter(pay__gt=0).all().values('recipient').annotate(
        count=Count('pk'),
        sum=Sum('pay'),
        last_date=Max('register_date'),
        last_id=Max('pk')
    ).order_by('-last_date')
    return cards
