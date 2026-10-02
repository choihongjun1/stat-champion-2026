"""Scan explicit public targets; recursive walks prune excluded/private directories."""
from __future__ import annotations

import argparse
from collections import Counter
from fnmatch import fnmatchcase
import json
import html
import os
from pathlib import Path
import re
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.md', '.txt', '.json', '.jsonl', '.csv', '.ts', '.tsx', '.js',
              '.jsx', '.html', '.htm', '.svg', '.py', '.yaml', '.yml'}
EXCLUDED = {'.git', 'node_modules', '.next', '.venv', '__pycache__', 'outputs'}


class UsageError(ValueError):
    pass


def load_config(rules_path, allowlist_path):
    """Validate all configuration before opening any scan target."""
    repo_root = repository_root()
    def read(path):
        if forbidden_data(Path(path), repo_root):
            raise UsageError('configuration path under data is forbidden')
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, ValueError) as exc:
            raise UsageError('cannot read JSON configuration') from exc
    rules = read(rules_path)
    allowlist = read(allowlist_path)
    if not isinstance(rules, list) or not rules:
        raise UsageError('rules must be a nonempty array')
    ids = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise UsageError('each rule must be an object')
        rid = rule.get('id')
        if not isinstance(rid, str) or not re.fullmatch(r'(CL|ID)-\d{2}', rid) or rid in ids:
            raise UsageError('invalid or duplicate rule id')
        ids.add(rid)
        if rule.get('severity') not in ('error', 'warn'):
            raise UsageError('invalid severity')
        if not isinstance(rule.get('reason'), str) or not rule['reason'].strip():
            raise UsageError('rule reason is required')
        if type(rule.get('mask')) is not bool or rule['mask'] != rid.startswith('ID-'):
            raise UsageError('only ID rules must have mask=true')
        patterns = rule.get('patterns')
        if not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p for p in patterns):
            raise UsageError('patterns must be a nonempty string array')
        try:
            rule['compiled'] = [re.compile(p) for p in patterns]
        except re.error as exc:
            raise UsageError('invalid regular expression') from exc
        if any(p.match('') for p in rule['compiled']):
            raise UsageError('empty matches are not allowed')
    if not isinstance(allowlist, list):
        raise UsageError('allowlist must be an array')
    for entry in allowlist:
        if not isinstance(entry, dict) or not isinstance(entry.get('path_glob'), str) or not entry['path_glob']:
            raise UsageError('allowlist path_glob is required')
        if '\\' in entry['path_glob'] or entry['path_glob'].startswith('/') or '..' in entry['path_glob'].split('/'):
            raise UsageError('allowlist globs must be relative POSIX paths')
        rids = entry.get('rule_ids')
        if not isinstance(rids, list) or not rids or any(not isinstance(r, str) or (r not in ids and r != '*') for r in rids):
            raise UsageError('unknown allowlist rule id')
        if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
            raise UsageError('allowlist reason is required')
    return rules, allowlist


def repository_root():
    """Resolve the current checkout root; outside Git use the current directory."""
    try:
        result = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                                capture_output=True, encoding='utf-8', check=True)
        return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return Path.cwd().resolve()


def forbidden_data(path, repo_root):
    # Only the checkout's top-level data tree is private, including resolved aliases.
    data_dir = repo_root.resolve() / 'data'
    return path.absolute().is_relative_to(data_dir) or path.resolve().is_relative_to(data_dir)


def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def excluded(path, repo_root):
    # Used only for child directories encountered during recursive traversal.
    return forbidden_data(path, repo_root) or path.name.casefold() in EXCLUDED


def candidates(paths, skipped, repo_root):
    roots = [Path(p).absolute() for p in paths]
    # Reject the entire request before any target is opened.
    for root in roots:
        if forbidden_data(root, repo_root):
            raise UsageError('explicit data path is forbidden')
        if not root.exists():
            raise UsageError('scan target does not exist')
    seen = set()
    for root in roots:
        if any(linked(p) for p in [root, *root.parents]):
            skipped['excluded_or_linked'] += 1
            continue
        if root.is_file():
            entries = [root]
        else:
            entries = []
            def walk_error(_):
                raise UsageError('cannot traverse scan directory')
            for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
                kept = []
                for name in sorted(dirs):
                    child = Path(current) / name
                    if excluded(child, repo_root) or linked(child):
                        skipped['excluded_or_linked'] += 1
                    else:
                        kept.append(name)
                dirs[:] = kept
                entries.extend(Path(current) / name for name in sorted(files))
        for path in entries:
            if linked(path) or forbidden_data(path, repo_root):
                skipped['excluded_or_linked'] += 1
                continue
            key = str(path.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            if path.suffix.lower() not in EXTENSIONS:
                skipped['extension'] += 1
                continue
            yield path


def display_path(path, base):
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def redact(value, rules):
    spans = []
    for rule in rules:
        if not rule['mask']:
            continue
        for pattern in rule['compiled']:
            for match in pattern.finditer(value):
                end = match.end()
                # ID-01 is a detection prefix, but the entire numeric identifier is private.
                if rule['id'] == 'ID-01':
                    while end < len(value) and value[end].isdigit():
                        end += 1
                spans.append((match.start(), end))
    chars = list(value)
    for start, end in spans:
        for pos in range(start, end):
            if chars[pos].isalnum() and not (value[start:start + 3] == 'SR_' and pos < start + 3):
                chars[pos] = '*'
    return ''.join(chars)


def allowed(path, rid, line, allowlist):
    # Decision/claims records may never exempt identifier rules, even with inline comments.
    record = fnmatchcase(path, 'docs/DECISIONS*.md') or path == 'docs/CLAIMS.md'
    if record and rid.startswith('ID-'):
        return False
    for entry in allowlist:
        if fnmatchcase(path, entry['path_glob']) and ('*' in entry['rule_ids'] or rid in entry['rule_ids']):
            return True
    for comment in re.finditer(r'claims-allow:\s*((?:CL|ID)-\d{2}(?:\s*,\s*(?:CL|ID)-\d{2})*)(?![\w-])', line):
        if rid in re.split(r'\s*,\s*', comment.group(1)):
            return True
    return False


def scan(paths, rules, allowlist, base=None):
    repo_root = repository_root()
    if base is None:
        base = repo_root
    findings = []
    skipped = Counter()
    scanned = 0
    def finding(path, line, col, rule, match, context):
        findings.append(dict(file=redact(path, rules), line=line, column=col,
                             severity=rule['severity'], rule_id=rule['id'],
                             match=redact(match, rules), context=redact(context, rules),
                             reason=redact(rule['reason'], rules)))
    for path in candidates(paths, skipped, repo_root):
        label = display_path(path, base)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise UsageError('cannot read scan target') from exc
        if b'\0' in raw:
            skipped['binary'] += 1
            continue
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            skipped['decode'] += 1
            finding(label, 0, 0, dict(id='IO-DECODE', severity='warn', reason='UTF-8 decoding failed'), '', '')
            continue
        scanned += 1
        source_line_count = content.count('\n') + 1
        # Scan rendered text too: escaped JSON and HTML markup must not hide claims.
        # Preserve newlines; columns refer to this normalized text, not source bytes.
        if path.suffix.lower() in {'.json', '.jsonl', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.svg'}:
            content = re.sub(r'\\u([0-9a-fA-F]{4})',
                             lambda m: chr(int(m.group(1), 16)).replace('\n', ' ').replace('\r', ' '), content)
        if path.suffix.lower() in {'.html', '.htm', '.svg'}:
            content = re.sub(r'&(?:#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]+);',
                             lambda m: html.unescape(m.group()).replace('\n', ' ').replace('\r', ' '), content)
            # Retain raw markup (including attribute values) as well as visible text.
            content += '\n' + re.sub(r'<[^>]*>', lambda m: '\n' * m.group().count('\n'), content)
        safe_content = redact(content, rules)
        for rule in rules:
            seen = set()
            for pattern in rule['compiled']:
                for match in pattern.finditer(content):
                    if match.span() in seen:
                        continue
                    seen.add(match.span())
                    start, end = match.span()
                    line_start = content.rfind('\n', 0, start) + 1
                    line_end = content.find('\n', end)
                    if line_end < 0:
                        line_end = len(content)
                    # Multiline matches are exempt only when every covered line is exempt.
                    covered = content[line_start:line_end].splitlines() or ['']
                    if all(allowed(label, rule['id'], ln, allowlist) for ln in covered):
                        continue
                    if rule['id'] == 'ID-01':
                        while end < len(content) and content[end].isdigit():
                            end += 1
                    # Redact before slicing so a boundary never leaks part of an identifier.
                    context = safe_content[max(line_start, start - 20):min(line_end, end + 20)]
                    finding(label, content.count('\n', 0, start) % source_line_count + 1, start - line_start + 1,
                            rule, content[start:end], context)
    findings.sort(key=lambda f: (f['file'], f['line'], f['column'], f['rule_id']))
    return dict(findings=findings, summary=dict(scanned_files=scanned, skipped=dict(sorted(skipped.items())),
                by_rule=dict(sorted(Counter(f['rule_id'] for f in findings).items())),
                by_severity=dict(sorted(Counter(f['severity'] for f in findings).items()))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--rules', default=str(ROOT / 'configs/claims_rules.json'))
    parser.add_argument('--allowlist', default=str(ROOT / 'configs/claims_allowlist.json'))
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    parser.add_argument('--fail-on', choices=('error', 'warn'), default='error')
    args = parser.parse_args(argv)
    try:
        rules, allowlist = load_config(args.rules, args.allowlist)
        result = scan(args.paths, rules, allowlist)
    except (UsageError, OSError, RuntimeError) as exc:
        # Avoid echoing invalid user values, paths, or raw decode buffers.
        print('usage error: ' + str(exc), file=sys.stderr)
        return 2
    no_files = result['summary']['scanned_files'] == 0
    if no_files:
        result['message'] = '검사한 파일이 없습니다'
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result['findings']:
            context = item['context'].replace('\r', '\\r').replace('\n', '\\n')
            match = item['match'].replace('\r', '\\r').replace('\n', '\\n')
            print(f"{item['file']}:{item['line']}:{item['column']}  [{item['severity'].upper()}] "
                  f"{item['rule_id']}  {match} ({context})")
        print('summary: ' + json.dumps(result['summary'], ensure_ascii=False, sort_keys=True))
        if no_files:
            print(result['message'])
    if no_files:
        return 3
    return int(any(f['severity'] == 'error' or args.fail_on == 'warn' for f in result['findings']))


if __name__ == '__main__':
    sys.exit(main())
