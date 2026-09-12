<template>
    <div
        v-if="data.models && data.models.length"
        class="escr-models-panel"
    >
        <details
            v-for="model in data.models"
            :key="model.pk"
            class="escr-model-details"
        >
            <summary>
                <span>{{ model.name }}</span>
                <a
                    :href="model.file"
                    :aria-label="$t('modelsPanel.download')"
                >
                    <DownloadIcon />
                </a>
            </summary>
            <dl>
                <dt>{{ $t("modelsPanel.role") }}</dt>
                <dd>{{ model.job }}</dd>
                <dt>{{ $t("modelsPanel.script") }}</dt>
                <dd>{{ model.script || "-" }}</dd>
                <dt>{{ $t("modelsPanel.size") }}</dt>
                <dd>{{ filesize(model.file_size) }}</dd>
                <dt>{{ $t("modelsPanel.trainedFrom") }}</dt>
                <dd>{{ model.parent || "-" }}</dd>
                <dt>{{ $t("modelsPanel.trainedStatus") }}</dt>
                <dd><component :is="trainedStatusIcon(model.training)" /></dd>
                <dt>{{ $t("modelsPanel.accuracy") }}</dt>
                <dd>
                    {{ model.accuracy_percent ? `${model.accuracy_percent.toFixed(2)}%` : "-" }}
                </dd>
                <dt>{{ $t("modelsPanel.rights") }}</dt>
                <dd>{{ model.rights }}</dd>
                <dt>{{ $t("modelsPanel.sharing") }}</dt>
                <dd v-if="model.can_share">
                    <a :href="`/model/${model.pk}/rights/`">{{ $t("modelsPanel.share") }}</a>
                </dd>
                <dd v-else>
                    -
                </dd>
            </dl>
        </details>
    </div>
    <EscrLoader
        v-else
        :loading="data.loading"
        :no-data-message="$t('modelsPanel.empty')"
    />
</template>
<script>
import CheckIcon from "../Icons/CheckIcon/CheckIcon.vue";
import DownloadIcon from "../Icons/DownloadIcon/DownloadIcon.vue";
import EscrLoader from "../Loader/Loader.vue";
import XIcon from "../Icons/XIcon/XIcon.vue";
import { filesizeformat } from "../../store/util/filesize";
import "./ModelsPanel.css";

export default {
    name: "EscrModelsPanel",
    components: { DownloadIcon, EscrLoader },
    props: {
        data: {
            type: Object,
            required: true,
        },
    },
    methods: {
        /**
         * Get the correct "Trained" icon (Check or X) for training status.
         */
        trainedStatusIcon(training) {
            return training === true ? XIcon : CheckIcon;
        },
        /**
         * Format the filesize similarly to how Django formats it.
         */
        filesize(bytes) {
            return filesizeformat(bytes);
        },
    }
}
</script>
