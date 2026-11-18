"""車型資料：共用的讀寫與轉換規則（沿用 2026-10-30 整理主檔時的規則）。"""
import csv, json, re, os, html
from urllib.parse import unquote
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')
MASTER = os.path.join(DATA, 'taiwan_vehicle_models.csv')
SEEN = os.path.join(DATA, 'yahoo_seen.txt')
APPJSON = os.path.join(DATA, 'vehicles.json')
R = json.load(open(os.path.join(DATA, 'rules.json'), encoding='utf-8'))
SLUG, YBRAND, ZH, EN = R['SLUG'], R['YBRAND'], R['ZH'], R['EN']
SPEC_KEYS = ['body_type', 'fuel_type', 'displacement_cc', 'transmission', 'doors', 'horsepower_ps', 'fuel_tank_liters']
PHEV_NAME = re.compile(r'PHEV|PHV|Plug-?in|E-Hybrid|Recharge|\bT8\b|\b\d{3}e\b|\b(x|s)Drive\d+e\b|S E ALL4', re.I)

def read_master():
    with open(MASTER, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return rows, list(rows[0].keys())

def write_master(rows, cols):
    with open(MASTER, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

def read_seen():
    return set(x.strip() for x in open(SEEN, encoding='utf-8') if x.strip())

def write_seen(s):
    open(SEEN, 'w', encoding='utf-8').write('\n'.join(sorted(s)) + '\n')

def norm_path(u):
    return unquote(re.sub(r'^https?://[^/]+', '', u.strip())).replace('/spec', '').rstrip('/')

def body_norm(s, veh):
    s = s or ''
    if veh == '機車':
        for k, v in [('速克達', '速克達'), ('越野', '越野車'), ('跑車', '跑車'), ('仿賽', '跑車'), ('美式', '美式車'), ('巡航', '美式車'),
                     ('街車', '街車'), ('檔車', '檔車'), ('復古', '街車'), ('多功能', '多功能車'), ('旅行', '旅跑車')]:
            if k in s: return v
        return s.split(',')[0]
    for k, v in [('皮卡', '貨卡'), ('貨', '貨車'), ('廂型', '廂型車'), ('休旅', '休旅車'), ('敞篷', '敞篷車'), ('旅行', '旅行車'),
                 ('跑車', '跑車'), ('掀背', '掀背車'), ('轎車', '轎車')]:
        if k in s: return v
    return ''

def fuel_norm(s):
    s = s or ''
    return '插電式複合動力' if '插電' in s else '油電混合' if '油電' in s else '電動' if '電動' in s else '柴油' if '柴油' in s else '汽油' if s else ''

def num(s):
    m = re.search(r'\d+(?:\.\d+)?', (s or '').replace(',', ''))
    return float(m.group()) if m else None

def trans_norm(s):
    s = (s or '').replace(' ', '')
    for a, b in zip('一二三四五六七八九十', ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']): s = s.replace(a + '速', b + '速')
    m = re.search(r'(\d+)速', s)
    if '無段' in s or 'CVT' in s.upper(): return 'CVT'
    if m and ('手自排' in s or '自排' in s or '雙離合' in s): return 'A' + m.group(1)
    if m and ('手排' in s or '鏈' in s or '皮帶' in s or '軸傳' in s): return 'M' + m.group(1)
    if '單速' in s or '固定齒比' in s: return 'A1'
    return s[:12]

def g(v):
    return f'{v:g}' if v else ''

def parse_spec_page(h):
    """Yahoo 規格頁 → 標題與規格欄位（同名欄位以後面的詳細規格為準）。"""
    t = re.search(r'<title>(.*?)</title>', h, re.S)
    title = html.unescape(t.group(1).split('|')[0].strip()) if t else ''
    spec = {}
    for k, v in re.findall(r'<li>\s*<span>([^<]{1,20})</span>\s*<span[^>]*>(.*?)</span>\s*</li>', h, re.S):
        v = html.unescape(re.sub(r'<[^>]+>', ' ', v)); v = re.sub(r'\s+', ' ', v).strip()
        if v: spec[k.strip()] = v
    return title, spec

def split_path(path):
    m = re.search(r'/new-(cars|bikes)/trim/(.+?)-((?:19|20)\d\d)-(.+)$', path)
    if m and m.group(2) in SLUG:   # 車型名稱像年份的（Peugeot 2008／3008／5008）
        m = re.search(r'/new-(cars|bikes)/trim/(.+?-(?:19|20)\d\d)-((?:19|20)\d\d)-(.+)$', path)
    return m

def page_to_row(path, title, spec):
    """一個 Yahoo 車款頁 → 主檔的一列（沿用 build_yahoo_master.py 的命名與規格規則）。回傳 (row, 問題清單)。"""
    issues = []
    m = split_path(path)
    if not m: return None, ['網址格式看不懂']
    kind, mm, yr, tr = m.group(1), m.group(2).replace('(new)', ''), int(m.group(3)), m.group(4)
    mk = next((s for s in sorted(SLUG, key=len, reverse=True) if mm.startswith(s + '-')), None)
    if not mk:
        mk = mm.split('-')[0]; brand = mk.capitalize(); issues.append(f'新品牌（Yahoo 代號 {mk}），品牌名稱要確認')
    else:
        brand = SLUG[mk]
    veh = '機車' if kind == 'bikes' else '汽車'
    words = mm[len(mk) + 1:].replace('(new)', '').split('-')
    model_name = ' '.join(w.capitalize() if len(w) > 3 else w.upper() for w in words)
    rest = re.sub(r'^.*?\b(19|20)\d\d\s+', '', title) if title else ''
    if rest:
        rw = rest.split(); n = len(words)
        target = re.sub(r'[^A-Z0-9]', '', ''.join(words).upper())
        for k in range(1, len(rw) + 1):
            if re.sub(r'[^A-Z0-9]', '', re.sub(r'\(NEW\)', '', ' '.join(rw[:k]).upper())) == target: n = k; break
        model_name = re.sub(r'\(NEW\)|\(New\)', '', ' '.join(rw[:n])).strip() or model_name
        trim_name = re.sub(r'(\d)\.\.(\d)', r'\1.\2', ' '.join(rw[n:])) or '標準版'
    else:
        trim_name = re.sub(r'(\d)-\.-(\d)', r'\1.\2', tr).replace('-', ' ')
    bd = YBRAND.get(brand) if veh == '汽車' and brand in YBRAND else (f'{brand} {ZH[brand]}'.strip() if ZH.get(brand) else f"{brand} {EN.get(brand, '')}".strip())
    r = dict(vehicle_type=veh, brand=brand, brand_display=bd, model_family=model_name, trim=trim_name,
             display_name=f'{bd} {model_name} {trim_name}'.replace(' 標準版', ''), year_from=str(yr), year_to=str(yr),
             body_type=body_norm(spec.get('車身型式'), veh), fuel_type=fuel_norm(spec.get('動力型式')),
             displacement_cc=g(num(spec.get('排氣量'))), transmission=trans_norm(spec.get('變速系統') or spec.get('變速型式')),
             doors=(f"{int(num(spec['車門數']))}D" if num(spec.get('車門數')) else ''),
             horsepower_ps='', hp_source='', hp_official='', fuel_tank_liters='', tank_source='',
             battery_kwh='', battery_usable_kwh='', ev_range_km='', phev_basis='',
             data_source='Yahoo', official_model='', yahoo_url='https://autos.yahoo.com.tw' + path, review_note='')
    hp = re.match(r'\s*(\d+(?:\.\d+)?)\s*hp', spec.get('最大馬力') or '', re.I)
    if hp: r['horsepower_ps'], r['hp_source'] = g(float(hp.group(1))), 'Yahoo'
    tot = re.match(r'\s*(\d+(?:\.\d+)?)\s*hp', spec.get('系統總合輸出') or '', re.I)
    if tot: r['horsepower_ps'], r['hp_source'] = g(float(tot.group(1))), 'Yahoo（系統總合）'   # 油電車用系統總馬力（2026-10-30 決定）
    if re.search(r'\d', spec.get('油箱容量') or ''):
        tk = num(spec['油箱容量'])
        if tk: r['fuel_tank_liters'], r['tank_source'] = g(tk), 'Yahoo'
    if r['fuel_type'] == '電動':
        r['battery_kwh'] = g(num(spec.get('電池度數'))); r['battery_usable_kwh'] = g(num(spec.get('可用度數')))
    rng = num(spec.get('滿電可續航里程'))
    if rng: r['ev_range_km'] = g(rng)
    if veh == '汽車' and r['fuel_type'] == '油電混合':
        if rng: r['fuel_type'], r['phev_basis'] = '插電式複合動力', 'Yahoo 滿電可續航里程'
        elif PHEV_NAME.search(model_name + ' ' + trim_name): r['fuel_type'], r['phev_basis'] = '插電式複合動力', '車名'
    if not spec: issues.append('讀不到規格')
    if not r['horsepower_ps'] and r['fuel_type'] != '電動': issues.append('沒有馬力')
    if r['fuel_type'] not in ('電動', '') and not r['fuel_tank_liters']: issues.append('沒有油箱容量')
    if r['fuel_type'] == '': issues.append('沒有燃料')
    return r, issues

def build_app_json(rows):
    """主檔 → 車帳本的 vehicles.json：{汽車|機車: {品牌: {車型: [[車款, 起, 迄, 車種, 燃料, 排氣量, 變速箱, 門數, 馬力, 油箱或可用度數]]}}}"""
    def n(v):
        if v in ('', None): return None
        f = float(v); return int(f) if f == int(f) else f
    out = {}
    for r in rows:
        last = n(r['battery_usable_kwh'] or r['battery_kwh']) if r['fuel_type'] == '電動' else n(r['fuel_tank_liters'])
        x = [r['trim'], int(r['year_from']), int(r['year_to']), r['body_type'], r['fuel_type'], n(r['displacement_cc']), r['transmission'], r['doors'], n(r['horsepower_ps']), last]
        out.setdefault(r['vehicle_type'], {}).setdefault(r['brand_display'], {}).setdefault(r['model_family'], []).append(x)
    with open(APPJSON, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
