"""Real local Git integration: durable reservation survives a fresh checkout."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import parser as p

class GitPersistence(unittest.TestCase):
    def test_checkpoint_before_send_survives_fresh_checkout(self):
        script = str(Path('scripts/state.sh').resolve())
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            remote, repo = base/'remote.git', base/'repo'
            def git(*args, cwd=None):
                return subprocess.run(['git', *map(str,args)], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git('init', '--bare', remote)
            git('init', repo)
            git('config','user.name','Test',cwd=repo)
            git('config','user.email','test@example.com',cwd=repo)
            (repo/'README').write_text('test')
            git('add','README',cwd=repo)
            git('commit','-m','Initial',cwd=repo)
            git('remote','add','origin',remote,cwd=repo)
            git('push','origin','HEAD',cwd=repo)
            env = {**os.environ, 'GITHUB_ENV':str(base/'env')}
            subprocess.run(['bash',script],cwd=repo,env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            state = repo/'state'
            store = p.Store(state/'history.sqlite')
            product = {'url':'https://example.com/p','title':'Smart robot'}
            store.reserve(product,['photo-hash'],'2026-11-19',6)
            with patch.dict(os.environ, {'GITHUB_ACTIONS':'true','STATE_GIT_DIR':str(state)}):
                p.checkpoint(store)
            store.db.close()
            restored = base/'restored'
            git('clone','--branch','parser-state',remote,restored)
            reopened = p.Store(restored/'history.sqlite')
            self.assertTrue(reopened.duplicate(product))
            self.assertEqual(reopened.db.execute('SELECT status FROM deliveries').fetchone()[0], 'pending')
            reopened.db.close()
