"""Acrobat-style signature appearance: signer's name in cursive on the left
(over a faint seal), signing details on the right."""
import io
import os
from dataclasses import dataclass, field
from itertools import combinations

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.font.basic import SimpleFontEngineFactory
from pyhanko.pdf_utils.layout import (AxisAlignment, BoxConstraints, InnerScaling,
                                      Margins, Positioning, SimpleBoxLayoutRule)
from pyhanko.pdf_utils.text import TextBox, TextBoxStyle
from pyhanko.stamp.art import STAMP_ART_CONTENT
from pyhanko.stamp.text import TextStamp, TextStampStyle

import dsc_config

# The cursive font is taken from the user's own macOS install at run time and is
# not redistributed with this app.
FONT_DIR = os.path.join(dsc_config.APP_DIR, "fonts")
SUPP = "/System/Library/Fonts/Supplemental"
# (display name, source file, face index inside a .ttc or None)
# Only fonts that pyHanko's OpenType engine lays out correctly (Apple Chancery,
# Brush Script and Savoye come out with broken letter spacing).
CURSIVE_FONTS = {
    "Snell Roundhand": (f"{SUPP}/SnellRoundhand.ttc", 0),   # fsType 0: embedding allowed
}
DEFAULT_CURSIVE = "Snell Roundhand"
NAME_CHAR_W = 0.45        # rough average glyph width (em) of the script fonts, for wrapping
NAME_LEADING = 1.05


def cursive_font_file(name=DEFAULT_CURSIVE):
    """Return a .ttf path for the cursive font (extracting it from a .ttc if needed)."""
    for n in [name] + [k for k in CURSIVE_FONTS if k != name]:
        src, idx = CURSIVE_FONTS.get(n, (None, None))
        if not src or not os.path.exists(src):
            continue
        if idx is None:
            return src
        out = os.path.join(FONT_DIR, n.replace(" ", "") + ".ttf")
        if not os.path.exists(out):
            from fontTools.ttLib import TTCollection
            os.makedirs(FONT_DIR, exist_ok=True)
            TTCollection(src).fonts[idx].save(out)
        return out
    return None


def display_name(cn):
    """'JOHN A SMITH' -> 'John A Smith' (caps look bad in script)."""
    if cn and cn.isupper():
        return " ".join(w.capitalize() for w in cn.split())
    return cn or ""


def wrap_name(name, width, height):
    """Split the name over 1-3 lines, choosing whichever gives the largest text."""
    words = name.split()
    best = (0, name)
    for n in range(1, min(3, len(words)) + 1):
        for cuts in combinations(range(1, len(words)), n - 1):
            idx = (0,) + cuts + (len(words),)
            lines = [" ".join(words[a:b]) for a, b in zip(idx, idx[1:])]
            longest = max(len(s) for s in lines)
            size = min(width / (longest * NAME_CHAR_W), height / (n * NAME_LEADING))
            if size > best[0]:
                best = (size, "\n".join(lines))
    return best[1]


@dataclass(frozen=True)
class SplitStampStyle(TextStampStyle):
    name_text: str = ""
    name_font_file: str | None = None
    name_font_size: int = 28
    split: float = 0.46          # fraction of the width used by the name column

    def create_stamp(self, writer, box, text_params):
        return SplitStamp(writer=writer, style=self, box=box, text_params=text_params)


class SplitStamp(TextStamp):
    def _render_inner_content(self):
        st = self.style
        W, H = self.box.width, self.box.height
        lw = W * st.split if st.name_text else 0
        cmds = [b"q"]

        if st.name_text:
            if st.name_font_file:
                from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory
                font = GlyphAccumulatorFactory(st.name_font_file)
            else:
                font = SimpleFontEngineFactory("Times-Italic", 0.45)
            size = st.name_font_size
            tb = TextBox(TextBoxStyle(font=font, font_size=size, leading=int(size * NAME_LEADING)),
                         writer=self.writer, resources=self.resources, box=None, font_name="F2")
            tb.content = st.name_text
            name_cmds = tb.render()
            pos = SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MID, y_align=AxisAlignment.ALIGN_MID,
                margins=Margins.uniform(4), inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
            ).fit(BoxConstraints(width=lw, height=H), tb.box.width, tb.box.height)
            cmds += [b"q", pos.as_cm(), name_cmds, b"Q"]

        det_cmds, (dw, dh) = self._inner_layout_natural_size()
        pos = SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MIN, y_align=AxisAlignment.ALIGN_MID,
            margins=Margins(left=4 if lw else 6, right=4, top=4, bottom=4),
            inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
        ).fit(BoxConstraints(width=W - lw, height=H), dw, dh)
        pos = Positioning(x_scale=pos.x_scale, y_scale=pos.y_scale,
                          x_pos=pos.x_pos + lw, y_pos=pos.y_pos)
        cmds += [b"q", pos.as_cm(), *det_cmds, b"Q", b"Q"]
        return cmds


def make_style(box_w, box_h, signer_cn, show_reason, show_location,
               cursive=True, seal=True, font=DEFAULT_CURSIVE):
    lines = ["Digitally signed by", "%(signer)s", "Date: %(ts)s"]
    if show_reason:
        lines.append("Reason: %(reason)s")
    if show_location:
        lines.append("Location: %(location)s")
    name = display_name(signer_cn) if cursive else ""
    split = 0.46 if name else 0.0
    lw = box_w * split
    if name:
        name = wrap_name(name, lw - 8, box_h - 8)
    kw = {}
    if seal:
        # seal centred in the name column (or the whole box when there's no name)
        kw = dict(
            background=STAMP_ART_CONTENT,
            background_opacity=0.22,
            background_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MID, y_align=AxisAlignment.ALIGN_MID,
                margins=Margins(left=2, right=int(box_w - lw) + 2 if name else 2, top=3, bottom=3),
                inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
            ),
        )
    else:
        kw = dict(background=None)
    return SplitStampStyle(
        stamp_text="\n".join(lines),
        text_box_style=TextBoxStyle(font=SimpleFontEngineFactory("Helvetica", 0.56),
                                    font_size=10, leading=12),
        timestamp_format="%d-%m-%Y %H:%M:%S %z",
        border_width=1,
        name_text=name,
        name_font_file=cursive_font_file(font) if name else None,
        split=split,
        **kw,
    )


def render_preview(box_w, box_h, style, text_params, scale=2.0):
    """Render a stamp style to a PIL image (for on-screen previews)."""
    from pyhanko.pdf_utils.writer import PageObject, PdfFileWriter
    import pypdfium2 as pdfium

    w = PdfFileWriter()
    contents = w.add_object(generic.StreamObject(stream_data=b""))
    w.insert_page(PageObject(contents=contents, media_box=[0, 0, box_w, box_h]))
    stamp = style.create_stamp(w, BoxConstraints(width=box_w, height=box_h), text_params)
    stamp.apply(0, 0, 0)
    buf = io.BytesIO()
    w.write(buf)
    doc = pdfium.PdfDocument(buf.getvalue())
    try:
        return doc[0].render(scale=scale).to_pil()
    finally:
        doc.close()
