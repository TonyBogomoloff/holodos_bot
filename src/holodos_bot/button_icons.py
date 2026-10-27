"""Custom button icons; Unicode is reserved for the two navigation buttons."""

from copy import deepcopy

# Published set: https://t.me/addemoji/HolodosMint20261027_by_holodos_recept_bot
ICONS = {
    "dish": "5404820582254555032",
    "fridge": "5404565349528023913",
    "plan": "5404588031250311328",
    "recognize": "5404736735903002466",
    "new_list": "5404708968939433893",
    "shopping": "5404816356006734980",
    "recipe": "5404855874000827684",
    "alternatives": "5404641374744126678",
    "refine": "5404520269551280347",
    "profile": "5404835086359115551",
    "save": "5404554929937361724",
    "calendar": "5406951710667089127",
    "previous": "5406845882672914056",
    "next": "5404545493894211166",
    "back": "5404577542940173434",
    "home": "5404485420186643823",
    "help": "5404543148842068899",
    "privacy": "5404820118398082751",
    "delete": "5404639248735317743"
}

ACTION_ICONS = {
    "search": "dish",
    "pick": "dish",
    "slot": "dish",
    "replace": "dish",
    "list": "fridge",
    "plan": "plan",
    "day": "plan",
    "recognize": "recognize",
    "new": "new_list",
    "shop": "shopping",
    "shopping": "shopping",
    "meal": "recipe",
    "alternatives": "alternatives",
    "refine": "refine",
    "profile": "profile",
    "saveprofile": "save",
    "build": "calendar",
    "home": "home",
    "profileback": "back",
    "choices": "back",
    "help": "help",
    "privacy": "privacy",
    "delete": "delete",
}


def icon_key(button):
    text = button.get("text", "")
    if text in {"⬅️", "Назад", "⬅️ Назад"}:
        return "back"
    if text in {"🏠", "Главное меню", "🏠 Главное меню"}:
        return "home"
    parts = button.get("callback_data", "").split(":")
    action = parts[2] if len(parts) > 2 else ""
    if action in {"page", "screen"}:
        return "previous" if text.startswith("←") else "next"
    return ACTION_ICONS.get(action)


def decorate(markup, enabled=True):
    result = deepcopy(markup)
    for row in result.get("inline_keyboard", []):
        for button in row:
            key = icon_key(button)
            icon = ICONS.get(key) if enabled else None
            button.pop("icon_custom_emoji_id", None)
            if key == "back":
                button["text"] = "Назад" if icon else "⬅️ Назад"
            elif key == "home":
                button["text"] = "Главное меню" if icon else "🏠 Главное меню"
            if icon:
                button["icon_custom_emoji_id"] = icon
    return result
