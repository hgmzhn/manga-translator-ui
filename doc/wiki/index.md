---
title: Manga Translator Wiki
description: 自动跳转到简体中文或 English 文档首页
pageId: index-root
lang: zh-CN
outline: false
lastUpdated: false
---

<script setup>
import { onMounted } from 'vue'
import { withBase } from 'vitepress'

onMounted(() => {
  const lang = (navigator.language || '').toLowerCase()
  const target = lang.startsWith('zh') ? 'zh/' : 'en/'
  window.location.replace(withBase(target))
})
</script>

正在跳转到文档首页…

如果没有自动跳转，请选择语言：

- [简体中文](zh/index.md)
- [English](en/index.md)