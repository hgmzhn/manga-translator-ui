<script setup lang="ts">
import Teek from 'vitepress-theme-teek'
import { computed } from 'vue'
import { useData } from 'vitepress'

const { Layout: TeekLayout } = Teek
const { lang } = useData()
const skipLabel = computed(() => lang.value.startsWith('zh') ? '跳到正文' : 'Skip to content')
</script>

<template>
  <TeekLayout>
    <template #layout-top>
      <a class="wiki-skip-link" href="#VPContent">{{ skipLabel }}</a>
    </template>
  </TeekLayout>
</template>
