"""
tests/table_fit.py
==================
帳票の台帳（`<table>`）の**最小幅**を、同じ文書に埋まっている CSS を解いて測る。

**何のために在るか**（B-145）:
  台帳の `td` は既定で `white-space:nowrap` で、そこへ自由文（ERROR 行の理由）を
  流し込むと **折り返せない 1 行が `table-layout:auto` の表全体を押し広げる**。
  結果、右端の列が A4 の印字域の外へ出る。**「クラスが入っているか」を見る検査は
  この欠陥をそのまま通す**（nowrap が残っていても字は入っている）ので、
  ここでは*幅そのもの*を測る。

⚠️ **絶対値（182mm に収まるか）は測らない。**
  字の実寸は機械のフォントでしか出ない（[[I-118]] で実際に CI が落ちた）。
  ここで使うのは近似の字幅モデルなので、絶対値を閾値にすると*帳票ではなく
  モデルを検査する*ことになる。代わりに **増分**で見る＝
  「理由の文字列を 20 倍に伸ばしても、表の最小幅は変わらない」。
  折り返さない実装ではこの差が数千 px 開き、折り返す実装では 0 になる。
  近似の誤差は両辺で打ち消えるので、モデルの精度に結果が依存しない。

対応しているのは、この 2 つの帳票が実際に使っている範囲だけ:
  - 子孫結合子（空白）だけのセレクタ／タグ＋クラスの複合セレクタ
  - `white-space` / `overflow-wrap` / `word-break` / `font-size` の解決
  - `@media` などの at 規則の中は**見ない**（印刷専用の上書きは対象外）
"""

import re

# ------------------------------------------------------------
# 字幅モデル（em 単位・相対比較にしか使わない＝上の ⚠️ を参照）
# ------------------------------------------------------------
_EM_ASCII = 0.55        # 欧文の平均字幅
_EM_WIDE  = 1.0         # 全角（日本語の理由文が入り得る）

# セルの左右パディング＋縦罫線（report_summary / report_multihop で 4px + 4px + 1px）
_CELL_CHROME_PX = 9.0


def _char_em(ch: str) -> float:
    return _EM_WIDE if ord(ch) > 0x2E80 else _EM_ASCII


def _text_px(text: str, font_px: float) -> float:
    return sum(_char_em(c) for c in text) * font_px


# ------------------------------------------------------------
# ごく小さな CSS 解決器
# ------------------------------------------------------------
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_STYLE_RE   = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
_COMPOUND_RE = re.compile(r"^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)$")


class _Rule:
    __slots__ = ("parts", "decls", "order", "spec")

    def __init__(self, selector, decls, order):
        # 子孫結合子だけを解く（`>` や `:` を含むセレクタは対象外＝無視する）
        self.parts = [_parse_compound(p) for p in selector.split()]
        self.decls = decls
        self.order = order
        n_class = sum(len(cls) for _, cls in self.parts if cls is not None)
        n_type  = sum(1 for tag, _ in self.parts if tag)
        self.spec = (n_class, n_type)


def _parse_compound(part):
    m = _COMPOUND_RE.match(part)
    if not m:
        return (None, None)          # 解けない＝この規則は使わない
    tag = (m.group(1) or "").lower() or None
    classes = frozenset(c for c in m.group(2).split(".") if c)
    return (tag, classes)


def _split_top_level(css: str):
    """at 規則のブロックを飛ばしながら `セレクタ { 宣言 }` を拾う。"""
    out, i, n = [], 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            break
        selector = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        if not selector.startswith("@"):     # at 規則の中は見ない
            out.append((selector, css[brace + 1:j - 1]))
        i = j
    return out


def _parse_css(html: str):
    css = "".join(_STYLE_RE.findall(html))
    css = _COMMENT_RE.sub("", css)
    rules, order = [], 0
    for selector, body in _split_top_level(css):
        decls = {}
        for decl in body.split(";"):
            if ":" in decl:
                prop, _, val = decl.partition(":")
                decls[prop.strip().lower()] = val.strip().lower()
        for one in selector.split(","):
            one = one.strip()
            if one:
                rules.append(_Rule(one, decls, order))
                order += 1
    return rules


def _matches(rule, chain):
    """`chain`（[(tag, classes), …] ルート→対象）の末尾に対する子孫マッチ。"""
    parts = rule.parts
    if not parts or any(cls is None for _, cls in parts):
        return False
    if not _match_one(parts[-1], chain[-1]):
        return False
    i = len(parts) - 2
    j = len(chain) - 2
    while i >= 0:
        while j >= 0 and not _match_one(parts[i], chain[j]):
            j -= 1
        if j < 0:
            return False
        i -= 1
        j -= 1
    return True


def _match_one(part, element):
    tag, classes = part
    el_tag, el_classes = element
    if tag and tag != el_tag:
        return False
    return classes <= el_classes


def resolve(rules, chain, prop, default=None):
    """`chain` の末尾要素に効く `prop` の値（詳細度→出現順で決める）。"""
    best, best_key = default, None
    for rule in rules:
        if prop not in rule.decls or not _matches(rule, chain):
            continue
        key = (rule.spec, rule.order)
        if best_key is None or key > best_key:
            best, best_key = rule.decls[prop], key
    return best


# ------------------------------------------------------------
# 表の最小幅
# ------------------------------------------------------------
_TABLE_RE = re.compile(r"<table[^>]*class=[\"']([^\"']*)[\"'][^>]*>(.*?)</table>", re.S | re.I)
_ROW_RE   = re.compile(r"<tr([^>]*)>(.*?)</tr>", re.S | re.I)
_CELL_RE  = re.compile(r"<(t[dh])([^>]*)>(.*?)</\1>", re.S | re.I)
_CLASS_RE = re.compile(r"class=[\"']([^\"']*)[\"']")
_SPAN_RE  = re.compile(r"colspan=[\"']?(\d+)")
_TAG_RE   = re.compile(r"<[^>]+>")


def _px(value, default):
    if not value:
        return default
    m = re.match(r"([\d.]+)px", value)
    return float(m.group(1)) if m else default


def _min_segment(text, white_space, wrap, word_break):
    """min-content 幅を決める「割れない最長の塊」。"""
    if white_space in ("nowrap", "pre"):
        return text
    if wrap == "anywhere" or word_break == "break-all":
        # どこでも割れる＝いちばん広い 1 字が下限（CSS の min-content の定義）
        return max(text, key=_char_em, default="")
    # `break-word` は min-content を縮めない＝空白で切れる最長語が下限
    tokens = text.split() or [""]
    return max(tokens, key=len)


def table_min_width_px(html: str, table_class: str,
                       ancestors=(("div", frozenset({"sheet"})),)) -> float:
    """`table.<table_class>` の最小幅（px）。列ごとの最小幅の総和。

    `colspan` のセルは、またぐ列へ均等に配る（ブラウザの近似）。
    """
    rules = _parse_css(html)
    body = None
    for cls, inner in _TABLE_RE.findall(html):
        if table_class in cls.split():
            body = inner
            break
    if body is None:
        raise AssertionError(f"table.{table_class} が見つからない")

    table_chain = list(ancestors) + [("table", frozenset(table_class.split()))]
    columns: dict[int, float] = {}
    for row_attrs, row_html in _ROW_RE.findall(body):
        row_classes = frozenset(_class_of(row_attrs))
        col = 0
        for tag, attrs, cell_html in _CELL_RE.findall(row_html):
            span = int(_SPAN_RE.search(attrs).group(1)) if _SPAN_RE.search(attrs) else 1
            chain = table_chain + [("tr", row_classes),
                                   (tag.lower(), frozenset(_class_of(attrs)))]
            text = _TAG_RE.sub("", cell_html).strip()
            font = _px(resolve(rules, chain, "font-size"), 9.0)
            seg = _min_segment(text,
                               resolve(rules, chain, "white-space", "normal"),
                               resolve(rules, chain, "overflow-wrap", "normal"),
                               resolve(rules, chain, "word-break", "normal"))
            width = _text_px(seg, font) + _CELL_CHROME_PX
            for k in range(span):
                share = width / span
                columns[col + k] = max(columns.get(col + k, 0.0), share)
            col += span
    return sum(columns.values())


def _class_of(attrs: str):
    m = _CLASS_RE.search(attrs)
    return m.group(1).split() if m else []
