"""Publish the prepared Holodos icons under the configured owner's Telegram account."""
import argparse
import hashlib
import json
from pathlib import Path

import httpx
from PIL import Image

EMOJI = dict(zip(
    ['dish', 'fridge', 'plan', 'recognize', 'new_list', 'shopping', 'recipe', 'alternatives',
     'refine', 'profile', 'save', 'calendar', 'previous', 'next', 'back', 'home'],
    ['🍽', '🧊', '📋', '📷', '📝', '🛒', '📖', '🔄', '✏️', '👤', '✅', '📅', '⬅️', '➡️', '↩️', '🏠'], strict=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--pack', type=Path, help='JSON with name_prefix, title and emoji mapping')
    args = parser.parse_args()
    pack = json.loads(args.pack.read_text()) if args.pack else {
        'name_prefix': 'HolodosOrange20261025', 'title': 'Холодос · Тёплый оранжевый', 'emoji': EMOJI}
    emoji = pack['emoji']
    hashes = {}
    for key in emoji:
        path = args.assets / (key + '.png')
        with Image.open(path) as picture:
            if picture.size != (100, 100) or picture.mode != 'RGBA' or picture.getextrema()[3][0] != 0:
                raise SystemExit(f'{key}: expected transparent RGBA 100x100')
        hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    secrets = json.loads(Path('/home/tony/Desktop/_code/config.json').read_text())
    owner = int(secrets['my_chat_id'])
    token = secrets['bot_token_holodos_recept']
    with httpx.Client(timeout=60, trust_env=False) as client:
        def call(method, payload=None, files=None):
            url = f'https://api.telegram.org/bot{token}/{method}'
            try:
                response = (client.post(url, data=payload, files=files) if files
                            else client.post(url, json=payload or {}))
                result = response.json()
            except (httpx.HTTPError, ValueError):
                raise SystemExit(f'{method}: transport failure; verify remote state before retrying') from None
            if not result.get('ok'):
                raise SystemExit(f"{method}: {result.get('description', 'failed')}")
            return result['result']
        username = call('getMe')['username']
        if username != 'holodos_recept_bot':
            raise SystemExit('Unexpected bot; nothing published')
        name = f"{pack['name_prefix']}_by_{username}"
        if args.receipt.exists():
            receipt = json.loads(args.receipt.read_text())
            assert receipt['name'] == name and set(receipt['icons']) == set(emoji)
            if 'sha256' in receipt:
                assert receipt['sha256'] == hashes, 'published_assets_changed'
            current = call('getStickerSet', {'name': name})
            assert [s['custom_emoji_id'] for s in current['stickers']] == list(receipt['icons'].values())
            print(json.dumps(receipt))
            return
        stickers = []
        for key, glyph in emoji.items():
            path = args.assets / (key + '.png')
            uploaded = call('uploadStickerFile', {'user_id': str(owner), 'sticker_format': 'static'},
                            {'sticker': (path.name, path.read_bytes(), 'image/png')})
            stickers.append({'sticker': uploaded['file_id'], 'format': 'static', 'emoji_list': [glyph]})
        call('createNewStickerSet', {'user_id': owner, 'name': name, 'title': pack['title'],
                                    'sticker_type': 'custom_emoji', 'stickers': stickers})
        current = call('getStickerSet', {'name': name})
        assert current['sticker_type'] == 'custom_emoji' and len(current['stickers']) == len(emoji)
        icons = {}
        for key, sticker in zip(emoji, current['stickers'], strict=True):
            assert sticker['width'] == sticker['height'] == 100 and sticker.get('custom_emoji_id')
            icons[key] = sticker['custom_emoji_id']
        receipt = {'name': name, 'url': 'https://t.me/addemoji/' + name, 'icons': icons, 'sha256': hashes}
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()
