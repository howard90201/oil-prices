#!/usr/bin/env python3
"""每日油價自動更新：把最新牌價併入 oil_prices_2010.csv。

資料來源（依序）：
  1. 經濟部能源署：中油＋台塑每週參考零售價（主要來源）
  2. 台灣中油牌價 API：核對中油價格；能源署尚未更新時補上
  3. 好市多官網門市 API：好市多三家加油站目前售價
  4. Club Gas Prices（GitHub）：好市多官網連不到時的備援

用法：python3 update_oil_prices.py oil_prices_2010.csv [--days 21]
執行後印出 JSON 摘要（新增幾筆、待確認、哪些來源失敗）。
"""
import csv, gzip, io, json, sys, uuid, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
HEAD = ['品牌', '油品', '每公升價格', '生效時間', '與上次差額', '來源', '狀態']
ORDER = ['92', '95', '98', '柴油']
BRANDS = {'中油': 0, '台塑': 1, '好市多': 2}
BIG_JUMP = 3.0  # 單次漲跌達 3 元以上標「待確認」，但照樣寫入
UA = {'User-Agent': 'Mozilla/5.0 (oil-price-log)'}

MOEAEA = 'https://www2.moeaea.gov.tw/oil111/Gasoline/RetailPrice/load'
CPC = 'https://vipmbr.cpc.com.tw/CPCSTN/ListPriceWebService.asmx/getCPCMainProdListPrice_XML'
COSTCO = 'https://www.costco.com.tw/rest/v2/taiwan/stores?fields=FULL&pageSize=100&lang=zh_TW&curr=TWD'
CGP = 'https://github.com/coatless-data/club-gas-prices/releases/download/current/club-gas-latest.csv'


def http(url, data=None, headers=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def multipart(fields):
    b = uuid.uuid4().hex
    body = ''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                   for k, v in fields.items()) + f'--{b}--\r\n'
    return body.encode(), {'Content-Type': f'multipart/form-data; boundary={b}'}


# ---------- 來源解析（各自獨立，方便測試） ----------
def parse_moeaea(js):
    """回傳 [(品牌, 油品, 價格, 'YYYY-MM-DD HH:MM')]"""
    out = []
    for w in js['data']['gasoline']:
        d = datetime.strptime(w['Date'], '%Y/%m/%d').strftime('%Y-%m-%d')
        for brand, pre, hk in (('中油', 'A', 'Astart'), ('台塑', 'B', 'Bstart')):
            h = int(w.get(hk) or (0 if pre == 'A' else 1))
            for p, k in zip(ORDER, ('92', '95', '98', 'chai')):
                v = w.get(pre + k)
                if v is not None:
                    out.append((brand, p, round(float(v), 1), f'{d} {h:02d}:00'))
    return out


def parse_cpc(xml_bytes):
    names = {'92無鉛汽油': '92', '95無鉛汽油': '95', '98無鉛汽油': '98', '超級柴油': '柴油'}
    out = []
    for t in ET.fromstring(xml_bytes).iter('Table'):
        n = (t.findtext('產品名稱') or '').strip()
        if n in names:
            roc = t.findtext('牌價生效日期').strip()           # 民國，如 1151026
            y, m, d = int(roc[:-4]) + 1911, int(roc[-4:-2]), int(roc[-2:])
            out.append(('中油', names[n], round(float(t.findtext('參考牌價_金額')), 1),
                        f'{y:04d}-{m:02d}-{d:02d} 00:00'))
    return out


def parse_costco(raw):
    """好市多回 JSON 或 XML 都能解析；三站同價時取該價，不同價時取最常見者。"""
    prices = {}
    gmap = {'95': '95', '98': '98', 'Diesel': '柴油', '92': '92'}
    try:
        stores = json.loads(raw)['stores']
        items = [(g.get('name'), g.get('price')) for s in stores for g in (s.get('gasTypes') or [])]
    except (ValueError, KeyError):
        root = ET.fromstring(raw)
        items = [(g.findtext('name'), g.findtext('price')) for g in root.iter('gasTypes')]
    for name, price in items:
        if name in gmap and price:
            prices.setdefault(gmap[name], []).append(round(float(str(price).replace('$', '').replace(',', '')), 1))
    return {p: max(set(v), key=v.count) for p, v in prices.items()}


def parse_cgp(csv_bytes):
    gmap = {'95': '95', '98': '98', 'Diesel': '柴油', '92': '92'}
    prices = {}
    for r in csv.DictReader(io.StringIO(csv_bytes.decode('utf-8'))):
        if r.get('country') == 'TW' and r.get('grade_raw') in gmap:
            prices.setdefault(gmap[r['grade_raw']], []).append(round(float(r['price_local_per_litre']), 1))
    return {p: max(set(v), key=v.count) for p, v in prices.items()}


# ---------- 合併 ----------
def latest(rows, brand, prod):
    c = [r for r in rows if r[0] == brand and r[1] == prod]
    return max(c, key=lambda r: r[3]) if c else None


def merge(rows, candidates, source, summary):
    """candidates: [(品牌, 油品, 價格, 生效時間)]。同品牌油品同一天已有紀錄就比對，不重複寫。"""
    for brand, prod, price, ts in sorted(candidates, key=lambda c: c[3]):
        same_day = [r for r in rows if r[0] == brand and r[1] == prod and r[3][:10] == ts[:10]]
        if same_day:
            if all(abs(float(r[2]) - price) > 0.001 for r in same_day):
                summary['conflicts'].append(f'{brand}{prod} {ts[:10]}：資料庫 {same_day[0][2]}，{source} {price}')
            continue
        prev = [r for r in rows if r[0] == brand and r[1] == prod and r[3] < ts]
        p = max(prev, key=lambda r: r[3]) if prev else None
        diff = round(price - float(p[2]), 1) if p else ''
        status = '待確認' if diff != '' and abs(diff) >= BIG_JUMP else '已確認'
        rows.append([brand, prod, price, ts, diff, source, status])
        summary['added'].append(f'{brand}{prod} {ts} {price}' + ('（待確認）' if status == '待確認' else ''))


def costco_rows(rows, prices, source, summary, now):
    cand = []
    for prod, price in prices.items():
        last = latest(rows, '好市多', prod)
        if not last or abs(float(last[2]) - price) > 0.001:
            cand.append(('好市多', prod, price, now))
    merge(rows, cand, source, summary)


def main(path, days=21):
    rows = [r for r in csv.reader(open(path, encoding='utf-8-sig'))][1:]
    rows = [[r[0], r[1], float(r[2]), r[3], r[4], r[5], r[6]] for r in rows]
    n0 = len(rows)
    now_dt = datetime.now(TZ)
    now = now_dt.strftime('%Y-%m-%d %H:%M')
    s = {'run_at': now, 'added': [], 'conflicts': [], 'failed': [], 'notes': []}

    try:
        body, hdr = multipart({'start': (now_dt - timedelta(days=days)).strftime('%Y/%m/%d'),
                               'end': (now_dt + timedelta(days=7)).strftime('%Y/%m/%d')})
        js = json.loads(http(MOEAEA, body, hdr))
        if js.get('res') != '01':
            raise ValueError(f"res={js.get('res')}")
        merge(rows, parse_moeaea(js), '能源署油價資訊', s)
    except Exception as e:
        s['failed'].append(f'能源署：{e}')

    try:
        cpc = parse_cpc(http(CPC))
        if len(cpc) != 4:
            raise ValueError(f'只解析到 {len(cpc)} 種油品')
        merge(rows, cpc, '中油API', s)
    except Exception as e:
        s['failed'].append(f'中油API：{e}')

    try:
        prices = parse_costco(http(COSTCO, headers={'Accept': 'application/json'}))
        if not prices:
            raise ValueError('沒有加油站價格')
        costco_rows(rows, prices, '好市多官網', s, now)
    except Exception as e:
        s['failed'].append(f'好市多官網：{e}')
        try:
            prices = parse_cgp(http(CGP))
            if not prices:
                raise ValueError('沒有台灣資料')
            costco_rows(rows, prices, 'Club Gas Prices（好市多官網）', s, now)
            s['notes'].append('好市多改用 Club Gas Prices 備援')
        except Exception as e2:
            s['failed'].append(f'Club Gas Prices：{e2}')

    rows.sort(key=lambda r: (r[3], BRANDS.get(r[0], 9), ORDER.index(r[1]) if r[1] in ORDER else 9))
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(rows)
    s['total_rows'] = len(rows)
    s['new_rows'] = len(rows) - n0
    for b in BRANDS:
        s.setdefault('latest', {})[b] = {p: (r[2], r[3]) for p in ORDER if (r := latest(rows, b, p))}
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return s


if __name__ == '__main__':
    args = sys.argv[1:]
    days = int(args[args.index('--days') + 1]) if '--days' in args else 21
    s = main(args[0], days)
    with open('last_run.json', 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    failed = ' '.join(s['failed'])
    # 中油／台塑兩個來源都失敗，或好市多兩個來源都失敗，才算這次更新失敗
    if ('能源署' in failed and '中油API' in failed) or ('好市多官網' in failed and 'Club Gas Prices' in failed):
        sys.exit(1)
