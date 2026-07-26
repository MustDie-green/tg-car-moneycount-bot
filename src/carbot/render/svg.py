"""Мелкие строительные блоки SVG.

Никаких шаблонизаторов: картинка собирается из строк, поэтому её легко
отлаживать — сохранил .svg, открыл в браузере, посмотрел.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

FONT = "Inter"


def esc(value: str) -> str:
    return escape(str(value))


def num(value: float) -> str:
    """Короткая запись координат: 12.0 → «12», 12.345 → «12.35»."""
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def text(
    x: float,
    y: float,
    content: str,
    *,
    size: float = 20,
    weight: int = 400,
    fill: str = "#fff",
    anchor: str = "start",
    opacity: float | None = None,
    tracking: float | None = None,
) -> str:
    attrs = [
        f'x="{num(x)}"',
        f'y="{num(y)}"',
        f'font-family="{FONT}"',
        f'font-size="{num(size)}"',
        f'font-weight="{weight}"',
        f'fill="{fill}"',
    ]
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if opacity is not None:
        attrs.append(f'opacity="{num(opacity)}"')
    if tracking is not None:
        attrs.append(f'letter-spacing="{num(tracking)}"')
    return f"<text {' '.join(attrs)}>{esc(content)}</text>"


def rect(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    rx: float = 0,
    fill: str = "none",
    opacity: float | None = None,
    stroke: str | None = None,
    stroke_width: float = 1,
) -> str:
    attrs = [
        f'x="{num(x)}"',
        f'y="{num(y)}"',
        f'width="{num(max(width, 0))}"',
        f'height="{num(max(height, 0))}"',
        f'fill="{fill}"',
    ]
    if rx:
        attrs.append(f'rx="{num(rx)}"')
    if opacity is not None:
        attrs.append(f'opacity="{num(opacity)}"')
    if stroke:
        attrs.append(f'stroke="{stroke}" stroke-width="{num(stroke_width)}"')
    return f"<rect {' '.join(attrs)}/>"


def circle(cx: float, cy: float, r: float, *, fill: str, opacity: float | None = None) -> str:
    extra = f' opacity="{num(opacity)}"' if opacity is not None else ""
    return f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r)}" fill="{fill}"{extra}/>'


def path(d: str, *, fill: str = "none", stroke: str | None = None, width: float = 2) -> str:
    attrs = [f'd="{d}"', f'fill="{fill}"']
    if stroke:
        attrs.append(
            f'stroke="{stroke}" stroke-width="{num(width)}" '
            'stroke-linecap="round" stroke-linejoin="round"'
        )
    return f"<path {' '.join(attrs)}/>"


def group(content: str, *, transform: str | None = None, opacity: float | None = None) -> str:
    attrs = []
    if transform:
        attrs.append(f'transform="{transform}"')
    if opacity is not None:
        attrs.append(f'opacity="{num(opacity)}"')
    joined = (" " + " ".join(attrs)) if attrs else ""
    return f"<g{joined}>{content}</g>"


def clip(clip_id: str, shape: str, content: str) -> str:
    return (
        f'<clipPath id="{clip_id}">{shape}</clipPath>'
        f'<g clip-path="url(#{clip_id})">{content}</g>'
    )


def image(x: float, y: float, size: float, data_uri: str) -> str:
    return (
        f'<image x="{num(x)}" y="{num(y)}" width="{num(size)}" height="{num(size)}" '
        f'href="{data_uri}" preserveAspectRatio="xMidYMid slice"/>'
    )


def document(width: float, height: float, defs: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{num(width)}" height="{num(height)}" '
        f'viewBox="0 0 {num(width)} {num(height)}">'
        f"<defs>{defs}</defs>{body}</svg>"
    )
