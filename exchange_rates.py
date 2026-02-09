"""為替レート取得ユーティリティ"""
import urllib.request
import json
import time

# キャッシュ: {currency_pair: (rate, timestamp)}
_rate_cache = {}
_CACHE_TTL = 3600  # 1時間キャッシュ

# フォールバックレート（API接続不可時に使用）
DEFAULT_RATES = {
    'USD_JPY': 155.0,
    'HKD_JPY': 20.0,
    'EUR_JPY': 165.0,
    'GBP_JPY': 195.0,
}


def get_exchange_rates():
    """
    USD/JPY等の為替レートを取得する（キャッシュ付き）

    Returns:
        dict: {'USD_JPY': 156.5, 'HKD_JPY': 20.1, ...}
        bool: APIから取得できたかどうか
    """
    now = time.time()

    # キャッシュが有効ならそれを返す
    if _rate_cache and all(
        now - ts < _CACHE_TTL for _, ts in _rate_cache.values()
    ):
        rates = {k: v for k, (v, _) in _rate_cache.items()}
        return rates, True

    # APIからレート取得を試行
    apis = [
        'https://api.exchangerate-api.com/v4/latest/USD',
        'https://open.er-api.com/v6/latest/USD',
    ]

    for api_url in apis:
        try:
            req = urllib.request.Request(
                api_url,
                headers={'User-Agent': 'InvestmentManager/1.0'}
            )
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode('utf-8'))
            api_rates = data.get('rates', {})

            rates = {}
            if 'JPY' in api_rates:
                jpy_per_usd = float(api_rates['JPY'])
                rates['USD_JPY'] = jpy_per_usd

                if 'HKD' in api_rates:
                    rates['HKD_JPY'] = round(jpy_per_usd / float(api_rates['HKD']), 4)
                if 'EUR' in api_rates:
                    rates['EUR_JPY'] = round(jpy_per_usd / float(api_rates['EUR']), 4)
                if 'GBP' in api_rates:
                    rates['GBP_JPY'] = round(jpy_per_usd / float(api_rates['GBP']), 4)

                # キャッシュに保存
                for k, v in rates.items():
                    _rate_cache[k] = (v, now)

                return rates, True
        except Exception:
            continue

    # API不可時はフォールバック
    return DEFAULT_RATES.copy(), False


def convert_to_jpy(value, currency, rates):
    """通貨をJPYに変換する"""
    if currency == 'JPY':
        return value
    rate_key = f'{currency}_JPY'
    rate = rates.get(rate_key, DEFAULT_RATES.get(rate_key, 1.0))
    return value * rate


def set_manual_rate(currency_pair, rate):
    """レートを手動設定する（APIが使えない場合）"""
    _rate_cache[currency_pair] = (rate, time.time())


def get_rate_display(rates, api_success):
    """ダッシュボード表示用のレート情報を返す"""
    info = {
        'usd_jpy': rates.get('USD_JPY', DEFAULT_RATES['USD_JPY']),
        'hkd_jpy': rates.get('HKD_JPY', DEFAULT_RATES['HKD_JPY']),
        'api_success': api_success,
    }
    return info
