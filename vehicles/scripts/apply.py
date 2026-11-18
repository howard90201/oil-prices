"""把確認過的待確認清單套用到主檔，並重做車帳本用的 vehicles.json。

用法：python3 vehicles/scripts/apply.py vehicles/pending/<日期>.csv
清單的 action 欄：「延長年份」「新增」會套用；改成「不要」或「略過」的那列不套用（可以在套用前手動改）。
"""
import csv, os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

src = sys.argv[1]
rows, cols = C.read_master()
todo = list(csv.DictReader(open(src, encoding='utf-8-sig')))
key = lambda r: (r['vehicle_type'], r['brand'], r['model_family'].upper(), r['trim'].upper())
done = {'延長年份': 0, '新增': 0, '不套用': 0}
for p in sorted(todo, key=lambda p: (key(p) if p.get('brand') else (), p.get('year_from', ''))):
    a = p.get('action', '')
    if a not in ('延長年份', '新增'):
        done['不套用'] += 1; continue
    y = int(p['year_from'])
    r = {c: p.get(c, '') for c in cols}
    prev = next((x for x in rows if key(x) == key(r) and int(x['year_to']) == y - 1 and all(x[c] == r[c] for c in C.SPEC_KEYS)), None)
    if prev:
        prev['year_to'] = str(y); prev['yahoo_url'] = r['yahoo_url']; done['延長年份'] += 1; continue
    if any(key(x) == key(r) and int(x['year_from']) <= y <= int(x['year_to']) for x in rows):
        done['不套用'] += 1; continue
    # 放在同品牌同車型的最後一筆後面，沒有就放在品牌最後
    at = max((i for i, x in enumerate(rows) if x['vehicle_type'] == r['vehicle_type'] and x['brand'] == r['brand'] and x['model_family'].upper() == r['model_family'].upper()), default=None)
    if at is None:
        at = max((i for i, x in enumerate(rows) if x['vehicle_type'] == r['vehicle_type'] and x['brand'] == r['brand']), default=len(rows) - 1)
    rows.insert(at + 1, r); done['新增'] += 1
C.write_master(rows, cols)
C.build_app_json(rows)
os.makedirs(os.path.join(os.path.dirname(src), 'applied'), exist_ok=True)
shutil.move(src, os.path.join(os.path.dirname(src), 'applied', os.path.basename(src)))
print(f"延長年份 {done['延長年份']} 筆、新增 {done['新增']} 筆、不套用 {done['不套用']} 筆；主檔共 {len(rows)} 筆")
