from django.core.management.base import BaseCommand, CommandError

from payment.management.commands.bins import bins
from payment.models import PhoneScript


class Command(BaseCommand):
    help = 'Imports to the database'

    def handle(self, *args, **kwargs):
        try:
            objects = []
            print(bins)
            for name, data in bins.items():
                print(name)
                print(data)
                new_phone_script = PhoneScript(
                    name=name,
                    step_1=data['step_1'],
                    step_2_required=data.get('step_2_required', '1'),
                    step_2_x=data['step_2_x'],
                    step_2_y=data['step_2_y'],
                    step_3_x=data['step_3_x'],
                    step_3_y=data['step_3_y'],
                    bins=data['bins']
                )
                objects.append(new_phone_script)
            print(objects)
            PhoneScript.objects.bulk_create(objects)
        except Exception as error:
            raise CommandError(error)
        self.stdout.write(self.style.SUCCESS('Successfully imported data.'))