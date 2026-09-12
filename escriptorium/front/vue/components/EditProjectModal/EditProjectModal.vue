<template>
    <EscrModal class="escr-edit-project">
        <template #modal-header>
            <h2>{{ newProject ? $t("editProject.createTitle") : $t("editProject.editTitle") }}</h2>
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
            <TextField
                :label="$t('common.name')"
                :placeholder="$t('editProject.namePlaceholder')"
                :disabled="disabled"
                :max-length="512"
                :on-input="(e) => handleTextFieldInput('name', e.target.value)"
                :value="name"
                required
            />
            <TextField
                :label="$t('editProject.guidelines')"
                placeholder="https://"
                :disabled="disabled"
                :on-input="(e) => handleTextFieldInput('guidelines', e.target.value)"
                :value="guidelines"
                :invalid="!!guidelines && !isHttpUrl(guidelines)"
            />
            <span
                v-if="guidelines && !isHttpUrl(guidelines)"
                class="escr-help-text escr-error-text"
            >
                {{ $t("editProject.urlError") }}
            </span>
            <DropdownField
                :label="$t('editDoc.transcriptionFont')"
                :help-text="$t('editProject.fontHelp')"
                :disabled="disabled"
                :on-change="(e) => handleTextFieldInput('transcriptionFont', e.target.value)"
                :options="fontOptions"
            />
            <TagsField
                :label="$t('common.tags')"
                :disabled="disabled"
                :on-change="handleTagsFieldInput"
                :on-change-tag-name="(e) => handleTextFieldInput('tagName', e.target.value)"
                :on-create-tag="onCreateTag"
                :tag-name="tagName"
                :tags="tags"
                :selected-tags="selectedTags"
            />
        </template>
        <template #modal-actions>
            <EscrButton
                color="outline-primary"
                :label="$t('common.cancel')"
                :on-click="onCancel"
                :disabled="disabled"
            />
            <EscrButton
                color="primary"
                :label="newProject ? $t('common.create') : $t('common.save')"
                :on-click="onSave"
                :disabled="disabled || invalid"
            />
        </template>
    </EscrModal>
</template>
<script>
import { mapActions, mapState } from "vuex";
import DropdownField from "../Dropdown/DropdownField.vue";
import EscrButton from "../Button/Button.vue";
import EscrModal from "../Modal/Modal.vue";
import TagsField from "../TagsField/TagsField.vue";
import TextField from "../TextField/TextField.vue";
import XIcon from "../Icons/XIcon/XIcon.vue";
import "./EditProjectModal.css";

export default {
    name: "EscrEditProjectModal",
    components: {
        DropdownField,
        EscrButton,
        EscrModal,
        TagsField,
        TextField,
        XIcon,
    },
    props: {
        /**
         * Boolean indicating if the form fields should be disabled
         */
        disabled: {
            type: Boolean,
            default: false,
        },
        /**
         * If this is a new project, set true; if it's editing an existing one, leave false
         */
        newProject: {
            type: Boolean,
            default: false,
        },
        /**
         * Callback for clicking the cancel button
         */
        onCancel: {
            type: Function,
            required: true,
        },
        /**
         * Callback for clicking the "create tag" button
         */
        onCreateTag: {
            type: Function,
            required: true,
        },
        /**
         * Callback for clicking the save/create button
         */
        onSave: {
            type: Function,
            required: true,
        },
        /** list of available fonts from the api */
        fonts: {
            type: Array,
            default: () => [],
        },
        /**
         * Full list of tags across all projects
         */
        tags: {
            type: Array,
            default: () => [],
        },
    },
    computed: {
        ...mapState({
            guidelines: (state) => state.forms.editProject.guidelines,
            name: (state) => state.forms.editProject.name,
            selectedTags: (state) => state.forms.editProject.tags,
            tagName: (state) => state.forms.editProject.tagName,
            transcriptionFont: (state) => state.forms.editProject.transcriptionFont,
        }),
        invalid() {
            return !this.name || (!!this.guidelines && !this.isHttpUrl(this.guidelines));
        },
        /** dropdown options for transcription font, with a leading "Default" entry */
        fontOptions() {
            const selectedFont = this.transcriptionFont
                ? this.transcriptionFont.toString()
                : "";
            return [
                {
                    value: "",
                    label: "Default",
                    selected: !selectedFont,
                },
                ...this.fonts.map((font) => ({
                    value: font.pk.toString(),
                    label: font.name,
                    selected: font.pk.toString() === selectedFont,
                })),
            ];
        },
    },
    methods: {
        ...mapActions("forms", [
            "handleGenericInput",
            "handleTagsInput",
        ]),
        handleTagsFieldInput({ checked, tag }) {
            this.handleTagsInput({ checked, tag, form: "editProject" });
        },
        handleTextFieldInput(field, value) {
            this.handleGenericInput({ form: "editProject", field, value });
        },
        isHttpUrl(string) {
            let givenURL;
            try {
                givenURL = new URL(string);
            } catch (error) {
                return false;
            }
            return givenURL.protocol === "http:" || givenURL.protocol === "https:";
        },
    },
};
</script>
