from datetime import date

from app.models.badge import Badge
from app.models.contact import Contact
from app.models.key import Key
from app.models.member import Member
from app.models.member_badge import MemberBadge
from app.models.member_key import MemberKey
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage
from app.models.state import State


def test_org_model(db_session):
    """Org reference data can be read."""
    org = Org(id="test", label="Testorg", order=1)
    db_session.add(org)
    db_session.commit()

    loaded = db_session.get(Org, "test")
    assert loaded.label == "Testorg"
    assert loaded.order == 1


def test_state_model(db_session):
    """State reference data can be read."""
    state = State(id="fu", label="Fux", order=1)
    db_session.add(state)
    db_session.commit()

    loaded = db_session.get(State, "fu")
    assert loaded.label == "Fux"


def test_badge_model(db_session):
    """Badge reference data can be read."""
    badge = Badge(id=1, name="Fuxenband", group="band", order=1)
    db_session.add(badge)
    db_session.commit()

    loaded = db_session.get(Badge, 1)
    assert loaded.name == "Fuxenband"
    assert loaded.group == "band"


def test_key_model(db_session):
    """Key reference data can be read."""
    key = Key(id=1, name="Haustorschlüssel")
    db_session.add(key)
    db_session.commit()

    loaded = db_session.get(Key, 1)
    assert loaded.name == "Haustorschlüssel"


def test_member_cn_active(db_session):
    """Active member cn includes 'v/o'."""
    member = Member(
        email="cn@test.at",
        vorname="Max",
        nachname="Muster",
        couleurname="Maxl",
        org_id="vbw",
        entlassen=False,
        verstorben=False,
    )
    db_session.add(member)
    db_session.commit()

    assert member.cn == "Max Muster v/o Maxl"


def test_member_cn_dismissed(db_session):
    """Dismissed member cn uses 'wl.' instead of 'v/o'."""
    member = Member(
        email="wl@test.at",
        vorname="Hans",
        nachname="Alt",
        couleurname="Hansi",
        org_id="vbw",
        entlassen=True,
        verstorben=False,
    )
    db_session.add(member)
    db_session.commit()

    assert member.cn == "Hans Alt wl. Hansi"


def test_member_cn_deceased(db_session):
    """Deceased member cn uses 'wl.'."""
    member = Member(
        email="dead@test.at",
        vorname="Fritz",
        nachname="Meier",
        couleurname="Fritzi",
        org_id="vbw",
        entlassen=False,
        verstorben=True,
    )
    db_session.add(member)
    db_session.commit()

    assert member.cn == "Fritz Meier wl. Fritzi"


def test_member_cn_couleurname_only(db_session):
    """Member with only couleurname."""
    member = Member(
        email="nur@test.at",
        couleurname="Maxl",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    assert member.cn == "Maxl"


def test_member_cn_no_couleurname(db_session):
    """Member without couleurname."""
    member = Member(
        email="kein@test.at",
        vorname="Max",
        nachname="Muster",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    assert member.cn == "Max Muster"


def test_member_cn_full(db_session):
    """cn_full includes titles."""
    member = Member(
        email="full@test.at",
        vortitel="Dr.",
        vorname="Max",
        nachname="Muster",
        nachtitel="MBA",
        couleurname="Maxl",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    assert member.cn_full == "Dr. Max Muster MBA v/o Maxl"


def test_member_parent_child(db_session):
    """Self-referencing parent/children relationship."""
    parent = Member(
        email="parent@test.at",
        vorname="Vater",
        nachname="Test",
        org_id="vbw",
    )
    db_session.add(parent)
    db_session.commit()

    child = Member(
        email="child@test.at",
        vorname="Kind",
        nachname="Test",
        org_id="vbw",
        parent_id=parent.id,
    )
    db_session.add(child)
    db_session.commit()

    db_session.refresh(parent)
    assert len(parent.children) == 1
    assert parent.children[0].vorname == "Kind"
    assert child.parent.vorname == "Vater"


def test_member_fuzzy_dates(db_session):
    """Fuzzy dates store correctly with accuracy."""
    member = Member(
        email="fuzzy@test.at",
        vorname="Fuzzy",
        nachname="Test",
        org_id="vbw",
        geburtsdatum=date(1990, 3, 15),
        geburtsdatum_accuracy=3,
        aufnahmedatum=date(2010, 10, 1),
        aufnahmedatum_accuracy=2,
    )
    db_session.add(member)
    db_session.commit()

    db_session.refresh(member)
    assert member.geburtsdatum == date(1990, 3, 15)
    assert member.geburtsdatum_accuracy == 3
    assert member.aufnahmedatum == date(2010, 10, 1)
    assert member.aufnahmedatum_accuracy == 2


def test_member_role_relationship(db_session):
    """Member roles with startdate/enddate."""
    role = Role(id="testrole", group="test", label="Test")
    db_session.add(role)

    member = Member(
        email="roles@test.at",
        vorname="Role",
        nachname="Test",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    mr = MemberRole(
        member_id=member.id,
        role_id="testrole",
        startdate=date(2020, 1, 1),
        enddate=None,
    )
    db_session.add(mr)
    db_session.commit()

    db_session.refresh(member)
    assert len(member.member_roles) == 1
    assert member.member_roles[0].role.label == "Test"
    assert member.member_roles[0].enddate is None


def test_member_badge_relationship(db_session):
    """Member badges with presentation date."""
    badge = Badge(id=99, name="Testband", group="test")
    db_session.add(badge)

    member = Member(
        email="badge@test.at",
        vorname="Badge",
        nachname="Test",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    mb = MemberBadge(
        member_id=member.id,
        badge_id=99,
        presentationdate=date(2023, 6, 15),
        presentationdate_accuracy=3,
    )
    db_session.add(mb)
    db_session.commit()

    db_session.refresh(member)
    assert len(member.member_badges) == 1
    assert member.member_badges[0].badge.name == "Testband"
    assert member.member_badges[0].presentationdate == (date(2023, 6, 15))


def test_member_key_relationship(db_session):
    """Member keys with presentation date."""
    key = Key(id=99, name="Testschlüssel")
    db_session.add(key)

    member = Member(
        email="key@test.at",
        vorname="Key",
        nachname="Test",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    mk = MemberKey(
        member_id=member.id,
        key_id=99,
        presentationdate=date(2023, 1, 1),
        presentationdate_accuracy=1,
    )
    db_session.add(mk)
    db_session.commit()

    db_session.refresh(member)
    assert len(member.member_keys) == 1
    assert member.member_keys[0].key.name == "Testschlüssel"


def test_contact_model(db_session):
    """Contact CRUD with cn property."""
    contact = Contact(
        kontakttyp="person",
        name="Verein XYZ",
        couleurname="VXY",
        zustellungen=True,
    )
    db_session.add(contact)
    db_session.commit()

    loaded = db_session.get(Contact, contact.id)
    assert loaded.cn == "Verein XYZ v/o VXY"
    assert loaded.kontakttyp == "person"
    assert loaded.zustellungen is True


def test_contact_cn_without_couleurname(db_session):
    """Contact cn without couleurname."""
    contact = Contact(
        kontakttyp="organisation",
        name="Firma ABC",
    )
    db_session.add(contact)
    db_session.commit()

    assert contact.cn == "Firma ABC"


def test_standesdb_image_model(db_session):
    """StandesdbImage stores metadata correctly."""
    img = StandesdbImage(
        owner_type="member",
        owner_id=1,
        sha256_hash="abc123",
        type="image/jpeg",
        extension="jpeg",
        size=12345,
        width=800,
        height=600,
        description="Testbild",
        default=1,
    )
    db_session.add(img)
    db_session.commit()

    loaded = db_session.get(StandesdbImage, img.id)
    assert loaded.owner_type == "member"
    assert loaded.type == "image/jpeg"
    assert loaded.default == 1
    assert loaded.deleted_at is None


def test_member_default_image(db_session):
    """default_image returns the default or oldest."""
    member = Member(
        email="img@test.at",
        vorname="Img",
        nachname="Test",
        org_id="vbw",
    )
    db_session.add(member)
    db_session.commit()

    assert member.default_image is None

    img1 = StandesdbImage(
        owner_type="member",
        owner_id=member.id,
        sha256_hash="hash1",
        default=0,
    )
    img2 = StandesdbImage(
        owner_type="member",
        owner_id=member.id,
        sha256_hash="hash2",
        default=1,
    )
    db_session.add_all([img1, img2])
    db_session.commit()
    db_session.refresh(member)

    assert member.default_image == img2.id
