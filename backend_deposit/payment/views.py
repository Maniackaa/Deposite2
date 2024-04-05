import datetime
import logging
import random
from http import HTTPStatus

import requests
import structlog
from django.conf import settings
from django.http import HttpResponse, QueryDict, HttpResponseNotAllowed, HttpResponseForbidden, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, FormView, UpdateView, ListView

from payment import forms
from payment.forms import InvoiceForm, PaymentListConfirmForm
from payment.models import Payment, PayRequisite, Shop

logger = structlog.get_logger(__name__)


def get_pay_requisite(pay_type: str) -> PayRequisite:
    """Выдает реквизиты по типу
    [Card-to-Card]
    """
    active_requsite = PayRequisite.objects.filter(pay_type=pay_type, is_active=True).all()
    logger.debug(f'active_requsite {pay_type}: {active_requsite}')
    if active_requsite:
        selected_requisite = random.choice(active_requsite)
        logger.debug(f'get_pay_requisite {pay_type}: {selected_requisite.id} из {active_requsite}')
        return selected_requisite


def get_time_remaining(pay: Payment) -> datetime.timedelta:
    TIMER_SECONDS = 100
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
            'seconds': seconds
        }
    else:
        data = {
            'name': "Время до оплаты",
            'hours': 0,
            'minutes': 0,
            'seconds': 0
        }
    return data


def invoice(request, *args, **kwargs):
    """Создание платежа со стотусом 0 и идентификатором

    Parameters
    ----------
    args
        shop_id: id платежной системы
        outer_order_id: внешний идентификатор
        user_id
        amount: сумма платежа
        pay_type: тип платежа
    Returns
    -------
    """

    if request.method == 'GET':
        shop_id = request.GET.get('shop_id')
        outer_order_id = request.GET.get('outer_order_id')
        user_login = request.GET.get('user_login')
        amount = request.GET.get('amount')
        pay_type = request.GET.get('pay_type')
        logger.debug(f'GET {shop_id} {outer_order_id} {user_login} {amount} {pay_type}')
        required_key = ['shop_id', 'outer_order_id', 'user_login', 'amount', 'pay_type']
        # Проверяем наличие всех данных для создания платежа
        for key in required_key:
            if key not in request.GET:
                return HttpResponseBadRequest(status=HTTPStatus.BAD_REQUEST, reason='Not enough info for create pay',
                                              content='Not enough info for create pay'
                                              )
        logger.debug('Key ok')
        try:
            payment, status = Payment.objects.get_or_create(
                shop_id=shop_id,
                outer_order_id=outer_order_id,
                user_login=user_login,
                amount=amount,
            )
            logger.debug(f'payment, status: {payment} {status}')
        except Exception as err:
            raise err
        if payment.status > 0 or payment.status == -1:
            return redirect(reverse('payment:pay_result', kwargs={'pk': payment.id}))

        requisite = get_pay_requisite(pay_type)
        # Если нет активных реквизитов
        if not requisite:
            # Перенаправляем на извинения
            return redirect(reverse('payment:payment_type_not_worked'))

        # Сохраняем реквизит к рлатежу
        if not payment.pay_requisite:
            requisite = get_pay_requisite(pay_type)
            payment.pay_requisite = requisite
            payment.save()

        form = forms.InvoiceForm(instance=payment, )
        context = {'form': form, 'payment': payment, 'data': get_time_remaining_data(payment)}
        return render(request, context=context, template_name='payment/invoice.html')

    elif request.method == 'POST':
        # Обработка нажатия кнопки
        outer_order_id = request.POST.get('outer_order_id')
        amount = request.POST.get('amount')
        payment, status = Payment.objects.get_or_create(outer_order_id=outer_order_id, amount=amount)
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
            return render(request, context=context, template_name='payment/invoice.html')
    logger.critical('афй')


class PayResultView(DetailView):
    form_class = InvoiceForm
    template_name = 'payment/pay_result.html'
    model = Payment

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status'] = self.object.PAYMENT_STATUS[self.object.status][1]
        context['data'] = get_time_remaining_data(self.object)
        return context


class PaymentListView(ListView):
    """Спиок заявок"""
    template_name = 'payment/payment_list.html'
    model = Payment
    fields = ('confirmed_amount',
                  'incoming')

    # def get(self, request, *args, **kwargs):
    #     logger.debug('get form', request=request, args=args, kwargs=kwargs)
    #     self.object = Payment.objects.first()
    #     return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = PaymentListConfirmForm()
        context['form'] = form
        return context

    def post(self, request, *args, **kwargs):
        logger.debug('Обработка нажатия кнопки списка заявок')
        logger.info(request.POST.keys())
        logger.info(request.POST.dict())

        payment_id = confirmed_amount = incoming_id = None
        for key in request.POST.keys():
            if 'cancel_payment' in request.POST.keys():
                payment_id = request.POST['cancel_payment']
                # Отклонение заявки
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
            if key.startswith('incoming_id_value:'):
                incoming_id = request.POST[key]
                if incoming_id:
                    incoming_id = int(incoming_id)
        logger.debug('Получили:',
                     payment_id=payment_id,
                     confirmed_amount=confirmed_amount,
                     incoming_id=incoming_id)
        payment = Payment.objects.get(pk=payment_id)
        logger.debug(payment)
        form = PaymentListConfirmForm(instance=payment,
                                      data={'confirmed_amount': confirmed_amount,
                                            'incoming': incoming_id
                                            })
        if form.is_valid():
            # Логика подтверждения заявки
            logger.debug(f'valid {form.cleaned_data}')
            payment.status = 2
            payment.confirmed_time = timezone.now()
            form.save()
        else:
            return HttpResponseBadRequest(str(form.errors))
        return redirect(reverse('payment:payment_list'))


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
    logger.info(f'REQUEST_METHOD: {REQUEST_METHOD}')
    logger.info(f'SERVER_NAME: {SERVER_NAME}')
    logger.info(f'SERVER_PORT: {SERVER_PORT}')

    context = {'http_host': request.META['HTTP_HOST']}
    logger.debug(http_host)
    shop: Shop = Shop.objects.get(pk=1)
    print('--------------')
    url = 'http://45.67.228.39/receive_request/'
    logger.info(request)
    logger.info(f'Requests to url: {url}')
    try:
        result = requests.get(url, data={'aaa': 'bbb'})
        logger.info(result.status_code)
    except Exception as err:
        logger.error(err)


    return render(request,
                  template_name='payment/test_send.html',
                  context=context
                  )


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
    REQUEST_METHOD = request.META.get('REQUEST_METHOD')
    SERVER_NAME = request.META.get('SERVER_NAME')
    SERVER_PORT = request.META.get('SERVER_PORT')
    logger.info(CONTENT_LENGTH)
    logger.info(CONTENT_TYPE)
    logger.info(HTTP_ACCEPT)
    logger.info(HTTP_ACCEPT_ENCODING)
    logger.info(HTTP_ACCEPT_LANGUAGE)
    logger.info(HTTP_HOST)
    logger.info(HTTP_REFERER)
    logger.info(HTTP_USER_AGENT)
    logger.info(QUERY_STRING)
    logger.info(REMOTE_ADDR)
    logger.info(REMOTE_HOST)
    logger.info(REMOTE_USER)
    logger.info(REQUEST_METHOD)
    logger.info(SERVER_NAME)
    logger.info(SERVER_PORT)
    return HttpResponse(status=HTTPStatus.OK)


