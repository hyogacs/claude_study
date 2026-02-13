import os
import io
import csv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from models import (
    db, Family, FamilyMember, Account, Holding, Transaction,
    NISAUsage, PortfolioSnapshot, Dividend, DividendSchedule, RealizedGain, Goal,
)
from csv_parsers import parse_sbi_holdings, parse_moomoo_holdings, parse_sbi_transactions
from calculators import (
    compound_interest_simulation,
    calculate_fire_number,
    calculate_nisa_from_holdings,
    calculate_asset_allocation,
    calculate_broker_allocation,
)
from datetime import datetime, date
from exchange_rates import get_exchange_rates, convert_to_jpy, get_rate_display
from stock_api import fetch_stock_price, fetch_stock_prices_batch, fetch_dividend_info, get_dividend_schedule

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///investment.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

db.init_app(app)

with app.app_context():
    db.create_all()


def format_currency(value, currency='JPY'):
    """通貨フォーマット用フィルタ"""
    if currency == 'JPY':
        return f"¥{value:,.0f}"
    elif currency == 'USD':
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}"


app.jinja_env.filters['currency'] = format_currency
app.jinja_env.globals['convert_to_jpy'] = convert_to_jpy


def _get_family_holdings(family):
    """家族の全保有資産を取得"""
    all_holdings = []
    for member in family.members:
        for account in member.accounts:
            all_holdings.extend(account.holdings)
    return all_holdings


def _take_snapshot(family_id, rates=None):
    """ポートフォリオスナップショットを保存"""
    family = Family.query.get(family_id)
    if not family:
        return
    if not rates:
        rates, _ = get_exchange_rates()

    holdings = _get_family_holdings(family)
    total_value = sum(convert_to_jpy(h.market_value, h.currency, rates) for h in holdings)
    total_cost = sum(convert_to_jpy(h.cost_basis, h.currency, rates) for h in holdings)

    today = date.today()
    # 同日のスナップショットがあれば更新
    snap = PortfolioSnapshot.query.filter_by(
        family_id=family_id, snapshot_date=today
    ).first()
    if snap:
        snap.total_value = total_value
        snap.total_cost = total_cost
        snap.total_pnl = total_value - total_cost
    else:
        snap = PortfolioSnapshot(
            family_id=family_id, snapshot_date=today,
            total_value=total_value, total_cost=total_cost,
            total_pnl=total_value - total_cost,
        )
        db.session.add(snap)
    db.session.commit()


# ── ダッシュボード ─────────────────────────────
@app.route('/')
def dashboard():
    families = Family.query.all()
    family_id = request.args.get('family_id', type=int)

    rates, api_success = get_exchange_rates()
    rate_info = get_rate_display(rates, api_success)

    if not families:
        return render_template('dashboard.html', families=families, selected_family=None,
                               summary=None, rate_info=rate_info, goals=[])

    selected_family = None
    if family_id:
        selected_family = Family.query.get(family_id)
    if not selected_family:
        selected_family = families[0]

    all_holdings = _get_family_holdings(selected_family)
    member_summaries = []
    for member in selected_family.members:
        member_holdings = []
        for account in member.accounts:
            member_holdings.extend(account.holdings)
        total = sum(convert_to_jpy(h.market_value, h.currency, rates) for h in member_holdings)
        pnl = sum(convert_to_jpy(h.unrealized_pnl, h.currency, rates) for h in member_holdings)
        member_summaries.append({
            'member': member,
            'total_value': total,
            'total_pnl': pnl,
            'holding_count': len(member_holdings),
        })

    asset_alloc = calculate_asset_allocation(all_holdings, rates) if all_holdings else None
    broker_alloc = calculate_broker_allocation(all_holdings, rates) if all_holdings else None

    summary = {
        'total_value': sum(convert_to_jpy(h.market_value, h.currency, rates) for h in all_holdings),
        'total_pnl': sum(convert_to_jpy(h.unrealized_pnl, h.currency, rates) for h in all_holdings),
        'total_cost': sum(convert_to_jpy(h.cost_basis, h.currency, rates) for h in all_holdings),
        'holding_count': len(all_holdings),
        'member_summaries': member_summaries,
        'asset_allocation': asset_alloc,
        'broker_allocation': broker_alloc,
    }

    goals = Goal.query.filter_by(family_id=selected_family.id).all()
    goals_data = []
    for g in goals:
        pct = (summary['total_value'] / g.target_amount * 100) if g.target_amount > 0 else 0
        goals_data.append({
            'goal': g,
            'current': summary['total_value'],
            'percent': round(min(pct, 100), 1),
            'remaining': max(0, g.target_amount - summary['total_value']),
        })

    return render_template('dashboard.html', families=families,
                           selected_family=selected_family, summary=summary,
                           rate_info=rate_info, goals=goals_data)


# ── 家族管理 ──────────────────────────────────
@app.route('/family')
def family_list():
    families = Family.query.all()
    return render_template('family.html', families=families)


@app.route('/family/create', methods=['POST'])
def family_create():
    name = request.form.get('family_name', '').strip()
    if not name:
        flash('家族名を入力してください', 'error')
        return redirect(url_for('family_list'))

    family = Family(name=name)
    db.session.add(family)
    db.session.commit()
    flash(f'家族「{name}」を作成しました', 'success')
    return redirect(url_for('family_list'))


@app.route('/family/<int:family_id>/delete', methods=['POST'])
def family_delete(family_id):
    family = Family.query.get_or_404(family_id)
    db.session.delete(family)
    db.session.commit()
    flash(f'家族「{family.name}」を削除しました', 'success')
    return redirect(url_for('family_list'))


@app.route('/family/<int:family_id>/member/add', methods=['POST'])
def member_add(family_id):
    family = Family.query.get_or_404(family_id)
    name = request.form.get('member_name', '').strip()
    relationship = request.form.get('relationship', '').strip()
    if not name or not relationship:
        flash('名前と続柄を入力してください', 'error')
        return redirect(url_for('family_list'))

    member = FamilyMember(family_id=family.id, name=name, relationship=relationship)
    db.session.add(member)
    db.session.commit()
    flash(f'メンバー「{name}」を追加しました', 'success')
    return redirect(url_for('family_list'))


@app.route('/member/<int:member_id>/delete', methods=['POST'])
def member_delete(member_id):
    member = FamilyMember.query.get_or_404(member_id)
    db.session.delete(member)
    db.session.commit()
    flash(f'メンバー「{member.name}」を削除しました', 'success')
    return redirect(url_for('family_list'))


# ── CSV導入 ───────────────────────────────────
@app.route('/import')
def import_page():
    members = FamilyMember.query.all()
    return render_template('import.html', members=members)


@app.route('/import/upload', methods=['POST'])
def import_upload():
    member_id = request.form.get('member_id', type=int)
    broker = request.form.get('broker', '')
    file_type = request.form.get('file_type', 'holdings')
    file = request.files.get('csv_file')

    if not file or not file.filename:
        flash('CSVファイルを選択してください', 'error')
        return redirect(url_for('import_page'))

    if not member_id:
        flash('メンバーを選択してください', 'error')
        return redirect(url_for('import_page'))

    member = FamilyMember.query.get_or_404(member_id)
    content = file.read()

    if file_type == 'holdings':
        return _import_holdings(member, broker, content)
    else:
        return _import_transactions(member, broker, content)


@app.route('/api/import/preview', methods=['POST'])
def api_import_preview():
    """CSVを解析してプレビュー用JSONを返す"""
    member_id = request.form.get('member_id', type=int)
    broker = request.form.get('broker', '')
    file_type = request.form.get('file_type', 'holdings')
    file = request.files.get('csv_file')

    if not file or not file.filename or not member_id:
        return jsonify({'error': '必須項目が不足しています'}), 400

    content = file.read()

    if file_type == 'holdings':
        if broker == 'SBI':
            df, error = parse_sbi_holdings(content)
        elif broker == 'moomoo':
            df, error = parse_moomoo_holdings(content)
        else:
            return jsonify({'error': '不明な証券会社です'}), 400
    else:
        if broker == 'SBI':
            df, error = parse_sbi_transactions(content)
        else:
            return jsonify({'error': '取引履歴のインポートは現在SBIのみ対応しています'}), 400

    if error:
        return jsonify({'error': error}), 400

    # Convert DataFrame to preview data
    rows = []
    for _, row in df.iterrows():
        r = {}
        for col in df.columns:
            val = row[col]
            if hasattr(val, 'isoformat'):
                r[col] = val.isoformat()
            else:
                r[col] = str(val) if not isinstance(val, (int, float, bool)) else val
        rows.append(r)

    return jsonify({
        'count': len(rows),
        'broker': broker,
        'file_type': file_type,
        'rows': rows,
    })


def _get_or_create_account(member, broker, account_type, nisa_type='', sub_type=''):
    """口座を取得、なければ作成する"""
    name_parts = [broker, account_type]
    if nisa_type:
        name_parts.append(nisa_type)
    if sub_type:
        name_parts.append(sub_type)
    account_name = ' '.join(name_parts) + '口座'

    account = Account.query.filter_by(
        member_id=member.id, broker=broker, account_type=account_type,
        account_name=account_name
    ).first()
    if not account:
        account = Account(
            member_id=member.id, broker=broker,
            account_type=account_type,
            account_name=account_name
        )
        db.session.add(account)
        db.session.flush()
    return account


def _import_holdings(member, broker, content):
    if broker == 'SBI':
        df, error = parse_sbi_holdings(content)
    elif broker == 'moomoo':
        df, error = parse_moomoo_holdings(content)
    else:
        flash('不明な証券会社です', 'error')
        return redirect(url_for('import_page'))

    if error:
        flash(error, 'error')
        return redirect(url_for('import_page'))

    account_cache = {}
    for _, row in df.iterrows():
        account_type = str(row.get('account_type', '特定'))
        nisa_type = str(row.get('nisa_type', ''))
        sub_type = str(row.get('sub_type', ''))
        cache_key = (broker, account_type, nisa_type, sub_type)
        if cache_key not in account_cache:
            account_cache[cache_key] = _get_or_create_account(
                member, broker, account_type, nisa_type, sub_type
            )

    if broker == 'SBI':
        existing_accounts = Account.query.filter_by(
            member_id=member.id, broker=broker
        ).all()
        for acc in existing_accounts:
            Holding.query.filter_by(account_id=acc.id).delete()
    else:
        for acc in account_cache.values():
            Holding.query.filter_by(account_id=acc.id).delete()

    count = 0
    for _, row in df.iterrows():
        account_type = str(row.get('account_type', '特定'))
        nisa_type = str(row.get('nisa_type', ''))
        sub_type = str(row.get('sub_type', ''))
        cache_key = (broker, account_type, nisa_type, sub_type)
        account = account_cache[cache_key]
        holding = Holding(
            account_id=account.id,
            symbol=str(row.get('symbol', '')),
            name=str(row.get('name', 'Unknown')),
            asset_class=str(row.get('asset_class', '不明')),
            quantity=float(row.get('quantity', 0)),
            avg_cost=float(row.get('avg_cost', 0)),
            current_price=float(row.get('current_price', 0)),
            currency=str(row.get('currency', 'JPY')),
            is_nisa=bool(row.get('is_nisa', False)),
        )
        db.session.add(holding)
        count += 1

    db.session.commit()

    # インポート後にスナップショットを保存
    _take_snapshot(member.family_id)

    details = []
    for key, acc in account_cache.items():
        n = Holding.query.filter_by(account_id=acc.id).count()
        _, at, nt, st = key
        label = at + (f'({nt})' if nt else '') + (f' {st}' if st else '')
        details.append(f'{label}: {n}件')
    detail_str = '、'.join(details)

    flash(f'{broker}から{count}件の保有資産をインポートしました（{detail_str}）', 'success')
    return redirect(url_for('import_page'))


def _import_transactions(member, broker, content):
    if broker == 'SBI':
        df, error = parse_sbi_transactions(content)
    else:
        flash('取引履歴のインポートは現在SBIのみ対応しています', 'error')
        return redirect(url_for('import_page'))

    if error:
        flash(error, 'error')
        return redirect(url_for('import_page'))

    account = Account.query.filter_by(
        member_id=member.id, broker=broker, account_type='特定'
    ).first()
    if not account:
        account = Account(
            member_id=member.id, broker=broker,
            account_type='特定',
            account_name=f"{broker} 特定口座"
        )
        db.session.add(account)
        db.session.flush()

    count = 0
    for _, row in df.iterrows():
        tx = Transaction(
            account_id=account.id,
            trade_date=row.get('trade_date', datetime.now().date()),
            type=str(row.get('type', '不明')),
            symbol=str(row.get('symbol', '')),
            name=str(row.get('name', '')),
            quantity=float(row.get('quantity', 0)),
            price=float(row.get('price', 0)),
            amount=float(row.get('amount', 0)),
            fees=float(row.get('fees', 0)),
            is_nisa=bool(row.get('is_nisa', False)),
        )
        db.session.add(tx)
        count += 1

    db.session.commit()
    flash(f'{broker}から{count}件の取引をインポートしました', 'success')
    return redirect(url_for('import_page'))


# ── 資産一覧 ──────────────────────────────────
@app.route('/assets')
def assets():
    family_id = request.args.get('family_id', type=int)
    member_id = request.args.get('member_id', type=int)

    query = Holding.query.join(Account).join(FamilyMember)

    if member_id:
        query = query.filter(FamilyMember.id == member_id)
    elif family_id:
        query = query.filter(FamilyMember.family_id == family_id)

    holdings = query.all()
    families = Family.query.all()
    members = FamilyMember.query.all()
    accounts = Account.query.all()

    rates, _ = get_exchange_rates()

    return render_template('assets.html', holdings=holdings, families=families,
                           members=members, accounts=accounts,
                           selected_family_id=family_id,
                           selected_member_id=member_id, rates=rates)


# ── 保有資産の編集・削除 ─────────────────────────
@app.route('/holding/<int:holding_id>')
def holding_detail(holding_id):
    holding = Holding.query.get_or_404(holding_id)
    rates, _ = get_exchange_rates()

    # 同じ銘柄の取引履歴
    transactions = Transaction.query.filter_by(
        account_id=holding.account_id, symbol=holding.symbol
    ).order_by(Transaction.trade_date.desc()).all()

    # 同じ銘柄の配当金
    dividends = Dividend.query.filter_by(
        account_id=holding.account_id, symbol=holding.symbol
    ).order_by(Dividend.payment_date.desc()).all()

    return render_template('holding_detail.html', holding=holding,
                           transactions=transactions, dividends=dividends, rates=rates)


@app.route('/holding/<int:holding_id>/edit', methods=['POST'])
def holding_edit(holding_id):
    holding = Holding.query.get_or_404(holding_id)
    holding.name = request.form.get('name', holding.name).strip()
    holding.symbol = request.form.get('symbol', holding.symbol).strip()
    holding.asset_class = request.form.get('asset_class', holding.asset_class).strip()
    holding.quantity = float(request.form.get('quantity', holding.quantity))
    holding.avg_cost = float(request.form.get('avg_cost', holding.avg_cost))
    holding.current_price = float(request.form.get('current_price', holding.current_price))
    holding.currency = request.form.get('currency', holding.currency).strip()
    db.session.commit()
    flash(f'「{holding.name}」を更新しました', 'success')
    return redirect(request.referrer or url_for('assets'))


@app.route('/holding/<int:holding_id>/delete', methods=['POST'])
def holding_delete(holding_id):
    holding = Holding.query.get_or_404(holding_id)
    name = holding.name
    db.session.delete(holding)
    db.session.commit()
    flash(f'「{name}」を削除しました', 'success')
    return redirect(url_for('assets'))


@app.route('/holding/add', methods=['POST'])
def holding_add():
    account_id = request.form.get('account_id', type=int)
    if not account_id:
        flash('口座を選択してください', 'error')
        return redirect(url_for('assets'))

    holding = Holding(
        account_id=account_id,
        symbol=request.form.get('symbol', '').strip(),
        name=request.form.get('name', '').strip(),
        asset_class=request.form.get('asset_class', '不明').strip(),
        quantity=float(request.form.get('quantity', 0)),
        avg_cost=float(request.form.get('avg_cost', 0)),
        current_price=float(request.form.get('current_price', 0)),
        currency=request.form.get('currency', 'JPY').strip(),
        is_nisa=bool(request.form.get('is_nisa')),
    )
    db.session.add(holding)
    db.session.commit()
    flash(f'「{holding.name}」を追加しました', 'success')
    return redirect(url_for('assets'))


# ── 複利シミュレーション ─────────────────────────
@app.route('/simulation')
def simulation():
    families = Family.query.all()
    total_value = 0
    if families:
        rates, _ = get_exchange_rates()
        family = families[0]
        all_holdings = _get_family_holdings(family)
        total_value = sum(convert_to_jpy(h.market_value, h.currency, rates) for h in all_holdings)
    return render_template('simulation.html', current_portfolio_value=round(total_value))


@app.route('/api/simulation', methods=['POST'])
def api_simulation():
    data = request.get_json()
    initial = float(data.get('initial_amount', 0))
    monthly = float(data.get('monthly_contribution', 0))
    rate = float(data.get('annual_rate', 5.0))
    years = int(data.get('years', 20))
    is_nisa = bool(data.get('is_nisa', False))

    result = compound_interest_simulation(initial, monthly, rate, years, is_nisa=is_nisa)
    taxed = compound_interest_simulation(initial, monthly, rate, years, is_nisa=False)
    nisa = compound_interest_simulation(initial, monthly, rate, years, is_nisa=True)

    return jsonify({
        'simulation': result,
        'comparison': {'taxed': taxed, 'nisa': nisa},
        'fire_number_4pct': calculate_fire_number(monthly * 12 * 1.5),
    })


@app.route('/api/portfolio_value')
def api_portfolio_value():
    """現在のポートフォリオ総額を返す（JPY換算）"""
    families = Family.query.all()
    total = 0
    if families:
        rates, _ = get_exchange_rates()
        for family in families:
            holdings = _get_family_holdings(family)
            total += sum(convert_to_jpy(h.market_value, h.currency, rates) for h in holdings)
    return jsonify({'total_value': round(total)})


# ── NISA管理 ──────────────────────────────────
@app.route('/nisa')
def nisa():
    families = Family.query.all()
    family_id = request.args.get('family_id', type=int)
    year = request.args.get('year', type=int, default=date.today().year)

    if not families:
        return render_template('nisa.html', families=families, selected_family=None,
                               nisa_data=[], current_year=year)

    selected_family = None
    if family_id:
        selected_family = Family.query.get(family_id)
    if not selected_family and families:
        selected_family = families[0]

    nisa_data = []
    if selected_family:
        rates, _ = get_exchange_rates()
        for member in selected_family.members:
            summary = calculate_nisa_from_holdings(member, rates)
            # 年度別NISAUsage取得
            usage = NISAUsage.query.filter_by(member_id=member.id, year=year).first()
            nisa_data.append({
                'member': member,
                'summary': summary,
                'yearly_usage': usage,
            })

    return render_template('nisa.html', families=families,
                           selected_family=selected_family, nisa_data=nisa_data,
                           current_year=year)


@app.route('/nisa/update_year', methods=['POST'])
def nisa_update_year():
    member_id = request.form.get('member_id', type=int)
    year = request.form.get('year', type=int)
    tsumitate = float(request.form.get('tsumitate_used', 0))
    growth = float(request.form.get('growth_used', 0))

    usage = NISAUsage.query.filter_by(member_id=member_id, year=year).first()
    if usage:
        usage.tsumitate_used = tsumitate
        usage.growth_used = growth
        usage.total_holding = tsumitate + growth
    else:
        usage = NISAUsage(
            member_id=member_id, year=year,
            tsumitate_used=tsumitate, growth_used=growth,
            total_holding=tsumitate + growth,
        )
        db.session.add(usage)
    db.session.commit()
    flash(f'{year}年のNISA利用額を更新しました', 'success')
    return redirect(url_for('nisa', year=year))


# ── 配当金管理 ────────────────────────────────
@app.route('/dividends')
def dividends():
    family_id = request.args.get('family_id', type=int)
    year = request.args.get('year', type=int, default=date.today().year)

    query = Dividend.query.join(Account).join(FamilyMember)
    if family_id:
        query = query.filter(FamilyMember.family_id == family_id)

    all_dividends = query.order_by(Dividend.payment_date.desc()).all()
    year_dividends = [d for d in all_dividends
                      if d.payment_date and d.payment_date.year == year]

    rates, _ = get_exchange_rates()
    total_amount = sum(convert_to_jpy(d.amount, d.currency, rates) for d in year_dividends)
    total_tax = sum(convert_to_jpy(d.tax_amount, d.currency, rates) for d in year_dividends)
    total_net = total_amount - total_tax

    # 総資産評価額（配当利回り計算用）
    total_portfolio_value = 0
    if family_id:
        fam = Family.query.get(family_id)
        if fam:
            holdings = _get_family_holdings(fam)
            total_portfolio_value = sum(convert_to_jpy(h.market_value, h.currency, rates) for h in holdings)
    else:
        for fam in Family.query.all():
            holdings = _get_family_holdings(fam)
            total_portfolio_value += sum(convert_to_jpy(h.market_value, h.currency, rates) for h in holdings)

    dividend_yield = (total_amount / total_portfolio_value * 100) if total_portfolio_value > 0 else 0
    monthly_avg = total_amount / 12 if total_amount > 0 else 0

    # 月別配当データ
    monthly_data = {m: {'amount': 0, 'tax': 0, 'entries': []} for m in range(1, 13)}
    for d in year_dividends:
        m = d.payment_date.month
        jpy_amount = convert_to_jpy(d.amount, d.currency, rates)
        monthly_data[m]['amount'] += jpy_amount
        monthly_data[m]['tax'] += convert_to_jpy(d.tax_amount, d.currency, rates)
        monthly_data[m]['entries'].append({
            'symbol': d.symbol, 'name': d.name,
            'amount': jpy_amount,
        })

    # 銘柄別配当集計（ドーナツチャート用）
    by_stock = {}
    for d in year_dividends:
        key = d.symbol or d.name
        jpy_amount = convert_to_jpy(d.amount, d.currency, rates)
        if key not in by_stock:
            by_stock[key] = {'name': d.name, 'symbol': d.symbol, 'amount': 0}
        by_stock[key]['amount'] += jpy_amount
    by_stock_sorted = sorted(by_stock.values(), key=lambda x: x['amount'], reverse=True)

    # 予想配当データ（保有銘柄のスケジュールから計算）
    schedules = DividendSchedule.query.all()
    schedule_map = {s.symbol: s for s in schedules}

    # 年度リスト
    years_set = set()
    for d in all_dividends:
        if d.payment_date:
            years_set.add(d.payment_date.year)
    years_set.add(date.today().year)
    available_years = sorted(years_set, reverse=True)

    families = Family.query.all()
    accounts = Account.query.all()

    return render_template('dividends.html', dividends=year_dividends,
                           families=families, accounts=accounts,
                           selected_family_id=family_id, current_year=year,
                           available_years=available_years,
                           total_amount=total_amount, total_tax=total_tax,
                           total_net=total_net, rates=rates,
                           total_portfolio_value=total_portfolio_value,
                           dividend_yield=dividend_yield,
                           monthly_avg=monthly_avg,
                           monthly_data=monthly_data,
                           by_stock=by_stock_sorted,
                           schedule_map=schedule_map)


@app.route('/dividend/add', methods=['POST'])
def dividend_add():
    d = Dividend(
        account_id=int(request.form['account_id']),
        symbol=request.form.get('symbol', '').strip(),
        name=request.form.get('name', '').strip(),
        amount=float(request.form.get('amount', 0)),
        tax_amount=float(request.form.get('tax_amount', 0)),
        currency=request.form.get('currency', 'JPY').strip(),
        payment_date=datetime.strptime(request.form['payment_date'], '%Y-%m-%d').date(),
        is_nisa=bool(request.form.get('is_nisa')),
    )
    db.session.add(d)
    db.session.commit()
    flash(f'配当金「{d.name}」を追加しました', 'success')
    return redirect(url_for('dividends'))


@app.route('/api/dividend_schedule/save', methods=['POST'])
def api_dividend_schedule_save():
    """銘柄の配当スケジュールを保存する（手動 or API結果）"""
    data = request.get_json() or {}
    symbol = data.get('symbol', '').strip()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400

    sched = DividendSchedule.query.filter_by(symbol=symbol).first()
    if not sched:
        sched = DividendSchedule(symbol=symbol)
        db.session.add(sched)

    sched.name = data.get('name', sched.name or '')
    if data.get('annual_dividend') is not None:
        sched.annual_dividend = float(data['annual_dividend'])
    if data.get('dividend_yield') is not None:
        sched.dividend_yield = float(data['dividend_yield'])
    if data.get('payment_months'):
        sched.set_months_list(data['payment_months'])
    if data.get('frequency'):
        sched.frequency = data['frequency']
    if data.get('currency'):
        sched.currency = data['currency']

    db.session.commit()
    return jsonify({'success': True, 'id': sched.id})


@app.route('/api/dividend_calendar')
def api_dividend_calendar():
    """保有銘柄の予想配当カレンダーデータを返す"""
    family_id = request.args.get('family_id', type=int)

    if family_id:
        family = Family.query.get(family_id)
        holdings = _get_family_holdings(family) if family else []
    else:
        holdings = Holding.query.all()

    rates, _ = get_exchange_rates()
    schedules = {s.symbol: s for s in DividendSchedule.query.all()}

    # 月別の予想配当を計算
    monthly = {m: [] for m in range(1, 13)}
    total_annual = 0

    for h in holdings:
        if not h.symbol:
            continue
        sched = schedules.get(h.symbol)
        if not sched or not sched.annual_dividend:
            continue

        months = sched.get_months_list()
        if not months:
            continue

        per_payment = sched.annual_dividend * h.quantity / len(months)
        jpy_per_payment = convert_to_jpy(per_payment, sched.currency or h.currency, rates)
        total_annual += convert_to_jpy(sched.annual_dividend * h.quantity, sched.currency or h.currency, rates)

        for m in months:
            monthly[m].append({
                'symbol': h.symbol,
                'name': h.name,
                'amount': round(jpy_per_payment),
                'yield': sched.dividend_yield,
            })

    return jsonify({
        'monthly': {str(m): items for m, items in monthly.items()},
        'monthly_totals': {str(m): sum(i['amount'] for i in items) for m, items in monthly.items()},
        'total_annual': round(total_annual),
    })


@app.route('/dividend/<int:dividend_id>/delete', methods=['POST'])
def dividend_delete(dividend_id):
    d = Dividend.query.get_or_404(dividend_id)
    db.session.delete(d)
    db.session.commit()
    flash('配当金を削除しました', 'success')
    return redirect(url_for('dividends'))


# ── 実現損益管理 ──────────────────────────────
@app.route('/realized-gains')
def realized_gains():
    family_id = request.args.get('family_id', type=int)
    year = request.args.get('year', type=int, default=date.today().year)

    query = RealizedGain.query.join(Account).join(FamilyMember)
    if family_id:
        query = query.filter(FamilyMember.family_id == family_id)

    all_gains = query.order_by(RealizedGain.trade_date.desc()).all()
    year_gains = [g for g in all_gains
                  if g.trade_date and g.trade_date.year == year]

    rates, _ = get_exchange_rates()
    total_pnl = sum(convert_to_jpy(g.realized_pnl, g.currency, rates) for g in year_gains)
    total_proceeds = sum(convert_to_jpy(g.proceeds, g.currency, rates) for g in year_gains)
    total_cost = sum(convert_to_jpy(g.cost_basis, g.currency, rates) for g in year_gains)

    families = Family.query.all()
    accounts = Account.query.all()

    years_set = set()
    for g in all_gains:
        if g.trade_date:
            years_set.add(g.trade_date.year)
    years_set.add(date.today().year)
    available_years = sorted(years_set, reverse=True)

    return render_template('realized_gains.html', gains=year_gains,
                           families=families, accounts=accounts,
                           selected_family_id=family_id, current_year=year,
                           available_years=available_years,
                           total_pnl=total_pnl, total_proceeds=total_proceeds,
                           total_cost=total_cost, rates=rates)


@app.route('/realized-gain/add', methods=['POST'])
def realized_gain_add():
    g = RealizedGain(
        account_id=int(request.form['account_id']),
        symbol=request.form.get('symbol', '').strip(),
        name=request.form.get('name', '').strip(),
        quantity=float(request.form.get('quantity', 0)),
        buy_price=float(request.form.get('buy_price', 0)),
        sell_price=float(request.form.get('sell_price', 0)),
        currency=request.form.get('currency', 'JPY').strip(),
        trade_date=datetime.strptime(request.form['trade_date'], '%Y-%m-%d').date(),
        fees=float(request.form.get('fees', 0)),
        is_nisa=bool(request.form.get('is_nisa')),
    )
    db.session.add(g)
    db.session.commit()
    flash(f'実現損益「{g.name}」を追加しました', 'success')
    return redirect(url_for('realized_gains'))


@app.route('/realized-gain/<int:gain_id>/delete', methods=['POST'])
def realized_gain_delete(gain_id):
    g = RealizedGain.query.get_or_404(gain_id)
    db.session.delete(g)
    db.session.commit()
    flash('実現損益を削除しました', 'success')
    return redirect(url_for('realized_gains'))


# ── データエクスポート ─────────────────────────
@app.route('/export/holdings')
def export_holdings():
    family_id = request.args.get('family_id', type=int)
    query = Holding.query.join(Account).join(FamilyMember)
    if family_id:
        query = query.filter(FamilyMember.family_id == family_id)
    holdings = query.all()
    rates, _ = get_exchange_rates()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['メンバー', '証券会社', '口座', '銘柄コード', '銘柄名',
                     '資産クラス', '数量', '取得単価', '現在値', '通貨',
                     '評価額', '取得金額', '損益', '損益率', '評価額(JPY)', 'NISA'])
    for h in holdings:
        writer.writerow([
            h.account.member.name, h.account.broker, h.account.account_type,
            h.symbol, h.name, h.asset_class,
            f'{h.quantity:.2f}', f'{h.avg_cost:.4f}', f'{h.current_price:.4f}',
            h.currency,
            f'{h.market_value:.2f}', f'{h.cost_basis:.2f}',
            f'{h.unrealized_pnl:.2f}', f'{h.pnl_percent:.2f}',
            f'{convert_to_jpy(h.market_value, h.currency, rates):.0f}',
            'NISA' if h.is_nisa else '',
        ])

    output.seek(0)
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=holdings_{date.today().isoformat()}.csv'}
    )


@app.route('/export/dividends')
def export_dividends():
    year = request.args.get('year', type=int, default=date.today().year)
    divs = Dividend.query.join(Account).join(FamilyMember).order_by(Dividend.payment_date.desc()).all()
    divs = [d for d in divs if d.payment_date and d.payment_date.year == year]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['支払日', 'メンバー', '証券会社', '銘柄コード', '銘柄名',
                     '配当金額', '税額', '手取額', '通貨', 'NISA'])
    for d in divs:
        writer.writerow([
            d.payment_date.isoformat(), d.account.member.name, d.account.broker,
            d.symbol, d.name,
            f'{d.amount:.2f}', f'{d.tax_amount:.2f}', f'{d.net_amount:.2f}',
            d.currency, 'NISA' if d.is_nisa else '',
        ])

    output.seek(0)
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=dividends_{year}.csv'}
    )


@app.route('/export/realized-gains')
def export_realized_gains():
    year = request.args.get('year', type=int, default=date.today().year)
    gains = RealizedGain.query.join(Account).join(FamilyMember).order_by(RealizedGain.trade_date.desc()).all()
    gains = [g for g in gains if g.trade_date and g.trade_date.year == year]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['取引日', 'メンバー', '証券会社', '銘柄コード', '銘柄名',
                     '数量', '買付単価', '売却単価', '実現損益', '手数料', '通貨', 'NISA'])
    for g in gains:
        writer.writerow([
            g.trade_date.isoformat(), g.account.member.name, g.account.broker,
            g.symbol, g.name,
            f'{g.quantity:.2f}', f'{g.buy_price:.4f}', f'{g.sell_price:.4f}',
            f'{g.realized_pnl:.2f}', f'{g.fees:.2f}',
            g.currency, 'NISA' if g.is_nisa else '',
        ])

    output.seek(0)
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=realized_gains_{year}.csv'}
    )


# ── API ───────────────────────────────────────
@app.route('/api/asset_allocation/<int:family_id>')
def api_asset_allocation(family_id):
    family = Family.query.get_or_404(family_id)
    all_holdings = _get_family_holdings(family)

    if not all_holdings:
        return jsonify({'error': 'No holdings found'})

    rates, _ = get_exchange_rates()
    asset = calculate_asset_allocation(all_holdings, rates)
    broker = calculate_broker_allocation(all_holdings, rates)

    return jsonify({
        'asset': {
            'labels': list(asset['allocation'].keys()),
            'values': [a['value'] for a in asset['allocation'].values()],
            'percents': [a['percent'] for a in asset['allocation'].values()],
        },
        'broker': {
            'labels': list(broker['by_broker'].keys()),
            'values': [b['value'] for b in broker['by_broker'].values()],
            'percents': [b['percent'] for b in broker['by_broker'].values()],
        },
        'total_value': asset['total_value'],
    })


@app.route('/api/portfolio_history/<int:family_id>')
def api_portfolio_history(family_id):
    """ポートフォリオ推移データを返す"""
    snapshots = PortfolioSnapshot.query.filter_by(
        family_id=family_id
    ).order_by(PortfolioSnapshot.snapshot_date).all()

    return jsonify({
        'dates': [s.snapshot_date.isoformat() for s in snapshots],
        'values': [round(s.total_value) for s in snapshots],
        'costs': [round(s.total_cost) for s in snapshots],
        'pnls': [round(s.total_pnl) for s in snapshots],
    })


@app.route('/api/snapshot', methods=['POST'])
def api_snapshot():
    """手動でスナップショットを保存"""
    family_id = request.get_json().get('family_id')
    if family_id:
        _take_snapshot(family_id)
        return jsonify({'success': True})
    return jsonify({'error': 'family_id required'}), 400


# ── 目標管理 ──────────────────────────────────
@app.route('/goal/add', methods=['POST'])
def goal_add():
    family_id = request.form.get('family_id', type=int)
    if not family_id:
        flash('家族を選択してください', 'error')
        return redirect(url_for('dashboard'))

    goal = Goal(
        family_id=family_id,
        name=request.form.get('name', 'FIRE目標').strip(),
        target_amount=float(request.form.get('target_amount', 0)),
        goal_type=request.form.get('goal_type', 'total').strip(),
        target_date=datetime.strptime(request.form['target_date'], '%Y-%m-%d').date()
            if request.form.get('target_date') else None,
    )
    db.session.add(goal)
    db.session.commit()
    flash(f'目標「{goal.name}」を追加しました', 'success')
    return redirect(url_for('dashboard', family_id=family_id))


@app.route('/goal/<int:goal_id>/delete', methods=['POST'])
def goal_delete(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    family_id = goal.family_id
    db.session.delete(goal)
    db.session.commit()
    flash('目標を削除しました', 'success')
    return redirect(url_for('dashboard', family_id=family_id))


# ── 株価自動更新 ────────────────────────────────
@app.route('/api/refresh_prices', methods=['POST'])
def api_refresh_prices():
    """保有銘柄の株価を一括更新する"""
    data = request.get_json() or {}
    family_id = data.get('family_id')

    if family_id:
        family = Family.query.get(family_id)
        if not family:
            return jsonify({'error': '家族が見つかりません'}), 404
        holdings = _get_family_holdings(family)
    else:
        holdings = Holding.query.all()

    updated = 0
    failed = 0
    results = []

    for h in holdings:
        if not h.symbol:
            continue

        stock_data = fetch_stock_price(h.symbol, h.currency)
        if stock_data and stock_data['price'] > 0:
            old_price = h.current_price
            h.current_price = stock_data['price']
            h.updated_at = datetime.utcnow()
            updated += 1
            results.append({
                'name': h.name,
                'symbol': h.symbol,
                'old_price': old_price,
                'new_price': stock_data['price'],
                'change_pct': stock_data.get('change_pct', 0),
            })
        else:
            failed += 1

    if updated > 0:
        db.session.commit()
        # スナップショットも更新
        if family_id:
            _take_snapshot(family_id)

    return jsonify({
        'updated': updated,
        'failed': failed,
        'results': results,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
    })


@app.route('/api/refresh_price/<int:holding_id>', methods=['POST'])
def api_refresh_single_price(holding_id):
    """個別銘柄の株価を更新する"""
    h = Holding.query.get_or_404(holding_id)

    if not h.symbol:
        return jsonify({'error': '銘柄コードがありません'}), 400

    stock_data = fetch_stock_price(h.symbol, h.currency)
    if not stock_data or stock_data['price'] <= 0:
        return jsonify({'error': '株価の取得に失敗しました'}), 502

    old_price = h.current_price
    h.current_price = stock_data['price']
    h.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'success': True,
        'old_price': old_price,
        'new_price': stock_data['price'],
        'change': stock_data.get('change', 0),
        'change_pct': stock_data.get('change_pct', 0),
        'market_state': stock_data.get('market_state', ''),
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
    })


# ── 配当情報取得 ────────────────────────────────
@app.route('/api/dividend_info/<int:holding_id>')
def api_dividend_info(holding_id):
    """銘柄の配当情報を取得する"""
    h = Holding.query.get_or_404(holding_id)

    if not h.symbol:
        return jsonify({'error': '銘柄コードがありません'}), 400

    # まずAPIから取得を試行
    info = fetch_dividend_info(h.symbol, h.currency)

    if info:
        # スケジュールをDBに保存
        _save_dividend_schedule(h.symbol, h.name, info, h.currency)
        return jsonify({
            'source': 'api',
            'dividend_yield': info['dividend_yield'],
            'annual_dividend': info['annual_dividend'],
            'ex_dividend_date': info['ex_dividend_date'],
            'payment_months': info['payment_months'],
            'frequency': info['frequency'],
            'currency': info.get('currency', h.currency),
        })

    # フォールバック: 既知のスケジュール
    schedule = get_dividend_schedule(h.symbol)
    if schedule:
        _save_dividend_schedule(h.symbol, h.name, {
            'dividend_yield': 0, 'annual_dividend': 0,
            'payment_months': schedule['months'],
            'frequency': schedule['frequency'],
        }, h.currency)
        return jsonify({
            'source': 'known',
            'dividend_yield': None,
            'annual_dividend': None,
            'ex_dividend_date': None,
            'payment_months': schedule['months'],
            'frequency': schedule['frequency'],
            'currency': h.currency,
        })

    return jsonify({
        'source': 'none',
        'error': '配当情報が取得できませんでした',
        'payment_months': [],
        'frequency': '不明',
    })


def _save_dividend_schedule(symbol, name, info, currency):
    """配当スケジュールをDBに保存/更新する"""
    sched = DividendSchedule.query.filter_by(symbol=symbol).first()
    if not sched:
        sched = DividendSchedule(symbol=symbol)
        db.session.add(sched)
    sched.name = name
    if info.get('annual_dividend'):
        sched.annual_dividend = info['annual_dividend']
    if info.get('dividend_yield'):
        sched.dividend_yield = info['dividend_yield']
    if info.get('payment_months'):
        sched.set_months_list(info['payment_months'])
    if info.get('frequency'):
        sched.frequency = info['frequency']
    sched.currency = currency
    db.session.commit()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
