"""Token-scoped loopback server. Only session review files are writable."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

from .report import initial_review, markdown_report, validate_review

WEB = Path(__file__).resolve().parents[2] / 'web'


def atomic_json(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def create_server(directory, port=0):
    directory=Path(directory).resolve()
    session=json.loads((directory/'session.json').read_text(encoding='utf-8'))
    snapshot=json.loads((directory/'snapshot.json').read_text(encoding='utf-8'))
    prefix='/s/'+session['token']+'/'
    lock=threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass

        def send(self,status,body,kind='application/json; charset=utf-8'):
            if not isinstance(body,bytes):
                body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def route(self):
            expected=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=expected:
                self.send(403,{'error':'Invalid host'})
                return None
            path=urlsplit(self.path).path
            if not path.startswith(prefix):
                self.send(404,{'error':'Not found'})
                return None
            return path[len(prefix):]

        def current(self):
            path=directory/'review.json'
            if path.exists():
                saved=json.loads(path.read_text(encoding='utf-8'))
                review=validate_review(snapshot,saved)
                review['revision']=saved.get('revision',0)
                return review
            return {**initial_review(snapshot),'revision':0}

        def do_GET(self):
            route=self.route()
            if route is None:
                return
            if route=='api/snapshot':
                self.send(200,snapshot)
            elif route=='api/review':
                with lock:
                    self.send(200,self.current())
            elif route=='api/report':
                with lock:
                    self.send(200,{'markdown':markdown_report(snapshot,self.current())})
            elif route=='api/health':
                self.send(200,{'ok':True,'snapshotId':snapshot['id']})
            elif route in ('','index.html','app.js','style.css'):
                filename=route or 'index.html'
                kind={'index.html':'text/html','app.js':'text/javascript','style.css':'text/css'}[filename]
                self.send(200,(WEB/filename).read_bytes(),kind+'; charset=utf-8')
            else:
                self.send(404,{'error':'Not found'})

        def do_POST(self):
            route=self.route()
            if route is None:
                return
            origin=self.headers.get('Origin')
            expected=f'http://127.0.0.1:{self.server.server_port}'
            if origin!=expected:
                self.send(403,{'error':'Same-origin requests only'})
                return
            if route=='api/shutdown':
                self.send(200,{'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if route!='api/review':
                self.send(404,{'error':'Not found'})
                return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2*1024*1024:
                    raise ValueError('Invalid request size')
                payload=json.loads(self.rfile.read(length))
                version=payload.get('revision')
                review=validate_review(snapshot,payload)
                with lock:
                    current_version=self.current()['revision']
                    if type(version) is not int or version!=current_version:
                        self.send(409,{'error':'审阅已在其他页面更新，请重新加载后继续。'})
                        return
                    # Store the revision in the same document as the review so writes are atomic.
                    review['revision']=version+1
                    atomic_json(directory/'review.json',review)
                    self.send(200,review)
            except (ValueError,TypeError,KeyError,UnicodeError) as exc:
                self.send(400,{'error':str(exc)})

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def serve(directory):
    directory=Path(directory)
    server=create_server(directory)
    session=json.loads((directory/'session.json').read_text(encoding='utf-8'))
    import os
    atomic_json(directory/'server.json',{'url':f'http://127.0.0.1:{server.server_port}/s/{session["token"]}/','pid':os.getpid()})
    try:
        server.serve_forever()
    finally:
        server.server_close()
