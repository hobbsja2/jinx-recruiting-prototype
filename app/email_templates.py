"""Pre-generated coach outreach templates for the local email center."""
from __future__ import annotations

from dataclasses import dataclass

TEAM_NAME = "Jinx Softball"
SENDER_NAME = "Andy Hobbs"
EMAIL_SIGNATURE = "Andy Hobbs\nRecruiting Coordinator\nJinx Fastpitch HSD"


@dataclass(frozen=True)
class EmailTemplate:
    key: str
    label: str
    purpose: str
    subject: str
    body: str
    attachments: str = ""
    audience: str = "coach"  # "coach" or "family"
    embeds_form: bool = False


TEMPLATES: dict[str, EmailTemplate] = {
    "intro": EmailTemplate(
        key="intro",
        label="Team introduction",
        purpose="Introduce the team and its athletes to a college coaching staff.",
        subject="Introduction - {team} softball program",
        body="""{salutation}

My name is {sender}, writing on behalf of {team}. I wanted to introduce our program to you and your staff at {college}.

Our athletes compete on a year-round travel schedule and are actively working toward playing at the next level.{highlights}

I would welcome the chance to share more about our roster and to keep {college} updated as our players progress.

Thank you for your time,
{sender}
{team}""",
    ),
    "camps": EmailTemplate(
        key="camps",
        label="Camp & showcase inquiry",
        purpose="Request dates and registration details for camps and showcases the college hosts or sponsors.",
        subject="Camp and showcase information - {college}",
        body="""{salutation}

I am {sender} with {team}. Several of our athletes are interested in attending camps and showcases hosted or sponsored by {college}.

Could you share your upcoming camp and showcase schedule, including dates, locations, registration details, and any position-specific sessions?{highlights}

We would like to build our travel calendar around the events that best fit your program.

Thank you,
{sender}
{team}""",
    ),
    "needs": EmailTemplate(
        key="needs",
        label="Recruiting needs inquiry",
        purpose="Ask which positions and graduation years the program is prioritizing.",
        subject="Upcoming recruiting needs - {college}",
        body="""{salutation}

I am {sender} with {team}. As you look ahead to your next recruiting classes, I wanted to ask which positions and graduation years {college} is currently prioritizing.

If you are able to share your upcoming needs, I can point you toward the athletes on our roster who match them.{highlights}

Thank you for your help,
{sender}
{team}""",
    ),
    "flyer": EmailTemplate(
        key="flyer",
        label="Team promo flyer",
        purpose="Send the team promo flyer for the staff to keep on file.",
        subject="{team} team promo flyer",
        body="""{salutation}

I am {sender} with {team}. Attached is our team promo flyer with roster details, positions, graduation years, and staff contact information for {college} to keep on file.{highlights}

Please let me know if another format is easier for your staff, and I am glad to send individual player spotlight flyers as well.

Thank you,
{sender}
{team}""",
        attachments="Team-Promo-Flyer.pdf",
    ),
    "intake": EmailTemplate(
        key="intake",
        label="Player & parent intake form",
        purpose="Ask families to submit player profile details and college preferences through a fillable form.",
        subject="{team}: player profile and college preferences form",
        body="""{salutation}

We are building the recruiting file for {player_label} and need a few details from you. Please complete the short form below:

{form_url}

The form collects:
  - Player profile: graduation year, positions, academics, and key metrics
  - Intended major or area of study
  - Maximum tuition budget
  - Division level preference (NCAA D1/D2/D3, NAIA, JUCO)
  - Preferred campus location and setting

It takes about five minutes. Your answers feed directly into the school lists and coach outreach we build for your athlete, so the more complete the better.

If any field does not apply yet, leave it blank and we will follow up.

Thank you,
{sender}
{team}""",
        audience="family",
        embeds_form=True,
    ),
}


def coach_salutation(college) -> str:
    """Address the head coach of the college that owns the selected email address."""
    name = (getattr(college, "head_coach", "") or "").strip()
    if not name:
        return "Dear Coach,"
    return f"Dear Coach {name.split()[-1]},"


def family_salutation(player) -> str:
    """Greet the athlete and their family."""
    name = (getattr(player, "name", "") or "").strip()
    return f"Hi {name.split()[0]} and family," if name else f"Hi {TEAM_NAME} families,"


def render_template(template: EmailTemplate, college, player, form_url: str = "") -> tuple[str, str]:
    highlights = ""
    if player is not None and template.audience == "coach":
        highlights = (f" You may be particularly interested in {player.name}, a {player.grad_year} "
                      f"{player.primary_position} in our program.")
    context = {
        "salutation": family_salutation(player) if template.audience == "family" else coach_salutation(college),
        "coach": (getattr(college, "head_coach", "") or "Coach"),
        "college": getattr(college, "name", "your program"),
        "player_label": (getattr(player, "name", "") or "your athlete"),
        "team": TEAM_NAME,
        "sender": SENDER_NAME,
        "highlights": highlights,
        "form_url": form_url or "(intake form link)",
    }
    subject = template.subject.format(**context)
    body = template.body.format(**context)
    standard_closing = f"{SENDER_NAME}\n{TEAM_NAME}"
    if body.endswith(standard_closing):
        body = body[:-len(standard_closing)] + "\n" + EMAIL_SIGNATURE
    return subject, body
