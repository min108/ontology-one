# 比较两棵目录下全部 .py 文件的语法树是否等价（文档字符串按空白规整后比较）。用于核验“只改格式”的提交。
"""Compare two trees of .py files for AST equivalence (docstrings normalized). Usage: ast_equiv.py BEFORE AFTER dir..."""
import ast, sys, pathlib
before, after, dirs = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3:]
def norm(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) and isinstance(body[0].value.value, str):
                body[0].value.value = ' '.join(body[0].value.value.split())
    return ast.dump(tree, include_attributes=False)
strict = lambda src: ast.dump(ast.parse(src), include_attributes=False)
total = same_strict = same_norm = 0; diff = []
for d in dirs:
    for p in sorted((before / d).rglob('*.py')):
        rel = p.relative_to(before); q = after / rel
        a, b = p.read_text(encoding='utf-8'), q.read_text(encoding='utf-8')
        total += 1
        if strict(a) == strict(b): same_strict += 1; same_norm += 1
        elif norm(a) == norm(b): same_norm += 1
        else: diff.append(str(rel))
print(f'files={total} strict_equal={same_strict} equal_after_docstring_whitespace_normalization={same_norm} different={len(diff)}')
for x in diff: print('DIFF', x)
