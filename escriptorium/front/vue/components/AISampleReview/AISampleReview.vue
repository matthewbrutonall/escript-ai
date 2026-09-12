<template>
    <div
        v-if="gates && gates.length"
        class="escr-card escr-card-padding escr-ai-sample-review"
    >
        <h2>{{ $t("gate.title") }}</h2>
        <p class="escr-help-text">
            {{ $t("gate.help") }}
        </p>
        <div
            v-for="gate in gates"
            :key="gate.pk"
            class="escr-ai-gate"
        >
            <h3>{{ gate.transcription_name }}</h3>
            <p>
                {{ $t("gate.state") }}: {{ $t("gate.states." + gate.state.replace("-", "_")) }}
                <span v-if="gate.mean_cer != null">
                    · {{ $t("gate.meanCer", { cer: (gate.mean_cer * 100).toFixed(1) }) }}
                </span>
            </p>
            <p
                v-if="!gate.sample_lines || !gate.sample_lines.length"
                class="escr-help-text"
            >
                {{ $t("gate.emptySample") }}
            </p>
            <table
                v-else
                class="escr-ai-sample-table"
            >
                <thead>
                    <tr>
                        <th>{{ $t("gate.line") }}</th>
                        <th>{{ $t("gate.aiText") }}</th>
                        <th>{{ $t("gate.comparisonText") }}</th>
                        <th>{{ $t("gate.cer") }}</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    <tr
                        v-for="row in gate.sample_lines"
                        :key="row.line_pk"
                    >
                        <td>{{ row.line_pk }}</td>
                        <td>{{ row.ai_text }}</td>
                        <td>{{ row.comparison_text }}</td>
                        <td>{{ row.cer == null ? "—" : (row.cer * 100).toFixed(1) + "%" }}</td>
                        <td>
                            <button
                                type="button"
                                class="btn btn-secondary btn-sm"
                                :disabled="busy"
                                @click="fixLine(gate, row)"
                            >
                                {{ $t("gate.fixThis") }}
                            </button>
                        </td>
                    </tr>
                </tbody>
            </table>
            <form
                v-if="gate.state === 'raw'"
                class="escr-ai-ack-form"
                @submit.prevent="acknowledge(gate)"
            >
                <label>
                    {{ $t("gate.typePhrase", { phrase: gate.ack_phrase }) }}
                    <input
                        v-model="phrases[gate.pk]"
                        type="text"
                        autocomplete="off"
                    >
                </label>
                <button
                    type="submit"
                    class="btn btn-primary"
                    :disabled="busy"
                >
                    {{ $t("gate.acknowledge") }}
                </button>
                <p
                    v-if="errors[gate.pk]"
                    class="error"
                >
                    {{ errors[gate.pk] }}
                </p>
            </form>
            <button
                v-else-if="gate.state === 'sampled'"
                type="button"
                class="btn btn-success"
                :disabled="busy"
                @click="markEligible(gate)"
            >
                {{ $t("gate.markEligible") }}
            </button>
            <p v-else>
                {{ $t("gate.eligible") }}
            </p>
        </div>
    </div>
</template>
<script>
import {
    acknowledgeAiGate,
    fixAiLine,
    markAiGateEligible,
    retrieveAiGate,
    retrieveAiGates,
} from "../../../src/api/document";

export default {
    name: "AISampleReview",
    props: {
        documentId: { type: [Number, String], required: true },
    },
    data() {
        return {
            gates: [],
            phrases: {},
            errors: {},
            busy: false,
        };
    },
    watch: {
        documentId: {
            immediate: true,
            handler() { this.load(); },
        },
    },
    methods: {
        async load() {
            if (!this.documentId) return;
            try {
                const { data } = await retrieveAiGates(this.documentId);
                const list = Array.isArray(data) ? data : (data.results || []);
                const detailed = [];
                for (const g of list) {
                    if (g.state === "training-eligible") {
                        detailed.push(g);
                        continue;
                    }
                    const res = await retrieveAiGate(this.documentId, g.pk);
                    detailed.push(res.data);
                }
                this.gates = detailed;
            } catch (e) {
                this.gates = [];
            }
        },
        async fixLine(gate, row) {
            const partial = window.prompt(
                this.$t("gate.fixPartial"),
                row.ai_text || "",
            );
            if (partial === null) return;
            this.busy = true;
            this.$set(this.errors, gate.pk, "");
            try {
                const { data } = await fixAiLine({
                    documentId: this.documentId,
                    line: row.line_pk,
                    transcription: gate.transcription,
                    partial,
                });
                this.$set(row, "ai_text", data.text);
            } catch (e) {
                const msg = (e.response && e.response.data && (
                    e.response.data.detail || e.response.data.line
                    || JSON.stringify(e.response.data)
                )) || this.$t("gate.error");
                this.$set(this.errors, gate.pk, msg);
            } finally {
                this.busy = false;
            }
        },
        async acknowledge(gate) {
            this.busy = true;
            this.$set(this.errors, gate.pk, "");
            try {
                const { data } = await acknowledgeAiGate(
                    this.documentId, gate.pk, this.phrases[gate.pk] || "",
                );
                this.replaceGate(data);
            } catch (e) {
                const msg = (e.response && e.response.data && e.response.data.detail)
                    || this.$t("gate.error");
                this.$set(this.errors, gate.pk, msg);
            } finally {
                this.busy = false;
            }
        },
        async markEligible(gate) {
            this.busy = true;
            try {
                const { data } = await markAiGateEligible(
                    this.documentId, gate.pk,
                );
                this.replaceGate(data);
            } catch (e) {
                const msg = (e.response && e.response.data && e.response.data.detail)
                    || this.$t("gate.error");
                this.$set(this.errors, gate.pk, msg);
            } finally {
                this.busy = false;
            }
        },
        replaceGate(updated) {
            this.gates = this.gates.map((g) => (g.pk === updated.pk ? updated : g));
        },
    },
};
</script>
<style scoped>
.escr-ai-sample-table {
    width: 100%;
    margin: 0.75rem 0;
    font-size: 0.875rem;
}
.escr-ai-sample-table th,
.escr-ai-sample-table td {
    text-align: start;
    padding: 0.25rem 0.4rem;
    vertical-align: top;
}
.escr-ai-ack-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-width: 28rem;
}
.escr-ai-gate {
    margin-top: 1rem;
}
</style>
