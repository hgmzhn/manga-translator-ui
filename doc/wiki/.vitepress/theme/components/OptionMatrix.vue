<script setup lang="ts">
type Row = { value: string; english: string; chinese: string; page?: string; i18nKey?: string; notes?: string }
defineProps<{ rows: readonly Row[] }>()
</script>
<template><div class="wiki-table-wrap"><table><thead><tr><th scope="col">Stored value</th><th scope="col">English</th><th scope="col">简体中文</th><th scope="col">Page</th><th scope="col">i18n key</th><th scope="col">Notes</th></tr></thead><tbody><tr v-for="row in rows" :key="`${row.value}:${row.i18nKey ?? ''}`"><td><code>{{ row.value }}</code></td><td>{{ row.english }}</td><td>{{ row.chinese }}</td><td>{{ row.page || '—' }}</td><td><code>{{ row.i18nKey || '—' }}</code></td><td>{{ row.notes || '—' }}</td></tr></tbody></table></div></template>
