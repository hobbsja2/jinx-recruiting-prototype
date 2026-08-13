from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    out_of_state_tuition: Mapped[float | None] = mapped_column(Float)
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
    programs: Mapped[list[CollegeProgram]] = relationship(
        back_populates="college", cascade="all, delete-orphan")


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


class AcademicProgram(Base):
    """Canonical four-digit CIP field of study shared across colleges."""
    __tablename__ = "academic_programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    cip_code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    college_programs: Mapped[list[CollegeProgram]] = relationship(back_populates="program")


class CollegeProgram(Base):
    """An active undergraduate credential reported for a college and CIP field."""
    __tablename__ = "college_programs"
    __table_args__ = (
        UniqueConstraint("college_id", "program_id", "credential_level", name="uq_college_program_credential"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    college_id: Mapped[int] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"), index=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("academic_programs.id"), index=True)
    credential_level: Mapped[int] = mapped_column(Integer)
    credential_title: Mapped[str] = mapped_column(String(80))
    scorecard_unit_id: Mapped[int] = mapped_column(Integer, index=True)
    source_name: Mapped[str] = mapped_column(String(240))
    source_url: Mapped[str] = mapped_column(String(500))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    college: Mapped[College] = relationship(back_populates="programs")
    program: Mapped[AcademicProgram] = relationship(back_populates="college_programs")


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
    # Home/school state (2-letter). Drives in-state vs out-of-state tuition display.
    home_state: Mapped[str | None] = mapped_column(String(30))
    intended_major: Mapped[str | None] = mapped_column(String(160))
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


class MicrosoftConnection(Base):
    """Encrypted delegated OAuth token cache for the one recruiting mailbox."""
    __tablename__ = "microsoft_connections"
    __table_args__ = (CheckConstraint("id = 1", name="ck_microsoft_connection_singleton"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_email: Mapped[str] = mapped_column(String(320), unique=True)
    home_account_id: Mapped[str] = mapped_column(String(200))
    encrypted_cache: Mapped[str] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IntakeInvitation(Base):
    """One-time, expiring public link sent to a player's family."""
    __tablename__ = "intake_invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), index=True)
    recipients: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


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
    home_state: Mapped[str | None] = mapped_column(String(30))
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
