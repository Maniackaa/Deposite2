import datetime
import logging
import re
import uuid

from backend_deposit.settings import TZ
from django.conf import settings
from django.conf.global_settings import MEDIA_ROOT
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import (AccessMixin, LoginRequiredMixin,
                                        PermissionRequiredMixin)
from django.contrib.auth.views import redirect_to_login
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q, Subquery, Value, OuterRef
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import urlencode
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request


from deposit.forms import (ColorBankForm, DepositEditForm, DepositForm,
                           DepositImageForm, DepositTransactionForm,
                           IncomingForm, MyFilterForm, IncomingSearchForm)
from deposit.func import (img_path_to_str, make_after_incoming_save,
                          make_after_save_deposit, send_message_tg)
from deposit.models import BadScreen, ColorBank, Deposit, Incoming, TrashIncoming
from deposit.screen_response import screen_text_to_pay
from deposit.serializers import IncomingSerializer
from deposit.text_response_func import (response_sms1, response_sms2,
                                        response_sms3, response_sms4,
                                        response_sms5, response_sms6,
                                        response_sms7)

logger = logging.getLogger(__name__)
err_log = logging.getLogger('error_log')


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
        print('POST deposit_created', data)
        uid = data['uid']
        phone = data['phone']
        pay = data['pay_sum']
        deposit = Deposit.objects.filter(uid=uid).exists()
        print(deposit)
        print(uid, phone, pay)
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


def make_page_obj(request, objects, numbers_of_posts=100):
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


@api_view(['POST'])
def screen(request: Request):
    """
    Прием скриншота
    """
    try:
        host = request.META["HTTP_HOST"]  # получаем адрес сервера
        user_agent = request.META.get("HTTP_USER_AGENT")  # получаем данные бразера
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        path = request.path
        logger.debug(f'request.data: {request.data},'
                     f' host: {host},'
                     f' user_agent: {user_agent},'
                     f' path: {path},'
                     f' forwarded: {forwarded}')

        # params_example {'name': '/DCIM/Screen.jpg', 'worker': 'Station 1}
        image = request.data.get('image')
        worker = request.data.get('worker')
        name = request.data.get('name')

        if not image or not image.file:
            logger.info(f'Запрос без изображения')
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                                reason='no screen',
                                charset='utf-8')

        file_bytes = image.file.read()
        text = img_path_to_str(file_bytes)
        logger.debug(f'Распознан текст: {text}')
        pay = screen_text_to_pay(text)
        logger.debug(f'Распознан pay: {pay}')

        pay_status = pay.pop('status')
        errors = pay.pop('errors')

        if errors:
            logger.warning(f'errors: {errors}')
        sms_type = pay.get('type')

        if not sms_type:
            # Действие если скрин не по известному шаблону
            logger.debug('скрин не по известному шаблону')
            new_screen = BadScreen.objects.create(name=name, worker=worker, image=image)
            logger.debug(f'BadScreen сохранен')
            logger.debug(f'Возвращаем статус 200: not recognize')
            path = f'{host}{MEDIA_ROOT}{new_screen.image.url}'
            msg = f'Пришел хреновый скрин с {worker}: {name}\n{path}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
            return HttpResponse(status=status.HTTP_200_OK,
                                reason='not recognize',
                                charset='utf-8')

        # Если шаблон найден:
        if sms_type:
            transaction_m10 = pay.get('transaction')
            incoming_duplicate = Incoming.objects.filter(transaction=transaction_m10).all()
            # Если дубликат:
            if incoming_duplicate:
                logger.debug(f'Найден дубликат {incoming_duplicate}')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='Incoming duplicate',
                                    charset='utf-8')
            # Если статус отличается от 'успешно'
            if pay_status.lower() != 'успешно':
                logger.debug(f'fПлохой статус: {pay}.')
                # Проверяем на дубликат в BadScreen
                is_duplicate = BadScreen.objects.filter(transaction=transaction_m10).exists()
                if not is_duplicate:
                    logger.debug('Сохраняем в BadScreen')
                    BadScreen.objects.create(name=name, worker=worker, image=image,
                                             transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='New BadScreen',
                                        charset='utf-8')
                else:
                    logger.debug('Дубликат в BadScreen')
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='duplicate in BadScreen',
                                        charset='utf-8')

            # Действия со статусом Успешно
            serializer = IncomingSerializer(data=pay)
            if serializer.is_valid():
                # Сохраянем Incoming
                logger.debug(f'Incoming serializer valid. Сохраняем транзакцию {transaction_m10}')
                new_incoming = serializer.save(worker=worker, image=image)

                # Логика после сохранения
                make_after_incoming_save(new_incoming)

                # Сохраняем в базу-бота телеграм:
                # logger.debug(f'Пробуем сохранить в базу бота: {new_incoming}')
                # add_incoming_from_asu_to_bot_db(new_incoming)

                return HttpResponse(status=status.HTTP_201_CREATED,
                                    reason='created',
                                    charset='utf-8')
            else:
                # Если не сохранилось в Incoming
                logger.debug('Incoming serializer invalid')
                logger.debug(f'serializer errors: {serializer.errors}')
                transaction_error = serializer.errors.get('transaction')

                # Если просто дубликат:
                if transaction_error:
                    transaction_error_code = transaction_error[0].code
                    if transaction_error_code == 'unique':
                        # Такая транзакция уже есть. Дупликат.
                        return HttpResponse(status=status.HTTP_201_CREATED,
                                            reason='Incoming duplicate',
                                            charset='utf-8')

                # Обработа неизвестных ошибок при сохранении
                logger.warning('Неизестная ошибка')
                if not BadScreen.objects.filter(transaction=transaction_m10).exists():
                    BadScreen.objects.create(name=name, worker=worker, transaction=transaction_m10, type=sms_type)
                    return HttpResponse(status=status.HTTP_200_OK,
                                        reason='invalid serializer. Add to trash',
                                        charset='utf-8')
                return HttpResponse(status=status.HTTP_200_OK,
                                    reason='invalid serializer. Duplicate in trash',
                                    charset='utf-8')

    # Ошибка при обработке
    except Exception as err:
        logger.debug(f'Ошибка при обработке скрина: {err}')
        logger.error(err, exc_info=True)
        logger.debug(f'{request.data}')
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST,
                            reason=f'{err}',
                            charset='utf-8')


@api_view(['POST'])
def sms(request: Request):
    """
    Прием sms
    {'id': ['b1899338-2314-400c-a4ff-a9ef3d890c79'], 'from': ['icard'], 'to': [''], 'message': ['Mebleg:+50.00 AZN '], 'res_sn': ['111'], 'imsi': ['400055555555555'], 'imei': ['123456789000000'], 'com': ['COM39'], 'simno': [''], 'sendstat': ['0']}>, host: asu-payme.com, user_agent: None, path: /sms/, forwarded: 91.201.000.000
    """
    errors = []
    text = ''
    try:
        host = request.META["HTTP_HOST"]  # получаем адрес сервера
        user_agent = request.META.get("HTTP_USER_AGENT")  # получаем данные бразера
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        path = request.path
        logger.debug(f'request.data: {request.data},'
                     f' host: {host},'
                     f' user_agent: {user_agent},'
                     f' path: {path},'
                     f' forwarded: {forwarded}')

        post = request.POST
        text = post.get('message')
        sms_id = post.get('id')
        imei = post.get('imei')
        patterns = {
            'sms1': r'^Imtina:(.*)\nKart:(.*)\nTarix:(.*)\nMercant:(.*)\nMebleg:(.*) .+\nBalans:(.*) ',
            'sms2': r'.*Mebleg:(.+) AZN.*\nKart:(.*)\nTarix:(.*)\nMerchant:(.*)\nBalans:(.*) .*',
            'sms3': r'^.+[medaxil|mexaric] (.+?) AZN (.*)(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d).+Balance: (.+?) AZN.*',
            'sms4': r'^Amount:(.+?) AZN[\n]?.*\nCard:(.*)\nDate:(.*)\nMerchant:(.*)[\n]*Balance:(.*) .*',
            'sms5': r'.*Mebleg:(.+) AZN.*\n.*(\*\*\*.*)\nUnvan: (.*)\n(.*)\nBalans: (.*) AZN',
            'sms6': r'.*Mebleg:(.+) AZN.*\nHesaba medaxil: (.*)\nUnvan: (.*)\n(.*)\nBalans: (.*) AZN',
            'sms7': r'(.+) AZN.*\n(.+)\nBalans (.+) AZN\nKart:(.+)',
        }
        response_func = {
            'sms1': response_sms1,
            'sms2': response_sms2,
            'sms3': response_sms3,
            'sms4': response_sms4,
            'sms5': response_sms5,
            'sms6': response_sms6,
            'sms7': response_sms7,
        }
        fields = ['response_date', 'recipient', 'sender', 'pay', 'balance',
                  'transaction', 'type']
        text_sms_type = ''
        responsed_pay = {}

        for sms_type, pattern in patterns.items():
            search_result = re.findall(pattern, text)
            if search_result:
                logger.debug(f'Найдено: {sms_type}: {search_result}')
                text_sms_type = sms_type
                responsed_pay: dict = response_func[text_sms_type](fields, search_result[0])
                errors = responsed_pay.pop('errors')
                break

        # responsed_pay['message_url'] = message_url

        if text_sms_type:
            logger.info(f'Сохраняем в базу{responsed_pay}')
            is_duplicate = Incoming.objects.filter(
                response_date=responsed_pay.get('response_date'),
                sender=responsed_pay.get('sender'),
                pay=responsed_pay.get('pay'),
                balance=responsed_pay.get('balance')
            ).exists()
            if is_duplicate:
                logger.debug('Дубликат sms:\n\n{text}')
                msg = f'Дубликат sms:\n\n{text}'
                send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
            else:
                created = Incoming.objects.create(**responsed_pay, worker=imei)
                logger.debug(f'Создан: {created}')

        else:
            logger.info(f'Неизвестный шаблон\n{text}')
            new_trash = TrashIncoming.objects.create(text=text, worker=imei)
            logger.debug(f'Добавлено в мусор: {new_trash}')
        return HttpResponse(sms_id)

    except Exception as err:
        logger.error(f'Неизвестная ошибка при распознавании сообщения: {err}\n', exc_info=False)
        err_log.error(f'Неизвестная ошибка при распознавании сообщения: {err}\n', exc_info=True)
        raise err
    finally:
        if errors:
            msg = f'Ошибки при распознавании sms:\n{errors}\n\n{text}'
            send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)


@staff_member_required(login_url='users:login')
def incoming_list(request):
    # Список всех платежей и сохранение birpay
    if request.method == "POST":
        pk = list(request.POST.keys())[1]
        value = request.POST.get(pk) or None
        incoming = Incoming.objects.get(pk=pk)
        if not incoming.birpay_id:
            incoming.birpay_id = value
            incoming.birpay_confirm_time = datetime.datetime.now(tz=settings.TZ)
            incoming.save()
        return redirect('deposit:incomings')
    template = 'deposit/incomings_list.html'
    incoming_q = Incoming.objects.order_by('-id').all()
    context = {'page_obj': make_page_obj(request, incoming_q)}
    return render(request, template, context)


class IncomingFiltered(ListView):
    # Отфильтровованные платежи
    model = Incoming
    template_name = 'deposit/incomings_list.html'
    paginate_by = settings.PAGINATE

    def get_queryset(self, *args, **kwargs):
        if not self.request.user.is_staff:
            raise PermissionDenied('Недостаточно прав')
        user_filter = self.request.user.profile.my_filter
        filtered_incoming = Incoming.objects.filter(
            recipient__in=user_filter).order_by('-id').all()
        return filtered_incoming

    def get_context_data(self, **kwargs):
        context = super(IncomingFiltered, self).get_context_data(**kwargs)
        context['search_form'] = None
        return context


class IncomingSearch(ListView):
    # Поиск платежей
    model = Incoming
    template_name = 'deposit/incomings_list.html'
    paginate_by = settings.PAGINATE
    search_date = None

    @staticmethod
    def get_date(year, month, day):
        return datetime.date(int(year), int(month), int(day))

    def get_queryset(self):
        all_incoming = Incoming.objects.order_by('-id').all()
        if 'date_search' in self.request.GET:
            register_date_year = self.request.GET.get('register_date_year')
            register_date_month = self.request.GET.get('register_date_month')
            register_date_day = self.request.GET.get('register_date_day')
            search_date = self.get_date(register_date_year, register_date_month, register_date_day)
            self.search_date = search_date
            all_incoming = all_incoming.filter(
                register_date__date=search_date).order_by('-id').all()
        return all_incoming

    def get_context_data(self, **kwargs):
        context = super(IncomingSearch, self).get_context_data(**kwargs)
        search_form = IncomingSearchForm(initial={'register_date': self.search_date})
        context['search_form'] = search_form
        return context


@staff_member_required(login_url='users:login')
def my_filter(request):
    # Изменение фильтра по получателю
    context = {}
    user = request.user
    form = MyFilterForm(request.POST or None, initial={'my_filter': user.profile.my_filter})
    template = 'deposit/my_filter.html'
    context['form'] = form

    if request.POST:
        if form.is_valid():
            user_filter = form.cleaned_data.get("my_filter")
            user.profile.my_filter = user_filter
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
        # Добавляем новую переменную к контексту и инициализируем её некоторым значением
        # context['test'] = 'xxx'
        return context

    def form_valid(self, form):
        if form.is_valid():
            incoming: Incoming = self.object
            incoming.birpay_edit_time = datetime.datetime.now(tz=TZ)
            incoming.save()
            return super(IncomingEdit, self).form_valid(form)


class ColorBankCreate(CreateView):
    form_class = ColorBankForm
    template_name = 'deposit/color_bank_create.html'
    success_url = reverse_lazy('incomings')

    def form_valid(self, form):
        if form.is_valid():
            form.save()
