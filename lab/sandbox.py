"""受限 Python 执行：AST 白名单 + 精简 builtins +  curated helpers。

不是 OS 级沙箱。用户代码只在独立子进程内、通过白名单 AST 校验后执行，
作用域内没有 open/import/eval/网络入口；超时由父进程强制终止。
"""
import ast
import hashlib
import json
import textwrap
from datetime import date, timedelta


class SandboxError(Exception):
    pass


class BusinessError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(f"{status} {code}: {message}")


class DatabaseBusy(RuntimeError):
    pass


class InjectedFailure(RuntimeError):
    pass


ALLOWED_NODES = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.Assign, ast.AugAssign, ast.Expr, ast.Call, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.If, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
    ast.With, ast.withitem, ast.Attribute, ast.Subscript, ast.Slice,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.IfExp, ast.Raise, ast.Try, ast.ExceptHandler, ast.Assert,
    ast.JoinedStr, ast.FormattedValue,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.BitAnd, ast.BitOr, ast.BitXor,
    ast.And, ast.Or, ast.Not, ast.UAdd, ast.USub,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.keyword, ast.Starred,
}

DENIED_NAMES = {
    'open', 'eval', 'exec', 'compile', '__import__', 'globals', 'locals', 'vars',
    'getattr', 'setattr', 'delattr', 'input', 'breakpoint', 'exit', 'quit',
    'memoryview', 'type', 'object', 'super',
}


def validate_user_code(code):
    if len(code) > 20000:
        raise SandboxError('代码过长（上限 20000 字符）')
    wrapped = 'def _solution(db, request):\n' + textwrap.indent(code, '    ')
    try:
        tree = ast.parse(wrapped)
    except SyntaxError as exc:
        raise SandboxError(f'语法错误：第 {exc.lineno - 1} 行：{exc.msg}') from None
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            line = getattr(node, 'lineno', None)
            raise SandboxError(f'不允许的语法：{type(node).__name__}（第 {line - 1 if line else "?"} 行）')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise SandboxError(f'不允许访问下划线属性：{node.attr}')
        if isinstance(node, ast.Name) and (node.id.startswith('_') or node.id in DENIED_NAMES):
            raise SandboxError(f'不允许使用的名称：{node.id}')
    return wrapped


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest_of(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def make_fail(recorder, actor):
    def fail(status, code, message):
        raise BusinessError(status, code, message)
    return fail


def safe_builtins(print_sink):
    return {
        'len': len, 'range': range, 'int': int, 'str': str, 'float': float,
        'bool': bool, 'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
        'frozenset': frozenset, 'sorted': sorted, 'min': min, 'max': max,
        'sum': sum, 'enumerate': enumerate, 'zip': zip, 'any': any, 'all': all,
        'abs': abs, 'repr': repr, 'isinstance': isinstance,
        'print': print_sink,
    }


def compile_solution(code, helpers, print_sink):
    wrapped = validate_user_code(code)
    scope = {'__builtins__': safe_builtins(print_sink)}
    scope.update(helpers)
    exec(compile(wrapped, '<learner-code>', 'exec'), scope)
    return scope['_solution']


def nights_of(request):
    start = date.fromisoformat(request['check_in'])
    end = date.fromisoformat(request['check_out'])
    count = (end - start).days
    if not 1 <= count <= 30:
        raise BusinessError(422, 'VALIDATION_ERROR', '入住晚数必须在 1 到 30 之间')
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def auxiliary_needs(request):
    """教学简化：2 个固定床位；2 岁以下儿童用婴儿床，超出固定床位用加床。"""
    cots = sum(1 for age in request['child_ages'] if age < 2)
    beds = request['adults'] + sum(1 for age in request['child_ages'] if age >= 2)
    return cots, max(0, beds - 2)


class JsonShim:
    @staticmethod
    def loads(text):
        return json.loads(text)

    @staticmethod
    def dumps(value):
        return canonical(value)