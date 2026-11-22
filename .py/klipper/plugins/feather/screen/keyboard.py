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
KEYBOARD_LEFT = "keyboard.left"
KEYBOARD_RIGHT = "keyboard.right"
KEYBOARD_CONTROL_ACTIONS = frozenset((
    KEYBOARD_BACKSPACE, KEYBOARD_SHIFT, KEYBOARD_SYMBOLS, KEYBOARD_SPACE,
    KEYBOARD_LEFT, KEYBOARD_RIGHT,
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
    key_state = "keypad"

    @staticmethod
    def printable_ascii(character):
        return len(character) == 1 and 32 <= ord(character) <= 126

    def rows(self, symbols=False, shift=False, allowed_characters=None):
        if allowed_characters is None:
            return _PRINTABLE_ROWS[bool(symbols), bool(shift)]
        return keyboard_rows(symbols, shift, allowed_characters)

    def controls(self, symbols=False, shift=False):
        small, large = "JetBrainsMono 8pt", "JetBrainsMono 12pt"
        bold = "JetBrainsMono Bold 8pt"
        auxiliary, active = "keypad_aux", "keypad_confirm"
        return (
            (KEYBOARD_SHIFT, "SHIFT", active if shift else auxiliary, bold if shift else small),
            (KEYBOARD_SYMBOLS, "ABC" if symbols else "123",
             active if symbols else auxiliary, bold if symbols else small),
            (KEYBOARD_LEFT, "<", auxiliary, large),
            (KEYBOARD_SPACE, "SPACE", self.key_state, small),
            (KEYBOARD_RIGHT, ">", auxiliary, large),
            (KEYBOARD_BACKSPACE, "BACKSPACE", auxiliary, small),
        )

    def render(self, renderer, symbols=False, shift=False,
               allowed_characters=None):
        # One batch: the renderer reads its colours and fonts once for
        # every key.
        keys = []
        rows = self.rows(symbols, shift, allowed_characters)
        for row_index, row in enumerate(rows):
            if not row:
                continue
            key_width = 68
            gap = 7
            total_width = len(row) * key_width + max(0, len(row) - 1) * gap
            x = (800 - total_width) // 2
            for token, label in row:
                keys.append((
                    KEY_ACTION_PREFIX + token, x, 181 + row_index * 49,
                    key_width, 42, label, self.key_state, "JetBrainsMono 8pt"))
                x += key_width + gap
        control_bounds = ((25, 110), (145, 90), (245, 60), (315, 200), (525, 60), (595, 180))
        for (x, width), (action, label, state, font) in zip(control_bounds, self.controls(symbols, shift)):
            keys.append((action, x, 328, width, 43, label, state, font))
        return renderer.buttons(keys)

    def apply(self, value, cursor, action, shift=False, symbols=False,
              allowed_characters=None, max_length=None):
        value = str(value)
        cursor = max(0, min(int(cursor), len(value)))
        allowed = (self.printable_ascii if allowed_characters is None
                   else allowed_characters)
        limit = self.max_length if max_length is None else int(max_length)
        if action == KEYBOARD_BACKSPACE:
            if cursor > 0:
                value = value[:cursor - 1] + value[cursor:]
                cursor -= 1
        elif action == KEYBOARD_LEFT:
            cursor = max(0, cursor - 1)
        elif action == KEYBOARD_RIGHT:
            cursor = min(len(value), cursor + 1)
        elif action == KEYBOARD_SHIFT:
            shift = not shift
        elif action == KEYBOARD_SYMBOLS:
            symbols = not symbols
            # Enter each text/symbol layer through its primary state. Shift
            # then selects uppercase letters or the secondary punctuation page.
            shift = False
        elif action == KEYBOARD_SPACE:
            if len(value) < limit and _character_allowed(" ", allowed):
                value = value[:cursor] + " " + value[cursor:]
                cursor += 1
        elif action.startswith(KEY_ACTION_PREFIX):
            token = action[len(KEY_ACTION_PREFIX):]
            available = dict(
                key for row in self.rows(symbols, shift, allowed)
                for key in row)
            character = available.get(token)
            if character is not None and len(value) < limit:
                value = value[:cursor] + character + value[cursor:]
                cursor += 1
        return value, cursor, shift, symbols


# The four layers of the default filter, built once: a keyboard frame reads them on the reactor.
_PRINTABLE_ROWS = {
    (symbols, shift): keyboard_rows(symbols, shift, TextKeyboard.printable_ascii)
    for symbols in (False, True) for shift in (False, True)
}
TEXT_KEYBOARD = TextKeyboard()
