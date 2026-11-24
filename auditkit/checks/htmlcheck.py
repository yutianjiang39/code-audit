"""HTML 结构与无障碍静态检查（标准库 html.parser，不执行脚本、不渲染）。

只看得到写在 HTML 里的结构：脚本运行后才生成的节点、CSS 造成的不可见、对比度等要靠 Lighthouse（axe-core）在真实页面上查。
属性值里有模板语法（Jinja / Django / ERB / JS 模板串）时，值在渲染后才确定，一律按“未知”处理，不报。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
TEMPLATE = re.compile(r"\{\{|\{%|\{#|<%|\$\{")
# 页面是模板片段（继承 / 包含别的文件）时，被引用的 id 可能在别的文件里，跨元素引用类规则不报
PARTIAL = re.compile(r"\{%-?\s*(extends|include|block|import)\b|<%-?\s*(include|render)\b|\{\{>")
FIELD_TYPES_BUTTON = {"submit", "reset", "button"}


class El:
    __slots__ = ("tag", "attrs", "line", "parent", "chunks", "kids")

    def __init__(self, tag: str, attrs: dict[str, str], line: int, parent: "El | None"):
        self.tag, self.attrs, self.line, self.parent = tag, attrs, line, parent
        self.chunks: list[str] = []
        self.kids: list[El] = []

    def get(self, name: str) -> str:
        return (self.attrs.get(name) or "").strip()

    def unknown(self, name: str) -> bool:
        return bool(TEMPLATE.search(self.attrs.get(name) or ""))

    def text(self) -> str:
        return " ".join("".join(self.chunks).split())

    def ancestors(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    def descendants(self):
        todo = list(self.kids)
        while todo:
            e = todo.pop()
            yield e
            todo.extend(e.kids)


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.all: list[El] = []
        self.stack: list[El] = []

    def _open(self, tag: str, attrs, push: bool) -> None:
        parent = self.stack[-1] if self.stack else None
        el = El(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, self.getpos()[0], parent)
        if parent is not None:
            parent.kids.append(el)
        self.all.append(el)
        if push and tag not in VOID:
            self.stack.append(el)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, True)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, False)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.stack or self.stack[-1].tag in ("script", "style", "template"):
            return
        for el in self.stack:
            el.chunks.append(data)


def parse(text: str) -> list[El]:
    t = _Tree()
    try:
        t.feed(text)
        t.close()
    except Exception:  # noqa: BLE001 残缺到解析器放弃的文件：用已经解析出的部分
        pass
    return t.all


def _hidden(el: El) -> bool:
    return el.get("aria-hidden") == "true" or "hidden" in el.attrs or el.get("role") in ("presentation", "none")


class _Page:
    def __init__(self, text: str):
        self.els = parse(text)
        self.partial = bool(PARTIAL.search(text))
        self.ids: dict[str, list[El]] = {}
        for e in self.els:
            if e.get("id") and not e.unknown("id"):
                self.ids.setdefault(e.get("id"), []).append(e)
        self.label_for = {e.get("for") for e in self.els if e.tag == "label" and e.get("for") and self.own_name(e)}  # 空 label 不算
        self.any_unknown_for = any(e.tag == "label" and e.unknown("for") for e in self.els)
        self.out: list[dict] = []

    def add(self, rule, sev, el, message, cause, fix, code=""):
        self.out.append({"rule": rule, "severity": sev, "line": el.line if el is not None else 1, "message": message,
                         "cause": cause, "fix": fix, "fix_code": code})

    # ---- 可访问名称
    def labelledby(self, el: El) -> str:
        """'ok' 引用到了有文字的元素；'empty' 引用存在但没有文字；'missing' 引用的 id 不存在；'' 没写。"""
        if "aria-labelledby" not in el.attrs:
            return ""
        if el.unknown("aria-labelledby"):
            return "ok"
        refs = el.get("aria-labelledby").split()
        found = [self.ids[r][0] for r in refs if r in self.ids]
        if not found:
            return "ok" if self.partial else "missing"
        return "ok" if any(self.own_name(f) for f in found) else "empty"

    def own_name(self, el: El) -> bool:
        """元素自己的文字内容：文本、aria-label、内部图片的 alt。"""
        if el.text() or el.get("aria-label") or el.unknown("aria-label"):
            return True
        for d in el.descendants():
            if d.get("aria-label") or d.unknown("aria-label") or (d.tag == "img" and (d.get("alt") or d.unknown("alt"))):
                return True
            if d.tag == "input" and d.get("type").lower() == "image" and d.get("alt"):
                return True
        return False

    def named(self, el: El) -> bool:
        return self.own_name(el) or self.labelledby(el) == "ok"

    # ---- 规则
    def document(self) -> None:
        html = next((e for e in self.els if e.tag == "html"), None)
        head = next((e for e in self.els if e.tag == "head"), None)
        if html is not None and not html.get("lang") and not html.unknown("lang"):
            self.add("html-lang", "low", html, "<html> 没有 lang 属性", "读屏软件无法判断朗读语言，翻译工具也会误判。",
                     "加上页面语言。", '<html lang="zh-CN">')
        if head is None:
            return
        metas = [e for e in self.els if e.tag == "meta"]
        if not any(m.get("name").lower() == "viewport" for m in metas) and not self.partial:
            self.add("viewport", "medium", head, "没有 viewport meta", "手机上按桌面宽度渲染，文字极小。", "加 viewport 声明。",
                     '<meta name="viewport" content="width=device-width, initial-scale=1">')
        for m in metas:
            if m.get("name").lower() == "viewport" and re.search(r"user-scalable\s*=\s*(no|0)|maximum-scale\s*=\s*1(\.0*)?\b", m.get("content"), re.I):
                self.add("viewport-zoom-disabled", "medium", m, "viewport 禁止了缩放", "低视力用户无法放大页面（WCAG 1.4.4）。",
                         "去掉 user-scalable=no 和 maximum-scale=1。", '<meta name="viewport" content="width=device-width, initial-scale=1">')
        titles = [e for e in self.els if e.tag == "title" and not any(a.tag == "svg" for a in e.ancestors())]
        if not any(t.text() for t in titles) and not self.partial:
            self.add("document-title", "medium", head, "页面没有 <title>（或为空）", "读屏软件进入页面先读标题；浏览器标签页、历史记录和搜索结果也靠它。",
                     "在 <head> 里写一个能说明本页内容的标题。", "<title>页面名称 - 站点名称</title>")

    def images(self) -> None:
        for e in self.els:
            if e.tag == "img" and "alt" not in e.attrs and not _hidden(e) and not self.named(e):
                self.add("img-alt", "medium", e, "图片没有 alt", "读屏用户不知道图片内容，图片加载失败时也没有替代文字。",
                         "有意义的图片写描述，装饰图写 alt=\"\"。", '<img src="…" alt="图片说明">')
            elif e.tag == "input" and e.get("type").lower() == "image" and not e.get("alt") and not e.unknown("alt") and not self.named(e):
                self.add("img-alt", "medium", e, "图片按钮（input type=image）没有 alt", "读屏软件只会读出“按钮”，不知道它做什么。",
                         "用 alt 写出按钮的作用。", '<input type="image" src="…" alt="搜索">')

    def fields(self) -> None:
        for e in self.els:
            if e.tag not in ("input", "select", "textarea") or _hidden(e):
                continue
            typ = e.get("type").lower() if e.tag == "input" else ""
            if typ in ("hidden", "image") or typ in FIELD_TYPES_BUTTON or e.unknown("type"):
                continue
            if e.get("aria-label") or e.unknown("aria-label"):
                continue
            if self.labelledby(e):  # 引用无效的情况由 aria-labelledby-invalid 报，不重复
                continue
            eid = e.get("id")
            if (eid and eid in self.label_for) or e.unknown("id") or (self.any_unknown_for and (eid or e.unknown("id"))):
                continue
            if any(a.tag == "label" for a in e.ancestors()):
                continue
            what = {"select": "下拉框", "textarea": "文本域"}.get(e.tag, "输入框")
            ident = f"（id=\"{eid}\"）" if eid else f"（name=\"{e.get('name')}\"）" if e.get("name") else ""
            if e.get("title"):
                self.add("input-label", "low", e, f"{what}{ident}只靠 title 提供名称",
                         "title 能被读屏软件读到，但屏幕上不显示、触屏设备上看不到，点击文字也无法聚焦控件。",
                         "换成可见的 <label for>，title 只留作补充提示。")
                continue
            why = "只有 id 不算标签：id 只是让 <label for> 能指向它，页面里没有任何 label 的 for 等于这个 id。" if eid else \
                  "读屏用户不知道该填什么，点击文字也无法聚焦控件。"
            if e.get("placeholder"):
                why += " placeholder 不是标签：一输入就消失，多数读屏软件也不把它当名称。"
            name = eid or e.get("name") or "field"
            self.add("input-label", "medium", e, f"{what}{ident}没有可访问名称（没有关联的 label、aria-label 或有效的 aria-labelledby）",
                     why, "用 <label for> 关联（首选），或包在 <label> 里，或加 aria-label。",
                     f'<label for="{name}">字段名称</label>\n<{e.tag} id="{name}" …>')

    def references(self) -> None:
        for e in self.els:
            if self.labelledby(e) == "missing":
                self.add("aria-labelledby-invalid", "medium", e,
                         f"aria-labelledby 指向的 id 不存在：{e.get('aria-labelledby')[:60]}",
                         "引用落空时这个属性等于没写，元素没有名称；常见于改了 id 忘了同步。", "改成页面里真实存在、且有文字内容的元素的 id。")
            elif self.labelledby(e) == "empty":
                self.add("aria-labelledby-invalid", "medium", e,
                         f"aria-labelledby 指向的元素没有文字内容：{e.get('aria-labelledby')[:60]}",
                         "被引用的元素是空的，算出来的名称也是空的。", "让被引用的元素有文字，或改用 aria-label。")
        if not self.partial:
            for e in self.els:
                if e.tag == "label" and e.get("for") and not e.unknown("for") and e.get("for") not in self.ids:
                    self.add("label-for-missing", "medium", e, f"<label for=\"{e.get('for')[:40]}\"> 指向的 id 不存在",
                             "这个 label 没有关联到任何控件：读屏软件不会把它读成控件名称，点击它也不会聚焦。",
                             "让 for 的值等于对应控件的 id。")
        for i, els in self.ids.items():
            if len(els) > 1:
                self.add("duplicate-id", "medium", els[1], f"id=\"{i[:40]}\" 在页面里出现了 {len(els)} 次（首次在第 {els[0].line} 行）",
                         "id 必须唯一：label for、aria-labelledby、锚点跳转和 getElementById 都只会命中第一个。", "改成各不相同的 id。")

    def controls(self) -> None:
        for e in self.els:
            if _hidden(e):
                continue
            typ = e.get("type").lower()
            role = e.get("role").lower()
            if e.tag == "button" or (role == "button" and e.tag not in ("input",)):
                if not self.named(e) and not e.get("title"):
                    self.add("button-name", "medium", e, "按钮没有可访问名称（没有文字、aria-label，内部图片也没有 alt）",
                             "读屏软件只会读出“按钮”，用户不知道它做什么；纯图标按钮最常见。",
                             "写上文字，或给图标按钮加 aria-label。", '<button type="button" aria-label="关闭">…</button>')
            elif e.tag == "input" and typ == "button":
                if not (e.get("value") or e.unknown("value") or self.named(e) or e.get("title")):
                    self.add("button-name", "medium", e, "input type=button 没有 value，也没有 aria-label",
                             "这种按钮的名称来自 value；空着时读屏软件只读出“按钮”。", "用 value 写出按钮的作用。",
                             '<input type="button" value="保存">')
            elif e.tag == "a" and "href" in e.attrs:
                if not self.named(e) and not e.get("title"):
                    self.add("link-name", "medium", e, "链接没有可访问名称（没有文字，内部图片也没有 alt）",
                             "读屏软件只能读出链接地址；按链接列表导航时无法分辨。", "写上链接文字，或给图标链接加 aria-label。",
                             '<a href="…" aria-label="返回首页">…</a>')
            elif e.tag in ("iframe", "frame"):
                if not (e.get("title") or e.unknown("title") or e.get("aria-label") or self.labelledby(e) == "ok"):
                    self.add("iframe-title", "medium", e, "iframe 没有 title", "读屏用户不知道这个内嵌框架里是什么，无法决定进入还是跳过。",
                             "用 title 说明内嵌内容。", '<iframe src="…" title="订单地图"></iframe>')
            ti = e.get("tabindex")
            if re.fullmatch(r"\+?\d+", ti) and int(ti) > 0:
                self.add("positive-tabindex", "low", e, f"tabindex=\"{ti}\" 是正数",
                         "正数 tabindex 会让它排到所有普通元素之前，Tab 顺序和视觉顺序对不上。",
                         "用 tabindex=\"0\" 加入自然顺序，靠调整 DOM 顺序控制先后。")

    def groups(self) -> None:
        by: dict[tuple[str, str], list[El]] = {}
        for e in self.els:
            typ = e.get("type").lower()
            if e.tag == "input" and typ in ("radio", "checkbox") and e.get("name") and not e.unknown("name") and not _hidden(e):
                by.setdefault((typ, e.get("name")), []).append(e)
        for (typ, name), els in by.items():
            if len(els) < 2:
                continue
            first = els[0]
            ok = False
            for a in first.ancestors():
                if a.tag == "fieldset" and any(k.tag == "legend" and k.text() for k in a.descendants()):
                    ok = True
                elif a.get("role").lower() in ("group", "radiogroup") and (a.get("aria-label") or self.labelledby(a) == "ok"):
                    ok = True
            if not ok:
                kind = "单选" if typ == "radio" else "复选"
                self.add("fieldset-legend", "low", first, f"{len(els)} 个同名{kind}框（name=\"{name[:40]}\"）没有分组名称",
                         "每个选项各有标签，但这一组在问什么没有被关联起来；读屏用户只听到“是 / 否”，不知道问题。",
                         "用 <fieldset> 包起来，<legend> 写问题；或用 role=\"group\" + aria-labelledby。",
                         "<fieldset>\n  <legend>这一组选项的问题</legend>\n  …\n</fieldset>")


def check_html(text: str) -> list[dict]:
    """返回命中列表，每项：rule、severity、line、message、cause、fix、fix_code。"""
    page = _Page(text)
    page.document()
    page.images()
    page.fields()
    page.references()
    page.controls()
    page.groups()
    return sorted(page.out, key=lambda h: h["line"])
