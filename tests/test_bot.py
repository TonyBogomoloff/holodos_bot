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


def test_photos_auto_merge_below_messages_and_retry_once(tmp_path):
    async def run():
        from PIL import Image
        store, telegram = Store(tmp_path / 'db.sqlite'), Telegram()
        async def download(file_id, target):
            Image.new('RGB', (64, 64)).save(target, format='PNG')
        telegram.download = download
        class Model:
            calls = 0
            async def ask(self, prompt, schema, data, **kwargs):
                self.calls += 1
                assert len(data['inventory']) == self.calls - 1
                assert len(kwargs['images']) == 1
                return {'inventory': data['inventory'] + data['inventory'] + [
                    {'name': str(self.calls), 'quantity': '', 'uncertain': False}],
                    'preferences': '', 'reply': 'Добавил.'}
        model = Model()
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, model, None)
        state = store.session(1)
        state['panel'] = 999
        with store.db:
            store.save(1, state)
        for identity, unique in enumerate(['a', 'b', 'c', 'a'], 1):
            store.ingest(identity, 1, {'kind': 'photo', 'file_id': unique, 'unique': unique, 'caption': ''})
            job = store.claim()
            await bot.handle(job)
            revision = store.session(1)['rev']
            await bot.handle(job)
            assert store.session(1)['rev'] == revision
            store.finish(job)
        assert model.calls == 3
        assert len(store.session(1)['inventory']) == 3
        assert store.session(1)['photos'] == []
        assert sum(method == 'sendMessage' for method, _ in telegram.sent) == 4
        assert all(payload.get('message_id') != 999 for _, payload in telegram.sent)
        assert 'Готово, распознать' not in str(telegram.sent)
        assert 'Отменить правку' not in str(telegram.sent)
        assert 'Добавить фото' not in str(telegram.sent)
        second = [p for method, p in telegram.sent
                  if method == 'editMessageText' and '<b>2</b>' in p.get('text', '')][0]
        assert second['text'].startswith('<b>Твои продукты</b>\n• 1\n• <b>2</b>')
        assert second['text'].count('• 1') == 1
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
        sends = [payload for method, payload in telegram.sent if method == 'sendMessage']
        assert len(sends) == 2
        assert sends[0]['text'] == 'Слушаю голосовое…'
        assert sends[1]['text'].startswith('<b>Твои продукты</b>')
        assert '<b>рис</b>' in sends[1]['text'] and '<s>молоко</s>' in sends[1]['text']
        assert sends[1]['parse_mode'] == 'HTML'
        assert 'Поправил.' not in sends[1]['text']
        assert any(payload.get('entities') == [{'type': 'bold', 'offset': 0, 'length': 11}]
                   for _, payload in telegram.sent)
        assert any(method == 'editMessageText' and payload['text'] ==
                   'Расшифровка\nУбери молоко, добавь рис' for method, payload in telegram.sent)
    asyncio.run(run())


def test_recipe_click_burst_calls_luna_and_sends_recipe_once(tmp_path):
    async def run():
        store, telegram = Store(tmp_path / 'db.sqlite'), Telegram()
        state = store.session(1)
        state['recipes'] = [{'title': 'Eggs', 'title_ru': 'Яйца', 'ingredients': ['2 eggs'],
                             'steps': ['Boil for 5 minutes.', 'Serve.'],
                             'url': 'https://www.budgetbytes.com/test/'}]
        with store.db:
            store.save(1, state)
        payload = {'kind': 'callback', 'data': f"h:{state['rev']}:pick:0"}
        class Model:
            calls = 0
            async def ask(self, *args, **kwargs):
                self.calls += 1
                assert telegram.sent[-1][1]['text'] == 'Загружаю рецепт…'
                # Realistic separate callbacks received while Luna is working.
                for update in range(2, 12):
                    assert store.ingest(update, 1, {**payload, 'callback_id': str(update)})
                return {'ingredients': ['2 яйца'], 'steps': ['Вари 5 минут.', 'Подавай.']}
        model = Model()
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, model, None)
        store.ingest(1, 1, payload)
        job = store.claim()
        await bot.handle(job)
        store.finish(job)
        assert store.claim() is None
        assert model.calls == 1
        assert [method for method, _ in telegram.sent] == ['sendMessage', 'editMessageText']
        assert telegram.sent[1][1]['message_id'] == 1
        text = telegram.sent[1][1]['text']
        assert text.startswith('<b>Яйца</b>\n<a href="https://www.budgetbytes.com/test/">Источник</a>')
        assert '<b>Ингредиенты:</b>' in text and '<b>Как приготовить:</b>' in text
        assert '<i>Сверь количество продуктов перед готовкой.</i>' in text
        saved = store.session(1)
        del saved['recipes'][0]['title_ru']
        with store.db:
            store.save(1, saved)
        store.ingest(12, 1, {'kind': 'callback', 'data': f"h:{state['rev']}:choices"})
        choices_job = store.claim()
        await bot.handle(choices_job)
        store.finish(choices_job)
        assert telegram.sent[-1][0] == 'sendMessage'
        assert telegram.sent[-1][1]['reply_markup']['inline_keyboard'][0][0]['text'] == '1. Eggs'
        assert model.calls == 1
        store.ingest(13, 1, payload)
        repeat_job = store.claim()
        await bot.handle(repeat_job)
        store.finish(repeat_job)
        assert model.calls == 1
        assert telegram.sent[-1][0] == 'editMessageText'
        assert telegram.sent[-1][1]['text'] == text.replace('<b>Яйца</b>', '<b>Eggs</b>')
        assert '1. Вари 5 минут.\n\n2. Подавай.' in text
        assert '• 2 яйца' in text and state['recipes'][0]['url'] in text
        assert store.db.execute('SELECT used FROM quota WHERE owner=1').fetchone()[0] == 1
    asyncio.run(run())


def test_inventory_diff_formats_changes_and_escapes_input():
    from holodos_bot.bot import inventory_text
    def item(name):
        return {'name': name, 'quantity': '', 'uncertain': False}
    state = {'inventory': [item('рис'), item('<сыр & молоко>')], 'photos': [],
             'preferences': '', 'previous': [[item('рис'), item('хлеб')], '']}
    text = inventory_text(state, changes=True)
    assert text.startswith('<b>Твои продукты</b>\n• рис\n')
    assert '<b>&lt;сыр &amp; молоко&gt;</b>' in text
    assert '<s>хлеб</s>' in text
    assert '<s>' not in inventory_text(state)
    state['inventory'] = []
    assert '<s>рис</s>' in inventory_text(state, changes=True)
    assert 'Список пуст.' in inventory_text(state, changes=True)
