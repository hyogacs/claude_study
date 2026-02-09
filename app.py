import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, Family, FamilyMember, Account, Holding, Transaction, NISAUsage
from csv_parsers import parse_sbi_holdings, parse_moomoo_holdings, parse_sbi_transactions
from calculators import (
    compound_interest_simulation,
    calculate_fire_number,
    calculate_nisa_from_holdings,
    calculate_asset_allocation,
    calculate_broker_allocation,
)
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
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


def _get_family_holdings(family):
    """家族の全保有資産を取得"""
    all_holdings = []
    for member in family.members:
        for account in member.accounts:
            all_holdings.extend(account.holdings)
    return all_holdings


# ── ダッシュボード ─────────────────────────────
@app.route('/')
def dashboard():
    families = Family.query.all()
    family_id = request.args.get('family_id', type=int)

    if not families:
        return render_template('dashboard.html', families=families, selected_family=None,
                               summary=None)

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
        total = sum(h.market_value for h in member_holdings)
        pnl = sum(h.unrealized_pnl for h in member_holdings)
        member_summaries.append({
            'member': member,
            'total_value': total,
            'total_pnl': pnl,
            'holding_count': len(member_holdings),
        })

    asset_alloc = calculate_asset_allocation(all_holdings) if all_holdings else None
    broker_alloc = calculate_broker_allocation(all_holdings) if all_holdings else None

    summary = {
        'total_value': sum(h.market_value for h in all_holdings),
        'total_pnl': sum(h.unrealized_pnl for h in all_holdings),
        'total_cost': sum(h.cost_basis for h in all_holdings),
        'holding_count': len(all_holdings),
        'member_summaries': member_summaries,
        'asset_allocation': asset_alloc,
        'broker_allocation': broker_alloc,
    }

    return render_template('dashboard.html', families=families,
                           selected_family=selected_family, summary=summary)


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


def _get_or_create_account(member, broker, account_type, nisa_type=''):
    """口座を取得、なければ作成する"""
    account = Account.query.filter_by(
        member_id=member.id, broker=broker, account_type=account_type
    ).first()
    if not account:
        name_parts = [broker, account_type]
        if nisa_type:
            name_parts.append(nisa_type)
        account = Account(
            member_id=member.id, broker=broker,
            account_type=account_type,
            account_name=' '.join(name_parts) + '口座'
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

    # Clear existing holdings for this broker/member before import
    existing_accounts = Account.query.filter_by(
        member_id=member.id, broker=broker
    ).all()
    for acc in existing_accounts:
        Holding.query.filter_by(account_id=acc.id).delete()

    # Group by account_type and import into separate accounts
    count = 0
    account_cache = {}

    for _, row in df.iterrows():
        account_type = str(row.get('account_type', '特定'))
        nisa_type = str(row.get('nisa_type', ''))

        cache_key = (broker, account_type, nisa_type)
        if cache_key not in account_cache:
            account_cache[cache_key] = _get_or_create_account(
                member, broker, account_type, nisa_type
            )

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

    # Build detail message
    details = []
    for (b, at, nt), acc in account_cache.items():
        n = Holding.query.filter_by(account_id=acc.id).count()
        label = at + (f'({nt})' if nt else '')
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

    return render_template('assets.html', holdings=holdings, families=families,
                           members=members, selected_family_id=family_id,
                           selected_member_id=member_id)


# ── 複利シミュレーション ─────────────────────────
@app.route('/simulation')
def simulation():
    families = Family.query.all()
    total_value = 0
    if families:
        family = families[0]
        all_holdings = _get_family_holdings(family)
        total_value = sum(h.market_value for h in all_holdings)
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

    # Compare NISA vs taxed
    taxed = compound_interest_simulation(initial, monthly, rate, years, is_nisa=False)
    nisa = compound_interest_simulation(initial, monthly, rate, years, is_nisa=True)

    return jsonify({
        'simulation': result,
        'comparison': {
            'taxed': taxed,
            'nisa': nisa,
        },
        'fire_number_4pct': calculate_fire_number(monthly * 12 * 1.5),
    })


@app.route('/api/portfolio_value')
def api_portfolio_value():
    """現在のポートフォリオ総額を返す"""
    families = Family.query.all()
    total = 0
    if families:
        for family in families:
            holdings = _get_family_holdings(family)
            total += sum(h.market_value for h in holdings)
    return jsonify({'total_value': round(total)})


# ── NISA管理 ──────────────────────────────────
@app.route('/nisa')
def nisa():
    families = Family.query.all()
    family_id = request.args.get('family_id', type=int)

    if not families:
        return render_template('nisa.html', families=families, selected_family=None,
                               nisa_data=[])

    selected_family = None
    if family_id:
        selected_family = Family.query.get(family_id)
    if not selected_family and families:
        selected_family = families[0]

    nisa_data = []
    if selected_family:
        for member in selected_family.members:
            summary = calculate_nisa_from_holdings(member)
            nisa_data.append({
                'member': member,
                'summary': summary,
            })

    return render_template('nisa.html', families=families,
                           selected_family=selected_family, nisa_data=nisa_data)


# ── API ───────────────────────────────────────
@app.route('/api/asset_allocation/<int:family_id>')
def api_asset_allocation(family_id):
    family = Family.query.get_or_404(family_id)
    all_holdings = _get_family_holdings(family)

    if not all_holdings:
        return jsonify({'error': 'No holdings found'})

    asset = calculate_asset_allocation(all_holdings)
    broker = calculate_broker_allocation(all_holdings)

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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
