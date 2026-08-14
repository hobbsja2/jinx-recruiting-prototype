from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import ActivityLog, College, Player, TeamNeed


def seed_demo_data(db: Session) -> None:
    """Load safe fictional data only when the database is empty."""
    if db.scalar(select(College.id).limit(1)):
        return
    north = College(name="North Valley University", division="NCAA D1", conference="Pioneer Conference", city="Cedar Falls", state="IA", academic_ranking="Regional university", tuition=28500, financial_aid="Merit aid available", head_coach="Morgan Reed", recruiting_coordinator="Avery Hall", coach_emails="recruiting@northvalley.example", coach_phones="555-0101", roster_size=23, scholarship_count=12, facilities_notes="Indoor training center", program_reputation="Competitive regional program", website_url="https://northvalley.example", notes="Strong academics and player development.")
    lakeside = College(name="Lakeside College", division="NCAA D2", conference="Great Lakes Athletic", city="Harbor Point", state="MI", academic_ranking="Private liberal arts", tuition=36200, financial_aid="Need and merit based aid", head_coach="Jamie Cruz", recruiting_coordinator="Sam Lee", coach_emails="coach@lakeside.example", coach_phones="555-0102", roster_size=20, scholarship_count=8, facilities_notes="New turf field", program_reputation="Known for defense", website_url="https://lakeside.example", notes="")
    summit = College(name="Summit Institute", division="NAIA", conference="Frontier Athletic", city="Summit Ridge", state="CO", academic_ranking="STEM-focused", tuition=24800, financial_aid="Academic scholarships", head_coach="Taylor Morgan", recruiting_coordinator="", coach_emails="softball@summit.example", coach_phones="555-0103", roster_size=19, scholarship_count=6, facilities_notes="Altitude training facilities", program_reputation="Emerging program", website_url="https://summit.example", notes="")
    db.add_all([north, lakeside, summit]); db.flush()
    db.add_all([
        TeamNeed(college_id=north.id, class_year=2026, position="SS", hitting_profile="Line drive hitter with on-base skills", notes="Immediate middle-infield depth."),
        TeamNeed(college_id=lakeside.id, class_year=2026, position="OF", hitting_profile="Gap power and speed", notes="Center-field priority."),
        TeamNeed(college_id=summit.id, class_year=2027, position="P", pitching_profile="RHP, command and riseball", notes="Developmental arm."),
        TeamNeed(college_id=north.id, class_year=2027, position="C", hitting_profile="Power/OBP", notes="")])
    db.add_all([
        Player(name="Jordan Brooks", grad_year=2026, primary_position="SS", secondary_position="OF", gpa=3.8, sat_act="SAT 1260", height="5'7\"", throwing_hand="R", batting_side="R", home_to_first="2.90", exit_velo="68 mph", pop_time="", pitching_velo="", highlight_link="https://video.example/jordan", transcript_path="demo/transcript-jordan.pdf", photo_path="demo/jordan.jpg", social_handles="@jordanbrooks", notes="High academic performer and versatile defender."),
        Player(name="Casey Nguyen", grad_year=2027, primary_position="P", secondary_position="1B", gpa=3.6, sat_act="", height="5'9\"", throwing_hand="R", batting_side="L", home_to_first="3.15", exit_velo="63 mph", pop_time="", pitching_velo="61 mph", highlight_link="https://video.example/casey", transcript_path="", photo_path="demo/casey.jpg", social_handles="@caseynguyen", notes="Command-focused right-handed pitcher.")])
    db.add(ActivityLog(kind="seed", detail="Loaded fictional local demonstration data."))
    db.commit()


DEMO_CONTACTS = {
    "Jordan Brooks": ("jordan.brooks@example.com", "brooks.family@example.com"),
    "Casey Nguyen": ("casey.nguyen@example.com", "nguyen.family@example.com"),
}


def backfill_demo_contacts(db: Session) -> None:
    """Fill placeholder family emails for the bundled demo players only."""
    changed = False
    for name, (player_email, parent_email) in DEMO_CONTACTS.items():
        player = db.scalar(select(Player).where(Player.name == name))
        if player is None:
            continue
        if not player.player_email:
            player.player_email = player_email; changed = True
        if not player.parent_email:
            player.parent_email = parent_email; changed = True
    if changed:
        db.commit()
