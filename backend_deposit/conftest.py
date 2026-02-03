import os
import django
from django.conf import settings

# Настройка Django для pytest
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend_deposit.settings')
# Отключаем DEBUG до setup, чтобы debug_toolbar не ломал тесты (StaticFilesPanel → 400)
os.environ['DEBUG'] = 'False'
django.setup()

