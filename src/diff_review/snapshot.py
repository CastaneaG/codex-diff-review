"""Freeze Git content before presenting it to a reviewer; never change Git state."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from datetime import datetime, timezone

MAX_BYTES = 1024 * 1024
MAX_LINES = 12000


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), '-c', 'core.quotePath=false',
                             *args], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', 'replace').strip())
    return result.stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def revision(root, ref):
    # --end-of-options prevents a user-supplied ref from becoming a Git option.
    return git(root, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


def tree(root, ref):
    records = {}
    for record in git(root, 'ls-tree', '-r', '-z', ref).split(b'\0'):
        if record:
            meta, name = record.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            records[os.fsdecode(name)] = (mode, kind, oid)
    return records


def blob(root, entry):
    if not entry:
        return b''
    mode, kind, oid = entry
    if kind == 'commit':
        return ('Subproject commit ' + oid + '\n').encode()
    size = int(git(root, 'cat-file', '-s', oid))
    if size > MAX_BYTES:
        return None
    return git(root, 'cat-file', 'blob', oid)


def working_blob(root, filename):
    path = root / filename
    # A tracked file can have a directory ancestor replaced by a symlink.
    # Do not follow that ancestor outside the repository while collecting it.
    parent = path.parent
    while parent != root:
        if parent.is_symlink():
            return None, 'ancestor-symlink'
        parent = parent.parent
    if path.is_symlink():
        return os.fsencode(os.readlink(path)), '120000'
    try:
        info = path.stat()
    except FileNotFoundError:
        return b'', '000000'
    if not stat.S_ISREG(info.st_mode):
        return None, 'special-file'
    if info.st_size > MAX_BYTES:
        return None, 'large-file'
    return path.read_bytes(), '100755' if info.st_mode & stat.S_IXUSR else '100644'


def text_content(data):
    if data is None:
        return None, '文件超过大小限制或不是普通文件，仅展示元信息。'
    if b'\0' in data:
        return None, '二进制文件，仅展示元信息。'
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None, '非 UTF-8 文本，仅展示元信息。'
    if len(text.splitlines()) > MAX_LINES:
        return None, '文件超过 12000 行，仅展示元信息。'
    return text, ''


def make_hunks(old, new):
    left = old.replace('\r\n', '\n').splitlines()
    right = new.replace('\r\n', '\n').splitlines()
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=True)
    operations = list(matcher.get_opcodes())
    hunks = []
    for group in matcher.get_grouped_opcodes(3):
        start, end = group[0], group[-1]
        rows = []
        for kind, a, b, c, d in group:
            if kind == 'equal':
                rows.extend({'kind':'context', 'old':i+1, 'new':c+i-a+1,
                             'text':left[i]} for i in range(a,b))
            else:
                rows.extend({'kind':'deletion','old':i+1,'new':None,'text':left[i]}
                            for i in range(a,b))
                rows.extend({'kind':'addition','old':None,'new':i+1,'text':right[i]}
                            for i in range(c,d))
        old_count, new_count = end[2]-start[1], end[4]-start[3]
        hunks.append({'oldStart':start[1]+bool(old_count),'oldCount':old_count,
                      'newStart':start[3]+bool(new_count),'newCount':new_count, 'rows':rows})
    # Complete file rows use exactly the same opcodes as compact hunks.
    complete=[]
    for kind,a,b,c,d in operations:
        if kind=='equal':
            complete.extend({'kind':'context','old':i+1,'new':c+i-a+1,'text':left[i]} for i in range(a,b))
        else:
            complete.extend({'kind':'deletion','old':i+1,'new':None,'text':left[i]} for i in range(a,b))
            complete.extend({'kind':'addition','old':None,'new':i+1,'text':right[i]} for i in range(c,d))
    return hunks, complete


def collect_repository(spec):
    root = Path(git(spec['path'], 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    base = revision(root, spec.get('base','HEAD'))
    head_arg = spec.get('head')
    staged = bool(spec.get('staged'))
    if staged and head_arg:
        raise ValueError('head and staged cannot be combined')
    head = revision(root, head_arg) if head_arg else None
    paths = spec.get('paths', [])
    if not isinstance(paths, list) or any(not isinstance(p,str) or p.startswith(':') or Path(p).is_absolute() or '..' in Path(p).parts for p in paths):
        raise ValueError('paths must be repository-relative paths without .. or Git magic')
    args=['diff','--name-only','-z','--no-renames', '--ignore-submodules=dirty']
    if staged:
        args += ['--cached']
    args += [base]
    if head:
        args += [head]
    args += ['--', *paths]
    changed = {os.fsdecode(x) for x in git(root,*args).split(b'\0') if x}
    untracked=set()
    if not head and not staged:
        untracked={os.fsdecode(x) for x in git(root,'ls-files','--others','--exclude-standard','-z','--',*paths).split(b'\0') if x}
        changed.update(untracked)
    old_tree=tree(root,base)
    new_tree=tree(root,head) if head else {}
    index={}
    if staged:
        for record in git(root,'ls-files','--stage','-z').split(b'\0'):
            if record:
                meta,name=record.split(b'\t',1)
                mode,oid,stage=meta.decode().split()
                if stage!='0':
                    raise ValueError('Resolve merge conflicts before creating a review snapshot')
                index[os.fsdecode(name)]=(mode,'commit' if mode=='160000' else 'blob',oid)
    repo_id=sha(os.fsencode(str(root)))[:16]
    result={'id':repo_id,'name':spec.get('name') or root.name,'path':str(root),'base':base,
            'target':head or ('index' if staged else 'worktree'),'files':[]}
    for filename in sorted(changed):
        entry = None
        previous=old_tree.get(filename)
        before=blob(root,previous)
        old_mode=previous[0] if previous else '000000'
        if head or staged:
            entry=(new_tree if head else index).get(filename)
            after=blob(root,entry)
            new_mode=entry[0] if entry else '000000'
        elif old_mode=='160000' and (root/filename).is_dir():
            after=('Subproject commit '+revision(root/filename,'HEAD')+'\n').encode()
            new_mode='160000'
        else:
            after,new_mode=working_blob(root,filename)
        # On Windows and core.filemode=false filesystems, executable bits on
        # working files are not meaningful Git changes.
        if not head and not staged and previous and old_mode in ('100644','100755') and new_mode in ('100644','100755'):
            check=subprocess.run(['git','-C',str(root),'config','--bool','core.filemode'],capture_output=True)
            if check.stdout.strip()==b'false':
                new_mode=old_mode
        old,old_warning=text_content(before)
        new,new_warning=text_content(after)
        binary=old is None or new is None
        hunks,rows=([],[]) if binary else make_hunks(old,new)
        added=sum(row['kind']=='addition' for row in rows)
        deleted=sum(row['kind']=='deletion' for row in rows)
        mode_change=old_mode!=new_mode
        status='A' if not previous else 'D' if new_mode=='000000' else 'M'
        result['files'].append({'id':sha((repo_id+'\0'+filename).encode())[:24],
            'path':filename,'status':status,'additions':added,'deletions':deleted,
            'oldMode':old_mode,'newMode':new_mode,'modeChanged':mode_change,
            'oldHash':sha(before) if before is not None else None,
            'newHash':sha(after) if after is not None else None,
            'oldObject':previous[2] if previous else None,
            'newObject':entry[2] if entry else None,
            'unreadStat':({'size':(root/filename).lstat().st_size,'mtimeNs':(root/filename).lstat().st_mtime_ns} if after is None and not head and not staged and (root/filename).exists() else None),
            'oldText':old,'newText':new,'hunks':hunks,'rows':rows,
            'warning':old_warning or new_warning,
            'note':'仅编码、换行、文件模式或文件元信息变化' if not binary and not hunks else ''})
    return result


def collect_snapshot(specs, title='本次修改'):
    repositories=[collect_repository(spec) for spec in specs]
    # Repo IDs are path based. Reject duplicates instead of mixing review state.
    ids=[r['id'] for r in repositories]
    if len(ids)!=len(set(ids)):
        raise ValueError('Each repository may appear only once per review session')
    repositories=[r for r in repositories if r['files']]
    payload={'version':1,'title':title,'repositories':repositories}
    payload['id']=sha(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode())
    payload['createdAt']=datetime.now(timezone.utc).isoformat()
    return payload
