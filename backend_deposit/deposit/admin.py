from django.contrib import admin
from django.contrib.admin import DateFieldListFilter
from rangefilter.filters import DateRangeFilterBuilder, DateRangeQuickSelectListFilterBuilder, \
    NumericRangeFilterBuilder, DateTimeRangeFilterBuilder

from deposit.models import Incoming, BadScreen, Deposit, ColorBank, TrashIncoming, IncomingChange, CreditCard, Message, \
    MessageRead


class TrashIncomingAdmin(admin.ModelAdmin):
    list_display = ('id', 'register_date', 'text')


class IncomingAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'register_date', 'response_date', 'recipient', 'sender', 'pay', 'transaction', 'confirmed_deposit', 'type', 'image', 'phone_serial', 'worker'
    )
    list_filter = ('register_date', 'response_date',
                   ("register_date", DateRangeFilterBuilder()),
                   ("response_date", DateRangeQuickSelectListFilterBuilder()),
                   ("response_date", DateTimeRangeFilterBuilder()),
                   'worker', 'type')
    list_per_page = 1000


class BadScreenAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'incoming_time', 'transaction', 'type', 'image', 'worker', 'size'
    )


class DepositAdmin(admin.ModelAdmin):
    list_display = ('id', 'register_time', 'change_time', 'uid', 'phone', 'pay_sum', 'balance', 'input_transaction', 'status', 'pay_screen', 'confirmed_incoming')
    list_filter = ('register_time', 'status')
    list_editable = ('status',)
    # radio_fields = {'status': admin.VERTICAL}


class ColorBankAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'color_back', 'color_font', 'example'
    )
    list_display_links = ('id', 'name')


class IncomingChangeAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'time', 'incoming', 'user', 'val_name', 'new_val'
    )
    list_display_links = ('id', 'time')
    list_filter = ('user',)
    readonly_fields = ('id', 'time', 'incoming', 'user', 'val_name', 'new_val')


class CreditCardAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'number', 'expire', 'cvv', 'status'
    )
    list_display_links = ('id', 'name', 'number')


class MessageAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'author', 'title', 'created'
    )
    list_display_links = ('id', 'created', 'title', )


admin.site.register(Incoming, IncomingAdmin)
admin.site.register(TrashIncoming, TrashIncomingAdmin)
admin.site.register(BadScreen, BadScreenAdmin)
admin.site.register(Deposit, DepositAdmin)
admin.site.register(ColorBank, ColorBankAdmin)
admin.site.register(IncomingChange, IncomingChangeAdmin)
admin.site.register(CreditCard, CreditCardAdmin)
admin.site.register(Message, MessageAdmin)
admin.site.register(MessageRead)
