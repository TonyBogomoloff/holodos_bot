import asyncio
import json

import httpx
import pytest

from holodos_bot import button_icons
from holodos_bot.bot import Bot
from holodos_bot.config import Config
from holodos_bot.navigation import footer
from holodos_bot.providers import ProviderError, Telegram
from holodos_bot.store import Store


def test_custom_icons_and_navigation_only_fallback(monkeypatch):
    monkeypatch.setattr(button_icons, 'ICONS', {'dish': '123', 'back': '124', 'home': '125'})
    original = footer({'rev': 7}, [[{'text': 'Подобрать блюда', 'callback_data': 'h:7:search'}]])
    decorated = button_icons.decorate(original)
    assert decorated['inline_keyboard'][0][0]['icon_custom_emoji_id'] == '123'
    assert [b['text'] for b in decorated['inline_keyboard'][-1]] == ['Назад', 'Главное меню']
    fallback = button_icons.decorate(decorated, False)
    assert fallback['inline_keyboard'][0][0] == original['inline_keyboard'][0][0]
    assert [b['text'] for b in fallback['inline_keyboard'][-1]] == ['⬅️ Назад', '🏠 Главное меню']
    assert original['inline_keyboard'][-1][0]['text'] == '⬅️'
    assert all('icon_custom_emoji_id' not in b for row in fallback['inline_keyboard'] for b in row)


@pytest.mark.parametrize('error,expected_calls', [('CUSTOM_EMOJI_INVALID', 2), ('chat not found', 1)])
def test_only_explicit_icon_failure_retries_without_icons(monkeypatch, error, expected_calls):
    monkeypatch.setattr(button_icons, 'ICONS', {'dish': '123'})
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={'ok': False, 'error_code': 400, 'description': error})
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 1}})
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        telegram = Telegram('test:token', client)
        try:
            result = await telegram.call('sendMessage', chat_id=1, text='Меню', reply_markup=footer(
                {'rev': 7}, [[{'text': 'Подобрать блюда', 'callback_data': 'h:7:search'}]]))
            assert expected_calls == 2 and result['message_id'] == 1
        except ProviderError:
            assert expected_calls == 1
        finally:
            await telegram.close()
    asyncio.run(run())
    assert len(requests) == expected_calls
    if expected_calls == 2:
        assert requests[-1]['reply_markup']['inline_keyboard'][0][0] == {
            'text': 'Подобрать блюда', 'callback_data': 'h:7:search'}


def test_long_screen_pagination_sends_custom_icons(tmp_path):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 1}})

    async def run():
        store = Store(tmp_path / 'pages.sqlite')
        telegram = Telegram('test:token', httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            bot = Bot(Config('test:token', 'key', tmp_path), store, telegram, None, None, None)
            store.ingest(1, 1, {'kind': 'text', 'text': '/products'})
            job, state = store.claim(), store.session(1)
            state['screen_pages'] = {'texts': ['Первая', 'Вторая', 'Третья'], 'markup': footer(state, [])}
            await bot.screen_page(job, state, 1)
            buttons = requests[-1]['reply_markup']['inline_keyboard']
            assert [b['icon_custom_emoji_id'] for b in buttons[0]] == [
                button_icons.ICONS['previous'], button_icons.ICONS['next']]
            assert all(b.get('icon_custom_emoji_id') for row in buttons for b in row)
            assert requests[-1]['text'] == '2/3\n\nВторая'
        finally:
            await telegram.close()
            store.close()

    asyncio.run(run())
