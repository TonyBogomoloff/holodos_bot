"""Assumed kitchen staples; never persisted as photographed inventory."""

STAPLES = ('соль', 'чёрный перец', 'сахар', 'пшеничная мука', 'подсолнечное масло', 'вода')


def with_staples(inventory):
    return [*inventory, *({'name': name, 'quantity': '', 'uncertain': False} for name in STAPLES)]
