import logging
import re

import colorfield.fields
from colorfield.fields import ColorField
from django import forms
from django.contrib.admin import widgets
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Subquery
from django.forms import CheckboxInput

from ocr.models import ScreenResponse

logger = logging.getLogger(__name__)


class ScreenForm(forms.ModelForm):

    class Meta:
        model = ScreenResponse
        fields = '__all__'

