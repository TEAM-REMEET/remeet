"""Execute notebook cells in order without requiring a Jupyter kernel."""
from pathlib import Path
import sys
import traceback
import nbformat

root = Path(__file__).resolve().parent
notebook = nbformat.read(root / 'base_test.ipynb', as_version=4)
scope = {'__name__': '__main__'}
for i, cell in enumerate(notebook.cells):
    if cell.cell_type != 'code':
        continue
    print(f'노트북 셀 {i} 실행', flush=True)
    try:
        exec(compile(cell.source, f'notebook-cell-{i}', 'exec'), scope)
    except Exception:
        if 'exp' in scope:
            scope['exp'].record('failed', f'notebook cell {i}: {sys.exc_info()[0].__name__}')
        traceback.print_exc()
        sys.exit(1)
