"""HTML 结构检查：可访问名称必须来自真正的标签，不是 id。"""
from __future__ import annotations

import unittest

from auditkit.checks.htmlcheck import check_html


def page(body: str, head: str = "<title>t</title><meta name='viewport' content='width=device-width'>") -> str:
    return f"<!doctype html><html lang='zh-CN'><head>{head}</head><body>{body}</body></html>"


def rules(html: str) -> list[str]:
    return [h["rule"] for h in check_html(html)]


class InputLabel(unittest.TestCase):
    def test_id_alone_is_not_a_label(self) -> None:
        hits = check_html(page('<form>\n<input type="text" id="username">\n</form>'))
        self.assertEqual([h["rule"] for h in hits], ["input-label"])
        self.assertEqual(hits[0]["line"], 2)
        self.assertEqual(hits[0]["severity"], "medium")

    def test_name_and_placeholder_are_not_labels(self) -> None:
        self.assertEqual(rules(page('<input name="q" placeholder="搜索">')), ["input-label"])

    def test_accepted_ways_of_labelling(self) -> None:
        ok = {
            "label for": '<label for="u">用户名</label><input id="u">',
            "label for（标签在后）": '<input id="u"><label for="u">用户名</label>',
            "包裹的 label": "<label>用户名 <input></label>",
            "aria-label": '<input aria-label="用户名">',
            "aria-labelledby": '<span id="t">用户名</span><input aria-labelledby="t">',
            "多个 labelledby": '<span id="a">开始</span><span id="b">日期</span><input aria-labelledby="a b">',
            "select": '<label for="s">城市</label><select id="s"><option>a</option></select>',
            "textarea": "<label>备注<textarea></textarea></label>",
            "hidden": '<input type="hidden" name="csrf" value="x">',
            "submit 有 value": '<input type="submit" value="提交">',
            "image 有 alt": '<input type="image" src="go.png" alt="搜索">',
        }
        for name, body in ok.items():
            with self.subTest(name):
                self.assertEqual(rules(page(body)), [])

    def test_select_and_textarea_need_labels_too(self) -> None:
        self.assertEqual(rules(page('<select id="s"><option>a</option></select><textarea id="t"></textarea>')),
                         ["input-label", "input-label"])

    def test_empty_labels_do_not_count(self) -> None:
        self.assertIn("input-label", rules(page('<input aria-label="  ">')))
        self.assertIn("input-label", rules(page('<label for="u"></label><input id="u">')))

    def test_title_only_is_downgraded(self) -> None:
        hits = check_html(page('<input title="用户名">'))
        self.assertEqual([(h["rule"], h["severity"]) for h in hits], [("input-label", "low")])

    def test_broken_labelledby_is_reported_once(self) -> None:
        self.assertEqual(rules(page('<input aria-labelledby="nope">')), ["aria-labelledby-invalid"])
        self.assertEqual(rules(page('<span id="e"></span><input aria-labelledby="e">')), ["aria-labelledby-invalid"])

    def test_label_for_missing_target(self) -> None:
        self.assertEqual(rules(page('<label for="ghost">用户名</label>')), ["label-for-missing"])


class Controls(unittest.TestCase):
    def test_buttons(self) -> None:
        bad = ["<button></button>", '<button><i class="icon-x"></i></button>', '<div role="button"></div>',
               '<input type="button">', '<button><img src="x.png"></button>']
        for body in bad:
            with self.subTest(body):
                self.assertIn("button-name", rules(page(body)))
        good = ["<button>保存</button>", '<button aria-label="关闭">×</button>', '<button><img src="x.png" alt="关闭"></button>',
                '<input type="button" value="重试">', '<div role="button">打开</div>', '<button title="关闭"></button>']
        for body in good:
            with self.subTest(body):
                self.assertNotIn("button-name", rules(page(body)))

    def test_links(self) -> None:
        self.assertEqual(rules(page('<a href="/x"></a>')), ["link-name"])
        self.assertEqual(rules(page('<a href="/x">首页</a><a href="/y"><img src="l.png" alt="标志"></a><a name="anchor"></a>')), [])

    def test_images(self) -> None:
        self.assertEqual(rules(page('<img src="a.png">')), ["img-alt"])
        self.assertEqual(rules(page('<img src="a.png" alt=""><img src="b.png" alt="图表">')), [])  # 空 alt = 装饰图

    def test_iframe_and_tabindex(self) -> None:
        self.assertEqual(rules(page('<iframe src="/x"></iframe>')), ["iframe-title"])
        self.assertEqual(rules(page('<iframe src="/x" title="地图"></iframe>')), [])
        self.assertEqual(rules(page('<div tabindex="3">x</div>')), ["positive-tabindex"])
        self.assertEqual(rules(page('<div tabindex="0">x</div><div tabindex="-1">y</div>')), [])

    def test_duplicate_id(self) -> None:
        hits = check_html(page('<p id="a">1</p>\n<p id="a">2</p>\n<p id="b">3</p>'))
        self.assertEqual([h["rule"] for h in hits], ["duplicate-id"])

    def test_radio_group_needs_legend(self) -> None:
        radios = '<label><input type="radio" name="c" value="1">红</label><label><input type="radio" name="c" value="2">蓝</label>'
        self.assertEqual(rules(page(radios)), ["fieldset-legend"])
        self.assertEqual(rules(page(f"<fieldset><legend>颜色</legend>{radios}</fieldset>")), [])
        self.assertEqual(rules(page(f'<div role="radiogroup" aria-label="颜色">{radios}</div>')), [])


class Document(unittest.TestCase):
    def test_full_page_rules(self) -> None:
        got = rules("<html><head></head><body><p>x</p></body></html>")
        self.assertEqual(sorted(got), ["document-title", "html-lang", "viewport"])

    def test_zoom_disabled(self) -> None:
        head = "<title>t</title><meta name='viewport' content='width=device-width, user-scalable=no'>"
        self.assertEqual(rules(page("<p>x</p>", head)), ["viewport-zoom-disabled"])

    def test_fragment_is_not_judged_as_a_page(self) -> None:
        self.assertEqual(rules('<div class="card"><p>片段</p></div>'), [])


class Templates(unittest.TestCase):
    def test_template_values_are_unknown_not_missing(self) -> None:
        body = ('<label for="{{ field.id }}">名称</label><input id="{{ field.id }}">'
                '<input aria-label="{{ _(\'搜索\') }}"><img src="a.png" alt="{{ alt }}">'
                '<button>{{ label }}</button><a href="/x">{% trans "首页" %}</a>'
                '<p id="row-{{ i }}">a</p><p id="row-{{ i }}">b</p>')
        self.assertEqual(rules(page(body)), [])

    def test_partial_template_skips_cross_reference_rules(self) -> None:
        tpl = '{% extends "base.html" %}{% block body %}<label for="elsewhere">x</label><input aria-labelledby="in_base">{% endblock %}'
        self.assertEqual(rules(tpl), [])

    def test_real_problems_inside_templates_are_still_reported(self) -> None:
        tpl = '{% extends "base.html" %}{% block body %}<input id="username"><button></button>{% endblock %}'
        self.assertEqual(sorted(rules(tpl)), ["button-name", "input-label"])

    def test_malformed_html_does_not_crash(self) -> None:
        for text in ("<div><input id='x'", "</p></div><input>", "<<<>>>", "", "<input id=\"a\"><input id=\"a\"></span>"):
            with self.subTest(text):
                self.assertIsInstance(check_html(text), list)


if __name__ == "__main__":
    unittest.main()
