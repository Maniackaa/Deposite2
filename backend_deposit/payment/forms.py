from django import forms

from payment.models import Payment


class InvoiceForm(forms.ModelForm):
    amount = forms.CharField(widget=forms.HiddenInput())
    outer_order_id = forms.CharField(widget=forms.HiddenInput())
    screenshot = forms.ImageField(widget=forms.ClearableFileInput(), required=True, label='Скриншот об оплате')

    class Meta:
        model = Payment
        fields = (
                  'outer_order_id',
                  'amount',
                  'phone',
                  'screenshot',
                  )


class PaymentListConfirmForm(forms.ModelForm):

    class Meta:
        model = Payment
        fields = (
                  'confirmed_amount',
                  'incoming'

                  )