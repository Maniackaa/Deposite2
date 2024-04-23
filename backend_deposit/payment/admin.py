from django.contrib import admin

from payment.models import CreditCard, PayRequisite, Payment, Shop, PhoneScript


class CreditCardAdmin(admin.ModelAdmin):
    pass


class PayRequisiteAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'card', 'is_active', 'pay_type',
    )
    list_editable = ('is_active',)


class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'shop', 'order_id', 'amount', 'confirmed_amount', 'confirmed_time', 'pay_requisite',  'screenshot',
        'create_at', 'status', 'change_time', 'confirmed_time', 'confirmed_incoming'
    )


class ShopAdmin(admin.ModelAdmin):
    pass


class PhoneScriptAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'step_2_required', 'step_2_x', 'step_2_y', 'step_3_x', 'step_3_y'
    )


admin.site.register(CreditCard, CreditCardAdmin)
admin.site.register(PayRequisite, PayRequisiteAdmin)
admin.site.register(Payment, PaymentAdmin)
admin.site.register(Shop, ShopAdmin)
admin.site.register(PhoneScript, PhoneScriptAdmin)
