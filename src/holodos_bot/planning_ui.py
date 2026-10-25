"""Telegram views for the persistent profile and meal planner."""
import html

from .navigation import footer
from .planning import (
    PROFILE_PROMPT,
    PROFILE_SCHEMA,
    MealPlanner,
    alternatives,
    amount,
    fingerprint,
    multiplier,
    number,
    profile_question,
    replace_meal,
)
from .providers import ProviderError, prepare_image

INTRO = ("Расскажи о себе текстом или голосом: сколько человек, приёмов пищи в день, бюджет на всех "
         "с валютой и периодом, город или страна, любимые блюда и ограничения питания.\n\n"
         "Например: «Нас двое, 3 приёма пищи, 100 EUR в неделю, Испания. Любим овощи и курицу, "
         "без молока». Если ограничений или предпочтений нет, так и напиши.")


def button(state, text, action):
    return {"text": text, "callback_data": f"h:{state['rev']}:{action}"}


def markup(state, rows, back="home"):
    return footer(state, [[button(state, text, action) for text, action in row] for row in rows], back)


def profile_text(profile):
    periods = {1: "день", 7: "неделю", 14: "две недели", 30: "месяц (30 дней)"}
    return (f"Людей: {profile['people']} · приёмов пищи: {profile['meals']} в день\n"
            f"Бюджет на всех: {number(profile['budget'])} {profile['currency']} в {periods[profile['budget_days']]}\n"
            f"Покупки: {profile['region']}\nПредпочтения: {profile['preferences']}\n"
            f"Ограничения: {profile['restrictions']}")


def nutrition_text(n):
    return f"≈ {round(n['kcal'])} ккал · Б {number(n['protein'])} · Ж {number(n['fat'])} · У {number(n['carbs'])} г"


def shopping_text(plan):
    parts = [f"Покупки на {plan['days']} дн. · {plan['people']} чел.", ""]
    buy, check = [], []
    for row in plan['shopping']:
        if row['check']:
            if row['unit']:
                check.append(f"• {row['name']}: нужно ещё до {amount(row['missing'], row['unit'])}; уточни запас")
            else:
                sources = "; ".join(f"{s['text']} × {multiplier(s['factor'])}" for s in row['sources'])
                check.append(f"• {row['name']}: {sources}. Уточни количество по рецепту и запас.")
        elif row['missing'] > 1e-7:
            buy.append(f"• {row['name']} — {amount(row['missing'], row['unit'])}")
    parts.extend(["Докупить:", *(buy or ["По известным количествам докупать не нужно."])])
    if check:
        parts.extend(["", "Уточнить перед покупкой:", *check,
                      "Неизвестные запасы не вычитал. Уточни их в «Мои продукты» и обнови план."])
    parts.extend(["", f"Оценка покупок: ≈ {number(plan['estimate'])} {plan['currency']}",
                  f"Бюджет на этот срок: {number(round(plan['budget'], 2))} {plan['currency']}",
                  "Стоимость примерная, за нужное количество продуктов. Упаковки и цены магазина могут увеличить чек."])
    return "\n".join(parts)


class PlanningUI:
    def __init__(self, bot, luna, web):
        self.bot, self.luna, self.web = bot, luna, web
        self.engine = MealPlanner(luna, web)

    def enter(self, job, state, edit=False):
        state.pop('refine_position', None)
        state['mode'] = 'plan'
        if edit or (not state.get('profile') and state.get('profile_draft') is None):
            state['profile_draft'] = dict(state.get('profile') or {})
        state['rev'] += 1
        self.bot.apply(job, state, 'planning')

    async def text(self, job, state, text):
        if state.get('mode') != 'plan':
            return False
        if state.get('refine_position') is not None:
            position = state['refine_position']
            async def remember(key, operation):
                return await self.bot.cached(job, 'refine:' + key, operation)
            async def progress(message):
                await self.bot.panel(job, state, message, {'inline_keyboard': []})
            try:
                updated = await self.bot.cached(job, 'refined_plan', lambda: self.engine.refine(
                    state['meal_plan'], state['profile'], state['inventory'], position, text,
                    self.bot.work / str(job['id']), remember, progress))
            except ProviderError as error:
                if str(error) not in {'plan_incomplete', 'plan_over_budget', 'plan_stock_changed', 'plan_legacy'}:
                    raise
                await self.bot.say(job, 'Не нашёл подходящих проверенных вариантов в рамках бюджета. '
                    'Попробуй другое пожелание. Если запасы изменились — обнови меню.', 'refine_unavailable')
                self.bot.apply(job, state, 'plan_refine')
                return True
            state['meal_plan'], state['plan_slot'] = updated, position
            state.pop('refine_position', None)
            state['rev'] += 1
            self.bot.apply(job, state, 'plan_alternatives')
            return True
        draft = state.get('profile_draft')
        if draft is None:
            draft = state.get('profile') or {}
        result = await self.bot.cached(job, 'profile_parse', lambda: self.luna.ask(
            PROFILE_PROMPT, PROFILE_SCHEMA, {'profile': draft, 'message': text, 'question': profile_question(draft)}))
        state['profile_draft'], state['panel'] = result, None
        state['rev'] += 1
        self.bot.apply(job, state, 'planning')
        return True

    async def callback(self, job, state, action, argument, directory, choice=None):
        bot = self.bot
        state.pop("refine_position", None)
        if action == 'profileback':
            state['profile_draft'] = None
            self.enter(job, state)
        elif action in {'plan', 'profile'}:
            self.enter(job, state, edit=action == 'profile')
        elif action == 'home':
            state['mode'], state['profile_draft'] = 'home', None
            state['rev'] += 1
            bot.apply(job, state, 'home')
        elif action == 'saveprofile':
            draft = state.get('profile_draft')
            if draft and not profile_question(draft):
                if draft != state.get('profile'):
                    state['meal_plan'] = None
                state['profile'], state['profile_draft'] = draft, None
                state['rev'] += 1
            bot.apply(job, state, 'planning')
        elif action == 'build':
            profile = state.get('profile')
            if not profile or state.get('profile_draft') is not None:
                bot.apply(job, state, 'planning')
                return True
            days = int(argument or 0)
            if days not in range(1, 15):
                return True
            if not await bot.limit(job):
                return True
            state['mode'] = 'plan'
            async def progress(text):
                await bot.panel(job, state, text, {'inline_keyboard': []})
            async def remember(key, operation):
                return await bot.cached(job, key, operation)
            try:
                plan = await bot.cached(job, 'meal_plan', lambda: self.engine.generate(
                    profile, state['inventory'], days, directory, remember, progress))
            except ProviderError as error:
                if str(error) not in {'plan_incomplete', 'plan_over_budget'}:
                    raise
                text = ("Не удалось уложить полный план в этот бюджет. Попробуй изменить бюджет или пожелания."
                        if str(error) == 'plan_over_budget' else
                        "Не нашёл проверенные рецепты для всех приёмов пищи с твоими ограничениями. "
                        "Попробуй ещё раз или уточни профиль.")
                await bot.say(job, text, 'plan_unavailable')
                bot.apply(job, state, 'planning')
                return True
            state['meal_plan'], state['plan_day'] = plan, 0
            state['rev'] += 1
            bot.apply(job, state, 'plan_day')
        elif action in {'slot', 'alternatives', 'replace', 'refine'}:
            plan = state.get('meal_plan')
            if not plan:
                bot.apply(job, state, 'planning')
                return True
            position = int(argument if argument is not None else -1)
            day, slot = divmod(position, len(plan['labels']))
            if not 0 <= day < plan['days']:
                return True
            state['plan_day'], state['plan_slot'] = day, position
            if action == 'refine':
                state['mode'], state['refine_position'] = 'plan', position
                state['rev'] += 1
                bot.apply(job, state, 'plan_refine')
            elif action == 'replace':
                index = int(choice if choice is not None else -1)
                try:
                    updated = replace_meal(plan, position, index, state['inventory'])
                except ProviderError as error:
                    if str(error) == 'plan_invalid_replacement':
                        return True
                    await bot.say(job, 'Эта замена сейчас недоступна: изменились запасы или не хватает бюджета.',
                                  'replacement_unavailable')
                    bot.apply(job, state, 'plan_alternatives')
                    return True
                state['meal_plan'] = updated
                state['rev'] += 1
                bot.apply(job, state, 'plan_day')
            else:
                bot.apply(job, state, 'plan_slot' if action == 'slot' else 'plan_alternatives')
        elif action in {'shopping', 'day', 'page', 'meal'}:
            plan = state.get('meal_plan')
            if not plan:
                bot.apply(job, state, 'planning')
                return True
            if action == 'shopping':
                bot.apply(job, state, 'shopping')
            elif action in {'day', 'page'}:
                day = int(argument or 0)
                if 0 <= day < plan['days']:
                    state['plan_day'] = day
                    bot.apply(job, state, 'plan_day')
            else:
                index = int(argument if argument is not None else -1)
                if not 0 <= index < len(plan['recipes']):
                    return True
                recipe = plan['recipes'][index]
                if 'rendered' not in recipe and not await bot.limit(job):
                    return True
                progress = await bot.say(job, 'Загружаю рецепт…', 'recipe_progress', {'inline_keyboard': []})
                bot.store.checkpoint(job, 'recipe_panel', progress['message_id'])
                translated = recipe.get('rendered') or await bot.cached(job, 'translation', lambda: bot.recipes.render(recipe))
                if translated.get('translation_status') == 'translated':
                    recipe['rendered'] = translated
                bot.store.checkpoint(job, 'selected', {**recipe, **translated, 'plan_people': plan['people']})
                bot.apply(job, state, 'recipe')
        else:
            return False
        return True

    async def show(self, job, state, view):
        if view == 'home':
            await self.bot.panel(job, state,
                'Что приготовим?\n\nМогу подобрать блюда из твоих продуктов или составить меню и список покупок.',
                {"inline_keyboard": [[button(state, "Из холодильника", "list")],
                                     [button(state, "План питания", "plan")]]})
            return
        plan = state.get('meal_plan')
        back = 'home'
        if view in {'shopping', 'plan_day', 'plan_slot', 'plan_alternatives', 'plan_refine'} and plan:
            fresh = plan['inventory_hash'] == fingerprint(state['inventory'])
            if view in {'plan_slot', 'plan_alternatives', 'plan_refine'}:
                position = state.get('plan_slot', 0)
                day, slot = divmod(position, len(plan['labels']))
                current = plan['schedule'][day][slot]
                recipe = plan['recipes'][current]
                text = (f"День {day + 1} · {plan['labels'][slot]}\n{recipe['title_ru']}\n"
                        + nutrition_text(recipe['nutrition_estimate']))
                rows = []
                if view == 'plan_refine':
                    text += ('\n\nНапиши или пришли голосовое: что хочешь на этот приём пищи? '
                             'Например: «Хочу несладкую кашу, без яиц».\n'
                             'Найду варианты для этого дня. Ограничения профиля сохраняются. '
                             'Блюдо поменяется, когда выберешь замену.')
                elif view == 'plan_slot':
                    rows.append([('Открыть рецепт', f'meal:{current}')])
                    if all('items' in r for r in plan['recipes']):
                        rows.append([('Выбрать другое блюдо', f'alternatives:{position}')])
                    else:
                        text += '\n\nДля замены блюд в этом старом плане составь новое меню.'
                        rows.append([('Составить новое меню', f"build:{plan['days']}")])
                elif not fresh:
                    text += '\n\nЗапасы изменились. Обнови план перед заменой блюд.'
                    rows.append([('Обновить план', f"build:{plan['days']}")])
                else:
                    options = alternatives(plan, position, state['inventory'])
                    refinement = plan.get('refinements', {}).get(str(position))
                    if refinement:
                        options = [(i, cost) for i, cost in options if i in refinement['indices']]
                        text += '\n\nТвоё пожелание: ' + refinement['wish']
                    text += '\n\nВыбери замену только для этого приёма пищи:' if options else (
                        '\n\nДругих проверенных вариантов в рамках бюджета пока нет.')
                    for index, estimate in options:
                        r = plan['recipes'][index]
                        text += f"\n• {r['title_ru']} — {nutrition_text(r['nutrition_estimate'])}"
                        rows.append([(r['title_ru'][:64], f'replace:{position}:{index}')])
                    if not options:
                        rows.append([('Составить новое меню', f"build:{plan['days']}")])
                if fresh and all('items' in r for r in plan['recipes']) and view != 'plan_refine':
                    rows.append([('Уточнить предпочтения', f'refine:{position}')])
                rows.append([('Что докупить', 'shopping')])
                back = f'day:{day}' if view == 'plan_slot' else f'slot:{position}'
            elif view == 'shopping':
                text = shopping_text(plan) if fresh else (
                    'Список продуктов изменился. Обнови план, чтобы пересчитать недостающие покупки.')
                rows = []
                back = f"day:{state.get('plan_day', 0)}"
                if not fresh:
                    rows.insert(0, [('Обновить план и покупки', f"build:{plan['days']}")])
                rows.append([('Мои продукты', 'list')])
            else:
                day = min(state.get('plan_day', 0), plan['days'] - 1)
                parts, rows = [f"День {day + 1} из {plan['days']} · на {plan['people']} чел.",
                               'Нажми на блюдо: открыть рецепт или выбрать другое.\n'
                               'КБЖУ примерные, на одну порцию каждого блюда.'], []
                total = dict.fromkeys(['kcal', 'protein', 'fat', 'carbs'], 0)
                for slot, index in enumerate(plan['schedule'][day]):
                    recipe = plan['recipes'][index]
                    parts.extend(['', plan['labels'][slot] + ': ' + recipe['title_ru'],
                                  nutrition_text(recipe['nutrition_estimate'])])
                    for key in total:
                        total[key] += recipe['nutrition_estimate'][key]
                    rows.append([(f"{plan['labels'][slot]}: {recipe['title_ru']}"[:64], f'slot:{day * len(plan["labels"]) + slot}')])
                parts.extend(['', 'За день на человека: ' + nutrition_text(total)])
                if not fresh:
                    parts.extend(['', 'Запасы изменились — покупки нужно пересчитать.'])
                nav = []
                if day:
                    nav.append(('← День ' + str(day), f'page:{day - 1}'))
                if day + 1 < plan['days']:
                    nav.append(('День ' + str(day + 2) + ' →', f'page:{day + 1}'))
                if nav:
                    rows.append(nav)
                rows.append([('Что докупить', 'shopping')])
                back = 'plan'
                text = '\n'.join(parts)
        else:
            draft, profile = state.get('profile_draft'), state.get('profile')
            if draft is not None or not profile:
                back = 'profileback' if profile else 'home'
                question = profile_question(draft or {})
                if not draft:
                    text = INTRO
                elif question:
                    text = question
                else:
                    text = 'Всё верно?\n\n' + profile_text(draft) + '\n\nПоправь текстом или сохрани профиль.'
                rows = [[('Сохранить профиль', 'saveprofile')]] if draft and not question else []
            else:
                text = ('Твой план питания\n\n' + profile_text(profile)
                        + '\n\nНа какой срок составить меню? Сначала выберем блюда, потом составим покупки.')
                rows = [[('На день', 'build:1'), ('На неделю', 'build:7')],
                        [('Изменить профиль', 'profile')]]
                if plan:
                    rows.insert(0, [('Моё меню', 'day:0'), ('Что докупить', 'shopping')])
            rows.append([('Мои продукты', 'list')])
        await self.bot.panel(job, state, text, markup(state, rows, back))

    async def photo(self, job, recipe):
        async def send():
            directory = self.bot.work / str(job['id'])
            original, target = directory / 'selected-source', directory / 'selected.jpg'
            data, _ = await self.web.get(recipe['image_url'], limit=8_000_000, image=True)
            original.write_bytes(data)
            prepare_image(original, target, 1200)
            caption = html.escape(recipe['title_ru']) + '\n' + (
                f'<a href="{html.escape(recipe["url"], quote=True)}">Источник</a>')
            return await self.bot.telegram.album(job['owner'], [(target, caption)])
        await self.bot.effect(job, 'plan_recipe_photo', send)
