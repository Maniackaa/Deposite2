import base64
import datetime
import json
import logging
import os
import uuid
from pathlib import Path
from sys import platform
from tempfile import NamedTemporaryFile
from types import NoneType

import django.core.files.storage
import pytesseract
import pytz
import structlog
from django.db.models.functions import Lag

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q, OuterRef, Window, Exists
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.stat_func import cards_report, bad_incomings, get_img_for_day_graph, day_reports_birpay_confirm, \
    day_reports_orm
from deposit.forms import (ColorBankForm, DepositEditForm, DepositForm,
                           DepositImageForm, DepositTransactionForm,
                           IncomingForm, MyFilterForm, IncomingSearchForm, CheckSmsForm, CheckScreenForm)
from deposit.permissions import SuperuserOnlyPerm
from deposit.views_api import response_sms_template
from ocr.ocr_func import (make_after_save_deposit, response_text_from_image)
from deposit.models import Deposit, Incoming, TrashIncoming, IncomingChange, Message, \
    MessageRead, RePattern, IncomingCheck

logger = structlog.get_logger(__name__)
err_log = logging.getLogger('error_log')


@staff_member_required(login_url='users:login')
def home(request, *args, **kwargs):
    template = 'deposit/home.html'
    return render(request, template_name=template)


def index(request, *args, **kwargs):
    try:
        logger.debug(f'index {request}')
        uid = uuid.uuid4()
        form = DepositForm(request.POST or None, files=request.FILES or None, initial={'phone': '+994', 'uid': uid})
        if request.method == 'POST':
            logger.debug('index POST')
            data = request.POST
            uid = data.get('uid')
            deposit = Deposit.objects.filter(uid=uid).first()
            if deposit:
                template = 'deposit/deposit_created.html'
                form = DepositTransactionForm(request.POST, instance=deposit)
                context = {'form': form, 'deposit': deposit}
                return render(request, template_name=template, context=context)
            if form.is_valid():
                logger.debug('form valid')
                form.save()
                uid = form.cleaned_data.get('uid')
                deposit = Deposit.objects.get(uid=uid)
                logger.debug(f'form save')
                template = 'deposit/deposit_created.html'
                form = DepositTransactionForm(request.POST, instance=deposit)
                context = {'form': form, 'deposit': deposit}
                return render(request, template_name=template, context=context)
            else:
                template = 'deposit/index.html'
                context = {'form': form}
                return render(request, template_name=template, context=context)
        context = {'form': form}
        template = 'deposit/index.html'
        return render(request, template_name=template, context=context)
    except Exception as err:
        logger.error('ошибка', exc_info=True)
        raise err


def deposit_created(request):
    logger.debug(f'deposit_created: {request}')
    if request.method == 'POST':
        data = request.POST
        uid = data['uid']
        phone = data['phone']
        pay = data['pay_sum']
        deposit = Deposit.objects.filter(uid=uid).exists()
        if not deposit:
            form = DepositTransactionForm(request.POST or None, files=request.FILES or None, initial={'phone': phone, 'uid': uid, 'pay_sum': pay})
            if form.is_valid():
                form.save()
                logger.debug('Форма сохранена')
            else:
                logger.debug(f'Форма не валидная: {form}')
                for error in form.errors:
                    logger.warning(f'{error}')
                template = 'deposit/index.html'
                context = {'form': form}
                return render(request, template_name=template, context=context)

        deposit = Deposit.objects.get(uid=uid)
        logger.debug(f'deposit: {deposit}')
        input_transaction = data.get('input_transaction') or None
        logger.debug(f'input_transaction: {input_transaction}')
        deposit.input_transaction = input_transaction
        form = DepositTransactionForm(request.POST, files=request.FILES, instance=deposit)
        if form.is_valid():
            deposit = form.save()
            make_after_save_deposit(deposit)
        template = 'deposit/deposit_created.html'

        context = {'form': form, 'deposit': deposit, 'pay_screen': None}
        return render(request, template_name=template, context=context)


def deposit_status(request, uid):
    logger.debug(f'deposit_status {request}')
    template = 'deposit/deposit_status.html'
    deposit = get_object_or_404(Deposit, uid=uid)
    form = DepositImageForm(request.POST, files=request.FILES, instance=deposit)
    if request.method == 'POST' and form.has_changed():
        logger.debug(f'has_changed: {form.has_changed()}')
        if form.is_valid():
            form.save()
        else:
            form = DepositTransactionForm(instance=deposit, files=request.FILES)
            template = 'deposit/deposit_created.html'
            context = {'form': form, 'deposit': deposit, 'pay_screen': deposit.pay_screen}
            return render(request, template_name=template, context=context)
        form = DepositImageForm(instance=deposit)
        context = {'deposit': deposit, 'form': form}
        return render(request, template_name=template, context=context)
    # form = DepositImageForm(initial=deposit.__dict__, instance=deposit)
    context = {'deposit': deposit, 'form': form}
    logger.debug(f'has_changed: {form.has_changed()}')

    return render(request, template_name=template, context=context)


def make_page_obj(request, objects, numbers_of_posts=settings.PAGINATE):
    paginator = Paginator(objects, numbers_of_posts)
    page_number = request.GET.get('page')
    return paginator.get_page(page_number)


@staff_member_required(login_url='users:login')
def deposits_list(request):
    template = 'deposit/deposit_list.html'
    deposits = Deposit.objects.order_by('-id').all()
    context = {'page_obj': make_page_obj(request, deposits)}
    return render(request, template, context)


@staff_member_required(login_url='users:login')
def deposits_list_pending(request):
    template = 'deposit/deposit_list.html'
    deposits = Deposit.objects.order_by('-id').filter(status='pending').all()
    context = {'page_obj': make_page_obj(request, deposits)}
    return render(request, template, context)


@staff_member_required(login_url='users:login')
def deposit_edit(request, pk):
    deposit_from_pk = get_object_or_404(Deposit, pk=pk)
    template = 'deposit/deposit_edit.html'
    incomings = Incoming.objects.filter(confirmed_deposit=None).order_by('-id').all()
    form = DepositEditForm(data=request.POST or None, files=request.FILES or None,
                           instance=deposit_from_pk,
                           initial={'confirmed_incoming': deposit_from_pk.confirmed_incoming,
                                    'status': deposit_from_pk.status})
    if request.method == 'POST':
        old_confirmed_incoming = deposit_from_pk.confirmed_incoming
        if old_confirmed_incoming:
            old_confirmed_incoming_id = old_confirmed_incoming.id
        else:
            old_confirmed_incoming_id = None
        new_confirmed_incoming_id = request.POST.get('confirmed_incoming') or None

        if form.is_valid() and form.has_changed():
            saved_deposit = form.save()
            if old_confirmed_incoming_id and new_confirmed_incoming_id:
                # 'ветка 1. Чек меняется с одного на другое'
                old_incoming = Incoming.objects.get(id=old_confirmed_incoming_id)
                old_incoming.confirmed_deposit = None
                old_incoming.save()
                new_incoming = Incoming.objects.get(id=new_confirmed_incoming_id)
                new_incoming.confirmed_deposit = saved_deposit
                new_incoming.save()
            elif new_confirmed_incoming_id and not old_confirmed_incoming_id:
                # 'ветка 2. Было пусто стало новый чек'
                saved_deposit.status = 'approved'
                saved_deposit.save()
                incoming = Incoming.objects.get(id=new_confirmed_incoming_id)
                incoming.confirmed_deposit = saved_deposit
                incoming.save()
            else:
                # 'ветка 3. Удален чек'
                saved_deposit.status = 'pending'
                saved_deposit.save()
                if old_confirmed_incoming:
                    old_confirmed_incoming.confirmed_deposit = None
                old_confirmed_incoming.save()
            # form = DepositEditForm(data=request.POST, files=request.FILES or None,
            form = DepositEditForm(instance=deposit_from_pk)
            context = {'deposit': saved_deposit, 'form': form, 'page_obj': make_page_obj(request, incomings)}
            return render(request, template_name=template, context=context)
    context = {'deposit': deposit_from_pk, 'form': form, 'page_obj': make_page_obj(request, incomings)}
    return render(request, template, context)


class ShowDeposit(DetailView):
    model = Deposit
    template_name = 'deposit/deposit_edit.html'


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
            #Сохраняем историю
            new_history = IncomingChange(
                incoming=incoming,
                user=request.user,
                val_name='birpay_id',
                new_val=value
            )
            new_history.save()

        if 'filter' in options:
            return redirect('deposit:incomings_filter')
        else:
            return redirect('deposit:incomings')

    template = 'deposit/incomings_list.html'
    incoming_q = Incoming.objects.raw(
    """
    SELECT *,
    LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
    LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
    FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    ORDER BY deposit_incoming.id DESC LIMIT 5000;
    """
    )
    last_id = Incoming.objects.order_by('id').last()
    if last_id:
        last_id = last_id.id
    # last_bad = BadScreen.objects.order_by('-id').first()
    last_bad = Message.objects.filter(type='macros').order_by('-id').first()
    last_bad_id = last_bad.id if last_bad else last_bad
    context = {'page_obj': make_page_obj(request, incoming_q),
               'last_id': last_id,
               'last_bad_id': last_bad_id}
    return render(request, template, context)


@staff_member_required(login_url='users:login')
def incoming_list2(request):
    # Список всех платежей и сохранение birpay
    if request.method == "POST":
        input_name = list(request.POST.keys())[1]
        pk, options = list(request.POST.keys())[1].split('-')
        value = request.POST.get(input_name) or ''
        incoming = Incoming.objects.get(pk=pk)
        if isinstance(incoming.birpay_id, NoneType):
            incoming.birpay_id = value
            incoming.birpay_confirm_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            incoming.save()
            #Сохраняем историю
            new_history = IncomingChange(
                incoming=incoming,
                user=request.user,
                val_name='birpay_id',
                new_val=value
            )
            new_history.save()

        if 'filter' in options:
            return redirect('deposit:incomings_filter')
        else:
            return redirect('deposit:incomings')

    template = 'deposit/incomings_list.html'
    # incoming_q = Incoming.objects.order_by('-id').all()
    # incoming_q = Incoming.objects.raw(
    #     "with t1 as (SELECT * FROM deposit_colorbank) "
    #                                       "SELECT * FROM deposit_incoming LEFT JOIN t1 ON t1.name = deposit_incoming.sender"
    #                                       " ORDER BY deposit_incoming.id DESC;")

    incoming_q = Incoming.objects.raw(
    """
    SELECT *,
    LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by register_date desc, deposit_incoming.response_date desc) as prev_balance,
    LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by register_date desc, deposit_incoming.response_date desc) + pay as check_balance
    FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
    ORDER BY deposit_incoming.id DESC;
    """
    )

    last_id = Incoming.objects.order_by('id').last()
    if last_id:
        last_id = last_id.id
    # last_bad = BadScreen.objects.order_by('-id').first()
    last_bad = Message.objects.filter(type='macros').order_by('-id').first()
    last_bad_id = last_bad.id if last_bad else last_bad
    context = {'page_obj': make_page_obj(request, incoming_q),
               'last_id': last_id,
               'last_bad_id': last_bad_id
               }
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
        return empty_incoming


class IncomingCheckList(SuperuserOnlyPerm, ListView):
    model = IncomingCheck
    paginate_by = settings.PAGINATE
    template_name = 'deposit/incoming_checks_list.html'


class IncomingFiltered(ListView):
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
        filtered_incoming = Incoming.objects.raw(
        """
        SELECT *,
        LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) as prev_balance,
        LAG(balance, -1) OVER (PARTITION BY deposit_incoming.recipient order by response_date desc, balance desc, deposit_incoming.id desc) + pay as check_balance
        FROM deposit_incoming LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
        WHERE deposit_incoming.recipient = ANY(%s)
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
    paginate_by = 1000
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
        pk = self.request.GET.get('pk', 1)
        sort_by_sms_time = self.request.GET.get('sort_by_sms_time', 0)
        end_time = None
        tz = pytz.timezone(settings.TIME_ZONE)
        start_time = ''
        if pk:
            return Incoming.objects.filter(birpay_id__contains=pk)
        if sort_by_sms_time:
            all_incoming = Incoming.objects.order_by('-response_date').all()
        else:
            all_incoming = Incoming.objects.order_by('-id').all()
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
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if request.user.has_perm('deposit.can_hand_edit'):
            self.object = self.get_object()
            return super().post(request, *args, **kwargs)
        return HttpResponseForbidden('У вас нет прав делать ручную корректировку')

    def get_context_data(self, **kwargs):
        context = super(IncomingEdit, self).get_context_data(**kwargs)
        history = self.object.history.order_by('-id').all()
        context['history'] = history
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

    template = 'deposit/stats.html'
    page_obj = bad_incomings()
    cards = cards_report()
    # days_stat_dict = day_reports(100)
    days_stat_dict = day_reports_orm(100)
    context = {'page_obj': page_obj, 'cards': cards, 'day_reports': days_stat_dict}
    return render(request, template, context)


@staff_member_required(login_url='users:login')
def get_stats2(request):
    # Статистика по времени подтверждения платежа
    template = 'deposit/stats2.html'
    page_obj = bad_incomings()
    cards = cards_report()
    days_stat_dict = day_reports_birpay_confirm(100)
    context = {'page_obj': page_obj, 'cards': cards, 'day_reports': days_stat_dict}
    return render(request, template, context)


def day_graph(request):
    if not request.user.is_superuser:
        raise PermissionDenied('Недостаточно прав')
    template = 'deposit/test.html'
    encoded_file = get_img_for_day_graph()
    context = {'fig1': encoded_file}
    return render(request, template, context)


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
    data = {}
    black = int(data.get('black', 182))
    white = int(data.get('white', 255))
    logger.info(data.get('lang'))
    lang = data.get('lang', 'eng')
    oem = int(data.get('oem', 0))
    psm = int(data.get('psm', 6))

    print(platform == 'win32')
    logger.debug('asf')
    context = {}
    template = 'deposit/check_screen.html'
    form = CheckScreenForm(request.POST)
    if request.method == 'POST':
        print('post')
        if form.is_valid():
            print('valid')
            screen = form.cleaned_data.get('screen')
            image_bytes = screen.image.read()
            # char_whitelist = '+- :;*•0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
            # xy = {'y_start': 5, 'y_end': 10, 'x_start': 10, 'x_end': 100}
            # first_stoke = response_text_from_image(image_bytes,
            #                                        black=black, white=white,
            #                                        oem=oem, psm=psm, lang=lang, strip=False,
            #                                        char_whitelist=char_whitelist, **xy).strip()
            # xy = {'y_start': 12, 'y_end': 29}
            # amount = response_text_from_image(image_bytes,
            #                                   black=black, white=white,
            #                                   oem=oem, psm=psm, lang=lang, strip=False,
            #                                   char_whitelist=char_whitelist, **xy).strip()
            # xy = {'y_start': 29, 'y_end': 70}
            # info = response_text_from_image(image_bytes,
            #                                 black=black, white=white,
            #                                 oem=oem, psm=4, lang=lang, strip=False,
            #                                 char_whitelist=char_whitelist, **xy).strip()
            # print(first_stoke)
            # print(amount)
            # print(info)

            # fs = django.core.files.storage.FileSystemStorage()
            # filename = fs.save('imname.jpg', screen.image)
            # file_url = fs.url(filename)
            # context['file_url'] = file_url
            context['x'] = 1


            # with NamedTemporaryFile() as temp_file:
            temp_file = NamedTemporaryFile(mode='wb', suffix='.jpg', prefix='prefix_', delete=False)
            temp_file.write(image_bytes)
            print(temp_file.name)

            context['file_url'] = temp_file.name



        else:
            print(form.errors)
    context['form'] = form


    return render(request, template, context)