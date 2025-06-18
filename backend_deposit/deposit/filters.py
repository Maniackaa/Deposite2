from datetime import timedelta

import django_filters
import structlog
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import F, Value, Q
from django.db.models.functions import Extract
from django.forms import DateTimeInput, CheckboxInput
from django.utils import timezone

from deposit.models import IncomingCheck, Incoming, BirpayOrder

logger = structlog.get_logger('deposit')

class MyDateInput(forms.DateInput):
    input_type = 'date'
    format = '%Y-%m-%d'


class IncomingCheckFilter(django_filters.FilterSet):

    def __init__(self, data=None, *args, **kwargs):
        if data is not None:
            data = data.copy()
            for name, f in self.base_filters.items():
                initial = f.extra.get('initial')
                if not data.get(name) and initial:
                    data[name] = initial
        super().__init__(data, *args, **kwargs)

    id = django_filters.CharFilter(lookup_expr='icontains')
    operator = django_filters.CharFilter(lookup_expr='icontains')
    status = django_filters.MultipleChoiceFilter(choices=[('-1', '-1'), ('0', '0'), ('1', '1'), ('2', '2'), ],
                                                 null_label='Нет статуса')
    # oper1 = django_filters.CharFilter(label='Оператор №', method='my_custom_filter', initial=1, max_length=3)
    # oper2 = django_filters.CharFilter(label='из', method='my_custom_filter2', initial=1)
    create_at = django_filters.DateFilter(label='Дата проверки', field_name='create_at', lookup_expr='contains',
                                          widget=MyDateInput({'class': 'form-control'}))

    def with_delta(self, queryset, name, value):
        return queryset.filter(delta__gt=0)

    @property
    def qs(self):
        parent = super(IncomingCheckFilter, self).qs
        return parent.filter()

    class Meta:
        model = IncomingCheck
        fields = ['id', 'birpay_id', 'user', 'operator', 'status']


class IncomingStatSearch(django_filters.FilterSet):
    birpay_confirm_time__gte = django_filters.DateTimeFilter(
        field_name='birpay_confirm_time',
        lookup_expr='gte',
        label='Дата апрува от',
        widget=DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'})
    )
    birpay_confirm_time__lt = django_filters.DateTimeFilter(
        field_name='birpay_confirm_time',
        lookup_expr='lt',
        label='Дата апрува до',
        widget=DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'})
    )
    only_with_birpay = django_filters.BooleanFilter(
        label='Только с BirPay ID',
        method='filter_with_birpay',
        widget=CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def filter_with_birpay(self, queryset, name, value):
        if value:
            return queryset.exclude(birpay_id__isnull=True).exclude(birpay_id='')
        return queryset

    class Meta:
        model = Incoming
        fields = ['birpay_confirm_time__gte', 'birpay_confirm_time__lt', 'only_with_birpay']


class MyTimeInput(DateTimeInput):
    input_type = 'datetime-local'


class BirpayPanelFilter(django_filters.FilterSet):
    card_number = django_filters.MultipleChoiceFilter(widget=forms.SelectMultiple(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cards = BirpayOrder.objects.order_by().filter(sended_at__gte=timezone.now() - timedelta(days=5)).values_list('card_number', flat=True).distinct()
        self.filters['card_number'].field.choices = [(card, card) for card in cards if card]


class BirpayOrderFilter(django_filters.FilterSet):
    incoming_id = django_filters.BooleanFilter(
        method='filter_incoming_id',
        label='Есть incoming'
    )
    incoming_id_exact = django_filters.NumberFilter(
        field_name='incoming_id',
        lookup_expr='exact',
        label='ID incoming'
    )
    updated_at_gte = django_filters.DateTimeFilter(
        label='От включая', field_name='updated_at', lookup_expr='gte',
        widget=MyTimeInput({'class': 'form-control'})
    )
    updated_at_lt = django_filters.DateTimeFilter(
        label='до (не включая)', field_name='updated_at', lookup_expr='lt',
        widget=MyTimeInput({'class': 'form-control'})
    )
    incoming_pay_gte = django_filters.NumberFilter(
        field_name='incoming_pay', lookup_expr='gte', label='Наш amount >='
    )
    incoming_pay_lte = django_filters.NumberFilter(
        field_name='incoming_pay', lookup_expr='lte', label='Наш amount Pay <='
    )
    delta_gte = django_filters.NumberFilter(
        field_name='delta', lookup_expr='gte', label='Delta >='
    )
    delta_lte = django_filters.NumberFilter(
        field_name='delta', lookup_expr='lte', label='Delta <='
    )

    # Все текстовые поля icontains
    merchant_transaction_id = django_filters.CharFilter(
        lookup_expr='icontains', label='Merchant Tx ID'
    )
    customer_name = django_filters.CharFilter(
        lookup_expr='icontains', label='customer_name'
    )
    merchant_name = django_filters.CharFilter(
        lookup_expr='icontains', label='Мерчант'
    )
    merchant_user_id = django_filters.CharFilter(
        lookup_expr='icontains', label='User'
    )

    # Оператор — выбор из уникальных значений
    operator = django_filters.ChoiceFilter(
        label='Оператор',
        choices=lambda: [
            (op, op) for op in BirpayOrder.objects.order_by('operator')
                .values_list('operator', flat=True).distinct().exclude(operator__isnull=True).exclude(operator__exact='')
        ]
    )

    # Статус — мультивыбор
    status = django_filters.MultipleChoiceFilter(
        choices=[(0, '0'), (1, '1'), (2, '2')],
        widget=forms.CheckboxSelectMultiple,
        label='Статус'
    )
    check_download = django_filters.BooleanFilter(
        method='check_present',
        label='Скачан чек'
    )
    gpt_data = django_filters.BooleanFilter(
        method='gpt_data_present',
        label='Чек рапознан'
    )

    gpt_status = django_filters.MultipleChoiceFilter(
        choices=[(0, '0'), (1, 'Подтвердил')],
        widget=forms.CheckboxSelectMultiple,
        label='Gpt ИМХО'
    )


    def gpt_data_present(self, queryset, name, value):
        if value:
            return queryset.exclude(gpt_data={})
        else:
            return queryset.filter(gpt_data={})

    def filter_incoming_id(self, queryset, name, value):
        if value:
            return queryset.exclude(incoming_id__isnull=True)
        else:
            return queryset.filter(incoming_id__isnull=True)

    def check_present(self, queryset, name, value):
        logger.info(f'{name} {value}')
        if value:
            return queryset.exclude(check_file='').exclude(check_file__isnull=True)
        else:
            return queryset.filter(Q(check_file__isnull=True) | Q(check_file=''))

    class Meta:
        model = BirpayOrder
        fields = [
            'status', 'merchant_transaction_id', 'customer_name', 'merchant_name',
            'merchant_user_id', 'operator',
            # аннотированные поля НЕ указывать здесь
        ]
