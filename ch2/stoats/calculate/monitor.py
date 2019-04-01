
import datetime as dt
from logging import getLogger

from sqlalchemy.sql import func
from sqlalchemy.sql.functions import count

from . import MultiProcCalculator
from ..names import STEPS, REST_HR, HEART_RATE, DAILY_STEPS, BPM, STEPS_UNITS, summaries, SUM, AVG, CNT, MIN, MAX, MSR
from ..pipeline import LoaderMixin
from ...lib.date import local_date_to_time, time_to_local_date, to_date, format_date
from ...lib.log import log_current_exception
from ...squeal import MonitorJournal, StatisticJournalInteger, StatisticName, CompositeComponent, Composite, \
    StatisticJournal, Source
from ...squeal.utils import add

log = getLogger(__name__)
QUARTER_DAY = 6 * 60 * 60


class MonitorCalculator(LoaderMixin, MultiProcCalculator):

    # candidate for CompositeMixin

    def __init__(self, *args, cost_calc=1, cost_write=1, **kargs):
        super().__init__(*args, cost_calc=cost_calc, cost_write=cost_write, **kargs)

    def _missing(self, s):
        dates = set()
        for source in self._unused_sources(s):
            dates.update(self._dates_for_source(source))
        start, finish = self._start_finish(lambda x: to_date(x, none=True))
        missing = sorted(filter(lambda d: (start is None or start <= d) and (finish is None or d <= finish), dates))
        log.debug(f'Missing {start} - {finish}: {missing}')
        return missing

    def _args(self, missing, start, finish):
        s, f = format_date(missing[start]), format_date(missing[finish])
        log.info(f'Starting worker for {s} - {f}')
        return f'"{s}" "{f}"'

    def _dates_for_source(self, mjournal):
        start = time_to_local_date(mjournal.start)
        finish = time_to_local_date(mjournal.finish) + dt.timedelta(days=1)
        while start < finish:
            yield start
            start += dt.timedelta(days=1)

    def _unused_sources(self, s):
        Composite.clean(s)
        used_sources = s.query(CompositeComponent.input_source_id). \
            join(StatisticJournal, CompositeComponent.output_source_id == StatisticJournal.source_id). \
            join(StatisticName, StatisticJournal.statistic_name_id == StatisticName.id). \
            filter(StatisticName.owner == self.owner_out)
        return self._unused_sources_give_used(s, used_sources)

    def _unused_sources_give_used(self, s, used_sources):
        mjournals = s.query(MonitorJournal).filter(~MonitorJournal.id.in_(used_sources))
        start, finish = self._start_finish(local_date_to_time)
        if start:
            mjournals = mjournals.filter(MonitorJournal.finish >= start)
        if finish:
            mjournals = mjournals.filter(MonitorJournal.start <= finish)
        log.debug(f'Unused query: {mjournals}')
        return mjournals.all()

    def _delete(self, s):
        composite_ids = s.query(Composite.id). \
            join(StatisticJournal, Composite.id == StatisticJournal.source_id). \
            join(StatisticName, StatisticJournal.statistic_name_id == StatisticName.id). \
            filter(StatisticName.owner == self.owner_out)
        start, finish = self._start_finish()
        if start:
            composite_ids = composite_ids.filter(StatisticJournal.time >= start)
        if finish:
            composite_ids = composite_ids.filter(StatisticJournal.time <= finish)
        log.debug(f'Delete query: {composite_ids}')
        n = s.query(count(Source.id)). \
            filter(Source.id.in_(composite_ids)). \
            scalar()
        log.debug(n)
        if n:
            log.warning(f'Deleting {n} Composite sources ({start} - {finish})')
            s.query(Source). \
                filter(Source.id.in_(composite_ids)). \
                delete(synchronize_session=False)
            s.commit()

    def _run_one(self, s, start):
        start = local_date_to_time(start)
        finish = start + dt.timedelta(days=1)
        if s.query(count(Composite.id)). \
                join(StatisticJournal, Composite.id == StatisticJournal.source_id). \
                join(StatisticName, StatisticJournal.statistic_name_id == StatisticName.id). \
                filter(StatisticJournal.time >= start,
                       StatisticJournal.time <= finish,
                       StatisticName.owner == self.owner_out).scalar():
            raise Exception('Source already exists')
        try:
            input_source_ids, data = self._read_data(s, start, finish)
            output_source = add(s, Composite(n_components=len(input_source_ids)))
            for input_source_id in input_source_ids:
                s.add(CompositeComponent(input_source_id=input_source_id, output_source=output_source))
            s.commit()
            loader = self._get_loader(s, add_serial=False, clear_timestamp=False)
            self._calculate_results(s, output_source, data, loader, start, finish)
            loader.load()
            self._prev_loader = loader
        except Exception as e:
            log.warning(f'No statistics for {start} - {finish} due to error ({e})')
            log_current_exception()

    def _read_data(self, s, start, finish):
        midpt = start + dt.timedelta(hours=12)
        m0 = s.query(func.avg(func.abs(StatisticJournalInteger.time - midpt))).join(StatisticName). \
            filter(StatisticName.name == HEART_RATE,
                   StatisticName.owner == self.owner_in,
                   StatisticJournalInteger.time < finish,
                   StatisticJournalInteger.time >= start,
                   StatisticJournalInteger.value > 0).scalar()
        log.debug('M0: %s' % m0)
        if m0 and abs(m0 - QUARTER_DAY) < 0.25 * QUARTER_DAY:  # not evenly sampled
            all_hr = [row[0] for row in s.query(StatisticJournalInteger.value).join(StatisticName). \
                filter(StatisticName.name == HEART_RATE,
                       StatisticName.owner == self.owner_in,
                       StatisticJournalInteger.time < finish,
                       StatisticJournalInteger.time >= start,
                       StatisticJournalInteger.value > 0).all()]
            n = len(all_hr)
            rest_heart_rate = sorted(all_hr)[n // 10]  # 10th percentile
        else:
            log.info('Insufficient coverage for %s' % REST_HR)
            rest_heart_rate = None
        daily_steps = s.query(func.sum(StatisticJournalInteger.value)).join(StatisticName). \
            filter(StatisticName.name == STEPS,
                   StatisticName.owner == self.owner_in,
                   StatisticJournalInteger.time < finish,
                   StatisticJournalInteger.time >= start).scalar()
        input_source_ids = [row[0] for row in s.query(MonitorJournal.id).
            filter(MonitorJournal.start <= finish,
                   MonitorJournal.finish >= start).all()]
        return input_source_ids, (rest_heart_rate, daily_steps)

    def _calculate_results(self, s, source, data, loader, start, finish):
        rest_heart_rate, daily_steps = data
        if rest_heart_rate:
            loader.add(REST_HR, BPM, summaries(AVG, CNT, MIN, MSR), None, source, rest_heart_rate,
                       start, StatisticJournalInteger)
        loader.add(DAILY_STEPS, STEPS_UNITS, summaries(SUM, AVG, CNT, MAX, MSR), None, source, daily_steps,
                   start, StatisticJournalInteger)
