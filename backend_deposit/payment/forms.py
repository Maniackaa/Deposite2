from django import forms

from payment.models import Payment


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
        fields = ('confirmed_incoming', 'comment')
