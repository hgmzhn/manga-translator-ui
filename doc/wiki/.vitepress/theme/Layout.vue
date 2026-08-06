<script setup lang="ts">
import DefaultTheme from 'vitepress/theme'
import LanguageSwitch from './components/LanguageSwitch.vue'

const { Layout: DefaultLayout } = DefaultTheme
</script>

<template>
  <DefaultLayout>
    <template #nav-bar-content-after>
      <LanguageSwitch class="wiki-language-switch" />
    </template>

    <template #layout-top>
      <a class="wiki-skip-link" href="#VPContent">Skip to content</a>
    </template>
  </DefaultLayout>
</template>
