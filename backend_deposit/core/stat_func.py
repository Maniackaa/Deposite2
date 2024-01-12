import datetime
import logging
from dataclasses import dataclass

from django.db.models import Sum, Count, Max, Q, F, Avg

from backend_deposit.settings import TZ
from deposit.models import Incoming

logger = logging.getLogger(__name__)
err_log = logging.getLogger('error_log')

def bad_incomings():
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


@dataclass
class StepStat:
    pay_sum: float = 0
    count: int = 0


@dataclass
class DayReport:
    date: datetime.datetime
    step1: StepStat
    step2: StepStat
    step3: StepStat
    all_day: StepStat


def day_reports(days=30) -> dict:
    """
    Формирует статистику по дням
    :return: {'2023-10-27': {'step1': StepStat(), 'step2': StepStat(), 'step3': StepStat(), 'all_day': StepStat()},...}
    """
    try:
        bad_incomings_query = bad_incomings()
        all_incomings = Incoming.objects.filter(pay__gt=0).all()
        result_incomings = all_incomings.exclude(pk__in=bad_incomings_query).all()

        end_period = datetime.datetime.now().date()
        start_period = (end_period - datetime.timedelta(days=days))

        # Весь день
        # [{'response_date__date': datetime.date(2023, 10, 22), 'sum': 65.0, 'count': 2, 'avg': 32.5},...]
        all_day = result_incomings.values(
            'response_date__date').annotate(
            sum=Sum('pay'),
            count=Count('pk'),
            avg=Avg('pay')
        )

        # Смена1 0-8.
        step_1 = result_incomings.filter(response_date__hour__gte=0, response_date__hour__lt=8).values(
            'response_date__date').annotate(
            sum=Sum('pay'),
            count=Count('pk'),
            avg=Avg('pay')
        )

        # Смена1 8-16.
        step_2 = result_incomings.filter(response_date__hour__gte=8, response_date__hour__lt=16).values(
            'response_date__date').annotate(
            sum=Sum('pay'),
            count=Count('pk'),
            avg=Avg('pay')
        )

        # Смена3 16-0.
        step_3 = result_incomings.filter(response_date__hour__gte=16).values(
            'response_date__date').annotate(
            sum=Sum('pay'),
            count=Count('pk'),
            avg=Avg('pay')
        )
        days_stat_dict = {}
        for day_delta in range((end_period - start_period).days):
            current_day = (end_period - datetime.timedelta(days=day_delta))
            # {'2023-10-27': {'step1': StepStat(), 'step2': StepStat(), 'step3': StepStat(), 'all_day': StepStat()},...}
            days_stat_dict[current_day] = {'step1': StepStat(), 'step2': StepStat(), 'step3': StepStat(), 'all_day': StepStat(),}

        def fill_stat_dict(stat_dict, step_name, step_queryset):
            for step_stat in step_queryset:
                step_date = step_stat['response_date__date']
                if end_period >= step_date >= start_period:
                    current_step = StepStat(
                        pay_sum=step_stat['sum'],
                        count=step_stat['count'],
                    )
                    current_day_stat = stat_dict.get(step_date)
                    current_day_stat[step_name] = current_step
            return stat_dict

        days_stat_dict = fill_stat_dict(days_stat_dict, 'step1', step_1)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step2', step_2)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step3', step_3)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'all_day', all_day)
        return days_stat_dict
    except Exception as err:
        logger.error(err)
        err_log.error(err, exc_info=True)

