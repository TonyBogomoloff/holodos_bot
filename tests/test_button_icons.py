import asyncio
import json

import httpx
import pytest

from holodos_bot import button_icons
from holodos_bot.navigation import footer
from holodos_bot.providers import ProviderError, Telegram


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
