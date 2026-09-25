# 引用来源 / Citation sources

[中文 API 手册](../API使用与项目接入手册.md) · [English API guide](../API-USAGE.md)

## 中文

验证状态（2026-09-21）：引用更新已通过 184 项相关离线测试，包含实际 JavaScript 提取、JSON/SSE 返回和文字/图片回归。真实 NAS 网页引用验收尚未完成。

当 ChatGPT 的最终回复包含网页引用，并且同一条回复的引用信息提供了明确的网址对应关系时，REST 接口会返回 `choices[0].message.annotations`。正文 `content` 保持原样，包括引用标记；没有可解析的引用时不增加这个字段。普通 Markdown 链接仍直接保留在正文中。

以下是响应中 `message` 字段的示例，网址和编号均为演示：

```json
{
  "role": "assistant",
  "content": "Example citeturn0search0",
  "annotations": [
    {
      "type": "url_citation",
      "url_citation": {
        "ref_id": "turn0search0",
        "url": "https://example.org/source",
        "title": "Example source",
        "start_index": 8,
        "end_index": 27
      }
    }
  ]
}
```

调用项目的后端需要把 `annotations` 传给前端。前端按 `ref_id` 匹配正文标记，使用 `url` 创建链接，使用 `title` 显示来源名称；API 不会替客户端生成界面。`ref_id` 是本项目的兼容扩展。每个可解析的编号返回一次，`start_index` / `end_index` 表示其首次出现的整个引用标记在原始正文中的 Unicode 码点范围（结束位置不包含）；多来源标记中的多个编号共用这个范围。JavaScript 若按位置切片，应先使用 `Array.from(content)`，不能直接把它当 UTF-16 下标。

`stream: true` 时，同样的信息放在 `choices[0].delta.annotations`；客户端需要收集它，不能只拼接 `delta.content`。接口仍等待最终回复确认后才发送正文。

目前支持最终文本消息 `metadata.content_references` 中的网页来源，包括带显式 `refs` 的 `grouped_webpages`。只返回正文实际引用且对应关系唯一的 HTTP(S) 链接；不按数组顺序猜网址，不把文件引用转换为下载链接，不读取其他轮次的来源补缺。缺失、不支持或冲突的引用保持未解析状态，也不保证来源网站当前可访问。纯页面文字回退没有引用信息。

升级不会自动修复调用项目已经保存的旧回复。新回复会按新格式返回；旧记录若没有保存来源信息，需要单独重新获取，不能凭编号恢复网址。

## English

Validation status (2026-09-21): the citation update passed 184 related offline tests, including actual JavaScript projection execution, JSON/SSE responses and text/image regressions. Live NAS webpage citation acceptance remains pending.

When the verified final reply contains web citation markers with explicit source mappings, REST returns `choices[0].message.annotations` as illustrated above. `content` stays unchanged. Replies without resolvable citations omit the additional field; ordinary Markdown links remain in the text.

The calling backend must forward annotations to its frontend. Match the bridge's `ref_id` extension against the markers, render `url` as a link and `title` as the source label. The API does not render a UI. Each resolved reference appears once. `start_index` and exclusive `end_index` delimit its first complete citation marker in Unicode code points of the original content; grouped references share that span. JavaScript clients using these offsets should slice `Array.from(content)`, not UTF-16 code units.

For SSE, collect `choices[0].delta.annotations` as well as `delta.content`. Content is still buffered until final verification. The supported input is the selected terminal text message's `metadata.content_references`, including `grouped_webpages` with explicit item `refs`. Only unambiguous HTTP(S) mappings actually cited in the text are emitted. Missing, unsupported or conflicting metadata stays unresolved. No URLs are guessed by list order, file downloads are not added, other turns are not searched for replacement sources, and destination availability is not tested. Plain page-text fallback carries no annotations.

Existing client records are not retroactively repaired by upgrading the bridge. Newly generated replies can include sources; reference IDs alone cannot reconstruct missing URLs in old records.
