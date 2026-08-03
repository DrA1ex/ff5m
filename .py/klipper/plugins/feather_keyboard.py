## Shared text keyboard for Feather editors.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


ALPHA_KEY_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
DIGIT_KEY_ROW = tuple((str(value), str(value))
                      for value in range(1, 10)) + (("0", "0"),)
PRIMARY_SYMBOL_KEYS = (
    ("minus", "-"), ("under", "_"), ("plus", "+"), ("at", "@"),
    ("hash", "#"), ("dollar", "$"), ("percent", "%"), ("amp", "&"),
    ("star", "*"), ("bang", "!"), ("dot", "."), ("comma", ","),
    ("question", "?"), ("slash", "/"), ("colon", ":"), ("semi", ";"),
    ("lparen", "("), ("rparen", ")"), ("quote", '"'), ("bslash", "\\"),
)
SECONDARY_SYMBOL_KEYS = (
    ("apostrophe", "'"), ("equal", "="), ("less", "<"), ("greater", ">"),
    ("lbracket", "["), ("rbracket", "]"), ("caret", "^"), ("grave", "`"),
    ("lbrace", "{"), ("pipe", "|"), ("rbrace", "}"), ("tilde", "~"),
)
SYMBOL_KEYS = PRIMARY_SYMBOL_KEYS + SECONDARY_SYMBOL_KEYS
SYMBOL_KEY_ROWS = (
    DIGIT_KEY_ROW,
    PRIMARY_SYMBOL_KEYS[:10],
    PRIMARY_SYMBOL_KEYS[10:],
)
SECONDARY_SYMBOL_KEY_ROWS = (
    SECONDARY_SYMBOL_KEYS[:4],
    SECONDARY_SYMBOL_KEYS[4:8],
    SECONDARY_SYMBOL_KEYS[8:],
)
SYMBOL_MAP = dict(SYMBOL_KEYS)

KEY_ACTION_PREFIX = "keyboard.key."
KEYBOARD_BACKSPACE = "keyboard.backspace"
KEYBOARD_SHIFT = "keyboard.shift"
KEYBOARD_SYMBOLS = "keyboard.symbols"
KEYBOARD_SPACE = "keyboard.space"
KEYBOARD_CONTROL_ACTIONS = frozenset((
    KEYBOARD_BACKSPACE, KEYBOARD_SHIFT, KEYBOARD_SYMBOLS, KEYBOARD_SPACE,
))


def _character_allowed(character, allowed_characters):
    if allowed_characters is None:
        return True
    if callable(allowed_characters):
        return bool(allowed_characters(character))
    return character in allowed_characters


def keyboard_rows(symbols=False, shift=False, allowed_characters=None):
    if symbols:
        rows = SECONDARY_SYMBOL_KEY_ROWS if shift else SYMBOL_KEY_ROWS
    else:
        rows = tuple(
            tuple((character,
                   character.upper() if shift else character)
                  for character in row)
            for row in ALPHA_KEY_ROWS)
    if allowed_characters is None:
        return rows
    return tuple(
        tuple((token, character) for token, character in row
              if _character_allowed(character, allowed_characters))
        for row in rows)


def key_character(token, shift=False):
    if len(token) == 1 and token.isalnum():
        return token.upper() if shift and token.isalpha() else token
    return SYMBOL_MAP.get(token)


def is_keyboard_action(action):
    return (action in KEYBOARD_CONTROL_ACTIONS
            or action.startswith(KEY_ACTION_PREFIX))


class TextKeyboard:
    """Stateless shared Feather keyboard renderer and editing policy."""

    max_length = 64

    @staticmethod
    def printable_ascii(character):
        return len(character) == 1 and 32 <= ord(character) <= 126

    def rows(self, symbols=False, shift=False, allowed_characters=None):
        allowed = (self.printable_ascii if allowed_characters is None
                   else allowed_characters)
        return keyboard_rows(symbols, shift, allowed)

    def render(self, renderer, symbols=False, shift=False,
               allowed_characters=None):
        commands = []
        rows = self.rows(symbols, shift, allowed_characters)
        for row_index, row in enumerate(rows):
            if not row:
                continue
            key_width = 68
            gap = 7
            total_width = len(row) * key_width + max(0, len(row) - 1) * gap
            x = (800 - total_width) // 2
            for token, label in row:
                commands += renderer.button(
                    KEY_ACTION_PREFIX + token,
                    x, 181 + row_index * 49, key_width, 42, label,
                    font="JetBrainsMono 8pt")
                x += key_width + gap
        controls_y = 328
        commands += renderer.button(
            KEYBOARD_SHIFT, 25, controls_y, 120, 43, "SHIFT",
            state="selected" if shift else "enabled",
            font="JetBrainsMono 8pt")
        commands += renderer.button(
            KEYBOARD_SYMBOLS, 155, controls_y, 100, 43,
            "ABC" if symbols else "123",
            state="selected" if symbols else "enabled",
            font="JetBrainsMono 8pt")
        commands += renderer.button(
            KEYBOARD_SPACE, 265, controls_y, 300, 43, "SPACE",
            font="JetBrainsMono 8pt")
        commands += renderer.button(
            KEYBOARD_BACKSPACE, 575, controls_y, 200, 43, "BACKSPACE",
            font="JetBrainsMono 8pt")
        return commands

    def apply(self, value, action, shift=False, symbols=False,
              allowed_characters=None, max_length=None):
        value = str(value)
        allowed = (self.printable_ascii if allowed_characters is None
                   else allowed_characters)
        limit = self.max_length if max_length is None else int(max_length)
        if action == KEYBOARD_BACKSPACE:
            value = value[:-1]
        elif action == KEYBOARD_SHIFT:
            shift = not shift
        elif action == KEYBOARD_SYMBOLS:
            symbols = not symbols
            # Enter each text/symbol layer through its primary state. Shift
            # then selects uppercase letters or the secondary punctuation page.
            shift = False
        elif action == KEYBOARD_SPACE:
            if len(value) < limit and _character_allowed(" ", allowed):
                value += " "
        elif action.startswith(KEY_ACTION_PREFIX):
            token = action[len(KEY_ACTION_PREFIX):]
            available = dict(
                key for row in self.rows(symbols, shift, allowed)
                for key in row)
            character = available.get(token)
            if character is not None and len(value) < limit:
                value += character
        return value, shift, symbols


TEXT_KEYBOARD = TextKeyboard()
