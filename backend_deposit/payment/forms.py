from django import forms
from django.core.exceptions import ValidationError

from payment.models import Payment, CreditCard


class InvoiceForm(forms.ModelForm):
    amount = forms.CharField(widget=forms.HiddenInput())
    order_id = forms.CharField(widget=forms.HiddenInput())
    screenshot = forms.ImageField(widget=forms.ClearableFileInput(), required=True, label='Скриншот об оплате')

    class Meta:
        model = Payment
        fields = (
                  'order_id',
                  'amount',
                  'phone',
                  'screenshot',
                  )


class InvoiceM10Form(forms.ModelForm):
    payment_id = forms.CharField(widget=forms.HiddenInput())
    amount = forms.CharField(widget=forms.TextInput(attrs={'placeholder': '₼',
                                                           }
                                                         ))
    owner_name = forms.CharField(label='owner_name',
                                 widget=forms.TextInput(), required=False)
    card_number = forms.CharField(label='card_number',
                                  widget=forms.TextInput(attrs={'placeholder': '0000 0000 0000 0000',
                                                                'minlength': 16,
                                                                'maxlength': 19,
                                                                }
                                                         )
                                  )
    months = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11), (12, 12), )
    expired_month = forms.CharField(label='expired_month',
                                    widget=forms.TextInput(attrs={'placeholder': 'MM',
                                                                  'minlength': 2,
                                                                  'maxlength': 2,
                                                                  }
                                                           )
                                    )
    expired_year = forms.CharField(label='expired_month',
                                   widget=forms.TextInput(attrs={'placeholder': 'YY',
                                                                 'minlength': 2,
                                                                 'maxlength': 2,
                                                                 }
                                                          )
                                   )
    cvv = forms.CharField(label='cvv',
                          widget=forms.PasswordInput(render_value=True, attrs={
                                       'placeholder': '***',
                                       'minlength': 3,
                                       'maxlength': 4,
                                   }))
    sms_code = forms.CharField(label='sms_code', required=False,
                               widget=forms.TextInput(attrs={'minlength': 4,
                                                             'maxlength': 6})
                               )

    class Meta:
        model = Payment
        fields = ('payment_id', 'owner_name', 'card_number', 'expired_month', 'expired_year', 'cvv', 'sms_code')

    def clean_card_number(self):
        data = self.cleaned_data["card_number"]
        card_number = ''.join([x for x in data if x.isdigit()])
        if len(card_number) != 16:
            raise ValidationError('Kart nömrəsindəki simvolların sayı səhvdir')  # Неверное количество символов в номере карты
        return card_number

    def clean_expired_month(self):
        data = self.cleaned_data["expired_month"]
        try:
            month = int(data)
            if 1 <= month <= 12:
                return data
            else:
                raise ValidationError('Qeyd etdiyiniz ay yalnışdır')  # Неверный месяц
        except Exception:
            raise ValidationError('Qeyd etdiyiniz ay yalnışdır')



class PaymentListConfirmForm(forms.ModelForm):

    class Meta:
        model = Payment
        fields = (
                  'confirmed_amount',
                  'confirmed_incoming',
        )


class PaymentForm(forms.ModelForm):
    # birpay_id = forms.IntegerField(required=False)
    comment = forms.CharField(widget=forms.Textarea, required=False)

    class Meta:
        model = Payment
        fields = ('confirmed_incoming', 'comment', 'status')
