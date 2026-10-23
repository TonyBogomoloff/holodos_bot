from holodos_bot.store import Store


def test_durable_dedup_offset_and_per_user_order(tmp_path):
    path = tmp_path / 'db.sqlite'
    s = Store(path)
    for update, owner in [(10, 1), (11, 1), (12, 2), (10, 1)]:
        s.ingest(update, owner, {'kind': 'text', 'text': str(update)})
    first, parallel = s.claim(), s.claim()
    assert (first['update_id'], parallel['update_id']) == (10, 12)
    assert s.claim() is None
    assert s.offset() == 13
    s.close()
    s = Store(path)
    s.recover()
    recovered = s.claim()
    assert recovered['update_id'] == 10
    s.finish(recovered)
    assert s.claim()['update_id'] == 11


def test_atomic_correction_checkpoint_and_isolation(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    s.ingest(1, 100, {'kind': 'text'})
    job = s.claim()
    state = s.session(100)
    state['inventory'] = [{'name': 'рис'}]
    s.checkpoint(job, 'applied', True, state)
    s.recover()
    assert s.claim()['cache']['applied'] is True
    assert s.session(100)['inventory'] == [{'name': 'рис'}]
    assert s.session(200)['inventory'] == []
    old_rev = state['rev']
    with s.db:
        s.db.execute('DELETE FROM sessions WHERE owner=100')
    assert s.session(100)['rev'] != old_rev


def test_quota_reservation_not_charged_twice_on_retry(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    s.ingest(1, 100, {'kind': 'text'})
    job = s.claim()
    assert s.reserve(job, 1, 2)
    assert s.reserve(job, 1, 2)
    s.finish(job)
    s.ingest(2, 100, {'kind': 'text'})
    assert not s.reserve(s.claim(), 1, 2)


def test_cleanup_does_not_remove_pending_work(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    s.ingest(1, 1, {'kind': 'text'})
    with s.db:
        s.save(1, s.session(1))
        s.save(2, s.session(2))
        s.db.execute('UPDATE sessions SET updated=0')
    s.cleanup(24)
    assert [r[0] for r in s.db.execute('SELECT owner FROM sessions')] == [1]
