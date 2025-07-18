import base64
import datetime
import io
import json
import uuid
from http import HTTPStatus
from tempfile import NamedTemporaryFile
from types import NoneType

import numpy as np
import pandas as pd
import pytz
import structlog
from asgiref.sync import async_to_sync
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test, login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models.functions import Lag

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q, OuterRef, Window, Exists, Value, Sum, Count, Subquery, ExpressionWrapper, FloatField, \
    Max, DurationField
from django.http import HttpResponseForbidden, JsonResponse, HttpResponseBadRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView, TemplateView
from matplotlib import pyplot as plt
from rest_framework.views import APIView
from structlog.contextvars import bind_contextvars

from core.asu_pay_func import create_payment, send_card_data, create_asu_withdraw
from core.birpay_func import get_birpay_withdraw, get_new_token, approve_birpay_withdraw, decline_birpay_withdraw, \
    get_birpays, change_amount_birpay, approve_birpay_refill
from core.birpay_new_func import get_um_transactions, send_transaction_action
from core.global_func import TZ
from core.stat_func import cards_report, bad_incomings, get_img_for_day_graph, day_reports_birpay_confirm, \
    day_reports_orm
from deposit import tasks
from deposit.filters import IncomingCheckFilter, IncomingStatSearch, BirpayOrderFilter, BirpayPanelFilter
from deposit.forms import (ColorBankForm,
                           IncomingForm, MyFilterForm, IncomingSearchForm, CheckSmsForm, CheckScreenForm,
                           AssignCardsToUserForm,
                           MoshennikListForm, PainterListForm, OperatorStatsDayForm)
from deposit.func import find_possible_incomings
from deposit.permissions import SuperuserOnlyPerm, StaffOnlyPerm
from deposit.tasks import check_incoming, send_new_transactions_from_um_to_asu, refresh_birpay_data, \
    send_image_to_gpt_task, download_birpay_check_file
from deposit.views_api import response_sms_template
from ocr.ocr_func import (make_after_save_deposit, response_text_from_image)
from deposit.models import Incoming, TrashIncoming, IncomingChange, Message, \
    MessageRead, RePattern, IncomingCheck, WithdrawTransaction, BirpayOrder
from users.models import Options

logger = structlog.get_logger('deposit')


User = get_user_model()


def make_page_obj(request, objects, numbers_of_posts=settings.PAGINATE):
    paginator = Paginator(objects, numbers_of_posts)
    page_number = request.GET.get('page')
    return paginator.get_page(page_number)


@staff_member_required(login_url='users:login')
def incoming_list(request):
    # Список всех платежей и сохранение birpay
    patterns = {
        'm10': r'.*(\d\d\.\d\d\.\d\d\d\d \d\d:\d\d).*Получатель (.*) Отправитель (.*) Код транзакции (\d+) Сумма (.+) Статус (.*) .*8',
        'm10_short': r'.*(\d\d\.\d\d\.\d\d\d\d \d\d:\d\d).* (Пополнение.*) Получатель (.*) Код транзакции (\d+) Сумма (.+) Статус (\S+).*',
        'm10new': r'first: (.+)[\n]+amount:.*[\n]*([+-].*)m.*[\n]+.*[\n]*.*[\n]*.*[\n]*.*[\n]*Status (.+)[\n]*Date (.+)[\n]+Sender (.+)[\n]*Recipient (.+)[\n]+.*ID (.+)',
        'm10new_short': r'first: (.+)[\n]+amount:.*([+-].*)m.*[\n]+.*[\n]*.*[\n]*.*[\n]*.*[\n]*Status (.+)[\n]+Date (.+)[\n]+m10 wallet (.+)[\n]+.*ID (.+)'
    }
    db_patterns = RePattern.objects.all()
    for db_pattern in db_patterns:
        patterns.update({db_pattern.name: db_pattern.pattern})

    if request.method == "POST":
        input_name = list(request.POST.keys())[1]
        pk, options = list(request.POST.keys())[1].split('-')
        value = request.POST.get(input_name) or ''
        incoming = Incoming.objects.get(pk=pk)
        if isinstance(incoming.birpay_id, NoneType):
            incoming.birpay_id = value
            incoming.birpay_confirm_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            incoming.save()
            order = BirpayOrder.objects.filter(merchant_transaction_id=value).first()
            if order:
                order.incoming = incoming
                order.save()
            #Сохраняем историю
            new_history = IncomingChange(
                incoming=incoming,
                user=request.user,
                val_name='birpay_id',
                new_val=value
            )
            new_history.save()
        else:
            return HttpResponseBadRequest('Уже отработана')

        if 'filter' in options:
            return redirect('deposit:incomings_filter')
        else:
            return redirect('deposit:incomings')

    template = 'deposit/incomings_list.html'
    logger.info(request.user.has_perm('users.base2'))
    if request.user.has_perm('users.base2') and not request.user.has_perm('users.all_base'):
        # Опер базы2
        incoming_q = Incoming.objects.raw(
            """
            SELECT *,
            LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
            LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
            FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
            WHERE worker = 'base2'
            ORDER BY deposit_incoming.id DESC LIMIT 5000;
            """
        )
        last_id = Incoming.objects.filter(worker='base2').order_by('id').last()
    elif not request.user.has_perm('users.base2') and not request.user.has_perm('users.all_base'):
        # Опер базы не 2
        incoming_q = Incoming.objects.raw(
        """
        SELECT *,
        LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
        LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
        FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
        WHERE worker != 'base2' or worker is NULL
        ORDER BY deposit_incoming.id DESC LIMIT 5000;
        """)
        last_id = Incoming.objects.exclude(worker='base2').order_by('id').last()
    elif request.user.has_perm('users.all_base') or request.user.is_superuser:
        # support
        incoming_q = Incoming.objects.raw(
        # """
        # SELECT *,
        # LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
        # LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
        # FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
        # ORDER BY deposit_incoming.id DESC LIMIT 5000;
        # """)
        """
        with short_table as (SELECT * from deposit_incoming ORDER BY id desc limit 5000)
        SELECT *,
        LAG(balance, -1) OVER (PARTITION BY short_table.recipient order by response_date desc, balance desc, short_table.id desc) as prev_balance,
        LAG(balance, -1) OVER (PARTITION BY short_table.recipient order by response_date desc, balance desc, short_table.id desc) + pay as check_balance
        FROM short_table LEFT JOIN deposit_colorbank ON deposit_colorbank.name = short_table.sender
        ORDER BY short_table.id DESC LIMIT 5000;
        """)
        last_id = Incoming.objects.order_by('id').last()

    # incoming_q = Incoming.objects.raw(
    #     """
    #     SELECT *,
    #     LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
    #     LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
    #     FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    #     ORDER BY deposit_incoming.id DESC LIMIT 5000;
    #     """)
    # last_id = Incoming.objects.order_by('id').last()
    if last_id:
        last_id = last_id.id
    # last_bad = BadScreen.objects.order_by('-id').first()
    last_bad = Message.objects.filter(type='macros').order_by('-id').first()
    last_bad_id = last_bad.id if last_bad else last_bad
    context = {'page_obj': make_page_obj(request, incoming_q),
               'last_id': last_id,
               'last_bad_id': last_bad_id}
    return render(request, template, context)


class IncomingEmpty(ListView):
    # Не подтвержденные платежи
    model = Incoming
    template_name = 'deposit/incomings_list.html'
    paginate_by = settings.PAGINATE

    def get_queryset(self, *args, **kwargs):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        # empty_incoming = Incoming.objects.filter(Q(birpay_id__isnull=True) | Q(birpay_id='')).order_by('-id').all()
        empty_incoming = Incoming.objects.filter(Q(birpay_id__isnull=True) | Q(birpay_id='')).order_by('-response_date', '-id').annotate(
            prev_balance=Window(expression=Lag('balance', 1), partition_by=[F('recipient')], order_by=['response_date', 'balance', 'id']),
            check_balance=F('pay') + Window(expression=Lag('balance', 1), partition_by=[F('recipient')], order_by=['response_date', 'balance', 'id']),
        ).order_by('-id').all()
        if not self.request.user.has_perm('users.all_base'):
            if self.request.user.has_perm('users.base2'):
                empty_incoming = empty_incoming.filter(worker='base2')
            else:
                empty_incoming = empty_incoming.exclude(worker='base2')
        return empty_incoming


class IncomingCheckList(SuperuserOnlyPerm, ListView):
    model = IncomingCheck
    paginate_by = settings.PAGINATE
    template_name = 'deposit/incoming_checks_list.html'
    filter = IncomingCheckFilter

    def get_queryset(self):
        return IncomingCheckFilter(
            self.request.GET, queryset=IncomingCheck.objects
            .annotate(delta=F('pay_operator') - F('pay_birpay'))
        ).qs

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        filter = IncomingCheckFilter(self.request.GET, queryset=self.get_queryset())
        context['filter'] = filter
        context['form'] = filter.form
        qs = self.get_queryset()
        stat = {}
        status_0 = qs.filter(status=0).count()
        status_1 = qs.filter(status=1).count()
        status_decline = qs.filter(status=1).count()
        stat['status_0'] = status_0
        stat['status_1'] = status_1
        stat['status_decline'] = status_decline
        stat['status_other'] = qs.count() - status_0 - status_1 - status_decline
        context['stat'] = stat
        return context


def incoming_recheck(request, pk):
    result = check_incoming(pk)
    return JsonResponse(data=result, safe=False)


class IncomingFiltered(StaffOnlyPerm, ListView):
    # Отфильтровованные платежи
    model = Incoming
    template_name = 'deposit/incomings_list.html'
    paginate_by = settings.PAGINATE

    def get_queryset(self, *args, **kwargs):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        user_filter = self.request.user.profile.my_filter
        user_filter2 = self.request.user.profile.my_filter2
        user_filter3 = self.request.user.profile.my_filter3
        user_filter.extend(user_filter2)
        user_filter.extend(user_filter3)
        if self.request.user.has_perm('users.base2'):
            filtered_incoming = Incoming.objects.raw(
            """
            SELECT *,
            LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
            LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
            FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
            WHERE deposit_incoming.recipient = ANY(%s) and deposit_incoming.worker = 'base2'
            ORDER BY deposit_incoming.id DESC
            """, [user_filter])
        else:
            filtered_incoming = Incoming.objects.raw(
                """
                SELECT *,
                LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
                LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
                FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
                WHERE deposit_incoming.recipient = ANY(%s) and (deposit_incoming.worker != 'base2' or deposit_incoming.worker is NULL) 
                ORDER BY deposit_incoming.id DESC
                """, [user_filter])
        # filtered_incoming = Incoming.objects.filter(
        #     recipient__in=user_filter).order_by('-id').all()
        return filtered_incoming

    def get_context_data(self, **kwargs):
        context = super(IncomingFiltered, self).get_context_data(**kwargs)
        context['search_form'] = None
        user_filter = self.request.user.profile.my_filter
        last_filtered_id = None
        last_filtered = Incoming.objects.filter(
            recipient__in=user_filter).order_by('-id').first()
        if last_filtered:
            last_filtered_id = last_filtered.id
        context['last_id'] = last_filtered_id
        context['filter'] = json.dumps(user_filter)
        # last_bad = BadScreen.objects.order_by('-id').first()
        last_bad = Message.objects.filter(type='macros').order_by('-id').first()
        last_bad_id = last_bad.id if last_bad else last_bad
        context['last_bad_id'] = last_bad_id
        return context


class IncomingSearch(ListView):
    # Поиск платежей
    model = Incoming
    template_name = 'deposit/incomings_list.html'
    paginate_by = 50
    search_date = None

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if self.request.user.is_staff:
            self.object = None
            return super().get(request, *args, **kwargs)
        else:
            return redirect('deposit:index')

    @staticmethod
    def get_date(year, month, day):
        return datetime.date(int(year), int(month), int(day))

    def get_queryset(self):
        search_in = self.request.GET.get('search_in', 'register_date')
        begin0 = self.request.GET.get('begin_0', '')
        begin1 = self.request.GET.get('begin_1', '')
        end0 = self.request.GET.get('end_0', '')
        end1 = self.request.GET.get('end_1', '')
        only_empty = self.request.GET.get('only_empty', '')
        pay = self.request.GET.get('pay', 0)
        pk = self.request.GET.get('pk', 0)
        sort_by_sms_time = self.request.GET.get('sort_by_sms_time', 1)
        end_time = None
        tz = pytz.timezone(settings.TIME_ZONE)
        start_time = ''

        if pk:
            return Incoming.objects.filter(birpay_id__contains=pk)

        if sort_by_sms_time:
            all_incoming = Incoming.objects.order_by('-response_date').all()
        else:
            all_incoming = Incoming.objects.order_by('-id').all()

        if not self.request.user.has_perm('users.all_base'):
            if self.request.user.has_perm('users.base2'):
                all_incoming = all_incoming.filter(worker='base2')
            else:
                all_incoming = all_incoming.exclude(worker='base2')
        if begin0:
            begin = f'{begin0 + " " + begin1}'.strip()
            start_time = datetime.datetime.fromisoformat(begin)
            start_time = tz.localize(start_time)
        if end0:
            end_time = datetime.datetime.fromisoformat(f'{end0 + " " + end1}'.strip())
            end_time = tz.localize(end_time)
        if search_in == 'response_date':
            if start_time:
                all_incoming = all_incoming.filter(response_date__gte=start_time).all()
            if end_time:
                all_incoming = all_incoming.filter(response_date__lte=end_time).all()
        else:
            if start_time:
                all_incoming = all_incoming.filter(register_date__gte=start_time).all()
            if end_time:
                all_incoming = all_incoming.filter(register_date__lte=end_time).all()
        if only_empty:
            all_incoming = all_incoming.filter(Q(birpay_id='') | Q(birpay_id=None))
        if pay:
            all_incoming = all_incoming.filter(pay=pay)

        if not begin0 and not end0 and not only_empty and not pay:
            return all_incoming[:0]

        return all_incoming

    def get_context_data(self, **kwargs):
        context = super(IncomingSearch, self).get_context_data(**kwargs)
        request_dict = self.request.GET.dict()
        begin_0 = request_dict.get('begin_0')
        begin_1 = request_dict.get('begin_1')
        end_0 = request_dict.get('end_0')
        end_1 = request_dict.get('end_1')
        begin = None
        end = None
        if begin_0:
            begin = datetime.datetime.fromisoformat(f'{begin_0} {begin_1}'.strip())
        if end_0:
            end = datetime.datetime.fromisoformat(f'{end_0} {end_1}'.strip())
        request_dict.update({'begin': begin})
        request_dict.update({'end': end})
        search_form = IncomingSearchForm(initial={**request_dict})
        context['search_form'] = search_form
        last_id = Incoming.objects.order_by('id').last()
        if last_id:
            last_id = last_id.id
        context['last_id'] = last_id
        return context


class IncomingTrashList(ListView):
    # Мусор
    model = Incoming
    template_name = 'deposit/trash_list.html'
    paginate_by = settings.PAGINATE

    def get_queryset(self, *args, **kwargs):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        trash_list = TrashIncoming.objects.order_by('-id').all()
        # if self.request.user.has_perm('users.base2'):
        #     trash_list = trash_list.filter(worker='base2')
        # else:
        #     trash_list = trash_list.exclude(worker='base2')
        # logger.debug('Тест debug')
        # logger.warning('Тест warning')
        # logger.info('Тест info')
        # logger.error('Тест error')
        return trash_list


@staff_member_required(login_url='users:login')
def my_filter(request):
    # Изменение фильтра по получателю для платежей по фильтру
    context = {}
    user = request.user
    form = MyFilterForm(request.POST or None, initial={'my_filter': user.profile.my_filter, 'my_filter2': user.profile.my_filter2, 'my_filter3': user.profile.my_filter3})
    template = 'deposit/my_filter.html'
    context['form'] = form

    if request.POST:
        if form.is_valid():
            user_filter = form.cleaned_data.get("my_filter")
            user_filter2 = form.cleaned_data.get("my_filter2")
            user_filter3 = form.cleaned_data.get("my_filter3")
            user.profile.my_filter = user_filter
            user.profile.my_filter2 = user_filter2
            user.profile.my_filter3 = user_filter3

            user.profile.save()
            return redirect('deposit:incomings_filter')
    return render(request, template, context)


class IncomingEdit(UpdateView, ):
    # Ручная корректировка платежа
    model = Incoming
    form_class = IncomingForm
    success_url = reverse_lazy('deposit:incomings')
    template_name = 'deposit/incoming_edit.html'

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.user.has_perm('users.all_base'):
            return super().get(request, *args, **kwargs)
        user = self.request.user
        is_base2_perm = user.has_perm('users.base2')
        worker = self.object.worker
        if worker == 'base2' and is_base2_perm or worker != 'base2' and not is_base2_perm:
            return super().get(request, *args, **kwargs)
        else:
            return HttpResponseForbidden('Не ваша база')

    def post(self, request, *args, **kwargs):
        if request.user.has_perm('deposit.can_hand_edit'):
            self.object = self.get_object()
            return super().post(request, *args, **kwargs)
        return HttpResponseForbidden('У вас нет прав делать ручную корректировку')

    def get_context_data(self, **kwargs):
        context = super(IncomingEdit, self).get_context_data(**kwargs)
        history = self.object.history.order_by('-id').all()
        context['history'] = history
        if self.request.user.role == ['admin'] or self.request.user.is_superuser:
            jail_option = True
        else:
            jail_option = False
        context['jail_option'] = jail_option
        return context

    def form_valid(self, form):
        if form.is_valid():
            old_incoming = Incoming.objects.get(pk=self.object.id)
            incoming: Incoming = self.object
            incoming.birpay_edit_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            if not incoming.birpay_confirm_time:
                incoming.birpay_confirm_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            incoming.save()

            # Сохраняем историю
            IncomingChange().save_incoming_history(old_incoming, incoming, self.request.user)

            return super(IncomingEdit, self).form_valid(form)


class ColorBankCreate(CreateView):
    form_class = ColorBankForm
    template_name = 'deposit/color_bank_create.html'
    success_url = reverse_lazy('incomings')

    def form_valid(self, form):
        if form.is_valid():
            form.save()


def get_last(request):
    """Функция поиска последнего id Incoming и последнего id Message/macros для javascript"""
    all_incomings = Incoming.objects.order_by('id').all()
    if request.user.has_perm('users.base2'):
        all_incomings = all_incomings.filter(worker='base2')
    elif request.user.has_perm('users.all_base'):
        pass
    else:
        all_incomings = all_incomings.exclude(worker='base2')

    user_filter = request.GET.get('filter')
    # print('user_filter:', user_filter)
    if user_filter:
        user_filter = json.loads(user_filter)
        filtered_incomings = all_incomings.filter(recipient__in=user_filter).all()
        last_id = filtered_incomings.last()
    else:
        last_id = all_incomings.last()
    if last_id:
        last_id = last_id.id
    # last_bad = BadScreen.objects.order_by('-id').first()
    last_bad = Message.objects.filter(type='macros').order_by('-id').first()
    last_bad_id = last_bad.id if last_bad else last_bad

    data = list()
    data.append({
        'id': str(last_id),
        'last_bad_id': str(last_bad_id)
    })
    return JsonResponse(data, safe=False)


@staff_member_required(login_url='users:login')
def get_stats(request):
    # Статистика по времени поступления платежа
    if request.user.has_perm('users.stats') or request.user.is_superuser:
        template = 'deposit/stats.html'
        page_obj = bad_incomings()
        cards = cards_report()
        # days_stat_dict = day_reports(100)
        days_stat_dict = day_reports_orm(100)
        context = {'page_obj': page_obj, 'cards': cards, 'day_reports': days_stat_dict}
        return render(request, template, context)
    raise PermissionDenied('Недостаточно прав')


@staff_member_required(login_url='users:login')
def get_stats2(request):
    # Статистика по времени подтверждения платежа
    if not request.user.is_superuser:
        raise PermissionDenied('Недостаточно прав')
    template = 'deposit/stats2.html'
    page_obj = bad_incomings()
    cards = cards_report()
    days_stat_dict = day_reports_birpay_confirm(100)
    context = {'page_obj': page_obj, 'cards': cards, 'day_reports': days_stat_dict}
    return render(request, template, context)


def day_graph(request):
    if request.user.has_perm('users.graph') or request.user.is_superuser:

        template = 'deposit/test.html'
        encoded_file = get_img_for_day_graph()
        context = {'fig1': encoded_file}
        return render(request, template, context)
    raise PermissionDenied('Недостаточно прав')

@staff_member_required()
def operator_speed_graph(request):
    form = OperatorStatsDayForm(request.GET or None)
    graph_url = None
    stat_table_data = None
    stat_table_columns = None
    no_data = False

    if form.is_valid():
        chosen_date = form.cleaned_data['date']
        try:

            qs = BirpayOrder.objects.annotate(
                delta=ExpressionWrapper(
                    F('confirmed_time') - F('sended_at'),
                    output_field=DurationField()
                )
            ).filter(
                sended_at__date=chosen_date,
                confirmed_operator__isnull=False,
                confirmed_time__isnull=False,
                delta__lte=datetime.timedelta(days=1)
            )

            logger.info(f"Queryset count: {qs.count()} на {chosen_date}")

            df = pd.DataFrame(
                list(qs.values(
                    'sended_at', 'confirmed_time', 'id',
                    'confirmed_operator__username'
                ))
            )
            logger.info(f"DataFrame shape: {df.shape}")
            logger.info(f"DataFrame columns: {df.columns.tolist()}")

            if df.empty:
                no_data = True
            else:
                df['sended_at'] = pd.to_datetime(df['sended_at'], utc=True).dt.tz_convert(TZ)
                df['confirmed_time'] = pd.to_datetime(df['confirmed_time'], utc=True).dt.tz_convert(TZ)
                df['hour'] = df['sended_at'].dt.hour
                df['delta_minutes'] = (df['confirmed_time'] - df['sended_at']).dt.total_seconds() / 60

                # Категории для графика (по скорости)
                conditions = [
                    df['delta_minutes'] < 5,
                    (df['delta_minutes'] >= 5) & (df['delta_minutes'] < 15),
                    (df['delta_minutes'] >= 15) & (df['delta_minutes'] < 60),
                    (df['delta_minutes'] >= 60),
                ]
                choices = ['<5 минут', '<15 минут', '<60 минут', '≥60 минут']
                df['speed_cat'] = np.select(conditions, choices, default='≥60 минут')

                all_hours = np.arange(0, 24)
                speed_order = ['<5 минут', '<15 минут', '<60 минут', '≥60 минут']
                speed_colors = ['mediumseagreen', 'gold', 'tomato', 'lightgray']

                # --- График ---
                hourly = df.groupby(['hour', 'speed_cat'])['id'].count().unstack(fill_value=0)
                hourly = hourly.reindex(index=all_hours, fill_value=0)
                hourly = hourly.reindex(columns=speed_order, fill_value=0)

                fig, ax = plt.subplots(figsize=(14, 5))
                hourly.plot(
                    kind='bar',
                    stacked=True,
                    color=speed_colors,
                    ax=ax
                )
                ax.set_xlabel('Час (МСК)')
                ax.set_ylabel('Количество подтверждений')
                ax.set_title(f'Подтверждения по часам (МСК) за {chosen_date}')
                ax.set_xticks(range(24))
                ax.set_xticklabels([str(h) for h in range(24)], rotation=0)
                ax.legend(title='Время подтверждения')
                plt.tight_layout()

                # Подписи
                for i, (idx, row) in enumerate(hourly.iterrows()):
                    vals = [int(row[c]) for c in speed_order]
                    total_height = np.sum(vals)
                    ax.text(
                        i, total_height + 0.5,
                        f"{vals[0]}/{vals[1]}/{vals[2]}",
                        ha='center', va='bottom', fontsize=11, fontweight='bold'
                    )

                buf = io.BytesIO()
                plt.savefig(buf, format='png')
                buf.seek(0)
                graph_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
                plt.close(fig)

                # --- Таблица по юзерам (операторам) ---
                bins = [0, 5, 10, 15, 60, np.inf]
                labels = [
                    '0–5', '5–10', '10–15', '15–60', '60+'
                ]
                df['bucket'] = pd.cut(df['delta_minutes'], bins=bins, labels=labels, right=False)
                op_field = 'confirmed_operator__username'

                user_stats = []
                for op, group in df.groupby(op_field):
                    total = group.shape[0]
                    bucket_counts = group['bucket'].value_counts().reindex(labels, fill_value=0)
                    bucket_perc = (bucket_counts / total * 100).round(1)
                    # Только проценты (без "Доля", без %)
                    user_stats.append(
                        [op, total] + [bucket_perc[l] for l in labels]
                    )
                stat_cols = ['Оператор', 'Кол-во'] + labels
                stat_table = pd.DataFrame(user_stats, columns=stat_cols)
                # Сортировка по первому периоду (0–5)
                stat_table = stat_table.sort_values('0–5', ascending=False).reset_index(drop=True)
                stat_table_data = stat_table.to_dict('records')
                stat_table_columns = list(stat_table.columns)

        except Exception as e:
            logger.exception("Error in operator_speed_graph view")
            return render(request, 'deposit/operator_speed_graph.html', {
                'form': form,
                'graph_url': None,
                'stat_table_data': None,
                'stat_table_columns': None,
                'no_data': True,
                'error': str(e),
            })

    return render(request, 'deposit/operator_speed_graph.html', {
        'form': form,
        'graph_url': graph_url,
        'stat_table_data': stat_table_data,
        'stat_table_columns': stat_table_columns,
        'no_data': no_data,
    })


class MessageView(DetailView):
    """Подробный просмотр сообщения"""
    model = Message
    template_name = 'deposit/message_view.html'
    paginate_by = settings.PAGINATE

    def post(self, *args, **kwargs):
        object = self.get_object()
        MessageRead.objects.get_or_create(message=object, user=self.request.user)
        return super().get(self, *args, **kwargs)
    
    def get_object(self, queryset=None):
        message = super().get_object()
        is_read = MessageRead.objects.filter(message=message, user=self.request.user).exists()
        message.is_read = is_read
        return message


class MessageListView(ListView):
    """Просмотр сообщений"""
    model = Message
    template_name = 'deposit/messages.html'
    paginate_by = settings.PAGINATE

    def get_queryset(self, *args, **kwargs):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        messages = Message.objects.select_related('author').exclude(type='macros').annotate(
            is_read=Exists(MessageRead.objects.filter(message=OuterRef('pk')))
        ).order_by('-id').all()
        return messages

    def get(self, request, *args, **kwargs):
        if 'read_all' in request.GET:
            # Отметим как прочитанное непрочитанное
            readed_message_ids = request.user.messages_read.all().values('message')
            unread_messages = Message.objects.exclude(id__in=readed_message_ids).all()
            new_reads = []
            for unread_message in unread_messages:
                new_read = MessageRead(user=request.user, message=unread_message)
                new_reads.append(new_read)
            new = MessageRead.objects.bulk_create(new_reads)
        return super().get(request, *args, **kwargs)

    def post(self, *args):
        print('post', self, args)
        return super().get(*args)


@staff_member_required(login_url='users:login')
def check_sms(request):
    # Проверка шаблона sms
    context = {}
    form = CheckSmsForm(request.POST or None)
    template = 'deposit/check_sms.html'
    context['form'] = form
    if form.is_valid():
        text = form.cleaned_data['text'].replace('\r\n', '\n')
        responsed_pay = response_sms_template(text)
        context['responsed_pay'] = responsed_pay
    return render(request, template, context)


@staff_member_required(login_url='users:login')
def check_screen(request):
    # Проверка шаблона sms
    context = {}
    template = 'deposit/check_screen.html'
    form = CheckScreenForm(request.POST)

    if request.method == 'POST':
        if form.is_valid():
            screen = form.cleaned_data.get('screen')
            image_bytes = screen.image.read()
            context['x'] = 1

            # with NamedTemporaryFile() as temp_file:
            temp_file = NamedTemporaryFile(mode='wb', suffix='.jpg', prefix='prefix_', delete=False)
            temp_file.write(image_bytes)
            context['file_url'] = temp_file.name

        else:
            print(form.errors)
    context['form'] = form
    return render(request, template, context)


@staff_member_required(login_url='users:login')
def test_transactions(request):
    # ts = get_um_transactions()
    # # t = send_new_transactions_to_asu()
    # data = {'merchant': 34, 'order_id': 123151, 'amount': 390.0, 'user_login': '10724806', 'pay_type': 'card_2'}
    # payment_id = create_payment(data)
    # print('payment_id:', payment_id)
    # if payment_id:
    #     card_data = {
    #         "card_number": 1111222233334444,
    #         "owner_name": 'card_holder',
    #         "expired_month": '01',
    #         "expired_year": '26',
    #         "cvv": 1234
    #     }
    #     send_card_data(payment_id, card_data)
    # payment_id = 'a662830b-992b-48ef-a346-caa3337521a7'
    # card_data = {
    #     "card_number": 1111222233334444,
    #     "owner_name": 'card_holder',
    #     "expired_month": '01',
    #     "expired_year": '26',
    #     "cvv": 1234
    # }
    # send_card_data(payment_id, card_data)
    # send_new_transactions_to_asu()

    tasks.send_new_transactions_from_um_to_asu.delay()

    return HttpResponse("<body>hello</body>")


class BkashWebhook(APIView):

    def get(self, request, *args, **kwargs):
        try:
            data = request.GET
            logger.info(f'Получен вэбхук BkashWebhook: {data}')
            return HttpResponse(status=200)
        except Exception as err:
            logger.error(err)
            return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=str(err))


class WebhookReceive(APIView):
    # Получение вэбхука и подтверждение/отклонение на um

    def get(self, request, *args, **kwargs):
        try:
            data = request.GET
            logger.info(f'Получен вэбхук: {data}')
            return HttpResponse(status=200)
        except Exception as err:
            logger.error(err)
            return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=str(err))

    def post(self, request, *args, **kwargs):
        # {"id": "d874dbad-b55c-4acd-93c2-80627174e372", "order_id": "5e52ab95-5628-43c2-952c-e3341e31890d",
        #  "user_login": null, "amount": 2300, "create_at": "2024-10-15T05:46:22.091818+00:00", "status": 9,
        #  "confirmed_time": "2024-10-15T05:47:22.091818+00:00", "confirmed_amount": 1178,
        #  "signature": "afea80ca267b0c5566c25d62be8f7a8ab0dc8d2175f38ce8c2bd0bb2c74e6b89", "mask": null}
        try:
            data = request.data
            order_id = data.get('order_id')
            status = data.get('status')
            logger.info(f'Получен вэбхук: {data}')
            if status == 9:
                logger.info(f'Подтверждаем на um {order_id}')
                send_transaction_action(order_pk=order_id, action='agent_approve')
            elif status == -1:
                logger.info(f'Отклоняем на um {order_id}')
                send_transaction_action(order_pk=order_id, action='agent_decline')

            return HttpResponse(status=200)
        except Exception as err:
            logger.error(err)
            return HttpResponse(status=HTTPStatus.BAD_REQUEST, reason=str(err))


class WithdrawWebhookReceive(APIView):
    # Получение вэбхука выплаты и подтверждение/отклонение на birpay

    def post(self, request, *args, **kwargs):
        # {"id": "d874dbad-b55c-4acd-93c2-80627174e372", "withdraw_id": "5e52ab95-5628-43c2-952c-e3341e31890d",
        #  "amount": 2300, "create_at": "2024-10-15T05:46:22.091818+00:00", "status": 9,
        #  "confirmed_time": "2024-10-15T05:47:22.091818+00:00"}
        logger = structlog.get_logger('deposit')

        try:
            data = request.data
            withdraw_id = data.get('id')
            birpay_withdraw_id = data.get('withdraw_id')
            transaction_id = data.get('transaction_id')
            logger = logger.bind(withdraw_id=withdraw_id, birpay_withdraw_id=birpay_withdraw_id, transaction_id=transaction_id)
            status = data.get('status')
            logger.info(f'Получен вэбхук withdraw: {data}')

            result = {}
            if status == 9:
                logger.info(f'Подтверждаем на birpay {birpay_withdraw_id}')
                result = approve_birpay_withdraw(birpay_withdraw_id, transaction_id)
            elif status == -1:
                logger.info(f'Отклоняем на birpay {birpay_withdraw_id}')
                result = decline_birpay_withdraw(birpay_withdraw_id, transaction_id)

            logger.info(f'result: {result}')
            return JsonResponse(status=200, data=result, safe=False)
        except Exception as err:
            logger.error(err)
            return JsonResponse(status=HTTPStatus.BAD_REQUEST, data=str(err), safe=False)

# def withdraw_test(request):
#
#     template = 'deposit/withdraw_test.html'
#     logger = structlog.get_logger('deposit')
#     logger.info('тест логгера biragte')
#
#     logger = structlog.get_logger('deposit')
#     logger.info(f'тест логгера {__name__}')
#
#     token = get_new_token()
#     print(token)
#     # birpay = find_birpay_from_id('710021863')
#     withdraw_list = async_to_sync(get_birpay_withdraw)()
#
#     print(len(withdraw_list))
#     total_amount = 0
#     withdraws_to_work = []
#     results = []
#     limit = 1
#     count = 0
#     for withdraw in withdraw_list:
#         if count >= limit:
#             break
#         is_exists = WithdrawTransaction.objects.filter(withdraw_id=withdraw['id']).exists()
#         if not is_exists:
#             count += 1
#             # Если еще не брали в работу создадим на асупэй
#             expired_month = expired_year = target_phone = card_data = None
#             # print(withdraw)
#             amount = float(withdraw.get('amount'))
#             total_amount += amount
#             wallet_id = withdraw.get('customerWalletId', '')
#             if wallet_id.startswith('994'):
#                 target_phone = f'+{wallet_id}'
#             elif len(wallet_id) == 9:
#                 target_phone = f'+994{wallet_id}'
#             else:
#                 payload = withdraw.get('payload', {})
#                 if payload:
#                     card_date = payload.get('card_date')
#                     if card_date:
#                         expired_month, expired_year = card_date.split('/')
#                         if expired_year:
#                             expired_year = expired_year[-2:]
#                 card_data = {
#                     "card_number": wallet_id,
#                 }
#                 if expired_month and expired_year:
#                     card_data['expired_month'] = expired_month
#                     expired_year['expired_year'] = expired_year
#             withdraw_data = {
#                 'withdraw_id': withdraw['id'],
#                 'amount': amount,
#                 'card_data': card_data,
#                 'target_phone': target_phone,
#             }
#             withdraws_to_work.append(withdraw_data)
#
#             # result = create_asu_withdraw(**withdraw_data)
#             # if result.get('status') == 'success':
#             #     # Успешно создана
#             #     WithdrawTransaction.objects.create(
#             #         withdraw_id=withdraw['id'],
#             #         status=1,
#             #     )
#             #
#             #     results.append(result)
#
#     context = {
#         'withdraws_to_work': withdraws_to_work,
#         'results': results,
#     }
#     return render(request, template, context)


class IncomingStatSearchView(ListView):
    model = Incoming
    template_name = 'deposit/incomings_list_stat.html'  # тот же шаблон
    # context_object_name = 'page_obj'

    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().order_by('-register_date')
        self.filterset = IncomingStatSearch(self.request.GET, queryset=qs)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = self.filterset.form
        # Статистика по фильтру
        qs = self.filterset.qs
        context['filtered_total'] = qs.aggregate(
            total_pay=Sum('pay'),
            count=Count('id')
        )
        return context


class BirpayOrderRawView(StaffOnlyPerm, DetailView):
    model = BirpayOrder
    template_name = 'deposit/birpay_order_raw.html'
    slug_field = 'birpay_id'
    slug_url_kwarg = 'birpay_id'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        raw = self.object.raw_data
        try:
            context['raw_json_pretty'] = json.dumps(raw, ensure_ascii=False, indent=2)
        except Exception:
            context['raw_json_pretty'] = raw  # если вдруг невалидный JSON

        check_hash = self.object.check_hash
        if check_hash:
            duplicates = BirpayOrder.objects.filter(
                check_hash=check_hash
            ).exclude(id=self.object.id)
        else:
            duplicates = BirpayOrder.objects.none()
        context['duplicates'] = duplicates
        return context


class BirpayOrderView(StaffOnlyPerm, ListView):
    model = BirpayOrder
    template_name = 'deposit/birpay_orders.html'
    paginate_by = 100
    filterset_class = BirpayOrderFilter

    def get_queryset(self):
        qs = super().get_queryset().order_by('-created_at')
        # Аннотируем данными из связанного Incoming (через OneToOneField)
        qs = qs.annotate(
            incoming_pay=F('incoming__pay'),
            delta=ExpressionWrapper(F('incoming__pay') - F('amount'), output_field=FloatField()),
        )
        self.filterset = BirpayOrderFilter(self.request.GET, queryset=qs)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = self.filterset.form
        gpt_auto_approve = Options.load().gpt_auto_approve
        context['gpt_auto_approve'] = gpt_auto_approve

        show_stat = self.filterset.form.cleaned_data.get('show_stat')
        if show_stat:
            qs = self.filterset.qs
            total_count = qs.count()
            stats = {
                'total': total_count,
                'with_incoming': qs.exclude(incoming__isnull=True).count(),
                'sum_incoming_pay': qs.aggregate(sum=Sum('incoming_pay'))['sum'] or 0,
                'sum_amount': qs.aggregate(sum=Sum('amount'))['sum'] or 0,
                'sum_delta': qs.aggregate(sum=Sum('delta'))['sum'] or 0,
                'status_0': qs.filter(status=0).count(),
                'status_1': qs.filter(status=1).count(),
                'status_2': qs.filter(status=2).count(),
                'gpt_approve': int(qs.filter(gpt_flags=31).count() / total_count * 100) if total_count else 0
            }
            context['birpay_stats'] = stats


        for order in context['page_obj']:
            if hasattr(order, 'raw_data'):
                try:
                    order.raw_data_json = json.dumps(order.raw_data, ensure_ascii=False, cls=DjangoJSONEncoder)
                except Exception:
                    order.raw_data_json = '{}'
            else:
                order.raw_data_json = '{}'


        return context

class BirpayOrderInfoView(StaffOnlyPerm, DetailView):
    model = BirpayOrder
    template_name = 'deposit/birpay_order_info.html'
    slug_field = 'birpay_id'
    slug_url_kwarg = 'birpay_id'

    def get_object(self, queryset=None):
        #Найдем возможные смс
        order = super().get_object(queryset)
        possible_incomings = find_possible_incomings(order.amount, order.created_at)
        logger.info(f'order: {order} possible_incomings: {possible_incomings}')
        order.incomings = possible_incomings
        # Данные по юзеру
        user_orders = BirpayOrder.objects.filter(merchant_user_id=order.merchant_user_id)
        order.total_orders = user_orders.count()
        user_orders_1 = user_orders.filter(status=1).count()
        user_orders_0 = user_orders.filter(status=0).count()
        order.user_orders_1 = user_orders_1
        order.user_orders_0 = user_orders_0
        order.user_order_percent = round(user_orders_1 / order.total_orders * 100, 0 )
        return order

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        raw = self.object.raw_data
        gpt_data = self.object.gpt_data
        if isinstance(gpt_data, str):
            gpt_data = json.loads(gpt_data)
        context['gpt_data'] = gpt_data
        logger.info(f'gpt_data: {gpt_data} {type(gpt_data)}')
        try:
            context['raw_json_pretty'] = json.dumps(raw, ensure_ascii=False, indent=2)
        except Exception:
            context['raw_json_pretty'] = raw  # если вдруг невалидный JSON



        check_hash = self.object.check_hash
        if check_hash:
            duplicates = BirpayOrder.objects.filter(
                check_hash=check_hash
            ).exclude(id=self.object.id)
            for dublicate in duplicates:
                dublikate_incoming = Incoming.objects.filter(birpay_id=dublicate.merchant_transaction_id).first()
                dublicate.incoming = dublikate_incoming

        else:
            duplicates = BirpayOrder.objects.none()
        context['duplicates'] = duplicates
        return context

@login_required
@user_passes_test(lambda u: u.is_staff)  # Или любая ваша проверка прав
def assign_cards_to_user(request):
    assigned_cards = []
    selected_user = None
    only_my = bool(request.GET.get('only_my', ''))
    User = get_user_model()

    if request.method == 'POST':
        form = AssignCardsToUserForm(request.POST)
        if form.is_valid():
            selected_user = form.cleaned_data['user']
            cards_list = form.cleaned_data['assigned_card_numbers']
            profile = selected_user.profile
            profile.assigned_card_numbers = cards_list  # Для ArrayField — это список!
            profile.save()
            assigned_cards = cards_list
            messages.success(request, f"Карты назначены для {selected_user.username}")
        else:
            selected_user = form.cleaned_data.get('user', None)
            if selected_user:
                profile = selected_user.profile
                cards = profile.assigned_card_numbers or []
                # На случай, если поле — строка
                if isinstance(cards, str):
                    cards = [x.strip() for x in cards.split(',') if x.strip()]
                assigned_cards = cards
    else:
        form = AssignCardsToUserForm()
        user_id = request.GET.get('user_id')
        if user_id:
            try:
                selected_user = User.objects.get(pk=user_id, is_staff=True)
                profile = selected_user.profile
                cards = profile.assigned_card_numbers or []
                if isinstance(cards, str):
                    cards = [x.strip() for x in cards.split(',') if x.strip()]
                assigned_cards = cards
                form = AssignCardsToUserForm(initial={
                    'user': selected_user,
                    'assigned_card_numbers': '\n'.join(assigned_cards),
                })
            except User.DoesNotExist:
                pass

    # Формируем таблицу всех staff-юзеров и их назначенных карт
    staff_users = User.objects.filter(is_staff=True, role='staff', is_active=True).select_related('profile')
    users_cards = []
    for user in staff_users:
        profile = getattr(user, 'profile', None)
        cards = []
        if profile:
            cards = profile.assigned_card_numbers or []
            if isinstance(cards, str):
                cards = [x.strip() for x in cards.split(',') if x.strip()]

        if only_my:
            # Показывать только назначенные карты для ТЕКУЩЕГО пользователя
            if user == request.user:
                users_cards.append((user, cards))
        else:
            # Показывать все карты, как раньше
            users_cards.append((user, cards))

    return render(request, 'deposit/assign_cards_to_user.html', {
        'form': form,
        'assigned_cards': assigned_cards,
        'selected_user': selected_user,
        'users_cards': users_cards,
        'only_my': only_my,
    })



def get_user_card_numbers(user):
    profile = getattr(user, 'profile', None)
    if not profile or not profile.assigned_card_numbers:
        return []
    return profile.assigned_card_numbers


class BirpayPanelView(StaffOnlyPerm, ListView):
    template_name = 'deposit/birpay_panel.html'
    paginate_by = 100
    model = BirpayOrder
    filterset_class = BirpayPanelFilter

    def get_queryset(self):
        now = timezone.now()
        if settings.DEBUG:
            threshold = now - datetime.timedelta(days=50)
        else:
            threshold = now - datetime.timedelta(minutes=30)
        qs = super().get_queryset().filter(sended_at__gt=threshold, status_internal__in=[0, 1]).order_by('-created_at')

        incoming_qs = Incoming.objects.filter(
            birpay_id=OuterRef('merchant_transaction_id')
        ).order_by('-register_date')
        qs = qs.annotate(
            incoming_pay=Subquery(incoming_qs.values('pay')[:1]),
            delta=ExpressionWrapper(
                Subquery(incoming_qs.values('pay')[:1]) - F('amount'),
                output_field=FloatField()
            ),
            # incoming_id=Subquery(incoming_qs.values('id')[:1]),
            incoming_register_date=Subquery(incoming_qs.values('register_date')[:1]),
        )
        user_card_numbers = get_user_card_numbers(self.request.user)
        self.filterset = BirpayPanelFilter(
            self.request.GET,
            queryset=qs,
            request=self.request,
            user_card_numbers=user_card_numbers
        )
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = self.filterset.form
        # now = timezone.now()
        # threshold = now - datetime.timedelta(minutes=30)
        # incomings = Incoming.objects.filter(birpay_id__isnull=True, register_date__gte=threshold).order_by('-register_date')[:50]
        # context["incomings"] = incomings
        context['selected_card_numbers'] = self.request.GET.getlist('card_number')
        context['statuses'] = self.request.GET.getlist('status')
        context['only_my'] = self.request.GET.getlist('only_my')
        logger.info(self.request.GET.getlist('card_number'))
        logger.info(self.request.GET.getlist('status'))
        logger.info(self.request.GET.getlist('only_my'))
        user = self.request.user
        last_confirmed_order = BirpayOrder.objects.filter(confirmed_operator=user).order_by('-confirmed_time').first()
        if last_confirmed_order:
            context['last_confirmed_order_id'] = last_confirmed_order.id

        return context

    def post(self, request, *args, **kwargs):
        # исходный URL
        query = []
        filter_keys = ['card_number', 'status', 'only_my']
        logger.info(f'{request.POST.dict()}')
        for key in filter_keys:
            for value in request.POST.getlist(key):
                query.append(f"{key}={value}")
        query_string = '&'.join(query)

        try:
            logger.info(f'POST: {request.POST.dict()}')
            post_data = request.POST.dict()
            new_amount = 0
            order = None
            incoming_id = ''
            action = None
            for name, value in post_data.items():
                if name.startswith('orderconfirm'):
                    order_id = name.split('orderconfirm_')[1]
                    incoming_id = value.strip()
                    order = BirpayOrder.objects.get(pk=order_id)
                    logger.info(f'Для {order} сохраняем смс {incoming_id}')
                    bind_contextvars(merchant_transaction_id=order.merchant_transaction_id)
                elif name.startswith('orderamount'):
                    new_amount = float(value)
                    logger.info(f'new_amount: {new_amount}')
                elif name.startswith('order_action_'):
                    action = value
                    logger.info(f'action: {action}')

            update_fields = []
            # bind_contextvars(birpay_id=order.merchant_transaction_id)
            # смена суммы
            if order.amount != new_amount:
                if order.status != 0:
                    text = f'Не удалось сменить сумму {order} mtx_id {order.merchant_transaction_id}: Статус не pending'
                    logger.warning(text)
                    messages.add_message(request, messages.WARNING, text)
                    # raise ValidationError(text)
                else:
                    logger.info(f'Меняем amount с {order.amount} на {new_amount}')
                    response = change_amount_birpay(pk=order.birpay_id, amount=new_amount)
                    if response.status_code == 200:
                        text = f"Сумма {order} mtx_id {order.merchant_transaction_id} изменена с {order.amount} на {new_amount}"
                        logger.info(text)
                        messages.add_message(request, messages.INFO, text)
                        order.amount = new_amount
                        update_fields.append('amount')
                    else:
                        messages.add_message(request, messages.ERROR, "Сумма не изменена")

            # Обработка действий
            if action == 'hide':
                logger.info('hide')
                order.status_internal = -2
                update_fields.extend(['status_internal'])
                order.save(update_fields=update_fields)
            elif action == 'pending':
                logger.info('pending')
                order.save(update_fields=update_fields)
            elif action == 'approve':
                logger.info('approve')
                if incoming_id == '':
                    text = f'Не указана свободная смс {incoming_id}'
                    logger.warning(text)
                    messages.add_message(request, messages.ERROR, text)
                    return HttpResponseRedirect(f"{request.path}?{query_string}")
                else:
                    incoming_to_approve = Incoming.objects.filter(pk=incoming_id, birpay_id__isnull=True).first()
                    if not incoming_to_approve:
                        text = f'Не найдена свободная смс {incoming_id}'
                        logger.warning(text)
                        messages.add_message(request, messages.ERROR, text)
                        return HttpResponseRedirect(f"{request.path}?{query_string}")
                    else:
                        #Апрувнем заявку
                        logger.info('Апрувнем заявку')
                        response = approve_birpay_refill(pk=order.birpay_id)
                        if response.status_code != 200:
                            text = f"ОШИБКА пдтверждения {order} mtx_id {order.merchant_transaction_id}: {response.text}"
                            messages.add_message(request, messages.ERROR, text)
                            logger.warning(text)
                        else:
                            # Апрувнем заявку
                            text = f"Заявка {order} mtx_id {order.merchant_transaction_id} подтверждена в birpay с суммой {order.amount}"
                            logger.info(text)
                            messages.add_message(request, messages.INFO, text)
                            order.status = 1
                            order.status_internal = 1


                            with transaction.atomic():
                                operator = self.request.user
                                order.incomingsms_id = incoming_id
                                order.confirmed_operator = operator
                                order.confirmed_time = timezone.now()
                                order.incoming = incoming_to_approve
                                order.save()
                                incoming_to_approve.birpay_id = order.merchant_transaction_id
                                incoming_to_approve.save()
                                logger.info(f'"Заявка {order} mtx_id {order.merchant_transaction_id} успешно подтверждена')

            return HttpResponseRedirect(f"{request.path}?{query_string}")
        except Exception as e:
            logger.error(e, exc_info=True)
            return HttpResponseBadRequest(content=f'Ошибка при обработке заявки: {e}')
@staff_member_required()
def test(request):
    options = Options.load()
    o = BirpayOrder.objects.first()
    u_orders = BirpayOrder.objects.filter(merchant_user_id=o.merchant_user_id, card_number__isnull=False)
    logger.info(f'Всего: {u_orders.count()}')
    for u in u_orders:
        logger.info(f'{u.card_number}')
    context = {}
    return render(request=request, template_name='deposit/moshennik_list.html', context=context)



@staff_member_required()
def mark_as_jail(request, pk):
    incoming = Incoming.objects.get(pk=pk)
    incoming.is_jail = not incoming.is_jail
    incoming.save()
    return redirect(reverse('deposit:incoming_edit', args=[incoming.id]))

class BirpayUserStatView(StaffOnlyPerm, TemplateView):
    template_name = 'deposit/birpay_user_stats.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stats = []

        senders_dict = {}

        # Только те, у кого gpt_data — dict, и есть ключ sender, и он не пустой
        orders = BirpayOrder.objects.exclude(gpt_data={}).only('merchant_user_id', 'gpt_data')
        for order in orders:
            gpt = order.gpt_data
            gpt_sender = None
            if isinstance(gpt, dict):
                gpt_sender = gpt.get('sender')
            if gpt_sender:  # != None, != ''
                senders_dict.setdefault(order.merchant_user_id, set()).add(gpt_sender)

        filtered_user_ids = list(senders_dict.keys())

        if filtered_user_ids:
            qs = (
                BirpayOrder.objects.filter(merchant_user_id__in=filtered_user_ids)
                .values('merchant_user_id')
                .annotate(
                    last_date=Max('created_at'),
                    total_count=Count('id'),
                    status1_count=Count('id', filter=Q(status=1)),
                )
                .order_by('-last_date')
            )

            for stat in qs:
                merchant_user_id = stat['merchant_user_id']
                unique_senders = list(senders_dict.get(merchant_user_id, []))
                stat['uniq_card_count'] = len(unique_senders)
                stat['unique_cards'] = unique_senders
                if stat['uniq_card_count'] > 5:  # <--- вот фильтр
                    stats.append(stat)

        context['stats'] = stats
        return context

@staff_member_required()
def moshennik_list(request):
    options = Options.load()
    birpay_moshennik_list = options.birpay_moshennik_list
    form = MoshennikListForm(request.POST)

    if request.method == 'POST':
        if form.is_valid():
            m_list = form.cleaned_data['moshennik_list']
            logger.info(f'{m_list} {type(m_list)}')
            options.birpay_moshennik_list = m_list
            options.save(update_fields=['birpay_moshennik_list'])
    else:
        form = MoshennikListForm(initial={'moshennik_list': '\n'.join(birpay_moshennik_list)})
    context = {'form': form}
    return render(request=request, template_name='deposit/moshennik_list.html', context=context)

@staff_member_required()
def painter_list(request):
    options = Options.load()
    birpay_painter_list = options.birpay_painter_list
    form = PainterListForm(request.POST)

    if request.method == 'POST':
        if form.is_valid():
            p_list = form.cleaned_data['painter_list']
            logger.info(f'{p_list} {type(p_list)}')
            options.birpay_painter_list = p_list
            options.save(update_fields=['birpay_painter_list'])
    else:
        form = PainterListForm(initial={'painter_list': '\n'.join(birpay_painter_list)})
    context = {'form': form}
    return render(request=request, template_name='deposit/painter_list.html', context=context)

@staff_member_required()
def show_birpay_order_log(request, query_string):
    from subprocess import PIPE, STDOUT, Popen
    if not request.user.is_superuser:
        return HttpResponseBadRequest()

    if request.method == 'GET':
        output_text = ''
        with open('bash_request.sh', 'w', encoding='UTF-8') as file:
            file.write(
                f'#!/bin/sh\n'
                f'cat logs/deposit.log | grep {str(query_string)}'
            )
        command = ["bash", "bash_request.sh"]
        process = Popen(command, stdout=PIPE, stderr=STDOUT)
        output = process.stdout.read()
        exitstatus = process.poll()
        txt = output.decode()
        txt = txt.replace('\n', '<br>')
        output_text += txt
        output_text += '<br><br>'

        import ansiconv
        plain = ansiconv.to_plain(output_text)
        html = ansiconv.to_html(output_text)
        css = ansiconv.base_css()
        html_log = f'<html><head><style>{css}</style></head><body style="background: black"><pre class="ansi_fore ansi_back">{html}</pre></body></html>'
        return HttpResponse(html_log)


