"""Publish the prepared Holodos icons under the configured owner's Telegram account."""
import argparse
import json
from pathlib import Path

import httpx

EMOJI = dict(zip(
    ['dish', 'fridge', 'plan', 'recognize', 'new_list', 'shopping', 'recipe', 'alternatives',
     'refine', 'profile', 'save', 'calendar', 'previous', 'next', 'back', 'home'],
    ['🍽', '🧊', '📋', '📷', '📝', '🛒', '📖', '🔄', '✏️', '👤', '✅', '📅', '⬅️', '➡️', '↩️', '🏠'], strict=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
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
        name = f'HolodosOrange20261025_by_{username}'
        if args.receipt.exists():
            receipt = json.loads(args.receipt.read_text())
            current = call('getStickerSet', {'name': receipt['name']})
            assert len(current['stickers']) == len(EMOJI)
            print(json.dumps(receipt))
            return
        stickers = []
        for key, emoji in EMOJI.items():
            path = args.assets / (key + '.png')
            uploaded = call('uploadStickerFile', {'user_id': str(owner), 'sticker_format': 'static'},
                            {'sticker': (path.name, path.read_bytes(), 'image/png')})
            stickers.append({'sticker': uploaded['file_id'], 'format': 'static', 'emoji_list': [emoji]})
        call('createNewStickerSet', {'user_id': owner, 'name': name, 'title': 'Холодос · Тёплый оранжевый',
                                    'sticker_type': 'custom_emoji', 'stickers': stickers})
        current = call('getStickerSet', {'name': name})
        assert current['sticker_type'] == 'custom_emoji' and len(current['stickers']) == len(EMOJI)
        icons = {}
        for key, sticker in zip(EMOJI, current['stickers'], strict=True):
            assert sticker['width'] == sticker['height'] == 100 and sticker.get('custom_emoji_id')
            icons[key] = sticker['custom_emoji_id']
        receipt = {'name': name, 'url': 'https://t.me/addemoji/' + name, 'icons': icons}
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()
