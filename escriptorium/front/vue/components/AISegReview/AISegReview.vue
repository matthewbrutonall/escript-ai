<template>
    <div class="escr-card escr-card-padding escr-ai-seg-review">
        <h2>{{ $t("seg.title") }}</h2>
        <p class="escr-help-text">
            {{ $t("seg.help") }}
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
                :disabled="busy || !backend"
                @click="runReview"
            >
                {{ $t("seg.run") }}
            </button>
        </div>
        <p v-if="!rows.length">
            {{ $t("seg.empty") }}
        </p>
        <table
            v-else
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
    retrieveAiSegSuggestions,
    startAiSegReview,
    updateAiSegSuggestion,
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
            error: "",
        };
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
        async runReview() {
            this.busy = true;
            this.error = "";
            try {
                await startAiSegReview({
                    documentId: this.documentId,
                    backend: this.backend,
                });
            } catch (e) {
                this.error = (e.response && e.response.data && (
                    e.response.data.error || e.response.data.detail
                )) || this.$t("seg.error");
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
