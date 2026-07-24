from sqlalchemy.orm import Session

from app.models.member import Member


def toggle_chroniclemail(db: Session, member: Member) -> bool:
    member.chroniclemail = not member.chroniclemail
    db.commit()
    return member.chroniclemail or False
