<template>
    <div class="escr-card escr-card-padding escr-project-ontology escr-project-ontology-card">
        <div class="escr-card-header">
            <h2>{{ $t("projectOntology.title") }}</h2>
            <div class="escr-card-actions">
                <input
                    ref="importInput"
                    type="file"
                    accept=".json,.yaml,.yml"
                    class="sr-only"
                    @change="onImportFileChange"
                >
                <EscrButton
                    color="text"
                    size="small"
                    :label="$t('common.import')"
                    :disabled="disabled"
                    :on-click="onClickImport"
                >
                    <template #button-icon>
                        <UploadIcon class="escr-ontology-action-icon" />
                    </template>
                </EscrButton>
                <EscrButton
                    v-if="ontologyConfig"
                    color="text"
                    size="small"
                    :label="$t('common.export')"
                    :disabled="disabled"
                    :on-click="exportOntology"
                >
                    <template #button-icon>
                        <DownloadIcon />
                    </template>
                </EscrButton>
                <EscrButton
                    v-if="ontologyConfig"
                    color="text"
                    size="small"
                    :label="$t('common.clear')"
                    :disabled="disabled"
                    :on-click="onClickClear"
                >
                    <template #button-icon>
                        <TrashIcon />
                    </template>
                </EscrButton>
            </div>
        </div>
        <p v-if="!ontologyConfig" class="escr-project-ontology-empty">
            {{ $t("projectOntology.empty") }}
        </p>
        <p v-else class="escr-project-ontology-summary">
            {{ summary }}
        </p>
        <ConfirmModal
            v-if="clearModalOpen"
            :body-text="$t('projectOntology.clearBody')"
            :confirm-verb="$t('common.clear')"
            :title="$t('projectOntology.clearTitle')"
            :cannot-undo="true"
            :disabled="disabled"
            :on-cancel="closeClearModal"
            :on-confirm="onConfirmClear"
        />
    </div>
</template>
<script>
import { mapActions, mapState } from "vuex";
import ConfirmModal from "../ConfirmModal/ConfirmModal.vue";
import DownloadIcon from "../Icons/DownloadIcon/DownloadIcon.vue";
import EscrButton from "../Button/Button.vue";
import TrashIcon from "../Icons/TrashIcon/TrashIcon.vue";
import UploadIcon from "../Icons/UploadIcon/UploadIcon.vue";
import "./ProjectOntologyCard.css";

export default {
    name: "EscrProjectOntologyCard",
    components: { ConfirmModal, DownloadIcon, EscrButton, TrashIcon, UploadIcon },
    props: {
        /**
         * Whether or not data is loading/actions should be disabled.
         */
        disabled: {
            type: Boolean,
            default: false,
        },
    },
    data() {
        return {
            clearModalOpen: false,
        };
    },
    computed: {
        ...mapState({
            ontologyConfig: (state) => state.project.ontologyConfig,
        }),
        /**
         * Human-readable summary of the stored default ontology config.
         */
        summary() {
            if (!this.ontologyConfig) return "";
            const counts = [
                [this.$t("projectOntology.nRegionTypes", { n: (this.ontologyConfig.region_types || []).length }), this.ontologyConfig.region_types],
                [this.$t("projectOntology.nLineTypes", { n: (this.ontologyConfig.line_types || []).length }), this.ontologyConfig.line_types],
                [this.$t("projectOntology.nPartTypes", { n: (this.ontologyConfig.part_types || []).length }), this.ontologyConfig.part_types],
                [this.$t("projectOntology.nComponents", { n: (this.ontologyConfig.annotation_components || []).length }), this.ontologyConfig.annotation_components],
                [this.$t("projectOntology.nTaxonomies", { n: (this.ontologyConfig.taxonomy || []).length }), this.ontologyConfig.taxonomy],
            ]
                .filter(([, list]) => list && list.length)
                .map(([label]) => label);
            return counts.length
                ? `${counts.join(", ")}.`
                : this.$t("projectOntology.emptyConfig");
        },
    },
    methods: {
        ...mapActions("project", ["clearOntology", "exportOntology", "importOntology"]),
        /**
         * Trigger the hidden file input for importing an ontology config
         */
        onClickImport() {
            this.$refs.importInput.click();
        },
        /**
         * Handle a file being selected for import
         */
        async onImportFileChange(e) {
            const file = e.target.files[0];
            e.target.value = "";
            if (file) {
                await this.importOntology(file);
            }
        },
        onClickClear() {
            this.clearModalOpen = true;
        },
        closeClearModal() {
            this.clearModalOpen = false;
        },
        async onConfirmClear() {
            await this.clearOntology();
            this.clearModalOpen = false;
        },
    },
};
</script>
