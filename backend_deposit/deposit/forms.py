import datetime
import logging
import re
from copy import copy

import colorfield.fields
import structlog
from colorfield.fields import ColorField
from django import forms
from django.contrib.admin import widgets
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Subquery, Q
from django.forms import CheckboxInput
from django.utils import timezone

from .models import Incoming, ColorBank, BadScreen
from .widgets import MinimalSplitDateTimeMultiWidget

logger = structlog.get_logger('deposit')

User = get_user_model()


def get_choice(recepient_type='phone'):
    """Функция которая ищет получателя для фильтра в форме"""
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
        queryset=User.objects.filter(is_staff=True),
        label="Оператор"
    )
    assigned_card_numbers = forms.CharField(
        label="Назначенные карты",
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        help_text="Введите номера карт через запятую или с новой строки."
    )

    def clean_assigned_card_numbers(self):
        data = self.cleaned_data['assigned_card_numbers']
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