import datetime
import logging
from dataclasses import dataclass

from django.db.models import Sum, Count, Max, Q, F, Avg, Value, Subquery, OuterRef
import seaborn as sns
import pandas as pd
import matplotlib
matplotlib.use('AGG')
from io import BytesIO
import base64

from backend_deposit.settings import TZ
from deposit.models import Incoming, CreditCard

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
    credit_cards = CreditCard.objects.all()
    cards = Incoming.objects.filter(pay__gt=0).all().values('recipient').annotate(
        count=Count('pk'),
        sum=Sum('pay'),
        last_date=Max('response_date'),
        last_id=Max('pk'),
        text=Subquery(credit_cards.filter(name=OuterRef('recipient')).values('text')),
        number=Subquery(credit_cards.filter(name=OuterRef('recipient')).values('number')),
        expire=Subquery(credit_cards.filter(name=OuterRef('recipient')).values('expire')),
        cvv=Subquery(credit_cards.filter(name=OuterRef('recipient')).values('cvv')),
        status=Subquery(credit_cards.filter(name=OuterRef('recipient')).values('status')),
        card_id=Subquery(credit_cards.filter(name=OuterRef('recipient')).values('id')),
    ).order_by('-last_date')

    return cards


@dataclass
class StepStat:
    step_sum: float = 0
    count: int = 0
    unconfirm_count: int = 0
    confirm_count: int = 0
    unconfirm_sum: int = 0
    confirm_sum: float = 0
    count_rk: int = 0
    rk_sum: float = 0


@dataclass
class DayReport:
    date: datetime.datetime
    all_day: StepStat
    step1: StepStat
    step2: StepStat
    step3: StepStat


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

        all_day = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum from
        
        (SELECT id, response_date, response_date::date as date1,
        SUM(pay) OVER(PARTITION BY response_date::date) as step_sum,
        count(pay) OVER(PARTITION BY response_date::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE response_date::date >= %s and response_date::date <= %s AND pay> 0) as t
        
        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum 
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        step1 = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum from
        
        (SELECT id, response_date, response_date::date as date1,
        SUM(pay) OVER(PARTITION BY response_date::date) as step_sum,
        count(pay) OVER(PARTITION BY response_date::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE response_date::date >= %s and response_date::date <= %s AND pay> 0 AND
        date_part('hour', response_date) >= 0 AND date_part('hour', response_date) < 8
        ) as t
        
        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        step2 = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum from
        
        (SELECT id, response_date, response_date::date as date1,
        SUM(pay) OVER(PARTITION BY response_date::date) as step_sum,
        count(pay) OVER(PARTITION BY response_date::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE response_date::date >= %s and response_date::date <= %s AND pay > 0 AND
        date_part('hour', response_date) >= 8 AND date_part('hour', response_date) < 16
        ) as t
        
        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum 
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        step3 = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum  from
        
        (SELECT id, response_date, response_date::date as date1,
        SUM(pay) OVER(PARTITION BY response_date::date) as step_sum,
        count(pay) OVER(PARTITION BY response_date::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY response_date::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY response_date::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY response_date::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE response_date::date >= %s and response_date::date <= %s AND pay > 0 AND
        date_part('hour', response_date) >= 16 
        ) as t
        
        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        days_stat_dict = {}
        for day_delta in range((end_period - start_period).days):
            current_day = (end_period - datetime.timedelta(days=day_delta))
            # {'2023-10-27': {'step1': StepStat(), 'step2': StepStat(), 'step3': StepStat(), 'all_day': StepStat()},...}
            days_stat_dict[current_day] = {'all_day': StepStat(), 'step1': StepStat(), 'step2': StepStat(), 'step3': StepStat(),}

        def fill_stat_dict(stat_dict, step_name, step_queryset):
            for step_stat in step_queryset:
                step_date = step_stat.date1

                current_step = StepStat(
                    step_sum=step_stat.step_sum,
                    count=step_stat.count,
                    unconfirm_count=step_stat.unconfirm_count,
                    confirm_count=step_stat.confirm_count,
                    unconfirm_sum=step_stat.unconfirm_sum,
                    confirm_sum=step_stat.confirm_sum,
                    count_rk=step_stat.count_rk,
                    rk_sum=step_stat.rk_sum,
                )
                current_day_stat = stat_dict.get(step_date)
                current_day_stat[step_name] = current_step
            return stat_dict

        days_stat_dict = fill_stat_dict(days_stat_dict, 'all_day', all_day)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step1', step1)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step2', step2)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step3', step3)

        return days_stat_dict
    except Exception as err:
        logger.error(err)
        err_log.error(err, exc_info=True)


def day_reports_birpay_confirm(days=30) -> dict:
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

        all_day = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum from

        (SELECT id, birpay_confirm_time, birpay_confirm_time::date as date1,
        SUM(pay) OVER(PARTITION BY birpay_confirm_time::date) as step_sum,
        count(pay) OVER(PARTITION BY birpay_confirm_time::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE birpay_confirm_time::date >= %s and birpay_confirm_time::date <= %s AND pay> 0) as t

        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum 
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        step1 = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum from

        (SELECT id, birpay_confirm_time, birpay_confirm_time::date as date1,
        SUM(pay) OVER(PARTITION BY birpay_confirm_time::date) as step_sum,
        count(pay) OVER(PARTITION BY birpay_confirm_time::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE birpay_confirm_time::date >= %s and birpay_confirm_time::date <= %s AND pay> 0 AND
        date_part('hour', birpay_confirm_time) >= 0 AND date_part('hour', birpay_confirm_time) < 8
        ) as t

        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        step2 = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum from

        (SELECT id, birpay_confirm_time, birpay_confirm_time::date as date1,
        SUM(pay) OVER(PARTITION BY birpay_confirm_time::date) as step_sum,
        count(pay) OVER(PARTITION BY birpay_confirm_time::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE birpay_confirm_time::date >= %s and birpay_confirm_time::date <= %s AND pay > 0 AND
        date_part('hour', birpay_confirm_time) >= 8 AND date_part('hour', birpay_confirm_time) < 16
        ) as t

        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum 
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        step3 = Incoming.objects.raw(
            """
        SET timezone TO 'Europe/Moscow';
        select distinct(date1),  max(id) as id, step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum  from

        (SELECT id, birpay_confirm_time, birpay_confirm_time::date as date1,
        SUM(pay) OVER(PARTITION BY birpay_confirm_time::date) as step_sum,
        count(pay) OVER(PARTITION BY birpay_confirm_time::date) as count,
        count(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date) as unconfirm_count,
        count(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as confirm_count,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id = '' or birpay_id is NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as unconfirm_sum,
        COALESCE(sum(pay) FILTER (WHERE  birpay_id != '' and birpay_id is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as confirm_sum,
        count(id) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date) as count_rk,
        COALESCE(sum(pay) FILTER (WHERE  birpay_edit_time is not NULL) OVER(PARTITION BY birpay_confirm_time::date), 0) as rk_sum
        FROM public.deposit_incoming
        WHERE birpay_confirm_time::date >= %s and birpay_confirm_time::date <= %s AND pay > 0 AND
        date_part('hour', birpay_confirm_time) >= 16 
        ) as t

        GROUP BY date1,  step_sum, count, unconfirm_count, confirm_count, unconfirm_sum, confirm_sum, count_rk, rk_sum
        ORDER BY date1
            """, [str(start_period), str(end_period)]
        )

        days_stat_dict = {}
        for day_delta in range((end_period - start_period).days):
            current_day = (end_period - datetime.timedelta(days=day_delta))
            # {'2023-10-27': {'step1': StepStat(), 'step2': StepStat(), 'step3': StepStat(), 'all_day': StepStat()},...}
            days_stat_dict[current_day] = {'all_day': StepStat(), 'step1': StepStat(), 'step2': StepStat(),
                                           'step3': StepStat(), }

        def fill_stat_dict(stat_dict, step_name, step_queryset):
            for step_stat in step_queryset:
                step_date = step_stat.date1

                current_step = StepStat(
                    step_sum=step_stat.step_sum,
                    count=step_stat.count,
                    unconfirm_count=step_stat.unconfirm_count,
                    confirm_count=step_stat.confirm_count,
                    unconfirm_sum=step_stat.unconfirm_sum,
                    confirm_sum=step_stat.confirm_sum,
                    count_rk=step_stat.count_rk,
                    rk_sum=step_stat.rk_sum,
                )
                current_day_stat = stat_dict.get(step_date)
                current_day_stat[step_name] = current_step
            return stat_dict

        days_stat_dict = fill_stat_dict(days_stat_dict, 'all_day', all_day)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step1', step1)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step2', step2)
        days_stat_dict = fill_stat_dict(days_stat_dict, 'step3', step3)

        return days_stat_dict
    except Exception as err:
        logger.error(err)
        err_log.error(err, exc_info=True)


def get_img_for_day_graph():
    bad_incomings_query = bad_incomings()
    all_incomings = Incoming.objects.filter(pay__gt=0).all()
    result_incomings = all_incomings.exclude(pk__in=bad_incomings_query).all()
    df = pd.DataFrame(list(result_incomings.values()))
    print(df)
    df['response_date'] = df['response_date'].dt.tz_convert("Europe/Moscow")
    stat = df[['id', 'response_date', 'recipient', 'pay']]
    stat['reg_hr'] = stat.response_date.dt.hour
    stat['date'] = stat['response_date'].dt.date
    # stat = stat[stat['pay'] > 0]
    stat = stat[['id', 'date', 'reg_hr', 'pay']]
    print(stat)
    day_stat = stat.groupby('date').agg({'pay': ['sum', 'count']})
    day_stat = day_stat.reindex()

    # sns_plot = sns.barplot(data=day_stat, x='date', y=("pay", 'sum'))
    # sns_plot.bar_label(sns_plot.containers[0])
    # plot_file = BytesIO()
    # figure = sns_plot.get_figure()
    # figure.savefig(plot_file, format='png')
    # encoded_file = base64.b64encode(plot_file.getvalue()).decode()
    #
    # sns_plot2 = sns.barplot(data=day_stat, x='date', y=("pay", 'count'))
    # sns_plot2.bar_label(sns_plot2.containers[0])
    # plot_file2 = BytesIO()
    # figure2 = sns_plot2.get_figure()
    # figure2.savefig(plot_file2, format='png')
    # encoded_file2 = base64.b64encode(plot_file2.getvalue()).decode()

    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(12, 12))
    axes[0].set_title("Количество платежей")
    sns.barplot(x='date', y=('pay', 'count'), data=day_stat, ax=axes[0])
    sns.barplot(x='date', y=('pay', 'sum'), data=day_stat, ax=axes[1])
    axes[1].set_title("Сумма платежей")
    axes[0].bar_label(axes[0].containers[0])
    axes[1].bar_label(axes[1].containers[0])
    axes[0].set_xticklabels(axes[0].get_xticklabels(), rotation=90)
    axes[1].set_xticklabels(axes[1].get_xticklabels(), rotation=90)
    plt.subplots_adjust(hspace=0.5)

    plot_file = BytesIO()
    figure = fig.get_figure()
    figure.savefig(plot_file, format='png')
    encoded_file = base64.b64encode(plot_file.getvalue()).decode()

    return encoded_file
