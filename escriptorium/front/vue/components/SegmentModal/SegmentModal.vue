<template>
    <EscrModal
        class="escr-segment-modal"
    >
        <template #modal-header>
            <h2>{{ $t("segment.title", { scope }) }}</h2>
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
            <AutocompleteField
                :label="$t('transcribe.model')"
                :disabled="disabled || !models"
                :option-groups="modelOptionGroups"
                :on-change="handleModelChange"
                required
            />
            <ArrayField
                :on-change="handleIncludeChange"
                :options="includeOptions"
                :label="$t('segment.include')"
                required
            />
            <DropdownField
                :label="$t('segment.textDirection')"
                :disabled="disabled"
                :options="textDirectionOptions"
                :on-change="handleTextDirectionChange"
                required
            />
            <div class="escr-form-field escr-checkbox-field escr-overwrite-field">
                <label>
                    <input
                        type="checkbox"
                        value="overwrite"
                        :checked="overwrite === true"
                        @change="handleOverwriteChange"
                    >
                    {{ $t("segment.overwrite") }}
                </label>
                <span class="escr-help-text">
                    {{ $t("segment.overwriteHelp") }}
                </span>
            </div>
        </template>
        <template #modal-actions>
            <span
                v-if="segmentationInFlight"
                class="escr-cooldown-message"
            >
                {{ $t("segment.inProgress") }}
            </span>
            <EscrButton
                color="outline-primary"
                :label="$t('common.cancel')"
                :on-click="onCancel"
                :disabled="disabled || submitting"
            />
            <EscrButton
                color="primary"
                :label="(submitting || segmentationInFlight) ? $t('segment.segmenting') : $t('images.segment')"
                :loading="submitting || segmentationInFlight"
                :on-click="handleSubmit"
                :disabled="disabled || invalid"
            />
        </template>
    </EscrModal>
</template>
<script>
import { mapActions, mapState } from "vuex";
import AutocompleteField from "../AutocompleteDropdown/AutocompleteField.vue";
import EscrButton from "../Button/Button.vue";
import EscrModal from "../Modal/Modal.vue";
import XIcon from "../Icons/XIcon/XIcon.vue";
import DropdownField from "../Dropdown/DropdownField.vue";
import "../Common/Form.css";
import "./SegmentModal.css";
import ArrayField from "../ArrayField/ArrayField.vue";

export default {
    name: "EscrSegmentModal",
    components: {
        ArrayField,
        AutocompleteField,
        DropdownField,
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
         * Scope of the segmentation task, which will appear in the header to indicate
         * whether you are segmenting the entire document or specific images.
         */
        scope: {
            type: String,
            required: true,
        },
        /**
         * Callback function for submitting the segmentation task.
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
    data() {
        return {
            submitting: false,
        };
    },
    computed: {
        ...mapState({
            include: (state) => state.forms.segment.include,
            model: (state) => state.forms.segment.model,
            overwrite: (state) => state.forms.segment.overwrite,
            textDirection: (state) => state.forms.segment.textDirection,
            parts: (state) => state.document.parts,
            selectedParts: (state) => state.images.selectedParts,
        }),
        // True if any of the targeted parts already have a segmentation task in progress
        segmentationInFlight() {
            const targetParts = this.selectedParts.length > 0
                ? this.parts.filter((p) => this.selectedParts.includes(p.pk))
                : this.parts;
            return targetParts.some(
                (p) => p.workflow
                    && (p.workflow.segment === "pending"
                        || p.workflow.segment === "ongoing"),
            );
        },
        /**
         * this form is invalid and cannot be submitted if it is missing model,
         * text direction, or segmentation steps to include
         */
        invalid() {
            return !this.textDirection || this.include.length === 0;
        },
        /**
         * convert include to options for checkbox elements
         */
        includeOptions() {
            return [
                {
                    label: this.$t("segment.lines"),
                    value: "lines",
                    selected: this.include.includes("lines"),
                },
                {
                    label: this.$t("segment.regions"),
                    value: "regions",
                    selected: this.include.includes("regions"),
                },
            ];
        },
        /**
         * Group models into "Default", "Your Models", "Shared Models", and "Public Models"
         */
        modelOptionGroups() {
            const defaultModel = {
                label: this.$t("segment.defaultModel"),
                value: null,
                selected: !this.model && this.models.length === 0,
            };

            const yourModels = [];
            const sharedModels = [];
            const publicModels = [];

            this.models.forEach((model) => {
                const option = {
                    label: model.name,
                    value: model.pk,
                    selected: this.model?.toString() === model.pk.toString(),
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

            const groups = [
                { label: null, options: [defaultModel] },
            ];
            if (yourModels.length > 0) {
                groups.push({ label: this.$t("transcribe.yourModels"), options: yourModels });
            }
            if (sharedModels.length > 0) {
                groups.push({ label: this.$t("transcribe.sharedModels"), options: sharedModels });
            }
            if (publicModels.length > 0) {
                groups.push({ label: this.$t("transcribe.publicModels"), options: publicModels });
            }

            return groups;
        },
        /**
         * collect text direction options for select element
         */
        textDirectionOptions() {
            return [
                {
                    label: this.$t("segment.hLtr"),
                    value: "horizontal-lr",
                    selected: this.textDirection === "horizontal-lr",
                },
                {
                    label: this.$t("segment.hRtl"),
                    value: "horizontal-rl",
                    selected: this.textDirection === "horizontal-rl",
                },
                {
                    label: this.$t("segment.vLtr"),
                    value: "vertical-lr",
                    selected: this.textDirection === "vertical-lr",
                },
                {
                    label: this.$t("segment.vRtl"),
                    value: "vertical-rl",
                    selected: this.textDirection === "vertical-rl",
                },
            ];
        },
    },
    methods: {
        ...mapActions("forms", [
            "handleCheckboxArrayInput",
            "handleGenericInput",
        ]),
        async handleSubmit() {
            if (this.submitting || this.segmentationInFlight) return;
            this.submitting = true;
            try {
                await this.onSubmit();
            } catch {
                // Errors are handled by the store action
            } finally {
                this.submitting = false;
            }
        },
        handleIncludeChange(e) {
            this.handleCheckboxArrayInput({
                form: "segment", field: "include", checked: e.target.checked, value: e.target.value,
            });
        },
        handleModelChange(e) {
            this.handleGenericInput({ form: "segment", field: "model", value: e.target.value });
        },
        handleOverwriteChange(e) {
            this.handleGenericInput({
                form: "segment", field: "overwrite", value: e.target.checked
            });
        },
        handleTextDirectionChange(e) {
            this.handleGenericInput({
                form: "segment", field: "textDirection", value: e.target.value,
            });
        },
    },
};
</script>
