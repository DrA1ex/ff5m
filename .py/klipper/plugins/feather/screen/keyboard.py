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
            KEYBOARD_SHIFT, 25, controls_y, 110, 43, "SHIFT",
            state="selected" if shift else "enabled",
            font="JetBrainsMono 8pt")
        commands += renderer.button(
            KEYBOARD_SYMBOLS, 145, controls_y, 90, 43,
            "ABC" if symbols else "123",
            state="selected" if symbols else "enabled",
            font="JetBrainsMono 8pt")
        commands += renderer.button(
            KEYBOARD_LEFT, 245, controls_y, 60, 43, "<",
            font="JetBrainsMono 12pt")
        commands += renderer.button(
            KEYBOARD_SPACE, 315, controls_y, 200, 43, "SPACE",
            font="JetBrainsMono 8pt")
        commands += renderer.button(
            KEYBOARD_RIGHT, 525, controls_y, 60, 43, ">",
            font="JetBrainsMono 12pt")
        commands += renderer.button(
            KEYBOARD_BACKSPACE, 595, controls_y, 180, 43, "BACKSPACE",
            font="JetBrainsMono 8pt")
        return commands

    def render_value(self, renderer, value, cursor, x, y, max_width, color,
                     masked=False, font="JetBrainsMono 12pt"):
        value = str(value)
        cursor = max(0, min(int(cursor), len(value)))
        displayed = "*" * len(value) if masked else value
        font = renderer.normalize_font_for_text(font, displayed or " ")
        advance = renderer.font_advance(font)
        visible_characters = max(1, (int(max_width) - 2) // advance)
        start = min(
            max(0, cursor - visible_characters // 2),
            max(0, len(displayed) - visible_characters))
        visible = displayed[start:start + visible_characters]
        commands = []
        if visible:
            commands.append(renderer.text(
                x, y, visible, color, font, max_width=max_width,
                truncate=True))
        caret_x = min(
            int(x) + int(max_width) - 2,
            int(x) + (cursor - start) * advance)
        commands.append(renderer.fill(caret_x, int(y) - 13, 2, 26, color))
        return commands

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


TEXT_KEYBOARD = TextKeyboard()
