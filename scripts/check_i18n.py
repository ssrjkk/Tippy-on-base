"""Check all i18n keys exist in all 3 languages."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import i18n

missing = []
for lang in ('ru', 'en', 'zh'):
    for key in list(i18n.STRINGS.keys()):
        if lang not in i18n.STRINGS[key]:
            missing.append(f'{lang}: {key}')

if missing:
    print('MISSING i18n keys:')
    for m in missing:
        print(f'  {m}')
else:
    print('All i18n keys present in all 3 languages')

print(f'Total keys: {len(i18n.STRINGS)}')
