<template>
    <form
        method="get"
        action="/search/"
    >
        <input
            v-if="data && data.projectId"
            name="project"
            type="text"
            :value="data && data.projectId"
            hidden
        >
        <input
            v-if="data && data.documentId"
            name="document"
            type="text"
            :value="data && data.documentId"
            hidden
        >
        <div
            class="escr-search-form"
        >
            <h3>{{ $t("searchPanel.title", { scope: searchScopeLabel }) }}</h3>
            <label class="escr-text-field escr-form-field">
                <input
                    type="text"
                    :placeholder="$t('searchPanel.placeholder')"
                    :aria-label="$t('common.search')"
                    :disabled="data && data.disabled"
                    name="query"
                >
                <span
                    class="escr-help-text"
                >
                    {{ $t("searchPanel.help") }}
                </span>
            </label>
        </div>
        <EscrButton
            :disabled="data && data.disabled"
            :on-click="(data && data.onSearch) || (() => {})"
            :label="$t('common.search')"
            color="primary"
            type="submit"
        />
    </form>
</template>
<script>
import EscrButton from "../Button/Button.vue";
import "./SearchPanel.css";

export default {
    name: "EscrSearchPanel",
    components: { EscrButton },
    props: {
        /**
         * Data for the search panel, an object containing searchScope, disabled, and optionally
         * projectId and documentId.
         */
        data: {
            type: Object,
            required: true,
        },
    },
    computed: {
        searchScopeLabel() {
            const raw = (this.data && this.data.searchScope) || "";
            const key = raw.toLowerCase();
            if (key === "document") return this.$t("scope.document");
            if (key === "project") return this.$t("scope.project");
            return raw;
        },
    },
}
</script>
