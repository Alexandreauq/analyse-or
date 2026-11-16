# compte_api/models.py
# Toutes les tables du service (spec §4). Les tâches de la vague 2 ne
# modifient pas ce fichier : un besoin de colonne se signale, il ne
# s'ajoute pas en parallèle (une seule migration à maintenir).
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from compte_api.db import Base


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _fk_user() -> ForeignKey:
    return ForeignKey("users.id", ondelete="CASCADE")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # toujours en minuscules
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str | None] = mapped_column(String(255))  # null : compte Google seul
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="member")  # 'member' | 'admin'
    alerts_email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class Session(Base):
    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 du jeton du cookie
    user_id: Mapped[int] = mapped_column(_fk_user(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
    user_agent: Mapped[str | None] = mapped_column(String(255))


class EmailToken(Base):
    __tablename__ = "email_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk_user(), index=True)
    purpose: Mapped[str] = mapped_column(String(16))  # 'verify' | 'reset'
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Favorite(Base):
    __tablename__ = "favorites"

    user_id: Mapped[int] = mapped_column(_fk_user(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # 'index' | 'company'
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class Position(Base):
    """Mêmes champs que docs/portfolio.js : une position clôturée est la
    même ligne avec sell_price et sell_date remplis."""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk_user(), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float] = mapped_column(Float)
    buy_price: Mapped[float] = mapped_column(Float)
    buy_date: Mapped[date] = mapped_column(Date)
    sell_price: Mapped[float | None] = mapped_column(Float)
    sell_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk_user(), index=True)
    query: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class AiExchange(Base):
    __tablename__ = "ai_exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk_user(), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    user_id: Mapped[int] = mapped_column(_fk_user(), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)  # jour UTC
    count: Mapped[int] = mapped_column(Integer, default=0)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(_fk_user(), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # 'score_above' | 'score_below' | 'entry_signal'
    target: Mapped[str] = mapped_column(String(32))  # ticker ou clé d'indice
    threshold: Mapped[float | None] = mapped_column(Float)  # null pour entry_signal
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
    value: Mapped[float | None] = mapped_column(Float)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertSnapshot(Base):
    """Dernière valeur vue par la détection des alertes (score et signal
    d'entrée par ticker), pour ne déclencher qu'au franchissement."""
    __tablename__ = "alert_snapshots"
    __table_args__ = (UniqueConstraint("target", name="uq_alert_snapshots_target"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target: Mapped[str] = mapped_column(String(32))
    score: Mapped[float | None] = mapped_column(Float)
    entry_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class UserSettings(Base):
    __tablename__ = "settings"

    user_id: Mapped[int] = mapped_column(_fk_user(), primary_key=True)
    json: Mapped[str] = mapped_column(Text, default="{}")  # thème, niveau de réponse IA, filtres sauvegardés
