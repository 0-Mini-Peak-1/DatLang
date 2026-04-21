"""
DatLang Interpreter — v2
========================
Extensions over MVP:
  1. LET x = <expr>          — general variable assignment with arithmetic
  2. IF cond: ... ELSE: ...  — conditional branching
  3. Line numbers in all error messages

Pipeline: Lexer → Parser (AST) → Interpreter

Run:
    python3 datlang.py <source.dat> [--env <data.json>] [--debug]
"""

import re
import csv
import sys
import json
import os

# ─────────────────────────────────────────────────────────────────────────────
# 1.  ERRORS
# ─────────────────────────────────────────────────────────────────────────────

class DatLangError(Exception):
    def __init__(self, message: str, lineno: int = 0):
        self.lineno = lineno
        prefix = f"[Line {lineno}] " if lineno else ""
        super().__init__(f"{prefix}{message}")

class LexError(DatLangError):   pass
class ParseError(DatLangError): pass
class EvalError(DatLangError):  pass

# ─────────────────────────────────────────────────────────────────────────────
# 2.  LEXER
# ─────────────────────────────────────────────────────────────────────────────

TT_KEYWORD = "KEYWORD"
TT_IDENT   = "IDENTIFIER"
TT_NUMBER  = "NUMBER"
TT_STRING  = "STRING"
TT_OP      = "OP"
TT_COLON   = "COLON"
TT_DOT     = "DOT"
TT_COMMA   = "COMMA"
TT_LPAREN  = "LPAREN"
TT_RPAREN  = "RPAREN"
TT_NEWLINE = "NEWLINE"
TT_INDENT  = "INDENT"
TT_DEDENT  = "DEDENT"
TT_EOF     = "EOF"

KEYWORDS = {
    "FROM", "WHERE", "SELECT",
    "FOR", "each", "IN",
    "PRINT",
    "LET",
    "IF", "ELSE",
    "COUNT",
    "EXPORT", "TO",
}

TOKEN_SPEC = [
    ("NUMBER",   r"\d+(\.\d*)?"),
    ("STRING",   r'"[^"]*"|\'[^\']*\''),
    ("OP",       r">=|<=|!=|==|>|<|\+|-|\*|/|="),
    ("LPAREN",   r"\("),
    ("RPAREN",   r"\)"),
    ("DOT",      r"\."),
    ("COMMA",    r","),
    ("COLON",    r":"),
    ("NEWLINE",  r"\n"),
    ("SPACE",    r"[ \t]+"),
    ("IDENT",    r"[A-Za-z_]\w*"),
    ("COMMENT",  r"#[^\n]*"),
    ("MISMATCH", r"."),
]

TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC))


class Token:
    __slots__ = ("type", "value", "lineno")
    def __init__(self, type_: str, value, lineno: int = 0):
        self.type   = type_
        self.value  = value
        self.lineno = lineno
    def __repr__(self):
        return f"Token({self.type}, {self.value!r}, line={self.lineno})"


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    indent_stack = [0]
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    for lineno, line in enumerate(lines, start=1):
        stripped = line.rstrip("\n")
        if stripped.strip() == "":
            continue

        leading = len(stripped) - len(stripped.lstrip())

        if leading > indent_stack[-1]:
            indent_stack.append(leading)
            tokens.append(Token(TT_INDENT, leading, lineno))
        else:
            while indent_stack[-1] > leading:
                indent_stack.pop()
                tokens.append(Token(TT_DEDENT, indent_stack[-1], lineno))

        for mo in TOKEN_RE.finditer(stripped, leading):
            kind  = mo.lastgroup
            value = mo.group()

            if kind == "MISMATCH":
                raise LexError(f"Unexpected character {value!r}", lineno)
            if kind in ("SPACE", "COMMENT"):
                continue
            if kind == "NEWLINE":
                tokens.append(Token(TT_NEWLINE, "\n", lineno))
                break
            if kind == "IDENT":
                tt = TT_KEYWORD if value in KEYWORDS else TT_IDENT
                tokens.append(Token(tt, value, lineno))
            elif kind == "NUMBER":
                tokens.append(Token(TT_NUMBER, float(value) if "." in value else int(value), lineno))
            elif kind == "STRING":
                tokens.append(Token(TT_STRING, value[1:-1], lineno))
            elif kind == "OP":
                tokens.append(Token(TT_OP, value, lineno))
            elif kind == "DOT":
                tokens.append(Token(TT_DOT, ".", lineno))
            elif kind == "COMMA":
                tokens.append(Token(TT_COMMA, ",", lineno))
            elif kind == "COLON":
                tokens.append(Token(TT_COLON, ":", lineno))
            elif kind == "LPAREN":
                tokens.append(Token(TT_LPAREN, "(", lineno))
            elif kind == "RPAREN":
                tokens.append(Token(TT_RPAREN, ")", lineno))
        else:
            tokens.append(Token(TT_NEWLINE, "\n", lineno))

    while indent_stack[-1] > 0:
        indent_stack.pop()
        tokens.append(Token(TT_DEDENT, indent_stack[-1], 0))

    tokens.append(Token(TT_EOF, None, 0))
    return tokens

# ─────────────────────────────────────────────────────────────────────────────
# 3.  AST NODES
# ─────────────────────────────────────────────────────────────────────────────

class ProgramNode:
    def __init__(self, statements):
        self.statements = statements
    def __repr__(self):
        return f"ProgramNode({self.statements})"

class AssignNode:
    """<ident> = FROM … WHERE … SELECT …"""
    def __init__(self, name, query, lineno=0):
        self.name   = name
        self.query  = query
        self.lineno = lineno
    def __repr__(self):
        return f"AssignNode({self.name!r}, {self.query})"

class LetNode:
    """LET <ident> = <expr>"""
    def __init__(self, name, expr, lineno=0):
        self.name   = name
        self.expr   = expr
        self.lineno = lineno
    def __repr__(self):
        return f"LetNode({self.name!r} = {self.expr})"

class QueryNode:
    def __init__(self, source, condition, columns, lineno=0):
        self.source    = source
        self.condition = condition
        self.columns   = columns
        self.lineno    = lineno
    def __repr__(self):
        return f"QueryNode(FROM={self.source}, WHERE={self.condition}, SELECT={self.columns})"

class ForNode:
    def __init__(self, var, iterable, body, lineno=0):
        self.var      = var
        self.iterable = iterable
        self.body     = body
        self.lineno   = lineno
    def __repr__(self):
        return f"ForNode(each {self.var!r} IN {self.iterable!r}, body={self.body})"

class IfNode:
    """IF <condition>: <then_body> [ELSE: <else_body>]"""
    def __init__(self, condition, then_body, else_body=None, lineno=0):
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body
        self.lineno    = lineno
    def __repr__(self):
        return f"IfNode(cond={self.condition}, then={self.then_body}, else={self.else_body})"

class PrintNode:
    def __init__(self, expr, lineno=0):
        self.expr   = expr
        self.lineno = lineno
    def __repr__(self):
        return f"PrintNode({self.expr})"

class BinOpNode:
    def __init__(self, left, op, right, lineno=0):
        self.left   = left
        self.op     = op
        self.right  = right
        self.lineno = lineno
    def __repr__(self):
        return f"BinOpNode({self.left} {self.op} {self.right})"

class IdentNode:
    def __init__(self, name, lineno=0):
        self.name   = name
        self.lineno = lineno
    def __repr__(self):
        return f"IdentNode({self.name!r})"

class MemberAccessNode:
    def __init__(self, obj, attr, lineno=0):
        self.obj    = obj
        self.attr   = attr
        self.lineno = lineno
    def __repr__(self):
        return f"MemberAccessNode({self.obj!r}.{self.attr!r})"

class NumberNode:
    def __init__(self, value, lineno=0):
        self.value  = value
        self.lineno = lineno
    def __repr__(self):
        return f"NumberNode({self.value})"

class StringNode:
    def __init__(self, value, lineno=0):
        self.value  = value
        self.lineno = lineno
    def __repr__(self):
        return f"StringNode({self.value!r})"

class CountNode:
    """COUNT <table> — returns the number of rows in a table variable."""
    def __init__(self, table: str, lineno=0):
        self.table  = table
        self.lineno = lineno
    def __repr__(self):
        return f"CountNode({self.table!r})"

class ExportNode:
    """EXPORT <table> TO "<filename>" — writes a table to a file."""
    def __init__(self, table: str, filename: str, lineno=0):
        self.table    = table
        self.filename = filename
        self.lineno   = lineno
    def __repr__(self):
        return f"ExportNode({self.table!r} -> {self.filename!r})"

# ─────────────────────────────────────────────────────────────────────────────
# 4.  PARSER
# ─────────────────────────────────────────────────────────────────────────────

CMP_OPS      = {">", "<", ">=", "<=", "==", "!="}
ARITH_ADD    = {"+", "-"}
ARITH_MUL    = {"*", "/"}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos    = 0

    # ── helpers ──────────────────────────────────────────────────────────────

    def current(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def skip_newlines(self):
        while self.current().type in (TT_NEWLINE, TT_INDENT, TT_DEDENT):
            self.advance()

    def expect(self, type_: str, value=None) -> Token:
        tok = self.current()
        if tok.type != type_:
            raise ParseError(
                f"Expected {type_!r}{' (' + repr(value) + ')' if value else ''}"
                f" but got {tok.type!r} ({tok.value!r})",
                tok.lineno,
            )
        if value is not None and tok.value != value:
            raise ParseError(
                f"Expected {value!r} but got {tok.value!r}",
                tok.lineno,
            )
        return self.advance()

    # ── grammar ──────────────────────────────────────────────────────────────

    def parse(self) -> ProgramNode:
        self.skip_newlines()
        stmts = self.parse_stmt_list(top_level=True)
        self.expect(TT_EOF)
        return ProgramNode(stmts)

    def parse_stmt_list(self, top_level=False) -> list:
        stmts = []
        while True:
            if self.current().type == TT_EOF:
                break
            if not top_level and self.current().type == TT_DEDENT:
                break
            self.skip_newlines()
            if self.current().type == TT_EOF:
                break
            if not top_level and self.current().type == TT_DEDENT:
                break
            stmt = self.parse_stmt()
            if stmt is not None:
                stmts.append(stmt)
        return stmts

    def parse_stmt(self):
        tok = self.current()

        if tok.type == TT_KEYWORD:
            if tok.value == "LET":    return self.parse_let()
            if tok.value == "IF":     return self.parse_if()
            if tok.value == "FOR":    return self.parse_for()
            if tok.value == "PRINT":  return self.parse_print()
            if tok.value == "EXPORT": return self.parse_export()

        if tok.type == TT_IDENT:
            return self.parse_assign()

        if tok.type in (TT_NEWLINE, TT_INDENT, TT_DEDENT):
            self.advance()
            return None

        raise ParseError(f"Unexpected token {tok.value!r}", tok.lineno)

    # ── LET ──────────────────────────────────────────────────────────────────

    def parse_let(self) -> LetNode:
        lineno = self.current().lineno
        self.expect(TT_KEYWORD, "LET")
        name = self.expect(TT_IDENT).value
        self.expect(TT_OP, "=")
        expr = self.parse_expr()
        if self.current().type == TT_NEWLINE:
            self.advance()
        return LetNode(name, expr, lineno)

    # ── query assignment ──────────────────────────────────────────────────────

    def parse_assign(self) -> AssignNode:
        lineno   = self.current().lineno
        name_tok = self.expect(TT_IDENT)
        self.expect(TT_OP, "=")
        query = self.parse_query()
        if self.current().type == TT_NEWLINE:
            self.advance()
        return AssignNode(name_tok.value, query, lineno)

    def parse_query(self) -> QueryNode:
        lineno = self.current().lineno
        self.expect(TT_KEYWORD, "FROM")
        source = self.expect(TT_IDENT).value
        self.skip_newlines()
        self.expect(TT_KEYWORD, "WHERE")
        condition = self.parse_condition()
        self.skip_newlines()
        self.expect(TT_KEYWORD, "SELECT")
        columns = self.parse_column_list()
        return QueryNode(source, condition, columns, lineno)

    # ── IF / ELSE ─────────────────────────────────────────────────────────────

    def parse_if(self) -> IfNode:
        lineno = self.current().lineno
        self.expect(TT_KEYWORD, "IF")
        condition = self.parse_condition()
        self.expect(TT_COLON)
        if self.current().type == TT_NEWLINE:
            self.advance()
        self.expect(TT_INDENT)
        then_body = self.parse_stmt_list(top_level=False)
        if self.current().type == TT_DEDENT:
            self.advance()

        else_body = None
        if self.current().type == TT_KEYWORD and self.current().value == "ELSE":
            self.advance()
            self.expect(TT_COLON)
            if self.current().type == TT_NEWLINE:
                self.advance()
            self.expect(TT_INDENT)
            else_body = self.parse_stmt_list(top_level=False)
            if self.current().type == TT_DEDENT:
                self.advance()

        return IfNode(condition, then_body, else_body, lineno)

    # ── FOR ───────────────────────────────────────────────────────────────────

    def parse_for(self) -> ForNode:
        lineno = self.current().lineno
        self.expect(TT_KEYWORD, "FOR")
        self.expect(TT_KEYWORD, "each")
        var      = self.expect(TT_IDENT).value
        self.expect(TT_KEYWORD, "IN")
        iterable = self.expect(TT_IDENT).value
        self.expect(TT_COLON)
        if self.current().type == TT_NEWLINE:
            self.advance()
        self.expect(TT_INDENT)
        body = self.parse_stmt_list(top_level=False)
        if self.current().type == TT_DEDENT:
            self.advance()
        return ForNode(var, iterable, body, lineno)

    # ── EXPORT ───────────────────────────────────────────────────────────────

    def parse_export(self) -> ExportNode:
        """EXPORT <ident> TO "<filename>"""
        lineno = self.current().lineno
        self.expect(TT_KEYWORD, "EXPORT")
        table_tok = self.expect(TT_IDENT)
        self.expect(TT_KEYWORD, "TO")
        file_tok = self.expect(TT_STRING)
        if self.current().type == TT_NEWLINE:
            self.advance()
        return ExportNode(table_tok.value, file_tok.value, lineno)

    # ── PRINT ─────────────────────────────────────────────────────────────────

    def parse_print(self) -> PrintNode:
        lineno = self.current().lineno
        self.expect(TT_KEYWORD, "PRINT")
        expr = self.parse_expr()
        if self.current().type == TT_NEWLINE:
            self.advance()
        return PrintNode(expr, lineno)

    # ── expressions (arithmetic + member access) ──────────────────────────────

    def parse_condition(self) -> BinOpNode:
        """A comparison: <expr> <cmp_op> <expr>"""
        lineno = self.current().lineno
        left   = self.parse_expr()
        op_tok = self.current()
        if op_tok.type != TT_OP or op_tok.value not in CMP_OPS:
            raise ParseError(
                f"Expected comparison operator but got {op_tok.value!r}", op_tok.lineno
            )
        self.advance()
        right = self.parse_expr()
        return BinOpNode(left, op_tok.value, right, lineno)

    def parse_column_list(self) -> list[str]:
        cols = [self.expect(TT_IDENT).value]
        while self.current().type == TT_COMMA:
            self.advance()
            cols.append(self.expect(TT_IDENT).value)
        return cols

    def parse_expr(self):
        """Addition / subtraction (lowest precedence)."""
        left = self.parse_term()
        while self.current().type == TT_OP and self.current().value in ARITH_ADD:
            op  = self.advance()
            right = self.parse_term()
            left = BinOpNode(left, op.value, right, op.lineno)
        return left

    def parse_term(self):
        """Multiplication / division."""
        left = self.parse_factor()
        while self.current().type == TT_OP and self.current().value in ARITH_MUL:
            op  = self.advance()
            right = self.parse_factor()
            left = BinOpNode(left, op.value, right, op.lineno)
        return left

    def parse_factor(self):
        """Parentheses, literals, identifiers, member access."""
        tok = self.current()

        if tok.type == TT_LPAREN:
            self.advance()
            expr = self.parse_expr()
            self.expect(TT_RPAREN)
            return expr

        if tok.type == TT_NUMBER:
            self.advance()
            return NumberNode(tok.value, tok.lineno)

        if tok.type == TT_STRING:
            self.advance()
            return StringNode(tok.value, tok.lineno)

        if tok.type == TT_IDENT:
            self.advance()
            if self.current().type == TT_DOT:
                self.advance()
                attr = self.expect(TT_IDENT)
                return MemberAccessNode(tok.value, attr.value, tok.lineno)
            return IdentNode(tok.value, tok.lineno)

        if tok.type == TT_KEYWORD and tok.value == "COUNT":
            lineno = tok.lineno
            self.advance()  # consume COUNT
            table_tok = self.expect(TT_IDENT)
            return CountNode(table_tok.value, lineno)

        raise ParseError(f"Unexpected token {tok.value!r} in expression", tok.lineno)

# ─────────────────────────────────────────────────────────────────────────────
# 5.  INTERPRETER
# ─────────────────────────────────────────────────────────────────────────────

CMP_FNS = {
    ">":  lambda a, b: a >  b,
    "<":  lambda a, b: a <  b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

ARITH_FNS = {
    "+": lambda a, b: str(a) + str(b) if isinstance(a, str) or isinstance(b, str) else a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
}

_SENTINEL = object()


class Interpreter:
    def __init__(self, initial_env: dict | None = None):
        self.env: dict = dict(initial_env) if initial_env else {}

    def run(self, program: ProgramNode):
        for stmt in program.statements:
            self.exec_stmt(stmt)

    # ── statements ────────────────────────────────────────────────────────────

    def exec_stmt(self, node):
        if   isinstance(node, AssignNode):  self.exec_assign(node)
        elif isinstance(node, LetNode):     self.exec_let(node)
        elif isinstance(node, ForNode):     self.exec_for(node)
        elif isinstance(node, IfNode):      self.exec_if(node)
        elif isinstance(node, PrintNode):   self.exec_print(node)
        elif isinstance(node, ExportNode):  self.exec_export(node)
        elif node is None: pass
        else: raise EvalError(f"Unknown statement: {type(node).__name__}")

    def exec_assign(self, node: AssignNode):
        self.env[node.name] = self.eval_query(node.query)

    def exec_let(self, node: LetNode):
        self.env[node.name] = self.eval_expr(node.expr)

    def exec_for(self, node: ForNode):
        iterable = self.env.get(node.iterable)
        if iterable is None:
            raise EvalError(f"Undefined variable: {node.iterable!r}", node.lineno)
        outer = self.env.get(node.var, _SENTINEL)
        for item in iterable:
            self.env[node.var] = item
            for stmt in node.body:
                self.exec_stmt(stmt)
        if outer is _SENTINEL:
            self.env.pop(node.var, None)
        else:
            self.env[node.var] = outer

    def exec_if(self, node: IfNode):
        if self.eval_cmp(node.condition):
            for stmt in node.then_body:
                self.exec_stmt(stmt)
        elif node.else_body is not None:
            for stmt in node.else_body:
                self.exec_stmt(stmt)

    def exec_print(self, node: PrintNode):
        print(self.eval_expr(node.expr))

    def exec_export(self, node: ExportNode):
        """Write a table variable to a .json, .csv, or .tsv file."""
        table = self.env.get(node.table)
        if table is None:
            raise EvalError(f"Undefined variable: {node.table!r}", node.lineno)
        if not isinstance(table, list):
            raise EvalError(
                f"{node.table!r} is not a table (list of dicts)", node.lineno
            )

        ext = os.path.splitext(node.filename)[1].lower()

        if ext == ".json":
            with open(node.filename, "w") as fh:
                json.dump({node.table: table}, fh, indent=2)

        elif ext in (".csv", ".tsv"):
            if not table:
                open(node.filename, "w").close()
                return
            delimiter = "\t" if ext == ".tsv" else ","
            fieldnames = list(table[0].keys())
            with open(node.filename, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter=delimiter)
                writer.writeheader()
                writer.writerows(table)

        else:
            raise EvalError(
                f"Unsupported export format {ext!r}. Use .json, .csv, or .tsv",
                node.lineno
            )

        print(f"[DatLang] Exported {len(table)} rows to {node.filename!r}")

    # ── query evaluation ──────────────────────────────────────────────────────

    def eval_query(self, node: QueryNode) -> list[dict]:
        table = self.env.get(node.source)
        if table is None:
            raise EvalError(f"Undefined variable: {node.source!r}", node.lineno)
        if not isinstance(table, list):
            raise EvalError(
                f"FROM source {node.source!r} must be a list of dicts", node.lineno
            )
        filtered = [row for row in table if self.eval_cmp(node.condition, row)]
        return [{col: row[col] for col in node.columns if col in row} for row in filtered]

    def eval_cmp(self, node: BinOpNode, row: dict | None = None) -> bool:
        left  = self.eval_expr(node.left,  row)
        right = self.eval_expr(node.right, row)
        fn = CMP_FNS.get(node.op)
        if fn is None:
            raise EvalError(f"Unknown comparison operator: {node.op!r}", node.lineno)
        return fn(left, right)

    # ── expression evaluation ─────────────────────────────────────────────────

    def eval_expr(self, node, row: dict | None = None):
        if isinstance(node, NumberNode):
            return node.value

        if isinstance(node, StringNode):
            return node.value

        if isinstance(node, IdentNode):
            if row is not None and node.name in row:
                return row[node.name]
            if node.name in self.env:
                return self.env[node.name]
            raise EvalError(f"Undefined name: {node.name!r}", node.lineno)

        if isinstance(node, MemberAccessNode):
            obj = self.env.get(node.obj)
            if obj is None:
                raise EvalError(f"Undefined variable: {node.obj!r}", node.lineno)
            if not isinstance(obj, dict):
                raise EvalError(
                    f"{node.obj!r} is not a record (got {type(obj).__name__})", node.lineno
                )
            if node.attr not in obj:
                raise EvalError(f"Key {node.attr!r} not in {node.obj!r}", node.lineno)
            return obj[node.attr]

        if isinstance(node, BinOpNode):
            # arithmetic operators
            fn = ARITH_FNS.get(node.op)
            if fn is not None:
                left  = self.eval_expr(node.left,  row)
                right = self.eval_expr(node.right, row)
                try:
                    return fn(left, right)
                except ZeroDivisionError:
                    raise EvalError("Division by zero", node.lineno)
            # comparison operators (used inside expressions)
            if node.op in CMP_FNS:
                return self.eval_cmp(node, row)
            raise EvalError(f"Unknown operator: {node.op!r}", node.lineno)

        if isinstance(node, CountNode):
            table = self.env.get(node.table)
            if table is None:
                raise EvalError(f"Undefined variable: {node.table!r}", node.lineno)
            if not isinstance(table, list):
                raise EvalError(
                    f"{node.table!r} is not a table", node.lineno
                )
            return len(table)

        raise EvalError(f"Unknown expression node: {type(node).__name__}")

# ─────────────────────────────────────────────────────────────────────────────
# 6.  CLI RUNNER
# ─────────────────────────────────────────────────────────────────────────────

USAGE = """
Usage:
  python3 datlang.py <source.dat> [--debug] [--env <data-file>]

Arguments:
  <source.dat>    Path to a DatLang source file (.dat)
  --debug         Show token list and AST before running
  --env <file>    Data file to load into the environment.
                  Supported formats (auto-detected by extension):
                    .json  — JSON object mapping table names to row lists
                    .csv   — CSV file; loaded as table named by the filename stem
                    .tsv   — TSV file; same as CSV but tab-separated
  -h / --help     Show this message

If no source file is given, a built-in demo is run.
""".strip()


def _try_numeric(value: str):
    """Convert a CSV string cell to int or float if possible."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_env(env_path: str) -> dict:
    """Load an environment file (.json, .csv, or .tsv) into a dict."""
    ext = os.path.splitext(env_path)[1].lower()

    if ext == ".json":
        with open(env_path) as fh:
            return json.load(fh)

    if ext in (".csv", ".tsv"):
        delimiter = "\t" if ext == ".tsv" else ","
        table_name = os.path.splitext(os.path.basename(env_path))[0]
        rows = []
        with open(env_path, newline="") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            for row in reader:
                rows.append({k: _try_numeric(v) for k, v in row.items()})
        return {table_name: rows}

    raise ValueError(
        f"Unsupported env file format: {ext!r}. "
        "Use .json, .csv, or .tsv"
    )

_DEMO_SOURCE = """\
LET threshold = 49

result = FROM students
         WHERE score > 49
         SELECT name, score

FOR each student IN result:
    IF student.score > 80:
        PRINT student.name
    ELSE:
        PRINT student.name
"""

_DEMO_ENV = {
    "students": [
        {"name": "Alice",   "score": 85},
        {"name": "Bob",     "score": 40},
        {"name": "Charlie", "score": 75},
        {"name": "Diana",   "score": 92},
        {"name": "Evan",    "score": 55},
    ]
}


def run_source(source: str, initial_env: dict, *, debug: bool = False):
    """Full pipeline: Lex → Parse → Interpret."""
    tokens = tokenize(source)
    if debug:
        print("-- Tokens --")
        for tok in tokens:
            print(f"  {tok}")
        print()

    parser = Parser(tokens)
    ast    = parser.parse()
    if debug:
        print("-- AST --")
        for stmt in ast.statements:
            print(f"  {stmt}")
        print()

    Interpreter(initial_env=initial_env).run(ast)


def main():
    args  = sys.argv[1:]
    debug = "--debug" in args
    if debug:
        args.remove("--debug")

    initial_env: dict = {}
    if "--env" in args:
        idx = args.index("--env")
        if idx + 1 >= len(args):
            print("Error: --env requires a file path.", file=sys.stderr)
            sys.exit(1)
        env_path = args.pop(idx + 1)
        args.pop(idx)
        try:
            initial_env = load_env(env_path)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    if "-h" in args or "--help" in args:
        print(USAGE)
        sys.exit(0)

    if not args:
        print("=== DatLang Interpreter — built-in demo ===\n")
        run_source(_DEMO_SOURCE, _DEMO_ENV, debug=debug)
        return

    source_path = args[0]
    if not os.path.isfile(source_path):
        print(f"Error: file not found: {source_path!r}", file=sys.stderr)
        sys.exit(1)

    with open(source_path) as fh:
        source = fh.read()

    if debug:
        print(f"=== DatLang — {source_path} ===\n")

    try:
        run_source(source, initial_env, debug=debug)
    except DatLangError as e:
        print(f"\n❌  DatLang Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
