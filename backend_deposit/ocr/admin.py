from django.contrib import admin

from ocr.models import ScreenResponse, ScreenResponsePart


class ScreenResponseAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'image'
    )
    # list_display_links = ('id', 'created', 'title', )


class ScreenResponsePartAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'black', 'white', 'recipient', 'sender', 'transaction'
    )
    list_filter = ('screen', 'black')
    list_per_page = 1000


admin.site.register(ScreenResponse, ScreenResponseAdmin)
admin.site.register(ScreenResponsePart, ScreenResponsePartAdmin)