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
