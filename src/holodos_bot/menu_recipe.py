"""Read 1000.menu's primary Recipe microdata and its marked instruction blocks."""
from html.parser import HTMLParser


class MenuRecipeHTML(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                 "meta", "param", "source", "track", "wbr"}
    TEXT_BREAKS = {"br", "div", "p", "li", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.recipe = None
        self.recipes = []

    def handle_starttag(self, tag, attrs):
        if tag in self.TEXT_BREAKS:
            self.handle_data(" ")
        attrs = dict(attrs)
        parent = self.stack[-1] if self.stack else {}
        scope = parent.get("scope")
        root = (attrs.get("id") == "pt_info" and "itemscope" in attrs
                and any(t in {"http://schema.org/Recipe", "https://schema.org/Recipe"}
                        for t in attrs.get("itemtype", "").split()) and self.recipe is None)
        if "itemscope" in attrs:
            # Names/images belonging to authors, ratings or related items are not recipe properties.
            scope = None
            if root:
                self.recipe = {"@type": "Recipe", "recipeIngredient": [], "recipeInstructions": []}
                scope = self.recipe
        props = attrs.get("itemprop", "").split()
        primary = scope is not None and scope is self.recipe
        instructions = primary and ("recipeInstructions" in props or parent.get("instructions", False))
        ignored = parent.get("ignored", False) or tag in {"script", "style", "template", "noscript"}
        capture = (primary and instructions and "instruction" in attrs.get("class", "").split()
                   and not ignored and not any(f["text"] is not None for f in self.stack))
        if primary and not ignored:
            if tag == "meta":
                for prop in props:
                    if prop == "recipeIngredient":
                        self.recipe[prop].append(attrs.get("content", ""))
                    elif prop in {"name", "totalTime", "cookTime", "recipeYield"}:
                        self.recipe.setdefault(prop, attrs.get("content", ""))
            elif tag == "img" and "image" in props and attrs.get("src"):
                self.recipe.setdefault("image", attrs["src"])
        frame = {"tag": tag, "scope": scope, "instructions": instructions, "ignored": ignored,
                 "text": [] if capture else None, "root": root}
        if tag not in self.VOID_TAGS:
            if len(self.stack) >= 200:
                raise ValueError("recipe_markup_too_deep")
            self.stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack and not self.stack[-1]["ignored"]:
            for frame in reversed(self.stack):
                if frame["text"] is not None:
                    frame["text"].append(data)
                    break

    def handle_endtag(self, tag):
        if tag in self.TEXT_BREAKS:
            self.handle_data(" ")
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                for frame in reversed(self.stack[i:]):
                    if frame["text"] is not None:
                        self.recipe["recipeInstructions"].append("".join(frame["text"]))
                    if frame["root"]:
                        self.recipes.append(self.recipe)
                del self.stack[i:]
                break
