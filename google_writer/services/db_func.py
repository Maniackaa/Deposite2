import asyncio
import time
import datetime

from sqlalchemy import select, func, Date, Time, and_
from sqlalchemy.exc import IntegrityError

from config_data.bot_conf import get_my_loggers, tz
from database.db import Session, Incoming, TrashIncoming

logger, err_log, logger1, logger2 = get_my_loggers()


def check_transaction(transaction_num) -> bool:
    """Возвразает True если транзакция есть в базе"""
    logger.debug(f'Проверка наличия транзакции {transaction_num}')
    try:
        session = Session()
        with session:
            result = session.execute(select(Incoming).where(Incoming.transaction == transaction_num)).scalars().all()
            if result:
                logger.debug(f'Транзакция {transaction_num} найдена')
                return True
        return False
    except Exception as err:
        err_log.error('Ошибка при проверке транзакции')
        raise err


def add_pay_to_db(pay: dict):
    logger.debug(f'Добавление в базу {pay}')
    try:
        session = Session()
        with session:
            response_date = pay.get('response_date')
            sender = pay.get('sender')
            pay_sum = pay.get('pay')
            old_incomings = session.execute(select(Incoming).where(Incoming.response_date == response_date,
                                                                   Incoming.sender == sender,
                                                                   Incoming.pay == pay_sum)).all()
            if old_incomings:
                logger.warning(f'Транзакция уже есть')
                return 'duplicate'
            incoming = Incoming(**pay)
            session.add(incoming)
            session.commit()
            return True
    except IntegrityError as err:
        logger.error(err)
        logger.warning(f'Транзакция уже есть')

    except Exception as err:
        logger.debug(f'Ошибка при добавлении в базу', exc_info=True)
        raise err


def add_to_trash(text: str):
    logger.debug(f'Добавление в нераспознанное {text}')
    try:
        session = Session()
        with session:
            trash = TrashIncoming(text=text)
            session.add(trash)
            session.commit()
            return True
    except Exception as err:
        logger.debug(f'Ошибка при добавлении в базу', exc_info=True)
        raise err


def read_new_incomings(last_num=0, sms_types: list[str] = []):
    logger.debug(f'Читаем базу где id > {last_num}')
    try:
        session = Session()
        with session:
            incomings = select(Incoming).where(Incoming.id > last_num).order_by('id')
            res = session.scalars(incomings).all()
            logger.debug(f'Результат {res}')
            return res
    except Exception as err:
        logger.debug(f'Ошибка при чтении базы', exc_info=True)


def read_new_trashincomings(last_num=0):
    try:
        session = Session()
        with session:
            incomings = select(TrashIncoming).where(TrashIncoming.id > last_num).order_by('id')
            res = session.scalars(incomings).all()
            logger.debug(f'Результат {res}')
            return res
    except Exception as err:
        logger.debug(f'Ошибка при чтении базы', exc_info=True)


def find_new_out(last_num=0):
    # Находит новые выводы
    try:
        session = Session()
        with session:
            incomings = select(Incoming).where(Incoming.id > last_num).filter(
                Incoming.sender.regexp_match(r'\d\d\d \d\d \d\d\d \d\d \d\d')).where(
                Incoming.pay > 0
            ).order_by('id')
            results = session.scalars(incomings).all()
            logger2.debug(f'Результат {results}')
            return results

    except Exception as err:
        logger2.debug(f'Ошибка при чтении базы', exc_info=True)
        raise err


def get_day_report_rows():
    logger.debug(f'Считаем сменные отчеты')

    def bad_ids():
        """
        Функция аходит id которые не надо учитывать:
        m10 с отправителем картой
        m10 sender с полным номером телефона кроме 00 000 00 00
        :return:
        """
        session = Session()
        with session:
            # m10 с отправителем картой
            finded_sender_as_card = select(Incoming).where(Incoming.type.in_(['m10', 'm10_short'])).filter(
                    Incoming.sender.regexp_match(r'\d\d\d\d \d\d.*\d\d\d\d')
            ).distinct()
            finded_sender_as_card = session.execute(finded_sender_as_card).scalars().all()
            # print(finded_sender_as_card)

            # m10 sender с полным номером телефона кроме 00 000 00 00
            finded_sender_full_phone = select(Incoming).where(Incoming.type.in_(['m10', 'm10_short'])).filter(
                Incoming.sender.regexp_match(r'\d\d\d \d\d \d\d\d \d\d \d\d')).filter(~
                Incoming.sender.regexp_match(r'00 000 00 00')).distinct()
            finded_sender_full_phone = session.execute(finded_sender_full_phone).scalars().all()
            # print(finded_sender_full_phone)

            finded_total_bad_sender = set()
            finded_total_bad_ids = set()

            total_bad_incomings = finded_sender_as_card + finded_sender_full_phone

            for bad in total_bad_incomings:
                finded_total_bad_sender.add(bad.sender)
                finded_total_bad_ids.add(bad.id)
            logger1.info(f'Не учитываются sender: {len(finded_total_bad_sender)} шт: {finded_total_bad_sender}')
            logger1.info(f'Ид которые не учитываются: {len(finded_total_bad_ids)} шт: {finded_total_bad_ids}')
        return [finded_total_bad_ids, finded_total_bad_sender]

    def period_report(start='00:00', end='23:59:59.999999', sended_bad_ids=[]):
        """
        Находит сумму и количество pay за период времени
        :param start: '00:00'
        :param end: '23:59:59.999999'
        :sended_bad_ids: id которые не учитываются
        :return: [(datetime.date(2023, 8, 26), 500.0, 1)]
        """

        # Найдем кроме тех что выше:
        step = select(func.cast(Incoming.register_date, Date),
                      func.sum(Incoming.pay),
                      func.count(Incoming.pay)).where(
            and_(func.cast(Incoming.register_date, Time) >= start, func.cast(Incoming.register_date, Time) <= end)
        ).where(Incoming.pay > 0).where(Incoming.id.not_in(sended_bad_ids)).group_by(
            func.cast(Incoming.register_date, Date)
        )
        return step

    try:
        session = Session()
        not_calc_ids, bad_senders = bad_ids()

        with session:
            step1 = period_report('00:00', '07:59:59.999999', not_calc_ids)
            results1 = session.execute(step1).all()

            step2 = period_report('08:00', '15:59:59.999999', not_calc_ids)
            results2 = session.execute(step2).all()

            step3 = period_report('16:00', '23:59:59.999999', not_calc_ids)
            results3 = session.execute(step3).all()

            all_steps = period_report(sended_bad_ids=not_calc_ids)
            results_all = session.execute(all_steps).all()

            dates = select(func.cast(Incoming.register_date, Date)).order_by(func.cast(Incoming.register_date, Date).desc()).distinct()
            dates_result = session.execute(dates).scalars().all()

            str_not_calc_ids = [str(x) for x in sorted(not_calc_ids)]
            str_not_calc_ids = ', '.join(str_not_calc_ids)

            rows = [

                list(bad_senders),
                ['ID, которые пропускаются:'],
                [str_not_calc_ids],
                ['-'],
                [datetime.datetime.now(tz=tz).strftime('%d.%m.%Y  %H:%M:%S'),	'За день', 'Смена 1', 'Смена 2', 'Смена 3']
            ]
            for date in dates_result:
                print(date)
                row = [date.strftime('%d.%m.%Y'), '0 - 0', '0 - 0', '0 - 0', '0 - 0']
                for num, step in enumerate([results_all, results1, results2, results3], 1):
                    print('---', step)
                    for step_day in step:
                        if step_day[0] == date:
                            day_text = f'{round(step_day[1], 2)} - {step_day[2]}'
                            row[num] = day_text
                rows.append(row)

            logger1.debug(f'Сменные отчеты: {rows}')
            print(bad_senders)
            return rows

    except Exception as err:
        err_log.debug(f'Ошибка при чтении отчетов', exc_info=True)


def get_out_report_rows():
    logger.debug(f'Считаем сменные отчеты выводов')
    try:
        session = Session()

        def out_period_report(start='00:00', end='23:59:59.999999'):
            """
            Находит сумму и количество pay выводов за период времени
            :param start: '00:00'
            :param end: '23:59:59.999999'
            :return: [(datetime.date(2023, 8, 26), 500.0, 1)]
            """
            step = select(func.cast(Incoming.register_date, Date),
                          func.sum(Incoming.pay),
                          func.count(Incoming.pay)).where(
                Incoming.sender.regexp_match(r'\d\d\d \d\d \d\d\d \d\d \d\d')).where(
                and_(
                    func.cast(Incoming.register_date, Time) >= start,
                    func.cast(Incoming.register_date, Time) <= end)).where(
                Incoming.pay > 0).group_by(func.cast(Incoming.register_date, Date))
            return step

        with session:
            step1 = out_period_report('00:00', '07:59:59.999999')
            results1 = session.execute(step1).all()

            step2 = out_period_report('08:00', '15:59:59.999999')
            results2 = session.execute(step2).all()

            step3 = out_period_report('16:00', '23:59:59.999999')
            results3 = session.execute(step3).all()

            all_steps = out_period_report()
            results_all = session.execute(all_steps).all()

            dates = select(func.cast(Incoming.register_date, Date)).order_by(func.cast(Incoming.register_date, Date)).distinct()
            dates_result = session.execute(dates).scalars().all()

            rows = []
            for date in dates_result:
                # print(date)
                row = [date.strftime('%d.%m.%Y'), '0 - 0', '0 - 0', '0 - 0', '0 - 0']
                for num, step in enumerate([results_all, results1, results2, results3], 1):
                    # print('---', step)
                    for step_day in step:
                        if step_day[0] == date:
                            day_text = f'{round(step_day[1], 2)} - {step_day[2]}'
                            row[num] = day_text
                rows.append(row)

            logger1.debug(f'Сменные отчеты выводов: {rows}')
            return rows

    except Exception as err:
        err_log.debug(f'Ошибка при чтении отчетов выводов', exc_info=True)


def read_all_out():
    """Тестовое чтение всех выводов (где полный sender)"""
    try:
        logger.debug('read_all_out')
        session = Session()
        all_out = select(Incoming).filter(
            Incoming.sender.regexp_match(r'\d\d\d \d\d \d\d\d \d\d \d\d'))
        res = session.execute(all_out).scalars().all()
        print('Все выводы:', res)
        for row in res:
            print(row.id, row.sender)
        return res
    except Exception as err:
        print(err)


def get_card_volume_rows(select_cards: list):  # ['4127*4297', '+994 51 927 05 68', '4127*6822']
    """Находит select_cards отправителей платежей и суммирует объем и количество"""

    try:
        logger.debug(f'get_card_volume_rows. Карты: {select_cards}')
        session = Session()
        cards = select(Incoming.recipient, func.sum(Incoming.pay), func.count(Incoming.pay), func.max(Incoming.response_date)).where(
            Incoming.pay > 0).where(
            Incoming.recipient.in_(select_cards)
        ).group_by(Incoming.recipient)
        results = session.execute(cards).fetchall()
        # [('4127*4297', 7926.64, 319), ('+994 51 927 05 68', 151542.00999999995, 1809), ('4127*6822', 41097.64, 1420)]
        logger.debug(f'card_volume: {results}')
        rows = []
        for index_card, card in enumerate(select_cards, 1):
            row = [index_card, card, 0, 0, '-']
            for result in results:
                # ('+994 51 927 05 68', 197.0, 11)
                if result[0] == card:
                    row[1] = result[0]
                    row[2] = result[1]
                    row[3] = result[2]
                    row[4] = result[3].strftime('%Y.%m.%d %H:%M')

            rows.append(row)
        return rows
    except Exception as err:
        logger1.error('Ошибка при подсчете баланса карт', exc_info=True)


if __name__ == '__main__':
    pay = {'response_date': datetime.datetime(2023, 8, 25, 1, 7), 'sender': '+994 70 *** ** 27', 'bank': None, 'pay': 5.0, 'balance': None, 'transaction': 55555150, 'type': 'm10'}
    # print(get_day_report_rows())
    # print(get_out_report_rows())
    # find_new_out()
    # get_card_volume_rows(['+994 70 *** ** 27'])

    # session = Session()
    # #4127 20 ** ** ** 5502
    # with session:
    #     # m10 с отправителем картой
    #     sender_as_card = select(Incoming).where(Incoming.type.in_(['m10', 'm10_short'])).filter(
    #             Incoming.sender.regexp_match(r'\d\d\d\d \d\d.*\d\d\d\d')
    #     ).distinct()
    #     sender_as_card = session.execute(sender_as_card).scalars().all()
    #     # print(sender_as_card)
    #
    #     # m10 sender с полным номером телефона кроме 00 000 00 00
    #     sender_full_phone = select(Incoming).where(Incoming.type.in_(['m10', 'm10_short'])).filter(
    #         Incoming.sender.regexp_match(r'\d\d\d \d\d \d\d\d \d\d \d\d')).filter(~
    #         Incoming.sender.regexp_match(r'00 000 00 00')).distinct()
    #     sender_full_phone = session.execute(sender_full_phone).scalars().all()
    #     # print(sender_full_phone)
    #
    #     total_bad_sender = set()
    #     total_bad_ids = set()
    #     total_bad = sender_as_card + sender_full_phone
    #     # print(total_bad)
    #     for bad in total_bad:
    #         total_bad_sender.add(bad.sender)
    #         total_bad_ids.add(bad.id)
    #     logger1.info(f'Не учитываются sender: {len(total_bad_sender)} шт: {total_bad_sender}')
    #     logger1.info(f'Ид которые не учитываются: {len(total_bad_ids)} шт: {total_bad_ids}')





