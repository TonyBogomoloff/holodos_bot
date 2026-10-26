"""Refresh the two public recipe fixtures; no config, secrets or user data."""
import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from holodos_bot.recipes import PublicWeb, extract_recipe

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "omelette": "https://1000.menu/cooking/57273-omlet-s-pomidorami-i-syrom-na-skovorode",
    "pasta": "https://www.budgetbytes.com/creamy-tomato-spinach-pasta/",
}


async def main():
    web = PublicWeb()
    evidence = {}
    try:
        for name, url in SOURCES.items():
            page, final_url = await web.get(url, recipe_page=True)
            recipe = extract_recipe(page.decode("utf-8"), final_url)
            photo, photo_url = await web.get(recipe["image_url"], image=True, limit=10_000_000)
            with Image.open(BytesIO(photo)) as image:
                image.verify()
            (ROOT / "public" / f"{name}.jpg").write_bytes(photo)
            evidence[name] = {**{k: v for k, v in recipe.items() if k != "steps"},
                              "verified_step_count": len(recipe["steps"]),
                              "downloaded_image_url": photo_url,
                              "page_sha256": hashlib.sha256(page).hexdigest(),
                              "image_sha256": hashlib.sha256(photo).hexdigest()}
            print(name, recipe["title"], "ingredients", len(recipe["ingredients"]),
                  "steps", len(recipe["steps"]), "time", recipe["time"], "yield", recipe["yield"])
        (ROOT / "public" / "recipe-sources.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        await web.close()


if __name__ == "__main__":
    asyncio.run(main())
