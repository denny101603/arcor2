import importlib
import os
import stat
from typing import List, Optional, Dict, Any

import autopep8  # type: ignore
import typed_astunparse  # type: ignore  # TODO remove when version with py.typed on pypi
from horast import parse, unparse  # type: ignore  # TODO remove when version with py.typed on pypi

from typed_ast.ast3 import Module, Assign, AnnAssign, Name, Store, Load, Subscript, Attribute, Index, FunctionDef,\
    NameConstant, Pass, arguments, If, Compare, Eq, Expr, Call, alias, keyword, ClassDef, arg, Return, While, Str,\
    ImportFrom, NodeVisitor, NodeTransformer, fix_missing_locations, Try, ExceptHandler

from arcor2.resources import ResourcesBase
from arcor2.source import SourceException, SCRIPT_HEADER
from arcor2.source.object_types import fix_object_name


def object_instance_from_res(tree: Module, object_id: str, cls_name: str) -> None:

    main_body = find_function("main", tree).body
    last_assign_idx = None

    for body_idx, body_item in enumerate(main_body):

        if isinstance(body_item, (Assign, AnnAssign)):
            last_assign_idx = body_idx

    if last_assign_idx is None:
        raise SourceException()

    assign = AnnAssign(
        target=Name(
            id=fix_object_name(object_id),
            ctx=Store()),
        annotation=Name(
            id=cls_name,
            ctx=Load()),
        value=Subscript(
            value=Attribute(
                value=Name(
                    id='res',
                    ctx=Load()),
                attr='objects',
                ctx=Load()),
            slice=Index(value=Str(
                s=object_id,
                kind='')),
            ctx=Load()),
        simple=1)

    main_body.insert(last_assign_idx + 1, assign)


def main_loop_body(tree: Module) -> List[Any]:

    main = find_function("main", tree)

    for node in main.body:
        if isinstance(node, Try):
            for bnode in node.body:
                if isinstance(bnode, While):  # TODO more specific condition
                    return bnode.body

    raise SourceException("Main loop not found.")


def empty_script_tree() -> Module:
    """
    Creates barebones of the script (empty 'main' function).

    Returns
    -------

    """

    # TODO helper function for try ... except

    tree = Module(body=[
        FunctionDef(name="main",
                    body=[
                        Try(body=[While(test=NameConstant(value=True), body=[Pass()], orelse=[])],
                            handlers=[ExceptHandler(type=Name(id="Arcor2Exception", ctx=Load()),
                                                    name='e',
                                                    body=[Expr(value=Call(
                                                        func=Name(
                                                            id='print_exception',
                                                            ctx=Load()),
                                                        args=[Name(
                                                            id='e',
                                                            ctx=Load())],
                                                        keywords=[]))]
                                                    )],
                            orelse=[],
                            finalbody=[],
                            )
                        ],
                    decorator_list=[],
                    args=arguments(args=[],
                                   vararg=None,
                                   kwonlyargs=[],
                                   kw_defaults=[],
                                   kwarg=None,
                                   defaults=[]),
                    returns=NameConstant(value=None)),

        If(
            test=Compare(
                left=Name(
                    id='__name__',
                    ctx=Load()),
                ops=[Eq()],
                comparators=[Str(
                    s='__main__',
                    kind='')]),
            body=[Try(
                body=[Expr(value=Call(
                    func=Name(
                        id='main',
                        ctx=Load()),
                    args=[],
                    keywords=[]))],
                handlers=[ExceptHandler(
                    type=Name(
                        id='Exception',
                        ctx=Load()),
                    name='e',
                    body=[Expr(value=Call(
                        func=Name(
                            id='print_exception',
                            ctx=Load()),
                        args=[Name(
                            id='e',
                            ctx=Load())],
                        keywords=[]))])],
                orelse=[],
                finalbody=[])],
            orelse=[])
    ])

    add_import(tree, "arcor2.exceptions", "Arcor2Exception")
    add_import(tree, "arcor2.exceptions", "print_exception")

    return tree


def find_function(name: str, tree: Module) -> FunctionDef:
    class FindFunction(NodeVisitor):

        def __init__(self) -> None:
            self.function_node: Optional[FunctionDef] = None

        def visit_FunctionDef(self, node: FunctionDef) -> None:
            if node.name == name:
                self.function_node = node
                return

            if not self.function_node:
                self.generic_visit(node)

    ff = FindFunction()
    ff.visit(tree)

    if ff.function_node is None:
        raise SourceException(f"Function {name} not found.")

    return ff.function_node


def add_import(node: Module, module: str, cls: str, try_to_import: bool = True) -> None:
    """
    Adds "from ... import ..." to the beginning of the script.

    Parameters
    ----------
    node
    module
    cls

    Returns
    -------

    """

    class AddImportTransformer(NodeTransformer):

        def __init__(self, module: str, cls: str) -> None:
            self.done = False
            self.module = module
            self.cls = cls

        def visit_ImportFrom(self, node: ImportFrom) -> ImportFrom:
            if node.module == self.module:

                for aliass in node.names:
                    if aliass.name == self.cls:
                        self.done = True
                        break
                else:
                    node.names.append(alias(name=self.cls, asname=None))
                    self.done = True

            return node

    if try_to_import:

        try:
            imported_mod = importlib.import_module(module)
        except ModuleNotFoundError as e:
            raise SourceException(e)

        try:
            getattr(imported_mod, cls)
        except AttributeError as e:
            raise SourceException(e)

    tr = AddImportTransformer(module, cls)
    node = tr.visit(node)

    if not tr.done:
        node.body.insert(0, ImportFrom(module=module, names=[alias(name=cls, asname=None)], level=0))


def add_cls_inst(node: Module, cls: str, name: str, kwargs: Optional[Dict] = None,
                 kwargs2parse: Optional[Dict] = None) -> None:
    class FindImport(NodeVisitor):

        def __init__(self, cls: str) -> None:

            self.found = False
            self.cls = cls

        def visit_ImportFrom(self, node: ImportFrom) -> None:

            for node_alias in node.names:
                if node_alias.name == self.cls:
                    self.found = True

            if not self.found:
                self.generic_visit(node)

    class FindClsInst(NodeVisitor):

        def __init__(self) -> None:

            self.found = False

        def visit_FunctionDef(self, node: FunctionDef) -> None:

            if node.name == 'main':

                for item in node.body:

                    if isinstance(item, Assign):

                        assert isinstance(item.targets[0], Name)

                        if item.targets[0].id == name:

                            # TODO assert for item.value
                            if item.value.func.id != cls:  # type: ignore
                                raise SourceException(
                                    "Name '{}' already used for instance of '{}'!".
                                    format(name, item.value.func.id))  # type: ignore

                            self.found = True
                            # TODO update arguments?

            if not self.found:
                self.generic_visit(node)

    class AddClsInst(NodeTransformer):

        def visit_FunctionDef(self, node: FunctionDef) -> FunctionDef:

            if node.name == 'main':

                kw = []

                if kwargs:
                    for k, v in kwargs.items():
                        kw.append(keyword(arg=k, value=v))

                if kwargs2parse:
                    for k, v in kwargs2parse.items():
                        kw.append(keyword(arg=k, value=parse(v)))

                node.body.insert(0, Assign(targets=[Name(id=name, ctx=Store())],
                                           value=Call(func=Name(id=cls, ctx=Load()),
                                                      args=[],
                                                      keywords=kw)))

            return node

    find_import = FindImport(cls)
    find_import.visit(node)

    if not find_import.found:
        raise SourceException("Class '{}' not imported!".format(cls))

    vis = FindClsInst()
    vis.visit(node)

    if not vis.found:
        tr = AddClsInst()
        node = tr.visit(node)


def append_method_call(body: List, instance: str, method: str, args: List, kwargs: List) -> None:

    body.append(Expr(value=Call(func=Attribute(value=Name(id=instance, ctx=Load()), attr=method, ctx=Load()),
                                args=args,
                                keywords=kwargs)))


def add_method_call_in_main(tree: Module, instance: str, method: str, args: List, kwargs: List) -> None:
    """
    Places method call after block where instances are created.

    Parameters
    ----------
    tree
    instance
    init
    args
    kwargs

    Returns
    -------

    """

    main_body = find_function("main", tree).body
    last_assign_idx = None

    for body_idx, body_item in enumerate(main_body):

        # TODO check if instance exists!

        if isinstance(body_item, Assign):
            last_assign_idx = body_idx

    if not last_assign_idx:
        raise SourceException()

    # TODO iterate over args/kwargs
    # TODO check actual number of method's arguments (and types?)
    main_body.insert(last_assign_idx + 1, Expr(value=Call(func=Attribute(value=Name(id=instance,
                                                                                    ctx=Load()),
                                                                         attr=method,
                                                                         ctx=Load()),
                                                          args=args, keywords=[])))


def get_name(name: str) -> Name:
    return Name(id=name, ctx=Load())


def tree_to_str(tree: Module) -> str:
    # TODO why this fails?
    # validator.visit(tree)

    fix_missing_locations(tree)
    generated_code: str = unparse(tree)
    generated_code = autopep8.fix_code(generated_code, options={'aggressive': 1})

    return generated_code


def make_executable(path_to_file: str) -> None:
    st = os.stat(path_to_file)
    os.chmod(path_to_file, st.st_mode | stat.S_IEXEC)


def tree_to_script(tree: Module, out_file: str, executable: bool) -> None:
    generated_code = tree_to_str(tree)

    with open(out_file, "w") as f:
        f.write(SCRIPT_HEADER)
        f.write(generated_code)

    if executable:
        make_executable(out_file)


def derived_resources_class(project_id: str, parameters: List[str]) -> str:
    tree = Module(body=[])

    # TODO avoid having "arcor2.resources" as string - how?
    add_import(tree, "arcor2.resources", ResourcesBase.__name__)

    derived_cls_name = "Resources"

    init_body: List = [Expr(value=Call(
        func=Attribute(value=Call(func=Name(id='super', ctx=Load()),
                                  args=[Name(id=derived_cls_name, ctx=Load()),
                                        Name(id='self', ctx=Load())], keywords=[]),
                       attr='__init__', ctx=Load()), args=[Str(s=project_id)],
        keywords=[]))]

    for param in parameters:
        init_body.append(Assign(targets=[Attribute(value=Name(id='self', ctx=Load()), attr="_" + param, ctx=Store())],
                                value=Call(
                                    func=Attribute(value=Name(id='self', ctx=Load()), attr='parameters', ctx=Load()),
                                    args=[Str(s=param)], keywords=[])))

    cls_def = ClassDef(name=derived_cls_name,
                       bases=[Name(id=ResourcesBase.__name__, ctx=Load())],
                       keywords=[],
                       body=[FunctionDef(name='__init__', args=arguments(args=[arg(arg='self', annotation=None)],
                                                                         vararg=None, kwonlyargs=[],
                                                                         kw_defaults=[], kwarg=None,
                                                                         defaults=[]), body=init_body,
                                         decorator_list=[], returns=None)], decorator_list=[])

    tree.body.append(cls_def)

    for param in parameters:
        cls_def.body.append(FunctionDef(
            name=param,
            args=arguments(
                args=[arg(
                    arg='self',
                    annotation=None,
                    type_comment=None)],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[]),
            body=[
                Expr(value=Call(
                    func=Attribute(
                        value=Name(
                            id='Resources',
                            ctx=Load()),
                        attr='print_info',
                        ctx=Load()),
                    args=[
                        Str(
                            s=param,
                            kind=''),
                        Attribute(
                            value=Name(
                                id='self',
                                ctx=Load()),
                            attr='_' + param,
                            ctx=Load())],
                    keywords=[])),
                Return(value=Attribute(
                    value=Name(
                        id='self',
                        ctx=Load()),
                    attr='_' + param,
                    ctx=Load()))],
            decorator_list=[Name(
                id='property',
                ctx=Load())],
            returns=None,
            type_comment=None))

    return tree_to_str(tree)


def dump(tree: Module) -> str:
    return typed_astunparse.dump(tree)