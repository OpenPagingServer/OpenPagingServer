import html
import re

from srv.web.app import ACKNOWLEDGMENTS_PATH, Response, abort, legacy_user_context, require_admin


_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s<>]+")
_LIST_ITEM_RE = re.compile(r"^\s*(?:(\d+)\.|[-*+])\s+(.*)$")


def _render_inline(text):
    parts = str(text or "").split("`")
    rendered = []
    for index, part in enumerate(parts):
        if index % 2:
            rendered.append(f"<code>{html.escape(part)}</code>")
            continue
        cursor = 0
        for match in _URL_RE.finditer(part):
            url = match.group(0)
            trailing = ""
            while url and url[-1] in ".,;:":
                trailing = url[-1] + trailing
                url = url[:-1]
            rendered.append(html.escape(part[cursor:match.start()]))
            rendered.append(
                f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{html.escape(url)}</a>{html.escape(trailing)}'
            )
            cursor = match.end()
        rendered.append(html.escape(part[cursor:]))
    return "".join(rendered)


def _render_blocks(lines):
    output = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        list_match = _LIST_ITEM_RE.match(lines[index])
        if list_match:
            ordered = bool(list_match.group(1))
            tag = "ol" if ordered else "ul"
            items = []
            current = ""
            while index < len(lines) and lines[index].strip():
                match = _LIST_ITEM_RE.match(lines[index])
                if match and bool(match.group(1)) == ordered:
                    if current:
                        items.append(current)
                    current = match.group(2).strip()
                elif current:
                    current += " " + lines[index].strip()
                else:
                    break
                index += 1
            if current:
                items.append(current)
            output.append(f"<{tag}>" + "".join(f"<li>{_render_inline(item)}</li>" for item in items) + f"</{tag}>")
            continue
        paragraph = []
        while index < len(lines) and lines[index].strip() and not _LIST_ITEM_RE.match(lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        output.append(f"<p>{_render_inline(' '.join(paragraph))}</p>")
    return "".join(output)


def render_acknowledgments(markdown):
    """Render the file in source order, using cards only for level-three entries."""
    markdown = _COMMENT_RE.sub("", str(markdown or ""))
    output = []
    pending = []
    card_open = False
    grid_open = False

    def flush_pending():
        if pending:
            output.append(_render_blocks(pending))
            pending.clear()

    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_pending()
            level = len(heading.group(1))
            if level <= 2:
                if card_open:
                    output.append("</article>")
                    card_open = False
                if grid_open:
                    output.append("</div>")
                    grid_open = False
            elif level == 3:
                if card_open:
                    output.append("</article>")
                    card_open = False
                if not grid_open:
                    output.append('<div class="card-grid">')
                    grid_open = True
                output.append('<article class="card">')
                card_open = True
            output.append(f"<h{level}>{_render_inline(heading.group(2).strip())}</h{level}>")
        else:
            pending.append(line)
    flush_pending()
    if card_open:
        output.append("</article>")
    if grid_open:
        output.append("</div>")
    return "".join(output)


PAGE_STYLE = r"""
:root{color-scheme:light dark}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:"Roboto",Arial,sans-serif;background:#fff;color:#222}
main{width:min(900px,100%);margin:0 auto;padding:28px 24px 48px}
.logo{display:block;width:min(420px,82vw);height:auto;margin:0 auto 30px}
.logo-dark{display:none}
h1{font-size:2rem;font-weight:500;margin:0 0 24px}
h2{font-size:1.5rem;font-weight:500;margin:34px 0 14px}
h3{font-size:1.15rem;font-weight:500;margin:26px 0 10px}
h4,h5,h6{font-weight:500}
.card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(360px,100%),1fr));gap:18px;align-items:start}
.card{min-width:0;padding:0 20px 18px;border:1px solid #ddd;border-radius:10px;background:#fff;box-shadow:0 2px 7px rgba(0,0,0,.08);overflow:hidden}
.card h3{margin:0 -20px 16px;padding:15px 20px;border-bottom:1px solid #e5e5e5;background:#f5f5f5}
.card p:last-child,.card ul:last-child,.card ol:last-child{margin-bottom:0}
p,li{line-height:1.6}
p{margin:0 0 14px}
ul,ol{margin:0 0 16px;padding-left:28px}
a{color:#a94731;overflow-wrap:anywhere}
code{padding:2px 4px;border-radius:3px;background:#eee;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
html[data-theme="dark"] body{background:#121212;color:#e0e0e0}
html[data-theme="dark"] .logo-light{display:none}
html[data-theme="dark"] .logo-dark{display:block}
html[data-theme="dark"] a{color:#e7907b}
html[data-theme="dark"] code{background:#303030}
html[data-theme="dark"] .card{background:#1e1e1e;border-color:#3a3a3a;box-shadow:0 2px 8px rgba(0,0,0,.3)}
html[data-theme="dark"] .card h3{background:#292929;border-bottom-color:#3a3a3a}
@media(prefers-color-scheme:dark){
html[data-theme="system"] body{background:#121212;color:#e0e0e0}
html[data-theme="system"] .logo-light{display:none}
html[data-theme="system"] .logo-dark{display:block}
html[data-theme="system"] a{color:#e7907b}
html[data-theme="system"] code{background:#303030}
html[data-theme="system"] .card{background:#1e1e1e;border-color:#3a3a3a;box-shadow:0 2px 8px rgba(0,0,0,.3)}
html[data-theme="system"] .card h3{background:#292929;border-bottom-color:#3a3a3a}
}
"""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if not ACKNOWLEDGMENTS_PATH.is_file():
        abort(404)

    ctx = legacy_user_context(user)
    preferences = ctx.get("user") if isinstance(ctx.get("user"), dict) else {}
    theme = str(preferences.get("theme") or "system").strip().lower()
    if theme not in {"system", "light", "dark"}:
        theme = "system"
    markdown_html = render_acknowledgments(ACKNOWLEDGMENTS_PATH.read_text(encoding="utf-8"))
    document = f"""<!DOCTYPE html>
<html lang="en" data-theme="{theme}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acknowledgments</title>
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&amp;display=swap" rel="stylesheet">
<style>{PAGE_STYLE}</style>
</head>
<body>
<main>
<img src="/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png" class="logo logo-light" alt="Open Paging Server">
<img src="/assets/OPENPAGINGSERVER-768x576-DARKMODE.png" class="logo logo-dark" alt="Open Paging Server">
{markdown_html}
</main>
</body>
</html>"""
    return Response(document, mimetype="text/html")
