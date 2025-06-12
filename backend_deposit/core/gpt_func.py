import base64
import os
import re
from pathlib import Path

import requests
import structlog
from PIL import Image
from io import BytesIO
import openai
from django.conf import settings

from core.global_func import Timer

logger = structlog.get_logger('deposit')


def encode_image(image_path):
    with Image.open(image_path) as img:
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])  # Альфа как маска
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")


def extract_json(text):
    # Убираем обертку вида ```json ... ```
    text = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^```", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    return text.strip()


def gpt_recognize_check(image_path):
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    base64_image = encode_image(image_path)
    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    },
                    {
                        "type": "text",
                        "text": (
                            "Это банковский чек, полученный от клиента. "
                            "Распознай его и выведи информацию в виде json c ключами:\n"
                            "Сумма: amount\n"
                            "Статус: status (1 если транзакция успешна, -1 если не успешна. 0 Если не понятно\n"
                            "Дата и время: create_at\n"
                            "Карта отправителя: sender\n"
                            "Банк отправителя: bank\n"
                            "Карта получателя: recepient\n"
                            "Имя получателя: owner_name\n"
                            "Если на чеке есть печать банка — скорее всего операция успешна. "
                            "На чеках Unibank, если карта получателя указана в верхнем тексте перед основной таблицей, это всё равно получатель."
                            "Если чек из банковского приложения или интерфейса, и нет явных признаков неуспеха (например, красной надписи об ошибке или отказе), считать транзакцию успешной"
                            "На выходе должен получится чистый JSON без лишних символов: символов json в резудьтате быть не должно. amount брать модуль числа"
                        )
                    }
                ]
            }
        ],
        max_tokens=1000
    )
    usage = response.usage
    input_tokens = usage.prompt_tokens
    output_tokens = usage.completion_tokens
    total_tokens = usage.total_tokens
    cost_usd = total_tokens * 0.000005

    logger.info(f"📄 {image_path.name}")
    print(response.choices[0].message.content)
    print(f"🔢 Входные токены: {input_tokens}")
    print(f"🔢 Выходные токены: {output_tokens}")
    print(f"💰 Общая стоимость: ~ ${cost_usd:.5f}")
    return response.choices[0].message.content


def send_image_to_gpt(image_field, server_url="http://45.14.247.139:9000/recognize/"):  # Резерв Payment
    with image_field.open('rb') as f:
        files = {'file': (image_field.name, f, 'image/jpeg')}
        with Timer("Отправляю файл на сервер..."):
            response = requests.post(server_url, files=files, timeout=10)
    result = response.json().get('result')
    return result
