import unittest

from onebot_message import (
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
