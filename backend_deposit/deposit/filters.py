import django_filters
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import F, Value
from django.db.models.functions import Extract

from deposit.models import IncomingCheck


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

    @property
    def qs(self):
        parent = super(IncomingCheckFilter, self).qs
        return parent.filter()

    class Meta:
        model = IncomingCheck
        fields = ['id', 'birpay_id', 'user', 'operator', 'status']