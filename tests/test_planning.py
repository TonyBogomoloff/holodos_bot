import asyncio
import copy
import io
import json

import pytest
from PIL import Image

from holodos_bot.bot import Bot
from holodos_bot.config import Config
from holodos_bot.planning import (
    ANALYSIS_PROMPT,
    AUDIT_PROMPT,
    PLAN_SEARCH_PROMPT,
    PROFILE_PROMPT,
    MealPlanner,
    alternatives,
    analyze_recipes,
    assemble,
    quantity,
    replace_meal,
    servings,
    shopping_list,
)
from holodos_bot.planning_ui import shopping_text
from holodos_bot.providers import ProviderError
from holodos_bot.store import Store

PROFILE = {'people': 2, 'meals': 3, 'budget': 100, 'budget_days': 7, 'currency': 'EUR',
           'region': 'Испания', 'preferences': 'простые блюда', 'restrictions': 'нет', 'clarification': ''}
INVENTORY = [{'name': 'рис', 'quantity': '0,5 кг', 'uncertain': False},
             {'name': 'яйца', 'quantity': '', 'uncertain': False}]
NUTRITION = {'kcal': 200, 'protein': 10, 'fat': 10, 'carbs': 20}


class Web:
    def __init__(self, photo=True):
        self.seen, self.photo = [], photo
        stream = io.BytesIO()
        Image.new('RGB', (32, 32)).save(stream, format='JPEG')
        self.image = stream.getvalue()

    async def get(self, url, **kwargs):
        self.seen.append(url)
        if kwargs.get('image'):
            if not self.photo:
                raise ProviderError('source_unavailable')
            return self.image, url
        rice = url.endswith('/rice/')
        source = {'@type': 'Recipe', 'name': 'Rice' if rice else 'Eggs', 'recipeYield': '2 servings',
                  'recipeIngredient': ['200 g rice'] if rice else ['2 eggs'],
                  'recipeInstructions': ['Cook for 10 minutes.'], 'image': '/dish.jpg'}
        return ('<script type="application/ld+json">' + json.dumps(source) + '</script>').encode(), url


class Model:
    def __init__(self, safe=True):
        self.calls, self.safe = [], safe

    async def ask(self, prompt, schema, payload, **kwargs):
        self.calls.append(prompt)
        if prompt == PROFILE_PROMPT:
            return copy.deepcopy(PROFILE)
        if prompt == PLAN_SEARCH_PROMPT:
            assert kwargs['search'] is True
            return {'urls': ['https://www.budgetbytes.com/eggs/', 'https://www.budgetbytes.com/rice/',
                             'https://unapproved.test/injected/']}
        if prompt == ANALYSIS_PROMPT:
            return {'recipes': [{'id': i, 'suitable': True, 'title_ru': 'Рис' if r['title'] == 'Rice' else 'Яйца',
                                 'slots': [1, 2] if r['title'] == 'Rice' else [0],
                                 'nutrition': NUTRITION,
                                 'ingredients': [{'index': 0, 'name': 'рис' if r['title'] == 'Rice' else 'яйца',
                                                  'inventory': [0 if r['title'] == 'Rice' else 1]
                                                  if payload['inventory'] else [], 'cost': 1}]}
                                for i, r in enumerate(payload['recipes'])]}
        if prompt == AUDIT_PROMPT:
            return {'recipes': [{'id': r['id'], 'safe': self.safe} for r in payload['recipes']]}
        return {'ingredients': ['2 яйца'], 'steps': ['Готовить 10 минут.']}


class Telegram:
    def __init__(self):
        self.sent, self.photos = [], []

    async def call(self, method, **payload):
        self.sent.append((method, payload))
        return {'message_id': len(self.sent)}

    async def album(self, owner, pictures):
        self.photos.extend(pictures)
        return [{'message_id': 1000}]


async def generate(tmp_path, inventory=INVENTORY, days=7, model=None, web=None):
    async def remember(key, operation):
        return await operation()
    async def progress(text):
        pass
    return await MealPlanner(model or Model(), web or Web()).generate(
        PROFILE, inventory, days, tmp_path, remember, progress)


def test_plan_complete_scaled_to_household_stock_used_once_and_unknown_not_invented(tmp_path):
    web = Web()
    plan = asyncio.run(generate(tmp_path, web=web))
    assert len(plan['schedule']) == 7 and all(len(day) == 3 for day in plan['schedule'])
    assert plan['budget'] == 100
    rice, eggs = sorted(plan['shopping'], key=lambda r: r['name'])
    assert rice['name'] == 'рис' and rice['required'] == 2800 and rice['missing'] == 2300
    assert not rice['check']
    assert eggs['required'] == 14 and eggs['missing'] == 14 and eggs['check']
    assert all('unapproved' not in url for url in web.seen)
    assert all(r['url'].startswith('https://www.budgetbytes.com/') for r in plan['recipes'])
    assert len([url for url in web.seen if url.endswith('.jpg')]) == 2
    assert 'уточни запас' in shopping_text(plan)
    assert '≈' in shopping_text(plan)


@pytest.mark.parametrize('photo,safe', [(False, True), (True, False)])
def test_unverified_photo_or_failed_diet_audit_cannot_produce_a_plan(tmp_path, photo, safe):
    with pytest.raises(ProviderError, match='plan_incomplete'):
        asyncio.run(generate(tmp_path, model=Model(safe), web=Web(photo)))


def test_no_fridge_needed_and_one_day_budget_is_prorated(tmp_path):
    plan = asyncio.run(generate(tmp_path, inventory=[], days=1))
    assert plan['budget'] == pytest.approx(100 / 7)
    assert all(row['missing'] == row['required'] for row in plan['shopping'])
    assert all(not row['check'] for row in plan['shopping'])


def test_omitted_ingredients_and_out_of_range_stock_are_rejected():
    source = {'title': 'rice', 'ingredients': ['200 g rice', '2 eggs'], 'servings': 2}
    entry = {'id': 0, 'suitable': True, 'slots': [0], 'title_ru': 'рис', 'nutrition': NUTRITION,
             'ingredients': [{'index': 0, 'name': 'рис', 'inventory': [0], 'cost': 1}]}
    assert analyze_recipes({'recipes': [entry]}, [source], INVENTORY, 3) == []
    entry['ingredients'].append({'index': 1, 'name': 'яйца', 'inventory': [9], 'cost': 1})
    assert analyze_recipes({'recipes': [entry]}, [source], INVENTORY, 3) == []


def test_stock_aliases_never_spend_the_same_stock_twice_and_budget_cannot_be_ignored():
    recipe = {'title': 'Rice', 'url': 'https://www.budgetbytes.com/rice/', 'servings': 2,
              'ingredients': ['200 g rice'], 'slots': [0, 1, 2], 'items': [
                  {'index': 0, 'name': 'рис', 'quantity': quantity('200 g'), 'cost': 10, 'inventory': [0]}]}
    alias = copy.deepcopy(recipe)
    alias['items'][0]['name'] = 'длиннозёрный рис'
    rows = shopping_list([recipe, alias], [[0, 1, 0]], 2, INVENTORY)
    assert sum(r['missing'] for r in rows) == 100
    with pytest.raises(ProviderError, match='plan_over_budget'):
        assemble([recipe], {**PROFILE, 'budget': 1}, [], 7)


@pytest.mark.parametrize('source,expected', [
    ('1½ cups rice', {'amount': 1.5, 'unit': 'чашка'}),
    ('500 мл', {'amount': 500, 'unit': 'мл'}), ('1/2 ч. л.', {'amount': .5, 'unit': 'ч. л.'}),
    ('2 (400 g) cans tomatoes', None), ('1-2 eggs', None), ('соль по вкусу', None),
    ('1/0 tsp', None), ('1 fl oz milk', None),
])
def test_quantities_are_conservative(source, expected):
    assert quantity(source) == expected
    assert servings('1 loaf') is None


def test_profile_plan_navigation_retry_persistence_and_deletion(tmp_path):
    async def run():
        store, telegram, model, web = Store(tmp_path / 'db.sqlite'), Telegram(), Model(), Web()
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, model, web)
        seq = 0
        async def handle(text=None, action=None):
            nonlocal seq
            seq += 1
            payload = {'kind': 'text', 'text': text} if text is not None else {
                'kind': 'callback', 'data': f"h:{store.session(1)['rev']}:{action}"}
            store.ingest(seq, 1, payload)
            job = store.claim()
            await bot.handle(job)
            store.finish(job)
            return job
        await handle('/start')
        assert [row[0]['text'] for row in telegram.sent[-1][1]['reply_markup']['inline_keyboard']] == [
            'Из холодильника', 'План питания']
        await handle(action='plan')
        await handle('Нас двое, 3 приёма пищи, 100 евро в неделю, Испания, простые блюда, без ограничений')
        assert store.session(1)['profile'] is None  # Explicit in-bot confirmation.
        assert store.session(1)['inventory'] == []
        await handle(action='saveprofile')
        assert store.session(1)['profile'] == PROFILE
        build = await handle(action='build:7')
        plan = store.session(1)['meal_plan']
        assert telegram.sent[-1][1]['text'].startswith('День 1 из 7')
        calls = len(model.calls)
        sent = len(telegram.sent)
        await bot.handle(build)
        assert len(model.calls) == calls and len(telegram.sent) == sent
        for action in ['day:99', 'meal:99', 'build:99']:
            await handle(action=action)
        assert len(telegram.sent) == sent and len(model.calls) == calls
        await handle(action='day:6')
        assert 'День 7 из 7' in telegram.sent[-1][1]['text']
        assert 'на одну порцию' in telegram.sent[-1][1]['text']
        await handle(action='meal:0')
        assert telegram.photos and '>Источник</a>' in telegram.photos[0][1]
        assert 'На одну порцию' in telegram.sent[-1][1]['text']
        await handle('/new')
        assert store.session(1)['profile'] == PROFILE
        with store.db:
            store.db.execute('UPDATE sessions SET updated=0')
            store.db.execute('UPDATE jobs SET created=0')
        store.cleanup(24)
        assert store.session(1)['profile'] == PROFILE
        assert store.session(1)['meal_plan']['schedule'] == plan['schedule']
        assert store.session(2)['profile'] is None
        # A changed inventory cannot expose old purchase deficits as current.
        state = store.session(1)
        state['inventory'] = copy.deepcopy(INVENTORY)
        with store.db:
            store.save(1, state)
        await handle(action='shopping')
        assert 'Список продуктов изменился' in telegram.sent[-1][1]['text']
        await handle('/delete')
        assert store.session(1)['profile'] is None and store.session(1)['meal_plan'] is None
        assert store.db.execute('SELECT count(*) FROM planning').fetchone()[0] == 0
    asyncio.run(run())


def test_profile_clarification_keeps_inventory_and_cannot_be_confirmed(tmp_path):
    async def run():
        store, telegram = Store(tmp_path / 'db.sqlite'), Telegram()
        state = store.session(1)
        state.update(mode='plan', inventory=copy.deepcopy(INVENTORY), profile=copy.deepcopy(PROFILE))
        with store.db:
            store.save(1, state)
        class Ambiguous:
            async def ask(self, prompt, schema, payload, **kwargs):
                assert prompt == PROFILE_PROMPT
                return {**PROFILE, 'clarification': 'Что меняем на пять: людей или приёмы пищи?'}
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, Ambiguous(), None)
        store.ingest(1, 1, {'kind': 'voice', 'duration': 3, 'file_id': 'test'})
        job = store.claim()
        store.checkpoint(job, 'transcript', 'Поменяй на пять')
        await bot.handle(job)
        store.finish(job)
        assert store.session(1)['inventory'] == INVENTORY
        assert store.session(1)['profile'] == PROFILE
        assert telegram.sent[-1][1]['text'].startswith('Что меняем на пять')
        assert 'Сохранить профиль' not in str(telegram.sent[-1][1]['reply_markup'])
        for i, action in enumerate(['saveprofile', 'build:7', 'day:99', 'meal:99', 'build:99'], 2):
            store.ingest(i, 1, {'kind': 'callback', 'data': f"h:{store.session(1)['rev']}:{action}"})
            job = store.claim()
            await bot.handle(job)
            store.finish(job)
        assert store.session(1)['profile'] == PROFILE and store.session(1)['meal_plan'] is None
    asyncio.run(run())


def test_plan_generation_burst_coalesces_through_restart(tmp_path):
    path = tmp_path / 'db.sqlite'
    store = Store(path)
    payload = {'kind': 'callback', 'data': 'h:10:build:7'}
    store.ingest(1, 1, payload)
    store.claim()
    store.close()
    store = Store(path)
    store.recover()
    for update in range(2, 10):
        assert store.ingest(update, 1, payload)
    assert store.stats() == {'pending': 1}


def test_single_meal_replacement_reprices_stock_and_rejects_invalid_choices(tmp_path):
    plan = asyncio.run(generate(tmp_path))
    rice = next(i for i, r in enumerate(plan['recipes']) if r['title_ru'] == 'Рис')
    replacement = copy.deepcopy(plan['recipes'][rice])
    replacement.update(title_ru='Гречка', url='https://www.budgetbytes.com/buckwheat/', slots=[1])
    replacement['items'][0].update(name='гречка', inventory=[])
    plan['recipes'].append(replacement)
    chosen = len(plan['recipes']) - 1
    before = copy.deepcopy(plan)
    changed = replace_meal(plan, 1, chosen, INVENTORY)
    assert plan == before
    assert changed['schedule'][0] == [plan['schedule'][0][0], chosen, rice]
    assert changed['schedule'][1:] == plan['schedule'][1:]
    purchases = {r['name']: r for r in changed['shopping']}
    assert purchases['рис']['required'] == 2600 and purchases['рис']['missing'] == 2100
    assert purchases['гречка']['required'] == purchases['гречка']['missing'] == 200
    assert changed['estimate'] == round(sum(r['estimate'] for r in changed['shopping']), 2)
    assert chosen in dict(alternatives(plan, 1, INVENTORY))
    for position, stock, reason in [(0, INVENTORY, 'plan_invalid_replacement'),
                                    (21, INVENTORY, 'plan_invalid_replacement'),
                                    (1, [], 'plan_stock_changed')]:
        with pytest.raises(ProviderError, match=reason):
            replace_meal(plan, position, chosen, stock)
    plan['recipes'][chosen]['items'][0]['cost'] = 1000
    with pytest.raises(ProviderError, match='plan_over_budget'):
        replace_meal(plan, 1, chosen, INVENTORY)
    assert alternatives(plan, 1, INVENTORY) == []


def test_menu_alternatives_replace_retry_and_shopping_without_new_search(tmp_path):
    async def run():
        plan = await generate(tmp_path, inventory=[])
        extra = copy.deepcopy(plan['recipes'][0])
        extra.update(title_ru='Другой завтрак', url='https://www.budgetbytes.com/another/')
        extra['items'][0].update(name='новый продукт', inventory=[])
        plan['recipes'].append(extra)
        # Retain even unused candidates when assembling a short plan.
        short = assemble(plan['recipes'], PROFILE, [], 1)
        assert len(short['recipes']) == 3 and all('items' in r for r in short['recipes'])
        store, telegram, model = Store(tmp_path / 'replacement.sqlite'), Telegram(), Model()
        state = store.session(1)
        state.update(profile=PROFILE, meal_plan=plan, mode='plan')
        with store.db:
            store.save(1, state)
        bot = Bot(Config('dummy:token', 'key', tmp_path), store, telegram, None, model, Web())
        seq = 0
        async def action(value, rev=None):
            nonlocal seq
            seq += 1
            current = store.session(1)['rev'] if rev is None else rev
            store.ingest(seq, 1, {'kind': 'callback', 'data': f'h:{current}:{value}'})
            job = store.claim()
            await bot.handle(job)
            store.finish(job)
            return job
        await action('day:0')
        assert ':slot:0' in str(telegram.sent[-1])
        await action('slot:0')
        assert 'Открыть рецепт' in str(telegram.sent[-1]) and 'Выбрать другое' in str(telegram.sent[-1])
        await action('alternatives:0')
        assert 'Другой завтрак' in str(telegram.sent[-1])
        old_rev = store.session(1)['rev']
        job = await action('replace:0:2')
        changed = copy.deepcopy(store.session(1)['meal_plan'])
        assert changed['schedule'][0][0] == 2
        assert changed['schedule'][1:] == plan['schedule'][1:]
        assert telegram.sent[-1][1]['text'].startswith('День 1 из 7')
        sent = len(telegram.sent)
        await bot.handle(job)
        assert len(telegram.sent) == sent
        await action('replace:0:0', rev=old_rev)
        assert store.session(1)['meal_plan'] == changed
        await action('shopping')
        assert telegram.sent[-1][1]['text'].startswith('Покупки на 7')
        assert 'новый продукт' in telegram.sent[-1][1]['text']
        assert not model.calls
        legacy = store.session(1)
        for recipe in legacy['meal_plan']['recipes']:
            recipe.pop('items')
            recipe.pop('slots')
        with store.db:
            store.save(1, legacy)
        await action('slot:0')
        assert 'старом плане' in telegram.sent[-1][1]['text']
        assert 'Открыть рецепт' in str(telegram.sent[-1])
        store.close()
    asyncio.run(run())
