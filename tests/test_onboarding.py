import asyncio
import json

import httpx
import pytest

from holodos_bot.bot import Bot
from holodos_bot.config import Config
from holodos_bot.onboarding import CACHE_NAME, VIDEO, VideoCache, prepare_video
from holodos_bot.providers import ProviderError, Telegram
from holodos_bot.store import Store


class Delivery:
    def __init__(self):
        self.calls, self.fail = [], None

    async def call(self, method, **payload):
        self.calls.append((method, payload))
        if method == 'sendVideo' and self.fail:
            raise ProviderError(self.fail)
        return {'message_id': len(self.calls)}


def make_bot(tmp_path, *, ready=True, telegram=None):
    store = Store(tmp_path / 'bot.sqlite3')
    bot = Bot(Config('123:token', 'groq', tmp_path), store, telegram or Delivery(), None, None, None)
    if ready:
        bot.video_cache.put(bot.video_cache.digest(VIDEO), 'cached-video')
    return bot


async def dispatch(bot, update, command, owner=1):
    state = bot.store.session(owner)
    payload = {'kind': 'text', 'text': command} if command.startswith('/') else {
        'kind': 'callback', 'data': f"h:{state['rev']}:{command}", 'message_id': state['panel']}
    bot.store.ingest(update, owner, payload)
    job = bot.store.claim()
    await bot.handle(job)
    bot.store.finish(job)
    return job


def test_first_start_order_and_per_user_once_survive_cleanup_and_restart(tmp_path):
    async def run():
        bot = make_bot(tmp_path)
        telegram = bot.telegram
        await dispatch(bot, 1, '/start')
        assert [m for m, _ in telegram.calls] == ['sendVideo', 'sendMessage']
        assert telegram.calls[0][1]['video'] == 'cached-video'
        assert telegram.calls[0][1]['supports_streaming'] is True
        assert telegram.calls[1][1]['text'].startswith('Что приготовим?')
        await dispatch(bot, 2, '/start')
        assert [m for m, _ in telegram.calls][-1] == 'sendMessage'
        with bot.store.db:
            bot.store.db.execute('UPDATE sessions SET updated=0')
            bot.store.db.execute('UPDATE jobs SET created=0')
            bot.store.db.execute('UPDATE effects SET created=0')
        bot.store.cleanup(24)
        bot.store.close()
        bot = make_bot(tmp_path, ready=False, telegram=telegram)
        bot.store.recover()
        await dispatch(bot, 3, '/start')
        assert sum(m == 'sendVideo' for m, _ in telegram.calls) == 1
        await dispatch(bot, 4, '/start', owner=2)
        assert sum(m == 'sendVideo' for m, _ in telegram.calls) == 2
        bot.store.close()
    asyncio.run(run())


def test_help_command_and_button_send_video_after_text_keep_navigation_and_dedupe_retry(tmp_path):
    async def run():
        bot = make_bot(tmp_path)
        await dispatch(bot, 1, '/help')
        assert [m for m, _ in bot.telegram.calls] == ['sendMessage', 'sendVideo']
        panel = bot.store.session(1)['panel']
        job = await dispatch(bot, 2, 'help')
        assert [m for m, _ in bot.telegram.calls][-2:] == ['editMessageText', 'sendVideo']
        before_retry = len(bot.telegram.calls)
        await bot.handle(job)
        assert len(bot.telegram.calls) == before_retry
        assert bot.store.session(1)['panel'] == panel
        await dispatch(bot, 3, 'privacy')
        assert bot.telegram.calls[-1][0] == 'editMessageText'
        assert sum(m == 'sendVideo' for m, _ in bot.telegram.calls) == 2
        await dispatch(bot, 4, 'home')
        assert bot.telegram.calls[-1][1]['text'].startswith('Что приготовим?')
        bot.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure,video_attempts', [('telegram_transport_unknown', 1), ('telegram_429', 2)])
def test_video_failure_keeps_menu_unknown_is_not_repeated_and_rejection_can_retry(tmp_path, failure, video_attempts):
    async def run():
        bot = make_bot(tmp_path)
        bot.telegram.fail = failure
        await dispatch(bot, 1, '/start')
        assert bot.telegram.calls[-1][1]['text'].startswith('Что приготовим?')
        bot.store.close()
        bot = make_bot(tmp_path, ready=False, telegram=bot.telegram)
        bot.telegram.fail = None
        bot.store.recover()
        await dispatch(bot, 2, '/start')
        assert sum(m == 'sendVideo' for m, _ in bot.telegram.calls) == video_attempts
        bot.store.close()
    asyncio.run(run())


def test_crash_during_first_delivery_and_user_delete(tmp_path):
    async def run():
        bot = make_bot(tmp_path)
        assert bot.store.claim_onboarding(1)
        bot.store.close()
        bot = make_bot(tmp_path, ready=False)
        bot.store.recover()
        await dispatch(bot, 1, '/start')
        assert [m for m, _ in bot.telegram.calls] == ['sendMessage']
        await dispatch(bot, 2, '/start', owner=2)
        await dispatch(bot, 3, '/delete')
        assert bot.store.db.execute('SELECT owner FROM onboarding').fetchall()[0][0] == 2
        await dispatch(bot, 4, '/start')
        assert [m for m, _ in bot.telegram.calls][-2:] == ['sendVideo', 'sendMessage']
        bot.store.close()
    asyncio.run(run())


def test_missing_cache_keeps_start_and_help_usable_without_uploading(tmp_path):
    async def run():
        bot = make_bot(tmp_path, ready=False)
        await dispatch(bot, 1, '/start')
        await dispatch(bot, 2, '/help')
        assert [m for m, _ in bot.telegram.calls] == ['sendMessage', 'sendMessage']
        assert not bot.store.db.execute('SELECT * FROM onboarding').fetchall()
        bot.store.close()
    asyncio.run(run())


def test_preload_uploads_once_deletes_preparation_and_cache_is_bound_to_bot_and_content(tmp_path):
    requests = []
    def respond(request):
        method = request.url.path.rsplit('/', 1)[-1]
        requests.append((method, request))
        if method == 'sendVideo':
            assert request.headers['content-type'].startswith('multipart/form-data')
            assert b'video/mp4' in request.content and b'name="supports_streaming"\r\n\r\ntrue' in request.content
            assert b'name="disable_notification"\r\n\r\ntrue' in request.content
            result = {'message_id': 11, 'video': {'file_id': 'telegram-cached-video'}}
        elif method == 'deleteMessage':
            assert json.loads(request.content) == {'chat_id': 1234, 'message_id': 11}
            result = True
        else:
            assert method == 'getFile'
            result = {'file_id': 'telegram-cached-video', 'file_size': 20}
        return httpx.Response(200, json={'ok': True, 'result': result})
    async def run():
        clip = tmp_path / 'clip.mp4'
        clip.write_bytes(b'video fixture')
        cache = VideoCache(tmp_path / CACHE_NAME, '123')
        telegram = Telegram('123:secret', httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            with pytest.raises(ProviderError, match='video_cache_not_ready'):
                await prepare_video(telegram, cache, check_only=True, path=clip)
            assert requests == []
            assert (await prepare_video(telegram, cache, 1234, path=clip))['uploaded']
            assert [m for m, _ in requests] == ['sendVideo', 'deleteMessage', 'getFile']
            restarted = VideoCache(tmp_path / CACHE_NAME, '123')
            assert not (await prepare_video(telegram, restarted, check_only=True, path=clip))['uploaded']
            assert not (await prepare_video(telegram, restarted, 1234, path=clip))['uploaded']
            assert sum(m == 'sendVideo' for m, _ in requests) == 1
            other = VideoCache(tmp_path / CACHE_NAME, '456')
            assert other.get(other.digest(clip)) is None
            clip.write_bytes(b'new video fixture')
            with pytest.raises(ProviderError, match='video_cache_not_ready'):
                await prepare_video(telegram, restarted, check_only=True, path=clip)
        finally:
            await telegram.close()
    asyncio.run(run())


@pytest.mark.parametrize('error', ['timeout', 'invalid_file', 'forbidden'])
def test_preload_only_reuploads_explicitly_invalid_reference(tmp_path, error):
    methods = []
    def respond(request):
        method = request.url.path.rsplit('/', 1)[-1]
        methods.append(method)
        if len(methods) == 1:
            if error == 'timeout':
                raise httpx.ReadTimeout('lost response')
            code, description = (400, 'Bad Request: wrong file identifier/HTTP URL specified') if error == 'invalid_file' \
                else (403, 'Forbidden')
            return httpx.Response(code, json={'ok': False, 'error_code': code, 'description': description})
        result = {'message_id': 9, 'video': {'file_id': 'fresh'}} if method == 'sendVideo' else True
        return httpx.Response(200, json={'ok': True, 'result': result})
    async def run():
        clip = tmp_path / 'clip.mp4'
        clip.write_bytes(b'video')
        cache = VideoCache(tmp_path / CACHE_NAME, '123')
        cache.put(cache.digest(clip), 'old')
        telegram = Telegram('123:secret', httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            if error == 'invalid_file':
                assert (await prepare_video(telegram, cache, 1234, path=clip))['uploaded']
                assert methods == ['getFile', 'sendVideo', 'deleteMessage', 'getFile']
            else:
                with pytest.raises(ProviderError):
                    await prepare_video(telegram, cache, 1234, path=clip)
                assert methods == ['getFile']
                assert cache.get(cache.digest(clip)) == 'old'
        finally:
            await telegram.close()
    asyncio.run(run())


def test_preload_upload_timeout_is_not_retried(tmp_path):
    methods = []
    def respond(request):
        methods.append(request.url.path.rsplit('/', 1)[-1])
        raise httpx.ReadTimeout('lost response')
    async def run():
        clip = tmp_path / 'clip.mp4'
        clip.write_bytes(b'video')
        cache = VideoCache(tmp_path / CACHE_NAME, '123')
        telegram = Telegram('123:secret', httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            with pytest.raises(ProviderError, match='telegram_transport_unknown'):
                await prepare_video(telegram, cache, 1234, path=clip)
            assert methods == ['sendVideo']
            assert cache.get(cache.digest(clip)) is None
        finally:
            await telegram.close()
    asyncio.run(run())
