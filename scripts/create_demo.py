"""Create a disposable public example outside this repository, then print its path."""
from pathlib import Path
import subprocess
import tempfile

root=Path(tempfile.mkdtemp(prefix='diff-review-demo-'))
def git(*args):
    subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
git('init','-q')
git('config','user.name','Diff Review Demo')
git('config','user.email','demo@example.invalid')
git('config','core.autocrlf','false')
(root/'pricing.py').write_text('''"""Calculate a cart total."""

def calculate_total(items, discount=0):
    subtotal = sum(item["price"] for item in items)
    return subtotal - discount
''',encoding='utf-8')
(root/'README.md').write_text('# Cart service\n\nA small sample project.\n',encoding='utf-8')
git('add','.')
git('commit','-qm','Initial demo')
(root/'pricing.py').write_text('''"""Calculate a cart total, including quantities."""

def calculate_total(items, discount=0):
    subtotal = sum(item["price"] * item.get("quantity", 1) for item in items)
    total = subtotal - discount
    return max(0, total)
''',encoding='utf-8')
(root/'test_pricing.py').write_text('''from pricing import calculate_total

def test_quantity():
    assert calculate_total([{"price": 5, "quantity": 3}]) == 15
''',encoding='utf-8')
(root/'README.md').write_text('# Cart service\n\nSupports item quantities and fixed discounts.\n',encoding='utf-8')
print(root)
