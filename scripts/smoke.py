"""Opt-in live provider smoke. Never sends Telegram messages or reads user conversations."""
import argparse
import asyncio
import json
import logging
import tempfile
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from holodos_bot.bot import Bot
from holodos_bot.config import Config
from holodos_bot.planning import (
    AUDIT_PROMPT,
    AUDIT_SCHEMA,
    PROFILE_PROMPT,
    PROFILE_SCHEMA,
    MealPlanner,
    profile_question,
)
from holodos_bot.providers import Groq, Luna, prepare_image
from holodos_bot.recipes import (
    INVENTORY_PROMPT,
    INVENTORY_SCHEMA,
    PublicWeb,
    RecipeSearch,
    extract_recipe,
    recipe_source,
)
from holodos_bot.store import Store

SOURCE_SAMPLES = {
    'eda.rambler.ru': 'https://eda.rambler.ru/recepty/zavtraki/sirniki-iz-tvoroga-18506',
    'budgetbytes.com': 'https://www.budgetbytes.com/creamy-tomato-spinach-pasta/',
    '1000.menu': 'https://1000.menu/cooking/5227-prostaya-sharlotka',
}


async def run(args):
    config = Config.load(args.config, args.secrets)
    luna, web, groq = Luna(config), PublicWeb(), Groq(config.groq_key)
    report = {"model": config.model, "effort": config.effort}
    try:
        with tempfile.TemporaryDirectory(prefix="holodos-smoke-") as tmp:
            directory = Path(tmp)
            if args.stage == 'planning':
                # Real profile extraction, web search, downloaded recipes/photos and diet audit.
                # All inputs are synthetic; only delivery is simulated.
                profile = await luna.ask(PROFILE_PROMPT, PROFILE_SCHEMA, {'profile': {}, 'message':
                    'Два человека, три приёма пищи в день, бюджет на всех 140 евро в неделю. '
                    'Покупки в Испании. Любим простую домашнюю еду, ограничений питания нет.'})
                assert not profile_question(profile), 'incomplete_profile'
                assert profile['people'] == 2 and profile['meals'] == 3
                assert profile['budget'] == 140 and profile['budget_days'] == 7
                partial = await luna.ask(PROFILE_PROMPT, PROFILE_SCHEMA, {'profile': {}, 'message':
                    'Нас двое, 3 приёма пищи, бюджет 100 в неделю на всех. Испания, люблю овощи, '
                    'аллергия на орехи.'})
                assert partial['currency'] is None, 'guessed_currency'
                updated = await luna.ask(PROFILE_PROMPT, PROFILE_SCHEMA, {'profile': partial,
                    'question': profile_question(partial), 'message': 'Евро. И ещё не люблю грибы.'})
                assert 'орех' in updated['restrictions'].lower(), 'lost_allergy'
                audit = await luna.ask(AUDIT_PROMPT, AUDIT_SCHEMA, {
                    'profile': {**profile, 'restrictions': 'аллергия на молоко'},
                    'recipes': [{'id': 0, 'ingredients': ['200 мл молока', 'рис 100 г'],
                                 'steps': ['Сварить рис на молоке.']},
                                {'id': 1, 'ingredients': ['рис 100 г', 'вода 200 мл'],
                                 'steps': ['Сварить рис на воде.']}]})
                assert {r['id']: r['safe'] for r in audit['recipes']} == {0: False, 1: True}, 'diet_audit_failed'
                inventory = [{'name': 'рис', 'quantity': '500 г', 'uncertain': False},
                             {'name': 'яйца', 'quantity': '', 'uncertain': False}]
                async def remember(key, operation):
                    return await operation()
                async def progress(text):
                    logging.info('planning_stage %s', text)
                plan = await MealPlanner(luna, web).generate(
                    profile, inventory, 7, directory, remember, progress)
                assert len(plan['schedule']) == 7 and all(len(day) == 3 for day in plan['schedule'])
                assert plan['estimate'] <= plan['budget']
                store = Store(directory / 'planning.sqlite')
                class PlanningTelegram:
                    messages = []
                    photos = []
                    async def call(self, method, **payload):
                        self.messages.append((method, payload))
                        return {'message_id': len(self.messages)}
                    async def album(self, owner, pictures):
                        self.photos.extend(pictures)
                        return [{'message_id': 1000}]
                telegram = PlanningTelegram()
                bot = Bot(replace(config, data_dir=directory), store, telegram, None, luna, web)
                try:
                    state = store.session(1)
                    state.update(profile=profile, meal_plan=plan, inventory=inventory, mode='plan')
                    with store.db:
                        store.save(1, state)
                    for i, action in enumerate(['shopping', 'day:6', 'meal:0'], 1):
                        store.ingest(i, 1, {'kind': 'callback', 'data': f"h:{state['rev']}:{action}"})
                        job = store.claim()
                        await bot.handle(job)
                        store.finish(job)
                    assert telegram.messages[0][1]['text'].startswith('Покупки на 7')
                    assert 'День 7 из 7' in telegram.messages[1][1]['text']
                    assert telegram.photos and 'На одну порцию' in telegram.messages[-1][1]['text']
                    report['planning'] = {'days': 7, 'people': 2, 'meals': 21,
                        'verified_recipes': len(plan['recipes']), 'shopping_items': len(plan['shopping']),
                        'shopping_needs_clarification': sum(r['check'] for r in plan['shopping']),
                        'within_estimated_budget': True, 'profile': 'parsed', 'source_photo': 'delivered_to_stub',
                        'missing_currency': 'asked', 'allergy_edit': 'preserved', 'diet_conflict': 'rejected',
                        'recipes': [{'url': r['url'], 'servings': r['servings']} for r in plan['recipes']],
                        'telegram_delivery': 'simulated'}
                finally:
                    store.close()
            if args.stage == 'intake':
                # Real vision/ASR/LLM, temporary state and simulated Telegram delivery.
                store = Store(directory / 'intake.sqlite')
                class Telegram:
                    messages = []
                    async def call(self, method, **payload):
                        self.messages.append((method, payload))
                        return {'message_id': len(self.messages)}
                    async def download(self, file_id, target, *args):
                        target.write_bytes(Path(file_id).read_bytes())
                telegram = Telegram()
                bot = Bot(replace(config, data_dir=directory), store, telegram, groq, luna, web)
                try:
                    for i, label in enumerate(['EGGS', 'TOMATOES'], 1):
                        path = directory / f'food-{i}.jpg'
                        picture = Image.new('RGB', (600, 400), '#ddd7cc')
                        ImageDraw.Draw(picture).text((60, 160), label, fill='#222222', font_size=50)
                        picture.save(path)
                        store.ingest(i, 1, {'kind': 'photo', 'file_id': str(path),
                                            'unique': str(i), 'caption': 'Тестовая карточка продукта'})
                        job = store.claim()
                        await bot.handle(job)
                        store.finish(job)
                    inventory = store.session(1)['inventory']
                    assert any('яй' in x['name'].lower() for x in inventory), 'lost_eggs'
                    assert any('помидор' in x['name'].lower() or 'томат' in x['name'].lower()
                               for x in inventory), 'missing_tomatoes'
                    audio = directory / 'intake.wav'
                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'flite=text=Add rice.:voice=slt', str(audio),
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    assert await process.wait() == 0
                    store.ingest(3, 1, {'kind': 'voice', 'file_id': str(audio), 'duration': 3})
                    job = store.claim()
                    await bot.handle(job)
                    store.finish(job)
                    assert 'rice' in job['cache']['transcript'].lower(), 'missing_transcript'
                    assert any('рис' in x['name'].lower()
                               for x in store.session(1)['inventory']), 'missing_rice'
                    sends = [p for method, p in telegram.messages if method == 'sendMessage']
                    assert len(sends) == 4, 'unexpected_delivery_order'
                    assert sends[2]['text'] == 'Слушаю голосовое…'
                    assert '<b>Твои продукты</b>' in sends[3]['text']
                    report['intake'] = {'auto_photos': 2, 'merged': True,
                                        'voice_transcript_before_result': True,
                                        'telegram_delivery': 'simulated'}
                finally:
                    store.close()
            if args.stage == 'recipe':
                data, url = await web.get(SOURCE_SAMPLES['budgetbytes.com'], recipe_page=True)
                recipe = extract_recipe(data.decode('utf-8', errors='replace'), url)
                store = Store(directory / 'smoke.sqlite')
                try:
                    state = store.session(1)
                    state['recipes'] = [recipe]
                    with store.db:
                        store.save(1, state)
                    payload = {'kind': 'callback', 'data': f"h:{state['rev']}:pick:0"}
                    class Model:
                        calls = 0
                        async def ask(self, *args, **kwargs):
                            self.calls += 1
                            for update in range(2, 12):
                                assert store.ingest(update, 1, {**payload, 'callback_id': str(update)})
                            return await luna.ask(*args, **kwargs)
                    class Telegram:
                        messages = []
                        async def call(self, method, **payload):
                            assert method in {'sendMessage', 'editMessageText'}, 'unexpected_telegram_operation'
                            self.messages.append(payload['text'])
                            return {'message_id': len(self.messages)}
                    model, telegram = Model(), Telegram()
                    # Use a temporary work directory; never send messages or touch user state.
                    bot = Bot(replace(config, data_dir=directory), store, telegram, None, model, web)
                    store.ingest(1, 1, payload)
                    job = store.claim()
                    await bot.handle(job)
                    store.finish(job)
                    assert store.claim() is None, 'duplicate_recipe_job'
                    assert job['cache']['selected']['translation_status'] == 'translated', 'translation_fallback'
                    text = '\n'.join(telegram.messages)
                    assert '\n\n2. ' in text, 'missing_step_spacing'
                    assert text.count('>Источник</a>') == 1, 'duplicate_recipe'
                    assert telegram.messages[0] == 'Загружаю рецепт…'
                    assert '<b>Ингредиенты:</b>' in text and '<b>Как приготовить:</b>' in text
                    calls = model.calls
                    store.ingest(12, 1, {'kind': 'callback', 'data': f"h:{state['rev']}:choices"})
                    choices_job = store.claim()
                    await bot.handle(choices_job)
                    store.finish(choices_job)
                    assert model.calls == calls, 'choices_called_model'
                    assert telegram.messages[-1].startswith('Подходящие блюда:')
                    report['recipe'] = {'clicks': 11, 'recipes_sent': 1, 'luna_calls': model.calls,
                                        'step_spacing': 'ok', 'translation_status': 'translated'}
                finally:
                    store.close()
            if args.stage in {'sources', 'all'}:
                report['sources'] = []
                for domain, url in SOURCE_SAMPLES.items():
                    data, final_url = await web.get(url, recipe_page=True)
                    assert recipe_source(final_url) == domain, 'source_changed_domain'
                    recipe = extract_recipe(data.decode('utf-8', errors='replace'), final_url)
                    data, _ = await web.get(recipe['image_url'], limit=8_000_000, image=True)
                    original, picture = directory / 'source-image', directory / 'verified.jpg'
                    original.write_bytes(data)
                    prepare_image(original, picture, 1200)
                    report['sources'].append({'domain': domain, 'url': final_url,
                        'ingredients': len(recipe['ingredients']), 'steps': len(recipe['steps']),
                        'photo_bytes': picture.stat().st_size})
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
                assert all(recipe_source(r['url']) for r in found), 'unexpected_recipe_source'
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
    parser.add_argument('--stage', choices=['vision', 'asr', 'sources', 'search', 'recipe', 'intake', 'planning', 'all'], default='all')
    asyncio.run(run(parser.parse_args()))
