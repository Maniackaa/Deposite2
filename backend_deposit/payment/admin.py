from django.contrib import admin

from payment.models import CreditCard, PayRequisite, Payment, Shop


class CreditCardAdmin(admin.ModelAdmin):
    pass


class PayRequisiteAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'card', 'is_active', 'pay_type',
    )
    list_editable = ('is_active',)



class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'shop', 'outer_order_id', 'amount', 'confirmed_amount', 'confirmed_time', 'pay_requisite',  'screenshot',
        'create_at', 'status', 'change_time', 'confirmed_time', 'confirmed_incoming'
    )

class ShopAdmin(admin.ModelAdmin):
    pass


admin.site.register(CreditCard, CreditCardAdmin)
admin.site.register(PayRequisite, PayRequisiteAdmin)
admin.site.register(Payment, PaymentAdmin)
admin.site.register(Shop, ShopAdmin)
