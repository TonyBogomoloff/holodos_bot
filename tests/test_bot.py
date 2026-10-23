import asyncio

import pytest

from holodos_bot.bot import Bot, normalize_update
from holodos_bot.config import Config
from holodos_bot.providers import ProviderError
from holodos_bot.store import Store


class Telegram:
    def __init__(self):
        self.sent = []

    async def call(self, method, **payload):
        self.sent.append((method, payload))
        return {'message_id': len(self.sent)}


def test_cannot_act_from_another_users_callback_or_group():
    callback = {'callback_query': {'from': {'id': 1}, 'message': {'chat': {'id': 2, 'type': 'private'}},
                                   'data': 'h:1:search'}}
    assert normalize_update(callback) == (None, None)
    assert normalize_update({'message': {'from': {'id': 1}, 'chat': {'id': 1, 'type': 'group'}}}) == (None, None)


def test_album_and_duplicate_photo_keep_one_inventory_panel(tmp_path):
    async def run():
        store, telegram = Store(tmp_path / 'db.sqlite'), Telegram()
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, None, None)
        for identity, unique in enumerate(['a', 'b', 'a'], 1):
            store.ingest(identity, 1, {'kind': 'photo', 'file_id': unique, 'unique': unique, 'caption': ''})
            job = store.claim()
            await bot.handle(job)
            store.finish(job)
        assert len(store.session(1)['photos']) == 2
        assert [x[0] for x in telegram.sent] == ['sendMessage', 'editMessageText', 'editMessageText']
    asyncio.run(run())


def test_stale_button_does_not_call_model(tmp_path):
    async def run():
        store, telegram = Store(tmp_path / 'db.sqlite'), Telegram()
        state = store.session(1)
        with store.db:
            store.save(1, state)
        store.ingest(1, 1, {'kind': 'callback', 'data': f"h:{state['rev'] - 1}:search"})
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, None, None)
        await bot.handle(store.claim())
        assert not store.session(1)['searched']
        assert 'уже изменился' in telegram.sent[0][1]['text']
    asyncio.run(run())


def test_lost_send_response_is_not_retried_blindly(tmp_path):
    async def run():
        store = Store(tmp_path / 'db.sqlite')
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, Telegram(), None, None, None)
        store.ingest(1, 1, {'kind': 'text'})
        job = store.claim()
        calls = []
        async def ambiguous():
            calls.append(1)
            raise ProviderError('telegram_transport_unknown')
        with pytest.raises(ProviderError, match='telegram_transport_unknown'):
            await bot.effect(job, 'album', ambiguous)
        with pytest.raises(ProviderError, match='delivery_unknown'):
            await bot.effect(job, 'album', ambiguous)
        assert len(calls) == 1
    asyncio.run(run())


def test_voice_correction_applies_once_after_delivery_failure(tmp_path):
    async def run():
        store, telegram = Store(tmp_path / 'db.sqlite'), Telegram()
        state = store.session(1)
        state['inventory'] = [{'name': 'молоко', 'quantity': '', 'uncertain': False}]
        with store.db:
            store.save(1, state)
        class Model:
            calls = 0
            async def ask(self, *args, **kwargs):
                self.calls += 1
                return {'inventory': [{'name': 'рис', 'quantity': '', 'uncertain': False}],
                        'preferences': '', 'reply': 'Поправил.'}
        model = Model()
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, model, None)
        store.ingest(1, 1, {'kind': 'voice', 'duration': 3, 'file_id': 'test'})
        job = store.claim()
        store.checkpoint(job, 'transcript', 'Убери молоко, добавь рис')
        await bot.handle(job)
        revision = store.session(1)['rev']
        store.recover()
        await bot.handle(store.claim())
        assert model.calls == 1
        assert store.session(1)['rev'] == revision
        assert store.session(1)['previous'][0][0]['name'] == 'молоко'
    asyncio.run(run())
