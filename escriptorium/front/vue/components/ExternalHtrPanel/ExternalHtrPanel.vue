<template>
    <section
        class="escr-external-htr"
        data-external-htr-panel
    >
        <h2>{{ $t("externalHtr.title") }}</h2>
        <p>{{ $t("externalHtr.help") }}</p>
        <p>{{ $t("externalHtr.noFallback") }}</p>
        <div class="escr-external-htr-controls">
            <label>
                {{ $t("externalHtr.engine") }}
                <select
                    data-external-htr-engine
                    :disabled="busy"
                    :value="engineSelectValue"
                    @change="onEngineChange"
                >
                    <option value="">{{ $t("externalHtr.chooseEngine") }}</option>
                    <option
                        v-for="engine in engines"
                        :key="engine.placeholder ? engine.engine : engine.id"
                        :value="engine.placeholder ? engine.engine : String(engine.id)"
                        :disabled="!engine.available && !engine.placeholder"
                    >
                        {{ engineLabel(engine) }}
                    </option>
                </select>
            </label>
            <label>
                {{ $t("externalHtr.model") }}
                <select
                    data-external-htr-model
                    :disabled="busy || !models.length"
                    :value="modelId"
                    @change="onModelChange"
                >
                    <option value="">{{ $t("externalHtr.chooseModel") }}</option>
                    <option
                        v-for="model in models"
                        :key="model.model_id"
                        :value="model.model_id"
                    >
                        {{ model.display_name }}
                    </option>
                </select>
            </label>
            <button
                type="button"
                class="btn btn-primary"
                data-external-htr-start
                :disabled="!canStart"
                @click="start"
            >
                {{ $t("externalHtr.start") }}
            </button>
        </div>
        <p v-if="selectedModel">
            {{ $t("externalHtr.licence", { licence: selectedModel.licence || "—" }) }}
        </p>
        <p
            v-if="engineName === 'htrflow' && !beamBlocked"
            data-external-htr-page
        >
            {{ $t("externalHtr.pageLevel") }}
        </p>
        <p
            v-if="beamBlocked"
            class="escr-external-htr-error"
            data-external-htr-beam
        >
            {{ $t("externalHtr.beamRefused") }}
        </p>
        <p
            v-if="selectedModel && selectedModel.line_height && engineName !== 'htrflow'"
            data-external-htr-line-height
        >
            {{ $t("externalHtr.lineHeight", { height: selectedModel.line_height }) }}
        </p>
        <p
            v-if="lineHeightBlocked"
            class="escr-external-htr-error"
            data-external-htr-line-height
        >
            {{ $t("externalHtr.lineHeightMissing") }}
        </p>
        <p
            v-if="error"
            class="escr-external-htr-error"
            data-external-htr-message
        >
            {{ error }}
        </p>
        <p
            v-if="run"
            data-external-htr-status
        >
            {{ $t("externalHtr.status", { status: run.status }) }}
            <template v-if="run.code"> ({{ run.code }})</template>
            <template v-if="run.message"> — {{ run.message }}</template>
        </p>
        <table
            v-if="pages.length"
            data-external-htr-pages
        >
            <thead>
                <tr>
                    <th>{{ $t("externalHtr.page") }}</th>
                    <th>{{ $t("externalHtr.pageStatus") }}</th>
                    <th>{{ $t("externalHtr.counts") }}</th>
                    <th>{{ $t("externalHtr.lines") }}</th>
                </tr>
            </thead>
            <tbody>
                <tr
                    v-for="page in pages"
                    :key="page.part_id"
                    :data-external-htr-page="page.part_id"
                >
                    <td>{{ page.part_id }}</td>
                    <td>
                        {{ page.status }}
                        <template v-if="page.code"> ({{ page.code }})</template>
                        <div>{{ $t("externalHtr.attempts", { n: page.attempts || 0 }) }}</div>
                    </td>
                    <td>
                        {{ $t("externalHtr.pageCounts", page) }}
                    </td>
                    <td>
                        <ul
                            v-if="page.lines && page.lines.length"
                            class="escr-external-htr-lines"
                        >
                            <li
                                v-for="line in page.lines"
                                :key="line.line_id"
                            >
                                {{ lineOutcome(line) }}
                            </li>
                        </ul>
                        <span v-else>{{ $t("externalHtr.noLines") }}</span>
                    </td>
                </tr>
            </tbody>
        </table>
        <div class="escr-external-htr-actions">
            <button
                type="button"
                class="btn btn-secondary"
                data-external-htr-import
                :disabled="!canImport"
                @click="importRun"
            >
                {{ $t("externalHtr.import") }}
            </button>
            <a
                v-if="run && run.imported"
                data-external-htr-editor
                :href="editorHref"
            >
                {{ $t("externalHtr.editor") }}
            </a>
        </div>
        <p v-if="run && run.imported">
            {{ $t("externalHtr.imported", { name: run.layer_name || run.transcription_id }) }}
            {{ $t("externalHtr.gate", { state: run.gate_state || "—" }) }}
            {{ $t("externalHtr.exportHint") }}
        </p>
        <form
            v-if="run && run.imported && run.gate_state === 'raw'"
            class="escr-external-htr-actions"
            @submit.prevent="acknowledge"
        >
            <label>
                {{ $t("externalHtr.phrase") }}
                <input
                    v-model="phrase"
                    type="text"
                    autocomplete="off"
                    data-external-htr-phrase
                >
            </label>
            <button
                type="submit"
                class="btn btn-primary"
                data-external-htr-acknowledge
                :disabled="busy"
            >
                {{ $t("externalHtr.acknowledge") }}
            </button>
        </form>
        <button
            v-else-if="run && run.imported && run.gate_state === 'sampled'"
            type="button"
            class="btn btn-secondary"
            data-external-htr-eligible
            :disabled="busy"
            @click="markEligible"
        >
            {{ $t("externalHtr.markEligible") }}
        </button>
    </section>
</template>
<script>
import axios from "axios";
import {
    acknowledgeAiGate,
    markAiGateEligible,
} from "../../../src/api/document";
import "./ExternalHtrPanel.css";

const TERMINAL = ["completed", "partial", "failed"];

export default {
    name: "ExternalHtrPanel",
    props: {
        documentId: {
            type: Number,
            required: true,
        },
        partIds: {
            type: Array,
            default: () => [],
        },
        onImported: {
            type: Function,
            default: null,
        },
    },
    data() {
        return {
            engines: [],
            models: [],
            configId: "",
            engineName: "",
            modelId: "",
            run: null,
            error: "",
            busy: false,
            phrase: "",
            timer: null,
        };
    },
    computed: {
        selectedModel() {
            return this.models.find((model) => model.model_id === this.modelId) || null;
        },
        pages() {
            return this.run && Array.isArray(this.run.pages) ? this.run.pages : [];
        },
        lineHeightBlocked() {
            if (this.engineName !== "pylaia" || !this.selectedModel) return false;
            const height = this.selectedModel.line_height;
            return typeof height !== "number" || height < 1;
        },
        beamBlocked() {
            if (this.engineName !== "htrflow" || !this.selectedModel) return false;
            const beams = this.selectedModel.num_beams;
            if (beams === undefined || beams === null) return false;
            return beams !== 1;
        },
        engineSelectValue() {
            if (this.configId) return this.configId;
            if (this.engineName === "pylaia" || this.engineName === "calamari") {
                return this.engineName;
            }
            return "";
        },
        canStart() {
            return !this.busy && this.configId && this.modelId && this.partIds.length > 0
                && !this.lineHeightBlocked && !this.beamBlocked;
        },
        canImport() {
            return !this.busy && this.run && ["completed", "partial"].includes(this.run.status)
                && !this.run.imported;
        },
        editorHref() {
            return `/document/${this.documentId}/parts/edit/`;
        },
    },
    mounted() {
        this.loadEngines().then(() => this.loadRecent());
    },
    beforeDestroy() {
        this.stopPoll();
    },
    methods: {
        engineLabel(engine) {
            if (engine.placeholder) return engine.name;
            const suffix = engine.available ? "" : ` (${this.$t("externalHtr.unavailable")})`;
            return `${engine.name}${suffix}`;
        },
        lineOutcome(line) {
            const empty = line.empty ? ` ${this.$t("externalHtr.empty")}` : "";
            const code = line.code ? ` (${line.code})` : "";
            return `${line.line_id}: ${line.status}${code}${empty}, ${this.$t("externalHtr.length", { n: line.text_length || 0 })}`;
        },
        async loadEngines() {
            this.error = "";
            try {
                const { data } = await axios.get(
                    `/documents/${this.documentId}/external-htr/engines/`,
                );
                this.engines = Array.isArray(data.engines) ? data.engines : [];
            } catch (error) {
                this.error = this.readError(error);
            }
        },
        async loadRecent() {
            try {
                const { data } = await axios.get(
                    `/documents/${this.documentId}/external-htr/runs/`,
                );
                const latest = data && Array.isArray(data.runs) ? data.runs[0] : null;
                if (!latest) return;
                this.run = { id: latest.id, status: latest.status, pages: [] };
                if (TERMINAL.includes(latest.status)) {
                    await this.loadRun();
                } else {
                    this.poll();
                }
            } catch (error) {
                this.error = this.readError(error);
            }
        },
        onEngineChange(event) {
            const value = event.target.value;
            this.modelId = "";
            this.models = [];
            this.error = "";
            if (value === "pylaia" || value === "calamari") {
                this.configId = "";
                this.engineName = value;
                return;
            }
            this.configId = value;
            this.engineName = "";
            if (this.configId) this.loadModels();
        },
        onModelChange(event) {
            this.modelId = event.target.value;
        },
        async loadModels() {
            this.error = "";
            try {
                const { data } = await axios.get(
                    `/documents/${this.documentId}/external-htr/engines/${this.configId}/models/`,
                );
                this.engineName = data && typeof data.engine === "string" ? data.engine : "";
                this.models = Array.isArray(data.models) ? data.models : [];
            } catch (error) {
                this.models = [];
                this.error = this.readError(error);
            }
        },
        async start() {
            this.busy = true;
            this.error = "";
            this.stopPoll();
            try {
                const { data } = await axios.post(
                    `/documents/${this.documentId}/external-htr/runs/`,
                    {
                        config_id: Number(this.configId),
                        model_id: this.modelId,
                        part_ids: this.partIds,
                    },
                );
                this.run = { id: data.id, status: data.status, pages: [] };
                this.poll();
            } catch (error) {
                this.error = this.readError(error);
            } finally {
                this.busy = false;
            }
        },
        poll() {
            this.stopPoll();
            this.loadRun();
            this.timer = setInterval(this.loadRun, 2000);
        },
        stopPoll() {
            if (this.timer) {
                clearInterval(this.timer);
                this.timer = null;
            }
        },
        async loadRun() {
            if (!this.run || !this.run.id) return;
            try {
                const { data } = await axios.get(
                    `/documents/${this.documentId}/external-htr/runs/${this.run.id}/`,
                );
                this.run = data;
                if (TERMINAL.includes(data.status)) this.stopPoll();
            } catch (error) {
                this.error = this.readError(error);
                this.stopPoll();
            }
        },
        async importRun() {
            this.busy = true;
            this.error = "";
            try {
                await axios.post(
                    `/documents/${this.documentId}/external-htr/runs/${this.run.id}/import/`,
                );
                await this.loadRun();
                if (this.onImported) await this.onImported();
            } catch (error) {
                this.error = this.readError(error);
            } finally {
                this.busy = false;
            }
        },
        async acknowledge() {
            if (!this.run || !this.run.gate_id) return;
            this.busy = true;
            this.error = "";
            try {
                await acknowledgeAiGate(this.documentId, this.run.gate_id, this.phrase);
                await this.loadRun();
            } catch (error) {
                this.error = this.readError(error);
            } finally {
                this.busy = false;
            }
        },
        async markEligible() {
            if (!this.run || !this.run.gate_id) return;
            this.busy = true;
            this.error = "";
            try {
                await markAiGateEligible(this.documentId, this.run.gate_id);
                await this.loadRun();
            } catch (error) {
                this.error = this.readError(error);
            } finally {
                this.busy = false;
            }
        },
        readError(error) {
            const data = error && error.response && error.response.data;
            if (data && typeof data.message === "string" && data.message) return data.message;
            return this.$t("externalHtr.failed");
        },
    },
};
</script>
