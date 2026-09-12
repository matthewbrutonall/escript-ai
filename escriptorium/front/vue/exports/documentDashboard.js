import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import Document from "../pages/Document/Document.vue";

export default new Vue({
    el: "#document-dashboard",
    store,
    i18n,
    components: {
        "document-dashboard": Document,
    },
});
