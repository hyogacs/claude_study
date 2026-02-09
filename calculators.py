from models import NISAUsage
from exchange_rates import convert_to_jpy

# 貴金属に分類する銘柄コード
PRECIOUS_METAL_SYMBOLS = {'1540', '1541', '1542', '1693'}


def reclassify_asset_class(holding):
    """特殊銘柄の資産クラスを再分類する"""
    symbol = str(holding.symbol).strip()
    if symbol in PRECIOUS_METAL_SYMBOLS:
        return '貴金属'
    return holding.asset_class or '不明'


def compound_interest_simulation(
    initial_amount,
    monthly_contribution,
    annual_rate,
    years,
    tax_rate=0.20315,
    is_nisa=False
):
    """
    複利シミュレーション計算

    Args:
        initial_amount: 初期投資額（円）
        monthly_contribution: 毎月の積立額（円）
        annual_rate: 年利（例: 5.0 = 5%）
        years: 運用年数
        tax_rate: 税率（デフォルト: 20.315%）
        is_nisa: NISA口座かどうか（非課税）

    Returns:
        yearly_data: 年ごとのシミュレーション結果リスト
    """
    monthly_rate = (annual_rate / 100) / 12
    effective_tax = 0 if is_nisa else tax_rate

    yearly_data = []
    total_invested = initial_amount
    balance = initial_amount
    total_gain = 0

    for year in range(1, years + 1):
        yearly_contribution = 0
        yearly_interest = 0

        for month in range(12):
            interest = balance * monthly_rate
            after_tax_interest = interest * (1 - effective_tax)
            yearly_interest += after_tax_interest
            balance += after_tax_interest + monthly_contribution
            yearly_contribution += monthly_contribution

        total_invested += yearly_contribution
        total_gain = balance - total_invested

        yearly_data.append({
            'year': year,
            'balance': round(balance),
            'total_invested': round(total_invested),
            'total_gain': round(total_gain),
            'yearly_interest': round(yearly_interest),
            'yearly_contribution': round(yearly_contribution),
        })

    return yearly_data


def calculate_fire_number(annual_expenses, withdrawal_rate=4.0):
    """
    FIRE達成に必要な資産額を計算
    annual_expenses: 年間支出額
    withdrawal_rate: 引出率（デフォルト4%ルール）
    """
    return round(annual_expenses / (withdrawal_rate / 100))


def calculate_nisa_from_holdings(member, rates=None):
    """
    保有資産データからNISA利用状況を自動計算する（JPY換算対応）

    Returns:
        dict: NISA利用状況のサマリー
    """
    tsumitate_cost = 0  # つみたて投資枠の取得金額合計
    growth_cost = 0     # 成長投資枠の取得金額合計
    tsumitate_value = 0  # つみたて投資枠の時価合計
    growth_value = 0     # 成長投資枠の時価合計
    tsumitate_holdings = []
    growth_holdings = []

    for account in member.accounts:
        if account.account_type != 'NISA':
            continue

        # account_name から NISA種別を判定
        is_tsumitate = 'つみたて' in (account.account_name or '')
        is_growth = '成長' in (account.account_name or '')

        # account_name に種別がない場合、資産クラスで推定
        # 投資信託 → つみたて、株式 → 成長 がデフォルト
        for h in account.holdings:
            if rates:
                cost = convert_to_jpy(h.cost_basis, h.currency, rates)
                value = convert_to_jpy(h.market_value, h.currency, rates)
                pnl = convert_to_jpy(h.unrealized_pnl, h.currency, rates)
            else:
                cost = h.cost_basis
                value = h.market_value
                pnl = h.unrealized_pnl
            pnl_pct = h.pnl_percent

            holding_info = {
                'name': h.name,
                'symbol': h.symbol,
                'cost': cost,
                'value': value,
                'pnl': pnl,
                'pnl_percent': pnl_pct,
                'currency': h.currency,
            }

            if is_tsumitate:
                tsumitate_cost += cost
                tsumitate_value += value
                tsumitate_holdings.append(holding_info)
            elif is_growth:
                growth_cost += cost
                growth_value += value
                growth_holdings.append(holding_info)
            else:
                # 投資信託 → つみたて、それ以外 → 成長
                if h.asset_class == '投資信託':
                    tsumitate_cost += cost
                    tsumitate_value += value
                    tsumitate_holdings.append(holding_info)
                else:
                    growth_cost += cost
                    growth_value += value
                    growth_holdings.append(holding_info)

    total_cost = tsumitate_cost + growth_cost
    total_value = tsumitate_value + growth_value

    return {
        'tsumitate_cost': tsumitate_cost,
        'tsumitate_value': tsumitate_value,
        'growth_cost': growth_cost,
        'growth_value': growth_value,
        'total_cost': total_cost,
        'total_value': total_value,
        'tsumitate_holdings': tsumitate_holdings,
        'growth_holdings': growth_holdings,
        'lifetime_remaining': max(0, NISAUsage.TOTAL_LIFETIME_LIMIT - total_cost),
        'growth_lifetime_remaining': max(0, NISAUsage.GROWTH_LIFETIME_LIMIT - growth_cost),
        'tsumitate_annual_remaining': max(0, NISAUsage.TSUMITATE_ANNUAL_LIMIT - tsumitate_cost),
        'growth_annual_remaining': max(0, NISAUsage.GROWTH_ANNUAL_LIMIT - growth_cost),
    }


def calculate_asset_allocation(holdings, rates=None):
    """
    資産配分を計算（貴金属の再分類を含む、JPY換算対応）

    Returns:
        allocation: 資産クラスごとの配分データ
    """
    allocation = {}
    total_value = 0

    for h in holdings:
        if rates:
            value = convert_to_jpy(h.market_value, h.currency, rates)
            pnl = convert_to_jpy(h.unrealized_pnl, h.currency, rates)
        else:
            value = h.market_value
            pnl = h.unrealized_pnl
        total_value += value
        cls = reclassify_asset_class(h)
        if cls not in allocation:
            allocation[cls] = {'value': 0, 'count': 0, 'holdings': []}
        allocation[cls]['value'] += value
        allocation[cls]['count'] += 1
        allocation[cls]['holdings'].append({
            'name': h.name,
            'symbol': h.symbol,
            'value': value,
            'pnl': pnl,
            'pnl_percent': h.pnl_percent,
            'currency': h.currency,
        })

    for cls in allocation:
        allocation[cls]['percent'] = round(
            (allocation[cls]['value'] / total_value * 100) if total_value > 0 else 0, 2
        )

    return {
        'total_value': total_value,
        'allocation': allocation,
    }


def calculate_broker_allocation(holdings, rates=None):
    """証券会社ごとの資産配分を計算（JPY換算対応）"""
    by_broker = {}
    total = 0

    for h in holdings:
        broker = h.account.broker if h.account else '不明'
        if rates:
            value = convert_to_jpy(h.market_value, h.currency, rates)
        else:
            value = h.market_value
        total += value
        if broker not in by_broker:
            by_broker[broker] = 0
        by_broker[broker] += value

    result = {}
    for broker, value in by_broker.items():
        result[broker] = {
            'value': value,
            'percent': round((value / total * 100) if total > 0 else 0, 2),
        }

    return {'total_value': total, 'by_broker': result}
