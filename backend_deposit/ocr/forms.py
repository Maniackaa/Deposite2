import logging
import re

import colorfield.fields
import structlog
from colorfield.fields import ColorField
from django import forms
from django.contrib.admin import widgets
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Subquery
from django.forms import CheckboxInput

from ocr.models import ScreenResponse

logger = structlog.get_logger('deposit')

phones = [('jjeyzlhiz9ljeiso', 'Phone 1 ["jjeyzlhiz9ljeiso"]'), ('unknown', 'unknown')]

class ScreenForm(forms.ModelForm):
    source = forms.ChoiceField(choices=phones, required=True)

    class Meta:
        model = ScreenResponse
        fields = '__all__'
        # fields = ('source', )


class ScreenDeviceSelectFrom(forms.Form):
    devices = forms.MultipleChoiceField(choices=phones, widget=forms.CheckboxSelectMultiple, required=False)
