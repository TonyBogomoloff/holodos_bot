import copy

from holodos_bot.pantry import with_staples
from holodos_bot.planning import analyze_recipes, shopping_list
from holodos_bot.recipes import validated_matches


def test_staples_cover_recipe_without_polluting_fridge_or_allowing_unrelated_dishes():
    fridge = [{'name': 'яйца', 'quantity': '2 шт', 'uncertain': False}]
    before = copy.deepcopy(fridge)
    available = with_staples(fridge)
    recipe = {'title': 'Яйца', 'ingredients': ['2 eggs', 'salt']}
    match = {'id': 0, 'title_ru': 'Яйца', 'suitable': True, 'coverage': [
        {'ingredient': i, 'inventory': [i], 'missing': '', 'optional': False} for i in range(2)]}
    assert validated_matches({'matches': [match]}, [recipe], available, False, fridge_count=1)[0]['missing'] == []
    match['coverage'][0]['inventory'] = [2]
    assert not validated_matches({'matches': [match]}, [recipe], available, False, fridge_count=1)
    assert fridge == before


def test_plan_staples_are_not_purchases_and_regular_ingredients_still_are():
    source = {'title': 'Блюдо', 'ingredients': ['1 g salt', '200 g rice'], 'servings': 1}
    analysis = {'recipes': [{'id': 0, 'suitable': True, 'title_ru': 'Блюдо', 'slots': [0],
        'nutrition': {'kcal': 400, 'protein': 10, 'carbs': 90, 'fat': 0},
        'ingredients': [{'index': 0, 'name': 'соль', 'inventory': [0], 'cost': .01},
                        {'index': 1, 'name': 'рис', 'inventory': [], 'cost': 1}]}]}
    recipes = analyze_recipes(analysis, [source], [], 1)
    assert recipes[0]['items'][0]['inventory'] == []
    rows = shopping_list(recipes, [[0]], 1, [])
    assert len(rows) == 1 and rows[0]['name'] == 'рис' and rows[0]['missing'] == 200
