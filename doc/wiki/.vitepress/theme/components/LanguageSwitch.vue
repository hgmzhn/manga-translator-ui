<script setup lang="ts">
import { computed } from 'vue'
import { useData, useRoute, withBase } from 'vitepress'

const route = useRoute()
const { lang } = useData()

const targetLocale = computed(() => (lang.value.startsWith('zh') ? 'en' : 'zh'))

const targetPath = computed(() => {
  const match = route.path.match(/^\/(?:zh|en)(?=\/|$)(.*)$/)
  const suffix = match?.[1] || '/'

  return withBase(`/${targetLocale.value}${suffix}`)
})

const targetLabel = computed(() => (targetLocale.value === 'zh' ? '中文' : 'English'))
const accessibleLabel = computed(() => (
  targetLocale.value === 'zh' ? '切换到中文' : 'Switch to English'
))
</script>

<template>
  <a :href="targetPath" :lang="targetLocale" :aria-label="accessibleLabel">
    {{ targetLabel }}
  </a>
</template>
