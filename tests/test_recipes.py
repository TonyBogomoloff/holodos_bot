import asyncio
import json
import socket

import httpcore
import httpx
import pytest

from holodos_bot.providers import ProviderError
from holodos_bot.recipes import (
    PublicBackend,
    PublicWeb,
    RecipeSearch,
    extract_recipe,
    recipe_numbers,
    recipe_source,
    validated_matches,
)


def source():
    data = {'@context': 'https://schema.org', '@type': 'Recipe', 'name': 'Eggs',
            'recipeIngredient': ['2 eggs', '1 tsp butter'],
            'recipeInstructions': [{'@type': 'HowToSection', 'itemListElement': [
                {'@type': 'HowToStep', 'text': 'Cook for 5 minutes.'}]}],
            'image': {'url': '/eggs.jpg'}}
    return '<script type="application/ld+json">' + json.dumps({'@graph': [data]}) + '</script>'


def test_extracts_actual_source_and_rejects_non_recipes():
    r = extract_recipe(source(), 'https://example.com/recipe')
    assert r['ingredients'] == ['2 eggs', '1 tsp butter']
    assert r['steps'] == ['Cook for 5 minutes.']
    assert r['image_url'] == 'https://example.com/eggs.jpg'
    with pytest.raises(ProviderError, match='recipe_not_structured'):
        extract_recipe('<title>Egg recipes</title><p>Invent anything</p>', 'https://example.com')


MENU_SOURCE = """
<body itemscope itemtype="http://schema.org/WebPage">
<meta itemprop="name" content="Другой заголовок">
<section id="pt_info" itemscope itemtype="http://schema.org/Recipe">
  <meta itemprop="name" content="Яйца в форме">
  <div itemprop="author" itemscope itemtype="http://schema.org/Person">
    <meta itemprop="name" content="Автор"><img itemprop="image" src="/avatar.jpg">
  </div>
  <picture><source srcset="/large.jpg"><img itemprop="image" src="//static.1000.menu/dish.jpg"></picture>
  <meta itemprop="recipeYield" content="2"/>
  <div><meta itemprop="recipeIngredient" content="Яйца - 2 шт." /></div>
  <div><meta itemprop="recipeIngredient" content="Масло - 0.5 чайн.л." /></div>
  <section id="pt_steps">
    <meta itemprop="totalTime" content="PT20M">
    <ol class="instructions" itemprop="recipeInstructions">
      <li><h3>Шаг 1</h3><a data-caption="Не дублировать"><img src="/step.jpg"></a>
        <div class="instruction"><p>Смажьте форму.</p><p>Добавьте <b>2 яйца</b>.</p></div></li>
      <li class="as-ad-step"><script>advertising()</script><p>Реклама</p></li>
      <li><h3>Шаг 2</h3><div class="instruction">Готовьте при 180°С<br>15 минут.
        <script>ignore_me()</script><style>.ad { color: red }</style></div></li>
    </ol>
  </section>
</section>
<div class="instruction">Комментарий: ещё 100 минут.</div>
</body>
"""


def test_1000_menu_preserves_microdata_quantities_steps_and_recipe_photo():
    r = extract_recipe(MENU_SOURCE, 'https://1000.menu/cooking/123-eggs')
    assert r['title'] == 'Яйца в форме'
    assert r['ingredients'] == ['Яйца - 2 шт.', 'Масло - 0.5 чайн.л.']
    assert r['steps'] == ['Смажьте форму. Добавьте 2 яйца.', 'Готовьте при 180°С 15 минут.']
    assert r['image_url'] == 'https://static.1000.menu/dish.jpg'
    assert (r['time'], r['yield']) == ('PT20M', '2')


@pytest.mark.parametrize('document', [
    MENU_SOURCE.replace('id="pt_info"', 'id="related"'),
    MENU_SOURCE.replace('recipeIngredient', 'unrelated'),
    MENU_SOURCE.replace('recipeInstructions', 'unrelated'),
    MENU_SOURCE.replace('itemprop="image"', 'itemprop="unrelated"'),
])
def test_1000_menu_rejects_incomplete_or_unrelated_recipe_markup(document):
    with pytest.raises(ProviderError, match='recipe_not_structured'):
        extract_recipe(document, 'https://1000.menu/cooking/123-eggs')


def test_missing_ingredient_cannot_be_silently_dropped():
    recipe = extract_recipe(source(), 'https://example.com/recipe')
    m = {'id': 0, 'title_ru': 'Яичница', 'suitable': True, 'coverage': [
        {'ingredient': 0, 'inventory': [0], 'missing': '', 'optional': False}]}
    assert not validated_matches({'matches': [m]}, [recipe], [{}], False)
    m['coverage'].append({'ingredient': 1, 'inventory': [], 'missing': 'масло', 'optional': False})
    assert not validated_matches({'matches': [m]}, [recipe], [{}], False)
    assert validated_matches({'matches': [m]}, [recipe], [{}], True)[0]['missing'] == ['масло']
    m['coverage'][0]['inventory'] = [99]
    assert not validated_matches({'matches': [m]}, [recipe], [{}], True)


def test_public_backend_rejects_private_and_pins_actual_public_ip():
    async def run():
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        backend = PublicBackend()
        calls = []
        class Connector:
            async def connect_tcp(self, host, *args):
                calls.append(host)
                return 'connected'
        backend.backend = Connector()
        async def dns(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (host, port))]
        loop.getaddrinfo = dns
        try:
            for ip in ['127.0.0.1', '10.0.0.2', '169.254.169.254', '::1']:
                with pytest.raises(httpcore.ConnectError, match='private_address'):
                    await backend.connect_tcp(ip, 80)
            assert await backend.connect_tcp('8.8.8.8', 443) == 'connected'
            assert calls == ['8.8.8.8']
        finally:
            loop.getaddrinfo = original
    asyncio.run(run())


def test_redirect_is_revalidated_and_oversized_page_rejected():
    async def run():
        async def handler(request):
            if request.url.path == '/redirect':
                return httpx.Response(302, headers={'Location': 'file:///etc/passwd'})
            return httpx.Response(200, headers={'content-type': 'text/html'}, content=b'x' * 20)
        web = PublicWeb(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with pytest.raises(ProviderError, match='unsafe_url'):
                await web.get('https://example.com/redirect')
            with pytest.raises(ProviderError, match='source_too_large'):
                await web.get('https://example.com/big', limit=10)
        finally:
            await web.close()
    asyncio.run(run())


def test_recipe_sources_restrict_hosts_and_redirects_but_allow_image_cdn():
    for url in ['https://eda.ru.evil.test/recipe', 'https://evil.test/?source=eda.ru',
                'https://eda.ru@evil.test/recipe', 'https://sub.budgetbytes.com/recipe',
                'file:///eda.ru/recipe']:
        assert recipe_source(url) is None

    async def run():
        visited = []
        async def handler(request):
            visited.append(str(request.url))
            if request.url.host == 'eda.ru':
                return httpx.Response(301, headers={'Location': 'https://eda.rambler.ru/recepty/eggs'})
            if request.url.path == '/escape':
                return httpx.Response(302, headers={'Location': 'https://other.test/eggs'})
            return httpx.Response(200, headers={'content-type': 'image/jpeg' if
                request.url.host == 'cdn.test' else 'text/html'}, content=b'public content')
        web = PublicWeb(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            _, final = await web.get('https://eda.ru/recepty/eggs', recipe_page=True)
            assert final == 'https://eda.rambler.ru/recepty/eggs'
            with pytest.raises(ProviderError, match='recipe_source_not_allowed'):
                await web.get('https://www.budgetbytes.com/escape', recipe_page=True)
            assert not any('other.test' in url for url in visited)
            assert await web.get('https://cdn.test/photo.jpg', image=True)
        finally:
            await web.close()
    asyncio.run(run())


def test_search_discards_sources_outside_the_catalog(tmp_path):
    class Model:
        async def ask(self, instructions, schema, payload, **kwargs):
            assert kwargs['search'] is True
            assert 'budgetbytes.com' in payload['allowed_domains']
            return {'urls': ['https://other.test/recipe', 'https://www.budgetbytes.com/eggs/']}

    class Web:
        visited = []
        async def get(self, url, **kwargs):
            self.visited.append(url)
            assert kwargs['recipe_page'] is True
            raise ProviderError('source_unavailable')

    web = Web()
    asyncio.run(RecipeSearch(Model(), web).search([], '', tmp_path))
    assert web.visited == ['https://www.budgetbytes.com/eggs/']


def test_russian_recipe_text_does_not_exhaust_candidate_budget_prematurely(tmp_path):
    received = []
    class Model:
        async def ask(self, instructions, schema, payload, **kwargs):
            if kwargs.get('search'):
                return {'urls': [f'https://eda.rambler.ru/recepty/recipe-{i}' for i in range(3)]}
            received.extend(payload['recipes'])
            return {'matches': []}

    class Web:
        async def get(self, url, **kwargs):
            data = {'@type': 'Recipe', 'name': url.rsplit('/', 1)[-1], 'image': '/dish.jpg',
                    'recipeIngredient': ['2 яйца'],
                    'recipeInstructions': ['Текст русского шага. ' * 80] * 5}
            return ('<script type="application/ld+json">' + json.dumps(data) + '</script>').encode(), url

    search = RecipeSearch(Model(), Web())
    asyncio.run(search._batch([{'name': 'яйца'}], '', tmp_path, False, [], 0, 5))
    assert len(received) == 3


def test_recipe_translation_cannot_change_cooking_numbers():
    class Model:
        async def ask(self, *args, **kwargs):
            return {'ingredients': ['2 яйца', '1 ч. л. масла'], 'steps': ['Готовить 50 минут.']}
    recipe = extract_recipe(source(), 'https://example.com/recipe')
    result = asyncio.run(RecipeSearch(Model(), None).render(recipe))
    assert result['translation_status'] == 'source'
    assert result['steps'] == ['Cook for 5 minutes.']


def test_equivalent_fraction_translations_keep_the_same_quantity():
    assert recipe_numbers('1½ tbsp, ¼ tsp, cook 15 minutes') == recipe_numbers('1,5 ст. л., 1/4 ч. л., 15 минут')
    assert recipe_numbers('cook two to four minutes') == recipe_numbers('готовить 2–4 минуты')
