import io

from PIL.Image import Image
from django.core.files.images import ImageFile
from django.core.management.base import BaseCommand, CommandError

from backend_deposit.settings import BASE_DIR
from payment.management.commands.bins import bins, banks
from payment.models import PhoneScript, Bank


class Command(BaseCommand):
    help = 'Imports to the database'

    def handle(self, *args, **kwargs):
        for name, data in bins.items():
            print(name)
            try:
                new_phone_script, status = PhoneScript.objects.get_or_create(
                    name=name,
                    step_1=data['step_1'],
                    step_2_required=data.get('step_2_required', '1'),
                    step_2_x=data['step_2_x'],
                    step_2_y=data['step_2_y'],
                    step_3_x=data['step_3_x'],
                    step_3_y=data['step_3_y'],
                )
                print(status, new_phone_script)
            except Exception as error:
                raise error

        for bank_name, data_bank in banks.items():
            print(bank_name)
            try:
                phone_script = PhoneScript.objects.get(name=data_bank['script'])
                print(phone_script)
                image = BASE_DIR / 'payment' / 'management' / 'commands' / 'data' / 'bank_icons' / f'{bank_name}.jpg'
                with open(image, 'rb') as file:
                    bank = Bank.objects.filter(name=bank_name).first()
                    if not bank:
                        new_bank = Bank.objects.create(
                            name=bank_name,
                            bins=data_bank['bins'],
                            script=phone_script,
                            image=ImageFile(io.BytesIO(file.read()), name=f'{image.name}'))
                        print(new_bank)
            except Exception as error:
                raise CommandError(error)


        self.stdout.write(self.style.SUCCESS('Successfully imported data.'))