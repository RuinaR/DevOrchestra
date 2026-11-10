"""Admission grammar for explicit local imports in the MXC Python harness."""
import ast
import re
import sys

from .lease import Blocked
from .python_vectors import checked_function

TOOL = "mxc-python-modules-v1"


def validate_graph(graph, entry, names):
    if (type(graph) is not dict or set(graph) != set(names) or not 2 <= len(graph) <= 4
            or type(entry) is not str or entry not in graph
            or any(type(name) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}\.py", name)
                   or name[:-3] in sys.stdlib_module_names for name in graph)
            or any(type(items) is not list or any(type(item) is not str or item not in graph for item in items)
                   or len(items) != len(set(items)) for items in graph.values())):
        raise Blocked("Invalid local Python module import contract")
    ordered, active = [], set()

    def visit(name):
        if name in active:
            raise Blocked("Circular Python imports are outside the approved runtime")
        if name not in ordered:
            active.add(name)
            for dependency in graph[name]:
                visit(dependency)
            active.remove(name)
            ordered.append(name)

    visit(entry)
    if set(ordered) != set(graph):
        raise Blocked("Every approved module must be reachable from the entry point")
    return ordered


def validate_sources(sources, specs, graph, entry):
    functions = {}
    for name in validate_graph(graph, entry, sources):
        try:
            tree = ast.parse(sources[name])
        except (SyntaxError, ValueError, RecursionError):
            raise Blocked("Invalid Python module source") from None
        imports, body = {}, []
        for node in tree.body:
            if type(node) is ast.ImportFrom and not body:
                dependency = f"{node.module}.py"
                if (node.level != 0 or dependency not in graph[name] or dependency in imports
                        or len(node.names) != 1 or node.names[0].asname is not None
                        or node.names[0].name != specs[dependency]["function"]):
                    raise Blocked("Python import is outside the approved local graph")
                imports[dependency] = node.names[0].name
            else:
                body.append(node)
        if set(imports) != set(graph[name]) or len(set(imports.values())) != len(imports):
            raise Blocked("Python imports do not match the approved graph")
        module = ast.Module(body=body, type_ignores=[])
        if specs[name]["function"] in imports.values() or (
                body and type(body[0]) is ast.FunctionDef
                and any(arg.arg in imports.values() for arg in body[0].args.args)):
            raise Blocked("Local import shadows an approved function or argument")
        calls = {node.func.id for node in ast.walk(module)
                 if type(node) is ast.Call and type(node.func) is ast.Name}
        if calls != set(imports.values()):
            raise Blocked("Approved local imports must be called directly")
        functions[name] = checked_function(
            module, specs[name], {function: functions[dep] for dep, function in imports.items()},
        )
