"""Custom button icons; Unicode is reserved for the two navigation buttons."""

from copy import deepcopy

# Published set: https://t.me/addemoji/HolodosOrange20261025_by_holodos_recept_bot
ICONS = {
    "dish": "5399867664623509428",
    "fridge": "5399981116184635470",
    "plan": "5399937784259585027",
    "recognize": "5400007964025203427",
    "new_list": "5399956956993595568",
    "shopping": "5397712252465884659",
    "recipe": "5400074875320707188",
    "alternatives": "5400284903516448045",
    "refine": "5400068063502576562",
    "profile": "5398113136123359309",
    "save": "5400375076354826615",
    "calendar": "5398122091130171961",
    "previous": "5399918817684006576",
    "next": "5400073651255027590",
    "back": "5400010455106235269",
    "home": "5400102809787999353",
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
}


def icon_key(button):
    text = button.get("text", "")
    if text in {"⬅️", "Назад", "⬅️ Назад"}:
        return "back"
    if text in {"🏠", "Главное меню", "🏠 Главное меню"}:
        return "home"
    parts = button.get("callback_data", "").split(":")
    action = parts[2] if len(parts) > 2 else ""
    if action == "page":
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
