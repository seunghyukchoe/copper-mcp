"""A small iterative, budgeted S-expression reader for KiCad source bytes."""

from __future__ import annotations

from dataclasses import dataclass

from copper_mcp.board_ir.limits import ParseLimits


@dataclass(frozen=True, slots=True)
class SExprError(ValueError):
    code: str
    message: str
    offset: int

    def __str__(self) -> str:
        return f"{self.code} at byte {self.offset}: {self.message}"


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: str
    offset: int


@dataclass(frozen=True, slots=True)
class SExpr:
    items: tuple[str | SExpr, ...]
    offset: int

    @property
    def head(self) -> str | None:
        return self.items[0] if self.items and isinstance(self.items[0], str) else None


def _tokens(text: str, limits: ParseLimits) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    length = len(text)
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character in "()":
            tokens.append(Token(character, character, index))
            index += 1
        elif character == '"':
            start = index
            index += 1
            string_chars: list[str] = []
            while index < length and text[index] != '"':
                if text[index] == "\\":
                    index += 1
                    if index >= length:
                        raise SExprError("syntax.invalid", "unterminated string escape", start)
                    if text[index] not in escapes:
                        raise SExprError("syntax.invalid", "unsupported string escape", index)
                    string_chars.append(escapes[text[index]])
                    index += 1
                else:
                    string_chars.append(text[index])
                    index += 1
                if len(string_chars) > limits.max_atom_chars:
                    raise SExprError("budget.exceeded", "string length budget exceeded", start)
            if index >= length:
                raise SExprError("syntax.invalid", "unterminated quoted string", start)
            index += 1
            tokens.append(Token("atom", "".join(string_chars), start))
        else:
            start = index
            while index < length and not text[index].isspace() and text[index] not in "()":
                index += 1
            atom_value = text[start:index]
            if len(atom_value) > limits.max_atom_chars:
                raise SExprError("budget.exceeded", "atom length budget exceeded", start)
            tokens.append(Token("atom", atom_value, start))
        if len(tokens) > limits.max_tokens:
            raise SExprError("budget.exceeded", "token budget exceeded", index)
    return tokens


def parse_sexpr(payload: bytes, limits: ParseLimits | None = None) -> SExpr:
    """Decode exactly one bounded UTF-8 S-expression without recursion."""

    limits = limits or ParseLimits()
    if not isinstance(payload, bytes) or len(payload) > limits.max_input_bytes:
        raise SExprError("budget.exceeded", "input byte budget exceeded", 0)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SExprError("syntax.invalid", "source must be valid UTF-8", error.start) from error

    roots: list[str | SExpr] = []
    stack: list[tuple[list[str | SExpr], int]] = []
    nodes = 0
    for token in _tokens(text, limits):
        target = stack[-1][0] if stack else roots
        if token.kind == "(":
            if len(stack) + 1 > limits.max_depth:
                raise SExprError("budget.exceeded", "nesting depth budget exceeded", token.offset)
            stack.append(([], token.offset))
            continue
        if token.kind == ")":
            if not stack:
                raise SExprError("syntax.invalid", "unexpected closing parenthesis", token.offset)
            children, offset = stack.pop()
            if not children:
                raise SExprError("syntax.invalid", "empty list is unsupported", offset)
            expression = SExpr(tuple(children), offset)
            target = stack[-1][0] if stack else roots
            target.append(expression)
            nodes += 1
        else:
            if not stack:
                raise SExprError("syntax.invalid", "atom outside root expression", token.offset)
            target.append(token.value)
            nodes += 1
        if len(target) > limits.max_children_per_list:
            raise SExprError("budget.exceeded", "list child budget exceeded", token.offset)
        if nodes > limits.max_nodes:
            raise SExprError("budget.exceeded", "node budget exceeded", token.offset)
    if stack:
        raise SExprError("syntax.invalid", "unterminated list", stack[-1][1])
    if len(roots) != 1 or not isinstance(roots[0], SExpr):
        raise SExprError("syntax.invalid", "source must contain exactly one root list", 0)
    return roots[0]


def children(expression: SExpr, head: str) -> tuple[SExpr, ...]:
    """Return direct child expressions with the requested head."""

    return tuple(
        item for item in expression.items[1:] if isinstance(item, SExpr) and item.head == head
    )


def child(expression: SExpr, head: str) -> SExpr | None:
    """Return one direct child and reject ambiguous duplicate fields."""

    matches = children(expression, head)
    if len(matches) > 1:
        raise SExprError("syntax.duplicate_field", f"duplicate {head} field", matches[1].offset)
    return matches[0] if matches else None


def atoms(expression: SExpr) -> tuple[str, ...]:
    """Return a flat expression payload and reject unexpected nested values."""

    values = expression.items[1:]
    if not all(isinstance(value, str) for value in values):
        raise SExprError(
            "syntax.invalid",
            f"{expression.head or 'expression'} must contain atoms",
            expression.offset,
        )
    return tuple(value for value in values if isinstance(value, str))
