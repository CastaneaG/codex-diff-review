import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from diff_review.snapshot import collect_snapshot, make_hunks
from diff_review.report import initial_review, validate_review, markdown_report
from diff_review.server import atomic_json, create_server


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'repo with spaces'
        self.root.mkdir()
        self.git('init','-q')
        self.git('config','user.name','Test')
        self.git('config','user.email','test@example.invalid')
        self.git('config','core.autocrlf','false')
        self.git('config','core.filemode','false')
        self.write('main.py','\n'.join(f'value_{i} = {i}' for i in range(100))+'\n')
        self.write('gone.txt','remove me\n')
        self.write('.gitignore','ignored.txt\n')
        self.git('add','.')
        self.git('commit','-qm','initial')
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.root),*args],stderr=subprocess.STDOUT)
    def write(self,path,content):
        (self.root/path).write_bytes(content.encode('utf-8'))
    def change(self):
        self.write('main.py',(self.root/'main.py').read_text().replace('value_50 = 50','value_50 = 500'))
    def snapshot(self,**kwargs):
        return collect_snapshot([{'path':str(self.root),**kwargs}],'Demo review')
    def test_worktree_includes_staged_unstaged_untracked_but_not_ignored(self):
        self.change();self.git('add','main.py')
        self.write('new 文件.txt','hello\n')
        self.write('ignored.txt','private\n')
        (self.root/'gone.txt').unlink()
        before=self.git('status','--porcelain=v1','-z')
        snapshot=self.snapshot()
        self.assertEqual(before,self.git('status','--porcelain=v1','-z'))
        files={f['path']:f for f in snapshot['repositories'][0]['files']}
        self.assertEqual(set(files),{'main.py','new 文件.txt','gone.txt'})
        self.assertEqual(files['new 文件.txt']['status'],'A')
        self.assertEqual(files['gone.txt']['status'],'D')
    def test_complete_rows_preserve_both_full_versions(self):
        self.change()
        f=self.snapshot()['repositories'][0]['files'][0]
        self.assertEqual(len([r for r in f['rows'] if r['old']]),100)
        self.assertEqual(len([r for r in f['rows'] if r['new']]),100)
        old='\n'.join(r['text'] for r in f['rows'] if r['old'])+'\n'
        new='\n'.join(r['text'] for r in f['rows'] if r['new'])+'\n'
        self.assertEqual(old,self.git('show','HEAD:main.py').decode())
        self.assertEqual(new,(self.root/'main.py').read_text())
        self.assertLess(len(f['hunks'][0]['rows']),len(f['rows']))
    def test_staged_snapshot_excludes_later_worktree_edit(self):
        self.change();self.git('add','main.py');self.write('main.py','unstaged\n')
        f=self.snapshot(staged=True)['repositories'][0]['files'][0]
        self.assertIn('value_50 = 500',f['newText'])
        self.assertNotIn('unstaged',f['newText'])
    def test_committed_range_and_path_filter(self):
        base=self.git('rev-parse','HEAD').decode().strip()
        self.change();self.git('add','.');self.git('commit','-qm','change')
        self.write('other.txt','untracked')
        f=self.snapshot(base=base,head='HEAD',paths=['main.py'])['repositories'][0]['files']
        self.assertEqual(len(f),1)
        self.assertEqual(f[0]['path'],'main.py')
    def test_binary_and_large_metadata(self):
        (self.root/'binary.dat').write_bytes(b'\0\1binary')
        (self.root/'large.txt').write_bytes(b'x'*(1024*1024+1))
        files=self.snapshot()['repositories'][0]['files']
        self.assertEqual(len(files),2)
        self.assertTrue(all(f['warning'] for f in files))
        self.assertTrue(all(not f['rows'] for f in files))
    def test_snapshot_is_stable_until_content_changes(self):
        self.change()
        first=self.snapshot()['id']
        self.assertEqual(first,self.snapshot()['id'])
        self.write('main.py','different\n')
        self.assertNotEqual(first,self.snapshot()['id'])
    def test_large_staged_file_changes_snapshot_identity(self):
        (self.root/'large.txt').write_bytes(b'x'*(1024*1024+1))
        self.git('add','large.txt')
        first=self.snapshot(staged=True)
        (self.root/'large.txt').write_bytes(b'y'*(1024*1024+1))
        self.git('add','large.txt')
        second=self.snapshot(staged=True)
        self.assertNotEqual(first['id'],second['id'])
        self.assertNotEqual(first['repositories'][0]['files'][0]['newObject'],
                            second['repositories'][0]['files'][0]['newObject'])
    def test_new_and_deleted_file_hunk_ranges(self):
        added,_=make_hunks('','one\ntwo\n')
        deleted,_=make_hunks('one\ntwo\n','')
        self.assertEqual((added[0]['oldStart'],added[0]['oldCount']),(0,0))
        self.assertEqual((deleted[0]['newStart'],deleted[0]['newCount']),(0,0))
    def test_untracked_symlink_does_not_read_its_target(self):
        target=Path(self.temp.name)/'outside.txt';target.write_text('DO NOT COPY CONTENT')
        try:
            (self.root/'link.txt').symlink_to(target)
        except OSError:
            self.skipTest('OS does not permit symlink creation')
        f=self.snapshot()['repositories'][0]['files'][0]
        self.assertNotIn('DO NOT COPY CONTENT',f['newText'])
        self.assertEqual(f['newMode'],'120000')
    def test_scope_and_ref_validation(self):
        with self.assertRaises(ValueError):self.snapshot(paths=['../outside'])
        with self.assertRaises(ValueError):self.snapshot(base='--help')
        with self.assertRaises(ValueError):collect_snapshot([{'path':str(self.root)},{'path':str(self.root)}])
    def test_empty_and_newline_only(self):
        self.assertEqual(self.snapshot()['repositories'],[])
        original=(self.root/'gone.txt').read_bytes()
        (self.root/'gone.txt').write_bytes(original.replace(b'\n',b'\r\n'))
        f=self.snapshot()['repositories'][0]['files'][0]
        self.assertNotEqual(f['oldHash'],f['newHash'])
        self.assertTrue(f['note'])
    def test_comments_and_final_report_require_valid_anchors_and_all_reviewed(self):
        self.change();snapshot=self.snapshot();f=snapshot['repositories'][0]['files'][0]
        review=initial_review(snapshot)
        review['completed']=True
        with self.assertRaises(ValueError):validate_review(snapshot,review)
        review['reviewed']=[f['id']]
        review['comments']=[{'id':'one','fileId':f['id'],'side':'new','start':51,'end':52,'body':'请使用明确的配置值。'}]
        result=markdown_report(snapshot,review)
        self.assertIn('修改后 L51–L52',result)
        self.assertIn('value_50 = 500',result)
        self.assertIn(snapshot['id'],result)
        self.assertIn('完整审阅',result)
        review['comments'][0]['end']=1000
        with self.assertRaises(ValueError):validate_review(snapshot,review)
    def test_partial_report_does_not_claim_approval(self):
        self.change();snapshot=self.snapshot();review=initial_review(snapshot)
        review['decision']='approve'
        text=markdown_report(snapshot,review)
        self.assertIn('部分审阅',text)
        self.assertNotIn('结论：本快照审阅通过',text)
    def test_server_persists_review_and_rejects_stale_or_cross_origin_writes(self):
        self.change();snapshot=self.snapshot()
        directory=Path(self.temp.name)/'session';directory.mkdir()
        atomic_json(directory/'snapshot.json',snapshot)
        atomic_json(directory/'session.json',{'token':'test-token'})
        server=create_server(directory)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        origin=f'http://127.0.0.1:{server.server_port}'
        base=origin+'/s/test-token/'
        def call(route,data=None,custom_origin=None):
            req=Request(base+route,data=json.dumps(data).encode() if data is not None else None,
                        headers={'Origin':custom_origin or origin,'Content-Type':'application/json'})
            with urlopen(req,timeout=3) as response:return json.load(response)
        state=call('api/review');self.assertEqual(state['revision'],0)
        state['summary']='本地保存';state=call('api/review',state)
        self.assertEqual(state['revision'],1)
        self.assertEqual(call('api/review')['summary'],'本地保存')
        with self.assertRaises(HTTPError) as error:call('api/review',{**state,'revision':0})
        self.assertEqual(error.exception.code,409)
        with self.assertRaises(HTTPError) as error:call('api/review',state,'https://evil.invalid')
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError):
            urlopen(origin+'/api/snapshot',timeout=3)
        with self.assertRaises(HTTPError):
            urlopen(base+'../../snapshot.json',timeout=3)
        self.assertIn('本地保存',(directory/'review.json').read_text(encoding='utf-8'))


if __name__=='__main__':
    unittest.main()
