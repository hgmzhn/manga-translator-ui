<script setup lang="ts">
type Row = { layer: string; file: string; detail: string; lines?: string; status?: string }
defineProps<{ rows: readonly Row[] }>()
</script>
<template><div class="wiki-table-wrap"><table><thead><tr><th scope="col">Layer</th><th scope="col">File</th><th scope="col">Evidence</th><th scope="col">Lines</th><th scope="col">Status</th></tr></thead><tbody><tr v-for="row in rows" :key="`${row.layer}:${row.file}`"><td>{{ row.layer }}</td><td><code>{{ row.file }}</code></td><td>{{ row.detail }}</td><td>{{ row.lines || '—' }}</td><td>{{ row.status || '—' }}</td></tr></tbody></table></div></template>
