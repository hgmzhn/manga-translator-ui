<script setup lang="ts">
import { computed } from 'vue'

type Row = {
  key: string
  english: string
  chinese: string
  group?: string
  control?: string
  defaultValue?: string
  stage?: string
  fileAction?: string
  consumer?: string
  verification?: string
}
const props = defineProps<{ rows: readonly Row[]; labels?: Partial<Record<string, string>> }>()
const showOptional = computed(() => ['group', 'control', 'defaultValue', 'stage', 'fileAction', 'consumer', 'verification'].filter((key) => props.rows.some((row) => row[key as keyof Row])))
const label = (key: string) => props.labels?.[key] ?? ({ group: 'Group', control: 'Control', defaultValue: 'Default', stage: 'Stage', fileAction: 'File action', consumer: 'Consumer', verification: 'Verification' }[key] ?? key)
</script>
<template>
  <div class="wiki-table-wrap"><table><thead><tr><th scope="col">Key</th><th scope="col">English</th><th scope="col">简体中文</th><th v-for="key in showOptional" :key="key" scope="col">{{ label(key) }}</th></tr></thead><tbody><tr v-for="row in rows" :key="row.key"><td><code>{{ row.key }}</code></td><td>{{ row.english }}</td><td>{{ row.chinese }}</td><td v-for="key in showOptional" :key="key">{{ row[key as keyof Row] || '—' }}</td></tr></tbody></table></div>
</template>
