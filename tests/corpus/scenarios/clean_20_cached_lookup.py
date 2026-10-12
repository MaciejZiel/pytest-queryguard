"""CLEAN: a cached settings lookup called twelve times for two keys.

The second call for a key is served from a dict, so only two statements run.
"""

from sqlalchemy import String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ..kit import CLEAN, Ctx

VERDICT = CLEAN
KINDS = frozenset()
KNOWN = {
    "small-repeat": "two lookups of one shape from one line look like a small loop; a cache keyed by argument cannot be told apart",
}


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    value: Mapped[str] = mapped_column(String(20))


def setup(ctx: Ctx) -> None:
    ctx.reset()
    Base.metadata.create_all(ctx.engine())
    with Session(ctx.engine()) as session:
        session.add_all(
            [Setting(key="theme", value="dark"), Setting(key="lang", value="en")]
        )
        session.commit()


class Settings:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.cache: dict[str, str] = {}

    def get(self, key: str) -> str:
        if key not in self.cache:
            self.cache[key] = (
                self.session.scalar(select(Setting.value).where(Setting.key == key))
                or ""
            )
        return self.cache[key]


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        settings = Settings(session)
        values = [settings.get(k) for _ in range(6) for k in ("theme", "lang")]
    assert values[:2] == ["dark", "en"]
