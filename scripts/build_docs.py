"""Build the documentation site from docs/*.md into site/ (static HTML).

    uv run --group docs python scripts/build_docs.py [--strict] [--out DIR]

The pages and their order are listed in docs/site.toml. The builder only needs
the `markdown` and `pygments` packages (the `docs` dependency group). With
--strict, a broken link, a missing anchor, a page that is not in the
navigation or a link that leaves the documentation without being an absolute
URL fails the build.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown
import tomllib
from pygments.formatters import HtmlFormatter

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

EXTENSIONS = ["tables", "fenced_code", "toc", "admonition", "sane_lists", "codehilite"]
EXTENSION_CONFIGS = {
    "codehilite": {"css_class": "hl", "guess_lang": False},
    "toc": {"permalink": "#", "toc_depth": "2-3"},
}

CSS = """\
:root{color-scheme:light dark;--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;
--code:#f6f8fa;--link:#0550ae;--side:#f6f8fa;--accent:#0b6e4f}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;
--line:#3d444d;--code:#151b23;--link:#79c0ff;--side:#151b23;--accent:#56d4a4}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}
.layout{display:flex;min-height:100vh}
nav.side{width:17rem;flex:none;background:var(--side);border-right:1px solid var(--line);
padding:1.25rem 1rem;position:sticky;top:0;height:100vh;overflow-y:auto}
nav.side .brand{font-weight:700;font-size:1.05rem;display:block;margin-bottom:1rem;
color:var(--fg);text-decoration:none}
nav.side h2{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
color:var(--muted);margin:1.1rem 0 .3rem}
nav.side ul{list-style:none;margin:0;padding:0}
nav.side a{display:block;padding:.15rem .4rem;border-radius:4px;color:var(--fg);
text-decoration:none;font-size:.92rem}
nav.side a:hover{background:var(--line)}
nav.side a.current{background:var(--accent);color:#fff}
main{flex:1;min-width:0;max-width:52rem;padding:1.5rem 2rem 4rem}
a{color:var(--link)}
h1,h2,h3{line-height:1.25}
h2{border-bottom:1px solid var(--line);padding-bottom:.25rem;margin-top:2rem}
.headerlink{opacity:0;margin-left:.4rem;text-decoration:none}
:is(h1,h2,h3):hover .headerlink{opacity:.6}
code{background:var(--code);padding:.1em .3em;border-radius:4px;font-size:.9em}
pre{background:var(--code);padding:.8rem 1rem;border-radius:6px;overflow-x:auto;
border:1px solid var(--line)}
pre code{background:none;padding:0;font-size:.85rem}
.hl{background:var(--code)}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;margin:1rem 0}
th,td{border:1px solid var(--line);padding:.35rem .6rem;text-align:left;vertical-align:top}
th{background:var(--side)}
.admonition{border-left:4px solid var(--accent);background:var(--side);
padding:.1rem 1rem;margin:1rem 0;border-radius:0 6px 6px 0}
.admonition-title{font-weight:700}
.pager{display:flex;justify-content:space-between;gap:1rem;margin-top:3rem;
border-top:1px solid var(--line);padding-top:1rem}
@media (max-width:48rem){.layout{flex-direction:column}
nav.side{width:auto;height:auto;position:static;border-right:0;border-bottom:1px solid var(--line)}
main{padding:1rem}}
"""


@dataclass
class Page:
    title: str
    source: Path
    url: str  # path of the output file relative to the site root, e.g. "faq.html"
    section: str
    html: str = ""
    ids: frozenset[str] = frozenset()


def load_pages(config: dict) -> tuple[str, list[Page]]:
    pages: list[Page] = []
    for section in config["section"]:
        for entry in section["pages"]:
            source = (DOCS / entry["file"]).resolve()
            url = source.with_suffix(".html").name
            if source.parent == ROOT:
                url = source.stem.lower() + ".html"
            pages.append(Page(entry["title"], source, url, section["title"]))
    return config["title"], pages


def render(page: Page, errors: list[str]) -> None:
    md = markdown.Markdown(extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS)
    body = md.convert(page.source.read_text(encoding="utf-8"))
    page.html = re.sub(
        r"<table>(.*?)</table>",
        r'<div class="table-wrap"><table>\1</table></div>',
        body,
        flags=re.DOTALL,
    )
    page.ids = frozenset(re.findall(r'\sid="([^"]+)"', body))
    if not re.search(r"<h1[ >]", body):
        errors.append(f"{page.source.name}: no top-level heading")


def check_and_rewrite_links(pages: list[Page], errors: list[str]) -> None:
    by_source = {page.source: page for page in pages}
    link = re.compile(r'(<a\s[^>]*?href=")([^"]*)(")')

    def rewrite(page: Page, match: re.Match[str]) -> str:
        href = html.unescape(match.group(2))
        parts = urlsplit(href)
        if parts.scheme in ("http", "https", "mailto"):
            return match.group(0)
        if parts.scheme or parts.netloc:
            errors.append(f"{page.source.name}: unsupported link {href!r}")
            return match.group(0)
        if not parts.path:
            target, fragment = page, parts.fragment
        else:
            resolved = (page.source.parent / unquote(parts.path)).resolve()
            target = by_source.get(resolved)
            if target is None:
                errors.append(
                    f"{page.source.name}: link {href!r} leaves the documentation or "
                    "points to a page that is not in docs/site.toml; use an absolute URL "
                    "for files outside it"
                )
                return match.group(0)
            fragment = parts.fragment
        if fragment and fragment not in target.ids:
            errors.append(
                f"{page.source.name}: no anchor #{fragment} in {target.source.name}"
            )
        new = "" if target is page and not fragment else target.url
        if fragment:
            new += "#" + fragment
        return f"{match.group(1)}{html.escape(new or '#')}{match.group(3)}"

    for page in pages:
        page.html = link.sub(lambda m, p=page: rewrite(p, m), page.html)


def layout(site_title: str, pages: list[Page], index: int) -> str:
    page = pages[index]
    nav: list[str] = []
    current_section = None
    for other in pages:
        if other.section != current_section:
            if current_section is not None:
                nav.append("</ul>")
            current_section = other.section
            nav.append(f"<h2>{html.escape(current_section)}</h2><ul>")
        css = ' class="current"' if other is page else ""
        nav.append(
            f'<li><a{css} href="{other.url}">{html.escape(other.title)}</a></li>'
        )
    nav.append("</ul>")
    pager = []
    if index > 0:
        prev = pages[index - 1]
        pager.append(f'<a href="{prev.url}">&larr; {html.escape(prev.title)}</a>')
    else:
        pager.append("<span></span>")
    if index + 1 < len(pages):
        nxt = pages[index + 1]
        pager.append(f'<a href="{nxt.url}">{html.escape(nxt.title)} &rarr;</a>')
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(page.title)} - {html.escape(site_title)}</title>"
        '<link rel="stylesheet" href="style.css"></head><body><div class="layout">'
        f'<nav class="side"><a class="brand" href="{pages[0].url}">{html.escape(site_title)}</a>'
        + "".join(nav)
        + f'</nav><main>{page.html}<div class="pager">{"".join(pager)}</div></main>'
        "</div></body></html>\n"
    )


def build(out: Path, strict: bool) -> int:
    config = tomllib.loads((DOCS / "site.toml").read_text(encoding="utf-8"))
    site_title, pages = load_pages(config)
    errors: list[str] = []
    listed = {page.source for page in pages}
    for path in sorted(DOCS.glob("*.md")):
        if path.resolve() not in listed:
            errors.append(f"{path.name} is not in docs/site.toml")
    for page in pages:
        if not page.source.is_file():
            errors.append(f"{page.source} does not exist")
    if errors:
        return report(errors, strict)
    for page in pages:
        render(page, errors)
    check_and_rewrite_links(pages, errors)
    out.mkdir(parents=True, exist_ok=True)
    pygments_css = HtmlFormatter(style="default", cssclass="hl").get_style_defs(".hl")
    dark = HtmlFormatter(style="github-dark", cssclass="hl").get_style_defs(".hl")
    (out / "style.css").write_text(
        CSS + pygments_css + "\n@media (prefers-color-scheme:dark){\n" + dark + "\n}\n",
        encoding="utf-8",
    )
    for index, page in enumerate(pages):
        (out / page.url).write_text(layout(site_title, pages, index), encoding="utf-8")
    print(f"built {len(pages)} pages into {out}")
    return report(errors, strict)


def report(errors: list[str], strict: bool) -> int:
    for message in errors:
        print(("error: " if strict else "warning: ") + message, file=sys.stderr)
    return 1 if errors and strict else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    return build(args.out, args.strict)


if __name__ == "__main__":
    sys.exit(main())
