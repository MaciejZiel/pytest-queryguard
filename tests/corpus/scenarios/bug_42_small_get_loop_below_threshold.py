"""BUG repeated-statement: a four-iteration ``session.get`` loop.

The same pattern as the large loops, over the four rows a test happened to
seed. The default threshold of five lets it through; the small-repeat rule
reports it. The verdict is BUG because the code does a lookup per item of a
list whose length the code does not control.
"""

from sqlalchemy.orm import Session

from ..domains import shop
from ..kit import BUG, REPEAT, Ctx

VERDICT = BUG
KINDS = frozenset({REPEAT})
KNOWN = {"default": "the repeat threshold is 5; use queryguard_small_repeat_threshold"}
setup = shop.setup


def run(ctx: Ctx) -> None:
    with Session(ctx.engine()) as session:
        found = [session.get(shop.Product, i) for i in (1, 2, 3, 4)]
    assert all(found)
