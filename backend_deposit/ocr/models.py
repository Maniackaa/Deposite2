from django.db import models

phones = [('jjeyzlhiz9ljeiso', 'Phone 1 ["jjeyzlhiz9ljeiso"]'), ('unknown', 'unknown')]


class ScreenResponse(models.Model):
    register_date = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    name = models.CharField(unique=True)
    image = models.ImageField(upload_to='responsed_screen', verbose_name='Распознанный скрин')
    # source = models.CharField(default='unknown')
    source = models.CharField(choices=phones, default='unknown')
    sample_response_date = models.DateTimeField('Распознанное время', null=True, blank=True)
    sample_recipient = models.CharField('Получатель', max_length=50, null=True, blank=True)
    sample_sender = models.CharField('Отравитель/карта', max_length=50, null=True, blank=True)
    sample_pay = models.FloatField('Платеж', null=True, blank=True)
    # sample_balance = models.FloatField('Баланс', null=True, blank=True)
    sample_transaction = models.IntegerField('Транзакция', null=True, blank=True)

    def __str__(self):
        return f'{self.id}. {self.image}'


class ScreenResponsePart(models.Model):
    screen = models.ForeignKey(ScreenResponse, related_name='parts', on_delete=models.CASCADE)
    black = models.IntegerField()
    white = models.IntegerField()
    response_date = models.DateTimeField('Распознанное время', null=True, blank=True)
    recipient = models.CharField('Получатель', max_length=50, null=True, blank=True)
    sender = models.CharField('Отравитель/карта', max_length=50, null=True, blank=True)
    pay = models.FloatField('Платеж', null=True, blank=True)
    # balance = models.FloatField('Баланс', null=True, blank=True)
    transaction = models.IntegerField('Транзакция', null=True, blank=True)

    def __str__(self):
        return f'{self.id}. ({self.black} | {self.white}) {self.response_date} | {self.recipient} | {self.sender} | {self.pay} | {self.transaction}'

    class Meta:
        ordering = ('black', 'white')
