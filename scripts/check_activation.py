import json
from pathlib import Path
report = json.loads(Path('preview/report.json').read_text())
if report.get('sent',0) < 1:
    print('ACTIVATION BLOCKED: no real post was sent. Check sources, permissions, facts and photos in the report. Schedule remains disabled.')
    raise SystemExit(1)
print('First real Telegram publication confirmed.')
