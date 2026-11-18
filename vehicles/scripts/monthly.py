"""每月車型檢查：找出 Yahoo 汽車機車新出現的車款頁，讀規格、轉成主檔格式，寫成待確認清單。
也列出政府新審驗的車型（環境部噪音審驗、能源署耗能證明）給人對照。不會改主檔；主檔要等確認後由 apply.py 套用。

用法：python3 vehicles/scripts/monthly.py [最多讀幾頁，預設 600]
輸出：vehicles/pending/<日期>.csv、vehicles/pending/<日期>.md、vehicles/pending/latest.json（給 Actions 開 issue 用）
"""
import csv, json, os, re, sys, time, datetime, urllib.request, gzip
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

UA = {'User-Agent': 'Mozilla/5.0 (compatible; chezhangben-vehicle-check/1.0; +https://github.com/howard90201/oil-prices)',
      'Accept-Language': 'zh-TW,zh;q=0.9', 'Accept-Encoding': 'gzip'}
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 600
BUDGET = 45 * 60            # 讀車款頁最多 45 分鐘，沒讀完的下個月接著讀
T0 = time.time()
TZ = datetime.timezone(datetime.timedelta(hours=8))
today = datetime.datetime.now(TZ).date()
PEND = os.path.join(C.DATA, '..', 'pending')
os.makedirs(PEND, exist_ok=True)
log = []

def get(url, tries=2):
    for i in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20)
            b = r.read()
            if r.headers.get('Content-Encoding') == 'gzip': b = gzip.decompress(b)
            return b.decode('utf-8', 'ignore')
        except Exception as e:
            err = e; time.sleep(3 * (i + 1))
    raise err

# 1. Yahoo 網站地圖裡所有車款頁
def sitemap_trims():
    idx = get('https://autos.yahoo.com.tw/sitemap.xml')
    maps = [u for u in re.findall(r'<loc>([^<]+)</loc>', idx) if re.search(r'sitemap_(car|bike)_trims_\d+\.xml', u)]
    out = {}
    def one(u):
        try:
            return get(u)
        except Exception as e:
            log.append(f'網站地圖讀取失敗：{u}（{e}）'); return ''
    with ThreadPoolExecutor(4) as ex:
        for x in ex.map(one, maps):
            for blk in re.findall(r'<url>(.*?)</url>', x, re.S):
                loc = re.search(r'<loc>([^<]+)</loc>', blk)
                lm = re.search(r'<lastmod>([^<]+)</lastmod>', blk)
                if loc and '/trim/' in loc.group(1):
                    out[C.norm_path(loc.group(1))] = lm.group(1)[:10] if lm else ''
    return out, len(maps)

rows, cols = C.read_master()
seen = C.read_seen()
trims, nmaps = sitemap_trims()
T_MAP = round(time.time() - T0)
new = [p for p in trims if p not in seen]
def year_of(p):
    m = C.split_path(p); return int(m.group(3)) if m else 0
new.sort(key=lambda p: (-year_of(p), p))       # 新年式先讀
old = [p for p in new if year_of(p) < today.year - 1]   # 舊年式頁：主檔建立時已用其他方式涵蓋，每月不讀
new = [p for p in new if year_of(p) >= today.year - 1]
todo, later = new[:LIMIT], new[LIMIT:]

# 2. 讀新車款頁並分類
by_key = {}
for r in rows:
    by_key.setdefault((r['vehicle_type'], r['brand'], r['model_family'].upper(), r['trim'].upper()), []).append(r)
models = {(r['vehicle_type'], r['brand'], r['model_family'].upper()) for r in rows}
brands = {(r['vehicle_type'], r['brand']) for r in rows}
pending, failed = [], []
ERR = {}
def fetch(p):
    try:
        h = get('https://autos.yahoo.com.tw' + p + '/spec', tries=1)
        time.sleep(1.5)
        return p, h
    except Exception as e:
        k = getattr(e, 'code', None) or type(e).__name__
        ERR[k] = ERR.get(k, 0) + 1
        time.sleep(5)
        return p, None
T1 = time.time(); done_n = 0
streak = 0
with ThreadPoolExecutor(1) as ex:
    for k0 in range(0, len(todo), 40):
        if streak >= 30:
            later = todo[k0:] + later; todo = todo[:k0]; log.append('Yahoo 連續 30 頁讀不到，先停，下個月再讀'); break
        if time.time() - T0 > BUDGET:
            later = todo[k0:] + later; todo = todo[:k0]; log.append('時間到，剩下的下個月再讀'); break
        for p, h in ex.map(fetch, todo[k0:k0 + 40]):
            done_n += 1
            if h is None:
                failed.append(p); streak += 1; continue
            streak = 0
            title, spec = C.parse_spec_page(h)
            r, issues = C.page_to_row(p, title, spec)
            seen.add(p)
            if not r:
                pending.append(dict(action='略過', note='；'.join(issues), yahoo_url='https://autos.yahoo.com.tw' + p)); continue
            k = (r['vehicle_type'], r['brand'], r['model_family'].upper(), r['trim'].upper())
            y = int(r['year_from'])
            same = by_key.get(k, [])
            if any(int(x['year_from']) <= y <= int(x['year_to']) for x in same):
                continue                                   # 這個年份已經有了（Yahoo 重新上架同一頁）
            prev = next((x for x in same if int(x['year_to']) == y - 1 and all(x[c] == r[c] for c in C.SPEC_KEYS)), None)
            if prev:
                action = '延長年份'; issues.insert(0, f"把 {prev['year_from']}–{prev['year_to']} 那筆延長到 {y}")
            elif same:
                action = '新增'; issues.insert(0, '同車款新年份或規格不同')
            elif (r['vehicle_type'], r['brand'], r['model_family'].upper()) in models:
                action = '新增'; issues.insert(0, '既有車型的新車款')
            elif (r['vehicle_type'], r['brand']) in brands:
                action = '新增'; issues.insert(0, '新車型，名稱要確認')
            else:
                action = '新增'; issues.insert(0, '新品牌')
            pending.append(dict(action=action, note='；'.join(issues), **r))
        time.sleep(2)
T_PAGES = round(time.time() - T1)

# 3. 政府新審驗車型（只列出來對照，不自動加進主檔）
gov = []
try:
    meta = json.loads(get('https://data.gov.tw/api/v2/rest/dataset/34934'))
    url = next(d['resourceDownloadUrl'] for d in meta['result']['distribution'] if d['resourceFormat'] == 'JSON')
    key = re.search(r'api_key=([\w-]+)', url).group(1)
    since = today - datetime.timedelta(days=45)
    for ds in ['nos_p_11', 'nos_p_16', 'nos_p_17', 'nos_p_18', 'nos_p_19', 'nos_p_20']:
        try:
            d = json.loads(get(f'https://data.moenv.gov.tw/api/v2/{ds}?api_key={key}&limit=1000&sort=ImportDate%20desc&format=JSON'))
            recs = d.get('records', d) if isinstance(d, dict) else d
            for x in recs:
                m = re.match(r'(\d+)/(\d+)/(\d+)', x.get('approveddate', ''))
                if not m: continue
                dt = datetime.date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
                if dt >= since:
                    gov.append(dict(來源='環境部噪音審驗 ' + ds.upper(), 核准日期=dt.isoformat(), 廠商=x.get('applicant', ''),
                                    車型=x.get('vehiclemodel', ''), 最大馬力kW=x.get('maximumpower_value', ''), 排氣量=x.get('enginecapacity_value', '')))
        except Exception as e:
            log.append(f'環境部 {ds} 讀取失敗（{e}）')
except Exception as e:
    log.append(f'環境部資料讀取失敗（{e}）')
try:
    lst = get('https://www.moeaea.gov.tw/ECW/populace/opendata/wHandOpenData_File.ashx?set_id=305')
    files = list(csv.DictReader(lst.lstrip('﻿').splitlines()))
    state_f = os.path.join(C.DATA, 'gov_state.json')
    st = json.load(open(state_f)) if os.path.exists(state_f) else {'ec_files': []}
    newf = [f for f in files if (f.get('FileName(檔案名稱)') or f.get('FileName') or '') not in st['ec_files']]
    for f in newf:
        gov.append(dict(來源='能源署耗能證明', 核准日期=f.get('CreateDate(建立日期)', ''), 廠商='', 車型=f.get('FileName(檔案名稱)', ''),
                        最大馬力kW='', 排氣量=f.get('FileUrl(檔案連結)', '')))
    st['ec_files'] = sorted({f.get('FileName(檔案名稱)') or f.get('FileName') or '' for f in files})
    json.dump(st, open(state_f, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
except Exception as e:
    log.append(f'能源署資料讀取失敗（{e}）')

# 4. 輸出
C.write_seen(seen)
stamp = today.isoformat()
cols_out = ['action', 'note'] + cols
with open(os.path.join(PEND, f'{stamp}.csv'), 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols_out, extrasaction='ignore'); w.writeheader(); w.writerows(pending)
if gov:
    with open(os.path.join(PEND, f'{stamp}_政府新審驗.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(gov[0].keys())); w.writeheader(); w.writerows(gov)
cnt = {}
for p in pending: cnt[p['action']] = cnt.get(p['action'], 0) + 1
flag = [p for p in pending if p['action'] == '新增' and any(s in p['note'] for s in ('新車型', '新品牌', '沒有', '讀不到'))]
md = [f'# 車型資料每月檢查 {stamp}', '',
      f'Yahoo 網站地圖：{nmaps} 份、{len(trims)} 個車款頁；資料庫已讀過 {len(trims) - len(new)} 個。',
      f'這次新讀 {len(todo)} 頁' + (f'，還有 {len(later)} 頁下個月再讀' if later else '') + (f'，{len(failed)} 頁讀取失敗（下次重試）' if failed else '') + '。', '',
      '| 動作 | 筆數 |', '|---|---|'] + [f'| {k} | {v} |' for k, v in sorted(cnt.items())] + ['',
      f'政府新審驗車型（最近 45 天、對照用）：{len(gov)} 筆。', '']
if flag:
    md += ['## 需要確認的', ''] + [f"- {p['display_name']} {p['year_from']}：{p['note']}" for p in flag[:60]] + ([f'- …另有 {len(flag) - 60} 筆'] if len(flag) > 60 else []) + ['']
adds = [p for p in pending if p['action'] == '新增' and p not in flag]
if adds:
    md += ['## 新增車款', ''] + [f"- {p['display_name']} {p['year_from']}（{p['fuel_type']}，{p['horsepower_ps'] or '—'} 匹）" for p in adds[:80]] + ([f'- …另有 {len(adds) - 80} 筆'] if len(adds) > 80 else []) + ['']
if ERR:
    log.append('讀取失敗原因：' + '、'.join(f'{k} {v} 頁' for k, v in ERR.items()))
if log:
    md += ['## 連線問題', ''] + ['- ' + x for x in log] + ['']
md += ['完整清單：`vehicles/pending/' + stamp + '.csv`。確認後在這個 issue 加上「同意套用」標籤，或回覆 Claude，就會套用到主檔和車帳本的車款資料。']
open(os.path.join(PEND, f'{stamp}.md'), 'w', encoding='utf-8').write('\n'.join(md) + '\n')
json.dump(dict(date=stamp, sitemap_trims=len(trims), new_total=len(new), read=done_n, t_sitemap=T_MAP, t_pages=T_PAGES, counts=cnt, flagged=len(flag), gov=len(gov), later=len(later), failed=len(failed), old_unread=len(old), errors={str(k): v for k, v in ERR.items()}, log=log),
          open(os.path.join(PEND, 'latest.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print('\n'.join(md))
