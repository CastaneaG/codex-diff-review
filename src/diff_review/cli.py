"""CLI entry point used by the Codex skill and by humans."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from .server import atomic_json, serve
from .snapshot import collect_snapshot


def start_server(directory):
    metadata=directory/'server.json'
    if metadata.exists():
        previous=json.loads(metadata.read_text())
        try:
            with urlopen(previous['url']+'api/health',timeout=1) as response:
                if json.load(response).get('ok'):
                    return previous
        except (URLError,OSError,ValueError):
            pass
        metadata.unlink()
    launcher=Path(__file__).resolve().parents[2]/'skills/diff-review/scripts/review.py'
    options={'stdin':subprocess.DEVNULL,'close_fds':True}
    if os.name=='nt':
        options['creationflags']=subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session']=True
    with (directory/'server.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,str(launcher),'serve','--session',str(directory)],
                                 stdout=log,stderr=log,**options)
    for _ in range(100):
        if metadata.exists():
            return json.loads(metadata.read_text())
        if process.poll() is not None:
            raise ValueError('Review server stopped. See '+str(directory/'server.log'))
        time.sleep(.1)
    raise ValueError('Server startup timed out. See '+str(directory/'server.log'))


def main(argv=None):
    parser=argparse.ArgumentParser(description='Create a local human diff-review link; no source files are written.')
    commands=parser.add_subparsers(dest='command',required=True)
    start=commands.add_parser('start',help='Freeze a Git diff and launch a local reviewer')
    start.add_argument('--repo',action='append',help='Repository path; repeat for multiple repositories')
    start.add_argument('--base',default='HEAD',help='Base commit/ref, default HEAD')
    target=start.add_mutually_exclusive_group()
    target.add_argument('--head',help='Target commit/ref; omitted means current worktree')
    target.add_argument('--staged',action='store_true',help='Review the index instead of the worktree')
    start.add_argument('--path',action='append',default=[],help='Repository-relative path to include (repeatable)')
    start.add_argument('--manifest',type=Path,help='JSON list of {path, base?, head?, staged?, name?, paths?}')
    start.add_argument('--title',default='本次修改')
    start.add_argument('--session-root',type=Path,default=Path.home()/'.codex-diff-review/sessions')
    for name in ('serve','resume','stop'):
        command=commands.add_parser(name)
        command.add_argument('--session',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='serve':
            serve(args.session.resolve())
            return 0
        if args.command=='stop':
            metadata=json.loads((args.session/'server.json').read_text())
            origin=metadata['url'].split('/s/')[0]
            request=Request(metadata['url']+'api/shutdown',data=b'{}',headers={'Origin':origin,'Content-Type':'application/json'},method='POST')
            with urlopen(request,timeout=5) as response:
                print(response.read().decode())
            return 0
        if args.command=='resume':
            directory=args.session.resolve()
        else:
            if args.manifest:
                if args.repo or args.path or args.head or args.staged or args.base!='HEAD':
                    raise ValueError('--manifest cannot be combined with repository selection flags')
                specs=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
                if not isinstance(specs,list) or not specs:
                    raise ValueError('Manifest must contain at least one repository')
            else:
                specs=[{'path':p,'base':args.base,'head':args.head,'staged':args.staged,'paths':args.path}
                       for p in (args.repo or [str(Path.cwd())])]
            snapshot=collect_snapshot(specs,args.title)
            if not snapshot['repositories']:
                print(json.dumps({'status':'empty','message':'所选范围没有改动'},ensure_ascii=False))
                return 0
            directory=args.session_root.expanduser().resolve()/(snapshot['id'][:12]+'-'+secrets.token_hex(4))
            directory.mkdir(parents=True,mode=0o700)
            atomic_json(directory/'snapshot.json',snapshot)
            atomic_json(directory/'session.json',{'token':secrets.token_urlsafe(32)})
        metadata=start_server(directory)
        snapshot=json.loads((directory/'snapshot.json').read_text(encoding='utf-8'))
        print(json.dumps({'url':metadata['url'],'session':str(directory),'snapshotId':snapshot['id'],
                          'repositories':len(snapshot['repositories']),
                          'files':sum(len(r['files']) for r in snapshot['repositories'])},ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
