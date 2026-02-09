from models import NISAUsage


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


def calculate_nisa_summary(member_id, nisa_usages):
    """
    NISAの利用状況サマリーを計算

    新NISA制度:
    - つみたて投資枠: 年間120万円
    - 成長投資枠: 年間240万円
    - 合計年間投資枠: 360万円
    - 非課税保有限度額（総枠）: 1,800万円
    - うち成長投資枠: 1,200万円まで
    """
    summary = {
        'member_id': member_id,
        'yearly': [],
        'total_tsumitate': 0,
        'total_growth': 0,
        'total_holding': 0,
        'lifetime_remaining': NISAUsage.TOTAL_LIFETIME_LIMIT,
        'growth_lifetime_remaining': NISAUsage.GROWTH_LIFETIME_LIMIT,
    }

    total_holding = 0
    total_growth_holding = 0

    for usage in nisa_usages:
        year_data = {
            'year': usage.year,
            'tsumitate_used': usage.tsumitate_used,
            'tsumitate_limit': NISAUsage.TSUMITATE_ANNUAL_LIMIT,
            'tsumitate_remaining': usage.tsumitate_remaining,
            'growth_used': usage.growth_used,
            'growth_limit': NISAUsage.GROWTH_ANNUAL_LIMIT,
            'growth_remaining': usage.growth_remaining,
            'annual_total': usage.annual_total_used,
        }
        summary['yearly'].append(year_data)
        total_holding += usage.tsumitate_used + usage.growth_used
        total_growth_holding += usage.growth_used

    summary['total_holding'] = total_holding
    summary['lifetime_remaining'] = max(0, NISAUsage.TOTAL_LIFETIME_LIMIT - total_holding)
    summary['growth_lifetime_remaining'] = max(0, NISAUsage.GROWTH_LIFETIME_LIMIT - total_growth_holding)

    return summary


def calculate_asset_allocation(holdings):
    """
    資産配分を計算

    Returns:
        allocation: 資産クラスごとの配分データ
    """
    allocation = {}
    total_value = 0

    for h in holdings:
        value = h.market_value
        total_value += value
        cls = h.asset_class or '不明'
        if cls not in allocation:
            allocation[cls] = {'value': 0, 'count': 0, 'holdings': []}
        allocation[cls]['value'] += value
        allocation[cls]['count'] += 1
        allocation[cls]['holdings'].append({
            'name': h.name,
            'symbol': h.symbol,
            'value': value,
            'pnl': h.unrealized_pnl,
            'pnl_percent': h.pnl_percent,
        })

    for cls in allocation:
        allocation[cls]['percent'] = round(
            (allocation[cls]['value'] / total_value * 100) if total_value > 0 else 0, 2
        )

    return {
        'total_value': total_value,
        'allocation': allocation,
    }


def calculate_broker_allocation(holdings):
    """証券会社ごとの資産配分を計算"""
    by_broker = {}
    total = 0

    for h in holdings:
        broker = h.account.broker if h.account else '不明'
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


def calculate_currency_allocation(holdings):
    """通貨ごとの資産配分を計算"""
    by_currency = {}
    total = 0

    for h in holdings:
        currency = h.currency or 'JPY'
        value = h.market_value
        total += value
        if currency not in by_currency:
            by_currency[currency] = 0
        by_currency[currency] += value

    result = {}
    for currency, value in by_currency.items():
        result[currency] = {
            'value': value,
            'percent': round((value / total * 100) if total > 0 else 0, 2),
        }

    return {'total_value': total, 'by_currency': result}
