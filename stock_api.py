"""株価・配当情報取得ユーティリティ

Yahoo Finance APIを使用して株価と配当情報を取得する。
- 日本株: シンボル末尾に .T を付加 (例: 7203 → 7203.T)
- 米国株: そのまま (例: AAPL)
- 香港株: シンボル末尾に .HK を付加 (例: 0700 → 0700.HK)
"""
import urllib.request
import json
import time
from datetime import datetime

# キャッシュ: {symbol: {data, timestamp}}
_price_cache = {}
_CACHE_TTL = 300  # 5分キャッシュ


def _to_yahoo_symbol(symbol, currency='JPY'):
    """証券コードをYahoo Financeのシンボルに変換"""
    if not symbol:
        return None

    symbol = symbol.strip()

    # 既にサフィックス付きならそのまま
    if '.' in symbol:
        return symbol

    # 通貨から市場を推定
    if currency == 'JPY':
        # 数字のみ → 日本株
        if symbol.isdigit():
            return f'{symbol}.T'
        # アルファベット → 投資信託等（Yahoo Financeでは取得不可の場合が多い）
        return symbol
    elif currency == 'HKD':
        if symbol.isdigit():
            return f'{symbol.zfill(4)}.HK'
        return symbol
    else:
        # USD等 → 米国株としてそのまま
        return symbol


def fetch_stock_price(symbol, currency='JPY'):
    """
    Yahoo Finance APIから株価を取得する

    Returns:
        dict: {
            'price': float,          # 現在値
            'previous_close': float, # 前日終値
            'change': float,         # 変動額
            'change_pct': float,     # 変動率(%)
            'currency': str,         # 通貨
            'name': str,             # 銘柄名
            'market_state': str,     # 市場状態
            'updated': str,          # 更新時刻
        }
        None: 取得失敗時
    """
    yahoo_sym = _to_yahoo_symbol(symbol, currency)
    if not yahoo_sym:
        return None

    # キャッシュチェック
    now = time.time()
    if yahoo_sym in _price_cache:
        cached = _price_cache[yahoo_sym]
        if now - cached['timestamp'] < _CACHE_TTL:
            return cached['data']

    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1d&range=1d'

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))

        result = data.get('chart', {}).get('result', [])
        if not result:
            return None

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
        return None


def fetch_stock_prices_batch(symbols_currencies):
    """
    複数銘柄の株価を一括取得する

    Args:
        symbols_currencies: [(symbol, currency), ...]

    Returns:
        dict: {symbol: stock_data_or_None}
    """
    results = {}
    for symbol, currency in symbols_currencies:
        results[symbol] = fetch_stock_price(symbol, currency)
    return results


def fetch_dividend_info(symbol, currency='JPY'):
    """
    Yahoo Finance APIから配当情報を取得する

    Returns:
        dict: {
            'dividend_yield': float,       # 配当利回り(%)
            'annual_dividend': float,      # 年間配当額
            'ex_dividend_date': str,       # 権利落ち日
            'payment_months': list[int],   # 配当支払月リスト
            'frequency': str,             # 配当頻度 (年1回, 半期, 四半期, 毎月)
            'currency': str,
        }
        None: 取得失敗時
    """
    yahoo_sym = _to_yahoo_symbol(symbol, currency)
    if not yahoo_sym:
        return None

    # v10 API でサマリー情報を取得
    url = (
        f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{yahoo_sym}'
        f'?modules=summaryDetail,defaultKeyStatistics,calendarEvents'
    )

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))

        qr = data.get('quoteSummary', {}).get('result', [])
        if not qr:
            return None

        info = qr[0]
        summary = info.get('summaryDetail', {})
        calendar = info.get('calendarEvents', {})

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

        # 配当支払月の推定（配当履歴から）
        payment_months = _estimate_payment_months(yahoo_sym)
        frequency = _frequency_label(len(payment_months))

        return {
            'dividend_yield': round(div_yield, 2),
            'annual_dividend': annual_div,
            'ex_dividend_date': ex_date,
            'payment_months': payment_months,
            'frequency': frequency,
            'currency': summary.get('currency', {}).get('raw', currency) if isinstance(summary.get('currency'), dict) else currency,
        }

    except Exception:
        return None


def _estimate_payment_months(yahoo_sym):
    """配当履歴から支払月を推定する"""
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}'
        f'?interval=1mo&range=2y&events=div'
    )

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))

        result = data.get('chart', {}).get('result', [])
        if not result:
            return []

        events = result[0].get('events', {}).get('dividends', {})
        if not events:
            return []

        months = set()
        for ts_key, div_info in events.items():
            ts = div_info.get('date', int(ts_key))
            dt = datetime.fromtimestamp(ts)
            months.add(dt.month)

        return sorted(months)

    except Exception:
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


# 配当支払月のよく知られたパターン（APIが使えない場合のフォールバック）
KNOWN_DIVIDEND_SCHEDULES = {
    # 日本の主要銘柄（権利確定月）
    '7203': {'months': [3, 9], 'frequency': '半期'},      # トヨタ
    '9432': {'months': [3, 9], 'frequency': '半期'},      # NTT
    '8306': {'months': [3, 9], 'frequency': '半期'},      # 三菱UFJ
    '9984': {'months': [3, 9], 'frequency': '半期'},      # ソフトバンクG
    '6758': {'months': [3, 9], 'frequency': '半期'},      # ソニー
    '8058': {'months': [3, 9], 'frequency': '半期'},      # 三菱商事
    # 米国の主要銘柄（配当支払月）
    'AAPL': {'months': [2, 5, 8, 11], 'frequency': '四半期'},
    'MSFT': {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'JNJ':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'KO':   {'months': [4, 7, 10, 12], 'frequency': '四半期'},
    'VYM':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'VIG':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'SPYD': {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'HDV':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'VTI':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'VOO':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'QQQ':  {'months': [3, 6, 9, 12], 'frequency': '四半期'},
    'SCHD': {'months': [3, 6, 9, 12], 'frequency': '四半期'},
}


def get_dividend_schedule(symbol):
    """既知の配当スケジュールを返す（フォールバック用）"""
    clean_sym = symbol.strip().replace('.T', '').replace('.HK', '')
    return KNOWN_DIVIDEND_SCHEDULES.get(clean_sym)
