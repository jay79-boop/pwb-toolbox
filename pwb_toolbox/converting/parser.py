"""A lexer and recursive-descent parser for the PineScript subset.

Scope is deliberate. The grammar here covers what a typical published strategy
uses -- a declaration, inputs, indicator calls, arithmetic and comparison,
``if`` blocks and order calls -- and parses the rest only well enough to skip
it and hand back an :class:`~pwb_toolbox.converting.nodes.Unsupported` node.
Nothing is silently dropped: whatever the parser cannot model, the code
generator reports.

Dotted identifiers (``ta.sma``, ``strategy.long``, ``input.int``) are lexed as
single name tokens. Pine has no user-facing attribute access on values, so
treating them as atoms costs nothing and simplifies every later stage.
"""

import re

from .nodes import (
    Assign,
    Binary,
    Bool,
    Call,
    ExprStmt,
    FuncDef,
    If,
    Index,
    ListLit,
    Na,
    Name,
    Num,
    Param,
    Program,
    Str,
    Ternary,
    TupleAssign,
    Unary,
    Unsupported,
)

TAB_WIDTH = 4

_VERSION_RE = re.compile(r"^\s*//\s*@version\s*=\s*(\d+)", re.MULTILINE)
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_NUMBER_RE = re.compile(r"\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+")
_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\b")

#: Longest first, so ``==`` never lexes as two ``=``.
_OPERATORS = (
    ":=",
    "==",
    "!=",
    "<=",
    ">=",
    "=>",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "<",
    ">",
    "=",
    "?",
    ":",
    "(",
    ")",
    "[",
    "]",
    ",",
)

_COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}
_BLOCK_KEYWORDS = {"for", "while"}

#: `x += y` is `x := x + y`. Desugaring it in the parser means the generator's
#: existing reassignment paths apply untouched -- a `var` target still writes
#: through to its attribute, a local still stays a local, and a name that was
#: never defined is still reported.
_COMPOUND_ASSIGN = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}
_ASSIGN_OPS = {"=", ":=", *_COMPOUND_ASSIGN}

#: A long expression is routinely split across lines. Pine's own rule keys on
#: the continuation being indented by something other than a multiple of four,
#: which collides with the indentation that marks a block. Reading the operator
#: instead is unambiguous: no statement ends with a binary operator, and none
#: begins with one, so either position can only mean "this line and the next
#: are one expression".
_DANGLING_OPS = {
    "?",
    ":",
    "+",
    "-",
    "*",
    "/",
    "%",
    "=",
    ":=",
    ",",
    "==",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
}
_DANGLING_WORDS = {"and", "or", "not"}

#: `[` is deliberately absent: a line starting with it is tuple destructuring,
#: `[macd, signal, hist] = ta.macd(...)`, which is a statement of its own.
_LEADING_CONTINUATION = re.compile(
    r"^\s*(?:and\b|or\b|\?|:|==|!=|<=|>=|<|>|\+|-|\*|/|%)"
)

#: `-` and `+` are the two operators that are also prefixes, so a line opening
#: with one is genuinely ambiguous: it either continues the line above
#: (`x = a` / `    - b`) or is a fresh expression with a sign (`-1` as the
#: value of an `if` branch). Only the second can follow a line that opens a
#: block, and whether a line opens one is decidable from its own text.
_LEADING_SIGN = re.compile(r"^\s*[+-]")
_OPENS_BLOCK = re.compile(
    r"^\s*(?:if|else|for|while|switch)\b"  # a block statement
    r"|(?:=|:=)\s*(?:if|switch)\b"  # `x = if cond`, `x = switch mode`
    r"|=>\s*$"  # a function, or a switch case with its value below
)

#: Words that may precede the name in a declaration, as in
#: ``float entryPrice = na`` or ``series int n = 0``. Pine allows a type, a
#: type qualifier, or both. None of it changes what the assignment means to
#: Backtrader, so it is consumed and dropped -- but only when what follows
#: really is a declaration, since ``int(x)`` and ``float(x)`` are also casts.
_TYPE_WORDS = {
    "array",
    "bool",
    "box",
    "color",
    "const",
    "float",
    "int",
    "label",
    "line",
    "linefill",
    "map",
    "matrix",
    "series",
    "simple",
    "string",
    "table",
}


class PineSyntaxError(SyntaxError):
    """Raised when the source cannot be lexed or parsed at all."""


class Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind, value, line):
        self.kind = kind
        self.value = value
        self.line = line

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Token({self.kind!r}, {self.value!r}, line={self.line})"


def _strip_comment(line: str) -> str:
    """Drop a trailing ``//`` comment, ignoring ``//`` inside string literals."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "/" and line[i + 1 : i + 2] == "/":
            return line[:i]
        i += 1
    return line


def _ends_dangling(tokens, start) -> bool:
    """True when the tokens lexed for one line cannot be a complete statement."""
    if len(tokens) <= start:
        return False
    last = tokens[-1]
    if last.kind == "OP":
        return last.value in _DANGLING_OPS
    return last.kind == "NAME" and last.value in _DANGLING_WORDS


def _continues_previous(line: str, previous: str) -> bool:
    """True when ``line`` picks up the expression ``previous`` left hanging.

    Asked from both ends -- once by the line itself, to know that its
    indentation opens no block, and once by the line above, to know to hold
    its newline back -- so the two must be the same question.
    """
    if not _LEADING_CONTINUATION.match(line):
        return False
    if _LEADING_SIGN.match(line) and _OPENS_BLOCK.search(previous):
        return False
    return True


def tokenize(source: str) -> list:
    """Turn Pine source into tokens, with INDENT/DEDENT for block structure."""
    tokens = []
    indents = [0]
    depth = 0  # bracket nesting; newlines inside brackets are insignificant

    # Comments and blank lines are dropped first, so a continuation still finds
    # its previous line across them.
    lines = [
        (n, stripped)
        for n, stripped in (
            (n, _strip_comment(raw)) for n, raw in enumerate(source.splitlines(), 1)
        )
        if stripped.strip()
    ]

    # A source of nothing but comments and blanks leaves the loop below
    # unentered, and the trailing DEDENT/EOF still need a line to point at.
    lineno = len(source.splitlines()) or 1

    continuing = False  # the previous line left an expression unfinished
    for index, (lineno, line) in enumerate(lines):
        previous = lines[index - 1][1] if index else ""
        starts_continuation = _continues_previous(line, previous)
        # A continuation is part of the line above, so its indentation says
        # nothing about block structure and must not open one.
        if depth == 0 and not continuing and not starts_continuation:
            column = 0
            for ch in line:
                if ch == " ":
                    column += 1
                elif ch == "\t":
                    column += TAB_WIDTH
                else:
                    break
            if column > indents[-1]:
                indents.append(column)
                tokens.append(Token("INDENT", None, lineno))
            while column < indents[-1]:
                indents.pop()
                tokens.append(Token("DEDENT", None, lineno))
                if column > indents[-1]:
                    raise PineSyntaxError(f"inconsistent indentation on line {lineno}")

        line_start = len(tokens)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch in " \t":
                i += 1
                continue

            if ch in "\"'":
                end = i + 1
                buf = []
                while end < len(line) and line[end] != ch:
                    if line[end] == "\\" and end + 1 < len(line):
                        buf.append(line[end + 1])
                        end += 2
                        continue
                    buf.append(line[end])
                    end += 1
                if end >= len(line):
                    raise PineSyntaxError(f"unterminated string on line {lineno}")
                tokens.append(Token("STRING", "".join(buf), lineno))
                i = end + 1
                continue

            # `#00c853` is a colour literal, not a comment and not an operator.
            # It only ever reaches a plot, so it lexes as a NAME and is handled
            # downstream with the rest of the drawing constants.
            match = _HEX_COLOR_RE.match(line, i)
            if match:
                tokens.append(Token("NAME", match.group(), lineno))
                i = match.end()
                continue

            match = _NUMBER_RE.match(line, i)
            if match and (
                ch.isdigit() or (ch == "." and match.group().count(".") == 1)
            ):
                tokens.append(Token("NUMBER", float(match.group()), lineno))
                i = match.end()
                continue

            match = _NAME_RE.match(line, i)
            if match:
                tokens.append(Token("NAME", match.group(), lineno))
                i = match.end()
                continue

            for op in _OPERATORS:
                if line.startswith(op, i):
                    if op in "([":
                        depth += 1
                    elif op in ")]":
                        depth = max(depth - 1, 0)
                    tokens.append(Token("OP", op, lineno))
                    i += len(op)
                    break
            else:
                raise PineSyntaxError(f"unexpected character {ch!r} on line {lineno}")

        # Hold the newline back when this line is unfinished, or when the next
        # one picks the expression up with a leading operator.
        nxt = lines[index + 1][1] if index + 1 < len(lines) else ""
        continuing = _ends_dangling(tokens, line_start) or _continues_previous(
            nxt, line
        )
        if depth == 0 and not continuing:
            tokens.append(Token("NEWLINE", None, lineno))

    while len(indents) > 1:
        indents.pop()
        tokens.append(Token("DEDENT", None, lineno))
    tokens.append(Token("EOF", None, lineno if source else 1))
    return tokens


class Parser:
    def __init__(self, tokens, lines):
        self.tokens = tokens
        self.lines = lines
        self.pos = 0
        #: Names introduced by `type X` blocks, which then act as type words.
        self.user_types = set()
        #: Pine name -> FuncDef, filled as declarations are parsed.
        self.functions = {}

    # --- token helpers -------------------------------------------------------

    @property
    def current(self):
        return self.tokens[self.pos]

    def at(self, kind, value=None) -> bool:
        token = self.current
        return token.kind == kind and (value is None or token.value == value)

    def advance(self):
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, kind, value=None):
        if not self.at(kind, value):
            token = self.current
            wanted = value or kind
            raise PineSyntaxError(
                f"expected {wanted!r} but found {token.value!r} on line {token.line}"
            )
        return self.advance()

    def skip_newlines(self):
        while self.at("NEWLINE"):
            self.advance()

    # --- statements ----------------------------------------------------------

    def parse_program(self, version):
        body = []
        declaration = None
        declaration_call = None
        self.skip_newlines()
        while not self.at("EOF"):
            statement = self.parse_statement()
            if statement is not None:
                if isinstance(statement, FuncDef):
                    self.functions[statement.name] = statement
                    self.skip_newlines()
                    continue
                if (
                    declaration is None
                    and isinstance(statement, ExprStmt)
                    and isinstance(statement.value, Call)
                    and statement.value.func in ("strategy", "indicator", "study")
                ):
                    kind = statement.value.func
                    declaration = (
                        "indicator" if kind == "study" else kind,
                        _declaration_title(statement.value),
                    )
                    declaration_call = statement.value
                    continue
                body.append(statement)
            self.skip_newlines()
        return Program(
            declaration=declaration,
            version=version,
            body=body,
            functions=self.functions,
            declaration_call=declaration_call,
        )

    def _skip_block(self, kind, start_line):
        """Consume a construct plus any indented body, returning it verbatim."""
        while not self.at("NEWLINE") and not self.at("EOF"):
            self.advance()
        end_line = self.current.line
        if self.at("NEWLINE"):
            self.advance()
        if self.at("INDENT"):
            self.advance()
            level = 1
            while level and not self.at("EOF"):
                if self.at("INDENT"):
                    level += 1
                elif self.at("DEDENT"):
                    level -= 1
                end_line = max(end_line, self.current.line)
                self.advance()
        text = "\n".join(self.lines[start_line - 1 : end_line]).strip()
        return Unsupported(kind=kind, text=text)

    def _at_function_declaration(self) -> bool:
        """True when the statement is `name(...) =>`, with any parameter list.

        Scans for the `=>` past a balanced parameter list rather than trying to
        parse the parameters, which may carry types and defaults.
        """
        if not self.at("NAME") or self.tokens[self.pos + 1].kind != "OP":
            return False
        if self.tokens[self.pos + 1].value != "(":
            return False
        index, depth = self.pos + 1, 0
        while index < len(self.tokens):
            token = self.tokens[index]
            if token.kind == "OP" and token.value == "(":
                depth += 1
            elif token.kind == "OP" and token.value == ")":
                depth -= 1
                if depth == 0:
                    nxt = (
                        self.tokens[index + 1] if index + 1 < len(self.tokens) else None
                    )
                    return nxt is not None and nxt.kind == "OP" and nxt.value == "=>"
            elif token.kind in ("NEWLINE", "EOF"):
                return False
            index += 1
        return False

    def parse_statement(self, value_position=False):
        token = self.current

        if token.kind == "NAME" and token.value in _BLOCK_KEYWORDS:
            return self._skip_block(token.value, token.line)

        # A switch whose result goes nowhere is a side-effecting block, which
        # is a different thing from the expression handled below. Skip and
        # report it rather than mis-reading it as one -- unless the block it
        # sits in stands for a value, where the switch is that value.
        if (
            token.kind == "NAME"
            and token.value == "switch"
            and not (
                self.tokens[self.pos + 1].kind == "OP"
                and self.tokens[self.pos + 1].value in _ASSIGN_OPS
            )
        ):
            if value_position:
                return ExprStmt(self.parse_switch())
            return self._skip_block("switch statement", token.line)

        if token.kind == "NAME" and token.value == "if":
            return self.parse_if()

        if self._at_function_declaration():
            start = self.pos
            try:
                return self.parse_function(token.line)
            except PineSyntaxError:
                # Reading a body is best-effort. One that uses something the
                # grammar does not model is skipped and reported, exactly as
                # every body was before any of them could be read -- failing
                # the whole file over it would tell the caller far less.
                self.pos = start
                return self._skip_block("user-defined function", token.line)

        # `type Zone` opens a user-defined type, its fields on an indented
        # block. Same reasoning as a function: skip it and report it, rather
        # than giving up on the whole file.
        if self._at_type_declaration():
            # Remember the name: `bar b = bar.new()` declares `b` with `bar` as
            # its type, which only reads as a declaration once `bar` is known.
            self.user_types.add(self.tokens[self.pos + 1].value)
            return self._skip_block("user-defined type", token.line)

        # `[a, b] = ta.macd(...)` destructures a tuple; `[a, b]` on its own
        # returns one, which a function body does at its end. The `=` past the
        # closing bracket is what tells them apart.
        if token.kind == "OP" and token.value == "[":
            if self._at_tuple_assign():
                return self.parse_tuple_assign()
            value = self.parse_expression()
            self.expect("NEWLINE")
            return ExprStmt(value)

        qualifier = ""
        if token.kind == "NAME" and token.value in ("var", "varip"):
            nxt = self.tokens[self.pos + 1]
            if nxt.kind == "NAME":
                qualifier = token.value
                self.advance()

        self._skip_declared_type()

        if self.at("NAME"):
            nxt = self.tokens[self.pos + 1]
            if nxt.kind == "OP" and nxt.value in _ASSIGN_OPS:
                target = self.advance().value
                operator = self.advance().value
                if self.at("NAME", "switch"):
                    # The block runs to its DEDENT, so there is no trailing
                    # newline of its own to consume.
                    value = self.parse_switch()
                elif self.at("NAME", "if"):
                    value = self.parse_if_expression()
                else:
                    value = self.parse_expression()
                    self.expect("NEWLINE")
                if operator in _COMPOUND_ASSIGN:
                    # The whole right-hand side is the operand, so
                    # `q += a ? 1 : 0` reads as `q := q + (a ? 1 : 0)`.
                    value = Binary(_COMPOUND_ASSIGN[operator], Name(target), value)
                    operator = ":="
                return Assign(
                    target=target,
                    value=value,
                    qualifier=qualifier or (":=" if operator == ":=" else ""),
                )

        value = self.parse_expression()
        self.expect("NEWLINE")
        return ExprStmt(value)

    def parse_function(self, start_line):
        """Parse ``name(a, b) =>`` and the body that follows it.

        The body may be an expression on the same line or an indented block;
        both are kept as a list of statements, since the generator treats the
        one-liner as a block of one.
        """
        name = self.expect("NAME").value
        self.expect("OP", "(")
        params = []
        while not self.at("OP", ")"):
            params.append(self.parse_param())
            if self.at("OP", ","):
                self.advance()
        self.expect("OP", ")")
        self.expect("OP", "=>")

        if self.at("NEWLINE"):
            body = self.parse_block(value_position=True)
        else:
            body = [ExprStmt(self.parse_expression())]
            self.expect("NEWLINE")
        return FuncDef(name=name, params=tuple(params), body=body)

    def parse_param(self):
        """One parameter, with its type words dropped and its default kept.

        Pine writes these as `x`, `float x`, `series float x`, or
        `float phase = 0.0`. The type is consumed rather than recorded: it
        constrains what Pine accepts, not what the value means here.
        """
        while self.at("NAME") and (
            self.current.value in _TYPE_WORDS or self.current.value in self.user_types
        ):
            generic = self._generic_end(self.pos + 1)
            if generic is not None:
                self.pos = generic  # `array<float> xs`
                break
            if self._empty_brackets_at(self.pos + 1):
                self.pos += 3  # `float[] xs`, the older spelling
                break
            if self.tokens[self.pos + 1].kind != "NAME":
                break  # a parameter named after a type, as in `f(color) =>`
            self.advance()
        name = self.expect("NAME").value
        default = None
        if self.at("OP", "="):
            self.advance()
            default = self.parse_expression()
        return Param(name=name, default=default)

    def parse_switch(self):
        """Parse ``switch [subject]`` and its indented ``pattern => value`` block.

        Pine's switch is an expression, and it is exactly a chain of
        conditionals written vertically, so that is what it folds into. With a
        subject each pattern is compared against it; without one each pattern is
        already a condition.
        """
        self.advance()  # `switch`
        subject = None if self.at("NEWLINE") else self.parse_expression()
        self.expect("NEWLINE")
        self.expect("INDENT")

        cases = []
        while not self.at("DEDENT") and not self.at("EOF"):
            self.skip_newlines()
            if self.at("DEDENT") or self.at("EOF"):
                break
            # A case with no pattern before the arrow is the default.
            pattern = None if self.at("OP", "=>") else self.parse_expression()
            self.expect("OP", "=>")
            cases.append((pattern, self.parse_expression()))
            self.skip_newlines()
        if self.at("DEDENT"):
            self.advance()

        # Pine yields `na` when nothing matches and no default was written.
        result = Na()
        remaining = cases
        if cases and cases[-1][0] is None:
            result = cases[-1][1]
            remaining = cases[:-1]
        for pattern, value in reversed(remaining):
            test = Binary("==", subject, pattern) if subject is not None else pattern
            result = Ternary(test, value, result)
        return result

    def _at_type_declaration(self) -> bool:
        """True for `type Name` on its own line, which opens a type block.

        Requiring the newline keeps an ordinary variable called ``type`` --
        ``type = 5``, ``type x = 1`` -- out of it.
        """
        if not self.at("NAME", "type") or self.pos + 2 >= len(self.tokens):
            return False
        name, after = self.tokens[self.pos + 1], self.tokens[self.pos + 2]
        return name.kind == "NAME" and after.kind == "NEWLINE"

    def _is_call_at(self, index) -> bool:
        token = self.tokens[index] if index < len(self.tokens) else None
        return token is not None and token.kind == "OP" and token.value == "("

    def _empty_brackets_at(self, index) -> bool:
        """True for the `[]` in `float[] xs`, and never for `close[1]`.

        Emptiness is the whole discriminator: an array type carries nothing
        between the brackets, and a history access always carries an offset.
        """
        if index + 1 >= len(self.tokens):
            return False
        opening, closing = self.tokens[index], self.tokens[index + 1]
        return (
            opening.kind == "OP"
            and opening.value == "["
            and closing.kind == "OP"
            and closing.value == "]"
        )

    def _generic_end(self, index):
        """End of an ``array<float>``-style generic starting at ``index``.

        Returns None when the ``<`` is a comparison rather than a type
        parameter, which is why the contents are checked rather than assumed:
        ``count < limit`` must not be eaten as a type.
        """
        if self.tokens[index].kind != "OP" or self.tokens[index].value != "<":
            return None
        depth = 0
        i = index
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind in ("NEWLINE", "EOF"):
                return None
            if token.kind == "OP":
                if token.value == "<":
                    depth += 1
                elif token.value == ">":
                    depth -= 1
                    if depth == 0:
                        return i + 1
                elif token.value != ",":
                    return None  # arithmetic inside: it was a comparison
            elif token.kind != "NAME":
                return None
            i += 1
        return None

    def _skip_declared_type(self):
        """Consume a type annotation such as the ``float`` in ``float x = na``.

        Only a run of type words followed by ``name =`` counts, so the cast
        ``float(x)`` and a variable that happens to be called ``color`` are
        both left alone.
        """
        end = self.pos
        while self.tokens[end].kind == "NAME" and (
            self.tokens[end].value in _TYPE_WORDS
            or self.tokens[end].value in self.user_types
        ):
            end += 1
            generic = self._generic_end(end)
            if generic is not None:
                end = generic
                break  # `array<float>` is the whole annotation
            if self._empty_brackets_at(end):
                end += 2
                break  # `float[]` is the older spelling of the same thing

        # Back off one word at a time: the variable itself may be named after a
        # type, as in `string label = "x"`, and it must survive the scan.
        while end > self.pos:
            if end + 1 < len(self.tokens):
                name, operator = self.tokens[end], self.tokens[end + 1]
                if (
                    name.kind == "NAME"
                    and operator.kind == "OP"
                    and operator.value in ("=", ":=")
                ):
                    self.pos = end
                    return
            end -= 1

    def _at_tuple_assign(self) -> bool:
        """True when the `[` here opens a target list rather than a tuple value."""
        index, depth = self.pos, 0
        while index < len(self.tokens):
            token = self.tokens[index]
            if token.kind in ("NEWLINE", "EOF"):
                return False
            if token.kind == "OP":
                if token.value == "[":
                    depth += 1
                elif token.value == "]":
                    depth -= 1
                    if depth == 0:
                        nxt = self.tokens[index + 1]
                        return nxt.kind == "OP" and nxt.value in ("=", ":=")
            index += 1
        return False

    def parse_tuple_assign(self):
        self.expect("OP", "[")
        targets = []
        while not self.at("OP", "]"):
            targets.append(self.expect("NAME").value)
            if self.at("OP", ","):
                self.advance()
        self.expect("OP", "]")
        self.expect("OP", "=")
        value = self.parse_expression()
        self.expect("NEWLINE")
        return TupleAssign(targets=targets, value=value)

    def parse_block(self, value_position=False):
        """Parse an indented block.

        ``value_position`` marks a block whose statements stand for a value --
        a function body. There a bare ``switch`` is the value being returned,
        where at statement level the same text is a side-effecting block. The
        flag does not descend into nested blocks, which are control flow again.
        """
        self.expect("NEWLINE")
        self.expect("INDENT")
        body = []
        while not self.at("DEDENT") and not self.at("EOF"):
            self.skip_newlines()
            if self.at("DEDENT") or self.at("EOF"):
                break
            statement = self.parse_statement(value_position=value_position)
            if statement is not None:
                body.append(statement)
        if self.at("DEDENT"):
            self.advance()
        return body

    def parse_if_expression(self):
        """Parse an ``if`` used for its value rather than for its effect.

        Pine allows the same keyword in both roles. Read for its value it is a
        conditional expression with its arms on separate lines, so it folds
        into the same nesting a ternary or a ``switch`` does.
        """
        self.expect("NAME", "if")
        cond = self.parse_expression()
        then = self._branch_value(self.parse_block())

        # No `else` at all: Pine yields `na` when the condition is false.
        other = Na()
        self.skip_newlines()
        if self.at("NAME", "else"):
            self.advance()
            if self.at("NAME", "if"):
                other = self.parse_if_expression()
            else:
                other = self._branch_value(self.parse_block())
        return Ternary(cond, then, other)

    def _branch_value(self, body):
        """The single expression a value-carrying branch yields."""
        if len(body) == 1 and isinstance(body[0], ExprStmt):
            return body[0].value
        raise PineSyntaxError(
            "an if used for its value needs one expression per branch; this "
            "one carries a block, which a conditional expression cannot hold"
        )

    def parse_if(self):
        self.expect("NAME", "if")
        cond = self.parse_expression()
        body = self.parse_block()
        orelse = []
        self.skip_newlines()
        if self.at("NAME", "else"):
            self.advance()
            if self.at("NAME", "if"):
                orelse = [self.parse_if()]
            else:
                orelse = self.parse_block()
        return If(cond=cond, body=body, orelse=orelse)

    # --- expressions ---------------------------------------------------------

    def parse_expression(self):
        return self.parse_ternary()

    def parse_ternary(self):
        cond = self.parse_or()
        if self.at("OP", "?"):
            self.advance()
            then = self.parse_ternary()
            self.expect("OP", ":")
            other = self.parse_ternary()
            return Ternary(cond=cond, then=then, other=other)
        return cond

    def parse_or(self):
        node = self.parse_and()
        while self.at("NAME", "or"):
            self.advance()
            node = Binary("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.at("NAME", "and"):
            self.advance()
            node = Binary("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.at("NAME", "not"):
            self.advance()
            return Unary("not", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self):
        node = self.parse_additive()
        while self.current.kind == "OP" and self.current.value in _COMPARISONS:
            op = self.advance().value
            node = Binary(op, node, self.parse_additive())
        return node

    def parse_additive(self):
        node = self.parse_multiplicative()
        while self.current.kind == "OP" and self.current.value in ("+", "-"):
            op = self.advance().value
            node = Binary(op, node, self.parse_multiplicative())
        return node

    def parse_multiplicative(self):
        node = self.parse_unary()
        while self.current.kind == "OP" and self.current.value in ("*", "/", "%"):
            op = self.advance().value
            node = Binary(op, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.current.kind == "OP" and self.current.value in ("-", "+"):
            op = self.advance().value
            return Unary(op, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_primary()
        while True:
            if self.at("OP", "["):
                self.advance()
                offset = self.parse_expression()
                self.expect("OP", "]")
                node = Index(base=node, offset=offset)
            else:
                return node

    def parse_primary(self):
        token = self.current

        if token.kind == "NUMBER":
            self.advance()
            return Num(token.value)
        if token.kind == "STRING":
            self.advance()
            return Str(token.value)
        if token.kind == "OP" and token.value == "(":
            self.advance()
            node = self.parse_expression()
            self.expect("OP", ")")
            return node
        if token.kind == "OP" and token.value == "[":
            # A list in expression position, not a history index -- indexing is
            # postfix and never reaches here.
            self.advance()
            items = []
            while not self.at("OP", "]"):
                items.append(self.parse_expression())
                if self.at("OP", ","):
                    self.advance()
                elif not self.at("OP", "]"):
                    break
            self.expect("OP", "]")
            return ListLit(tuple(items))
        if token.kind == "NAME":
            self.advance()
            if token.value == "true":
                return Bool(True)
            if token.value == "false":
                return Bool(False)
            # Bare `na` is the missing-value literal, but `na(x)` is the call
            # that tests for it. Check for the paren before deciding.
            if token.value == "na" and not self.at("OP", "("):
                return Na()
            # `array.new<float>()` -- the type argument says nothing the
            # generator needs, and the call itself is reported downstream.
            generic = self._generic_end(self.pos)
            if generic is not None and self._is_call_at(generic):
                self.pos = generic
            if self.at("OP", "("):
                return self.parse_call(token.value)
            return Name(token.value)

        raise PineSyntaxError(f"unexpected {token.value!r} on line {token.line}")

    def parse_call(self, func):
        self.expect("OP", "(")
        args, kwargs = [], []
        while not self.at("OP", ")"):
            if (
                self.at("NAME")
                and self.tokens[self.pos + 1].kind == "OP"
                and self.tokens[self.pos + 1].value == "="
            ):
                key = self.advance().value
                self.advance()
                kwargs.append((key, self.parse_expression()))
            else:
                args.append(self.parse_expression())
            if self.at("OP", ","):
                self.advance()
            elif not self.at("OP", ")"):
                token = self.current
                raise PineSyntaxError(
                    f"expected ',' or ')' in call to {func} on line {token.line}"
                )
        self.expect("OP", ")")
        return Call(func=func, args=tuple(args), kwargs=tuple(kwargs))


def _declaration_title(call: Call) -> str:
    for key, value in call.kwargs:
        if key == "title" and isinstance(value, Str):
            return value.value
    for arg in call.args:
        if isinstance(arg, Str):
            return arg.value
    return ""


def parse(source: str) -> Program:
    """Parse Pine source into a :class:`Program`."""
    version_match = _VERSION_RE.search(source)
    version = int(version_match.group(1)) if version_match else None
    tokens = tokenize(source)
    return Parser(tokens, source.splitlines()).parse_program(version)
