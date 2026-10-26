"""Preload the demo before switching releases; --check never sends messages."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path

from holodos_bot.config import Config
from holodos_bot.onboarding import CACHE_NAME, VideoCache, prepare_video
from holodos_bot.providers import ProviderError, Telegram


async def prepare(args):
    shared = args.app.parent / 'config.json'
    config = Config.load(args.app / 'config.json', shared)
    cache = VideoCache(config.data_dir / CACHE_NAME, config.token.split(':', 1)[0])
    telegram = Telegram(config.token)
    try:
        owner = None
        if not args.check:
            # Use only the configured owner's private chat, never a user from the DB.
            owner = int(json.loads(shared.read_text())['my_chat_id'])
            if owner <= 0 or (config.allowed_user_ids and owner not in config.allowed_user_ids):
                raise ProviderError('video_cache_chat_not_authorized')
            chat = await telegram.call('getChat', chat_id=owner)
            if chat.get('type') != 'private' or chat.get('id') != owner:
                raise ProviderError('video_cache_chat_not_private')
        with (config.data_dir / '.video-cache.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = await prepare_video(telegram, cache, owner, check_only=args.check)
        print(json.dumps(result))
    finally:
        await telegram.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    os.umask(0o077)
    try:
        asyncio.run(prepare(parser.parse_args()))
    except (ProviderError, OSError, ValueError, KeyError) as error:
        print(json.dumps({'ready': False, 'error': str(error) if isinstance(error, ProviderError)
                         else type(error).__name__}))
        raise SystemExit(1) from None
