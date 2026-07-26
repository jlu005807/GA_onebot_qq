import unittest

from onebot_message import (
    escape_cq,
    split_for_send,
    unescape_cq,
    build_agent_prompt,
    extract_at_mentions,
    extract_segments,
    extract_text,
    format_attachments,
    is_at_bot,
    is_transient_status_message,
    match_trigger_word,
    normalize_outgoing_content,
    parse_send_content,
)


class NormalizeOutgoingContentTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize_outgoing_content(""), "")
        self.assertEqual(normalize_outgoing_content(None), "")

    def test_status_lines_dropped(self):
        self.assertEqual(normalize_outgoing_content("思考中..."), "")
        self.assertEqual(normalize_outgoing_content("⏳ 还在处理中，请稍等..."), "")
        self.assertEqual(normalize_outgoing_content("还在处理中，请稍等"), "")

    def test_llm_running_dropped(self):
        self.assertEqual(normalize_outgoing_content("LLM Running (Turn 3)..."), "")
        self.assertEqual(normalize_outgoing_content("🤖 LLM Running (Turn 12)…"), "")

    def test_status_line_dropped_inside_body(self):
        text = "第一行\n思考中...\n第二行"
        self.assertEqual(normalize_outgoing_content(text), "第一行\n第二行")

    def test_tool_call_block_dropped(self):
        text = "回答：\ncode_run({\n  'a': 1\n})\n结束"
        self.assertEqual(normalize_outgoing_content(text), "回答：\n结束")

    def test_single_line_tool_call_dropped(self):
        self.assertEqual(normalize_outgoing_content("file_read({'p': 'x'})\nok"), "ok")

    def test_code_fence_markers_removed(self):
        self.assertEqual(normalize_outgoing_content("```python\nprint(1)\n```"), "print(1)")

    def test_blank_lines_collapsed(self):
        self.assertEqual(normalize_outgoing_content("a\n\n\n\nb"), "a\n\nb")

    def test_crlf_normalized(self):
        self.assertEqual(normalize_outgoing_content("a\r\nb"), "a\nb")

    def test_plain_text_preserved(self):
        text = "你好，这是一段正常回复。\n第二行内容。"
        self.assertEqual(normalize_outgoing_content(text), text)


class ToolBlockBoundTest(unittest.TestCase):
    def test_unclosed_tool_block_does_not_eat_the_whole_reply(self):
        # 正文里出现形似工具调用的一行，不能把后面所有内容都吞掉
        text = "web_search(\n" + "\n".join(f"正文第{i}行" for i in range(1, 60))
        out = normalize_outgoing_content(text)
        self.assertIn("正文第1行", out)
        self.assertIn("正文第59行", out)

    def test_closed_tool_block_is_still_removed(self):
        text = "开头\nshell_command({\n  'cmd': 'ls'\n})\n结尾"
        self.assertEqual(normalize_outgoing_content(text), "开头\n结尾")

    def test_short_unclosed_block_at_eof_is_restored(self):
        out = normalize_outgoing_content("回答如下\ncode_run(\n还没写完")
        self.assertIn("还没写完", out)


class UnescapeCqTest(unittest.TestCase):
    def test_noop_without_ampersand(self):
        self.assertEqual(unescape_cq("plain"), "plain")
        self.assertEqual(unescape_cq(""), "")
        self.assertEqual(unescape_cq(None), "")

    def test_brackets_and_amp(self):
        self.assertEqual(unescape_cq("&#91;a&#93; &amp; b"), "[a] & b")

    def test_comma_only_inside_segment(self):
        self.assertEqual(unescape_cq("a&#44;b"), "a&#44;b")
        self.assertEqual(unescape_cq("a&#44;b", in_segment=True), "a,b")

    def test_round_trip(self):
        for raw in ("a&b", "[x]", "a,b", "&#91;literal&#93;", "混合 & [值] , 逗号"):
            self.assertEqual(unescape_cq(escape_cq(raw)), raw)
            self.assertEqual(
                unescape_cq(escape_cq(raw, in_segment=True), in_segment=True), raw
            )


class CqSegmentUnescapeTest(unittest.TestCase):
    def test_url_with_escaped_ampersand_is_restored(self):
        # 不还原就会拿到坏 URL，附件下载直接失败
        segs = extract_segments("[CQ:image,file=a.jpg,url=http://h/a?x=1&amp;y=2]")
        self.assertEqual(segs[0]["data"]["url"], "http://h/a?x=1&y=2")

    def test_escaped_comma_in_value(self):
        segs = extract_segments("[CQ:file,name=a&#44;b.txt]")
        self.assertEqual(segs[0]["data"]["name"], "a,b.txt")


class SplitForSendTest(unittest.TestCase):
    def test_short_text_stays_whole(self):
        self.assertEqual(split_for_send("hello", 100), ["hello"])

    def test_blank_becomes_placeholder(self):
        self.assertEqual(split_for_send("", 100), ["..."])
        self.assertEqual(split_for_send("   ", 100), ["..."])

    def test_all_content_is_preserved(self):
        body = "".join(str(i % 10) for i in range(250))
        parts = split_for_send(body, 100)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), body)

    def test_prefers_newline_boundary(self):
        body = "a" * 70 + "\n" + "b" * 70
        parts = split_for_send(body, 100)
        self.assertEqual(parts[0], "a" * 70)

    def test_never_cuts_inside_a_cq_segment(self):
        body = "x" * 95 + "[CQ:at,qq=123456]" + "y" * 50
        parts = split_for_send(body, 100)
        # 每个分片里的 [CQ: 都必须成对闭合，否则 @ 会变成一串乱码文本
        for part in parts:
            self.assertEqual(part.count("[CQ:"), part.count("]"), part)
        self.assertTrue(any("[CQ:at,qq=123456]" in part for part in parts))

    def test_oversize_single_segment_is_kept_intact(self):
        body = "x" * 50 + "[CQ:at,qq=" + "9" * 200 + "]"
        parts = split_for_send(body, 60)
        self.assertTrue(any("[CQ:at,qq=" + "9" * 200 + "]" in part for part in parts))

    def test_limit_zero_returns_single_part(self):
        self.assertEqual(split_for_send("abc", 0), ["abc"])


class IsTransientStatusMessageTest(unittest.TestCase):
    def test_blank_is_transient(self):
        self.assertTrue(is_transient_status_message(""))
        self.assertTrue(is_transient_status_message("   "))
        self.assertTrue(is_transient_status_message(None))

    def test_status_text_is_transient(self):
        self.assertTrue(is_transient_status_message("思考中..."))
        self.assertTrue(is_transient_status_message("LLM Running (Turn 1)..."))

    def test_real_answer_is_not_transient(self):
        self.assertFalse(is_transient_status_message("答案是 42"))


class ParseSendContentTest(unittest.TestCase):
    def test_plain_text_single_segment(self):
        self.assertEqual(
            parse_send_content("hello"), [{"type": "text", "data": {"text": "hello"}}]
        )

    def test_empty_gives_no_segments(self):
        self.assertEqual(parse_send_content(""), [])

    def test_at_only(self):
        self.assertEqual(
            parse_send_content("[CQ:at,qq=123]"), [{"type": "at", "data": {"qq": "123"}}]
        )

    def test_text_around_at(self):
        self.assertEqual(
            parse_send_content("hi [CQ:at,qq=123] there"),
            [
                {"type": "text", "data": {"text": "hi "}},
                {"type": "at", "data": {"qq": "123"}},
                {"type": "text", "data": {"text": " there"}},
            ],
        )

    def test_multiple_ats(self):
        segs = parse_send_content("[CQ:at,qq=1][CQ:at,qq=2]")
        self.assertEqual([s["type"] for s in segs], ["at", "at"])


class IsAtBotTest(unittest.TestCase):
    def test_string_form(self):
        self.assertTrue(is_at_bot("hi [CQ:at,qq=999] there", "999"))
        self.assertFalse(is_at_bot("hi [CQ:at,qq=111] there", "999"))

    def test_list_form(self):
        msg = [{"type": "at", "data": {"qq": "999"}}, {"type": "text", "data": {"text": "hi"}}]
        self.assertTrue(is_at_bot(msg, "999"))
        self.assertFalse(is_at_bot(msg, "111"))

    def test_other_types(self):
        self.assertFalse(is_at_bot(None, "999"))
        self.assertFalse(is_at_bot(123, "999"))


class ExtractTextTest(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(extract_text("  hello  "), "hello")

    def test_cq_codes_stripped(self):
        self.assertEqual(extract_text("[CQ:at,qq=1] hello [CQ:face,id=2]"), "hello")

    def test_list_message_joins_text_segments(self):
        msg = [
            {"type": "at", "data": {"qq": "1"}},
            {"type": "text", "data": {"text": "hello "}},
            {"type": "text", "data": {"text": "world"}},
        ]
        self.assertEqual(extract_text(msg), "hello world")

    def test_list_with_bare_strings(self):
        self.assertEqual(extract_text(["a", "b"]), "ab")

    def test_non_string_non_list(self):
        self.assertEqual(extract_text(42), "42")

    def test_string_form_unescapes_cq_entities(self):
        self.assertEqual(extract_text("a &amp; b &#91;x&#93;"), "a & b [x]")

    def test_amp_is_unescaped_last(self):
        # &amp;#91; 必须还原成字面量 "&#91;"，不能被二次解码成 "["
        self.assertEqual(extract_text("&amp;#91;"), "&#91;")

    def test_list_form_keeps_literal_cq_text(self):
        # 数组格式的 text 段未转义，用户原样输入的 CQ 文本不能被剥掉
        msg = [{"type": "text", "data": {"text": "看这个 [CQ:at,qq=1] 写法"}}]
        self.assertEqual(extract_text(msg), "看这个 [CQ:at,qq=1] 写法")

    def test_list_form_does_not_unescape(self):
        msg = [{"type": "text", "data": {"text": "a &amp; b"}}]
        self.assertEqual(extract_text(msg), "a &amp; b")


class MatchTriggerWordTest(unittest.TestCase):
    def test_no_words_no_match(self):
        self.assertEqual(match_trigger_word("hello", ()), "")

    def test_empty_content_no_match(self):
        self.assertEqual(match_trigger_word("", ("bot",)), "")

    def test_substring_match(self):
        self.assertEqual(match_trigger_word("请问小宇在吗", ("小宇", "bot")), "小宇")

    def test_case_insensitive(self):
        self.assertEqual(match_trigger_word("Hey BOT", ("bot",)), "bot")

    def test_blank_words_skipped(self):
        self.assertEqual(match_trigger_word("hey bot", ("", "  ", "bot")), "bot")

    def test_first_match_wins(self):
        self.assertEqual(match_trigger_word("a b", ("b", "a")), "b")


class ExtractAtMentionsTest(unittest.TestCase):
    def test_list_excludes_bot_and_all(self):
        msg = [
            {"type": "at", "data": {"qq": "999"}},
            {"type": "at", "data": {"qq": "all"}},
            {"type": "at", "data": {"qq": "111"}},
        ]
        self.assertEqual(extract_at_mentions(msg, "999"), ["111"])

    def test_dedup_preserves_order(self):
        msg = [
            {"type": "at", "data": {"qq": "111"}},
            {"type": "at", "data": {"qq": "222"}},
            {"type": "at", "data": {"qq": "111"}},
        ]
        self.assertEqual(extract_at_mentions(msg, "999"), ["111", "222"])

    def test_string_form(self):
        self.assertEqual(
            extract_at_mentions("[CQ:at,qq=111][CQ:at,qq=999]", "999"), ["111"]
        )

    def test_other_types_return_empty(self):
        self.assertEqual(extract_at_mentions(None, "999"), [])


class ExtractSegmentsTest(unittest.TestCase):
    def test_list_keeps_dict_segments_only(self):
        msg = [{"type": "text", "data": {}}, "junk", 5]
        self.assertEqual(extract_segments(msg), [{"type": "text", "data": {}}])

    def test_plain_string_without_cq(self):
        self.assertEqual(extract_segments("hello"), [])

    def test_cq_string_parsed(self):
        segs = extract_segments("[CQ:image,file=a.jpg,url=http://h/a.jpg]")
        self.assertEqual(segs[0]["type"], "image")
        self.assertEqual(segs[0]["data"]["file"], "a.jpg")
        self.assertEqual(segs[0]["data"]["url"], "http://h/a.jpg")

    def test_non_string_non_list(self):
        self.assertEqual(extract_segments(None), [])


class FormatAttachmentsTest(unittest.TestCase):
    def test_size_rounded_up_to_at_least_1kb(self):
        line = format_attachments([{"type": "image", "path": "/p/a.jpg", "size": 10}])
        self.assertIn("type=image", line)
        self.assertIn("path=/p/a.jpg", line)
        self.assertIn("size=1KB", line)

    def test_multiple_are_numbered(self):
        out = format_attachments(
            [
                {"type": "image", "path": "a", "size": 2048},
                {"type": "file", "path": "b", "size": 4096},
            ]
        )
        self.assertTrue(out.startswith("attachment1:"))
        self.assertIn("attachment2:", out)

    def test_empty(self):
        self.assertEqual(format_attachments([]), "")


class BuildAgentPromptTest(unittest.TestCase):
    def test_minimal_private_prompt(self):
        prompt = build_agent_prompt(
            "你好", [], is_group=False, is_admin=False, include_admin_policy=False
        )
        self.assertIn("context: group=0", prompt)
        self.assertNotIn("admin=", prompt)
        self.assertIn("你好", prompt)

    def test_admin_policy_included_when_requested(self):
        admin = build_agent_prompt(
            "x", [], is_group=False, is_admin=True, include_admin_policy=True
        )
        self.assertIn("admin=1", admin)
        self.assertIn("user is admin", admin)

        plain = build_agent_prompt(
            "x", [], is_group=False, is_admin=False, include_admin_policy=True
        )
        self.assertIn("admin=0", plain)
        self.assertIn("user is NOT admin", plain)

    def test_sender_and_mentions(self):
        prompt = build_agent_prompt(
            "hi",
            [],
            is_group=True,
            is_admin=False,
            include_admin_policy=False,
            sender_nickname="小明",
            sender_qq="111",
            at_mentions=["222", "333"],
        )
        self.assertIn("sender_qq: 111", prompt)
        self.assertIn("nickname: 小明", prompt)
        self.assertIn("mentioned_qq: 222, 333", prompt)

    def test_history_and_attachments(self):
        prompt = build_agent_prompt(
            "hi",
            [{"type": "image", "path": "/p/a.jpg", "size": 2048}],
            is_group=True,
            is_admin=False,
            include_admin_policy=False,
            history_messages=["A(1): 早", "B(2): 好"],
        )
        self.assertIn("recent_messages:", prompt)
        self.assertIn("A(1): 早", prompt)
        self.assertIn("attachments:", prompt)
        self.assertIn("/p/a.jpg", prompt)

    def test_plain_text_hint_appended(self):
        prompt = build_agent_prompt(
            "hi",
            [],
            is_group=False,
            is_admin=False,
            include_admin_policy=False,
            plain_text_hint="请用纯文本回复。",
        )
        self.assertIn("请用纯文本回复。", prompt)


if __name__ == "__main__":
    unittest.main()
