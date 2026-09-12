<template>
    <ul class="escr-quick-actions">
        <li>
            <VDropdown
                placement="left"
                :triggers="['hover']"
                theme="tags-dropdown"
            >
                <EscrButton
                    color="text"
                    :disabled="!data || data.disabled"
                    :on-click="() => openModal('import')"
                    :label="$t('common.import')"
                >
                    <template #button-icon>
                        <ImportIcon />
                    </template>
                </EscrButton>
                <template #popper>
                    <span class="escr-tooltip-text">
                        {{ $t("actions.importHint") }}
                    </span>
                </template>
            </VDropdown>
        </li>
        <li>
            <EscrButton
                color="text"
                :disabled="!data || data.disabled"
                :on-click="() => openModal('segment')"
                :label="$t('actions.segmentScope', { scope: actionScope })"
            >
                <template #button-icon>
                    <SegmentIcon />
                </template>
            </EscrButton>
        </li>
        <li>
            <EscrButton
                color="text"
                :disabled="!data || data.disabled"
                :on-click="() => openModal('transcribe')"
                :label="$t('actions.transcribeScope', { scope: actionScope })"
            >
                <template #button-icon>
                    <TranscribeIcon />
                </template>
            </EscrButton>
        </li>
        <li>
            <EscrButton
                color="text"
                :disabled="!data || data.disabled"
                :on-click="() => openModal('align')"
                :label="$t('actions.alignScope', { scope: actionScope })"
            >
                <template #button-icon>
                    <AlignIcon />
                </template>
            </EscrButton>
        </li>
        <li>
            <EscrButton
                color="text"
                :disabled="!data || data.disabled"
                :on-click="() => openModal('export')"
                :label="$t('actions.exportScope', { scope: actionScope })"
            >
                <template #button-icon>
                    <ExportIcon />
                </template>
            </EscrButton>
        </li>
        <li>
            <EscrButton
                color="text"
                :disabled="!data || data.disabled"
                :on-click="() => openModal('downloadArchive')"
                :label="$t('actions.downloadArchiveScope', { scope: actionScope })"
            >
                <template #button-icon>
                    <DownloadIcon />
                </template>
            </EscrButton>
        </li>
    </ul>
</template>

<script>
import { Dropdown as VDropdown } from "floating-vue";
import { mapActions } from "vuex";
import AlignIcon from "../Icons/AlignIcon/AlignIcon.vue";
import DownloadIcon from "../Icons/DownloadIcon/DownloadIcon.vue";
import EscrButton from "../Button/Button.vue";
import ExportIcon from "../Icons/ExportIcon/ExportIcon.vue";
import ImportIcon from "../Icons/ImportIcon/ImportIcon.vue";
import SegmentIcon from "../Icons/SegmentIcon/SegmentIcon.vue";
import TranscribeIcon from "../Icons/TranscribeIcon/TranscribeIcon.vue";
import "./QuickActionsPanel.css";

export default {
    name: "EscrQuickActionsPanel",
    components: {
        AlignIcon,
        DownloadIcon,
        EscrButton,
        ExportIcon,
        ImportIcon,
        SegmentIcon,
        TranscribeIcon,
        VDropdown,
    },
    props: {
        /**
         * Data for the quick actions panel, an object containing:
         * {
         *     disabled: Boolean, // true if the buttons on the panel should be disabled
         *     scope: String, // indicate if this panel is for "Document" or "Elements"
         * }
         */
        data: {
            type: Object,
            required: true,
        },
    },
    computed: {
        actionScope() {
            const raw = (this.data && this.data.scope) || "";
            const key = raw.toLowerCase();
            if (key === "document") return this.$t("scope.document");
            if (key === "project") return this.$t("scope.project");
            if (key === "image") return this.$t("scope.image");
            return raw;
        },
    },
    methods: {
        ...mapActions("tasks", [
            "closeModal",
            "openModal",
        ]),
    }
};
</script>
