
from sqlalchemy import Column, Integer, Text, ForeignKey, Boolean

from .types import Ordinal
from .support import Base


class ScheduleType(Base):

    __tablename__ = 'schedule_type'

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, default='')
    sort = Column(Text, nullable=False, default='')


class Schedule(Base):

    __tablename__ = 'schedule'

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey('schedule.id'), nullable=True)
    type_id = Column(Integer, ForeignKey('schedule_type.id'))
    repeat = Column(Text, nullable=False, default='')
    start = Column(Ordinal)
    finish = Column(Ordinal)
    title = Column(Text, nullable=False, default='')
    description = Column(Text, nullable=False, default='')
    has_notes = Column(Boolean, nullable=False, default=False)
    sort = Column(Text, nullable=False, default='')


class ScheduleDiary(Base):

    __tablename__ = 'schedule_diary'

    date = Column(Ordinal, primary_key=True)
    schedule_id = Column(Integer, ForeignKey('schedule.id'), primary_key=True)
    notes = Column(Text, nullable=False, default='')
