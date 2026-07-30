from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class College(Base):
    __tablename__ = "colleges"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    division: Mapped[str] = mapped_column(String(30))
    conference: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(30))
    academic_ranking: Mapped[str | None] = mapped_column(Text)
    # tuition holds the combined in-state tuition + housing cost (its sum), so the
    # existing school-list filter and PDF keep working; the components are stored too.
    tuition: Mapped[float | None] = mapped_column(Float)
    in_state_tuition: Mapped[float | None] = mapped_column(Float)
    housing_cost: Mapped[float | None] = mapped_column(Float)
    financial_aid: Mapped[str | None] = mapped_column(Text)
    head_coach: Mapped[str | None] = mapped_column(Text)
    recruiting_coordinator: Mapped[str | None] = mapped_column(Text)
    coach_emails: Mapped[str | None] = mapped_column(Text)
    coach_phones: Mapped[str | None] = mapped_column(Text)
    roster_size: Mapped[int | None] = mapped_column(Integer)
    scholarship_count: Mapped[int | None] = mapped_column(Integer)
    facilities_notes: Mapped[str | None] = mapped_column(Text)
    program_reputation: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    needs: Mapped[list[TeamNeed]] = relationship(back_populates="college", cascade="all, delete-orphan")
    coaches: Mapped[list[Coach]] = relationship(
        back_populates="college", cascade="all, delete-orphan", order_by="Coach.sort_order")


class Coach(Base):
    """A single member of a college's softball coaching staff.

    Colleges keep flat head_coach / coach_emails fields for the existing UI and
    email tools; this table holds the full structured staff (head coach,
    assistants, recruiting coordinators, directors of player development, etc.).
    """
    __tablename__ = "coaches"
    id: Mapped[int] = mapped_column(primary_key=True)
    college_id: Mapped[int] = mapped_column(ForeignKey("colleges.id"))
    name: Mapped[str] = mapped_column(String(160))
    title: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(50))
    twitter: Mapped[str | None] = mapped_column(String(120))
    # Lower sort_order surfaces first; head coach = 0, assistants after.
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    # Provenance for contact fields that were checked but not publicly listed.
    source_note: Mapped[str | None] = mapped_column(Text)
    college: Mapped[College] = relationship(back_populates="coaches")


class TeamNeed(Base):
    __tablename__ = "team_needs"
    id: Mapped[int] = mapped_column(primary_key=True)
    college_id: Mapped[int] = mapped_column(ForeignKey("colleges.id"))
    class_year: Mapped[int] = mapped_column(Integer)
    position: Mapped[str] = mapped_column(String(30))
    pitching_profile: Mapped[str | None] = mapped_column(Text)
    hitting_profile: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    college: Mapped[College] = relationship(back_populates="needs")


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    grad_year: Mapped[int] = mapped_column(Integer)
    primary_position: Mapped[str] = mapped_column(String(30))
    secondary_position: Mapped[str | None] = mapped_column(String(30))
    player_email: Mapped[str | None] = mapped_column(String(160))
    parent_email: Mapped[str | None] = mapped_column(String(160))
    gpa: Mapped[float | None] = mapped_column(Float)
    sat_act: Mapped[str | None] = mapped_column(String(50))
    height: Mapped[str | None] = mapped_column(String(30))
    weight: Mapped[str | None] = mapped_column(String(30))
    throwing_hand: Mapped[str | None] = mapped_column(String(20))
    batting_side: Mapped[str | None] = mapped_column(String(20))
    home_to_first: Mapped[str | None] = mapped_column(String(30))
    exit_velo: Mapped[str | None] = mapped_column(String(30))
    pop_time: Mapped[str | None] = mapped_column(String(30))
    pitching_velo: Mapped[str | None] = mapped_column(String(30))
    highlight_link: Mapped[str | None] = mapped_column(String(400))
    transcript_path: Mapped[str | None] = mapped_column(String(400))
    photo_path: Mapped[str | None] = mapped_column(String(400))
    social_handles: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlayerIntake(Base):
    """Submission from the player/parent profile and college preferences form."""
    __tablename__ = "player_intakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(30), default="new")
    player_name: Mapped[str] = mapped_column(String(160))
    grad_year: Mapped[int] = mapped_column(Integer)
    primary_position: Mapped[str] = mapped_column(String(30))
    secondary_position: Mapped[str | None] = mapped_column(String(30))
    player_email: Mapped[str | None] = mapped_column(String(160))
    parent_name: Mapped[str | None] = mapped_column(String(160))
    parent_email: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(50))
    gpa: Mapped[float | None] = mapped_column(Float)
    sat_act: Mapped[str | None] = mapped_column(String(50))
    height: Mapped[str | None] = mapped_column(String(30))
    weight: Mapped[str | None] = mapped_column(String(30))
    throwing_hand: Mapped[str | None] = mapped_column(String(20))
    batting_side: Mapped[str | None] = mapped_column(String(20))
    home_to_first: Mapped[str | None] = mapped_column(String(30))
    exit_velo: Mapped[str | None] = mapped_column(String(30))
    pop_time: Mapped[str | None] = mapped_column(String(30))
    pitching_velo: Mapped[str | None] = mapped_column(String(30))
    highlight_link: Mapped[str | None] = mapped_column(String(400))
    intended_major: Mapped[str | None] = mapped_column(String(160))
    max_tuition: Mapped[float | None] = mapped_column(Float)
    division_prefs: Mapped[str | None] = mapped_column(String(200))
    preferred_locations: Mapped[str | None] = mapped_column(String(300))
    campus_setting: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
