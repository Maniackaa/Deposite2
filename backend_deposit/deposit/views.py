import base64
import datetime
import io
import json
import re
import uuid
from http import HTTPStatus
from tempfile import NamedTemporaryFile
from types import NoneType
from urllib.parse import urlparse

import requests

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
    Max, DurationField, Case, When, BooleanField
from django.http import HttpResponseForbidden, JsonResponse, HttpResponseBadRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.generic import CreateView, DetailView, ListView, UpdateView, TemplateView
from matplotlib import pyplot as plt
from rest_framework.views import APIView
from structlog.contextvars import bind_contextvars, clear_contextvars

from core.asu_pay_func import create_asu_withdraw, should_send_to_z_asu, send_birpay_order_to_z_asu
from core.birpay_new_func import get_um_transactions, send_transaction_action
from core.global_func import TZ, mask_compare, send_message_tg
from core.stat_func import cards_report, bad_incomings, get_img_for_day_graph, day_reports_birpay_confirm, \
    day_reports_orm
from deposit import tasks
from deposit.filters import IncomingCheckFilter, IncomingStatSearch, BirpayOrderFilter, BirpayPanelFilter
from deposit.forms import (
    ColorBankForm,
    IncomingForm,
    MyFilterForm,
    IncomingSearchForm,
    CheckSmsForm,
    CheckScreenForm,
    AssignCardsToUserForm,
    MoshennikListForm,
    PainterListForm,
    BirpayOrderCreateForm,
    OperatorStatsDayForm,
    RequsiteZajonForm,
    RequisiteCardEditForm,
)
from deposit.func import find_possible_incomings
from deposit.permissions import SuperuserOnlyPerm, StaffOnlyPerm
from deposit.tasks import (
    check_incoming,
    refresh_birpay_data,
    send_image_to_gpt_task,
    download_birpay_check_file,
    _download_birpay_check_file_sync,
    _parse_check_proxy,
)
from deposit.views_api import response_sms_template
from ocr.ocr_func import (make_after_save_deposit, response_text_from_image)
from deposit.models import (
    Incoming,
    TrashIncoming,
    IncomingChange,
    Message,
    MessageRead,
    RePattern,
    IncomingCheck,
    WithdrawTransaction,
    BirpayOrder,
    Bank,
    RequsiteZajon,
    RequsiteZajonChangeLog,
)

from core.birpay_client import BirpayClient
from deposit.birpay_requisite_service import update_requisite_on_birpay

from users.models import Options

logger = structlog.get_logger('deposit')


User = get_user_model()


REQUIRED_REFILL_METHOD_ID = 127
REQUIRED_REFILL_METHOD_NAME = 'AZN_azcashier_5_birpay'


def add_balance_mismatch_flag(incoming):
    """Добавляет атрибут balance_mismatch к объекту incoming для проверки несовпадения баланса после округления до 0.1"""
    try:
        if incoming.check_balance is not None and incoming.balance is not None:
            check_rounded = round(float(incoming.check_balance) * 10) / 10
            balance_rounded = round(float(incoming.balance) * 10) / 10
            incoming.balance_mismatch = check_rounded != balance_rounded
        else:
            incoming.balance_mismatch = False
    except (ValueError, TypeError, AttributeError) as e:
        incoming.balance_mismatch = False
        logger.error(f'add_balance_mismatch_flag: ошибка для incoming.id={incoming.id}: {e}')
    return incoming



def _has_requisite_access(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    role = getattr(user, 'role', None)
    return user.is_superuser or role in ('admin', 'editor')


def _parse_iso_datetime(value: str | None):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        logger.warning('Failed to parse datetime', value=value)
        return None


def _has_required_method(refill_methods):
    if not refill_methods:
        return False
    for method in refill_methods:
        if (
            method.get('id') == REQUIRED_REFILL_METHOD_ID
            and method.get('name') == REQUIRED_REFILL_METHOD_NAME
        ):
            return True
    return False


def extract_card_number_digits(card_number_str):
    """
    Извлекает только цифры из строки номера карты.
    Удаляет все нецифровые символы (пробелы, буквы, знаки препинания).
    Обрезает результат до 32 символов (максимальная длина поля card_number).
    
    Args:
        card_number_str: Строка с номером карты (может содержать дополнительный текст)
    
    Returns:
        str: Только цифры из строки (максимум 32 символа)
    """
    if not card_number_str:
        return ''
    # Извлекаем только цифры
    digits_only = re.sub(r'\D', '', str(card_number_str))
    # Обрезаем до максимальной длины поля (32 символа)
    return digits_only[:32]


def sync_requsite_zajon():
    """
    Синхронизация реквизитов Birpay с локальной базой.
    Создает/обновляет записи только для нужного метода пополнения.
    """
    sync_result = {
        'created': 0,
        'updated': 0,
        'deleted': 0,
        'total': 0,
        'missing_ids': [],
        'error': None,
    }
    try:
        remote_data = BirpayClient().get_requisites()
    except Exception as err:
        logger.error('Не удалось получить реквизиты Birpay', exc_info=True)
        sync_result['error'] = str(err)
        return sync_result

    filtered = [
        row for row in remote_data
        if _has_required_method(row.get('refillMethodTypes'))
    ]
    sync_result['total'] = len(filtered)

    target_ids = [row.get('id') for row in filtered if row.get('id') is not None]
    existing_map = {
        obj.pk: obj for obj in RequsiteZajon.objects.filter(pk__in=target_ids)
    }

    seen_ids = set()

    with transaction.atomic():
        for row in filtered:
            pk = row.get('id')
            if pk is None:
                logger.warning('Пропущена запись без id', row=row)
                continue

            # logger.info(f'{row}')
            existing = existing_map.get(pk)

            created_at = _parse_iso_datetime(row.get('createdAt')) or timezone.now()
            updated_at = _parse_iso_datetime(row.get('updatedAt')) or created_at
            remote_changed = existing is None or existing.updated_at != updated_at

            payload_data = row.get('payload') or {}
            payload_copy = dict(payload_data)

            # Получаем сырое значение card_number из payload (может содержать дополнительный текст)
            raw_card_number = payload_copy.get('card_number', '') or ''
            
            if existing and not remote_changed:
                # Для существующих записей без изменений на сервере:
                # Если сырое значение есть в payload, пересчитываем card_number из него
                # Иначе сохраняем текущее значение card_number
                if raw_card_number:
                    # Пересчитываем card_number из сырого значения для синхронизации
                    card_number_value = extract_card_number_digits(raw_card_number)
                else:
                    # Если сырого значения нет, сохраняем текущее
                    card_number_value = existing.card_number
                    raw_card_number = existing.payload.get('card_number', '') if existing.payload else '' or existing.card_number or ''
                active_value = existing.active
            else:
                # Для новых записей или измененных на сервере:
                # Извлекаем только цифры для поля card_number (максимум 32 символа)
                card_number_value = extract_card_number_digits(raw_card_number)
                active_value = row.get('active', False)

            # В payload сохраняем сырое значение из Birpay (с дополнительным текстом)
            payload_copy['card_number'] = raw_card_number

            common_fields = {
                'active': active_value,
                'agent_id': row.get('agentId'),
                'agent_name': row.get('agentName') or '',
                'name': row.get('name') or '',
                'weight': row.get('weight') or 0,
                'created_at': created_at,
                'updated_at': updated_at,
                'payment_requisite_filter_id': row.get('paymentRequisiteFilterId'),
                'card_number': card_number_value,
                'refill_method_types': row.get('refillMethodTypes') or [],
                'payload': payload_copy,
                'users': row.get('users') or [],
            }

            if existing:
                for field, value in common_fields.items():
                    setattr(existing, field, value)
                existing._change_source = 'sync'
                existing.save(update_fields=list(common_fields.keys()))
                sync_result['updated'] += 1
            else:
                RequsiteZajon.objects.create(id=pk, **common_fields)
                sync_result['created'] += 1

            seen_ids.add(pk)

        missing_ids = list(
            RequsiteZajon.objects.exclude(pk__in=seen_ids).values_list('pk', flat=True)
        )
        sync_result['missing_ids'] = missing_ids
        sync_result['deleted'] = len(missing_ids)

    return sync_result


class RequsiteZajonListView(StaffOnlyPerm, ListView):
    model = RequsiteZajon
    template_name = 'deposit/requsite_zajon_list.html'
    context_object_name = 'requisites'
    paginate_by = settings.PAGINATE
    missing_ids = ()

    def dispatch(self, request, *args, **kwargs):
        if not _has_requisite_access(request.user):
            return redirect('deposit:index')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        self.sync_result = sync_requsite_zajon()
        if self.sync_result.get('error'):
            messages.error(self.request, f"Ошибка обновления реквизитов: {self.sync_result['error']}")
        self.missing_ids = set(self.sync_result.get('missing_ids', []))
        return RequsiteZajon.objects.order_by('-updated_at', '-weight')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sync_result'] = getattr(self, 'sync_result', {})
        context['method_name'] = REQUIRED_REFILL_METHOD_NAME
        context['missing_ids'] = list(getattr(self, 'missing_ids', set()))
        return context


class RequsiteZajonUpdateView(StaffOnlyPerm, UpdateView):
    model = RequsiteZajon
    form_class = RequsiteZajonForm
    template_name = 'deposit/requsite_zajon_form.html'
    success_url = reverse_lazy('deposit:requisite_zajon_list')

    def dispatch(self, request, *args, **kwargs):
        if not _has_requisite_access(request.user):
            return redirect('deposit:index')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        changed_fields = form.changed_data
        self.object = form.save(commit=False)
        
        log = logger.bind(
            requisite_id=self.object.pk,
            changed_fields=changed_fields,
        )

        # Получаем старое сырое значение ДО изменения payload
        old_payload = dict(self.object.payload or {}) if self.object.pk else {}
        current_raw = old_payload.get('card_number', '') if self.object.pk else ''
        log.debug('Начало обработки формы', current_raw=current_raw[:50] if current_raw else '')

        # Обработка сырого значения card_number из формы
        raw_card_number = form.cleaned_data.get('raw_card_number', '').strip()
        log.debug('Получено сырое значение из формы', raw_card_number=raw_card_number[:50] if raw_card_number else '')
        
        if raw_card_number:
            # Извлекаем номер карты из сырого значения (только цифры, максимум 32 символа)
            card_number_value = extract_card_number_digits(raw_card_number)
            # Берем первые 16 цифр для номера карты
            if len(card_number_value) >= 16:
                self.object.card_number = card_number_value[:16]
            else:
                self.object.card_number = card_number_value
            log.debug(
                'Извлечен номер карты из сырого значения',
                card_number_extracted=self.object.card_number,
                raw_length=len(raw_card_number),
            )
        else:
            # Если сырое значение пустое, очищаем и card_number
            self.object.card_number = ''
            raw_card_number = ''
            log.debug('Сырое значение пустое, очищаем card_number')

        # Сохраняем сырое значение в payload
        payload = dict(self.object.payload or {})
        payload['card_number'] = raw_card_number
        self.object.payload = payload

        sync_result = None
        # Отправляем на birpay сырое значение, если оно изменилось
        raw_changed = raw_card_number != current_raw
        log.info(
            'Проверка изменения сырого значения',
            raw_changed=raw_changed,
            old_raw=current_raw[:50] if current_raw else '',
            new_raw=raw_card_number[:50] if raw_card_number else '',
        )
        
        if raw_changed:
            log.info(
                'Отправка обновления на Birpay',
                requisite_id=self.object.pk,
                card_number_raw=raw_card_number[:50] if raw_card_number else '',
            )
            try:
                sync_result = update_requisite_on_birpay(
                    self.object.pk,
                    {'card_number': raw_card_number},
                )
                log.info(
                    'Результат обновления на Birpay',
                    status_code=sync_result.get('status_code') if sync_result else None,
                    success=sync_result.get('success') if sync_result else None,
                    error=sync_result.get('error') if sync_result else None,
                )
            except Exception as e:
                log.error('Ошибка при обновлении на Birpay', exc_info=True, error=str(e))
                sync_result = {'error': str(e), 'success': False}
        else:
            log.debug('Сырое значение не изменилось, пропускаем отправку на Birpay')

        # Сохраняем объект с учетом всех измененных полей формы, включая works_on_asu
        # card_number и payload всегда обновляются, так как мы их изменяем вручную
        update_fields_list = ['card_number', 'payload']
        if 'works_on_asu' in changed_fields:
            update_fields_list.append('works_on_asu')
        log.debug('Сохранение объекта', update_fields=update_fields_list)
        self.object._change_source = 'admin'
        user = getattr(self.request, 'user', None)
        self.object._changed_by_user_id = getattr(user, 'id', None)
        self.object._changed_by_username = getattr(user, 'username', '') or ''
        self.object.save(update_fields=update_fields_list)
        log.info('Объект успешно сохранен')

        status_code = sync_result.get('status_code') if sync_result else None
        if status_code and status_code >= 400:
            error_msg = sync_result.get('data') or sync_result.get('error') or 'Неизвестная ошибка'
            log.warning('Ошибка при обновлении на Birpay', status_code=status_code, error=error_msg)
            messages.warning(
                self.request,
                f"Реквизит обновлён только локально. Ошибка Birpay: {error_msg}",
            )
        elif sync_result:
            log.info('Реквизит успешно обновлен локально и на Birpay')
            messages.success(self.request, 'Реквизит обновлён')
        else:
            log.info('Реквизит обновлен только локально (без изменений для Birpay)')
            messages.success(self.request, 'Реквизит обновлён')
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['method_name'] = REQUIRED_REFILL_METHOD_NAME
        return context


class RequsiteZajonChangeLogListView(StaffOnlyPerm, ListView):
    """Список логов изменений реквизитов (requisite-zajon). Фильтр по ID: ?requisite_id=..."""
    model = RequsiteZajonChangeLog
    template_name = 'deposit/requisite_zajon_change_log_list.html'
    context_object_name = 'logs'
    paginate_by = 50

    def dispatch(self, request, *args, **kwargs):
        if not _has_requisite_access(request.user):
            return redirect('deposit:index')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = RequsiteZajonChangeLog.objects.select_related('requisite').order_by('-created_at')
        requisite_id = self.request.GET.get('requisite_id', '').strip()
        if requisite_id:
            try:
                rid = int(requisite_id)
                qs = qs.filter(requisite_id=rid)
            except ValueError:
                pass
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_requisite_id'] = self.request.GET.get('requisite_id', '').strip()
        return context


class RequsiteZajonToggleActiveView(StaffOnlyPerm, View):
    def post(self, request, pk):
        if not _has_requisite_access(request.user):
            return redirect('deposit:index')
        requisite = get_object_or_404(RequsiteZajon, pk=pk)
        new_active = request.POST.get('set_active')
        if new_active is None:
            new_active_bool = not requisite.active
        else:
            new_active_bool = new_active == '1'

        result = update_requisite_on_birpay(pk, {'active': new_active_bool})
        status_code = result.get('status_code') if result else None
        if status_code and status_code >= 400:
            messages.warning(
                request,
                f"Не удалось изменить активность в Birpay: {result.get('data')}",
            )
        else:
            requisite.active = new_active_bool
            requisite._change_source = 'toggle_active'
            requisite.save(update_fields=['active'])
            messages.success(
                request,
                f"Активность реквизита {requisite.name} изменена на {'включено' if new_active_bool else 'выключено'}.",
            )

        redirect_url = request.POST.get('next') or reverse('deposit:requisite_zajon_list')
        return HttpResponseRedirect(redirect_url)


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
            # Валидация: если значение не пустое, проверяем существование BirpayOrder
            if value and value.strip():
                value = value.strip()
                order_exists = BirpayOrder.objects.filter(merchant_transaction_id=value).exists()
                if not order_exists:
                    error_msg = f'BirpayOrder с MerchTxID "{value}" не найден. Проверьте правильность номера.'
                    logger.warning(f'Попытка привязать несуществующий MerchTxID {value} к Incoming {incoming.id}')
                    messages.add_message(request, messages.ERROR, error_msg)
                    if 'filter' in options:
                        return redirect('deposit:incomings_filter')
                    else:
                        return redirect('deposit:incomings')
                
                # Проверяем, что этот merchant_transaction_id не привязан к другому Incoming
                existing_incoming = Incoming.objects.filter(birpay_id=value).exclude(pk=incoming.pk).first()
                if existing_incoming:
                    error_msg = f'MerchTxID "{value}" уже привязан к Incoming ID {existing_incoming.id}. Нельзя привязывать один номер к нескольким записям.'
                    logger.warning(f'Попытка привязать уже используемый MerchTxID {value} к Incoming {incoming.id} (уже привязан к {existing_incoming.id})')
                    messages.add_message(request, messages.ERROR, error_msg)
                    if 'filter' in options:
                        return redirect('deposit:incomings_filter')
                    else:
                        return redirect('deposit:incomings')
                
                # Проверяем, что BirpayOrder не привязан к другому Incoming
                order = BirpayOrder.objects.filter(merchant_transaction_id=value).first()
                if order and order.incoming and order.incoming.pk != incoming.pk:
                    error_msg = f'BirpayOrder с MerchTxID "{value}" уже привязан к Incoming ID {order.incoming.id}. Нельзя привязывать один заказ к нескольким записям.'
                    logger.warning(f'Попытка привязать BirpayOrder {value} к Incoming {incoming.id} (уже привязан к {order.incoming.id})')
                    messages.add_message(request, messages.ERROR, error_msg)
                    if 'filter' in options:
                        return redirect('deposit:incomings_filter')
                    else:
                        return redirect('deposit:incomings')
            
            # Проверяем несовпадение баланса перед привязкой
            # Если check_balance не вычислен, вычисляем его (на случай, если запись была создана до добавления этой логики)
            if incoming.check_balance is None and incoming.recipient:
                incoming.calculate_balance_fields()
                incoming.save(update_fields=['prev_balance', 'check_balance'])
                logger.info(f'Вычислен check_balance для Incoming {incoming.id}: check_balance={incoming.check_balance}')
            
            # Проверяем баланс только если введено непустое значение
            # Пустое значение используется для удаления привязки (убрать SMS из поиска)
            if value and value.strip():
                add_balance_mismatch_flag(incoming)
                logger.info(f'Проверка баланса для Incoming {incoming.id}: check_balance={incoming.check_balance}, balance={incoming.balance}, balance_mismatch={incoming.balance_mismatch}')
                
                # Проверяем несовпадение баланса
                balance_mismatch = False
                if incoming.check_balance is not None and incoming.balance is not None:
                    check_rounded = round(float(incoming.check_balance) * 10) / 10
                    balance_rounded = round(float(incoming.balance) * 10) / 10
                    balance_mismatch = check_rounded != balance_rounded
                    logger.info(f'Проверка баланса для Incoming {incoming.id}: check_rounded={check_rounded}, balance_rounded={balance_rounded}, не совпадают={balance_mismatch}')
                
                # Если баланс не совпадает и нет подтверждения оператора - блокируем привязку
                # Подтверждение устанавливается через JavaScript confirm dialog
                confirm_balance_mismatch = request.POST.get(f'confirm_balance_mismatch_{pk}', '') == '1'
                if balance_mismatch and not confirm_balance_mismatch:
                    error_msg = (
                        f'⚠️ ВНИМАНИЕ: Несовпадение баланса для Incoming ID {incoming.id}!\n'
                        f'Баланс из SMS: {incoming.balance}\n'
                        f'Расчетный баланс: {incoming.check_balance}\n'
                        f'Привязка заблокирована. Подтвердите привязку во всплывающем окне.'
                    )
                    logger.warning(f'Блокировка привязки BirpayOrder {value} к Incoming {incoming.id} из-за несовпадения баланса (не подтверждено)')
                    messages.add_message(request, messages.ERROR, error_msg)
                    if 'filter' in options:
                        return redirect('deposit:incomings_filter')
                    else:
                        return redirect('deposit:incomings')
                
                # Если баланс не совпадает, но есть подтверждение - отправляем уведомление в Telegram
                if balance_mismatch and confirm_balance_mismatch:
                    msg = (
                        f'⚠️ ВНИМАНИЕ: Привязка BirpayOrder к Incoming с несовпадающим балансом (подтверждено оператором)!\n'
                        f'Incoming ID: {incoming.id}\n'
                        f'MerchTxID: {value}\n'
                        f'Баланс из SMS: {incoming.balance}\n'
                        f'Расчетный баланс: {incoming.check_balance}\n'
                        f'Получатель: {incoming.recipient}\n'
                        f'Платеж: {incoming.pay}\n'
                        f'Пользователь: {request.user.username}'
                    )
                    logger.warning(f'Привязка BirpayOrder {value} к Incoming {incoming.id} с несовпадающим балансом (подтверждено оператором)')
                    try:
                        if settings.ALARM_IDS:
                            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
                            logger.info(f'Alarm-сообщение отправлено успешно в чаты: {settings.ALARM_IDS}')
                        else:
                            logger.error(f'ALARM_IDS не настроен! Уведомление не может быть отправлено.')
                    except Exception as e:
                        logger.error(f'Ошибка при отправке alarm-сообщения: {e}', exc_info=True)
            
            # Сохраняем привязку
            incoming.birpay_id = value.strip() if value else ''
            # Используем одно и то же время для синхронизации
            confirm_time = timezone.now()
            incoming.birpay_confirm_time = confirm_time
            if value and value.strip():
                order = BirpayOrder.objects.filter(merchant_transaction_id=value.strip()).first()
                if order:
                    # Автоматически заполняем merchant_user_id из BirpayOrder
                    incoming.merchant_user_id = order.merchant_user_id
                    # Устанавливаем контекст со всеми идентификаторами BirpayOrder при связывании
                    bind_contextvars(
                        birpay_id=order.birpay_id,
                        merchant_transaction_id=order.merchant_transaction_id,
                        birpay_order_id=order.id
                    )
                    order.incoming = incoming
                    order.confirmed_time = confirm_time  # Используем то же время
                    order.save(update_fields=['incoming', 'confirmed_time'])
                    logger.info(f'Привязан BirpayOrder {order.merchant_transaction_id} к Incoming {incoming.id} в incoming_list')
                    # Очищаем контекст после связывания
                    clear_contextvars()
            incoming.save()
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
        # Теперь prev_balance и check_balance вычисляются при создании и хранятся в БД
        incoming_q = Incoming.objects.raw(
            """
            SELECT deposit_incoming.*, deposit_colorbank.color_font, deposit_colorbank.color_back
            FROM deposit_incoming 
            LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
            WHERE worker = 'base2'
            ORDER BY deposit_incoming.id DESC LIMIT 5000;
            """
        )
        last_id = Incoming.objects.filter(worker='base2').order_by('id').last()
    elif not request.user.has_perm('users.base2') and not request.user.has_perm('users.all_base'):
        # Опер базы не 2
        incoming_q = Incoming.objects.raw(
        """
        SELECT deposit_incoming.*, deposit_colorbank.color_font, deposit_colorbank.color_back
        FROM deposit_incoming 
        LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
        WHERE worker != 'base2' or worker is NULL
        ORDER BY deposit_incoming.id DESC LIMIT 5000;
        """)
        last_id = Incoming.objects.exclude(worker='base2').order_by('id').last()
    elif request.user.has_perm('users.all_base') or request.user.is_superuser:
        # support
        incoming_q = Incoming.objects.raw(
        """
        WITH short_table AS (SELECT * FROM deposit_incoming ORDER BY id DESC LIMIT 5000)
        SELECT short_table.*, deposit_colorbank.color_font, deposit_colorbank.color_back
        FROM short_table 
        LEFT JOIN deposit_colorbank ON deposit_colorbank.name = short_table.sender
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
    # Обрабатываем raw queryset и добавляем флаг несовпадения баланса
    incoming_list = list(incoming_q)
    
    # Обрабатываем все объекты перед пагинацией
    for incoming in incoming_list:
        add_balance_mismatch_flag(incoming)
    
    # Создаем page_obj после обработки
    page_obj = make_page_obj(request, incoming_list)
    
    # Обрабатываем объекты в page_obj еще раз (на случай, если Paginator создал новые объекты)
    if hasattr(page_obj, 'object_list'):
        for incoming in page_obj.object_list:
            add_balance_mismatch_flag(incoming)
    
    context = {'page_obj': page_obj,
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
        # Теперь prev_balance и check_balance вычисляются при создании и хранятся в БД
        empty_incoming = Incoming.objects.filter(Q(birpay_id__isnull=True) | Q(birpay_id='')).order_by('-response_date', '-id').all()
        if not self.request.user.has_perm('users.all_base'):
            if self.request.user.has_perm('users.base2'):
                empty_incoming = empty_incoming.filter(worker='base2')
            else:
                empty_incoming = empty_incoming.exclude(worker='base2')
        return empty_incoming
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Добавляем флаг несовпадения баланса для каждого объекта
        for incoming in context['object_list']:
            add_balance_mismatch_flag(incoming)
        return context


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
            # Теперь prev_balance и check_balance вычисляются при создании и хранятся в БД
            filtered_incoming = Incoming.objects.raw(
            """
            SELECT deposit_incoming.*, deposit_colorbank.color_font, deposit_colorbank.color_back
            FROM deposit_incoming 
            LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
            WHERE deposit_incoming.recipient = ANY(%s) and deposit_incoming.worker = 'base2'
            ORDER BY deposit_incoming.id DESC
            """, [user_filter])
        else:
            filtered_incoming = Incoming.objects.raw(
                """
                SELECT deposit_incoming.*, deposit_colorbank.color_font, deposit_colorbank.color_back
                FROM deposit_incoming 
                LEFT JOIN deposit_colorbank ON deposit_colorbank.name = deposit_incoming.sender
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
        # Добавляем флаг несовпадения баланса для каждого объекта (raw queryset)
        if 'page_obj' in context and hasattr(context['page_obj'], 'object_list'):
            for incoming in context['page_obj'].object_list:
                add_balance_mismatch_flag(incoming)
        elif 'object_list' in context:
            for incoming in context['object_list']:
                add_balance_mismatch_flag(incoming)
        return context


class IncomingMyCardsView(StaffOnlyPerm, ListView):
    """Incoming list filtered by user's assigned card numbers (recipient masks)."""
    model = Incoming
    template_name = 'deposit/incomings_list.html'
    paginate_by = settings.PAGINATE

    def get_queryset(self, *args, **kwargs):

        profile = getattr(self.request.user, 'profile', None)
        cards = []
        if profile and profile.assigned_card_numbers:
            cards = profile.assigned_card_numbers

        # Базовый queryset по правам (как в IncomingSearch)
        qs = Incoming.objects.order_by('-response_date')
        if not self.request.user.has_perm('users.all_base'):
            if self.request.user.has_perm('users.base2'):
                qs = qs.filter(worker='base2')
            else:
                qs = qs.exclude(worker='base2')

        if not cards:
            return qs.none()

        # Нельзя SQL-ом по маскам, фильтруем в Python из последней 1000 записей по времени
        limited_qs = qs.exclude(recipient__isnull=True).exclude(recipient='')[:1000]
        matched_ids = []
        for inc in limited_qs:
            recipient = inc.recipient or ''
            for card_mask in cards:
                if mask_compare(card_mask, recipient):
                    matched_ids.append(inc.id)
                    break

        return Incoming.objects.filter(id__in=matched_ids).order_by('-response_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = None
        context['hide_search_form'] = True  # Флаг для скрытия формы поиска
        
        # Получаем назначенные карты для отображения в начале страницы
        profile = getattr(self.request.user, 'profile', None)
        assigned_cards = []
        if profile and profile.assigned_card_numbers:
            assigned_cards = profile.assigned_card_numbers
        context['assigned_cards'] = assigned_cards
        
        # Добавляем флаг несовпадения баланса для каждого объекта
        if 'object_list' in context:
            for incoming in context['object_list']:
                add_balance_mismatch_flag(incoming)
        
        # Последний id среди отфильтрованных (для уведомлений)
        last_filtered = self.object_list.first()
        context['last_id'] = last_filtered.id if last_filtered else None
        # Сообщения макросов как в IncomingFiltered
        last_bad = Message.objects.filter(type='macros').order_by('-id').first()
        context['last_bad_id'] = last_bad.id if last_bad else None
        # Для AJAX уведомлений в шаблоне - передаем список уникальных получателей из отфильтрованных записей
        filtered_recipients = list(set(self.object_list.values_list('recipient', flat=True).distinct()))
        context['filter'] = json.dumps(filtered_recipients)
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
        merchant_user_id = self.request.GET.get('merchant_user_id', '')
        sort_by_sms_time = self.request.GET.get('sort_by_sms_time', 1)
        end_time = None
        tz = pytz.timezone(settings.TIME_ZONE)
        start_time = ''

        if pk:
            return Incoming.objects.filter(birpay_id__contains=pk)
        
        if merchant_user_id:
            all_incoming = Incoming.objects.filter(merchant_user_id=merchant_user_id)
            if not self.request.user.has_perm('users.all_base'):
                if self.request.user.has_perm('users.base2'):
                    all_incoming = all_incoming.filter(worker='base2')
                else:
                    all_incoming = all_incoming.exclude(worker='base2')
            return all_incoming

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

        if not begin0 and not end0 and not only_empty and not pay and not merchant_user_id:
            return all_incoming[:0]

        return all_incoming

    def get_context_data(self, **kwargs):
        context = super(IncomingSearch, self).get_context_data(**kwargs)
        # Добавляем флаг несовпадения баланса для каждого объекта
        if 'object_list' in context:
            for incoming in context['object_list']:
                add_balance_mismatch_flag(incoming)
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
    
    # Получаем текущие значения фильтра из профиля
    all_recipients = []
    if hasattr(user.profile, 'my_filter') and user.profile.my_filter:
        all_recipients.extend(user.profile.my_filter)
    if hasattr(user.profile, 'my_filter2') and user.profile.my_filter2:
        all_recipients.extend(user.profile.my_filter2)
    if hasattr(user.profile, 'my_filter3') and user.profile.my_filter3:
        all_recipients.extend(user.profile.my_filter3)
    
    form = MyFilterForm(request.POST or None, initial={'recipients': all_recipients})
    template = 'deposit/my_filter.html'
    context['form'] = form

    if request.POST:
        if form.is_valid():
            # Собираем все выбранные значения из динамических полей банков
            selected_recipients = []
            for field_name, field_value in form.cleaned_data.items():
                if field_name.startswith('bank_') and field_value:
                    selected_recipients.extend(field_value)
            
            # Сохраняем в старые поля для обратной совместимости
            user.profile.my_filter = selected_recipients
            user.profile.my_filter2 = []
            user.profile.my_filter3 = []
            
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
            
            # Нормализуем старое значение для сравнения
            old_birpay_id = old_incoming.birpay_id or ''
            old_birpay_id = str(old_birpay_id).strip() if old_birpay_id else ''
            
            # Получаем новое значение из формы (может быть int или str)
            # В форме birpay_id определен как IntegerField, но в модели это CharField
            new_birpay_id_raw = form.cleaned_data.get('birpay_id')
            new_birpay_id = str(new_birpay_id_raw).strip() if new_birpay_id_raw is not None and new_birpay_id_raw != '' else ''
            
            # Валидация нового birpay_id (если не пустой)
            if new_birpay_id:
                # Проверяем существование BirpayOrder
                order_exists = BirpayOrder.objects.filter(merchant_transaction_id=new_birpay_id).exists()
                if not order_exists:
                    form.add_error('birpay_id', f'BirpayOrder с MerchTxID "{new_birpay_id}" не найден. Проверьте правильность номера.')
                    return super(IncomingEdit, self).form_invalid(form)
                
                # Проверяем, что этот merchant_transaction_id не привязан к другому Incoming
                existing_incoming = Incoming.objects.filter(birpay_id=new_birpay_id).exclude(pk=incoming.pk).first()
                if existing_incoming:
                    form.add_error('birpay_id', f'MerchTxID "{new_birpay_id}" уже привязан к Incoming ID {existing_incoming.id}. Нельзя привязывать один номер к нескольким записям.')
                    return super(IncomingEdit, self).form_invalid(form)
                
                # Проверяем, что BirpayOrder не привязан к другому Incoming
                new_order = BirpayOrder.objects.filter(merchant_transaction_id=new_birpay_id).first()
                if new_order and new_order.incoming and new_order.incoming.pk != incoming.pk:
                    form.add_error('birpay_id', f'BirpayOrder с MerchTxID "{new_birpay_id}" уже привязан к Incoming ID {new_order.incoming.id}. Нельзя привязывать один заказ к нескольким записям.')
                    return super(IncomingEdit, self).form_invalid(form)
            
            # Обновление временных меток
            incoming.birpay_edit_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            
            # Сохраняем birpay_id (может быть пустой строкой или None)
            incoming.birpay_id = new_birpay_id if new_birpay_id else None
            
            # Обновление связи с BirpayOrder для консистентности
            if old_birpay_id != new_birpay_id:
                # Используем одно и то же время для синхронизации birpay_confirm_time и order.confirmed_time
                # Обновляем время только если birpay_id изменился
                confirm_time = timezone.now()
                incoming.birpay_confirm_time = confirm_time
                
                # Отвязываем старый BirpayOrder (если был привязан)
                if old_birpay_id:
                    old_order = BirpayOrder.objects.filter(merchant_transaction_id=old_birpay_id).first()
                    if old_order and old_order.incoming and old_order.incoming.pk == incoming.pk:
                        # Устанавливаем контекст со всеми идентификаторами BirpayOrder при отвязывании
                        bind_contextvars(
                            birpay_id=old_order.birpay_id,
                            merchant_transaction_id=old_order.merchant_transaction_id,
                            birpay_order_id=old_order.id
                        )
                        old_order.incoming = None
                        old_order.save(update_fields=['incoming'])
                        # При отвязывании очищаем merchant_user_id
                        incoming.merchant_user_id = None
                        logger.info(f'Отвязан старый BirpayOrder {old_birpay_id} от Incoming {incoming.id}')
                        # Очищаем контекст после отвязывания
                        clear_contextvars()
                
                # Привязываем новый BirpayOrder (если существует и не пустой)
                if new_birpay_id:
                    new_order = BirpayOrder.objects.filter(merchant_transaction_id=new_birpay_id).first()
                    if new_order:
                        # Автоматически заполняем merchant_user_id из BirpayOrder
                        incoming.merchant_user_id = new_order.merchant_user_id
                        # Устанавливаем контекст со всеми идентификаторами BirpayOrder при связывании
                        bind_contextvars(
                            birpay_id=new_order.birpay_id,
                            merchant_transaction_id=new_order.merchant_transaction_id,
                            birpay_order_id=new_order.id
                        )
                        new_order.incoming = incoming
                        new_order.confirmed_time = confirm_time  # Используем то же время
                        new_order.save(update_fields=['incoming', 'confirmed_time'])
                        logger.info(f'Привязан новый BirpayOrder {new_birpay_id} к Incoming {incoming.id}')
                        # Очищаем контекст после связывания
                        clear_contextvars()
                else:
                    # При отвязывании очищаем merchant_user_id
                    incoming.merchant_user_id = None
                    logger.info(f'BirpayOrder отвязан от Incoming {incoming.id} (birpay_id очищен)')
            elif not incoming.birpay_confirm_time:
                # Если birpay_id не изменился, но birpay_confirm_time не установлен, устанавливаем его
                incoming.birpay_confirm_time = timezone.now()
            
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
                delta__lte=datetime.timedelta(hours=1)
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
                    (df['delta_minutes'] >= 5) & (df['delta_minutes'] < 10),
                    (df['delta_minutes'] >= 10) & (df['delta_minutes'] < 15),
                    (df['delta_minutes'] >= 15),
                ]
                choices = ['<5 минут', '<10 минут', '<15 минут', '≥15 минут']
                df['speed_cat'] = np.select(conditions, choices, default='≥15 минут')

                all_hours = np.arange(0, 24)
                speed_order = ['<5 минут', '<10 минут', '<15 минут', '≥15 минут']
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
                bins = [0, 5, 10, 15, np.inf]
                labels = [
                    '0–5', '5–10', '10–15', '15+'
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


def _iframe_proxy_url_allowed(target_url):
    """Проверка URL для прокси: только http/https, запрет внутренних хостов (SSRF)."""
    try:
        parsed = urlparse(target_url)
        if parsed.scheme not in ('http', 'https'):
            return False
        host = (parsed.hostname or '').strip().lower()
        if not host or host in ('localhost', '127.0.0.1'):
            return False
        if host.startswith('127.'):
            return False
        if host.startswith('10.'):
            return False
        if host.startswith('172.'):
            parts = host.split('.')
            if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
                return False
        if host.startswith('192.168.'):
            return False
        return True
    except Exception:
        return False


@xframe_options_exempt
@login_required(login_url='users:login')
def iframe_proxy_view(request):
    """
    Прокси для iframe: GET url=... и опционально proxy=host:port:user:password.
    Загружает страницу через requests с прокси и отдаёт ответ в iframe.
    """
    if request.method != 'GET':
        return HttpResponseBadRequest('Only GET')
    target_url = (request.GET.get('url') or '').strip()
    if not target_url:
        return HttpResponseBadRequest('Missing url')
    if not _iframe_proxy_url_allowed(target_url):
        return HttpResponseForbidden('URL not allowed')
    proxy_str = (request.GET.get('proxy') or '').strip()
    proxy_dict = _parse_check_proxy(proxy_str) if proxy_str else None
    try:
        resp = requests.get(
            target_url,
            timeout=30,
            proxies=proxy_dict,
            headers={'User-Agent': request.META.get('HTTP_USER_AGENT', '') or 'Mozilla/5.0'},
            allow_redirects=True,
        )
    except requests.RequestException as e:
        logger.warning('iframe_proxy request failed', url=target_url, error=str(e))
        return HttpResponse(
            f'<html><body><p>Ошибка загрузки через прокси: {e!s}</p></body></html>',
            status=502,
            content_type='text/html; charset=utf-8',
        )
    content_type = resp.headers.get('Content-Type') or 'application/octet-stream'
    return HttpResponse(resp.content, status=resp.status_code, content_type=content_type)


@xframe_options_exempt
@login_required(login_url='users:login')
def iframe_view(request):
    """Страница с полем для вставки ссылки и отображением в iframe. Разрешено открывать в iframe."""
    initial_url = request.GET.get('url', '').strip()
    initial_proxy = request.GET.get('proxy', '').strip()
    context = {'initial_url': initial_url, 'initial_proxy': initial_proxy}
    return render(request, 'deposit/iframe_view.html', context)


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

    # tasks.send_new_transactions_from_um_to_asu.delay()

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
                result = BirpayClient().approve_payout(birpay_withdraw_id, transaction_id)

            elif status == -1:
                logger.info(f'Отклоняем на birpay {birpay_withdraw_id}')
                result = BirpayClient().decline_payout(birpay_withdraw_id, reason='err')

            logger.info(f'result: {result}')
            if result.get('errors'):
                return JsonResponse(status=400, data=result, safe=False)
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
        # Добавляем флаг несовпадения баланса для каждого объекта
        if 'object_list' in context:
            for incoming in context['object_list']:
                add_balance_mismatch_flag(incoming)
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
        # Оптимизация: загружаем связанные объекты одним запросом
        qs = qs.select_related('incoming', 'confirmed_operator', 'requisite')
        self.filterset = BirpayOrderFilter(self.request.GET, queryset=qs)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = self.filterset.form
        
        # Базовый URL страницы Payment на ASU (для ссылки по payment_id в столбце ID)
        asu_host = getattr(settings, 'ASU_HOST', '') or ''
        context['asu_payments_base_url'] = (asu_host.rstrip('/') + '/payments/') if asu_host else ''
        
        # Загружаем Options один раз для всего контекста
        options = Options.load()
        context['gpt_auto_approve'] = options.gpt_auto_approve
        
        # Кэшируем списки для проверки is_moshennik/is_painter без запросов к БД
        birpay_moshennik_list = set(options.birpay_moshennik_list)
        birpay_painter_list = set(options.birpay_painter_list)

        show_stat = self.filterset.form.cleaned_data.get('show_stat')
        if show_stat:
            qs = self.filterset.qs
            # Оптимизация: используем один запрос с агрегацией вместо множественных count()
            stats_agg = qs.aggregate(
                total=Count('id'),
                with_incoming=Count('id', filter=Q(incoming__isnull=False)),
                sum_incoming_pay=Sum('incoming_pay'),
                sum_amount=Sum('amount'),
                sum_delta=Sum('delta'),
                sum_confirmed_amount=Sum('amount', filter=Q(status=1)),
                status_0=Count('id', filter=Q(status=0)),
                status_1=Count('id', filter=Q(status=1)),
                status_2=Count('id', filter=Q(status=2)),
                gpt_approve_count=Count('id', filter=Q(gpt_flags=255)),
            )
            total_count = stats_agg['total'] or 0
            stats = {
                'total': total_count,
                'with_incoming': stats_agg['with_incoming'] or 0,
                'sum_incoming_pay': stats_agg['sum_incoming_pay'] or 0,
                'sum_amount': stats_agg['sum_amount'] or 0,
                'sum_delta': stats_agg['sum_delta'] or 0,
                'sum_confirmed_amount': stats_agg['sum_confirmed_amount'] or 0,
                'status_0': stats_agg['status_0'] or 0,
                'status_1': stats_agg['status_1'] or 0,
                'status_2': stats_agg['status_2'] or 0,
                'gpt_approve': int(stats_agg['gpt_approve_count'] / total_count * 100) if total_count else 0
            }
            context['birpay_stats'] = stats

        # Предвычисляем is_moshennik и is_painter для каждого заказа, чтобы избежать вызовов Options.load() в шаблоне
        for order in context['page_obj']:
            if hasattr(order, 'raw_data'):
                try:
                    order.raw_data_json = json.dumps(order.raw_data, ensure_ascii=False, cls=DjangoJSONEncoder)
                except Exception:
                    order.raw_data_json = '{}'
            else:
                order.raw_data_json = '{}'
            
            # Предвычисляем флаги для избежания запросов в свойствах модели
            order._is_moshennik = order.merchant_user_id in birpay_moshennik_list
            order._is_painter = order.merchant_user_id in birpay_painter_list

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
        # Проверяем, нажата ли кнопка очистки
        if request.POST.get('clear_cards'):
            form = AssignCardsToUserForm(request.POST)
            if form.is_valid():
                selected_user = form.cleaned_data['user']
                profile = selected_user.profile
                profile.assigned_card_numbers = []  # Очищаем список
                profile.save()
                assigned_cards = []
                messages.success(request, f"Карты очищены для {selected_user.username}")
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
            # Обычное сохранение карт
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
    staff_users = User.objects.filter(is_staff=True, is_active=True).select_related('profile')
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
            # Очищаем контекст в начале обработки POST запроса
            clear_contextvars()
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
                    # Устанавливаем контекст со всеми идентификаторами BirpayOrder
                    bind_contextvars(
                        birpay_id=order.birpay_id,
                        merchant_transaction_id=order.merchant_transaction_id,
                        birpay_order_id=order.id
                    )
                elif name.startswith('orderamount'):
                    new_amount = float(value)
                    logger.info(f'sended_amount: {new_amount}')
                elif name.startswith('order_action_'):
                    action = value
                    logger.info(f'action: {action}')

            update_fields = []

            # смена суммы
            if order.amount != new_amount:
                if order.status != 0:
                    text = f'Не удалось сменить сумму {order} mtx_id {order.merchant_transaction_id}: Статус не pending'
                    logger.warning(text)
                    messages.add_message(request, messages.WARNING, text)
                    raise ValidationError(text)
                else:
                    logger.info(f'Меняем amount с {order.amount} на {new_amount}')
                    response = BirpayClient().change_refill_amount(order.birpay_id, new_amount)
                    if response.status_code == 200:
                        text = f"Сумма {order} mtx_id {order.merchant_transaction_id} изменена с {order.amount} на {new_amount}"
                        logger.info(text)
                        messages.add_message(request, messages.INFO, text)
                        order.amount = new_amount
                        update_fields.append('amount')
                    else:
                        text = f'Не удалось изменить сумму mtx_id {order.merchant_transaction_id} с {order.amount} на {new_amount}'
                        messages.add_message(request, messages.ERROR, text)
                        raise ValidationError(text)

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
                        # Но сначала проверим суммы
                        if incoming_to_approve.pay != order.amount:
                            text = f'Сумма в смс {incoming_to_approve.id} {incoming_to_approve.pay} и заказе {order.amount} отличаются. Подтверждение и привязка не возможна'
                            messages.add_message(request, messages.ERROR, text)
                            logger.error(text)
                            raise ValidationError(text)
                        
                        # Проверяем несовпадение баланса перед привязкой
                        # Если check_balance не вычислен, вычисляем его
                        if incoming_to_approve.check_balance is None and incoming_to_approve.recipient:
                            incoming_to_approve.calculate_balance_fields()
                            incoming_to_approve.save(update_fields=['prev_balance', 'check_balance'])
                            logger.info(f'Вычислен check_balance для Incoming {incoming_to_approve.id}: check_balance={incoming_to_approve.check_balance}')
                        
                        add_balance_mismatch_flag(incoming_to_approve)
                        if incoming_to_approve.balance_mismatch:
                            # Проверяем подтверждение оператора через скрытое поле
                            # Если JavaScript прошел проверку, флаг должен быть установлен
                            confirm_balance_mismatch = request.POST.get(f'confirm_balance_mismatch_{order.id}', '') == '1'
                            
                            # Если баланс не совпадает и есть подтверждение - отправляем alarm-сообщение
                            # JavaScript уже проверил баланс и показал диалог, поэтому здесь только отправляем уведомление
                            if confirm_balance_mismatch:
                                msg = (
                                    f'⚠️ ВНИМАНИЕ: Привязка BirpayOrder к Incoming с несовпадающим балансом (подтверждено оператором)!\n'
                                    f'Incoming ID: {incoming_to_approve.id}\n'
                                    f'BirpayOrder ID: {order.id}\n'
                                    f'MerchTxID: {order.merchant_transaction_id}\n'
                                    f'Баланс из SMS: {incoming_to_approve.balance}\n'
                                    f'Расчетный баланс: {incoming_to_approve.check_balance}\n'
                                    f'Получатель: {incoming_to_approve.recipient}\n'
                                    f'Платеж: {incoming_to_approve.pay}\n'
                                    f'Пользователь: {request.user.username}'
                                )
                                try:
                                    if settings.ALARM_IDS:
                                        send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
                                        logger.info(f'Alarm-сообщение отправлено успешно в чаты: {settings.ALARM_IDS}')
                                    else:
                                        logger.error(f'ALARM_IDS не настроен! Уведомление не может быть отправлено.')
                                except Exception as e:
                                    logger.error(f'Ошибка при отправке alarm-сообщения: {e}', exc_info=True)
                                logger.warning(f'Привязка BirpayOrder {order.merchant_transaction_id} к Incoming {incoming_to_approve.id} с несовпадающим балансом (подтверждено оператором)')
                        
                        logger.info('Апрувнем заявку')
                        
                        # Логика Z-ASU: если DEBUG=True, не отправляем запрос, считаем успешным
                        if settings.DEBUG:
                            logger.info(f'DEBUG=True: пропускаем отправку approve_refill для {order.birpay_id}, считаем успешным')
                            response = type('MockResponse', (), {'status_code': 200, 'text': 'OK (DEBUG mode)'})()
                        else:
                            response = BirpayClient().approve_refill(order.birpay_id)
                        
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
                                # Автоматически заполняем merchant_user_id из BirpayOrder
                                incoming_to_approve.merchant_user_id = order.merchant_user_id
                                incoming_to_approve.save()
                                logger.info(f'"Заявка {order} mtx_id {order.merchant_transaction_id} успешно подтверждена')
                            # Логика Z-ASU: подтверждение на ASU выполняется только по смене status на 1 (сигнал ставит задачу)

            # Очищаем контекст после завершения обработки заказа
            clear_contextvars()
            return HttpResponseRedirect(f"{request.path}?{query_string}")
        except Exception as e:
            # Очищаем контекст при ошибке
            clear_contextvars()
            logger.error(e, exc_info=True)
            messages.add_message(request, messages.ERROR, e)
            return HttpResponseRedirect(f"{request.path}?{query_string}")

@staff_member_required()
def get_incoming_balance_info(request, incoming_id):
    """API endpoint для получения информации о балансе Incoming по ID"""
    try:
        incoming = Incoming.objects.get(pk=incoming_id)
        # Если check_balance не вычислен, вычисляем его
        if incoming.check_balance is None and incoming.recipient:
            incoming.calculate_balance_fields()
            incoming.save(update_fields=['prev_balance', 'check_balance'])
        
        add_balance_mismatch_flag(incoming)
        
        balance_mismatch = False
        if incoming.check_balance is not None and incoming.balance is not None:
            check_rounded = round(float(incoming.check_balance) * 10) / 10
            balance_rounded = round(float(incoming.balance) * 10) / 10
            balance_mismatch = check_rounded != balance_rounded
        
        return JsonResponse({
            'id': incoming.id,
            'balance': incoming.balance,
            'check_balance': incoming.check_balance,
            'balance_mismatch': balance_mismatch,
            'pay': incoming.pay,
            'recipient': incoming.recipient
        })
    except Incoming.DoesNotExist:
        return JsonResponse({'error': 'Incoming not found'}, status=404)
    except Exception as e:
        logger.error(f'Ошибка при получении информации о балансе Incoming {incoming_id}: {e}', exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)

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


# class BirpayMyFilterView(StaffOnlyPerm, ListView):
#     template_name = 'deposit/birpay_panel.html'
#     paginate_by = 100
#     model = BirpayOrder
#     filterset_class = BirpayPanelFilter
#
#     def get_queryset(self):
#         now = timezone.now()
#         if settings.DEBUG:
#             threshold = now - datetime.timedelta(days=50)
#         else:
#             threshold = now - datetime.timedelta(minutes=30)
#         qs = super().get_queryset().filter(sended_at__gt=threshold, status_internal__in=[0, 1]).order_by('-created_at')
#
#         incoming_qs = Incoming.objects.filter(
#             birpay_id=OuterRef('merchant_transaction_id')
#         ).order_by('-register_date')
#         qs = qs.annotate(
#             incoming_pay=Subquery(incoming_qs.values('pay')[:1]),
#             delta=ExpressionWrapper(
#                 Subquery(incoming_qs.values('pay')[:1]) - F('amount'),
#                 output_field=FloatField()
#             ),
#             incoming_register_date=Subquery(incoming_qs.values('register_date')[:1]),
#         )
#
#         # Применяем фильтр по назначенным картам пользователя
#         user_card_numbers = get_user_card_numbers(self.request.user)
#         if user_card_numbers:
#             qs = qs.filter(card_number__in=user_card_numbers)
#         else:
#             qs = qs.none()
#
#         # Создаем фильтр с предустановленным значением only_my=True
#         modified_get = self.request.GET.copy()
#         modified_get['only_my'] = 'on'
#
#         self.filterset = BirpayPanelFilter(
#             modified_get,
#             queryset=qs,
#             request=self.request,
#             user_card_numbers=user_card_numbers
#         )
#         return self.filterset.qs
#
#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context['search_form'] = self.filterset.form
#         context['selected_card_numbers'] = self.request.GET.getlist('card_number')
#         context['statuses'] = self.request.GET.getlist('status')
#         context['only_my'] = ['on']  # Всегда включен для этой вкладки
#         context['is_my_filter_tab'] = True  # Флаг для шаблона
#
#         user = self.request.user
#         last_confirmed_order = BirpayOrder.objects.filter(confirmed_operator=user).order_by('-confirmed_time').first()
#         if last_confirmed_order:
#             context['last_confirmed_order_id'] = last_confirmed_order.id
#
#         return context
#
#     def post(self, request, *args, **kwargs):
#         # исходный URL
#         query = []
#         filter_keys = ['card_number', 'status', 'only_my']
#         logger.info(f'{request.POST.dict()}')
#         for key in filter_keys:
#             for value in request.POST.getlist(key):
#                 query.append(f"{key}={value}")
#         query_string = '&'.join(query)
#
#         try:
#             logger.info(f'POST: {request.POST.dict()}')
#             post_data = request.POST.dict()
#             new_amount = 0
#             order = None
#             incoming_id = ''
#             action = None
#             for name, value in post_data.items():
#                 if name.startswith('orderconfirm'):
#                     order_id = name.split('orderconfirm_')[1]
#                     incoming_id = value.strip()
#                     order = BirpayOrder.objects.get(pk=order_id)
#                     logger.info(f'Для {order} сохраняем смс {incoming_id}')
#                     bind_contextvars(merchant_transaction_id=order.merchant_transaction_id)
#                 elif name.startswith('orderamount'):
#                     new_amount = float(value)
#                     logger.info(f'sended_amount: {new_amount}')
#                 elif name.startswith('order_action_'):
#                     action = value
#                     logger.info(f'action: {action}')
#
#             update_fields = []
#
#             # смена суммы
#             if order.amount != new_amount:
#                 if order.status != 0:
#                     text = f'Не удалось сменить сумму {order} mtx_id {order.merchant_transaction_id}: Статус не pending'
#                     logger.warning(text)
#                     messages.add_message(request, messages.WARNING, text)
#                     raise ValidationError(text)
#                 else:
#                     logger.info(f'Меняем amount с {order.amount} на {new_amount}')
#                     response = change_amount_birpay(pk=order.birpay_id, amount=new_amount)
#                     if response.status_code == 200:
#                         order.amount = new_amount
#                         update_fields.append('amount')
#                         logger.info(f'Успешно сменили amount на {new_amount}')
#                     else:
#                         text = f'Не удалось сменить сумму {order} mtx_id {order.merchant_transaction_id}: {response.text}'
#                         logger.warning(text)
#                         messages.add_message(request, messages.WARNING, text)
#                         raise ValidationError(text)
#
#             # привязка смс
#             if incoming_id:
#                 try:
#                     incoming = Incoming.objects.get(pk=incoming_id)
#                     order.incoming = incoming
#                     update_fields.append('incoming')
#                     logger.info(f'Привязали смс {incoming_id} к заказу {order}')
#                 except Incoming.DoesNotExist:
#                     text = f'СМС с id {incoming_id} не найдена'
#                     logger.warning(text)
#                     messages.add_message(request, messages.WARNING, text)
#                     raise ValidationError(text)
#
#             # смена статуса
#             if action:
#                 if action == 'approve':
#                     order.status = 1
#                     order.confirmed_operator = request.user
#                     order.confirmed_time = timezone.now()
#                     update_fields.extend(['status', 'confirmed_operator', 'confirmed_time'])
#                     logger.info(f'Подтвердили заказ {order}')
#                 elif action == 'hide':
#                     order.status = 2
#                     order.confirmed_operator = request.user
#                     order.confirmed_time = timezone.now()
#                     update_fields.extend(['status', 'confirmed_operator', 'confirmed_time'])
#                     logger.info(f'Скрыли заказ {order}')
#                 elif action == 'pending':
#                     order.status = 0
#                     order.confirmed_operator = None
#                     order.confirmed_time = None
#                     update_fields.extend(['status', 'confirmed_operator', 'confirmed_time'])
#                     logger.info(f'Вернули заказ {order} в pending')
#
#             if update_fields:
#                 order.save(update_fields=update_fields)
#                 messages.add_message(request, messages.SUCCESS, f'Заказ {order.merchant_transaction_id} обновлен')
#
#         except Exception as e:
#             logger.error(f'Ошибка при обновлении заказа: {e}')
#             messages.add_message(request, messages.ERROR, e)
#             return HttpResponseRedirect(f"{request.path}?{query_string}")


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


class BirpayOrderCreateView(SuperuserOnlyPerm, CreateView):
    """Представление для ручного создания тестовых BirpayOrder (только для суперюзера)"""
    model = BirpayOrder
    form_class = BirpayOrderCreateForm
    template_name = 'deposit/birpay_order_create.html'
    success_url = reverse_lazy('deposit:birpay_orders')
    
    def form_valid(self, form):
        # Сохраняем заявку в БД в отдельной транзакции, чтобы последующие шаги
        # (чек, Z-ASU) не могли откатить сохранение при ошибке.
        with transaction.atomic():
            response = super().form_valid(form)
            order = form.instance
            if not order.pk:
                messages.error(self.request, 'Ошибка: заявка не была сохранена (нет ID).')
                return response
        # Транзакция закоммичена — BirpayOrder уже в БД.

        # Дополнительные шаги не должны откатывать создание заявки — оборачиваем в try/except
        try:
            # Устанавливаем sended_at и status_internal для отображения в birpay_panel
            now = timezone.now()
            update_fields = []
            if not order.sended_at:
                order.sended_at = now
                update_fields.append('sended_at')
            if order.status_internal is None or order.status_internal not in [0, 1]:
                order.status_internal = 0
                update_fields.append('status_internal')
            if update_fields:
                order.save(update_fields=update_fields)
                logger.debug(f'Установлены поля для отображения в birpay_panel: {update_fields}')
        except Exception as err:
            logger.exception('Ошибка при установке sended_at/status_internal для BirpayOrder %s', order.pk, exc_info=True)
            messages.warning(self.request, f'Заявка создана, но не удалось обновить поля: {err}')

        # Если указан URL чека и файл еще не скачан, скачиваем синхронно (для тестовых заявок)
        if order.check_file_url:
            log = logger.bind(
                birpay_id=order.birpay_id,
                order_id=order.id,
                check_file_url=order.check_file_url,
                has_check_file=bool(order.check_file),
                check_file_failed=order.check_file_failed,
            )
            log.debug('Проверка необходимости скачивания чека')
            if not order.check_file and not order.check_file_failed:
                try:
                    log.info('Скачивание чека для тестовой заявки синхронно')
                    result = _download_birpay_check_file_sync(order.id, order.check_file_url)
                    log.info('Чек успешно скачан', result=result)
                    order.refresh_from_db()
                    log.info('Объект обновлен из БД', check_file_exists=bool(order.check_file))
                except Exception as err:
                    log.error('Ошибка скачивания чека', exc_info=True, error=str(err))
                    messages.warning(self.request, f'Не удалось скачать чек: {str(err)}')
            else:
                log.debug('Чек уже скачан или скачивание уже было неудачным', 
                         has_check_file=bool(order.check_file), 
                         check_file_failed=order.check_file_failed)

        # Логика Z-ASU: проверка условия и отправка на ASU (по реквизиту works_on_asu)
        logger.debug(f"Проверка Z-ASU для BirpayOrder {order.birpay_id}: requisite_id={order.requisite_id}")
        should_send = should_send_to_z_asu(order)
        logger.debug(f"should_send_to_z_asu(order) = {should_send}")
        if should_send:
            logger.info(f"BirpayOrder {order.birpay_id} соответствует условию Z-ASU (реквизит works_on_asu), отправляем на ASU")
            try:
                result = send_birpay_order_to_z_asu(order)
                if result.get('success'):
                    payment_id = result.get('payment_id')
                    logger.info(f"BirpayOrder {order.birpay_id} успешно отправлен на Z-ASU, payment_id={payment_id}")
                    if payment_id:
                        order.payment_id = str(payment_id)
                        order.save(update_fields=['payment_id'])
                    messages.success(self.request, f'Заявка отправлена на Z-ASU! Payment ID: {payment_id}')
                else:
                    logger.error(f"Ошибка отправки BirpayOrder {order.birpay_id} на Z-ASU: {result.get('error')}")
                    messages.error(self.request, f'Ошибка отправки на Z-ASU: {result.get("error")}')
            except Exception as err:
                logger.error(f"Исключение при отправке BirpayOrder {order.birpay_id} на Z-ASU: {err}", exc_info=True)
                messages.error(self.request, f'Ошибка отправки на Z-ASU: {str(err)}')
        else:
            logger.debug(f"BirpayOrder {order.birpay_id} не соответствует условию Z-ASU (нет реквизита с works_on_asu)")

        # Сообщение об успехе только после гарантированного сохранения в БД
        messages.success(self.request, f'BirpayOrder успешно создан! ID: {order.id}, birpay_id: {order.birpay_id}')
        return response


def _birpay_raw_result_to_display(raw_dict):
    """Преобразует сырой ответ birpay API в список (ключ, строка для отображения)."""
    if not raw_dict:
        return []
    items = []
    for key, value in raw_dict.items():
        if isinstance(value, (dict, list)):
            try:
                display = json.dumps(value, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                display = str(value)
        elif value is None:
            display = '—'
        else:
            display = str(value)
        items.append((key, display))
    return items


class ZASUManagementView(SuperuserOnlyPerm, View):
    """Страница управления Z-ASU (только для суперюзера). Форма редактирования номера карты по ID реквизита."""
    template_name = 'deposit/z_asu_management.html'

    def get(self, request, *args, **kwargs):
        context = {'requisite_card_form': RequisiteCardEditForm()}
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        form = RequisiteCardEditForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'requisite_card_form': form})
        requisite_id = form.cleaned_data['requisite_id']
        card_number_raw = form.cleaned_data['card_number']
        try:
            sync_result = update_requisite_on_birpay(requisite_id, {'card_number': card_number_raw})
        except Exception as e:
            logger.exception('Z-ASU: ошибка обновления реквизита на Birpay')
            messages.error(request, f'Ошибка Birpay: {e}')
            return render(request, self.template_name, {'requisite_card_form': form})
        requisite = get_object_or_404(RequsiteZajon, pk=requisite_id)
        old_payload = requisite.payload or {}
        requisite.card_number = re.sub(r'\D', '', card_number_raw)[:16] if re.sub(r'\D', '', card_number_raw) else ''
        requisite.payload = dict(old_payload)
        requisite.payload['card_number'] = card_number_raw
        requisite._change_source = 'z_asu_form'
        requisite.save(update_fields=['card_number', 'payload'])
        if sync_result.get('success'):
            messages.success(request, f'Реквизит {requisite_id}: номер карты обновлён локально и на Birpay.')
        else:
            messages.warning(
                request,
                f'Реквизит {requisite_id} обновлён локально. Birpay: {sync_result.get("error", "ошибка")}.',
            )
        return HttpResponseRedirect(reverse('deposit:z_asu_management'))


class BirpayGateStatusCheckView(SuperuserOnlyPerm, View):
    """
    Проверка актуальных данных заявки на birpay-gate по Merchant Tx ID.
    Поддерживается поиск только по полю merchantTransactionId (Refill и Payout).
    """
    template_name = 'deposit/birpay_gate_status_check.html'

    def get(self, request):
        return render(request, self.template_name, {
            'order_type': 'refill',
            'merchant_tx_id': '',
            'result': None,
            'error': None,
        })

    def post(self, request):
        merchant_tx_id = (request.POST.get('merchant_tx_id') or '').strip()
        order_type = request.POST.get('order_type', 'refill').strip().lower()
        if order_type not in ('refill', 'payout'):
            order_type = 'refill'

        error = None
        result = None
        result_items = None

        if not merchant_tx_id:
            error = 'Введите Merchant Tx ID.'
        else:
            try:
                client = BirpayClient()
                if order_type == 'refill':
                    result = client.find_refill_order(merchant_tx_id)
                else:
                    result = client.find_payout_order(merchant_tx_id)
                if result is None:
                    error = f'Заявка с Merchant Tx ID «{merchant_tx_id}» не найдена (тип: {order_type}).'
                else:
                    result_items = _birpay_raw_result_to_display(result)
            except Exception as e:
                logger.error(f'Ошибка запроса к birpay-gate: {e}', exc_info=True)
                error = str(e)

        return render(request, self.template_name, {
            'order_type': order_type,
            'merchant_tx_id': merchant_tx_id,
            'result': result,
            'result_items': result_items,
            'error': error,
        })