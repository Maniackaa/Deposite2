#!/usr/bin/env python
"""
Скрипт для запуска тестов BirpayPanelView
"""
import os
import sys
import django
from django.conf import settings
from django.test.utils import get_runner

if __name__ == "__main__":
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend_deposit.settings')
    django.setup()
    
    TestRunner = get_runner(settings)
    test_runner = TestRunner()
    
    print("Запуск тестов для BirpayPanelView...")
    print("=" * 50)
    
    # Список тестов для запуска
    test_modules = [
        'deposit.tests.test_birpay_panel_sms_form',
        'deposit.tests.test_birpay_panel_integration',
    ]
    
    failures = test_runner.run_tests(test_modules, verbosity=2)
    
    if failures:
        print(f"\n❌ Тесты завершились с ошибками: {failures}")
        sys.exit(1)
    else:
        print("\n✅ Все тесты прошли успешно!")
        sys.exit(0)
