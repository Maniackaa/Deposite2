import asyncio
import datetime
import sys
from sqlite3 import Timestamp
from time import time

from sqlalchemy import create_engine, ForeignKey, Date, String, DateTime, \
    Float, UniqueConstraint, Integer, MetaData, BigInteger, Text
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import sessionmaker

from config_data.bot_conf import conf, tz

metadata = MetaData()
db_url = f"postgresql+psycopg2://{conf.db.db_user}:{conf.db.db_password}@{conf.db.db_host}:{conf.db.db_port}/{conf.db.database}"
engine = create_engine(db_url, echo=False)
# from sqlalchemy_utils.functions import database_exists, create_database

Session = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Incoming(Base):
    __tablename__ = 'deposit_incoming'
    # __table_args__ = (UniqueConstraint("response_date", "pay", "sender", name="unique_sms"),)
    id: Mapped[int] = mapped_column(primary_key=True,
                                    autoincrement=True,
                                    comment='Первичный ключ')
    register_date: Mapped[time] = mapped_column(DateTime(timezone=True), nullable=True, default=lambda: datetime.datetime.now(tz=tz))
    response_date: Mapped[time] = mapped_column(DateTime(timezone=True), nullable=True)
    recipient: Mapped[str] = mapped_column(String(50), nullable=True)
    sender: Mapped[str] = mapped_column(String(50), nullable=True)
    pay: Mapped[float] = mapped_column(Float())
    balance: Mapped[float] = mapped_column(Float(), nullable=True)
    transaction: Mapped[int] = mapped_column(Integer(), nullable=True, unique=True)
    type: Mapped[str] = mapped_column(String(20), default='unknown')
    birpay_confirm_time: Mapped[time] = mapped_column(DateTime(timezone=True), nullable=True)
    birpay_edit_time: Mapped[time] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_deposit_id: Mapped[int] = mapped_column(Integer())
    birpay_id: Mapped[str] = mapped_column(String(15), nullable=True)

    def __repr__(self):
        return f'{self.id}. {self.register_date}'


class TrashIncoming(Base):
    __tablename__ = 'deposit_trashincoming'
    id: Mapped[int] = mapped_column(primary_key=True,
                                    autoincrement=True,
                                    comment='Первичный ключ')
    register_date: Mapped[time] = mapped_column(DateTime(timezone=True), nullable=True,
                                                default=lambda: datetime.datetime.now(tz=tz))
    text: Mapped[str] = mapped_column(Text())


# if not database_exists(db_url):
#     create_database(db_url)

# Base.metadata.create_all(engine)
