import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import OntologyOverview from "../pages/OntologyOverview/OntologyOverview.vue";

export default new Vue({
    el: "#ontology-overview-page",
    store,
    i18n,
    components: {
        "ontology-overview-page": OntologyOverview,
    },
});
