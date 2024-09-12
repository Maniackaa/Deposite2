import asyncio
import datetime

import requests
import uvicorn
from adbutils import AdbClient, AdbDevice
from fastapi import FastAPI
from starlette.responses import HTMLResponse

from config_data.conf import get_my_loggers

app = FastAPI()
logger, err_log = get_my_loggers()
HOST = 'https://asu-payme.com'

press_home = 'input keyevent 3'
press_tab = 'input keyevent 61'


def get_device():
    try:
        adb_client = AdbClient(host="127.0.0.1", port=5037, socket_timeout=1)
        adb_devices = adb_client.device_list()
        logger.info(f'Подключены устройства: {adb_devices}')
        if adb_devices:
            adb_device = adb_devices[-1]
            return adb_device
    except Exception as err:
        raise err


async def insert_card_data(adb_device: AdbDevice, data: dict):
    owner_name = data['owner_name']
    amount = data['amount']
    card_number = data['card_number']
    expired_month = data['expired_month']
    expired_year = data['expired_year']
    cvv = data['cvv']
    logger.debug(f'Ввожу данные карты {data}')

    await asyncio.sleep(3)
    adb_device.shell(f'input tap 280 751')
    await asyncio.sleep(1)
    adb_device.shell(f'input text {owner_name}')
    await asyncio.sleep(1)
    adb_device.shell(f'input tap 550 1360')
    await asyncio.sleep(10)
    adb_device.shell(f'input tap 77 1380')
    await asyncio.sleep(0.5)
    adb_device.shell(f'input tap 330 590')
    adb_device.shell(press_tab)
    await asyncio.sleep(1)
    adb_device.shell(f'input text {card_number}')
    adb_device.shell(press_tab)
    await asyncio.sleep(1)
    adb_device.shell(f'input text {expired_month}{expired_year}')
    adb_device.shell(press_tab)
    await asyncio.sleep(1)
    adb_device.shell(f'input text {cvv}')
    await asyncio.sleep(0.5)
    adb_device.shell(f'input tap 550 1477')
    logger.debug('Данные введены')


async def insert_sms_code(adb_device: AdbDevice, sms_code):
    logger.debug('Ввожу смс-код')
    # adb_device.shell(f'input tap 150 927')
    await asyncio.sleep(1)
    # adb_device.shell(f'input tap 150 927')
    await asyncio.sleep(1)
    # adb_device.shell(f'input tap 150 927')
    await asyncio.sleep(1)
    # adb_device.shell(f'input tap 150 927')
    await asyncio.sleep(1)
    adb_device.shell(f'input text {sms_code}')
    await asyncio.sleep(1)
    # adb_device.shell(f'input keyevent KEYCODE_ENTER')
    adb_device.shell(f'input tap 550 2050')
    logger.debug('Смс введен')


async def get_status(payment_id):
    logger.debug(f'Проверка статуса: {payment_id}')
    response = requests.get(url=f'{HOST}/api/payment_status/',
                            data={
                                'id': f'{payment_id}'}
                            )
    status = response.json().get('status')
    logger.debug(f'status {payment_id}: {status}')
    await asyncio.sleep(0.1)
    return status


async def job(device, data: dict):
    """Работа телефона.
    1. Ввод данных карты и ожидание sms
    """
    logger.debug(f'Старт job: {device.serial}, {data}')
    payment_id = data['payment_id']
    logger.info(F'Телефон {device.serial} start job {data}')
    status = await get_status(payment_id)
    if status != 3:
        logger.debug(f'Некорректный статус: {status}')
        raise AttributeError

    # # Меняем статус на 4 Отправлено боту
    response = requests.patch(url=f'{HOST}/api/payment_status/',
                              data={'id': payment_id, 'status': 4})
    # Ввод данных карты
    await insert_card_data(device, data=data)
    # Меняем статус на 5 Ожидание смс
    response = requests.patch(url=f'{HOST}/api/payment_status/',
                              data={'id': payment_id, 'status': 5})
    logger.debug('Статус 5 Ожидание смс')
    await asyncio.sleep(10)
    sms = ''
    start_time = datetime.datetime.now()
    while not sms:
        total_time = datetime.datetime.now() - start_time
        if total_time > datetime.timedelta(minutes=3):
            logger.debug('Ожидание вышло')
            return False
        response = requests.get(
            url=f'{HOST}/api/payment_status/',
            data={'id': payment_id})
        logger.debug(response.status_code)
        logger.debug(response.text)
        response_data = response.json()
        sms = response_data.get('sms')
        logger.debug('Ожидание sms_code')
        await asyncio.sleep(3)
    logger.info(f'Получен код смс: {sms}')
    await insert_sms_code(device, sms)
    # Меняем статус на 6 Бот отработал
    response = requests.patch(url=f'{HOST}/api/payment_status/',
                              data={'id': payment_id, 'status': 6})
    logger.debug('Статус 6 Бот отработал. Конец')


@app.get("/")
async def root(payment_id: str,
               amount: str,
               owner_name: str,
               card_number: str,
               expired_month: int,
               expired_year: int,
               cvv: int,
               sms_code: str | None = None):
    try:
        logger.debug(f'payment_id: {payment_id}')
        device = get_device()
        if device:
            logger.info(f'Выбран телефон {device}')
            data = {
                'payment_id': payment_id,
                'owner_name': owner_name,
                'amount': amount,
                'device': device,
                'card_number': card_number,
                'expired_month': f'{expired_month:02d}',
                'expired_year': expired_year,
                'cvv': cvv,
            }
            # Запускаем телефон
            asyncio.create_task(job(device, data))

            script = f"""<script>           
function getData() {{
            var xhr = new XMLHttpRequest();
            xhr.open("POST", "{HOST}/api/payment_status/", true);
            xhr.setRequestHeader("Content-Type", "application/json");

            xhr.onload = function () {{
                if (xhr.status >= 200 && xhr.status < 300) {{
                    var jsonResponse = JSON.parse(xhr.responseText);

                    // Вывод результата "status" в тег с id="status"
                    document.getElementById("status").innerText = jsonResponse.status;

                    // Вывод результата "sms" в тег с id="sms"
                    document.getElementById("sms").innerText = jsonResponse.sms;
                }} else {{
                    console.error(xhr.statusText);
                }}
            }};

            xhr.onerror = function () {{
                console.error("Request failed");
            }};

            xhr.send(JSON.stringify({{ id: "{payment_id}" }}));
        }}

        // Вызов функции getData каждую секунду
        getData(); // вызов для первоначального получения данных
        setInterval(getData, 3000); // вызов каждую секунду
           </script>"""

            data = """
            <h3>Платеж {payment_id}</h3>
            <h4>Телефон: {device}</h4>
            {card_number}<br>
            {expired_month}/{expired_year}<br>

            <div>
              <p>SMS: <span id="sms"></span></p>
              <p>Status: <span id="status"></span></p>
            </div>
            {script}
            """
            return HTMLResponse(content=data.format(payment_id=payment_id, device=device, card_number=card_number,
                                                    expired_month=expired_month, expired_year=expired_year, sms='sms',
                                                    script=script))
        else:
            return "Телефон не найден"

    except KeyboardInterrupt:
        logger.info('Stoped')
    except Exception as err:
        logger.error(err)
        raise err


if __name__ == "__main__":
    logger.debug('start app')
    uvicorn.run(app, host="0.0.0.0", port=3000)