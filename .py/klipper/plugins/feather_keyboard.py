## Shared keyboard layouts for Feather text editors.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


ALPHA_KEY_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
DIGIT_KEY_ROW = tuple((str(value), str(value))
                      for value in range(1, 10)) + (("0", "0"),)
SYMBOL_KEYS = (
    ("minus", "-"), ("under", "_"), ("plus", "+"), ("at", "@"),
    ("hash", "#"), ("dollar", "$"), ("percent", "%"), ("amp", "&"),
    ("star", "*"), ("bang", "!"), ("dot", "."), ("comma", ","),
    ("question", "?"), ("slash", "/"), ("colon", ":"), ("semi", ";"),
    ("lparen", "("), ("rparen", ")"), ("quote", '"'), ("bslash", "\\"),
)
SYMBOL_KEY_ROWS = (
    DIGIT_KEY_ROW,
    SYMBOL_KEYS[:10],
    SYMBOL_KEYS[10:],
)
SYMBOL_MAP = dict(SYMBOL_KEYS)


def keyboard_rows(symbols=False, shift=False):
    if symbols:
        return SYMBOL_KEY_ROWS
    return tuple(
        tuple((character,
               character.upper() if shift else character)
              for character in row)
        for row in ALPHA_KEY_ROWS)


def key_character(token, shift=False):
    if len(token) == 1 and token.isalnum():
        return token.upper() if shift and token.isalpha() else token
    return SYMBOL_MAP.get(token)
