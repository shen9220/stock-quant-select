"""Embed V3 source assets in the legacy single-file Flask/Pages template.
No data export, commit, or publish is performed.
"""
from pathlib import Path


def build(root: Path) -> None:
    directory = root / 'templates'
    target = directory / 'index.html'
    html = target.read_text(encoding='utf-8')
    css = (directory / 'v3-dashboard.css').read_text(encoding='utf-8')
    js = (directory / 'v3-dashboard.js').read_text(encoding='utf-8')
    assert '{#' not in css and '{{' not in js, 'Jinja delimiter in static source'
    start = html.index('<style id="v3-dashboard-style">')
    end = html.index('</style>', start) + len('</style>')
    html = html[:start] + '<style id="v3-dashboard-style">\n' + css + '\n</style>' + html[end:]
    start = html.index('    /* V3-DASHBOARD-BEGIN */')
    end = html.index('    /* V3-DASHBOARD-END */', start) + len('    /* V3-DASHBOARD-END */')
    html = html[:start] + '    /* V3-DASHBOARD-BEGIN */\n' + js + '\n    /* V3-DASHBOARD-END */' + html[end:]
    target.write_text(html, encoding='utf-8')


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[1])
