"""株価・配当情報取得ユーティリティ

Yahoo Finance APIを使用して株価と配当情報を取得する。
- 日本株: シンボル末尾に .T を付加 (例: 7203 → 7203.T)
- 米国株: そのまま (例: AAPL)
- 香港株: シンボル末尾に .HK を付加 (例: 0700 → 0700.HK)

認証方式:
  Yahoo Finance API は2023年以降 crumb+cookie認証が必要。
  最初にYahoo Financeにアクセスしてcookieとcrumbを取得してから
  APIリクエストに付加する。
"""
import urllib.request
import urllib.parse
import http.cookiejar
import json
import time
import re
from datetime import datetime, timedelta

# ── 認証 & キャッシュ ──────────────────────────────
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar)
)
_crumb = None
_crumb_time = 0
_CRUMB_TTL = 1800  # 30分

_price_cache = {}
_CACHE_TTL = 300  # 5分

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def _get_yahoo_crumb():
    """Yahoo Finance APIのcrumb認証トークンを取得する"""
    global _crumb, _crumb_time

    now = time.time()
    if _crumb and now - _crumb_time < _CRUMB_TTL:
        return _crumb

    try:
        # Step 1: Yahoo Financeトップにアクセスしてcookieを取得
        req1 = urllib.request.Request(
            'https://fc.yahoo.com',
            headers={'User-Agent': _UA}
        )
        try:
            _opener.open(req1, timeout=10)
        except Exception:
            pass  # 404等でもcookieは設定される

        # Step 2: crumbを取得
        req2 = urllib.request.Request(
            'https://query2.finance.yahoo.com/v1/test/getcrumb',
            headers={'User-Agent': _UA}
        )
        resp = _opener.open(req2, timeout=10)
        _crumb = resp.read().decode('utf-8').strip()
        _crumb_time = now
        return _crumb
    except Exception:
        return None


def _yahoo_request(url):
    """crumb認証付きのYahoo Finance APIリクエスト"""
    crumb = _get_yahoo_crumb()

    if crumb:
        sep = '&' if '?' in url else '?'
        url = f'{url}{sep}crumb={urllib.parse.quote(crumb)}'

    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    resp = _opener.open(req, timeout=15)
    return json.loads(resp.read().decode('utf-8'))


def _yahoo_request_simple(url):
    """認証なしの簡易リクエスト（v8 chart API用）"""
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode('utf-8'))


# ── シンボル変換 ──────────────────────────────────
def _to_yahoo_symbol(symbol, currency='JPY'):
    """証券コードをYahoo Financeのシンボルに変換"""
    if not symbol:
        return None

    symbol = symbol.strip()

    # 既にサフィックス付きならそのまま
    if '.' in symbol:
        return symbol

    if currency == 'JPY':
        if symbol.isdigit():
            return f'{symbol}.T'
        return symbol
    elif currency == 'HKD':
        if symbol.isdigit():
            return f'{symbol.zfill(4)}.HK'
        return symbol
    else:
        return symbol


# ── 株価取得 ──────────────────────────────────────
def fetch_stock_price(symbol, currency='JPY'):
    """Yahoo Finance APIから株価を取得する"""
    yahoo_sym = _to_yahoo_symbol(symbol, currency)
    if not yahoo_sym:
        return None

    now = time.time()
    if yahoo_sym in _price_cache:
        cached = _price_cache[yahoo_sym]
        if now - cached['timestamp'] < _CACHE_TTL:
            return cached['data']

    # まずcrumb認証付きで試行、失敗したら認証なしで試行
    for fetch_fn in [_yahoo_request, _yahoo_request_simple]:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1d&range=1d'
            data = fetch_fn(url)

            result = data.get('chart', {}).get('result', [])
            if not result:
                continue

            meta = result[0].get('meta', {})
            price = meta.get('regularMarketPrice', 0)
            prev_close = meta.get('chartPreviousClose', meta.get('previousClose', 0))

            stock_data = {
                'price': price,
                'previous_close': prev_close,
                'change': round(price - prev_close, 4) if prev_close else 0,
                'change_pct': round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                'currency': meta.get('currency', currency),
                'name': meta.get('shortName', ''),
                'market_state': meta.get('marketState', 'UNKNOWN'),
                'updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
            }

            _price_cache[yahoo_sym] = {'data': stock_data, 'timestamp': now}
            return stock_data
        except Exception:
            continue

    return None


def fetch_stock_prices_batch(symbols_currencies):
    """複数銘柄の株価を一括取得する"""
    results = {}
    for symbol, currency in symbols_currencies:
        results[symbol] = fetch_stock_price(symbol, currency)
    return results


# ── 配当情報取得 ──────────────────────────────────
def fetch_dividend_info(symbol, currency='JPY'):
    """
    配当情報を取得する（複数ソースを順に試行）

    優先順位:
      1. v8 chart API（配当履歴から金額・月を計算）
      2. v10 quoteSummary API（crumb認証付き）
      3. KNOWN_DIVIDEND_SCHEDULES（フォールバック）
    """
    yahoo_sym = _to_yahoo_symbol(symbol, currency)
    if not yahoo_sym:
        return None

    # ── Source 1: v8 chart APIから配当履歴を取得 ──
    chart_result = _fetch_dividends_from_chart(yahoo_sym, currency)
    if chart_result:
        return chart_result

    # ── Source 2: v10 quoteSummary API（crumb認証付き） ──
    v10_result = _fetch_dividends_from_v10(yahoo_sym, currency)
    if v10_result:
        return v10_result

    # ── Source 3: 既知のスケジュール ──
    return None


def _fetch_dividends_from_chart(yahoo_sym, currency):
    """v8 chart APIから配当履歴+株価を取得し、年間配当・利回り・支払月を計算する"""
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}'
        f'?interval=1mo&range=3y&events=div'
    )

    for fetch_fn in [_yahoo_request, _yahoo_request_simple]:
        try:
            data = fetch_fn(url)
            result = data.get('chart', {}).get('result', [])
            if not result:
                continue

            meta = result[0].get('meta', {})
            current_price = meta.get('regularMarketPrice', 0)
            chart_currency = meta.get('currency', currency)

            events = result[0].get('events', {}).get('dividends', {})
            if not events:
                continue

            # 配当履歴をパース
            div_history = []
            for ts_key, div_info in events.items():
                ts = div_info.get('date', int(ts_key))
                amount = div_info.get('amount', 0)
                if amount > 0:
                    dt = datetime.fromtimestamp(ts)
                    div_history.append({
                        'date': dt,
                        'month': dt.month,
                        'amount': amount,
                    })

            if not div_history:
                continue

            # 直近1年間の配当を合計 → 年間配当額
            one_year_ago = datetime.now() - timedelta(days=400)  # 少し余裕を持つ
            recent_divs = [d for d in div_history if d['date'] >= one_year_ago]

            if recent_divs:
                annual_dividend = sum(d['amount'] for d in recent_divs)
                # 1年以上前のデータしかない場合は全期間の平均を使う
            else:
                total = sum(d['amount'] for d in div_history)
                years = max(1, (datetime.now() - min(d['date'] for d in div_history)).days / 365)
                annual_dividend = total / years

            # 支払月を抽出（直近2年の実績から）
            two_years_ago = datetime.now() - timedelta(days=730)
            recent_months = set()
            for d in div_history:
                if d['date'] >= two_years_ago:
                    recent_months.add(d['month'])
            payment_months = sorted(recent_months)

            # 配当利回り計算
            div_yield = (annual_dividend / current_price * 100) if current_price > 0 else 0

            return {
                'dividend_yield': round(div_yield, 2),
                'annual_dividend': round(annual_dividend, 2),
                'ex_dividend_date': max(div_history, key=lambda x: x['date'])['date'].strftime('%Y-%m-%d'),
                'payment_months': payment_months,
                'frequency': _frequency_label(len(payment_months)),
                'currency': chart_currency,
                'source': 'chart',
            }

        except Exception:
            continue

    return None


def _fetch_dividends_from_v10(yahoo_sym, currency):
    """v10 quoteSummary APIから配当サマリーを取得する（crumb認証付き）"""
    url = (
        f'https://query2.finance.yahoo.com/v10/finance/quoteSummary/{yahoo_sym}'
        f'?modules=summaryDetail,defaultKeyStatistics,calendarEvents'
    )

    try:
        data = _yahoo_request(url)

        qr = data.get('quoteSummary', {}).get('result', [])
        if not qr:
            return None

        info = qr[0]
        summary = info.get('summaryDetail', {})

        # 配当利回り
        div_yield_raw = summary.get('dividendYield', {})
        div_yield = div_yield_raw.get('raw', 0) * 100 if div_yield_raw.get('raw') else 0

        # 年間配当額
        div_rate_raw = summary.get('dividendRate', {})
        annual_div = div_rate_raw.get('raw', 0) if div_rate_raw.get('raw') else 0

        # 権利落ち日
        ex_date_raw = summary.get('exDividendDate', {})
        ex_date = ''
        if ex_date_raw.get('raw'):
            ex_date = datetime.fromtimestamp(ex_date_raw['raw']).strftime('%Y-%m-%d')

        # 配当が全くない銘柄は除外
        if div_yield <= 0 and annual_div <= 0:
            return None

        # 配当支払月は chart API から取得を試みる（v10には含まれないため）
        payment_months = _get_payment_months_from_chart(yahoo_sym)
        frequency = _frequency_label(len(payment_months))

        return {
            'dividend_yield': round(div_yield, 2),
            'annual_dividend': annual_div,
            'ex_dividend_date': ex_date,
            'payment_months': payment_months,
            'frequency': frequency,
            'currency': summary.get('currency', {}).get('raw', currency) if isinstance(summary.get('currency'), dict) else currency,
            'source': 'v10',
        }

    except Exception:
        return None


def _get_payment_months_from_chart(yahoo_sym):
    """chart APIから配当支払月のみを取得する（軽量版）"""
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}'
        f'?interval=1mo&range=3y&events=div'
    )

    for fetch_fn in [_yahoo_request, _yahoo_request_simple]:
        try:
            data = fetch_fn(url)
            result = data.get('chart', {}).get('result', [])
            if not result:
                continue

            events = result[0].get('events', {}).get('dividends', {})
            if not events:
                continue

            two_years_ago = datetime.now() - timedelta(days=730)
            months = set()
            for ts_key, div_info in events.items():
                ts = div_info.get('date', int(ts_key))
                dt = datetime.fromtimestamp(ts)
                if dt >= two_years_ago:
                    months.add(dt.month)

            if months:
                return sorted(months)
        except Exception:
            continue

    return []


def _frequency_label(month_count):
    """配当月数から頻度ラベルを返す"""
    if month_count >= 12:
        return '毎月'
    elif month_count >= 4:
        return '四半期'
    elif month_count >= 2:
        return '半期'
    elif month_count == 1:
        return '年1回'
    return '不明'


# ── フォールバック: 既知の配当スケジュール ──────────
# APIが使えない場合の最終フォールバック
# annual_dividend: 1株あたりの年間予想配当（円/ドル）
KNOWN_DIVIDEND_SCHEDULES = {
    # ── 日本株（権利確定月） ──
    # 高配当・人気銘柄
    '4502': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 196, 'currency': 'JPY'},    # 武田薬品工業
    '4503': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 74, 'currency': 'JPY'},     # アステラス製薬
    '6758': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 105, 'currency': 'JPY'},    # ソニーグループ
    '7203': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 75, 'currency': 'JPY'},     # トヨタ自動車
    '7974': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 206, 'currency': 'JPY'},    # 任天堂
    '8002': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 90, 'currency': 'JPY'},     # 丸紅
    '8058': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 100, 'currency': 'JPY'},    # 三菱商事
    '8031': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 170, 'currency': 'JPY'},    # 三井物産
    '8001': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 200, 'currency': 'JPY'},    # 伊藤忠商事
    '8053': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 150, 'currency': 'JPY'},    # 住友商事
    '9432': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 5.2, 'currency': 'JPY'},    # NTT（分割後）
    '9433': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 70, 'currency': 'JPY'},     # KDDI
    '9434': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 86, 'currency': 'JPY'},     # ソフトバンク
    '9984': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 44, 'currency': 'JPY'},     # ソフトバンクG
    # メガバンク・金融
    '8306': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 50, 'currency': 'JPY'},     # 三菱UFJ
    '8316': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 34, 'currency': 'JPY'},     # 三井住友FG
    '8411': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 37, 'currency': 'JPY'},     # みずほFG
    '8766': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 300, 'currency': 'JPY'},    # 東京海上HD
    '8591': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 100, 'currency': 'JPY'},    # オリックス
    # 高配当ETF
    '1489': {'months': [1, 4, 7, 10], 'frequency': '四半期', 'annual_dividend': 1000, 'currency': 'JPY'},  # NF日経高配当50
    '2914': {'months': [6, 12], 'frequency': '半期', 'annual_dividend': 194, 'currency': 'JPY'},           # JT（日本たばこ）
    # その他人気銘柄
    '9101': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 260, 'currency': 'JPY'},    # 日本郵船
    '9104': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 180, 'currency': 'JPY'},    # 商船三井
    '5401': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 160, 'currency': 'JPY'},    # 日本製鉄
    '8593': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 160, 'currency': 'JPY'},    # 三菱HCキャピタル
    '3382': {'months': [2, 8], 'frequency': '半期', 'annual_dividend': 58, 'currency': 'JPY'},     # セブン&アイ
    '4063': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 240, 'currency': 'JPY'},    # 信越化学
    '6861': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 300, 'currency': 'JPY'},    # キーエンス
    '6098': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 30, 'currency': 'JPY'},     # リクルートHD
    '6273': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 66, 'currency': 'JPY'},     # SMC
    '7267': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 68, 'currency': 'JPY'},     # ホンダ
    '6501': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 150, 'currency': 'JPY'},    # 日立製作所
    '6902': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 220, 'currency': 'JPY'},    # デンソー
    '8035': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 355, 'currency': 'JPY'},    # 東京エレクトロン
    '6594': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 70, 'currency': 'JPY'},     # 日本電産
    '4661': {'months': [3, 9], 'frequency': '半期', 'annual_dividend': 100, 'currency': 'JPY'},    # OLC
    '9843': {'months': [2, 8], 'frequency': '半期', 'annual_dividend': 58, 'currency': 'JPY'},     # ニトリHD

    # ── 米国株（配当支払月） ──
    'AAPL': {'months': [2, 5, 8, 11], 'frequency': '四半期', 'annual_dividend': 1.00, 'currency': 'USD'},
    'MSFT': {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 3.32, 'currency': 'USD'},
    'JNJ':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 5.04, 'currency': 'USD'},
    'KO':   {'months': [4, 7, 10, 12], 'frequency': '四半期', 'annual_dividend': 1.94, 'currency': 'USD'},
    'PG':   {'months': [2, 5, 8, 11], 'frequency': '四半期', 'annual_dividend': 4.03, 'currency': 'USD'},
    'PEP':  {'months': [1, 3, 6, 9], 'frequency': '四半期', 'annual_dividend': 5.42, 'currency': 'USD'},
    'XOM':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 3.80, 'currency': 'USD'},
    'ABBV': {'months': [2, 5, 8, 11], 'frequency': '四半期', 'annual_dividend': 6.20, 'currency': 'USD'},
    # 米国ETF
    'VYM':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 3.50, 'currency': 'USD'},
    'VIG':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 3.40, 'currency': 'USD'},
    'SPYD': {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 1.80, 'currency': 'USD'},
    'HDV':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 3.80, 'currency': 'USD'},
    'VTI':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 3.60, 'currency': 'USD'},
    'VOO':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 6.80, 'currency': 'USD'},
    'QQQ':  {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 2.50, 'currency': 'USD'},
    'SCHD': {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 2.80, 'currency': 'USD'},
    'VT':   {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 2.20, 'currency': 'USD'},
    'VXUS': {'months': [3, 6, 9, 12], 'frequency': '四半期', 'annual_dividend': 1.80, 'currency': 'USD'},
    'AGG':  {'months': list(range(1, 13)), 'frequency': '毎月', 'annual_dividend': 3.30, 'currency': 'USD'},
    'BND':  {'months': list(range(1, 13)), 'frequency': '毎月', 'annual_dividend': 3.20, 'currency': 'USD'},
}


def get_dividend_schedule(symbol):
    """既知の配当スケジュールを返す（フォールバック用）"""
    clean_sym = symbol.strip().replace('.T', '').replace('.HK', '')
    return KNOWN_DIVIDEND_SCHEDULES.get(clean_sym)
