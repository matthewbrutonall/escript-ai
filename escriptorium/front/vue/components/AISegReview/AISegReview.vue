<template>
    <div class="escr-card escr-card-padding escr-ai-seg-review">
        <h2>{{ $t("seg.title") }}</h2>
        <p class="escr-help-text">
            {{ $t("seg.help") }}
        </p>
        <p
            v-if="running"
            class="escr-help-text"
        >
            {{ $t("seg.running") }}
        </p>
        <p
            v-if="error"
            class="text-danger"
        >
            {{ error }}
        </p>
        <div class="escr-ai-seg-run">
            <select
                v-model="backend"
                class="form-control"
                :disabled="busy"
            >
                <option
                    :value="null"
                    disabled
                >
                    {{ $t("seg.pickBackend") }}
                </option>
                <option
                    v-for="b in backends"
                    :key="b.pk"
                    :value="b.pk"
                >
                    {{ b.name }}
                </option>
            </select>
            <button
                type="button"
                class="btn btn-primary"
                :class="{ 'is-running': running }"
                :disabled="busy || running || !backend"
                @click="runReview"
            >
                {{ $t("seg.run") }}
            </button>
        </div>
        <p v-if="!rows.length">
            {{ $t("seg.empty") }}
        </p>
        <div
            v-else
            class="escr-ai-seg-bulk"
        >
            <button
                type="button"
                class="btn btn-primary btn-sm"
                :disabled="busy || running"
                @click="setAll('accepted')"
            >
                {{ $t("seg.acceptAll") }}
            </button>
            <button
                type="button"
                class="btn btn-secondary btn-sm"
                :disabled="busy || running"
                @click="setAll('dismissed')"
            >
                {{ $t("seg.dismissAll") }}
            </button>
        </div>
        <table
            v-if="rows.length"
            class="escr-ai-sample-table"
        >
            <thead>
                <tr>
                    <th>{{ $t("seg.page") }}</th>
                    <th>{{ $t("seg.kind") }}</th>
                    <th>{{ $t("seg.line") }}</th>
                    <th>{{ $t("seg.reason") }}</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                <tr
                    v-for="row in rows"
                    :key="row.pk"
                >
                    <td>{{ row.part_filename || row.part }}</td>
                    <td>{{ $t("seg.kinds." + row.kind) }}</td>
                    <td>{{ row.line_order == null ? "—" : row.line_order }}</td>
                    <td>{{ reason(row) }}</td>
                    <td>
                        <button
                            type="button"
                            class="btn btn-secondary btn-sm"
                            :disabled="busy"
                            @click="setStatus(row, 'accepted')"
                        >
                            {{ $t("seg.accept") }}
                        </button>
                        <button
                            type="button"
                            class="btn btn-secondary btn-sm"
                            :disabled="busy"
                            @click="setStatus(row, 'dismissed')"
                        >
                            {{ $t("seg.dismiss") }}
                        </button>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
</template>

<script>
import {
    retrieveAiBackends,
    retrieveAiJobs,
    retrieveAiSegSuggestions,
    startAiSegReview,
    updateAiSegSuggestion,
    bulkUpdateAiSegSuggestions,
} from "../../../src/api/document";

export default {
    name: "AISegReview",
    props: {
        documentId: { type: [Number, String], required: true },
    },
    data() {
        return {
            backends: [],
            backend: null,
            rows: [],
            busy: false,
            running: false,
            error: "",
            poller: null,
        };
    },
    beforeDestroy() {
        this.stopPoll();
    },
    async created() {
        await this.refresh();
    },
    methods: {
        reason(row) {
            const p = row.payload || {};
            return p.reason || p.where || p.type || "";
        },
        async refresh() {
            this.busy = true;
            this.error = "";
            try {
                const [b, s] = await Promise.all([
                    retrieveAiBackends(),
                    retrieveAiSegSuggestions(this.documentId, "pending"),
                ]);
                this.backends = b.data.results || b.data || [];
                this.rows = s.data.results || s.data || [];
                if (this.backend == null && this.backends.length) {
                    this.backend = this.backends[0].pk;
                }
            } catch (e) {
                this.error = this.$t("seg.error");
            } finally {
                this.busy = false;
            }
        },
        stopPoll() {
            if (this.poller) {
                clearInterval(this.poller);
                this.poller = null;
            }
        },
        async pollJob() {
            try {
                const { data } = await retrieveAiJobs(this.documentId);
                const jobs = data.results || data || [];
                const job = jobs.find((j) => j.mode === "seg_review");
                if (!job) return;
                if (job.status === "done") {
                    this.running = false;
                    this.stopPoll();
                    await this.refresh();
                } else if (job.status === "error") {
                    this.running = false;
                    this.stopPoll();
                    const err = job.error || this.$t("seg.error");
                    await this.refresh();
                    this.error = err;
                }
            } catch (e) {
                /* keep polling */
            }
        },
        async runReview() {
            this.busy = true;
            this.error = "";
            try {
                await startAiSegReview({
                    documentId: this.documentId,
                    backend: this.backend,
                });
                this.running = true;
                this.stopPoll();
                this.poller = setInterval(() => this.pollJob(), 4000);
            } catch (e) {
                this.error = (e.response && e.response.data && (
                    e.response.data.error || e.response.data.detail
                )) || this.$t("seg.error");
            } finally {
                this.busy = false;
            }
        },
        async setAll(status) {
            this.busy = true;
            this.error = "";
            try {
                await bulkUpdateAiSegSuggestions(this.documentId, status);
                this.rows = [];
            } catch (e) {
                this.error = this.$t("seg.error");
            } finally {
                this.busy = false;
            }
        },
        async setStatus(row, status) {
            this.busy = true;
            try {
                await updateAiSegSuggestion(this.documentId, row.pk, status);
                this.rows = this.rows.filter((r) => r.pk !== row.pk);
            } catch (e) {
                this.error = this.$t("seg.error");
            } finally {
                this.busy = false;
            }
        },
    },
};
</script>
<style scoped>
.escr-ai-seg-run {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    margin: 0.75rem 0;
}
.escr-ai-seg-run select {
    max-width: 18rem;
}
.escr-ai-seg-bulk {
    display: flex;
    gap: 0.5rem;
    margin: 0.5rem 0 0.75rem;
}
.escr-ai-seg-run .btn.is-running {
    animation: escr-workflow-blink 1s ease-in-out infinite alternate;
}
@keyframes escr-workflow-blink {
    from { opacity: 1; }
    to { opacity: 0.35; }
}
.escr-ai-sample-table {
    width: 100%;
    font-size: 0.875rem;
}
.escr-ai-sample-table th,
.escr-ai-sample-table td {
    text-align: start;
    padding: 0.35rem 0.5rem;
    vertical-align: top;
}
</style>
