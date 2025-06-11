import base64
import json

import httpx
import requests
from PIL import Image
from io import BytesIO
from pathlib import Path
import os
import openai
from dotenv import load_dotenv

load_dotenv()


client = openai.OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
)


def encode_image(image_path):
    with Image.open(image_path) as img:
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")


def recognize_text(image_path):
    os.environ["HTTP_PROXY"] = "http://dZAYaxg6:pKSC24ap@91.220.90.245:62786"

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
                            "На выходе должен получится чистый JSON без лишних символов: символов json в резудьтате быть не должно"
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

    print(f"📄 {image_path.name}")
    print(response.choices[0].message.content)
    print(f"🔢 Входные токены: {input_tokens}")
    print(f"🔢 Выходные токены: {output_tokens}")
    print(f"💰 Общая стоимость: ~ ${cost_usd:.5f}")
    return response.choices[0].message.content


def send_image(image_path, server_url="http://45.14.247.139:9000/recognize/"):
    image_path = Path(image_path)
    with open(image_path, "rb") as f:
        files = {'file': (image_path.name, f, 'image/jpeg')}
        print("Отправляю файл на сервер...")
        response = requests.post(server_url, files=files, timeout=16)
    print(response.status_code)
    result = response.json().get('result')
    return result


if __name__ == "__main__":
    folder = Path(__file__).parent.resolve()
    print(folder)
    image_files = list(folder.glob("*.jpg")) + list(folder.glob("*.jpeg")) + list(folder.glob("*.png"))

    print(f"🧾 Найдено файлов: {len(image_files)}")

    for image_path in image_files:
        print(image_path)
        # try:
        #     result = recognize_text(image_path)
        #     print(f'result: {json.loads(result)}')
        #
        # except Exception as e:
        #     print(f"Ошибка при обработке {image_path.name}: {e}")
        # break
        result = send_image(image_path)
        print(result)

