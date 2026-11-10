"""Fixed deterministic harness for one bounded integer function."""
import ast
import operator
import re

from .lease import Blocked

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def _integer(value):
    return type(value) is int and abs(value) <= 1_000_000


def validate_vectors(spec, acceptance):
    if (type(spec) is not dict or set(spec) != {"function", "acceptance", "vectors"}
            or type(spec["function"]) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", spec["function"])
            or not acceptance or spec["acceptance"] != acceptance or type(spec["vectors"]) is not list
            or not 1 <= len(spec["vectors"]) <= 20
            or any(type(row) is not list or len(row) != 2 or not all(_integer(x) for x in row)
                   for row in spec["vectors"])):
        raise Blocked("Invalid approved Python vectors or acceptance binding")


def checked_function(tree, spec, project_calls=None):
    """Check the full expression and return its bounded interpreter."""
    project_calls = project_calls or {}
    if len(tree.body) != 1 or type(tree.body[0]) is not ast.FunctionDef:
        raise Blocked("Verifier supports exactly one plain function")
    fn = tree.body[0]
    args = fn.args
    if (fn.name != spec["function"] or fn.decorator_list or fn.returns or getattr(fn, "type_params", ())
            or args.posonlyargs or len(args.args) != 1 or args.args[0].annotation or args.kwonlyargs
            or args.vararg or args.kwarg or args.defaults):
        raise Blocked("Verifier supports one undecorated integer argument")
    statements = fn.body[1:] if ast.get_docstring(fn, clean=False) is not None else fn.body
    if len(statements) != 1 or type(statements[0]) is not ast.Return:
        raise Blocked("Verifier supports a single return expression")
    argument = args.args[0].arg

    def expression(node, depth=0):
        if depth > 12:
            raise Blocked("Verifier expression depth exceeded")
        if type(node) is ast.Name and node.id == argument:
            return lambda value: value
        if (type(node) is ast.Call and type(node.func) is ast.Name and node.func.id in project_calls
                and len(node.args) == 1 and not node.keywords):
            operand = expression(node.args[0], depth + 1)
            function = project_calls[node.func.id]
            return lambda value: function(operand(value))
        if type(node) is ast.Constant and _integer(node.value):
            return lambda _value: node.value
        if type(node) is ast.UnaryOp and type(node.op) in (ast.UAdd, ast.USub):
            operand = expression(node.operand, depth + 1)
            return lambda value: operand(value) if type(node.op) is ast.UAdd else -operand(value)
        if type(node) is ast.IfExp:
            test, yes, no = (expression(part, depth + 1) for part in (node.test, node.body, node.orelse))
            return lambda value: yes(value) if test(value) else no(value)
        if type(node) is ast.BinOp and type(node.op) in (ast.Add, ast.Sub, ast.Mult):
            left, right = expression(node.left, depth + 1), expression(node.right, depth + 1)
            def arithmetic(value):
                result = OPS[type(node.op)](left(value), right(value))
                if not _integer(result):
                    raise Blocked("Verifier integer result exceeds its limit")
                return result
            return arithmetic
        if type(node) is ast.Compare and len(node.ops) == 1 and type(node.ops[0]) in OPS:
            left, right = expression(node.left, depth + 1), expression(node.comparators[0], depth + 1)
            return lambda value: OPS[type(node.ops[0])](left(value), right(value))
        raise Blocked("Verifier encountered an unsupported Python expression")

    return expression(statements[0].value)


def run_vectors(source, spec):
    """Compile for syntax and interpret only allowlisted integer AST nodes. No imports or exec."""
    try:
        compile(source, "<approved source>", "exec", dont_inherit=True)
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {"verdict": "FAIL", "exit_code": 1, "syntax": "FAIL", "observations": []}
    evaluate = checked_function(tree, spec)
    observed = [{"input": value, "expected": expected, "actual": evaluate(value)}
                for value, expected in spec["vectors"]]
    passed = all(type(row["actual"]) is int and row["actual"] == row["expected"] for row in observed)
    return {"verdict": "PASS" if passed else "FAIL", "exit_code": 0 if passed else 1,
            "syntax": "PASS", "observations": observed}
