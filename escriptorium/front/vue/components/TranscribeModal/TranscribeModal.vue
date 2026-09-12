<template>
    <EscrModal
        class="escr-transcribe-modal"
    >
        <template #modal-header>
            <h2>{{ $t("transcribe.title", { scope }) }}</h2>
            <EscrButton
                color="text"
                :on-click="onCancel"
                size="small"
            >
                <template #button-icon>
                    <XIcon />
                </template>
            </EscrButton>
        </template>
        <template #modal-content>
            <EscrAlert
                color="secondary"
                :message="modelHint"
            />
            <AutocompleteField
                :label="$t('transcribe.model')"
                :disabled="disabled || (!models && !aiBackends.length)"
                :option-groups="modelOptionGroups"
                :on-change="handleModelChange"
                required
            />
            <AutocompleteField
                :label="$t('transcribe.layerName')"
                :disabled="disabled"
                :help-text="$t('transcribe.layerHelp')"
                :option-groups="transcriptionOptionGroups"
                :on-change="handleLayerNameInput"
                :allow-custom-value="true"
                :placeholder="$t('transcribe.layerPlaceholder')"
                required
            />
        </template>
        <template #modal-actions>
            <EscrButton
                color="outline-primary"
                :label="$t('transcribe.cancel')"
                :on-click="onCancel"
                :disabled="disabled"
            />
            <EscrButton
                color="primary"
                :label="$t('transcribe.submit')"
                :on-click="onSubmit"
                :disabled="disabled || invalid"
            />
        </template>
    </EscrModal>
</template>
<script>
import { mapActions, mapState } from "vuex";
import AutocompleteField from "../AutocompleteDropdown/AutocompleteField.vue";
import DropdownField from "../Dropdown/DropdownField.vue";
import EscrAlert from "../Alert/Alert.vue";
import EscrButton from "../Button/Button.vue";
import EscrModal from "../Modal/Modal.vue";
import XIcon from "../Icons/XIcon/XIcon.vue";
import "../Common/Form.css";

export default {
    name: "EscrTranscribeModal",
    components: {
        AutocompleteField,
        DropdownField,
        EscrAlert,
        EscrButton,
        EscrModal,
        XIcon,
    },
    props: {
        /**
         * Boolean indicating whether or not the form fields should be disabled.
         */
        disabled: {
            type: Boolean,
            required: true,
        },
        /**
         * The list of all OCR models on the document. Should be an array of objects
         * with at least a name and pk for each model.
         */
        models: {
            type: Array,
            required: true,
        },
        /**
         * AI backends (Gemini / local VLM). Optional; omitted group if empty.
         */
        aiBackends: {
            type: Array,
            default: () => [],
        },
        /**
         * The list of existing transcription layers on the document.
         */
        transcriptions: {
            type: Array,
            required: true,
        },
        /**
         * Scope of the transcription task, which will appear in the header to indicate
         * whether you are transcribing the entire document or specific images.
         */
        scope: {
            type: String,
            required: true,
        },
        /**
         * Callback function for submitting the transcription task.
         */
        onSubmit: {
            type: Function,
            required: true,
        },
        /**
         * Callback function for clicking "cancel".
         */
        onCancel: {
            type: Function,
            required: true,
        },
    },
    computed: {
        ...mapState({
            model: (state) => state.forms.transcribe.model,
            layerName: (state) => state.forms.transcribe.layerName,
        }),
        /**
         * this form is invalid and cannot be submitted if it is missing model
         * or layer name
         */
        invalid() {
            return !this.layerName || !this.model;
        },
        isAiModel() {
            return String(this.model || "").startsWith("ai:");
        },
        modelHint() {
            return this.isAiModel
                ? this.$t("transcribe.hintAi")
                : this.$t("transcribe.hintKraken");
        },
        /**
         * Group models into "Your Models", "Shared Models", and "Public Models"
         */
        modelOptionGroups() {
            const yourModels = [];
            const sharedModels = [];
            const publicModels = [];

            this.models.forEach((model) => {
                const option = {
                    label: model.name,
                    value: model.pk.toString(),
                    selected: this.model.toString() === model.pk.toString(),
                };

                if (model.rights === "owner") {
                    yourModels.push(option);
                } else if (model.rights === "public") {
                    publicModels.push(option);
                } else {
                    // model.rights === "user" (shared)
                    sharedModels.push(option);
                }
            });

            const groups = [];
            if (yourModels.length > 0) {
                groups.push({ label: this.$t("transcribe.yourModels"), options: yourModels });
            }
            if (sharedModels.length > 0) {
                groups.push({ label: this.$t("transcribe.sharedModels"), options: sharedModels });
            }
            if (publicModels.length > 0) {
                groups.push({ label: this.$t("transcribe.publicModels"), options: publicModels });
            }
            if (this.aiBackends && this.aiBackends.length > 0) {
                const aiOptions = this.aiBackends.map((b) => {
                    const value = `ai:${b.pk}`;
                    const where = b.is_local ? this.$t("transcribe.local") : b.provider;
                    return {
                        label: `${b.name} (${where})`,
                        value,
                        selected: String(this.model) === value,
                    };
                });
                groups.push({ label: this.$t("transcribe.aiBackends"), options: aiOptions });
            }

            return groups;
        },
        /**
         * Format existing transcription layers as options
         */
        transcriptionOptionGroups() {
            const options = this.transcriptions.map((transcription) => ({
                label: transcription.name,
                value: transcription.name,
                selected: this.layerName === transcription.name,
            }));

            return options.length > 0
                ? [{ label: "Existing Layers", options }]
                : [];
        },
    },
    methods: {
        ...mapActions("forms", [
            "handleGenericInput",
        ]),
        handleLayerNameInput(e) {
            this.handleGenericInput({
                form: "transcribe", field: "layerName", value: e.target.value,
            });
        },
        handleModelChange(e) {
            const value = e.target.value;
            this.handleGenericInput({ form: "transcribe", field: "model", value });
            if (String(value).startsWith("ai:") && !this.layerName) {
                const backend = (this.aiBackends || []).find(
                    (b) => `ai:${b.pk}` === String(value),
                );
                if (backend) {
                    this.handleGenericInput({
                        form: "transcribe",
                        field: "layerName",
                        value: `AI — ${backend.name}`,
                    });
                }
            }
        },
    },
};
</script>
