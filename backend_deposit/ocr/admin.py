from django.contrib import admin

from ocr.models import ScreenResponse


class ScreenResponseAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'image'
    )
    # list_display_links = ('id', 'created', 'title', )


admin.site.register(ScreenResponse, ScreenResponseAdmin)