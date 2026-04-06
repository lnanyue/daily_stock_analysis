#!/usr/bin/env python3
"""
Convert logger.XXX(f"...") to logger.XXX("...", args) style.
Handles slicing, format specs, multi-line, exc_info, etc.
"""
import re, sys, py_compile, pathlib

def convert_body(body: str):
    """Convert f-string body to (fmt_string, [args]).
    Properly handles {expr!r}, {expr:.2f}, {expr[:50]}, {expr['key']}, etc.
    """
    args = []

    # We parse character by character to properly handle nested brackets
    result = []
    i = 0
    while i < len(body):
        if body[i] == '{':
            # Find matching }
            depth = 1
            j = i + 1
            while j < len(body) and depth > 0:
                if body[j] == '{': depth += 1
                elif body[j] == '}': depth -= 1
                j += 1

            # body[i+1:j-1] is the expression
            expr = body[i+1:j-1].strip()

            # Check for !r or !s conversion
            conv = ''
            if '!' in expr:
                idx = expr.index('!')
                conv = expr[idx+1]
                expr = expr[:idx].strip()

            # Check for : format spec - but NOT slice expressions!
            # A colon is a format spec only if it's NOT part of a slice [start:end]
            # Format specs: .2f, .1%, d, s, etc.
            # Slices: :50, 1:3, :
            fmt_spec = ''
            for ci, ch in enumerate(expr):
                if ch == ':':
                    rest = expr[ci+1:]
                    # If rest is empty or starts with . or is a letter/number, it's a format spec
                    # If rest starts with a digit and contains ] or nothing, check if expr has [ before the :
                    has_bracket = '[' in expr[:ci]
                    if has_bracket:
                        # It's a slice - don't treat : as format spec
                        fmt_spec = ''
                    elif rest and (rest[0] in '.srdxfncobneEgG%' or rest[0].isdigit()):
                        fmt_spec = rest
                    elif not rest:
                        # Bare :  -> probably format spec, but could be slice. Treat as %s
                        fmt_spec = ''
                    break

            if conv == 'r':
                args.append(expr)
                result.append('%r')
            elif fmt_spec:
                args.append(expr)
                result.append(f'%{fmt_spec}')
            elif expr:
                # Replace any remaining { } inside (shouldn't happen with simple expr)
                args.append(expr)
                result.append('%s')
            else:
                result.append('{')

            i = j
        else:
            result.append(body[i])
            i += 1

    return ''.join(result), args


def _try_convert(call_text: str) -> str | None:
    im = re.match(r'^( *)', call_text)
    indent = im.group(1) if im else ''
    lm = re.search(r'logger\.(\w+)\(', call_text)
    if not lm: return None
    level = lm.group(1)

    parts = re.findall(r'f(["\\\'])(.*?)\1', call_text, re.DOTALL)
    if not parts: return None

    extra_args = re.findall(r',\s*(exc_info|extra|stack_info|stacklevel)\s*=\s*(True|False|\{[^}]*\})', call_text)
    extra_str = ', '.join(f'{k}={v}' for k, v in extra_args)

    body = ''.join(p[1] for p in parts)
    body = re.sub(r'\s+', ' ', body).strip()

    fmt, args = convert_body(body)
    if not fmt: return None

    args_str = ', '.join(a for a in args if a)
    all_args = ', '.join(filter(None, [f'"{fmt}"', args_str, extra_str]))
    return f'{indent}logger.{level}({all_args})'


def process_file(fp: pathlib.Path) -> int:
    try:
        txt = fp.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return 0

    lines = txt.split('\n')
    out = []
    changes = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        m = re.search(r'(logger\.\w+)\(\s*f(["\\\'])', line)
        if not m:
            out.append(line)
            i += 1
            continue

        # Collect lines until balanced
        call_text = line
        while True:
            depth = 0
            in_s = False
            esc = False
            qc = None
            done = False
            for ch in call_text:
                if esc: esc = False; continue
                if ch == '\\': esc = True; continue
                if in_s:
                    if ch == qc: in_s = False
                    continue
                if ch in ('"', "'"): in_s = True; qc = ch; continue
                if ch == '(': depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0: done = True; break
            if done or depth <= 0:
                break
            i += 1
            if i >= len(lines): break
            call_text += '\n' + lines[i]

        converted = _try_convert(call_text)
        if converted:
            out.append(converted)
            changes += 1
        else:
            out.append(call_text)
        i += 1

    if changes > 0:
        candidate = '\n'.join(out)
        tmp = fp.with_suffix('.py.tmpcheck')
        try:
            tmp.write_text(candidate, encoding='utf-8')
            py_compile.compile(str(tmp), doraise=True)
            fp.write_text(candidate, encoding='utf-8')
        except py_compile.PyCompileError as e:
            print(f"  SKIP {fp}: {e}", file=sys.stderr)
            return 0
        finally:
            if tmp.exists(): tmp.unlink()

    return changes


def main():
    exclude = {'.git', '.worktrees', 'tests', '__pycache__', 'docs',
               '.claude', 'node_modules', 'scripts'}
    files = [f for f in pathlib.Path('.').rglob('*.py') if not any(d in f.parts for d in exclude)]

    total = 0; changed = 0
    print(f"Processing {len(files)} files...")
    for fp in sorted(files):
        try:
            c = process_file(fp)
            if c > 0:
                total += c; changed += 1
                print(f"  {fp}: {c}")
        except Exception as e:
            print(f"  EXCEPTION {fp}: {e}")

    print(f"\nTotal: {total} changes across {changed} files")


if __name__ == '__main__':
    main()
