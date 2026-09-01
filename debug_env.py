import os
from pathlib import Path
path = Path(__file__).resolve().parent / 'debug_env_out.txt'
with open(path, 'w', encoding='utf-8') as f:
    f.write('cwd=' + str(Path.cwd()) + '\n')
    f.write('exists=' + str(Path(__file__).exists()) + '\n')
    f.write('python=' + str(os.environ.get('PYTHONHOME','')) + '\n')
    f.write('path=' + os.environ.get('PATH','')[:1000] + '\n')
print('done')
