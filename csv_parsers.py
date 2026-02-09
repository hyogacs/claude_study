import pandas as pd
import io
from datetime import datetime


def parse_sbi_holdings(file_content, encoding='shift_jis'):
    """
    SBI証券の保有証券CSVを解析する
    想定カラム: 銘柄コード, 銘柄名, 数量, 取得単価, 現在値, 評価額, 損益, 損益率(%), 口座区分
    """
    try:
        if isinstance(file_content, bytes):
            content = file_content.decode(encoding)
        else:
            content = file_content

        df = pd.read_csv(io.StringIO(content))
        df.columns = df.columns.str.strip()

        column_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if '銘柄コード' in col or 'コード' in col:
                column_map[col] = 'symbol'
            elif '銘柄' in col and 'コード' not in col:
                column_map[col] = 'name'
            elif '数量' in col or '保有数' in col or '口数' in col:
                column_map[col] = 'quantity'
            elif '取得' in col and ('単価' in col or '価格' in col):
                column_map[col] = 'avg_cost'
            elif '現在' in col or '時価' in col and '評価' not in col:
                column_map[col] = 'current_price'
            elif '評価額' in col or '時価総額' in col:
                column_map[col] = 'market_value'
            elif '損益' in col and '率' not in col:
                column_map[col] = 'pnl'
            elif '口座' in col or '区分' in col:
                column_map[col] = 'account_type'
            elif '種別' in col or '商品' in col:
                column_map[col] = 'asset_class'

        df = df.rename(columns=column_map)

        for col in ['quantity', 'avg_cost', 'current_price', 'market_value', 'pnl']:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(',', '').str.replace('円', '').str.strip(),
                    errors='coerce'
                ).fillna(0)

        if 'symbol' not in df.columns:
            df['symbol'] = ''
        if 'name' not in df.columns:
            df['name'] = 'Unknown'
        if 'quantity' not in df.columns:
            df['quantity'] = 0
        if 'avg_cost' not in df.columns:
            df['avg_cost'] = 0
        if 'current_price' not in df.columns:
            if 'market_value' in df.columns and df['quantity'].sum() > 0:
                df['current_price'] = df['market_value'] / df['quantity'].replace(0, 1)
            else:
                df['current_price'] = 0
        if 'asset_class' not in df.columns:
            df['asset_class'] = df['symbol'].apply(_guess_asset_class_sbi)
        if 'account_type' not in df.columns:
            df['account_type'] = '特定'

        df['is_nisa'] = df['account_type'].astype(str).str.contains('NISA|ニーサ', case=False, na=False)
        df['currency'] = 'JPY'
        df['broker'] = 'SBI'

        return df, None

    except Exception as e:
        return None, f"SBI CSVの解析エラー: {str(e)}"


def parse_moomoo_holdings(file_content, encoding='utf-8'):
    """
    moomoo証券の保有証券CSVを解析する
    想定カラム: Symbol, Name, Qty, Avg Cost, Market Price, Market Value, P&L, P&L%
    """
    try:
        if isinstance(file_content, bytes):
            content = file_content.decode(encoding)
        else:
            content = file_content

        df = pd.read_csv(io.StringIO(content))
        df.columns = df.columns.str.strip()

        column_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if col_lower in ('symbol', 'ticker', 'code', '銘柄コード', 'ティッカー'):
                column_map[col] = 'symbol'
            elif col_lower in ('name', 'stock name', '銘柄名', '名称'):
                column_map[col] = 'name'
            elif col_lower in ('qty', 'quantity', 'shares', '数量', '保有数量'):
                column_map[col] = 'quantity'
            elif 'avg' in col_lower and ('cost' in col_lower or 'price' in col_lower) or '取得' in col_lower:
                column_map[col] = 'avg_cost'
            elif ('market' in col_lower and 'price' in col_lower) or col_lower in ('price', 'last', '現在値'):
                column_map[col] = 'current_price'
            elif ('market' in col_lower and 'value' in col_lower) or col_lower in ('value', '評価額'):
                column_map[col] = 'market_value'
            elif col_lower in ('p&l', 'pnl', 'profit', '損益') and '%' not in col_lower and '率' not in col_lower:
                column_map[col] = 'pnl'
            elif col_lower in ('account', 'type', '口座区分'):
                column_map[col] = 'account_type'
            elif col_lower in ('currency', '通貨'):
                column_map[col] = 'currency'

        df = df.rename(columns=column_map)

        for col in ['quantity', 'avg_cost', 'current_price', 'market_value', 'pnl']:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(',', '').str.replace('$', '').str.replace('¥', '').str.strip(),
                    errors='coerce'
                ).fillna(0)

        if 'symbol' not in df.columns:
            df['symbol'] = ''
        if 'name' not in df.columns:
            df['name'] = 'Unknown'
        if 'quantity' not in df.columns:
            df['quantity'] = 0
        if 'avg_cost' not in df.columns:
            df['avg_cost'] = 0
        if 'current_price' not in df.columns:
            if 'market_value' in df.columns and df['quantity'].sum() > 0:
                df['current_price'] = df['market_value'] / df['quantity'].replace(0, 1)
            else:
                df['current_price'] = 0
        if 'currency' not in df.columns:
            df['currency'] = 'JPY'
        if 'asset_class' not in df.columns:
            df['asset_class'] = '外国株式'
        if 'account_type' not in df.columns:
            df['account_type'] = '特定'

        df['is_nisa'] = df['account_type'].astype(str).str.contains('NISA|ニーサ', case=False, na=False)
        df['broker'] = 'moomoo'

        return df, None

    except Exception as e:
        return None, f"moomoo CSVの解析エラー: {str(e)}"


def parse_sbi_transactions(file_content, encoding='shift_jis'):
    """SBI証券の取引履歴CSVを解析する"""
    try:
        if isinstance(file_content, bytes):
            content = file_content.decode(encoding)
        else:
            content = file_content

        df = pd.read_csv(io.StringIO(content))
        df.columns = df.columns.str.strip()

        column_map = {}
        for col in df.columns:
            if '約定日' in col or '取引日' in col:
                column_map[col] = 'trade_date'
            elif '銘柄コード' in col or 'コード' in col:
                column_map[col] = 'symbol'
            elif '銘柄' in col and 'コード' not in col:
                column_map[col] = 'name'
            elif '取引' in col and '種類' in col or '売買' in col:
                column_map[col] = 'type'
            elif '数量' in col or '株数' in col:
                column_map[col] = 'quantity'
            elif '単価' in col or '約定価格' in col:
                column_map[col] = 'price'
            elif '金額' in col or '受渡金額' in col:
                column_map[col] = 'amount'
            elif '手数料' in col:
                column_map[col] = 'fees'
            elif '口座' in col:
                column_map[col] = 'account_type'

        df = df.rename(columns=column_map)

        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')

        for col in ['quantity', 'price', 'amount', 'fees']:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(',', '').str.replace('円', '').str.strip(),
                    errors='coerce'
                ).fillna(0)

        if 'type' in df.columns:
            df['type'] = df['type'].apply(_normalize_transaction_type)
        if 'is_nisa' not in df.columns:
            if 'account_type' in df.columns:
                df['is_nisa'] = df['account_type'].astype(str).str.contains('NISA', case=False, na=False)
            else:
                df['is_nisa'] = False

        df['broker'] = 'SBI'
        return df, None

    except Exception as e:
        return None, f"SBI取引CSVの解析エラー: {str(e)}"


def _guess_asset_class_sbi(symbol):
    """銘柄コードから資産クラスを推定"""
    if not symbol or pd.isna(symbol):
        return '不明'
    symbol = str(symbol).strip()
    if symbol.isdigit():
        code = int(symbol)
        if 1000 <= code <= 9999:
            return '日本株式'
        elif code >= 10000:
            return '投資信託'
    if any(c.isalpha() for c in symbol):
        return '外国株式'
    return '不明'


def _normalize_transaction_type(t):
    """取引種別を正規化"""
    t = str(t).strip()
    if '買' in t:
        return '買付'
    elif '売' in t:
        return '売付'
    elif '配当' in t or '分配' in t:
        return '配当'
    return t
