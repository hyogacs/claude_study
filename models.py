from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Family(db.Model):
    """家族グループ"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    members = db.relationship('FamilyMember', backref='family', cascade='all, delete-orphan')


class FamilyMember(db.Model):
    """家族メンバー"""
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('family.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    relationship = db.Column(db.String(50), nullable=False)  # 本人, 配偶者, 子供, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accounts = db.relationship('Account', backref='member', cascade='all, delete-orphan')


class Account(db.Model):
    """証券口座"""
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('family_member.id'), nullable=False)
    broker = db.Column(db.String(50), nullable=False)  # SBI, moomoo, other
    account_type = db.Column(db.String(50), nullable=False)  # 特定, 一般, NISA
    account_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    holdings = db.relationship('Holding', backref='account', cascade='all, delete-orphan')
    transactions = db.relationship('Transaction', backref='account', cascade='all, delete-orphan')


class Holding(db.Model):
    """保有資産"""
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    symbol = db.Column(db.String(20))
    name = db.Column(db.String(200), nullable=False)
    asset_class = db.Column(db.String(50), nullable=False)  # 日本株式, 外国株式, 投資信託, ETF, 債券, 現金
    quantity = db.Column(db.Float, nullable=False, default=0)
    avg_cost = db.Column(db.Float, nullable=False, default=0)
    current_price = db.Column(db.Float, nullable=False, default=0)
    currency = db.Column(db.String(10), default='JPY')
    is_nisa = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def market_value(self):
        return self.quantity * self.current_price

    @property
    def cost_basis(self):
        return self.quantity * self.avg_cost

    @property
    def unrealized_pnl(self):
        return self.market_value - self.cost_basis

    @property
    def pnl_percent(self):
        if self.cost_basis == 0:
            return 0
        return (self.unrealized_pnl / self.cost_basis) * 100


class Transaction(db.Model):
    """取引履歴"""
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    trade_date = db.Column(db.Date, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 買付, 売付, 配当
    symbol = db.Column(db.String(20))
    name = db.Column(db.String(200))
    quantity = db.Column(db.Float, default=0)
    price = db.Column(db.Float, default=0)
    amount = db.Column(db.Float, nullable=False)
    fees = db.Column(db.Float, default=0)
    is_nisa = db.Column(db.Boolean, default=False)
    nisa_type = db.Column(db.String(30))  # つみたて投資枠, 成長投資枠


class NISAUsage(db.Model):
    """NISA利用状況"""
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('family_member.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    tsumitate_used = db.Column(db.Float, default=0)  # つみたて投資枠使用額 (上限120万)
    growth_used = db.Column(db.Float, default=0)      # 成長投資枠使用額 (上限240万)
    total_holding = db.Column(db.Float, default=0)     # 非課税保有総額 (上限1800万, 成長枠は1200万まで)
    member = db.relationship('FamilyMember', backref='nisa_usages')

    # 新NISA制度の上限
    TSUMITATE_ANNUAL_LIMIT = 1_200_000    # 年間120万円
    GROWTH_ANNUAL_LIMIT = 2_400_000       # 年間240万円
    TOTAL_LIFETIME_LIMIT = 18_000_000     # 生涯1800万円
    GROWTH_LIFETIME_LIMIT = 12_000_000    # 成長投資枠は生涯1200万円まで

    @property
    def tsumitate_remaining(self):
        return max(0, self.TSUMITATE_ANNUAL_LIMIT - self.tsumitate_used)

    @property
    def growth_remaining(self):
        return max(0, self.GROWTH_ANNUAL_LIMIT - self.growth_used)

    @property
    def annual_total_used(self):
        return self.tsumitate_used + self.growth_used

    @property
    def lifetime_remaining(self):
        return max(0, self.TOTAL_LIFETIME_LIMIT - self.total_holding)


class Goal(db.Model):
    """資産目標"""
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('family.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    goal_type = db.Column(db.String(30), default='total')  # total, fire, custom
    target_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship('Family', backref='goals')


class PortfolioSnapshot(db.Model):
    """資産推移スナップショット"""
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('family.id'), nullable=False)
    snapshot_date = db.Column(db.Date, nullable=False)
    total_value = db.Column(db.Float, default=0)
    total_cost = db.Column(db.Float, default=0)
    total_pnl = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    family = db.relationship('Family', backref='snapshots')


class Dividend(db.Model):
    """配当金記録"""
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    symbol = db.Column(db.String(20))
    name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    tax_amount = db.Column(db.Float, default=0)
    currency = db.Column(db.String(10), default='JPY')
    payment_date = db.Column(db.Date, nullable=False)
    is_nisa = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    account = db.relationship('Account', backref='dividends')

    @property
    def net_amount(self):
        return self.amount - self.tax_amount


class RealizedGain(db.Model):
    """実現損益記録"""
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    symbol = db.Column(db.String(20))
    name = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    buy_price = db.Column(db.Float, nullable=False)
    sell_price = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='JPY')
    trade_date = db.Column(db.Date, nullable=False)
    fees = db.Column(db.Float, default=0)
    is_nisa = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    account = db.relationship('Account', backref='realized_gains')

    @property
    def realized_pnl(self):
        return (self.sell_price - self.buy_price) * self.quantity - self.fees

    @property
    def cost_basis(self):
        return self.buy_price * self.quantity

    @property
    def proceeds(self):
        return self.sell_price * self.quantity
