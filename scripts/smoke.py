"""Opt-in live provider smoke. Never sends Telegram messages or reads user conversations."""
import argparse
import asyncio
import json
import logging
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from holodos_bot.config import Config
from holodos_bot.providers import Groq, Luna
from holodos_bot.recipes import (
    INVENTORY_PROMPT,
    INVENTORY_SCHEMA,
    PublicWeb,
    RecipeSearch,
)


async def run(args):
    config = Config.load(args.config, args.secrets)
    luna, web, groq = Luna(config), PublicWeb(), Groq(config.groq_key)
    report = {"model": config.model, "effort": config.effort}
    try:
        with tempfile.TemporaryDirectory(prefix="holodos-smoke-") as tmp:
            directory = Path(tmp)
            if args.stage in {"vision", "all"}:
                images = []
                for i, labels in enumerate([['EGGS', 'TOMATOES', 'MILK'], ['EGGS', 'CHEESE']]):
                    path = directory / f"shelf-{i}.jpg"
                    image = Image.new("RGB", (900, 600), "#dee3e6")
                    draw = ImageDraw.Draw(image)
                    for j, label in enumerate(labels):
                        x = j * 290 + 15
                        draw.rectangle((x, 60, x + 250, 500), fill=["#e4c48e", "#e45145", "#fafafa"][j])
                        draw.text((x + 15, 260), label, fill="#161616", font_size=30)
                    image.save(path)
                    images.append(path)
                value = await luna.ask(INVENTORY_PROMPT, INVENTORY_SCHEMA,
                    {"inventory": [], "preferences": "", "note": "Это тестовые карточки продуктов на полке."},
                    images=images)
                assert len(value["inventory"]) >= 3, "vision_did_not_read_images"
                report['vision_fixture'] = value["inventory"]
                corrected = await luna.ask(INVENTORY_PROMPT, INVENTORY_SCHEMA,
                    {"inventory": value["inventory"], "preferences": "", "correction": "Убери молоко, добавь рис."})
                assert any('рис' in x['name'].lower() for x in corrected['inventory']), 'edit_missing_rice'
                assert not any('молок' in x['name'].lower() for x in corrected['inventory']), 'edit_kept_milk'
                report['text_correction'] = 'ok'
            if args.stage in {"asr", "all"}:
                # A locally generated sample carries no user data.
                audio = directory / 'voice.wav'
                process = await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'flite=text=Remove milk. Add rice and tomatoes.:voice=slt', str(audio),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                assert await process.wait() == 0, 'speech_fixture_failed'
                transcript = await groq.transcribe(audio)
                assert 'rice' in transcript.lower(), 'asr_missing_rice'
                report['asr'] = 'ok'
            if args.stage in {"search", "all"}:
                inventory = [{"name": x, "quantity": "", "uncertain": False} for x in
                    ['яйца', 'помидоры', 'молоко', 'сыр чеддер', 'сливочное масло', 'оливковое масло',
                     'соль', 'чёрный перец', 'вода', 'лук', 'чеснок', 'рис', 'картофель', 'хлеб', 'базилик']]
                search = RecipeSearch(luna, web)
                found = await search.search(inventory, '', directory)
                assert found, 'no_verified_recipes'
                rendered = await search.render(found[0])
                report['recipes'] = [{"title": r['title_ru'], 'url': r['url'],
                                      'photo_bytes': Path(r['picture']).stat().st_size,
                                      'missing': r['missing']} for r in found]
                report['rendered_steps'] = len(rendered['steps'])
                report['translation_status'] = rendered['translation_status']
            print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await web.close()
        await groq.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--secrets', type=Path, default=Path('/home/tony/Desktop/_code/config.json'))
    parser.add_argument('--stage', choices=['vision', 'asr', 'search', 'all'], default='all')
    asyncio.run(run(parser.parse_args()))
