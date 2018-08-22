from collections import namedtuple
from itertools import chain

import pytest

from plenum.server.monitor import Monitor


class ReqStream:
    Period = namedtuple('Period', ['start', 'interval', 'quantity'])
    Once = namedtuple('Once', ['time', 'quantity'])
    Stop = namedtuple('Stop', ['time'])

    def __init__(self):
        self._steps = []

    def period(self, s, i, q):
        self._steps.append(ReqStream.Period(start=s, interval=i, quantity=q))
        return self

    def once(self, t, q):
        self._steps.append(ReqStream.Once(time=t, quantity=q))
        return self

    def stop(self, t):
        self._steps.append(ReqStream.Stop(time=t))
        return self

    def build(self):
        sections = []
        for i in range(len(self._steps)):
            if isinstance(self._steps[i], ReqStream.Period):
                sections.append(
                    self._translate_period(self._steps[i],
                                           next_step=self._steps[i + 1]))
            elif isinstance(self._steps[i], ReqStream.Once):
                sections.append(self._translate_once(self._steps[i]))
            elif isinstance(self._steps[i], ReqStream.Stop):
                if not isinstance(self._steps[i - 1], ReqStream.Period):
                    raise RuntimeError('ReqStream Stop step is not'
                                       ' after Period step')
            else:
                raise RuntimeError('ReqStream step type is unsupported')
        return chain(*sections)

    @staticmethod
    def _translate_period(period, next_step):
        if isinstance(next_step, ReqStream.Period):
            end = next_step.start
        elif isinstance(next_step, ReqStream.Once) \
                or isinstance(next_step, ReqStream.Stop):
            end = next_step.time
        else:
            raise RuntimeError('ReqStream step type is unsupported')
        return ((ts, period.quantity)
                for ts in range(period.start, end, period.interval))

    @staticmethod
    def _translate_once(once):
        return [(once.time, once.quantity)]


def create_throughput_measurement(start_ts, config):
    return Monitor.create_throughput_measurement(config, start_ts)


def get_average_throughput(calculated_throughputs, config):
    return config.throughput_averaging_strategy_class.get_avg(calculated_throughputs)


def get_througput_ratio(inst_req_streams, config):
    # print('DELTA = {}'.format(tconf.DELTA))
    # print('throughput_measurement_class = {}'
    #       .format(tconf.throughput_measurement_class))
    # print('throughput_measurement_params = {}'
    #       .format(tconf.throughput_measurement_params))
    # print('Max3PCBatchSize = {}'.format(tconf.Max3PCBatchSize))
    # print('Max3PCBatchWait = {}'.format(tconf.Max3PCBatchWait))

    assert len(inst_req_streams) > 1

    inst_tms = []
    max_end_ts = 0
    for req_stream in inst_req_streams:
        tm = create_throughput_measurement(start_ts=0, config=config)
        ts = 0

        for ts, reqs_num in req_stream:
            for req in range(reqs_num):
                tm.add_request(ts)

        if ts > max_end_ts:
            max_end_ts = ts

        inst_tms.append(tm)

    inst_throughput = []
    # Calculate throughput after the latest request ordering plus
    # the window size to take into account all the requests in calculation
    for tm in inst_tms:
        inst_throughput.append(
            tm.get_throughput(max_end_ts + 15))

    master_throughput = inst_throughput[0]

    backups_throughputs = inst_throughput[1:]
    calculated_backups_throughputs = \
        [t for t in backups_throughputs if t is not None]
    average_backup_throughput = \
        get_average_throughput(calculated_backups_throughputs, config) \
        if calculated_backups_throughputs \
        else None

    throughput_ratio = master_throughput / average_backup_throughput \
        if master_throughput is not None and average_backup_throughput is not None \
        else None

    return throughput_ratio


def assert_master_degraded(throughput_ratio, tconf):
    assert throughput_ratio < tconf.DELTA


def assert_master_not_degraded(throughput_ratio, tconf):
    assert throughput_ratio is None or throughput_ratio >= tconf.DELTA


def test_master_not_degraded_if_same_throughput(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=5, q=1)
                                   .stop(t=4 * 60)
                                   .build()
                        for inst_id in range(9)]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_not_degraded(throughput_ratio, tconf)


@pytest.mark.skip(reason='INDY-1565 is in progress')
def test_master_not_degraded_on_spike_in_1_batch_on_backups(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=5, q=1)
                                   .stop(t=1 * 60 * 60)
                                   .build()] + \
                       [ReqStream().period(s=0, i=5, q=1)
                                   .once(t=1 * 60 * 60, q=1000)
                                   .build()
                        for inst_id in range(1, 9)]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_not_degraded(throughput_ratio, tconf)


@pytest.mark.skip(reason='INDY-1565 is in progress')
def test_master_not_degraded_on_spike_in_2_batches_in_1_window_on_backups(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=5, q=1)
                                   .stop(t=1 * 60 * 60)
                                   .build()] + \
                       [ReqStream().period(s=0, i=5, q=1)
                                   .period(s=1 * 60 * 60 - 2, i=1, q=1000)
                                   .stop(t=1 * 60 * 60)
                                   .build()
                        for inst_id in range(1, 9)]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_not_degraded(throughput_ratio, tconf)


def test_master_degraded_on_spike_in_2_batches_in_2_windows_on_backups(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=5, q=1)
                                   .stop(t=1 * 60 * 60)
                                   .build()] + \
                       [ReqStream().period(s=0, i=5, q=1)
                                   .period(s=1 * 60 * 60 - 1, i=1, q=1000)
                                   .stop(t=1 * 60 * 60 + 1)
                                   .build()
                        for inst_id in range(1, 9)]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_degraded(throughput_ratio, tconf)


def test_master_degraded_on_stop_ordering_on_master(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=1, q=11)
                                   .stop(t=4 * 60 * 60)
                                   .build()] + \
                       [ReqStream().period(s=0, i=1, q=11)
                                   .stop(t=4 * 60 * 60 + 5 * 60)
                                   .build()
                        for inst_id in range(1, 9)]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_degraded(throughput_ratio, tconf)


def test_master_not_degraded_on_revival_spike_on_one_backup(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=1, q=11)
                                   .stop(t=4 * 60 * 60 + 15 * 60)
                                   .build()
                        for inst_id in range(0, 8)] + \
                       [ReqStream().period(s=0, i=1, q=11)
                                   .stop(t=4 * 60 * 60)
                                   .once(t=4 * 60 * 60 + 15 * 60, q=9900)
                                   .build()]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_not_degraded(throughput_ratio, tconf)


@pytest.mark.skip(reason='INDY-1565 is in progress')
def test_master_not_degraded_on_revival_spike_on_one_backup_while_load_stopped(tconf):
    inst_req_streams = [ReqStream().period(s=0, i=1, q=15)
                                   .stop(t=4 * 60 * 60 + 11 * 60)
                                   .build()
                        for inst_id in range(0, 8)] + \
                       [ReqStream().period(s=0, i=1, q=15)
                                   .stop(t=4 * 60 * 60)
                                   .once(t=4 * 60 * 60 + 17 * 60, q=9900)
                                   .build()]

    throughput_ratio = get_througput_ratio(inst_req_streams, tconf)

    assert_master_not_degraded(throughput_ratio, tconf)