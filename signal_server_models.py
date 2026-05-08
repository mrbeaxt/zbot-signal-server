"""
Database Models for Z-BOT Signal Server
Uses SQLAlchemy with PostgreSQL (Free on Render)
"""

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import uuid

Base = declarative_base()


class User(Base):
    """User model - providers and subscribers"""
    __tablename__ = 'users'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    access_token = Column(String, unique=True, nullable=False, index=True)
    user_type = Column(String, default=None)  # 'provider' or 'subscriber' (null = legacy)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    providers = relationship('Provider', back_populates='owner')
    subscriptions = relationship('Subscription', back_populates='user')
    subscription_requests = relationship('SubscriptionRequest', back_populates='user')


class Provider(Base):
    """Signal Provider model"""
    __tablename__ = 'providers'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String, ForeignKey('users.id'), nullable=False)
    name = Column(String, nullable=False, index=True)
    membership_value = Column(String, default='Free')
    observations = Column(Text, default='')
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    owner = relationship('User', back_populates='providers')
    subscriptions = relationship('Subscription', back_populates='provider')
    subscription_requests = relationship('SubscriptionRequest', back_populates='provider')
    signals = relationship('Signal', back_populates='provider')
    trades = relationship('ProviderTrade', back_populates='provider')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'membership_value': self.membership_value,
            'observations': self.observations,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'owner_email': self.owner.email if self.owner else None
        }


class Subscription(Base):
    """Active subscription model"""
    __tablename__ = 'subscriptions'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    provider_id = Column(String, ForeignKey('providers.id'), nullable=False)
    contact = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    real_access = Column(Boolean, default=False)
    demo_access = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship('User', back_populates='subscriptions')
    provider = relationship('Provider', back_populates='subscriptions')
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'provider_id': self.provider_id,
            'contact': self.contact,
            'is_active': self.is_active,
            'real_access': self.real_access,
            'demo_access': self.demo_access,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created_at': self.created_at.isoformat(),
            'user_email': self.user.email if self.user else None,
            'user_name': self.user.name if self.user else None
        }


class SubscriptionRequest(Base):
    """Subscription request model"""
    __tablename__ = 'subscription_requests'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    provider_id = Column(String, ForeignKey('providers.id'), nullable=False)
    contact = Column(String, nullable=False)
    status = Column(String, default='pending')  # pending, approved, rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship('User', back_populates='subscription_requests')
    provider = relationship('Provider', back_populates='subscription_requests')
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'provider_id': self.provider_id,
            'contact': self.contact,
            'status': self.status,
            'user_email': self.user.email if self.user else None,
            'created_at': self.created_at.isoformat()
        }


class Signal(Base):
    """Signal history model"""
    __tablename__ = 'signals'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_id = Column(String, ForeignKey('providers.id'), nullable=False)
    asset = Column(String, nullable=False)
    direction = Column(String, nullable=False)  # CALL or PUT
    duration = Column(Integer, nullable=False)  # minutes
    brokers = Column(String, default='')  # JSON string
    timestamp = Column(DateTime, default=datetime.utcnow)
    # Outcome fields (filled later by provider/subscriber report)
    result = Column(String, nullable=True)  # WIN / LOSS / DRAW
    profit = Column(Float, nullable=True)   # net profit (can be negative)
    closed_at = Column(DateTime, nullable=True)
    
    # Relationship
    provider = relationship('Provider', back_populates='signals')
    
    def to_dict(self):
        return {
            'id': self.id,
            'provider_id': self.provider_id,
            'asset': self.asset,
            'direction': self.direction,
            'duration': self.duration,
            'brokers': self.brokers,
            'timestamp': self.timestamp.isoformat(),
            'result': self.result,
            'profit': self.profit,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None
        }


class ProviderTrade(Base):
    """
    Provider private trade/performance record.
    Used for public provider stats without broadcasting signals.
    """
    __tablename__ = 'provider_trades'

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_id = Column(String, ForeignKey('providers.id'), nullable=False, index=True)

    asset = Column(String, nullable=False)
    direction = Column(String, nullable=False)  # CALL/PUT
    duration = Column(Integer, nullable=False)  # minutes

    account_type = Column(String, default='Demo')  # Demo / Real
    broker = Column(String, default='')
    strategy = Column(String, default='')

    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, default=datetime.utcnow)

    result = Column(String, nullable=False)  # WIN/LOSS/DRAW
    profit = Column(Float, nullable=True)

    provider = relationship('Provider', back_populates='trades')

    def to_dict(self):
        return {
            'id': self.id,
            'provider_id': self.provider_id,
            'asset': self.asset,
            'direction': self.direction,
            'duration': self.duration,
            'account_type': self.account_type,
            'broker': self.broker,
            'strategy': self.strategy,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'result': self.result,
            'profit': self.profit,
        }


# Database setup
def get_db_engine(database_url):
    """Create database engine"""
    return create_engine(database_url)


def get_db_session(engine):
    """Create database session"""
    Session = sessionmaker(bind=engine)
    return Session()


def init_db(engine):
    """Create all tables + lightweight schema migration for new columns."""
    Base.metadata.create_all(engine)

    # Auto-migrate existing installs (SQLite/Postgres) without Alembic.
    # This keeps older deployments working when we add new columns.
    try:
        from sqlalchemy import inspect, text
        insp = inspect(engine)
        
        # Migrate users table
        if insp.has_table('users'):
            existing_user_cols = {c['name'] for c in insp.get_columns('users')}
            user_alters = []
            if 'user_type' not in existing_user_cols:
                user_alters.append("ALTER TABLE users ADD COLUMN user_type VARCHAR(16)")
            if user_alters:
                with engine.begin() as conn:
                    for sql in user_alters:
                        conn.execute(text(sql))
        
        # Migrate signals table
        if not insp.has_table('signals'):
            return
        existing_cols = {c['name'] for c in insp.get_columns('signals')}
        alters = []
        if 'result' not in existing_cols:
            alters.append("ALTER TABLE signals ADD COLUMN result VARCHAR(16)")
        if 'profit' not in existing_cols:
            alters.append("ALTER TABLE signals ADD COLUMN profit FLOAT")
        if 'closed_at' not in existing_cols:
            alters.append("ALTER TABLE signals ADD COLUMN closed_at TIMESTAMP")
        if alters:
            with engine.begin() as conn:
                for sql in alters:
                    conn.execute(text(sql))
    except Exception:
        # Safe best-effort migration. If it fails (permissions/dialect),
        # server still runs; stats will just omit missing fields.
        pass
