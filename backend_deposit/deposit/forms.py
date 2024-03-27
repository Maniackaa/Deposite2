import logging
import re
from copy import copy

import colorfield.fields
from colorfield.fields import ColorField
from django import forms
from django.contrib.admin import widgets
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Subquery
from django.forms import CheckboxInput

from .models import Deposit, Incoming, ColorBank
from .widgets import MinimalSplitDateTimeMultiWidget

logger = logging.getLogger(__name__)
err_log = logging.getLogger('error_log')


class DepositForm(forms.ModelForm):
    def clean_phone(self):
        phone = self.cleaned_data['phone']
        cleaned_phone = ''
        for num in phone:
            if num.isdigit() or num in '+':
                cleaned_phone += num
        if not cleaned_phone.startswith('+994'):
            raise ValidationError('Телефон должен начинаться с +994')
        if len(cleaned_phone) != 13:
            raise ValidationError('Неверное количество цифр в телефоне')
        return cleaned_phone

    uid = forms.CharField(widget=forms.HiddenInput)
    input_transaction = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Deposit
        fields = ('phone', 'pay_sum', 'uid')
        hidden_fields = ('uid',  'input_transaction')
        help_texts = {'phone': 'Ваш телефон',
                      'pay_sum': 'Сумма платежа'}
        labels = {'phone': 'Your phone', 'pay_sum': 'Pay summ (Min: 5 AZN, Max: Unlim)'}


class DepositEditForm(forms.ModelForm):
    uid = forms.Field(disabled=True)
    phone = forms.Field(disabled=True)
    pay_sum = forms.Field(disabled=True)
    input_transaction = forms.Field(disabled=True)
    confirmed_incoming = forms.ModelChoiceField(
        queryset=None,
        blank=True,
        required=False,
    )
    status = forms.CharField(widget=forms.HiddenInput, disabled=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        deposit: Deposit = kwargs.get('instance')
        incoming_id = None
        if deposit.confirmed_incoming:
            incoming_id = deposit.confirmed_incoming.id
        self.fields['confirmed_incoming'].queryset = Incoming.objects.filter(confirmed_deposit=None).order_by('-id') | Incoming.objects.filter(id=incoming_id)

    class Meta:
        model = Deposit
        fields = ('input_transaction', 'pay_sum', 'phone', 'uid', 'confirmed_incoming', )
        hidden_fields = ('pay_screen',)


class DepositImageForm(forms.ModelForm):
    uid = forms.CharField(widget=forms.HiddenInput, disabled=True)
    phone = forms.CharField(widget=forms.HiddenInput, disabled=True)
    pay_sum = forms.IntegerField(widget=forms.HiddenInput, disabled=True)
    input_transaction = forms.IntegerField(widget=forms.HiddenInput, disabled=True)

    class Meta:
        model = Deposit
        fields = ('uid', 'phone', 'pay_sum', 'uid', 'pay_screen', 'input_transaction')
        # exclude = ('phone', 'pay_sum', 'uid',)
        hidden_fields = ('uid', 'phone', 'pay_sum', 'input_transaction')
        labels = {'pay_screen': '', 'input_transaction': 'Номер тарнзакции'}
        # help_texts = {'pay_screen': 'pay_screen',
        #               'input_transaction': 'input_transaction'}


class DepositTransactionForm(forms.ModelForm):
    uid = forms.CharField(widget=forms.HiddenInput)
    phone = forms.CharField(widget=forms.HiddenInput)
    pay_sum = forms.CharField(widget=forms.HiddenInput)
    input_transaction = forms.IntegerField(required=False, min_value=50_000_000, max_value=99_999_999)

    class Meta:
        model = Deposit
        fields = ('uid', 'phone', 'pay_sum', 'uid', 'input_transaction')
        hidden_fields = ('uid', 'phone', 'pay_sum')
        labels = {'pay_screen': '', 'input_transaction': 'Номер тарнзакции'}


def get_choice(recepient_type='phone'):
    """Функция которая ищет получателя для фильтра в форме"""
    try:

        tables = connection.creation.connection.introspection.get_table_list(connection.cursor())
        if not tables:
            return []
        result = []
        for table in tables:
            if 'deposit_incoming' == table.name:
                q = Incoming.objects.exclude(
                    recipient__iregex=r'\d\d\d\d \d\d.*\d\d\d\d').exclude(
                    type__in=('m10', 'm10_short'), sender__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d'
                ).distinct('recipient').values('pk')
                # distinct_recipients = Incoming.objects.filter(
                #     pk__in=Subquery(q)).order_by('-register_date').all()
                distinct_recipients = Incoming.objects.filter(
                    pk__in=Subquery(q), recipient__isnull=False).values('recipient').order_by('register_date')
                phone_recipents = distinct_recipients.filter(recipient__iregex=r'\d\d\d \d\d \d\d\d \d\d \d\d')
                stars_recipents = distinct_recipients.filter(recipient__iregex=r'^\*\*\*')
                if recepient_type == 'phone':
                    distinct_recipients = phone_recipents
                elif recepient_type == 'stars':
                    distinct_recipients = stars_recipents
                else:
                    distinct_recipients = distinct_recipients.exclude(pk__in=phone_recipents.values('id')).exclude(pk__in=stars_recipents.values('id'))
                distinct_recipients = copy([x for x in distinct_recipients])
                for incoming in sorted(distinct_recipients, key=lambda x: bool(re.findall(r'\d\d\d \d\d \d\d\d \d\d \d\d', x['recipient']))):
                    result.append((incoming['recipient'], incoming['recipient']))
        return result
    except Exception as err:
        logger.error(err)
        err_log.error(err, exc_info=True)
        return []


class MyFilterForm(forms.Form):
    def __init__(self, *args, **kwargs):
        # print('**********__init__ MyFilterForm')
        super(MyFilterForm, self).__init__(*args, **kwargs)
        if self.fields.get('my_filter'):
            self.fields['my_filter'].choices = copy(get_choice('phone'))
            self.fields['my_filter2'].choices = copy(get_choice('card'))
            self.fields['my_filter3'].choices = copy(get_choice('stars'))

    my_filter = forms.MultipleChoiceField(choices=copy(get_choice('phone')), widget=forms.CheckboxSelectMultiple, required=False)
    my_filter2 = forms.MultipleChoiceField(choices=copy(get_choice('card')), widget=forms.CheckboxSelectMultiple, required=False)
    my_filter3 = forms.MultipleChoiceField(choices=copy(get_choice('stars')), widget=forms.CheckboxSelectMultiple,
                                           required=False)


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
    comment = forms.CharField(widget=forms.Textarea, required=False)

    class Meta:
        model = Incoming
        fields = ('birpay_id', 'comment')
        # exclude = ('birpay_confirm_time', 'worker', 'type')


class IncomingSearchForm(forms.Form):
    pk = forms.IntegerField(required=False, label='id')
    search_in = forms.ChoiceField(choices=[
        ('register_date', 'Время поступления'),
        ('response_date', 'Время в смс/чеке')
    ], label='Поиск по')
    begin = forms.DateTimeField(widget=MinimalSplitDateTimeMultiWidget(), required=False)
    end = forms.DateTimeField(widget=MinimalSplitDateTimeMultiWidget(), required=False)
    only_empty = forms.BooleanField(widget=CheckboxInput(), label='Только неподтвержденные', required=False)
    pay = forms.FloatField(required=False)
    sort_by_sms_time = forms.BooleanField(widget=CheckboxInput(), label='Сортировка по времени чека', required=False)
