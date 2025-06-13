from django.apps import AppConfig


class DepositConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'deposit'

    def ready(self):
        import deposit.models