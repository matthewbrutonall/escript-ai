<template>
    <section v-if="jobs.length" class="escr-card escr-card-padding ai-jobs-card">
        <h2>{{ $t('aiJobs.title') }}</h2>
        <table><thead><tr><th>{{ $t('aiJobs.started') }}</th><th>{{ $t('aiJobs.type') }}</th><th>{{ $t('aiJobs.layer') }}</th><th>{{ $t('aiJobs.backend') }}</th><th>{{ $t('aiJobs.status') }}</th><th>{{ $t('aiJobs.lines') }}</th><th>{{ $t('aiJobs.cost') }}</th></tr></thead>
            <tbody><template v-for="job in jobs.slice(0, 5)"><tr :key="job.pk"><td>{{ formatDate(job.created_at) }}</td><td>{{ job.mode === 'seg_review' ? $t('aiJobs.segReview') : $t('aiJobs.transcription') }}</td><td>{{ job.layer }}</td><td>{{ job.backend }}</td><td>{{ job.status }}</td><td>{{ job.lines_written }} / {{ job.lines_flagged }}</td><td>{{ job.actual_cost ? '$' + Number(job.actual_cost).toFixed(3) : '—' }}</td></tr>
                <tr v-if="job.status === 'error'" :key="`${job.pk}-error`"><td colspan="7" class="ai-job-error">{{ job.message || $t('aiJobs.failed') }}<details v-if="job.error"><summary>{{ $t('aiJobs.technical') }}</summary>{{ job.error }}</details></td></tr>
                <tr v-else-if="job.message" :key="`${job.pk}-message`"><td colspan="7" class="ai-job-error">{{ job.message }}<details v-if="job.error"><summary>{{ $t('aiJobs.technical') }}</summary>{{ job.error }}</details></td></tr>
                <tr v-else-if="job.status === 'done' && job.lines_flagged > 0" :key="`${job.pk}-flagged`"><td colspan="7">{{ $t('aiJobs.flagged', { n: job.lines_flagged }) }}</td></tr>
            </template></tbody>
        </table>
    </section>
</template>
<script>
import { retrieveAiJobs } from '../../../src/api/document';
export default {
    name: 'AIJobsCard', props: { documentId: { type: Number, required: true } },
    data: () => ({ jobs: [], poll: null }),
    mounted() { this.refresh(); }, beforeDestroy() { clearInterval(this.poll); },
    methods: {
        async refresh() { try { const { data } = await retrieveAiJobs(this.documentId); this.jobs = data || []; clearInterval(this.poll); this.poll = this.jobs.some((job) => ['pending', 'running'].includes(job.status)) ? setInterval(this.refresh, 5000) : null; } catch (_) { /* polling is best effort */ } },
        formatDate(value) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—'; },
    },
};
</script>
