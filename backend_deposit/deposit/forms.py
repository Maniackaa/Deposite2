import datetime
import logging
import re
import string
from copy import copy

import colorfield.fields
import structlog
from colorfield.fields import ColorField
from django import forms
from django.contrib.admin import widgets
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Subquery, Q, Max
import random
from django.forms import CheckboxInput
from django.utils import timezone

from .models import Incoming, ColorBank, BadScreen, Bank, RequsiteZajon, BirpayOrder
from .widgets import MinimalSplitDateTimeMultiWidget

logger = structlog.get_logger('deposit')

User = get_user_model()


def get_choice_by_banks():
    """Функция которая группирует получателей по банкам на основе BIN-кодов"""
    try:
        # Получаем все банки с их BIN-кодами
        banks = Bank.objects.all().order_by('name')
        
        # Получаем всех получателей за последние 30 дней (исключая телефоны)
        start_q = Incoming.objects.filter(register_date__gte=timezone.now() - datetime.timedelta(days=30))
        q = start_q.exclude(
            recipient__iregex=r'\d\d\d\d \d\d.*\d\d\d\d').exclude(
            type__in=('m10', 'm10_short'), sender__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d'
        ).distinct('recipient').values('pk')
        
        distinct_recipients = start_q.filter(
            pk__in=Subquery(q), recipient__isnull=False
        ).exclude(
            recipient__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d'  # исключаем телефоны
        ).values('recipient').order_by('register_date')
        
        # Группируем получателей по банкам
        bank_groups = {}
        ungrouped = []
        
        for recipient_data in distinct_recipients:
            recipient = recipient_data['recipient']
            
            # Извлекаем BIN только если строка начинается с 4 цифр
            recipient_str = (recipient or '').strip()
            start_bin_match = re.match(r'^(\d{4})', recipient_str)
            if start_bin_match:
                bin_code = int(start_bin_match.group(1))
                
                # Ищем банк по BIN-коду
                found_bank = None
                for bank in banks:
                    if bin_code in bank.bins:
                        found_bank = bank
                        break
                
                if found_bank:
                    if found_bank.name not in bank_groups:
                        bank_groups[found_bank.name] = []
                    bank_groups[found_bank.name].append((recipient, recipient))
                else:
                    ungrouped.append((recipient, recipient))
            else:
                ungrouped.append((recipient, recipient))
        
        # Формируем результат: сначала банки, потом негруппированные
        result = []
        for bank_name in sorted(bank_groups.keys()):
            result.append((bank_name, bank_groups[bank_name]))
        
        if ungrouped:
            result.append(('Неопределенные', ungrouped))
            
        return result
        
    except Exception as err:
        logger.error(err, exc_info=True)
        return []


def get_choice(recepient_type='phone'):
    """Функция которая ищет получателя для фильтра в форме (старая версия)"""
    try:

        tables = connection.creation.connection.introspection.get_table_list(connection.cursor())
        if not tables:
            return []
        result = []
        for table in tables:
            if 'deposit_incoming' == table.name:
                start_q = Incoming.objects.filter(register_date__gte=timezone.now() - datetime.timedelta(days=30))
                q = start_q.exclude(
                    recipient__iregex=r'\d\d\d\d \d\d.*\d\d\d\d').exclude(
                    type__in=('m10', 'm10_short'), sender__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d'
                ).distinct('recipient').values('pk')
                # distinct_recipients = Incoming.objects.filter(
                #     pk__in=Subquery(q)).order_by('-register_date').all()
                distinct_recipients = start_q.filter(
                    pk__in=Subquery(q), recipient__isnull=False).values('recipient').order_by('register_date')
                if recepient_type == 'phone':
                    phone_recipents = distinct_recipients.filter(recipient__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d')
                    distinct_recipients = phone_recipents
                elif recepient_type == 'stars':
                    stars_recipents = distinct_recipients.filter(recipient__iregex=r'^\*\*\*')
                    distinct_recipients = stars_recipents
                elif recepient_type == 'card':
                    phone_recipents = distinct_recipients.filter(recipient__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d')
                    stars_recipents = distinct_recipients.filter(recipient__iregex=r'^\*\*\*')
                    distinct_recipients = distinct_recipients.filter(~Q(recipient__in=phone_recipents.values('recipient'))).filter(~Q(recipient__in=stars_recipents.values('recipient')))
                distinct_recipients = copy([x for x in distinct_recipients])
                # for incoming in sorted(distinct_recipients, key=lambda x: bool(re.findall(r'\d\d\d \d\d \d\d\d \d\d \d\d', x['recipient']))):
                for incoming in distinct_recipients:
                    result.append((incoming['recipient'], incoming['recipient']))
        return result
    except Exception as err:
        logger.error(err, exc_info=True)
        return []


class MyFilterForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super(MyFilterForm, self).__init__(*args, **kwargs)
        
        # Получаем группы по банкам
        bank_groups = get_choice_by_banks()
        
        # Создаем динамические поля для каждого банка
        for bank_name, choices in bank_groups:
            field_name = f'bank_{bank_name.lower().replace(" ", "_").replace("-", "_")}'
            self.fields[field_name] = forms.MultipleChoiceField(
                choices=choices,
                widget=forms.CheckboxSelectMultiple,
                required=False,
                label=bank_name
            )
            
            # Устанавливаем начальные значения если они есть
            if self.initial and isinstance(self.initial, dict):
                # Если initial содержит список получателей, проверяем какие из них относятся к этому банку
                if 'recipients' in self.initial:
                    bank_recipients = [choice[0] for choice in choices]
                    selected_for_bank = [r for r in self.initial['recipients'] if r in bank_recipients]
                    if selected_for_bank:
                        self.fields[field_name].initial = selected_for_bank

    # Старые поля для обратной совместимости (можно удалить позже)
    my_filter = forms.MultipleChoiceField(choices=copy(get_choice('phone')), widget=forms.CheckboxSelectMultiple, required=False)
    my_filter2 = forms.MultipleChoiceField(choices=copy(get_choice('card')), widget=forms.CheckboxSelectMultiple, required=False)
    my_filter3 = forms.MultipleChoiceField(choices=copy(get_choice('stars')), widget=forms.CheckboxSelectMultiple, required=False)


class ColorBankForm(forms.ModelForm):
    color_back = colorfield.fields.ColorWidget()
    color_font = colorfield.fields.ColorWidget()


    class Meta:
        model = ColorBank
        fields = '__all__'


class IncomingForm(forms.ModelForm):
    # register_date = forms.CharField(disabled=True, required=False)
    # response_date = forms.DateTimeField(disabled=True, required=False)
    # sender = forms.Field(disabled=True, required=False)
    # recipient = forms.Field(disabled=True, required=False)
    #
    # pay = forms.Field(disabled=True, required=False)
    # balance = forms.Field(disabled=True, required=False)
    # transaction = forms.Field(disabled=True, required=False)
    # type = forms.Field(disabled=True, required=False)
    # worker = forms.CharField(disabled=True, required=False)
    # image = forms.ImageField(disabled=True, required=False)
    # birpay_confirm_time = forms.DateTimeField(disabled=True, required=False)
    # birpay_edit_time = forms.DateTimeField(disabled=True, required=False)
    # confirmed_deposit = forms.Select()
    birpay_id = forms.IntegerField(required=False)
    merchant_user_id = forms.CharField(max_length=16, required=False, label='ID пользователя мерчанта')
    comment = forms.CharField(widget=forms.Textarea, required=False)

    class Meta:
        model = Incoming
        fields = ('birpay_id', 'merchant_user_id', 'comment')
        # exclude = ('birpay_confirm_time', 'worker', 'type')


class IncomingSearchForm(forms.Form):
    pk = forms.IntegerField(required=False, label='id')
    merchant_user_id = forms.CharField(max_length=16, required=False, label='ID пользователя мерчанта')
    search_in = forms.ChoiceField(choices=[
        ('response_date', 'Время в смс/чеке'),
        ('register_date', 'Время поступления'),

    ], label='Поиск по')
    begin = forms.DateTimeField(widget=MinimalSplitDateTimeMultiWidget(), required=False)
    end = forms.DateTimeField(widget=MinimalSplitDateTimeMultiWidget(), required=False)
    only_empty = forms.BooleanField(widget=CheckboxInput(), label='Только неподтвержденные', required=False)
    pay = forms.FloatField(required=False)
    sort_by_sms_time = forms.ChoiceField(choices=[(1, 'Да'), (0, 'Нет')], label='Сортировка по времени чека', required=False)


class CheckSmsForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea(attrs={'cols': '20', 'rows': 10}))

    class Meta:
        fields = ('text', )


class CheckScreenForm(forms.Form):
    # screen = forms.ImageField(help_text="Upload image: ", required=False)

    screen = forms.ModelChoiceField(
        queryset=BadScreen.objects.order_by('-id').all(),
        blank=True,
        required=False,
    )

    class Meta:
        fields = ('screen', )


class AssignCardsToUserForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_staff=True, is_active=True),
        label="Оператор"
    )
    assigned_card_numbers = forms.CharField(
        label="Назначенные карты",
        widget=forms.Textarea(attrs={
            'rows': 3, 
            'class': 'form-control',
            'style': 'font-family: monospace; font-size: 14px; min-width: 400px;'
        }),
        help_text="Введите номера карт через запятую или с новой строки.",
        required=False
    )

    def clean_assigned_card_numbers(self):
        data = self.cleaned_data['assigned_card_numbers']
        # Если поле пустое, возвращаем пустой список
        if not data or not data.strip():
            return []
        # Всегда возвращаем список
        cards = [c.strip() for c in data.replace('\n', ',').split(',') if c.strip()]
        return cards



class MoshennikListForm(forms.Form):
    moshennik_list = forms.CharField(
        label="Список мошенников",
        widget=forms.Textarea(attrs={'rows': 30, 'class': 'form-control'}),
        help_text="Втавьте список мошенников через запятую или с новой строки."
    )

    def clean_moshennik_list(self):
        data = self.cleaned_data['moshennik_list']
        # Всегда возвращаем список
        result = [c.strip() for c in data.replace('\n', ',').split(',') if c.strip()]
        return result

class PainterListForm(forms.Form):
    painter_list = forms.CharField(
        label="Список рисовальщиков",
        widget=forms.Textarea(attrs={'rows': 30, 'class': 'form-control'}),
        help_text="Втавьте список рисовальщиков через запятую или с новой строки."
    )

    def clean_painter_list(self):
        data = self.cleaned_data['painter_list']
        logger.info(f'clean_painter_list: {data}')
        # Всегда возвращаем список
        result = [c.strip() for c in data.replace('\n', ',').split(',') if c.strip()]
        return result


class OperatorStatsDayForm(forms.Form):
    date = forms.DateField(label='День', widget=forms.DateInput(attrs={'type': 'date'}))


class RequsiteZajonForm(forms.ModelForm):
    # Поле для редактирования сырого значения из payload
    raw_card_number = forms.CharField(
        label='Номер карты (сырое значение)',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Например: 4189 8000 8635 5664 Bu kartlara yalnız elektron ödəniş sistemlərindən köçürmə ed'
        }),
        help_text='Введите сырое значение из Birpay. Система автоматически извлечет номер карты (16 цифр) и проверит его валидность.'
    )
    
    class Meta:
        model = RequsiteZajon
        fields = ('works_on_asu',)  # card_number больше не редактируется напрямую
        widgets = {
            'works_on_asu': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Устанавливаем начальное значение raw_card_number из payload
        if self.instance and self.instance.pk:
            payload = self.instance.payload or {}
            self.fields['raw_card_number'].initial = payload.get('card_number', '') or self.instance.card_number or ''
    
    def clean_raw_card_number(self):
        from deposit.birpay_requisite_service import validate_card_number_raw
        raw_card_number = self.cleaned_data.get('raw_card_number', '')
        try:
            return validate_card_number_raw(raw_card_number, allow_empty=True)
        except ValueError as e:
            raise forms.ValidationError(str(e))


class RequisiteCardEditForm(forms.Form):
    """Форма редактирования номера карты реквизита по ID (страница Z-ASU)."""
    requisite_id = forms.IntegerField(
        label='ID реквизита',
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Например: 2090'}),
        help_text='ID реквизита в Birpay (должен быть в списке реквизитов Zajon).',
    )
    card_number = forms.CharField(
        label='Новый номер карты',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '16 цифр или сырое значение с текстом'}),
        help_text='Номер карты (16 цифр) или сырое значение из Birpay.',
    )

    def clean_requisite_id(self):
        rid = self.cleaned_data.get('requisite_id')
        if rid is not None and not RequsiteZajon.objects.filter(pk=rid).exists():
            raise forms.ValidationError(
                f'Реквизит с ID {rid} не найден. Синхронизируйте реквизиты со страницы «Реквизиты Zajon».'
            )
        return rid

    def clean_card_number(self):
        from deposit.birpay_requisite_service import validate_card_number_raw
        raw = (self.cleaned_data.get('card_number') or '').strip()
        try:
            return validate_card_number_raw(raw, allow_empty=False)
        except ValueError as e:
            raise forms.ValidationError(str(e))


class BirpayOrderCreateForm(forms.ModelForm):
    """Форма для ручного создания тестовых BirpayOrder"""
    
    class Meta:
        model = BirpayOrder
        fields = [
            'birpay_id', 'merchant_transaction_id', 'merchant_user_id', 'amount',
            'status', 'created_at', 'updated_at', 'merchant_name', 'customer_name',
            'card_number', 'operator', 'check_file_url'
        ]
        widgets = {
            'birpay_id': forms.NumberInput(attrs={'class': 'form-control', 'required': True}),
            'merchant_transaction_id': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'merchant_user_id': forms.TextInput(attrs={'class': 'form-control', 'required': True, 'maxlength': 16}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'required': True}),
            'status': forms.Select(attrs={'class': 'form-control', 'required': True}),
            'created_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'updated_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'merchant_name': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 64}),
            'customer_name': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 128}),
            'card_number': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 20, 'placeholder': '4111 1111 1111 1111 (для Z-ASU)'}),
            'operator': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 128}),
            'check_file_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://example.com/receipt.jpg'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Устанавливаем значения по умолчанию для дат (+3 часа для тестовой заявки), если форма новая
        if not self.instance.pk:
            now = timezone.now() + datetime.timedelta(hours=3)
            # Форматируем дату для datetime-local input
            now_str = now.strftime('%Y-%m-%dT%H:%M')
            self.fields['created_at'].initial = now_str
            self.fields['updated_at'].initial = now_str
            
            # Генерируем случайный birpay_id (максимальный + случайное число от 1 до 1000)
            max_birpay_id = BirpayOrder.objects.aggregate(max_id=Max('birpay_id'))['max_id'] or 0
            random_birpay_id = max_birpay_id + random.randint(1, 1000)
            self.fields['birpay_id'].initial = random_birpay_id
            
            # Генерируем Merchant Transaction ID (6-значное число, начиная с 100000)
            merchant_tx_id = str(100000 + random_birpay_id)
            self.fields['merchant_transaction_id'].initial = merchant_tx_id
            
            # Генерируем Merchant User ID (случайная строка из букв и цифр, макс 16 символов)
            merchant_user_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
            self.fields['merchant_user_id'].initial = merchant_user_id
            
            # Устанавливаем значение по умолчанию для суммы
            self.fields['amount'].initial = 100.0
            
            # Устанавливаем значение по умолчанию для номера карты
            self.fields['card_number'].initial = '4111 1111 1111 1111'
            
            # Устанавливаем значение по умолчанию для URL чека (полный URL)
            check_file_url = "http://45.14.247.139/media/uploaded_pay_screens/Frame_38_ysLyLMQ.jpg"
            self.fields['check_file_url'].initial = check_file_url
        
        # Устанавливаем choices для статуса
        self.fields['status'].widget.choices = [
            (0, '0 - Pending (ожидает)'),
            (1, '1 - Approved (подтвержден)'),
            (2, '2 - Declined (отклонен)'),
        ]
        
        # Скрываем поля дат (они будут заполняться автоматически)
        self.fields['created_at'].widget = forms.HiddenInput()
        self.fields['updated_at'].widget = forms.HiddenInput()
    
    def clean_birpay_id(self):
        birpay_id = self.cleaned_data.get('birpay_id')
        if birpay_id:
            # Проверяем уникальность, исключая текущий объект
            qs = BirpayOrder.objects.filter(birpay_id=birpay_id)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(f'BirpayOrder с birpay_id={birpay_id} уже существует')
        return birpay_id
    
    def clean_card_number(self):
        card_number = self.cleaned_data.get('card_number', '').strip()
        if card_number:
            # Убираем пробелы и дефисы
            cleaned = re.sub(r'[\s\-]', '', card_number)
            if not re.match(r'^\d+$', cleaned):
                raise forms.ValidationError('Номер карты должен содержать только цифры')
            return cleaned
        return card_number
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Если даты не заполнены, устанавливаем текущее время +3 часа (для тестовой заявки)
        now_plus_3h = timezone.now() + datetime.timedelta(hours=3)
        if not instance.created_at:
            instance.created_at = now_plus_3h
        if not instance.updated_at:
            instance.updated_at = now_plus_3h
        
        # Создаем raw_data в формате, похожем на данные от Birpay API
        raw_data = {
            'id': instance.birpay_id,
            'createdAt': instance.created_at.isoformat(),
            'updatedAt': instance.updated_at.isoformat(),
            'merchantTransactionId': instance.merchant_transaction_id,
            'merchantUserId': instance.merchant_user_id,
            'amount': str(instance.amount),
            'status': instance.status,
        }
        
        if instance.merchant_name:
            raw_data['merchant'] = {'name': instance.merchant_name}
        
        if instance.customer_name:
            raw_data['customerName'] = instance.customer_name
        
        if instance.card_number:
            raw_data['paymentRequisite'] = {
                'payload': {
                    'card_number': instance.card_number
                }
            }
        
        if instance.check_file_url:
            raw_data['payload'] = {'check_file': instance.check_file_url}
        
        if instance.operator:
            raw_data['operator'] = {'username': instance.operator}
        
        instance.raw_data = raw_data
        
        # Устанавливаем sender как последние 4 цифры карты
        if instance.card_number:
            # Убираем пробелы перед извлечением последних 4 цифр
            cleaned_card = re.sub(r'[\s\-]', '', instance.card_number)
            instance.sender = cleaned_card[-4:] if len(cleaned_card) >= 4 else cleaned_card
        
        if commit:
            instance.save()
        return instance