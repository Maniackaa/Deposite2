import django_filters
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import F, Value
from django.db.models.functions import Extract
from django.forms import DateTimeInput, CheckboxInput

from deposit.models import IncomingCheck, Incoming, BirpayOrder


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

class BirpayOrderFilter(django_filters.FilterSet):
    updated_at_gte = django_filters.DateTimeFilter(label='От включая', field_name='updated_at', lookup_expr='gte',
                                                   widget=MyTimeInput({'class': 'form-control'})
                                                   )
    updated_at_lt = django_filters.DateTimeFilter(label='до (не включая)', field_name='updated_at', lookup_expr='lt',
                                                   widget=MyTimeInput({'class': 'form-control'})
                                                   )
    class Meta:
        model = BirpayOrder
        fields = [
            'birpay_id', 'status', 'merchant_transaction_id', 'customer_name', 'merchant_name', 'merchant_user_id', 'operator',

        ]
