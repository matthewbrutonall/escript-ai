<template>
    <EscrPage class="escr-projects-list">
        <template #page-content>
            <h1>{{ $t("projects.welcome", { name: firstName || username }) }}</h1>
            <div class="escr-card escr-card-table">
                <div class="escr-card-padding escr-card-header">
                    <h2>{{ $t("projects.title") }}</h2>
                    <div class="escr-card-actions">
                        <FilterSet
                            :disabled="loading"
                            :tags="tags"
                            :on-filter="async () => await fetchProjects()"
                            :search-placeholder="$t('projects.searchPlaceholder')"
                        />
                        <EscrButton
                            :label="$t('projects.createNew')"
                            :on-click="openCreateModal"
                            :disabled="loading || createModalOpen"
                        >
                            <template #button-icon>
                                <PlusIcon />
                            </template>
                        </EscrButton>
                        <NewProjectModal
                            v-if="createModalOpen"
                            :disabled="loading"
                            :fonts="fonts"
                            :new-project="true"
                            :on-save="createNewProject"
                            :on-cancel="closeCreateModal"
                            :on-create-tag="createNewProjectTag"
                            :tags="tags"
                        />
                    </div>
                </div>
                <EscrModal
                    v-if="deleteModalOpen"
                    class="escr-delete-project"
                >
                    <template #modal-content>
                        <h2>{{ $t("projects.deleteTitle", { name: projectToDelete.name }) }}</h2>
                        <p>
                            {{ $t("projects.deleteBody") }}
                        </p>
                    </template>
                    <template #modal-actions>
                        <EscrButton
                            color="outline-primary"
                            :label="$t('projects.cancel')"
                            :disabled="loading"
                            :on-click="() => closeDeleteModal()"
                        />
                        <EscrButton
                            color="danger"
                            :label="$t('projects.delete')"
                            :disabled="loading"
                            :on-click="() => deleteProject()"
                        />
                    </template>
                </EscrModal>
                <div
                    v-if="projects.length"
                    class="table-container"
                >
                    <EscrTable
                        :items="projects"
                        item-key="slug"
                        :headers="headers"
                        :on-sort="sortProjects"
                        :disabled="loading"
                        :linkable="true"
                    >
                        <template #actions="{ item }">
                            <EscrButton
                                v-tooltip.bottom="'Delete'"
                                size="small"
                                color="text"
                                :on-click="() => openDeleteModal(item)"
                                :disabled="loading"
                                aria-label="Delete project"
                            >
                                <template #button-icon>
                                    <TrashIcon />
                                </template>
                            </EscrButton>
                        </template>
                    </EscrTable>
                    <EscrButton
                        v-if="nextPage"
                        :label="$t('projects.loadMore')"
                        class="escr-load-more-btn"
                        color="outline-primary"
                        size="small"
                        :disabled="loading"
                        :on-click="async () => await fetchNextPage()"
                    />
                </div>
                <EscrLoader
                    v-else
                    :loading="loading"
                    :no-data-message="$t('projects.empty')"
                />
            </div>
        </template>
    </EscrPage>
</template>
<script>
import { mapActions, mapState } from "vuex";
import EscrButton from "../../components/Button/Button.vue";
import EscrLoader from "../../components/Loader/Loader.vue";
import EscrModal from "../../components/Modal/Modal.vue";
import EscrPage from "../Page/Page.vue";
import EscrTable from "../../components/Table/Table.vue";
import EscrTags from "../../components/Tags/Tags.vue";
import FilterSet from "../../components/FilterSet/FilterSet.vue";
import NewProjectModal from "../../components/EditProjectModal/EditProjectModal.vue";
import PlusIcon from "../../components/Icons/PlusIcon/PlusIcon.vue";
import TrashIcon from "../../components/Icons/TrashIcon/TrashIcon.vue";
import "../../components/Common/Card.css"
import "./ProjectsList.css";

export default {
    name: "EscrProjectsListPage",
    components: {
        EscrButton,
        EscrLoader,
        EscrModal,
        EscrPage,
        EscrTable,
        // eslint-disable-next-line vue/no-unused-components
        EscrTags,
        FilterSet,
        NewProjectModal,
        PlusIcon,
        TrashIcon,
    },
    computed: {
        ...mapState({
            createModalOpen: (state) => state.projects.createModalOpen,
            deleteModalOpen: (state) => state.projects.deleteModalOpen,
            firstName: (state) => state.user.firstName,
            fonts: (state) => state.projects.fonts,
            loading: (state) => state.projects.loading,
            nextPage: (state) => state.projects.nextPage,
            projects: (state) => state.projects.projects,
            projectToDelete: (state) => state.projects.projectToDelete,
            tags: (state) => state.projects.tags,
            username: (state) => state.user.username,
        }),
        headers() {
            return [
                { label: this.$t("projects.name"), value: "name", sortable: true },
                { label: this.$t("projects.projectTags"), value: "tags", component: EscrTags },
                { label: this.$t("projects.nDocuments"), value: "documents_count", sortable: true  },
                { label: this.$t("projects.owner"), value: "owner", sortable: true  },
                {
                    label: this.$t("projects.lastUpdate"),
                    value: "updated_at",
                    sortable: true,
                    format: (val) => new Date(val).toLocaleDateString(
                        undefined,
                        { year: "numeric", month: "long", day: "numeric" },
                    ),
                },
            ];
        },
    },
    async created() {
        try {
            await this.fetchCurrentUser();
            await this.fetchProjects();
            await this.fetchAllProjectTags();
            await this.fetchFonts();
        } catch (error) {
            this.addError(error);
        }
    },
    methods: {
        ...mapActions("projects", [
            "closeCreateModal",
            "closeDeleteModal",
            "createNewProject",
            "createNewProjectTag",
            "deleteProject",
            "fetchAllProjectTags",
            "fetchFonts",
            "fetchProjects",
            "fetchNextPage",
            "openCreateModal",
            "openDeleteModal",
            "sortProjects",
        ]),
        ...mapActions("alerts", ["addError"]),
        ...mapActions("user", ["fetchCurrentUser"]),
    },
};
</script>
