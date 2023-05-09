from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from datetimerange import DateTimeRange

from noisepy.seis.asdfstore import ASDFCCStore, ASDFRawDataStore
from noisepy.seis.datatypes import Channel, ChannelType, ConfigParameters, Station


@pytest.fixture
def store():
    import os

    return ASDFRawDataStore(os.path.join(os.path.dirname(__file__), "./data"))


# Use the built in tmp_path fixture: https://docs.pytest.org/en/7.1.x/how-to/tmp_path.html
# to create a CC store
@pytest.fixture
def ccstore(tmp_path: Path) -> ASDFCCStore:
    return ASDFCCStore(str(tmp_path))


def test_get_timespans(store: ASDFRawDataStore):
    ts = store.get_timespans()
    assert len(ts) == 1
    assert ts[0].start_datetime == datetime.fromisoformat("2019-02-01T00:00:00+00:00")
    assert ts[0].end_datetime == datetime.fromisoformat("2019-02-01T01:00:00+00:00")


def test_get_channels(store: ASDFRawDataStore):
    ts = store.get_timespans()[0]
    chans = store.get_channels(ts)
    assert len(chans) == 1
    assert str(chans[0].type) == "bhn_00"
    assert chans[0].station.name == "BAK"


def test_get_data(store: ASDFRawDataStore):
    ts = store.get_timespans()[0]
    chan = store.get_channels(ts)[0]
    chdata = store.read_data(ts, chan)
    assert chdata.data.size == 72001
    assert chdata.sampling_rate == 20.0
    assert chdata.start_timestamp == ts.start_datetime.timestamp()


def test_ccstore(ccstore: ASDFCCStore):
    def make_1dts(dt: datetime):
        return DateTimeRange(dt, dt + timedelta(days=1))

    config = ConfigParameters()
    data = np.zeros(0)
    ts1 = make_1dts(datetime.now())
    ts2 = make_1dts(ts1.end_datetime)
    src = Channel(ChannelType("foo"), Station("nw", "sta1", -1, -1, -1, ""))
    rec = Channel(ChannelType("bar"), Station("nw", "sta2", -1, -1, -1, ""))

    # assert empty state
    assert not ccstore.is_done(ts1)
    assert not ccstore.is_done(ts2)
    assert not ccstore.contains(ts1, src, rec, config)
    assert not ccstore.contains(ts2, src, rec, config)

    # add CC (src->rec) for ts1
    ccstore.append(ts1, src, rec, config, {}, data)
    # assert ts1 is there, but not ts2
    assert ccstore.contains(ts1, src, rec, config)
    assert not ccstore.contains(ts2, src, rec, config)
    # also rec->src should not be there for ts1
    assert not ccstore.contains(ts1, rec, src, config)
    assert not ccstore.is_done(ts1)
    # now mark ts1 done and assert it
    ccstore.mark_done(ts1)
    assert ccstore.is_done(ts1)
    assert not ccstore.is_done(ts2)

    # now add CC for ts2
    ccstore.append(ts2, src, rec, config, {}, data)
    assert ccstore.contains(ts2, src, rec, config)
    assert not ccstore.is_done(ts2)
    ccstore.mark_done(ts2)
    assert ccstore.is_done(ts2)