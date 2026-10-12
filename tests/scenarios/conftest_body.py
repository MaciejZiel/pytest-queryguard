import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import *


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path}/t.db"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        for i in range(1, 7):
            s.add(
                Parent(
                    id=i,
                    name=f"p{i}",
                    children=[Child(name=f"c{i}{j}", blob="x") for j in range(3)],
                )
            )
        tags = [Tag(name=f"t{i}") for i in range(3)]
        s.add_all(tags)
        for i in range(1, 7):
            s.add(Post(id=i, tags=tags[:2]))
        root = Node(id=1)
        for i in range(2, 8):
            root.kids.append(Node(id=i))
        s.add(root)
        s.commit()
    with Session(eng) as s:
        s.execute(select(1))
    yield eng, url
    eng.dispose()
