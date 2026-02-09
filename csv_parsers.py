import pandas as pd
import io
import re
from datetime import datetime


def parse_sbi_holdings(file_content, encoding='shift_jis'):
    """
    SBI証券の保有証券一覧CSVを解析する。
    ファイルはセクション分けされており、各セクションが口座区分と資産クラスを表す。

    セクション例:
      株式（特定預り）, 株式（一般預り）, 株式（NISA預り（成長投資枠））,
      投資信託（金額/特定預り）, 投資信託（金額/NISA預り（つみたて投資枠））,
      国内債券（特定預り）
    """
    try:
        if isinstance(file_content, bytes):
            try:
                content = file_content.decode(encoding)
            except UnicodeDecodeError:
                content = file_content.decode('utf-8')
        else:
            content = file_content

        lines = content.splitlines()
        sections = _split_sbi_sections(lines)
        all_rows = []

        for section in sections:
            asset_class = section['asset_class']
            account_type = section['account_type']
            nisa_type = section['nisa_type']
            is_nisa = section['is_nisa']

            if asset_class == '株式':
                rows = _parse_sbi_stock_section(section['data_lines'])
            elif asset_class == '投資信託':
                rows = _parse_sbi_fund_section(section['data_lines'])
            elif asset_class == '国内債券':
                rows = _parse_sbi_bond_section(section['data_lines'])
            else:
                continue

            for row in rows:
                row['asset_class'] = asset_class
                row['account_type'] = account_type
                row['nisa_type'] = nisa_type
                row['is_nisa'] = is_nisa
                row['currency'] = 'JPY'
                row['broker'] = 'SBI'
                all_rows.append(row)

        if not all_rows:
            return None, "解析可能なデータが見つかりませんでした"

        df = pd.DataFrame(all_rows)
        return df, None

    except Exception as e:
        return None, f"SBI CSVの解析エラー: {str(e)}"


# ── セクション認識パターン ───────────────────────
# 「株式（特定預り）」「投資信託（金額/NISA預り（つみたて投資枠））」等にマッチ
_SECTION_HEADER_RE = re.compile(
    r'^(株式|投資信託|国内債券|外国株式|外国債券|ＭＲＦ)'
    r'（(.+?)）\s*$'
)
# 合計行を検出するパターン
_SUMMARY_RE = re.compile(r'合計\s*$')


def _split_sbi_sections(lines):
    """ファイルをセクションに分割する"""
    sections = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # 合計行はスキップ（例: 株式（特定預り）合計）
        if _SUMMARY_RE.search(line):
            i += 1
            # 合計のデータ行もスキップ
            while i < len(lines) and lines[i].strip() and not _SECTION_HEADER_RE.match(lines[i].strip()) and not _SUMMARY_RE.search(lines[i].strip()):
                i += 1
            continue

        match = _SECTION_HEADER_RE.match(line)
        if match:
            asset_class = match.group(1)
            detail = match.group(2)  # e.g. "特定預り", "NISA預り（成長投資枠）", "金額/NISA預り（つみたて投資枠）"

            account_type, nisa_type, is_nisa = _parse_account_detail(detail)

            i += 1  # move to header line
            # Collect data lines until next section or empty block
            data_lines = []
            if i < len(lines):
                header_line = lines[i].strip()
                data_lines.append(header_line)  # column headers
                i += 1

                while i < len(lines):
                    l = lines[i].strip()
                    # Stop at empty line, next section header, or summary line
                    if not l:
                        i += 1
                        break
                    if _SECTION_HEADER_RE.match(l) or _SUMMARY_RE.search(l):
                        break
                    data_lines.append(l)
                    i += 1

            if len(data_lines) > 1:  # header + at least one data row
                sections.append({
                    'asset_class': asset_class,
                    'account_type': account_type,
                    'nisa_type': nisa_type,
                    'is_nisa': is_nisa,
                    'data_lines': data_lines,
                })
        else:
            i += 1

    return sections


def _parse_account_detail(detail):
    """
    口座区分の詳細文字列を解析する

    Examples:
      "特定預り" -> ("特定", "", False)
      "一般預り" -> ("一般", "", False)
      "NISA預り（成長投資枠）" -> ("NISA", "成長投資枠", True)
      "金額/特定預り" -> ("特定", "", False)
      "金額/NISA預り（つみたて投資枠）" -> ("NISA", "つみたて投資枠", True)
    """
    # Remove "金額/" prefix if present
    detail = re.sub(r'^金額/', '', detail)

    is_nisa = 'NISA' in detail
    nisa_type = ''

    if is_nisa:
        account_type = 'NISA'
        nisa_match = re.search(r'（(.+?)）', detail)
        if nisa_match:
            nisa_type = nisa_match.group(1)
    elif '一般' in detail:
        account_type = '一般'
    else:
        account_type = '特定'

    return account_type, nisa_type, is_nisa


def _clean_numeric(val):
    """数値文字列をfloatに変換"""
    if pd.isna(val) or val == '' or val == '--':
        return 0.0
    s = str(val).strip().replace(',', '').replace('"', '').replace('円', '').replace('口', '')
    s = s.replace('+', '').replace('　', '')
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_sbi_stock_section(data_lines):
    """
    株式セクションを解析する
    カラム: 銘柄コード, 銘柄名称, 保有株数, 売却注文中, 取得単価/参考単価, 現在値,
            取得金額/参考金額, 評価額, 評価損益
    """
    rows = []
    csv_text = '\n'.join(data_lines)
    try:
        df = pd.read_csv(io.StringIO(csv_text), dtype=str)
        df.columns = df.columns.str.strip().str.replace('"', '')

        for _, r in df.iterrows():
            cols = {c.strip().replace('"', ''): str(v).strip().replace('"', '') for c, v in r.items()}

            symbol = cols.get('銘柄コード', '')
            name = cols.get('銘柄名称', '')
            quantity = _clean_numeric(cols.get('保有株数', 0))
            # 取得単価 or 参考単価
            avg_cost = _clean_numeric(cols.get('取得単価', cols.get('参考単価', 0)))
            current_price = _clean_numeric(cols.get('現在値', 0))
            # 取得金額 or 参考金額
            cost_total = _clean_numeric(cols.get('取得金額', cols.get('参考金額', 0)))
            market_value = _clean_numeric(cols.get('評価額', 0))

            if quantity > 0:
                rows.append({
                    'symbol': symbol,
                    'name': name,
                    'quantity': quantity,
                    'avg_cost': avg_cost,
                    'current_price': current_price,
                })
    except Exception:
        pass

    return rows


def _parse_sbi_fund_section(data_lines):
    """
    投資信託セクションを解析する
    カラム: ファンド名, 保有口数, 売却注文中, 取得単価, 基準価額, 取得金額, 評価額, 評価損益, 分配金受取方法
    """
    rows = []
    csv_text = '\n'.join(data_lines)
    try:
        df = pd.read_csv(io.StringIO(csv_text), dtype=str)
        df.columns = df.columns.str.strip().str.replace('"', '')

        for _, r in df.iterrows():
            cols = {c.strip().replace('"', ''): str(v).strip().replace('"', '') for c, v in r.items()}

            name = cols.get('ファンド名', '')
            # 保有口数: "30704口" -> 30704
            quantity_str = cols.get('保有口数', '0')
            quantity = _clean_numeric(quantity_str)
            avg_cost_raw = _clean_numeric(cols.get('取得単価', 0))
            current_price_raw = _clean_numeric(cols.get('基準価額', 0))

            # 基準価額は1万口あたりの価格なので、1口あたりに変換
            # quantity * (price / 10000) = market_value
            if quantity > 0:
                avg_cost = avg_cost_raw / 10000.0
                current_price = current_price_raw / 10000.0
                rows.append({
                    'symbol': '',
                    'name': name,
                    'quantity': quantity,
                    'avg_cost': avg_cost,
                    'current_price': current_price,
                })
    except Exception:
        pass

    return rows


def _parse_sbi_bond_section(data_lines):
    """
    国内債券セクションを解析する
    カラム: 銘柄, 利率(%), 償還日, 利払日, 保有額面, 取得単価, 約定為替, 参考為替, 評価額
    """
    rows = []
    csv_text = '\n'.join(data_lines)
    try:
        df = pd.read_csv(io.StringIO(csv_text), dtype=str)
        df.columns = df.columns.str.strip().str.replace('"', '')

        for _, r in df.iterrows():
            cols = {c.strip().replace('"', ''): str(v).strip().replace('"', '') for c, v in r.items()}

            name = cols.get('銘柄', '')
            face_value = _clean_numeric(cols.get('保有額面', 0))
            avg_cost_pct = _clean_numeric(cols.get('取得単価', 0))
            market_value = _clean_numeric(cols.get('評価額', 0))

            # 債券: 額面=1、単価は額面に対するパーセント
            # quantity=1, avg_cost=取得金額, current_price=評価額
            if face_value > 0:
                cost_amount = face_value * avg_cost_pct / 100.0
                rows.append({
                    'symbol': '',
                    'name': name,
                    'quantity': 1,
                    'avg_cost': cost_amount,
                    'current_price': market_value,
                })
    except Exception:
        pass

    return rows


# ── moomoo解析 ────────────────────────────────

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
        df['nisa_type'] = ''
        df['broker'] = 'moomoo'

        return df, None

    except Exception as e:
        return None, f"moomoo CSVの解析エラー: {str(e)}"


# ── 取引履歴解析 ──────────────────────────────

def parse_sbi_transactions(file_content, encoding='shift_jis'):
    """SBI証券の取引履歴CSVを解析する"""
    try:
        if isinstance(file_content, bytes):
            try:
                content = file_content.decode(encoding)
            except UnicodeDecodeError:
                content = file_content.decode('utf-8')
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
